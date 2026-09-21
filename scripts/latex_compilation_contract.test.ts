import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertManifest, assertQueueReceipt, assertSourceFiles, compilationOutcome, sha256,
  type CorpusCase, type Execution } from './latex_compilation_contract.ts';
import { withReviewedLanguage } from './latex_corpus_contract.ts';

const root = 'tests/fixtures/latex_research';
const manifest = JSON.parse(readFileSync(`${root}/corpus.json`, 'utf8'));
const fixture = (id: string): CorpusCase => manifest.cases.find((row: CorpusCase) => row.id === id);
const positive = (): Execution => ({ exit_code: 0, signal: null, error_code: null, pdf_sha256: 'a'.repeat(64) });
const negative = (): Execution => ({ exit_code: 1, signal: null, error_code: null, pdf_sha256: null });

test('26 project contexts preserve all28 manifest-pinned source files', () => {
  assertManifest(manifest);
  let count = 0;
  for (const row of manifest.cases) for (const file of row.sources) {
    assert.equal(sha256(readFileSync(`${root}/${file.path}`)), file.sha256);
    count++;
  }
  assert.equal(count, 28);
});

test('manifest refuses dropped cases, duplicated source and flattened project', () => {
  for (const mutation of [
    (value: any) => value.cases.pop(),
    (value: any) => value.cases.push(value.cases[0]),
    (value: any) => value.cases.find((row: any) => row.id === 'P01').sources.pop(),
    (value: any) => value.cases.find((row: any) => row.id === 'P01').sources[2].path = 'P01/one.tex',
    (value: any) => value.cases[0].sources[0].path = 'M01/../private.tex',
    (value: any) => value.cases.find((row: any) => row.id === 'N01').compile_expected = 'passed',
  ]) {
    const changed = structuredClone(manifest);
    mutation(changed);
    assert.throws(() => assertManifest(changed));
  }
});

test('P04 reviewed variant explicitly declares German and P01 changes entrypoint only', () => {
  const german = readFileSync(`${root}/P04/main.tex`, 'utf8');
  assert(withReviewedLanguage(german, 'de').includes('\\hypersetup{pdflang={de}}'));
  const project = fixture('P01');
  const reviewed = project.sources.map(source => ({ ...source, sha256: source.path === project.entrypoint
    ? sha256(withReviewedLanguage(readFileSync(`${root}/${source.path}`, 'utf8'))) : source.sha256 }));
  assert.notEqual(reviewed[0].sha256, project.sources[0].sha256);
  assert.deepEqual(reviewed.slice(1), project.sources.slice(1));
});

test('queue receipt binds exact input hash and requires a hash for delivered TEX', () => {
  const source = fixture('M01');
  const row = { kind: 'latex', fixture: 'M01', status: 'delivered', source_sha256: source.sources[0].sha256,
    output_sha256: 'b'.repeat(64) };
  assertQueueReceipt([row], 'M01', source.sources, source.entrypoint);
  for (const updates of [{ source_sha256: '0'.repeat(64) }, { output_sha256: null }, { status: 'refused' }]) {
    assert.throws(() => assertQueueReceipt([{ ...row, ...updates }], 'M01', source.sources, source.entrypoint));
  }
  assert.throws(() => assertQueueReceipt([], 'M01', source.sources, source.entrypoint));
  assert.throws(() => assertQueueReceipt([row, row], 'M01', source.sources, source.entrypoint));
});

test('project receipt binds all dependencies and original archive identity', () => {
  const project = fixture('P01');
  const row = { kind: 'latex_project', fixture: 'P01', entrypoint: project.entrypoint,
    source_sha256: project.sources[0].sha256, source_files: project.sources,
    archive_sha256: 'c'.repeat(64), downloaded_original_sha256: 'c'.repeat(64) };
  assertQueueReceipt([row], 'P01', project.sources, project.entrypoint);
  for (const updates of [{ source_files: project.sources.slice(1) },
    { source_files: [...project.sources, project.sources[0]] }, { downloaded_original_sha256: 'd'.repeat(64) }]) {
    assert.throws(() => assertQueueReceipt([{ ...row, ...updates }], 'P01', project.sources, project.entrypoint));
  }
  const changed = project.sources.map((source, i) => i === 2 ? { ...source, sha256: '0'.repeat(64) } : source);
  assert.throws(() => assertSourceFiles(changed, project.sources));
});

test('German no-op receipts retain source identity without claiming downloads', () => {
  const source = fixture('P04');
  for (const id of ['P04', 'P04-reviewed']) {
    const row = { kind: 'latex', fixture: id, status: 'no_op', source_sha256: source.sources[0].sha256 };
    assertQueueReceipt([row], id, source.sources, source.entrypoint);
    assert.throws(() => assertQueueReceipt([{ ...row, output_sha256: 'b'.repeat(64) }], id, source.sources, source.entrypoint));
    assert.throws(() => assertQueueReceipt([{ ...row, source_sha256: '0'.repeat(64) }], id, source.sources, source.entrypoint));
  }
});

test('no-op receipts cannot replace other corpus cases', () => {
  const source = fixture('M01');
  const row = { kind: 'latex', fixture: 'M01', status: 'no_op', source_sha256: source.sources[0].sha256 };
  assert.throws(() => assertQueueReceipt([row], 'M01', source.sources, source.entrypoint));
});

test('positive compilation requires two real successful passes and their exact PDFs', () => {
  const source = fixture('M14');
  assert.equal(compilationOutcome(source, [positive(), positive()], '').expectation_met, true);
  assert.equal(compilationOutcome(source, [positive(), positive()], "LaTeX Warning: Reference `eq:mass' undefined.").expectation_met, false);
  for (const runs of [[], [positive()], [positive(), negative()],
    [positive(), { ...positive(), pdf_sha256: null }],
    [positive(), { ...positive(), signal: 'SIGKILL' }],
    [positive(), { ...positive(), error_code: 'ENOENT' }]]) {
    assert.equal(compilationOutcome(source, runs, '').expectation_met, false);
  }
});

const diagnostics: Record<string, string> = {
  N01: '! Undefined control sequence. l.14 \\benchUnknownMacro{a}{b}',
  N02: "! LaTeX Error: File `chapters/absent.tex' not found.",
  N03: "! Package pdftex.def Error: File `absent-image.pdf' not found: using draft setting.",
  N04: 'Runaway argument? {c-d ! File ended while scanning use of \\frac.',
};
for (const [id, log] of Object.entries(diagnostics)) {
  test(`${id} requires its actual negative diagnostic and a real nonzero compiler exit`, () => {
    const source = fixture(id);
    assert.equal(compilationOutcome(source, [negative()], log).expectation_met, true);
    for (const run of [positive(), { ...negative(), signal: 'SIGKILL' },
      { ...negative(), error_code: 'ETIMEDOUT' }, { ...negative(), exit_code: null }]) {
      assert.equal(compilationOutcome(source, [run], log).expectation_met, false);
    }
    assert.equal(compilationOutcome(source, [negative()], "! File `geometry.sty' not found.").expectation_met, false);
  });
}
