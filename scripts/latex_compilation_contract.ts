/** Content-bound compilation observations; never mathematical or accessibility proof. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export interface SourceFile { path: string; sha256: string }
export interface CorpusCase {
  id: string;
  entrypoint: string;
  sources: SourceFile[];
  required_packages: string[];
  compile_expected: 'passed' | 'failed';
}
export interface CorpusManifest {
  schema_version: number;
  corpus_id: string;
  case_count: number;
  source_count: number;
  cases: CorpusCase[];
}
export const sha256 = (bytes: Uint8Array | string) => createHash('sha256').update(bytes).digest('hex');
export const validHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function assertManifest(value: unknown): asserts value is CorpusManifest {
  const manifest = value as CorpusManifest;
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.corpus_id, 'latex-research-444-v1');
  assert.equal(manifest.case_count, 26);
  assert.equal(manifest.source_count, 28);
  const ids = [...Array.from({ length: 18 }, (_, i) => `M${String(i + 1).padStart(2, '0')}`),
    ...['P', 'N'].flatMap(prefix => Array.from({ length: 4 }, (_, i) => `${prefix}0${i + 1}`))].sort();
  assert.deepEqual(manifest.cases.map(row => row.id).sort(), ids);
  const paths: string[] = [];
  for (const fixture of manifest.cases) {
    assert.equal(fixture.entrypoint, `${fixture.id}/main.tex`);
    assert.equal(fixture.compile_expected, fixture.id.startsWith('N') ? 'failed' : 'passed');
    assert.equal(fixture.sources.length, fixture.id === 'P01' ? 3 : 1);
    assert(fixture.sources.some(file => file.path === fixture.entrypoint));
    for (const file of fixture.sources) {
      assert(file.path.startsWith(`${fixture.id}/`));
      assert(file.path.split('/').every(part => /^[A-Za-z0-9_-]+(?:\.tex)?$/.test(part)), 'Unsafe source path');
      assert(file.path.endsWith('.tex'));
      assert(validHash(file.sha256));
      paths.push(file.path);
    }
    assert(fixture.required_packages.every(name => /^[a-zA-Z0-9-]+$/.test(name)));
  }
  assert.equal(paths.length, 28);
  assert.equal(new Set(paths).size, paths.length);
  assert.deepEqual(manifest.cases.find(row => row.id === 'P01')!.sources.map(file => file.path).sort(),
    ['P01/chapters/one.tex', 'P01/macros.tex', 'P01/main.tex']);
}

export function assertSourceFiles(actual: SourceFile[], expected: SourceFile[]) {
  const sorted = (files: SourceFile[]) => files.map(file => ({ path: file.path, sha256: file.sha256 }))
    .sort((a, b) => a.path.localeCompare(b.path));
  assert.equal(new Set(actual.map(file => file.path)).size, actual.length, 'Duplicate source files');
  assert(actual.every(file => validHash(file.sha256)));
  assert.deepEqual(sorted(actual), sorted(expected), 'Exact source context changed');
}

export function assertQueueReceipt(rows: any[], fixture: string, expectedFiles: SourceFile[], entrypoint: string) {
  const project = fixture === 'P01' || fixture === 'P01-reviewed';
  const matches = rows.filter(row => row.kind === (project ? 'latex_project' : 'latex') && row.fixture === fixture);
  assert.equal(matches.length, 1, `Exactly one queue receipt is required for ${fixture}`);
  const receipt = matches[0];
  assert.equal(receipt.source_sha256, expectedFiles.find(file => file.path === entrypoint)?.sha256,
    `Queue source hash differs for ${fixture}`);
  if (project) {
    assert.equal(receipt.entrypoint, entrypoint);
    assertSourceFiles(receipt.source_files, expectedFiles);
    assert(validHash(receipt.archive_sha256));
    assert.equal(receipt.downloaded_original_sha256, receipt.archive_sha256);
  } else {
    assert(['delivered', 'refused', 'no_op'].includes(receipt.status));
    if (receipt.status === 'no_op') assert(['P04', 'P04-reviewed'].includes(fixture), 'Only measured German cases may be no-op');
    if (receipt.status === 'delivered') assert(validHash(receipt.output_sha256), 'Delivered TEX requires an output hash');
    else assert(receipt.output_sha256 == null, 'Withheld or no-op TEX cannot claim a download');
  }
  return receipt;
}

export interface Execution {
  exit_code: number | null;
  signal: string | null;
  error_code: string | null;
  pdf_sha256: string | null;
}

export function compilationOutcome(fixture: CorpusCase, passes: Execution[], log: string) {
  const executed = passes.length > 0 && passes.every(pass => pass.error_code === null &&
    pass.signal === null && Number.isInteger(pass.exit_code));
  const passed = executed && passes.every(pass => pass.exit_code === 0);
  const negativePatterns: Record<string, RegExp[]> = {
    N01: [/Undefined control sequence/i, /benchUnknownMacro/],
    N02: [/chapters\/absent(?:\.tex)?/i, /not found|can't find|cannot find/i],
    N03: [/absent-image\.pdf/i, /not found|cannot find|unable to load|could not locate/i],
    N04: [/Runaway argument|File ended while scanning use/i],
  };
  const diagnosticMatched = fixture.compile_expected === 'failed' &&
    (negativePatterns[fixture.id]?.every(pattern => pattern.test(log)) ?? false);
  const unresolvedReferences = /(?:Reference|Citation)[^\n]*undefined|There were undefined (?:references|citations)/i.test(log);
  // An exit code alone cannot establish success, or the intended negative failure.
  const expectationMet = fixture.compile_expected === 'passed'
    ? passed && passes.length === 2 && passes.every(pass => validHash(pass.pdf_sha256)) && !unresolvedReferences
    : executed && !passed && passes.length === 1 && diagnosticMatched;
  return {
    status: executed ? (passed ? 'passed' : 'failed') : 'unavailable',
    expectation_met: expectationMet,
    expected_negative_diagnostic_observed: diagnosticMatched,
    unresolved_references_observed: unresolvedReferences,
  };
}
