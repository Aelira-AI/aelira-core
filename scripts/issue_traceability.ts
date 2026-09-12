/** Metadata-only issue policy. Never loads or executes pull-request code. */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const REPOSITORY = 'Aelira-AI/aelira-core';
const API_ORIGIN = 'https://api.github.com';
const ROOT = `/repos/${REPOSITORY}`;
const CONTEXT = 'Issue traceability';
const DEPENDENCY_FILES = new Set([
  'requirements.txt', 'requirements-dev.txt',
  'dashboard/package.json', 'dashboard/package-lock.json',
  'cli/package.json', 'cli/package-lock.json',
]);
type JsonObject = Record<string, unknown>;
type Api = { request(path: string, method?: string, body?: JsonObject): Promise<unknown> };
function object(value: unknown): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid API object');
  return value as JsonObject;
}
function number(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) throw new Error('Invalid number');
  return value as number;
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
        Authorization: `Bearer ${this.token}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json',
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status !== (method === 'POST' ? 201 : 200)) throw new Error('GitHub request failed');
    return response.json();
  }
}

/** Deliberately narrow declarations, not a general Markdown or GitHub-link parser. */
export function parseReferences(body: string): number[] {
  const references = new Set<number>();
  let fence: { char: string; length: number } | undefined;
  const lines: string[] = [];
  const plain = body.replace(/<!--[\s\S]*?(?:-->|$)/g, '');
  for (const line of plain.split(/\r?\n/)) {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (marker) {
      if (!fence) fence = { char: marker[1][0], length: marker[1].length };
      else if (marker[1][0] === fence.char && marker[1].length >= fence.length && /^ {0,3}(?:`+|~+)\s*$/.test(line)) fence = undefined;
      continue;
    }
    if (!fence) lines.push(line);
  }
  // Fail closed on raw HTML outside comments/fenced examples. Handling arbitrary
  // nested tags, CDATA, declarations and attributes would require a Markdown parser.
  if (/<[a-z!/?]/i.test(lines.join('\n'))) return [];
  // A whole paragraph must consist of declarations. This excludes lazy quote
  // continuations, multiline code spans, link titles, attributes, and image alt text.
  for (const paragraph of lines.join('\n').split(/\n[ \t]*\n/)) {
    const declarations = paragraph.split('\n').filter((line) => line.length);
    if (!declarations.length || !declarations.every((line) => /^(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s/i.test(line))) continue;
    for (const line of declarations) {
      const match = /^(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#([1-9]\d*)|Aelira-AI\/aelira-core#([1-9]\d*)|https:\/\/github\.com\/Aelira-AI\/aelira-core\/issues\/([1-9]\d*))\s*$/i.exec(line);
      if (!match) throw new Error('Use a standalone same-repository closing reference');
      references.add(number(Number(match[1] ?? match[2] ?? match[3])));
      if (references.size > 20) throw new Error('Too many issue references');
    }
  }
  return [...references];
}

type Pull = {
  number: number; state: string; body: string; sha: string;
  user: { login: string; type: string }; changedFiles: number;
};
async function getPull(api: Api, prNumber: number): Promise<Pull> {
  const value = object(await api.request(`${ROOT}/pulls/${number(prNumber)}`));
  const base = object(value.base);
  const head = object(value.head);
  const user = object(value.user);
  if (value.number !== prNumber || object(base.repo).full_name !== REPOSITORY || base.ref !== 'main'
    || !['open', 'closed'].includes(value.state as string)
    || (value.body !== null && typeof value.body !== 'string')
    || typeof head.sha !== 'string' || !/^[a-f0-9]{40}$/.test(head.sha)
    || typeof user.login !== 'string' || typeof user.type !== 'string'
    || !Number.isSafeInteger(value.changed_files) || (value.changed_files as number) < 0) {
    throw new Error('Invalid pull request');
  }
  return {
    number: prNumber, state: value.state as string, body: (value.body ?? '') as string,
    sha: head.sha, user: { login: user.login, type: user.type }, changedFiles: value.changed_files as number,
  };
}
async function dependencyOnly(api: Api, pull: Pull): Promise<boolean> {
  if (pull.user.login !== 'dependabot[bot]' || pull.user.type !== 'Bot' || pull.changedFiles < 1) return false;
  let count = 0;
  for (let page = 1; page <= 30; page++) {
    const files = await api.request(`${ROOT}/pulls/${pull.number}/files?per_page=100&page=${page}`);
    if (!Array.isArray(files)) throw new Error('Invalid files response');
    for (const raw of files) {
      const file = object(raw);
      if (typeof file.filename !== 'string' || !DEPENDENCY_FILES.has(file.filename) || file.status !== 'modified') return false;
      count++;
    }
    if (files.length < 100) return count === pull.changedFiles;
  }
  return false;
}
async function evaluate(api: Api, pull: Pull): Promise<boolean> {
  const references = parseReferences(pull.body);
  if (!references.length) return dependencyOnly(api, pull);
  for (const reference of references) {
    const issue = object(await api.request(`${ROOT}/issues/${reference}`));
    if (issue.number !== reference || 'pull_request' in issue
      || issue.repository_url !== `${API_ORIGIN}${ROOT}` || !Array.isArray(issue.labels)) return false;
    const labels = issue.labels.map((label: unknown) => object(label).name);
    if (labels.some((label: unknown) => typeof label !== 'string' || !label.trim()) || new Set(labels).size < 2) return false;
  }
  return true;
}
async function status(api: Api, sha: string, state: string): Promise<void> {
  const response = object(await api.request(`${ROOT}/statuses/${sha}`, 'POST', {
    state, context: CONTEXT,
    description: state === 'success' ? 'Linked issues validated, or scoped Dependabot update'
      : state === 'pending' ? 'Checking current pull request and issue metadata'
        : 'Issue policy not satisfied; check CONTRIBUTING.md and rerun',
  }));
  if (response.state !== state || response.context !== CONTEXT) throw new Error('Status publication not confirmed');
}

async function inspectPullRequest(api: Api, prNumber: number, knownHead?: string): Promise<{ pull: Pull; valid: boolean }> {
  // The event's SHA/body can be stale, and pull_request_target uses a base SHA.
  let pull: Pull;
  try { pull = await getPull(api, prNumber); } catch {
    // Event/list metadata is sufficient to invalidate a known status, never to approve it.
    if (knownHead && /^[a-f0-9]{40}$/.test(knownHead)) await status(api, knownHead, 'failure');
    throw new Error('Current pull request unavailable');
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    if (pull.state !== 'open') return { pull, valid: true };
    await status(api, pull.sha, 'pending');
    let valid = false;
    try { valid = await evaluate(api, pull); } catch { /* All lookup/parser uncertainty fails closed. */ }
    const current = await getPull(api, prNumber);
    if (JSON.stringify(current) !== JSON.stringify(pull)) { pull = current; continue; }
    return { pull, valid };
  }
  return { pull, valid: false };
}

/** Single-PR primitive for unit tests; production uses run() for shared-head safety. */
export async function checkPullRequest(api: Api, prNumber: number, knownHead?: string): Promise<boolean> {
  const result = await inspectPullRequest(api, prNumber, knownHead);
  if (result.pull.state === 'open') await status(api, result.pull.sha, result.valid ? 'success' : 'failure');
  return result.valid;
}

async function listOpenHeads(api: Api, invalidate: boolean): Promise<Map<number, string>> {
  const entries = new Map<number, string>();
  for (let page = 1; page <= 10; page++) {
    const values = await api.request(`${ROOT}/pulls?state=open&base=main&per_page=100&page=${page}`);
    if (!Array.isArray(values)) throw new Error('Invalid pull request list');
    for (const raw of values) {
      const pull = object(raw);
      const sha = object(pull.head).sha;
      if (typeof sha !== 'string' || !/^[a-f0-9]{40}$/.test(sha)) throw new Error('Invalid listed head');
      entries.set(number(pull.number), sha);
      if (invalidate) await status(api, sha, 'pending');
    }
    if (values.length < 100) break;
    if (page === 10) throw new Error('Too many open pull requests; manual recheck required');
  }
  return entries;
}
const headSnapshot = (entries: Map<number, string>) => JSON.stringify([...entries].sort(([a], [b]) => a - b));

export async function run(api: Api, eventName: string, event: JsonObject, invalidateOnly = false): Promise<boolean> {
  if (!['pull_request_target', 'issues', 'label', 'workflow_dispatch'].includes(eventName)) throw new Error('Unsupported event');
  let eventNumber: number | undefined;
  if (eventName === 'pull_request_target') {
    const pull = object(event.pull_request);
    const sha = object(pull.head).sha;
    if (typeof sha !== 'string' || !/^[a-f0-9]{40}$/.test(sha)) throw new Error('Invalid event head');
    eventNumber = number(pull.number);
    // Event data may invalidate a known head even if live API reads are unavailable.
    await status(api, sha, 'pending');
  }
  // All serialized events recheck all open PRs: coalesced queued runs lose no work,
  // and a commit/context shared by two PRs must never pass if either PR is invalid.
  let entries = await listOpenHeads(api, true);
  if (invalidateOnly) return true;
  for (let attempt = 0; attempt < 3; attempt++) {
    const results = new Map<number, { pull: Pull; valid: boolean }>();
    let lookupFailed = false;
    for (const [prNumber, knownHead] of entries) {
      try { results.set(prNumber, await inspectPullRequest(api, prNumber, knownHead)); }
      catch { lookupFailed = true; }
    }
    if (lookupFailed) {
      for (const sha of new Set(entries.values())) await status(api, sha, 'failure');
      return false;
    }
    const currentEntries = await listOpenHeads(api, false);
    let changed = headSnapshot(entries) !== headSnapshot(currentEntries);
    for (const [prNumber, result] of results) {
      // PR metadata equality does not prove linked issues kept their labels.
      // Revalidate those dependencies before the final PR snapshot and publication.
      try { result.valid = await evaluate(api, result.pull) && result.valid; }
      catch { result.valid = false; }
      if (entries.get(prNumber) !== result.pull.sha
        || JSON.stringify(await getPull(api, prNumber)) !== JSON.stringify(result.pull)) changed = true;
    }
    // A just-opened PR omitted by an eventually consistent listing cannot grant success.
    if (eventNumber && !currentEntries.has(eventNumber) && (await getPull(api, eventNumber)).state === 'open') changed = true;
    if (changed) {
      entries = await listOpenHeads(api, true);
      continue;
    }
    const grouped = new Map<string, boolean>();
    for (const result of results.values()) {
      const { pull, valid } = result;
      if (pull.state === 'open') grouped.set(pull.sha, (grouped.get(pull.sha) ?? true) && valid);
    }
    for (const [sha, valid] of grouped) await status(api, sha, valid ? 'success' : 'failure');
    return [...grouped.values()].every(Boolean);
  }
  for (const sha of new Set(entries.values())) await status(api, sha, 'failure');
  return false;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.env.GITHUB_REPOSITORY !== REPOSITORY) throw new Error('Unexpected repository');
    const api = new GitHub(process.env.GITHUB_TOKEN ?? '', process.env.GITHUB_API_URL);
    const event = object(JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH ?? '', 'utf8')));
    if (process.argv.slice(2).some((arg) => arg !== '--invalidate')) throw new Error('Invalid argument');
    const valid = await run(api, process.env.GITHUB_EVENT_NAME ?? '', event, process.argv.includes('--invalidate'));
    console.log(valid ? 'Issue traceability validated.' : 'Issue traceability failed. See CONTRIBUTING.md for the issue policy.');
    process.exitCode = valid ? 0 : 1;
  } catch {
    // Never print API response text, credentials, PR bodies, or arbitrary exception messages.
    console.error('Issue traceability could not be verified. Check workflow configuration and rerun.');
    process.exitCode = 1;
  }
}
