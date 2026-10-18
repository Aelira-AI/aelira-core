import { test, expect } from 'bun:test';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile, readdir, stat, open, rm, cp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const repo = resolve(import.meta.dir, '..');
const cli = join(repo, 'scripts/candidate_image_bundle.ts');
const sha = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const digest = (bytes: Buffer | string) => `sha256:${sha(bytes)}`;
const MAX_METADATA_BYTES = 16 * 1024 * 1024;

function command(executable: string, args: string[], cwd = repo) {
  return spawnSync(executable, args, { cwd, encoding: 'utf8', maxBuffer: 1024 * 1024 });
}
function cliRun(args: string[], env: Record<string, string> = {}) {
  return spawnSync(process.execPath, [cli, ...args], { cwd: repo, encoding: 'utf8', maxBuffer: 1024 * 1024,
    env: { ...process.env, GITHUB_REPOSITORY: 'Aelira-AI/aelira-core', ...env } });
}
function success(result: ReturnType<typeof command>) {
  if (result.error || result.status !== 0) throw new Error(`Command failed: ${result.error ?? result.stderr}`);
}
function refused(result: ReturnType<typeof command>) {
  expect(result.status).not.toBe(0);
}
async function json(path: string, value: unknown) { await writeFile(path, JSON.stringify(value) + '\n'); }
async function mutateJson(path: string, edit: (value: any) => void) {
  const value = JSON.parse(await readFile(path, 'utf8'));
  edit(value);
  await json(path, value);
}
async function makeArchive(root: string, name: string, options: { missingLayer?: boolean; wrongConfig?: boolean; wrongPlatform?: boolean; oversizedIndex?: boolean; directoryEntries?: 'both' | 'sha256-only' | 'none'; unrelatedBlob?: boolean } = {}) {
  const source = join(root, `${name}-source`);
  const blobs = join(source, 'blobs', 'sha256');
  await mkdir(blobs, { recursive: true });
  const layerSource = join(root, `${name}-layer-source`);
  await mkdir(layerSource);
  await writeFile(join(layerSource, 'hello.txt'), `independent ${name} layer fixture\n`);
  const layerPath = join(root, `${name}-layer.tar`);
  success(command('tar', ['--format=ustar', '-cf', layerPath, '-C', layerSource, 'hello.txt']));
  const layer = await readFile(layerPath);
  const config = Buffer.from(JSON.stringify({ created: '1970-01-01T00:00:00Z',
    architecture: options.wrongPlatform ? 'amd64' : 'arm64', os: 'linux',
    config: {}, rootfs: { type: 'layers', diff_ids: [digest(layer)] },
    history: [{ created: '1970-01-01T00:00:00Z', created_by: 'COPY hello.txt /hello.txt' }] }));
  const manifest = Buffer.from(JSON.stringify({ schemaVersion: 2,
    config: { mediaType: 'application/vnd.oci.image.config.v1+json', digest: options.wrongConfig ? digest('different config') : digest(config), size: config.length },
    layers: [{ mediaType: 'application/vnd.oci.image.layer.v1.tar', digest: digest(layer), size: layer.length }] }));
  const index = Buffer.from(JSON.stringify({ schemaVersion: 2, mediaType: 'application/vnd.oci.image.index.v1+json',
    manifests: [{ mediaType: 'application/vnd.oci.image.manifest.v1+json', digest: digest(manifest), size: manifest.length,
      platform: { architecture: 'arm64', os: 'linux' } }] }));
  await writeFile(join(source, 'oci-layout'), JSON.stringify({ imageLayoutVersion: '1.0.0' }));
  await writeFile(join(source, 'index.json'), options.oversizedIndex ? Buffer.alloc(MAX_METADATA_BYTES + 1, 0x20) : index);
  await writeFile(join(blobs, sha(config)), config);
  await writeFile(join(blobs, sha(manifest)), manifest);
  if (!options.missingLayer) await writeFile(join(blobs, sha(layer)), layer);
  if (options.unrelatedBlob) await writeFile(join(blobs, sha('unrelated')), 'unrelated');
  const archive = join(root, `${name}.oci.tar`);
  const blobEntries = options.directoryEntries === 'none'
    ? (await readdir(blobs)).map((entry) => `blobs/sha256/${entry}`)
    : [options.directoryEntries === 'sha256-only' ? 'blobs/sha256' : 'blobs'];
  success(command('tar', ['--format=ustar', '-cf', archive, '-C', source, ...blobEntries, 'index.json', 'oci-layout']));
  return { archive, layer };
}
type Fixture = Awaited<ReturnType<typeof makeFixture>>;
async function makeFixture() {
  const dir = await mkdtemp(join(tmpdir(), 'aelira-candidate-bundle-test-'));
  const commit = command('git', ['rev-parse', 'HEAD']).stdout.trim();
  const tree = command('git', ['rev-parse', 'HEAD^{tree}']).stdout.trim();
  const archives = { api: await makeArchive(dir, 'api'), dashboard: await makeArchive(dir, 'dashboard') };
  for (const kind of ['api', 'dashboard'] as const) {
    const evidence = join(dir, `${kind}.evidence.txt`);
    await writeFile(evidence, `${kind} runtime and descriptor probe passed\n`);
    success(cliRun(['create', '--archive', archives[kind].archive, '--evidence', evidence,
      '--output', join(dir, `${kind}.receipt.json`), '--kind', kind, '--platform', 'linux/arm64',
      '--checkout-commit', commit, '--checkout-tree', tree, '--run-head', commit,
      '--run-id', '900001', '--run-attempt', '1']));
    success(command('zip', ['-q', '-0', '-j', join(dir, `${kind}.zip`),
      archives[kind].archive, join(dir, `${kind}.receipt.json`), evidence]));
  }
  await json(join(dir, 'run.json'), { id: 900001, run_attempt: 1, head_sha: commit,
    event: 'workflow_dispatch', status: 'completed', conclusion: 'success', name: 'CI',
    repository: { full_name: 'Aelira-AI/aelira-core' }, path: '.github/workflows/ci.yml@refs/heads/main' });
  for (const [kind, id] of [['api', 900002], ['dashboard', 900003]] as const) {
    const zip = join(dir, `${kind}.zip`);
    await json(join(dir, `${kind}-artifact.json`), { id, workflow_run: { id: 900001, head_sha: commit },
      expired: false, name: `candidate-${kind}-arm64-${commit}-1`,
      size_in_bytes: (await stat(zip)).size, digest: digest(await readFile(zip)) });
  }
  return { dir, commit, tree, archives };
}
function fullArgs(f: Fixture) {
  const p = (name: string) => join(f.dir, name);
  return ['verify', '--zip', p('api.zip'), '--peer-zip', p('dashboard.zip'),
    '--archive', p('api.oci.tar'), '--receipt', p('api.receipt.json'), '--evidence', p('api.evidence.txt'),
    '--peer-archive', p('dashboard.oci.tar'), '--peer-receipt', p('dashboard.receipt.json'), '--peer-evidence', p('dashboard.evidence.txt'),
    '--run-json', p('run.json'), '--artifact-json', p('api-artifact.json'), '--artifact-id', '900002',
    '--peer-artifact-json', p('dashboard-artifact.json'), '--peer-artifact-id', '900003',
    '--checkout-commit', f.commit, '--checkout-tree', f.tree, '--platform', 'linux/arm64', '--kind', 'api'];
}
async function withFixture(check: (fixture: Fixture) => Promise<void>) {
  const fixture = await makeFixture();
  try { await check(fixture); } finally { await rm(fixture.dir, { recursive: true }); }
}

