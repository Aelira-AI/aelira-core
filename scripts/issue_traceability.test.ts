import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { GitHub, parseReferences, checkPullRequest, run } from './issue_traceability.ts';

const repo = 'Aelira-AI/aelira-core';
const sha = 'a'.repeat(40);
const nextSha = 'b'.repeat(40);
const pull = (body: string | null = 'Closes #349', extra = {}) => ({
  number: 350, state: 'open', body, head: { sha },
  base: { ref: 'main', repo: { full_name: repo } },
  user: { login: 'contributor', type: 'User' }, changed_files: 1, ...extra,
});
const issue = (number = 349, extra = {}) => ({
  number, repository_url: `https://api.github.com/repos/${repo}`,
  labels: [{ name: 'bug' }, { name: 'quality' }], ...extra,
});
function fixture(options: {
  pulls?: unknown[]; issue?: unknown; files?: unknown[]; failIssue?: boolean;
  failStatus?: boolean; failPull?: boolean;
} = {}) {
  const statuses: { path: string; body: Record<string, unknown> }[] = [];
  let reads = 0;
  const api = {
    async request(path: string, method = 'GET', body?: Record<string, unknown>) {
      if (method === 'POST') {
        if (options.failStatus) throw new Error('private transport detail');
        statuses.push({ path, body: body! });
        return { state: body!.state, context: body!.context };
      }
      if (path.includes('/issues/')) {
        if (options.failIssue) throw new Error('private transport detail');
        return options.issue ?? issue();
      }
      if (path.includes('/files?')) return options.files ?? [{ filename: 'requirements.txt', status: 'modified' }];
      if (path.includes('/pulls?')) return [{ number: 350, head: { sha } }];
      if (options.failPull) throw new Error('private transport detail');
      const values = options.pulls ?? [pull()];
      return values[Math.min(reads++, values.length - 1)];
    },
  };
  return { api, statuses };
}

test('closing references are explicit, same-repository, standalone declarations', () => {
  assert.deepEqual(parseReferences('Closes #349\nFixes https://github.com/Aelira-AI/aelira-core/issues/351\nResolves Aelira-AI/aelira-core#352'), [349, 351, 352]);
  for (const text of ['No issue', '<!--\nCloses #349\n-->', '<!--\nCloses #349', '```md\nCloses #349\n```', '~~~\nCloses #349\n~~~', '````\n```\nCloses #349\n````', '<pre>\nCloses #349\n</pre>', '<code>\nCloses #349\n</code>', '`Closes #349`', '> Closes #349', '    Closes #349', 'Example: Closes #349']) {
    assert.deepEqual(parseReferences(text), [], text);
  }
  for (const text of ['Closes other/repo#349', 'Closes https://github.com/other/repo/issues/349', 'Closes #0', 'Closes #349 trailing text', 'Closes #349\nCloses other/repo#2']) {
    assert.throws(() => parseReferences(text), Error, text);
  }
});

test('Markdown code, quote, hidden link-title, HTML and image continuations do not count', () => {
  for (const body of [
    '`example\nCloses #349\n`', '<div><pre>\nCloses #349\n</pre></div>',
    '> example\nCloses #349', '[sample]: https://example.org/ "\nCloses #349\n"',
    '<a title="\nCloses #349\n">example</a>', '![example\nCloses #349\n](https://example.org/a.png)',
    '<div><pre>\n\nCloses #349\n\n</pre></div>',
    '<![CDATA[\n\nCloses #349\n\n]]>', '<?example\n\nCloses #349\n\n?>',
    '<!DOCTYPE\n\nCloses #349\n\n>', '<pre><pre></pre>\n\nCloses #349\n\n</pre>',
  ]) assert.deepEqual(parseReferences(body), [], body);
  assert.deepEqual(parseReferences('## Related Issues\n\nCloses #349\n\n## Testing\n\nTests pass.'), [349]);
  assert.deepEqual(parseReferences('```html\n<pre>Closes #12</pre>\n```\n\nCloses #349'), [349]);
});

test('genuine labelled issue succeeds on exact live head, not event/base SHA', async () => {
  const { api, statuses } = fixture();
  assert.equal(await checkPullRequest(api, 350), true);
  assert.deepEqual(statuses.map(({ path, body }) => [path, body.state, body.context]), [
    [`/repos/${repo}/statuses/${sha}`, 'pending', 'Issue traceability'],
    [`/repos/${repo}/statuses/${sha}`, 'success', 'Issue traceability'],
  ]);
});

