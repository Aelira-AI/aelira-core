/** Compile intact research contexts, reviewed variants and exact TEX downloads. */
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { readFile, writeFile, mkdir, mkdtemp, unlink } from 'node:fs/promises';
import { basename, dirname, resolve, relative } from 'node:path';
import { withReviewedLanguage } from './latex_corpus_contract.ts';
import { assertManifest, assertQueueReceipt, assertSourceFiles, compilationOutcome, sha256,
  type CorpusCase, type Execution, type SourceFile } from './latex_compilation_contract.ts';

const root = resolve('tests/fixtures/latex_research');
const output = resolve(process.env.STACK_EVIDENCE_DIR || 'test-results/document-stack');
const compiler = process.env.STACK_TEX_COMPILER || 'pdflatex';
assert(['pdflatex', 'lualatex'].includes(compiler), 'Use a supported TeX engine from PATH');
const manifestBytes = await readFile(resolve(root, 'corpus.json'));
const manifest: unknown = JSON.parse(manifestBytes.toString('utf8'));
assertManifest(manifest);
const queueBytes = await readFile(resolve(output, 'report.json'));
const queue = JSON.parse(queueBytes.toString('utf8'));
assert.deepEqual(queue.failures, [], 'Compile only outputs from a successful queue replay');
assert(Array.isArray(queue.evidence));
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
assert.equal(queue.revision, revision, 'Queue evidence belongs to another repository revision');
const harnessFiles = ['scripts/verify_document_stack.ts', 'scripts/verify_latex_corpus_stack.ts',
  'scripts/latex_corpus_contract.ts', 'scripts/verify_latex_corpus_compilation.ts',
  'scripts/latex_compilation_contract.ts', 'tests/fixtures/latex_research/corpus.json'];
const harnessHashes = Object.fromEntries(await Promise.all(harnessFiles.map(async path =>
  [path, sha256(await readFile(path))])));
for (const path of [harnessFiles[0], harnessFiles[1], harnessFiles[2], harnessFiles[5]]) {
  assert.equal(queue.harness_sha256?.[path], harnessHashes[path], `Queue harness changed: ${path}`);
}
await mkdir(output, { recursive: true });
// Fresh contexts prevent stale PDFs and auxiliary files satisfying later runs.
const runRoot = await mkdtemp(resolve(output, 'compiler-run-'));
const versionRun = spawnSync(compiler, ['--version'], { encoding: 'utf8', timeout: 30000, maxBuffer: 65536 });
const failures: string[] = [];
const versionAvailable = !versionRun.error && versionRun.signal === null && versionRun.status === 0;
if (!versionAvailable) failures.push('Compiler version probe unavailable');
const compilerVersion = versionAvailable ? versionRun.stdout.trim().slice(0, 4096) : null;
await writeFile(resolve(runRoot, 'compiler-version.txt'), (versionRun.stdout || '') + (versionRun.stderr || ''));

// Pin installed package bytes as well as engine version, without exposing paths.
const packageNames = [...new Set([...manifest.cases.flatMap(row => row.required_packages), 'hyperref'])].sort();
const packageFiles: object[] = [];
for (const name of [...packageNames.map(name => `${name}.sty`), 'ngerman.ldf']) {
  const probe = spawnSync('kpsewhich', [name], { encoding: 'utf8', timeout: 30000, maxBuffer: 65536 });
  if (probe.error || probe.signal || probe.status !== 0 || !probe.stdout.trim()) {
    packageFiles.push({ name, status: 'unavailable' });
    failures.push(`Required package unavailable: ${name}`);
    continue;
  }
  const bytes = await readFile(probe.stdout.trim());
  const declaration = bytes.toString('utf8').match(/\\Provides(?:Package|File|Language)\{[^}]+\}\s*\[([^\]]{1,512})\]/s);
  packageFiles.push({ name, status: 'observed', sha256: sha256(bytes),
    declared_version: declaration ? declaration[1].replace(/\s+/g, ' ').trim() : null });
}
const cases: object[] = [];
const verifiedBaselines = new Map<string, Buffer>();
for (const fixture of manifest.cases) for (const source of fixture.sources) {
  const bytes = await readFile(resolve(root, source.path));
  assert.equal(sha256(bytes), source.sha256, `Manifest mismatch: ${source.path}`);
  verifiedBaselines.set(source.path, bytes);
}