test('accepts a complete native API/dashboard pair after independent ZIP verification', async () => {
  await withFixture(async (f) => {
    for (const [kind, id] of [['api', '900002'], ['dashboard', '900003']] as const) {
      success(cliRun(['verify-zip', '--zip', join(f.dir, `${kind}.zip`), '--artifact-json', join(f.dir, `${kind}-artifact.json`), '--artifact-id', id, '--kind', kind]));
    }
    success(cliRun(fullArgs(f)));
  });
});

test('refuses corrupt and missing OCI blobs and unsafe tar entries', async () => {
  await withFixture(async (f) => {
    const corrupt = join(f.dir, 'corrupt.oci.tar');
    await cp(f.archives.api.archive, corrupt);
    const bytes = await readFile(corrupt);
    const position = bytes.indexOf(f.archives.api.layer);
    expect(position).toBeGreaterThan(0);
    const handle = await open(corrupt, 'r+');
    try { await handle.write(Buffer.from('X'), 0, 1, position); } finally { await handle.close(); }
    refused(cliRun(['inspect', '--archive', corrupt, '--platform', 'linux/arm64']));
    const missing = await makeArchive(f.dir, 'missing', { missingLayer: true });
    refused(cliRun(['inspect', '--archive', missing.archive, '--platform', 'linux/arm64']));
    const source = join(f.dir, 'unsafe-source');
    await mkdir(source);
    success(command('ln', ['-s', 'index.json', join(source, 'link')]));
    const unsafeType = join(f.dir, 'unsafe-type.oci.tar');
    success(command('tar', ['--format=ustar', '-cf', unsafeType, '-C', source, 'link']));
    refused(cliRun(['inspect', '--archive', unsafeType, '--platform', 'linux/arm64']));
    const unsafePath = join(f.dir, 'unsafe-path.oci.tar');
    await cp(f.archives.api.archive, unsafePath);
    const tar = await readFile(unsafePath);
    const headerAt = tar.indexOf(Buffer.from('index.json\0'));
    expect(headerAt % 512).toBe(0);
    tar.fill(0, headerAt, headerAt + 100);
    tar.write('../escape', headerAt, 'ascii');
    tar.fill(0x20, headerAt + 148, headerAt + 156);
    const sum = tar.subarray(headerAt, headerAt + 512).reduce((n, byte) => n + byte, 0);
    tar.write(sum.toString(8).padStart(6, '0') + '\0 ', headerAt + 148, 'ascii');
    await writeFile(unsafePath, tar);
    refused(cliRun(['inspect', '--archive', unsafePath, '--platform', 'linux/arm64']));
  });
});

