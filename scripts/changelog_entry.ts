/** Every pull request records its change in CHANGELOG.md or says why it does not. */
import { execFileSync } from 'node:child_process';
import { appendFileSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const CHANGELOG = 'CHANGELOG.md';
const OPT_OUT = /^changelog:\s*none\b[\s:;,.()\-–—]*(.*)$/i;
const MINIMUM_REASON = 10;

export type Decision = { ok: boolean; message: string };
export type PullRequest = { body: string | null; author: string; authorType: string };

/** Body lines outside HTML comments and fenced code, as the issue traceability check reads them. */
export function plainLines(body: string): string[] {
  const lines: string[] = [];
  let fence: { char: string; length: number } | undefined;
  for (const line of body.replace(/<!--[\s\S]*?(?:-->|$)/g, '').split(/\r?\n/)) {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (marker) {
      if (!fence) fence = { char: marker[1][0], length: marker[1].length };
      else if (marker[1][0] === fence.char && marker[1].length >= fence.length && /^ {0,3}(?:`+|~+)\s*$/.test(line)) fence = undefined;
      continue;
    }
    if (!fence) lines.push(line);
  }
  return lines;
}

/** The reason given on a standalone `Changelog: none` line, or undefined when there is none. */
export function optOutReason(body: string): string | undefined {
  for (const line of plainLines(body)) {
    const match = OPT_OUT.exec(line.trim());
    if (match) return match[1].trim();
  }
  return undefined;
}

/** Bullets that a unified diff of CHANGELOG.md adds or rewrites. */
export function addedEntries(diff: string): string[] {
  return diff.split(/\r?\n/)
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .filter((line) => /^\s*[-*]\s+\S/.test(line));
}

export function decide(pull: PullRequest, diff: string): Decision {
  if (pull.author === 'dependabot[bot]' && pull.authorType === 'Bot') {
    return { ok: true, message: 'Dependabot pull request: its entry is written when the release is prepared.' };
  }
  const entries = addedEntries(diff);
  if (entries.length) {
    return { ok: true, message: `${CHANGELOG} gains or changes ${entries.length} ${entries.length === 1 ? 'entry' : 'entries'}.` };
  }
  const reason = optOutReason(pull.body ?? '');
  if (reason !== undefined && reason.replace(/\s/g, '').length >= MINIMUM_REASON) {
    return { ok: true, message: `No ${CHANGELOG} entry: ${reason}` };
  }
  if (reason !== undefined) {
    return { ok: false, message: `\`Changelog: none\` needs a reason on the same line, for example \`Changelog: none, test-only change\`.` };
  }
  return {
    ok: false,
    message: `Add an entry under \`## [Unreleased]\` in ${CHANGELOG}, or add a line to the pull request description `
      + 'that starts `Changelog: none` and gives the reason, for example `Changelog: none, test-only change`.',
  };
}

export function run(eventPath: string, base = 'HEAD^1', head = 'HEAD'): Decision {
  const event = JSON.parse(readFileSync(eventPath, 'utf8'));
  const pull = event?.pull_request;
  if (!pull || typeof pull.user?.login !== 'string' || typeof pull.user?.type !== 'string'
    || (pull.body !== null && typeof pull.body !== 'string')) {
    throw new Error('The event is not a pull request event');
  }
  const diff = execFileSync('git', ['diff', '--unified=0', base, head, '--', CHANGELOG], { encoding: 'utf8' });
  return decide({ body: pull.body, author: pull.user.login, authorType: pull.user.type }, diff);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!eventPath) throw new Error('GITHUB_EVENT_PATH is not set');
  const decision = run(eventPath, process.argv[2], process.argv[3]);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `### Changelog entry\n\n${decision.message}\n`);
  if (decision.ok) {
    console.log(decision.message);
  } else {
    console.log(`::error title=Changelog entry::${decision.message}`);
    process.exitCode = 1;
  }
}
