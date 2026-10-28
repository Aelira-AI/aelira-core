import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { addedEntries, decide, optOutReason, run } from './changelog_entry.ts';

const human = (body: string | null = 'Closes #851') => ({ body, author: 'contributor', authorType: 'User' });
const diff = (...lines: string[]) =>
  ['diff --git a/CHANGELOG.md b/CHANGELOG.md', '--- a/CHANGELOG.md', '+++ b/CHANGELOG.md', '@@ -8,0 +9,3 @@', ...lines].join('\n');

test('an added bullet passes', () => {
  const decision = decide(human(), diff('+### Fixed', '+', '+- The scan no longer stalls.'));
  assert.equal(decision.ok, true);
  assert.match(decision.message, /1 entry/);
});

test('a rewritten bullet and a nested bullet both count', () => {
  assert.equal(addedEntries(diff('-- Old wording.', '+- New wording.', '+  - a nested detail')).length, 2);
});

test('headings, prose, removed lines and the file header are not entries', () => {
  assert.deepEqual(addedEntries(diff('+### Fixed', '+', '+Some prose.', '-- A removed entry.')), []);
  assert.equal(decide(human(), diff('+### Fixed', '-- A removed entry.')).ok, false);
});

test('no entry and no reason fails and says how to fix it', () => {
  const decision = decide(human(), '');
  assert.equal(decision.ok, false);
  assert.match(decision.message, /## \[Unreleased\]/);
  assert.match(decision.message, /Changelog: none/);
});

test('a standalone Changelog: none line with a reason passes', () => {
  for (const line of ['Changelog: none, test-only change', 'changelog: NONE - documentation only', 'Changelog: none (CI runner label only)']) {
    const decision = decide(human(`Closes #851\n\n${line}\n`), '');
    assert.equal(decision.ok, true, line);
    assert.match(decision.message, /^No CHANGELOG\.md entry: /);
  }
});

test('Changelog: none without a real reason fails', () => {
  for (const line of ['Changelog: none', 'Changelog: none.', 'Changelog: none, n/a']) {
    const decision = decide(human(line), '');
    assert.equal(decision.ok, false, line);
    assert.match(decision.message, /needs a reason/);
  }
});

test('the skip line only counts outside comments, fenced code and running prose', () => {
  const template = '## Changelog\n\n<!-- write `Changelog: none, test-only change` here if needed -->\n';
  assert.equal(optOutReason(template), undefined);
  assert.equal(optOutReason('```\nChangelog: none, an example in a fence\n```'), undefined);
  assert.equal(optOutReason('We wrote Changelog: none, inside a sentence.'), undefined);
  assert.equal(optOutReason('Changelog: nonessential wording'), undefined);
  assert.equal(decide(human(null), '').ok, false);
});

test('only the real Dependabot bot is exempt', () => {
  assert.equal(decide({ body: '', author: 'dependabot[bot]', authorType: 'Bot' }, '').ok, true);
  assert.equal(decide({ body: '', author: 'dependabot[bot]', authorType: 'User' }, '').ok, false);
  assert.equal(decide({ body: '', author: 'dependabot', authorType: 'User' }, '').ok, false);
});

test('run reads the event and the merge commit diff from git', () => {
  const dir = mkdtempSync(join(tmpdir(), 'changelog-entry-'));
  const git = (...args: string[]) => execFileSync('git', ['-C', dir, ...args], { encoding: 'utf8' });
  try {
    git('init', '-q');
    git('config', 'user.email', 'test@example.invalid');
    git('config', 'user.name', 'Test');
    writeFileSync(join(dir, 'CHANGELOG.md'), '# Changelog\n\n## [Unreleased]\n\n## [0.1.0]\n\n- First.\n');
    writeFileSync(join(dir, 'app.txt'), 'one\n');
    git('add', '.');
    git('commit', '-q', '-m', 'base');
    writeFileSync(join(dir, 'app.txt'), 'two\n');
    git('commit', '-q', '-am', 'code only');
    const event = join(dir, 'event.json');
    writeFileSync(event, JSON.stringify({ pull_request: { body: 'Closes #851', user: { login: 'contributor', type: 'User' } } }));
    const cwd = process.cwd();
    process.chdir(dir);
    try {
      assert.equal(run(event).ok, false);
      writeFileSync(join(dir, 'CHANGELOG.md'), '# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Two.\n\n## [0.1.0]\n\n- First.\n');
      git('commit', '-q', '-am', 'with entry');
      assert.equal(run(event).ok, true);
      assert.throws(() => run(join(dir, 'app.txt')), /JSON|pull request/);
    } finally {
      process.chdir(cwd);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
