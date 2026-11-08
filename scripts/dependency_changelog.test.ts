import assert from 'node:assert/strict';
import test from 'node:test';
import {
  compareSemver, insertDependencyEntries, missingPulls, parseArgs, parseTag,
  previousTag, referencesPull, renderEntry,
} from './dependency_changelog.ts';

const pull = (prNumber: number, title: string) => ({
  number: prNumber, title, url: `https://github.com/Aelira-AI/aelira-core/pull/${prNumber}`,
});

const sample = [
  '# Changelog',
  '',
  '## [Unreleased]',
  '',
  '### Added',
  '',
  '- New surface.',
  '',
  '### Fixed',
  '',
  '- Old breakage.',
  '',
  '### Security',
  '',
  '- Patched CVE.',
  '',
  '## [0.12.0] - 2026-10-01',
  '',
  '- First.',
  '',
].join('\n');

test('previousTag picks the highest strictly older semver tag', () => {
  const tags = ['v0.1.0', 'v0.9.0', 'v0.13.0', 'v0.13.0-rc.1', 'not-a-tag', 'release-7'];
  assert.equal(previousTag(tags, 'v0.14.0'), 'v0.13.0');
  assert.equal(previousTag(tags, 'v0.13.0'), 'v0.9.0');
  assert.equal(previousTag(tags, 'v0.2.0'), 'v0.1.0');
  assert.equal(previousTag(tags, 'v0.1.0'), undefined);
  assert.throws(() => previousTag(tags, 'latest'), /vX\.Y\.Z/);
});

test('semver comparison is numeric, not lexical', () => {
  assert.ok(compareSemver(parseTag('v0.9.0')!, parseTag('v0.13.0')!) < 0);
  assert.ok(compareSemver(parseTag('v1.0.0')!, parseTag('v0.99.99')!) > 0);
  assert.equal(parseTag('v0.1'), undefined);
});

test('renderEntry lowercases the title and appends the pull url', () => {
  assert.equal(
    renderEntry(pull(949, 'Bump webauthn from 2.7.0 to 3.0.1')),
    '- bump webauthn from 2.7.0 to 3.0.1 (https://github.com/Aelira-AI/aelira-core/pull/949)',
  );
  assert.equal(renderEntry(pull(950, '  Bump   lodash\n from 1 to 2 ')), '- bump lodash from 1 to 2 (https://github.com/Aelira-AI/aelira-core/pull/950)');
  assert.ok(!/[^\x00-\x7F]/.test(renderEntry(pull(951, 'Bump emoji-pkg \u{1F600} to 2'))));
  assert.throws(() => renderEntry({ number: 5, title: 'Bump x', url: 'https://evil.example/pull/5' }), /Invalid/);
  assert.throws(() => renderEntry(pull(0, 'Bump x')), /Invalid/);
});

test('referencesPull matches whole numbers only', () => {
  assert.equal(referencesPull('bump (https://github.com/Aelira-AI/aelira-core/pull/949)', 949), true);
  assert.equal(referencesPull('see #94', 949), false);
  assert.equal(referencesPull('version 1.9490.2', 949), false);
});

test('insert creates ### Dependencies between Fixed and Security', () => {
  const result = insertDependencyEntries(sample, [pull(949, 'Bump webauthn from 2.7.0 to 3.0.1')]);
  assert.equal(result.added, 1);
  const lines = result.changelog.split('\n');
  const position = (needle: string) => lines.indexOf(needle);
  assert.ok(position('### Fixed') < position('### Dependencies'));
  assert.ok(position('### Dependencies') < position('### Security'));
  assert.ok(result.changelog.includes(
    '- bump webauthn from 2.7.0 to 3.0.1 (https://github.com/Aelira-AI/aelira-core/pull/949)',
  ));
  assert.ok(result.changelog.includes('- Patched CVE.'));
  assert.ok(result.changelog.includes('## [0.12.0] - 2026-10-01'));
});

