/** Compile only hash-pinned synthetic fixtures and the exact queue downloads. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { withReviewedLanguage } from './latex_corpus_contract.ts';

const root = resolve('tests/fixtures/latex_validation');
const output = resolve(process.env.STACK_EVIDENCE_DIR || 'test-results/document-stack');
const compiler = process.env.STACK_TEX_COMPILER || 'pdflatex';
assert(['pdflatex', 'lualatex'].includes(compiler), 'Use a supported TeX engine from PATH');
const version = execFileSync(compiler, ['--version'], { encoding: 'utf8', timeout: 30000 }).split('\n')[0];
const manifest = JSON.parse(await readFile(resolve(root, 'corpus.json'), 'utf8'));
const queue = JSON.parse(await readFile(resolve(output, 'report.json'), 'utf8'));
assert.equal(queue.failures.length, 0, 'Compile only outputs from a successful queue replay');
const sha = (data: Uint8Array) => createHash('sha256').update(data).digest('hex');
const cases: object[] = [];
const failures: string[] = [];
for (const originalFixture of manifest.cases) for (const reviewed of [false, true]) {
  const fixture = { ...originalFixture, id: reviewed ? `${originalFixture.id}-reviewed` : originalFixture.id,
    file: reviewed ? `reviewed-${originalFixture.file}` : originalFixture.file };
  const baseline = await readFile(resolve(root, originalFixture.file));
  assert.equal(sha(baseline), originalFixture.sha256);
  const source = reviewed ? Buffer.from(withReviewedLanguage(baseline.toString('utf8'))) : baseline;
  fixture.sha256 = sha(source);
  const receipt = queue.evidence.find((row: any) => row.kind === 'latex' && row.fixture === fixture.id);
  assert(receipt, `Missing queue evidence for ${fixture.id}`);
  for (const kind of ['source', 'saved']) {
    if (kind === 'saved' && (!reviewed || ['N03', 'N04'].includes(originalFixture.id))) {
      assert.equal(receipt.status, 'refused');
      cases.push({ fixture: fixture.id, kind, status: 'not_run', reason: 'output_withheld' });
      continue;
    }
    const file = kind === 'source' ? resolve(output, `input-${fixture.file}`) : resolve(output, `saved-${fixture.file}`);
    const bytes = await readFile(file);
    assert.equal(sha(bytes), kind === 'source' ? fixture.sha256 : receipt.output_sha256);
    const directory = resolve(output, 'compiler', fixture.id, kind);
    await mkdir(directory, { recursive: true });
    const result = spawnSync(compiler, ['-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error',
      '-output-directory', directory, file], { cwd: directory, encoding: 'utf8', timeout: 60000, maxBuffer: 4 * 1024 * 1024 });
    await writeFile(resolve(directory, 'compiler-output.txt'), (result.stdout || '') + (result.stderr || ''));
    // A killed/missing compiler is not a successful negative control.
    const executed = !result.error && result.signal === null && Number.isInteger(result.status);
    const passed = result.status === 0;
    const expectationMet = executed && passed === (fixture.compile_expected === 'passed');
    cases.push({ fixture: fixture.id, kind, sha256: sha(bytes), exit_code: result.status,
      status: executed ? (passed ? 'passed' : 'failed') : 'unavailable', expectation_met: expectationMet });
    if (!expectationMet) failures.push(`${fixture.id} ${kind}: compiler expectation not met`);
  }
}
await writeFile(resolve(output, 'compilation.json'), JSON.stringify({ compiler: version,
  queue_report_sha256: sha(await readFile(resolve(output, 'report.json'))),
  scope: 'Compilation only; no PDF/UA, mathematical fidelity or assistive-technology claim', cases, failures }, null, 2));
assert.equal(failures.length, 0, failures.join('\n'));
console.log('PASS LaTeX compilation: 11 originals, 11 explicitly authored language variants, 9 queue downloads');