async function bindProjectArchive(file: string, expectedFiles: SourceFile[], archiveHash: string) {
  assert.equal(sha256(await readFile(file)), archiveHash, 'Project archive bytes differ from queue');
  const listing = spawnSync('unzip', ['-Z1', file], { encoding: 'utf8', timeout: 30000, maxBuffer: 65536 });
  assert(!listing.error && listing.signal === null && listing.status === 0, 'Cannot inspect exact project archive');
  const paths = listing.stdout.trim().split(/\r?\n/);
  assert.deepEqual(paths.sort(), expectedFiles.map(source => source.path.slice('P01/'.length)).sort(), 'Archive members differ from source context');
  for (const source of expectedFiles) {
    const member = spawnSync('unzip', ['-p', file, source.path.slice('P01/'.length)], { timeout: 30000, maxBuffer: 2 * 1024 * 1024 });
    assert(!member.error && member.signal === null && member.status === 0, 'Cannot read project member');
    assert.equal(sha256(member.stdout), source.sha256, `Archive member changed: ${source.path}`);
  }
}

async function compileContext(fixture: CorpusCase, id: string, kind: 'source' | 'saved',
  files: Map<string, Buffer>, receipt: any) {
  const directory = resolve(runRoot, id, kind);
  await mkdir(directory, { recursive: true });
  const fileHashes: SourceFile[] = [];
  for (const [path, bytes] of files) {
    const target = resolve(directory, path);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, bytes);
    assert.equal(sha256(await readFile(target)), sha256(bytes));
    fileHashes.push({ path, sha256: sha256(bytes) });
  }
  const workingDirectory = dirname(resolve(directory, fixture.entrypoint));
  const build = resolve(directory, 'artifacts');
  await mkdir(build, { recursive: true });
  const pdfPath = resolve(build, 'compiled.pdf');
  const passes: (Execution & { pass_number: number; stdout_sha256: string; tex_log_sha256: string | null })[] = [];
  let finalLog = '';
  for (let pass = 1; pass <= (fixture.compile_expected === 'passed' ? 2 : 1); pass++) {
    await unlink(pdfPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
    const result = spawnSync(compiler, ['-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error',
      '-recorder', '-jobname=compiled', '-output-directory', build, basename(fixture.entrypoint)],
    { cwd: workingDirectory, encoding: 'utf8', timeout: 60000, maxBuffer: 4 * 1024 * 1024 });
    const stdout = (result.stdout || '') + (result.stderr || '');
    await writeFile(resolve(build, `pass-${pass}-output.txt`), stdout);
    const texLog = await readFile(resolve(build, 'compiled.log')).catch(error => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    if (texLog !== null) await writeFile(resolve(build, `pass-${pass}.log`), texLog);
    finalLog = stdout + '\n' + (texLog?.toString('utf8') || '');
    const pdf = await readFile(pdfPath).catch(error => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    const validPdf = pdf !== null && pdf.subarray(0, 5).toString('ascii') === '%PDF-';
    if (validPdf) await writeFile(resolve(build, `pass-${pass}.pdf`), pdf!);
    passes.push({ pass_number: pass, exit_code: result.status, signal: result.signal,
      error_code: result.error ? String((result.error as NodeJS.ErrnoException).code || 'execution_error') : null,
      stdout_sha256: sha256(stdout), tex_log_sha256: texLog === null ? null : sha256(texLog),
      pdf_sha256: validPdf ? sha256(pdf!) : null });
    if (result.error || result.signal || result.status !== 0) break;
  }
  const outcome = compilationOutcome(fixture, passes, finalLog);
  cases.push({ fixture: id, kind, entrypoint: fixture.entrypoint, files: fileHashes,
    receipt_source_sha256: receipt.source_sha256,
    receipt_output_sha256: kind === 'saved' ? receipt.output_sha256 : null,
    receipt_archive_sha256: fixture.id === 'P01' ? receipt.archive_sha256 : null,
    compile_expected: fixture.compile_expected, passes,
    pdf_sha256: passes.at(-1)?.pdf_sha256 ?? null,
    evidence_directory: relative(output, build), ...outcome });
  if (!outcome.expectation_met) failures.push(`${id} ${kind}: compiler expectation not met`);
}

for (const fixture of manifest.cases) for (const reviewed of [false, true]) {
  const id = reviewed ? `${fixture.id}-reviewed` : fixture.id;
  try {
    const sources = new Map(fixture.sources.map(source => [source.path, verifiedBaselines.get(source.path)!]));
    if (reviewed) sources.set(fixture.entrypoint, Buffer.from(withReviewedLanguage(
      sources.get(fixture.entrypoint)!.toString('utf8'), fixture.id === 'P04' ? 'de' : 'en')));
    const hashes = [...sources].map(([path, bytes]) => ({ path, sha256: sha256(bytes) }));
    const receipt = assertQueueReceipt(queue.evidence, id, hashes, fixture.entrypoint);
    if (fixture.id === 'P01') {
      await bindProjectArchive(resolve(output, `input-${reviewed ? 'reviewed-' : ''}P01.zip`), hashes, receipt.archive_sha256);
    } else {
      const input = await readFile(resolve(output, `input-${reviewed ? 'reviewed-' : ''}${fixture.id}.tex`));
      assert.equal(sha256(input), receipt.source_sha256, `Queued input bytes changed: ${id}`);
      sources.set(fixture.entrypoint, input);
    }
    assertSourceFiles([...sources].map(([path, bytes]) => ({ path, sha256: sha256(bytes) })), hashes);
    await compileContext(fixture, id, 'source', sources, receipt);
    if (fixture.id === 'P01') {
      cases.push({ fixture: id, kind: 'saved', status: 'not_run', reason: 'original_archive_only', archive_sha256: receipt.archive_sha256 });
    } else if (receipt.status === 'no_op') {
      cases.push({ fixture: id, kind: 'saved', status: 'not_run', reason: 'no_repairs_needed' });
    } else if (receipt.status === 'refused') {
      cases.push({ fixture: id, kind: 'saved', status: 'not_run', reason: 'output_withheld' });
    } else {
      const saved = await readFile(resolve(output, `saved-${reviewed ? 'reviewed-' : ''}${fixture.id}.tex`));
      assert.equal(sha256(saved), receipt.output_sha256, `Downloaded TEX bytes changed: ${id}`);
      const candidateSources = new Map(sources);
      candidateSources.set(fixture.entrypoint, saved);
      await compileContext(fixture, id, 'saved', candidateSources, receipt);
    }
  } catch (error) {
    failures.push(`${id}: ${error instanceof Error ? error.message : 'Compilation context failed'}`);
    cases.push({ fixture: id, kind: 'context', status: 'failed', expectation_met: false });
  }
}
const sourceCount = cases.filter((row: any) => row.kind === 'source').length;
if (sourceCount !== 52) failures.push(`Expected 52 source contexts, observed ${sourceCount}`);
const deliveredCount = queue.evidence.filter((row: any) => row.kind === 'latex' && row.status === 'delivered').length;
const savedCount = cases.filter((row: any) => row.kind === 'saved' && Array.isArray(row.passes)).length;
if (savedCount !== deliveredCount) failures.push(`Expected ${deliveredCount} exact TEX downloads, compiled ${savedCount}`);
await writeFile(resolve(output, 'compilation.json'), JSON.stringify({ schema_version: 2,
  corpus_id: manifest.corpus_id, manifest_sha256: sha256(manifestBytes), revision,
  tracked_diff_sha256: sha256(execFileSync('git', ['diff', 'HEAD', '--binary'])),
  harness_sha256: harnessHashes, queue_report_sha256: sha256(queueBytes),
  runtime: { bun: (globalThis as { Bun?: { version: string } }).Bun?.version ?? null, node: process.version },
  compiler: { name: compiler, version: compilerVersion, status: versionAvailable ? 'available' : 'unavailable' },
  package_files: packageFiles,
  counts: { original_contexts: 26, reviewed_contexts: 26, compiled_source_contexts: sourceCount,
    delivered_tex: deliveredCount, compiled_downloads: savedCount },
  scope: 'Compilation only; no PDF/UA, mathematical fidelity, semantic, human-review or assistive-technology claim',
  cases, failures }, null, 2));
assert.equal(failures.length, 0, failures.join('\n'));
console.log(`PASS LaTeX compilation: 26 original contexts, 26 reviewed variants, ${savedCount} exact queue downloads`);