test('insert without Security or Fixed still follows Keep a Changelog order', () => {
  const only = '# Changelog\n\n## [Unreleased]\n\n### Added\n\n- New surface.\n';
  const result = insertDependencyEntries(only, [pull(1, 'Bump a from 1 to 2')]);
  assert.ok(result.changelog.includes('- bump a from 1 to 2 (https://github.com/Aelira-AI/aelira-core/pull/1)'));
  assert.ok(result.changelog.indexOf('### Added') < result.changelog.indexOf('### Dependencies'));
  const empty = '# Changelog\n\n## [Unreleased]\n\n- Stray bullet.\n';
  assert.ok(insertDependencyEntries(empty, [pull(2, 'Bump b')]).changelog.includes('### Dependencies'));
});

test('insert merges into an existing Dependencies subsection and is idempotent', () => {
  const first = insertDependencyEntries(sample, [pull(949, 'Bump webauthn from 2.7.0 to 3.0.1')]);
  const second = insertDependencyEntries(first.changelog, [
    pull(949, 'Bump webauthn from 2.7.0 to 3.0.1'),
    pull(950, 'Bump lodash from 4.17.20 to 4.17.21'),
  ]);
  assert.equal(second.added, 1);
  assert.ok(second.changelog.includes('- bump lodash from 4.17.20 to 4.17.21 (https://github.com/Aelira-AI/aelira-core/pull/950)'));
  const third = insertDependencyEntries(second.changelog, [
    pull(949, 'Bump webauthn from 2.7.0 to 3.0.1'),
    pull(950, 'Bump lodash from 4.17.20 to 4.17.21'),
  ]);
  assert.equal(third.added, 0);
  assert.equal(third.changelog, second.changelog);
});

test('insert fills an empty Dependencies subsection in place', () => {
  const source = '## [Unreleased]\n\n### Dependencies\n\n### Security\n\n- Patched CVE.\n';
  const result = insertDependencyEntries(source, [pull(3, 'Bump c')]);
  assert.equal(result.added, 1);
  assert.ok(result.changelog.includes('### Dependencies\n\n- bump c (https://github.com/Aelira-AI/aelira-core/pull/3)\n\n### Security'));
});

test('insert without an Unreleased section fails', () => {
  assert.throws(() => insertDependencyEntries('# Changelog\n\n## [0.1.0]\n', [pull(1, 'Bump a')]), /Unreleased/);
});

const windowPulls = [pull(949, 'Bump a'), pull(950, 'Bump b')];
const stubApi = (pulls: typeof windowPulls) => ({
  request: async (path: string) => {
    if (path.includes('/git/refs/tags')) return [];
    if (/\/pulls\/[0-9]+$/.test(path)) {
      const found = pulls.find((entry) => path.endsWith(`/${entry.number}`));
      return found ? { number: found.number, title: found.title, merged_at: '2026-11-06T00:00:00Z', html_url: found.url } : null;
    }
    return pulls.map(({ number: prNumber }) => ({ number: prNumber, pull_request: {} }));
  },
});

test('missingPulls passes when every number is referenced', async () => {
  const changelog = '## [Unreleased]\n\n### Dependencies\n\n- bump a (#949).\n\n### Security\n\n- bump b landed (https://github.com/Aelira-AI/aelira-core/pull/950).\n';
  assert.deepEqual(await missingPulls(stubApi(windowPulls) as never, 'v0.14.0', changelog), []);
});

test('missingPulls lists missing numbers', async () => {
  const changelog = '## [Unreleased]\n\n### Dependencies\n\n- bump a (#949).\n';
  const missing = await missingPulls(stubApi(windowPulls) as never, 'v0.14.0', changelog);
  assert.deepEqual(missing.map((entry) => entry.number), [950]);
});

test('parseArgs accepts exactly one mode and a semver tag', () => {
  assert.deepEqual(parseArgs(['--check', '--tag', 'v0.14.0']), { mode: 'check', tag: 'v0.14.0' });
  assert.deepEqual(parseArgs(['--insert', '--tag', 'v1.2.3']), { mode: 'insert', tag: 'v1.2.3' });
  for (const argv of [['--check'], ['--check', 'v0.1.0'], ['--check', '--tag', '0.1.0'], ['--insert', '--tag', 'v0.1'], ['--write', '--tag', 'v0.1.0'], []]) {
    assert.throws(() => parseArgs(argv as string[]), /Usage/);
  }
});