test('accepts omitted optional tar directories but refuses every unreferenced blob', async () => {
  await withFixture(async (f) => {
    const withoutDirectories = await makeArchive(f.dir, 'without-directories', { directoryEntries: 'none' });
    const names = command('tar', ['-tf', withoutDirectories.archive]).stdout.split('\n');
    expect(names).not.toContain('blobs/');
    expect(names).not.toContain('blobs/sha256/');
    success(cliRun(['inspect', '--archive', withoutDirectories.archive, '--platform', 'linux/arm64']));
    const unrelated = await makeArchive(f.dir, 'unrelated-blob', { directoryEntries: 'sha256-only', unrelatedBlob: true });
    const otherNames = command('tar', ['-tf', unrelated.archive]).stdout.split('\n');
    expect(otherNames).not.toContain('blobs/');
    expect(otherNames).toContain('blobs/sha256/');
    refused(cliRun(['inspect', '--archive', unrelated.archive, '--platform', 'linux/arm64']));
  });
});

test('refuses bad config and platform identities and oversized metadata', async () => {
  await withFixture(async (f) => {
    const badConfig = await makeArchive(f.dir, 'bad-config', { wrongConfig: true });
    refused(cliRun(['inspect', '--archive', badConfig.archive, '--platform', 'linux/arm64']));
    const badPlatform = await makeArchive(f.dir, 'bad-platform', { wrongPlatform: true });
    refused(cliRun(['inspect', '--archive', badPlatform.archive, '--platform', 'linux/arm64']));
    refused(cliRun(['inspect', '--archive', f.archives.api.archive, '--platform', 'linux/amd64']));
    const oversized = await makeArchive(f.dir, 'oversized', { oversizedIndex: true });
    refused(cliRun(['inspect', '--archive', oversized.archive, '--platform', 'linux/arm64']));
  });
});

