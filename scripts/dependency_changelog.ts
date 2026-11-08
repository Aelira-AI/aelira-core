/** Dependabot pull requests get their changelog entries when the release is prepared. */
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const REPOSITORY = 'Aelira-AI/aelira-core';
const API_ORIGIN = 'https://api.github.com';
const ROOT = `/repos/${REPOSITORY}`;
const CHANGELOG = 'CHANGELOG.md';
const SECTION = 'Dependencies';
/** Keep a Changelog section order; Dependencies sits between Fixed and Security. */
const ORDER = ['Added', 'Changed', 'Deprecated', 'Removed', 'Fixed', SECTION, 'Security'];
type JsonObject = Record<string, unknown>;
type Api = { request(path: string, method?: string, body?: JsonObject): Promise<unknown> };
type DependencyPull = { number: number; title: string; url: string };
type Semver = { major: number; minor: number; patch: number };
type InsertResult = { changelog: string; added: number };

function object(value: unknown): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid API object');
  return value as JsonObject;
}
function number(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) throw new Error('Invalid number');
  return value as number;
}
function dateString(value: unknown): Date {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
    throw new Error('Invalid date');
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error('Invalid date');
  return date;
}

export class GitHub implements Api {
  private token: string;
  private transport: typeof fetch;
  constructor(token: string, origin = API_ORIGIN, transport: typeof fetch = fetch) {
    if (origin !== API_ORIGIN || !token || /\s/.test(token)) throw new Error('Invalid API configuration');
    this.token = token;
    this.transport = transport;
  }
  async request(path: string, method = 'GET', body?: JsonObject): Promise<unknown> {
    // Only internally constructed repository paths are accepted. Redirects must not carry credentials.
    if (!path.startsWith(`${ROOT}/`) || /[\\\s#]/.test(path) || path.includes('..')) throw new Error('Invalid API path');
    if (!['GET', 'POST'].includes(method)) throw new Error('Invalid API method');
    const response = await this.transport(`${API_ORIGIN}${path}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(15_000),
      headers: {
        Authorization: 'Bearer ' + this.token, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json',
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status !== (method === 'POST' ? 201 : 200)) throw new Error('GitHub request failed');
    return response.json();
  }
}

export function parseTag(tag: string): Semver | undefined {
  const match = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(tag);
  if (!match) return undefined;
  const semver = { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) };
  return Object.values(semver).every(Number.isSafeInteger) ? semver : undefined;
}

export function compareSemver(a: Semver, b: Semver): number {
  return a.major - b.major || a.minor - b.minor || a.patch - b.patch;
}

/** The highest semver release tag strictly older than the given tag, or undefined for a first release. */
export function previousTag(tags: string[], tag: string): string | undefined {
  const target = parseTag(tag);
  if (!target) throw new Error('Release tag must look like vX.Y.Z');
  let best: { tag: string; semver: Semver } | undefined;
  for (const candidate of tags) {
    const semver = parseTag(candidate);
    if (!semver || compareSemver(semver, target) >= 0) continue;
    if (!best || compareSemver(semver, best.semver) > 0) best = { tag: candidate, semver };
  }
  return best?.tag;
}

/** One ASCII-only bullet: '- <lowercased title> (<pull url>)'. */
export function renderEntry(pull: DependencyPull): string {
  const title = pull.title.replace(/[^\x20-\x7E]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!title || !Number.isSafeInteger(pull.number) || pull.number <= 0
    || pull.url !== `https://github.com/${REPOSITORY}/pull/${pull.number}`) {
    throw new Error('Invalid pull request entry');
  }
  return `- ${title} (${pull.url})`;
}

/** Whole-number reference, so 94 never matches inside 949. */
export function referencesPull(text: string, prNumber: number): boolean {
  number(prNumber);
  return new RegExp(`(?<![0-9])${prNumber}(?![0-9])`).test(text);
}

function unreleasedRange(lines: string[]): { start: number; end: number } {
  const start = lines.findIndex((line) => /^## \[Unreleased\][ \t]*$/.test(line));
  if (start === -1) throw new Error('CHANGELOG.md has no ## [Unreleased] section');
  let end = lines.findIndex((line, index) => index > start && /^## /.test(line));
  if (end === -1) end = lines.length;
  return { start, end };
}

function subsectionRanges(lines: string[], start: number, end: number): Map<string, { start: number; end: number }> {
  const headers: { name: string; start: number }[] = [];
  for (let index = start + 1; index < end; index++) {
    const match = /^### ([A-Za-z]+)[ \t]*$/.exec(lines[index]);
    if (match) headers.push({ name: match[1], start: index });
  }
  const ranges = new Map<string, { start: number; end: number }>();
  headers.forEach((header, position) => {
    const rangeEnd = position + 1 < headers.length ? headers[position + 1].start : end;
    if (ranges.has(header.name)) throw new Error('Duplicate subsection in ## [Unreleased]');
    ranges.set(header.name, { start: header.start, end: rangeEnd });
  });
  return ranges;
}

/**
 * Merge bullets into the ### Dependencies subsection of ## [Unreleased], creating it in
 * Keep a Changelog order when absent. Pull requests already referenced in that subsection are skipped.
 */
export function insertDependencyEntries(changelog: string, pulls: DependencyPull[]): InsertResult {
  const lines = changelog.split('\n');
  const { start, end } = unreleasedRange(lines);
  const ranges = subsectionRanges(lines, start, end);
  const existing = ranges.get(SECTION);
  const existingText = existing ? lines.slice(existing.start, existing.end).join('\n') : '';
  const pending = pulls.filter((pull) => !referencesPull(existingText, pull.number));
  if (!pending.length) return { changelog, added: 0 };
  const bullets = pending.map(renderEntry);

  if (existing) {
    let last = existing.start;
    for (let index = existing.start + 1; index < existing.end; index++) {
      if (lines[index].trim()) last = index;
    }
    if (last === existing.start) {
      lines.splice(existing.start + 1, existing.end - existing.start - 1, '', ...bullets, '');
    } else {
      lines.splice(last + 1, 0, ...bullets);
    }
    return { changelog: lines.join('\n'), added: bullets.length };
  }

  const rank = ORDER.indexOf(SECTION);
  let insertAt = end;
  for (let index = start + 1; index < end; index++) {
    const match = /^### ([A-Za-z]+)[ \t]*$/.exec(lines[index]);
    if (match && ORDER.indexOf(match[1]) > rank) { insertAt = index; break; }
  }
  const before = lines.slice(0, insertAt);
  while (before.length > start + 1 && !before[before.length - 1].trim()) before.pop();
  const after = lines.slice(insertAt);
  while (after.length && !after[0].trim()) after.shift();
  const rebuilt = [...before, '', `### ${SECTION}`, '', ...bullets];
  if (after.length) rebuilt.push('', ...after);
  else rebuilt.push('');
  return { changelog: rebuilt.join('\n'), added: bullets.length };
}

async function listTagNames(api: Api): Promise<string[]> {
  const names: string[] = [];
  for (let page = 1; page <= 30; page++) {
    const refs = await api.request(`${ROOT}/git/refs/tags?per_page=100&page=${page}`);
    if (!Array.isArray(refs)) throw new Error('Invalid tags response');
    for (const raw of refs) {
      const ref = object(raw);
      if (typeof ref.ref !== 'string' || !ref.ref.startsWith('refs/tags/')) throw new Error('Invalid tag ref');
      names.push(decodeURIComponent(ref.ref.slice('refs/tags/'.length)));
    }
    if (refs.length < 100) return names;
  }
  throw new Error('Too many tags; manual recheck required');
}

async function tagDate(api: Api, tag: string): Promise<Date> {
  if (!/^[A-Za-z0-9._-]+$/.test(tag)) throw new Error('Invalid tag name');
  const ref = object(await api.request(`${ROOT}/git/ref/tags/${encodeURIComponent(tag)}`));
  const target = object(ref.object);
  if (typeof target.sha !== 'string' || !/^[a-f0-9]{40}$/.test(target.sha)) throw new Error('Invalid tag object');
  if (target.type === 'tag') {
    const tagged = object(await api.request(`${ROOT}/git/tags/${target.sha}`));
    return dateString(object(tagged.tagger).date);
  }
  if (target.type === 'commit') {
    const commit = object(await api.request(`${ROOT}/git/commits/${target.sha}`));
    return dateString(object(commit.author).date);
  }
  throw new Error('Unexpected tag object type');
}

/** Merged pull requests carrying the dependencies label, updated since the lower bound. */
export async function mergedDependencyPulls(api: Api, since: Date | undefined): Promise<DependencyPull[]> {
  const pulls: DependencyPull[] = [];
  for (let page = 1; page <= 30; page++) {
    const query = `state=closed&labels=dependencies&per_page=100&page=${page}`
      + (since ? `&since=${encodeURIComponent(since.toISOString())}` : '');
    const items = await api.request(`${ROOT}/issues?${query}`);
    if (!Array.isArray(items)) throw new Error('Invalid issues response');
    for (const raw of items) {
      const issue = object(raw);
      if (issue.pull_request === undefined || issue.pull_request === null) continue;
      object(issue.pull_request);
      const prNumber = number(issue.number);
      const pull = object(await api.request(`${ROOT}/pulls/${prNumber}`));
      if (pull.number !== prNumber || typeof pull.title !== 'string') throw new Error('Invalid pull request');
      if (pull.merged_at === undefined || pull.merged_at === null) continue;
      const mergedAt = dateString(pull.merged_at);
      if (since && mergedAt < since) continue;
      if (pull.html_url !== `https://github.com/${REPOSITORY}/pull/${prNumber}`) throw new Error('Invalid pull request url');
      pulls.push({ number: prNumber, title: pull.title, url: pull.html_url as string });
    }
    if (items.length < 100) return pulls.sort((a, b) => a.number - b.number);
  }
  throw new Error('Too many dependency pull requests; manual recheck required');
}

async function releaseWindowPulls(api: Api, tag: string): Promise<DependencyPull[]> {
  const previous = previousTag(await listTagNames(api), tag);
  const since = previous ? await tagDate(api, previous) : undefined;
  return mergedDependencyPulls(api, since);
}

/** Merged dependency pulls in the window whose numbers appear nowhere in the changelog. */
export async function missingPulls(api: Api, tag: string, changelog: string): Promise<DependencyPull[]> {
  const pulls = await releaseWindowPulls(api, tag);
  return pulls.filter((pull) => !referencesPull(changelog, pull.number));
}

export async function insertEntries(api: Api, tag: string, changelog: string): Promise<InsertResult> {
  return insertDependencyEntries(changelog, await releaseWindowPulls(api, tag));
}

export function parseArgs(argv: string[]): { mode: 'insert' | 'check'; tag: string } {
  if (argv.length !== 3 || argv[1] !== '--tag' || !['--insert', '--check'].includes(argv[0]) || !parseTag(argv[2])) {
    throw new Error('Usage: dependency_changelog.ts --insert|--check --tag vX.Y.Z');
  }
  return { mode: argv[0] === '--insert' ? 'insert' : 'check', tag: argv[2] };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.env.GITHUB_REPOSITORY !== REPOSITORY) throw new Error('Unexpected repository');
    const { mode, tag } = parseArgs(process.argv.slice(2));
    const api = new GitHub(process.env.GITHUB_TOKEN ?? '', process.env.GITHUB_API_URL);
    const changelog = readFileSync(CHANGELOG, 'utf8');
    if (mode === 'check') {
      const missing = await missingPulls(api, tag, changelog);
      if (missing.length) {
        console.log(`Merged dependency pull requests missing a ${CHANGELOG} reference: ${missing.map((pull) => `#${pull.number}`).join(', ')}`);
        process.exitCode = 1;
      } else {
        console.log(`Every merged dependency pull request since the previous release is referenced in ${CHANGELOG}.`);
      }
    } else {
      const result = insertEntries(api, tag, changelog);
      writeFileSync(CHANGELOG, result.changelog);
      console.log(result.added
        ? `Added ${result.added} ${SECTION} ${result.added === 1 ? 'entry' : 'entries'} to ${CHANGELOG}.`
        : `No new ${SECTION} entries to add to ${CHANGELOG}.`);
    }
  } catch {
    // Never print API response text or credentials.
    console.error('Dependency changelog could not be verified. Check workflow configuration and rerun.');
    process.exitCode = 1;
  }
}