for (const [name, options] of Object.entries({
  'missing reference': { pulls: [pull(null)] },
  'nonexistent issue': { failIssue: true },
  'PR masquerading as issue': { issue: issue(349, { pull_request: {} }) },
  'wrong issue number': { issue: issue(350) },
  'cross-repo API result': { issue: issue(349, { repository_url: 'https://api.github.com/repos/other/repo' }) },
  'one label': { issue: issue(349, { labels: [{ name: 'bug' }] }) },
  'duplicate labels': { issue: issue(349, { labels: [{ name: 'bug' }, { name: 'bug' }] }) },
  'malformed labels': { issue: issue(349, { labels: ['bug', 'quality'] }) },
})) {
  test(`fails closed: ${name}`, async () => {
    const { api, statuses } = fixture(options);
    assert.equal(await checkPullRequest(api, 350), false);
    assert.equal(statuses.at(-1)?.body.state, 'failure');
    assert.ok(!JSON.stringify(statuses).includes('private transport detail'));
  });
}

test('authenticated Dependabot has only a dependency-file exception', async () => {
  const bot = { login: 'dependabot[bot]', type: 'Bot' };
  assert.equal(await checkPullRequest(fixture({ pulls: [pull(null, { user: bot })] }).api, 350), true);
  for (const extra of [{ user: { ...bot, type: 'User' } }, { user: { login: 'dependabot', type: 'Bot' } }, { title: 'dependabot dependency update', labels: [{ name: 'dependencies' }] }]) {
    assert.equal(await checkPullRequest(fixture({ pulls: [pull(null, extra)] }).api, 350), false);
  }
  for (const files of [[], [{ filename: 'src/api/main.py', status: 'modified' }], [{ filename: '.github/workflows/ci.yml', status: 'modified' }], [{ filename: 'requirements.txt', status: 'removed' }]]) {
    assert.equal(await checkPullRequest(fixture({ pulls: [pull(null, { user: bot })], files }).api, 350), false);
  }
});

test('head or body mutation during evaluation cannot receive stale success', async () => {
  for (const updated of [pull(null), pull(null, { head: { sha: nextSha } })]) {
    const { api, statuses } = fixture({ pulls: [pull(), updated] });
    assert.equal(await checkPullRequest(api, 350), false);
    assert.ok(!statuses.some(({ body }) => body.state === 'success'));
    assert.equal(statuses.at(-1)?.path, `/repos/${repo}/statuses/${updated.head.sha}`);
  }
});

test('missing PR and failed status writes cannot report success', async () => {
  for (const options of [{ failPull: true }, { failStatus: true }]) {
    await assert.rejects(() => checkPullRequest(fixture(options).api, 350));
  }
});

test('initial API lookup failure invalidates a known event/list head', async () => {
  const { api, statuses } = fixture({ failPull: true });
  await assert.rejects(() => checkPullRequest(api, 350, sha));
  assert.equal(statuses.at(-1)?.path, `/repos/${repo}/statuses/${sha}`);
  assert.equal(statuses.at(-1)?.body.state, 'failure');
});

test('trusted-test preflight invalidates statuses without granting success', async () => {
  const { api, statuses } = fixture();
  assert.equal(await run(api, 'pull_request_target', { pull_request: { number: 350, head: { sha: nextSha }, body: 'ignored' } }, true), true);
  assert.ok(statuses.every(({ body }) => body.state === 'pending'));
  assert.ok(statuses.some(({ path }) => path.endsWith(nextSha)));
  assert.ok(statuses.some(({ path }) => path.endsWith(sha)));
});

test('malformed status response cannot report success', async () => {
  const { api } = fixture();
  const original = api.request;
  api.request = async (path, method, body) => method === 'POST' ? {} : original(path, method, body);
  await assert.rejects(() => checkPullRequest(api, 350));
});

test('continuous PR edits exhaust retries with a failure on latest observed head', async () => {
  const pulls = Array.from({ length: 4 }, (_, index) => pull(`Closes #349\n\nRevision ${index}`, { head: { sha: String(index).repeat(40) } }));
  const { api, statuses } = fixture({ pulls });
  assert.equal(await checkPullRequest(api, 350), false);
  assert.equal(statuses.at(-1)?.path, `/repos/${repo}/statuses/${'3'.repeat(40)}`);
  assert.equal(statuses.at(-1)?.body.state, 'failure');
  assert.ok(!statuses.some(({ body }) => body.state === 'success'));
});