test('refuses wrong source, tree, failed or incomplete runs, and mixed attempts', async () => {
  await withFixture(async (f) => {
    const args = fullArgs(f);
    const treeAt = args.indexOf('--checkout-tree') + 1;
    const sourceAt = args.indexOf('--checkout-commit') + 1;
    const wrongTree = [...args]; wrongTree[treeAt] = 'a'.repeat(40);
    refused(cliRun(wrongTree));
    const wrongSource = [...args]; wrongSource[sourceAt] = 'b'.repeat(40);
    refused(cliRun(wrongSource));
    refused(cliRun(['create', '--archive', f.archives.api.archive, '--evidence', join(f.dir, 'api.evidence.txt'),
      '--output', join(f.dir, 'wrong-repository.receipt.json'), '--kind', 'api', '--platform', 'linux/arm64',
      '--checkout-commit', f.commit, '--checkout-tree', f.tree, '--run-head', f.commit,
      '--run-id', '900001', '--run-attempt', '1'], { GITHUB_REPOSITORY: 'other/fork' }));
    await mutateJson(join(f.dir, 'run.json'), (run) => { run.conclusion = 'failure'; });
    refused(cliRun(args));
    await mutateJson(join(f.dir, 'run.json'), (run) => { run.conclusion = null; run.status = 'in_progress'; });
    refused(cliRun(args));
    await mutateJson(join(f.dir, 'run.json'), (run) => { run.conclusion = 'success'; run.status = 'completed'; });
    await mutateJson(join(f.dir, 'dashboard.receipt.json'), (receipt) => { receipt.run_attempt = 2; });
    refused(cliRun(args));
  });
});

test('binds immutable artifact metadata, ZIP bytes, and copied bundle members', async () => {
  await withFixture(async (f) => {
    const args = fullArgs(f);
    const metadataPath = join(f.dir, 'api-artifact.json');
    const originalMetadata = await readFile(metadataPath);
    const originalZip = await readFile(join(f.dir, 'api.zip'));
    await mutateJson(join(f.dir, 'api-artifact.json'), (artifact) => { delete artifact.digest; });
    refused(cliRun(['verify-zip', '--zip', join(f.dir, 'api.zip'), '--artifact-json', metadataPath, '--artifact-id', '900002', '--kind', 'api']));
    await writeFile(metadataPath, originalMetadata);
    refused(cliRun(['verify-zip', '--zip', join(f.dir, 'api.zip'), '--artifact-json', metadataPath, '--artifact-id', '999999', '--kind', 'api']));
    await mutateJson(metadataPath, (artifact) => { artifact.digest = `sha256:${'a'.repeat(64)}`; });
    refused(cliRun(['verify-zip', '--zip', join(f.dir, 'api.zip'), '--artifact-json', metadataPath, '--artifact-id', '900002', '--kind', 'api']));
    await writeFile(metadataPath, originalMetadata);
    const changedZip = Buffer.from(originalZip);
    changedZip[20] ^= 1;
    await writeFile(join(f.dir, 'api.zip'), changedZip);
    refused(cliRun(['verify-zip', '--zip', join(f.dir, 'api.zip'), '--artifact-json', metadataPath, '--artifact-id', '900002', '--kind', 'api']));
    await writeFile(join(f.dir, 'api.zip'), originalZip);
    const swapped = [...args];
    swapped[swapped.indexOf('--zip') + 1] = join(f.dir, 'dashboard.zip');
    refused(cliRun(swapped));
    const alternate = await makeArchive(f.dir, 'api-alternate');
    await cp(alternate.archive, join(f.dir, 'api.oci.tar'));
    const result = cliRun(['inspect', '--archive', join(f.dir, 'api.oci.tar'), '--platform', 'linux/arm64']);
    success(result);
    const actual = JSON.parse(result.stdout);
    await mutateJson(join(f.dir, 'api.receipt.json'), (receipt) => {
      for (const key of ['archive_sha256', 'archive_bytes', 'platform_manifest_digest', 'config_digest', 'platform']) receipt[key] = actual[key];
    });
    refused(cliRun(args));
  });
});