test('closed PRs are not given a new success status', async () => {
  const { api, statuses } = fixture({ pulls: [pull(null, { state: 'closed' })] });
  assert.equal(await checkPullRequest(api, 350), true);
  assert.equal(statuses.length, 0);
});

test('truncated or mismatched dependency file count is not exempt', async () => {
  const { api } = fixture({ pulls: [pull(null, { user: { login: 'dependabot[bot]', type: 'Bot' }, changed_files: 2 })] });
  assert.equal(await checkPullRequest(api, 350), false);
});

test('label removal invalidates a previous success when an issue event is processed', async () => {
  const { api, statuses } = fixture();
  assert.equal(await checkPullRequest(api, 350), true);
  const original = api.request;
  api.request = async (path, method, body) => path.includes('/issues/')
    ? issue(349, { labels: [{ name: 'bug' }] }) : original(path, method, body);
  assert.equal(await run(api, 'issues', {}), false);
  assert.equal(statuses.at(-1)?.body.state, 'failure');
});

test('label removal during the first PR recheck prevents grouped success', async () => {
  const { api, statuses } = fixture();
  const original = api.request;
  let pullReads = 0;
  let labelRemoved = false;
  api.request = async (path, method, body) => {
    if (/\/pulls\/350$/.test(path) && ++pullReads === 2) labelRemoved = true;
    if (path.includes('/issues/') && labelRemoved) return issue(349, { labels: [{ name: 'bug' }] });
    return original(path, method, body);
  };
  assert.equal(await run(api, 'workflow_dispatch', {}), false);
  assert.ok(!statuses.some(({ body }) => body.state === 'success'));
  assert.equal(statuses.at(-1)?.body.state, 'failure');
});

test('issue-label events recheck open PRs and ignore event-supplied body/head', async () => {
  const { api, statuses } = fixture({ pulls: [pull(null)] });
  assert.equal(await run(api, 'issues', { issue: { number: 349 } }), false);
  assert.equal(statuses.at(-1)?.body.state, 'failure');
  assert.equal(await run(api, 'label', { action: 'deleted' }), false);
  await assert.rejects(() => run(api, 'push', {}));
});

test('two PRs sharing a head cannot overwrite an invalid result with success', async () => {
  for (const invalidFirst of [true, false]) {
    const { api, statuses } = fixture();
    const original = api.request;
    api.request = async (path, method, body) => {
      if (path.includes('/pulls?')) return [350, 351].map((number) => ({ number, head: { sha } }));
      const match = /\/pulls\/(350|351)$/.exec(path);
      if (match) return pull(Number(match[1]) === (invalidFirst ? 350 : 351) ? null : 'Closes #349', { number: Number(match[1]) });
      return original(path, method, body);
    };
    assert.equal(await run(api, 'workflow_dispatch', {}), false);
    assert.equal(statuses.at(-1)?.body.state, 'failure');
    assert.ok(!statuses.some(({ body }) => body.state === 'success'));
  }
});

test('API credentials cannot be sent to alternate origins or redirects', async () => {
  assert.throws(() => new GitHub('token', 'https://example.org'));
  assert.throws(() => new GitHub('bad\ntoken'));
  let seen: RequestInit | undefined;
  const api = new GitHub('test_token', undefined, async (_url, init) => {
    seen = init;
    return new Response('{}', { status: 302 });
  });
  await assert.rejects(() => api.request(`/repos/${repo}/pulls/350`));
  assert.equal(seen?.redirect, 'error');
  await assert.rejects(() => api.request('https://example.org'));
});

test('workflow executes trusted base code only and runs executable tests', () => {
  const workflow = readFileSync(new URL('../.github/workflows/issue-traceability.yml', import.meta.url), 'utf8');
  assert.match(workflow, /pull_request_target:/);
  assert.match(workflow, /statuses: write/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /ref: refs\/heads\/main/);
  assert.match(workflow, /--test scripts\/issue_traceability.test.ts/);
  assert.doesNotMatch(workflow, /pull_request\.head|pull_request\.body|secrets\./);
  assert.match(workflow, /labeled, unlabeled/);
  assert.ok(workflow.indexOf('--invalidate') < workflow.indexOf('--test scripts/issue_traceability.test.ts'));
  const tests = readFileSync(new URL('../.github/workflows/issue-traceability-tests.yml', import.meta.url), 'utf8');
  assert.match(tests, /pull_request:/);
  assert.match(tests, /contents: read/);
  assert.doesNotMatch(tests, /statuses: write|pull_request_target|GITHUB_TOKEN/);
});
