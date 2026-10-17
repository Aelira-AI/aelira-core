#!/usr/bin/env bun
/** Validate and bind a native CI OCI archive before it is loaded. No extraction. */
import { createHash } from 'node:crypto';
import { open, readFile, writeFile, lstat } from 'node:fs/promises';
import { createReadStream, constants } from 'node:fs';
import { basename } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';

const REPO = 'Aelira-AI/aelira-core';
const digestPattern = /^sha256:[0-9a-f]{64}$/;
const hexPattern = /^[0-9a-f]{40}$/;
const MAX_METADATA_BYTES = 16 * 1024 * 1024;
const args = new Map<string, string>();
for (let i = 3; i < process.argv.length; i += 2) {
  if (!process.argv[i]?.startsWith('--') || !process.argv[i + 1]) throw new Error('Expected --key value arguments');
  args.set(process.argv[i].slice(2), process.argv[i + 1]);
}
const command = process.argv[2];
function required(key: string): string {
  const value = args.get(key);
  if (!value) throw new Error(`Missing --${key}`);
  return value;
}
function insist(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
function parseJson(bytes: Buffer, label: string): any {
  insist(bytes.length <= MAX_METADATA_BYTES, `${label} is too large`);
  return JSON.parse(bytes.toString('utf8'));
}
type Entry = { size: number; hash: string; offset: number; bytes?: Buffer };

// Read headers at fixed offsets and hash file contents in bounded chunks. OCI
// layouts need only regular files. Refusing extensions and links also prevents
// path traversal or extraction surprises from untrusted downloads.
async function readArchive(path: string) {
  const input = await lstat(path);
  insist(input.isFile(), 'OCI archive must be a regular file');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const archiveSize = (await file.stat()).size;
    insist(Number.isSafeInteger(archiveSize) && archiveSize > 0, 'Invalid OCI archive size');
    const entries = new Map<string, Entry>();
    const whole = createHash('sha256');
    let offset = 0;
    let zeroBlocks = 0;
    const chunk = Buffer.alloc(1024 * 1024);
    async function readAt(position: number, length: number): Promise<Buffer> {
      const out = Buffer.alloc(length);
      let done = 0;
      while (done < length) {
        const r = await file.read(out, done, length - done, position + done);
        insist(r.bytesRead > 0, 'Truncated OCI archive');
        done += r.bytesRead;
      }
      return out;
    }
    while (offset < archiveSize) {
      insist(offset + 512 <= archiveSize, 'Truncated tar header');
      const header = await readAt(offset, 512);
      whole.update(header);
      offset += 512;
      if (header.every((byte) => byte === 0)) { zeroBlocks++; continue; }
      insist(zeroBlocks === 0, 'Nonzero data after tar terminator');
      const expectedChecksum = Number.parseInt(header.subarray(148, 156).toString('ascii').replace(/\0.*$/, '').trim(), 8);
      const checksumHeader = Buffer.from(header);
      checksumHeader.fill(0x20, 148, 156);
      insist(Number.isSafeInteger(expectedChecksum) && checksumHeader.reduce((sum, byte) => sum + byte, 0) === expectedChecksum, 'Invalid tar header checksum');
      const field = (start: number, end: number) => header.subarray(start, end).toString('utf8').split('\0')[0];
      insist(field(345, 500) === '', 'Tar prefixes are forbidden');
      const name = field(0, 100);
      const directory = name === 'blobs/' || name === 'blobs/sha256/';
      insist(directory || name === 'index.json' || name === 'oci-layout' || /^blobs\/sha256\/[0-9a-f]{64}$/.test(name), `Unsafe or unexpected tar path: ${name}`);
      insist(directory ? header[156] === 53 : header[156] === 0 || header[156] === 48, `Unexpected tar entry type: ${name}`);
      insist(!entries.has(name), `Duplicate tar entry: ${name}`);
      const sizeText = field(124, 136).trim();
      insist(/^[0-7]+$/.test(sizeText), `Invalid tar size: ${name}`);
      const size = Number.parseInt(sizeText, 8);
      insist(Number.isSafeInteger(size) && size >= 0 && offset + Math.ceil(size / 512) * 512 <= archiveSize, `Invalid tar bounds: ${name}`);
      insist(!directory || size === 0, 'Directory has nonzero size');
      const hash = createHash('sha256');
      const capture = name === 'index.json' || name === 'oci-layout';
      insist(!capture || size <= MAX_METADATA_BYTES, `${name} metadata is too large`);
      const parts: Buffer[] = [];
      let read = 0;
      while (read < size) {
        const length = Math.min(chunk.length, size - read);
        const bytes = await readAt(offset + read, length);
        hash.update(bytes);
        whole.update(bytes);
        if (capture) parts.push(bytes);
        read += length;
      }
      const pad = Math.ceil(size / 512) * 512 - size;
      if (pad) {
        const padding = await readAt(offset + size, pad);
        insist(padding.every((byte) => byte === 0), `Nonzero tar padding: ${name}`);
        whole.update(padding);
      }
      entries.set(name, { size, hash: hash.digest('hex'), offset, ...(capture ? { bytes: Buffer.concat(parts) } : {}) });
      offset += size + pad;
    }
    insist(zeroBlocks >= 2, 'Missing tar terminator');
    insist(entries.has('oci-layout') && entries.has('index.json'), 'Missing OCI layout or index');
    const layout = parseJson(entries.get('oci-layout')!.bytes!, 'oci-layout');
    insist(layout.imageLayoutVersion === '1.0.0', 'Unsupported OCI layout');
    const index = parseJson(entries.get('index.json')!.bytes!, 'index.json');
    insist(index.schemaVersion === 2 && Array.isArray(index.manifests) && index.manifests.length === 1, 'Expected one OCI index manifest');
    const referenced = new Set(['oci-layout', 'index.json']);
    for (const directory of ['blobs/', 'blobs/sha256/']) {
      if (entries.has(directory)) referenced.add(directory);
    }
    async function blob(descriptor: any, label: string): Promise<Buffer> {
      insist(digestPattern.test(descriptor?.digest) && Number.isSafeInteger(descriptor?.size), `Invalid ${label} descriptor`);
      const name = `blobs/sha256/${descriptor.digest.slice(7)}`;
      const entry = entries.get(name);
      insist(entry && entry.size === descriptor.size && entry.hash === descriptor.digest.slice(7), `Missing or corrupt ${label} blob`);
      referenced.add(name);
      insist(entry.size <= MAX_METADATA_BYTES, `${label} metadata is too large`);
      return readAt(entry.offset, entry.size);
    }
    const manifestDescriptor = index.manifests[0];
    insist(manifestDescriptor.mediaType === 'application/vnd.oci.image.manifest.v1+json', 'Expected OCI image manifest');
    const manifest = parseJson(await blob(manifestDescriptor, 'manifest'), 'manifest');
    insist(manifest.schemaVersion === 2 && Array.isArray(manifest.layers) && manifest.layers.length > 0, 'Invalid image manifest');
    const config = parseJson(await blob(manifest.config, 'config'), 'config');
    for (const layer of manifest.layers) {
      insist(layer.mediaType === 'application/vnd.oci.image.layer.v1.tar+gzip' || layer.mediaType === 'application/vnd.oci.image.layer.v1.tar', 'Unexpected OCI layer type');
      insist(digestPattern.test(layer.digest) && Number.isSafeInteger(layer.size), 'Invalid layer descriptor');
      const name = `blobs/sha256/${layer.digest.slice(7)}`;
      const entry = entries.get(name);
      insist(entry && entry.size === layer.size && entry.hash === layer.digest.slice(7), `Missing or corrupt layer ${layer.digest}`);
      referenced.add(name);
    }
    for (const name of entries.keys()) insist(referenced.has(name), `Unreferenced OCI archive entry: ${name}`);
    return { archive_sha256: whole.digest('hex'), archive_bytes: archiveSize,
      platform_manifest_digest: manifestDescriptor.digest, config_digest: manifest.config.digest,
      platform: `linux/${config.architecture}`, os: config.os,
      descriptor_platform: manifestDescriptor.platform ? `${manifestDescriptor.platform.os}/${manifestDescriptor.platform.architecture}` : null };
  } finally { await file.close(); }
}
async function hashFile(path: string) {
  const file = await lstat(path);
  insist(file.isFile() && file.size > 0, 'Input must be a nonempty regular file');
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return { name: basename(path), bytes: file.size, sha256: hash.digest('hex') };
}
async function readJsonFile(path: string): Promise<any> {
  const file = await lstat(path);
  insist(file.isFile() && file.size > 0 && file.size <= 1024 * 1024, 'JSON metadata exceeds one MiB');
  return JSON.parse(await readFile(path, 'utf8'));
}
function expectedZipMembers(kind: string): string[] {
  insist(kind === 'api' || kind === 'dashboard', 'Invalid bundle kind');
  return [`${kind}.evidence.txt`, `${kind}.oci.tar`, `${kind}.receipt.json`];
}
async function verifyZipDigest(zipPath: string, artifactJsonPath: string, artifactId: string, kind: string) {
  const artifact = await readJsonFile(artifactJsonPath);
  insist(Number.isSafeInteger(artifact.id) && artifact.id === Number(artifactId), 'Wrong immutable artifact ID');
  insist(digestPattern.test(artifact.digest), 'Missing or invalid GitHub artifact digest');
  insist(artifact.expired === false && Number.isSafeInteger(artifact.size_in_bytes) && artifact.size_in_bytes > 0, 'Unavailable artifact');
  const zip = await hashFile(zipPath);
  insist(`sha256:${zip.sha256}` === artifact.digest, 'Downloaded ZIP differs from GitHub artifact digest');
  const list = spawnSync('unzip', ['-Z1', zipPath], { encoding: 'utf8', maxBuffer: 64 * 1024 });
  insist(list.status === 0, 'Cannot inspect artifact ZIP members');
  const names = list.stdout.trimEnd().split('\n').sort();
  insist(JSON.stringify(names) === JSON.stringify(expectedZipMembers(kind).sort()), 'Unexpected ZIP members');
  return artifact;
}
async function hashZipMember(zipPath: string, name: string) {
  const child = spawn('unzip', ['-p', zipPath, name], { stdio: ['ignore', 'pipe', 'pipe'] });
  const hash = createHash('sha256');
  let bytes = 0;
  let stderr = '';
  child.stdout.on('data', (chunk: Buffer) => { hash.update(chunk); bytes += chunk.length; });
  child.stderr.on('data', (chunk: Buffer) => { stderr = (stderr + chunk.toString()).slice(-4096); });
  const code = await new Promise<number>((resolve, reject) => {
    child.on('error', reject);
    child.on('close', (status) => resolve(status ?? -1));
  });
  insist(code === 0 && bytes > 0, `Cannot read ZIP member ${name}: ${stderr}`);
  return { bytes, sha256: hash.digest('hex') };
}
async function verifyZipContents(zipPath: string, kind: string, archive: string, receipt: string, evidence: string) {
  for (const [suffix, path] of [['oci.tar', archive], ['receipt.json', receipt], ['evidence.txt', evidence]]) {
    const name = `${kind}.${suffix}`;
    insist(basename(path) === name, 'Extracted bundle filename differs from ZIP member');
    const [actual, zipped] = await Promise.all([hashFile(path), hashZipMember(zipPath, name)]);
    insist(actual.bytes === zipped.bytes && actual.sha256 === zipped.sha256, `Extracted ${name} differs from its ZIP member`);
  }
}

function sourceIdentity(receipt: any) {
  insist(receipt.schema === 'aelira-candidate-image-v1' && receipt.repository === REPO, 'Wrong receipt schema or repository');
  insist(hexPattern.test(receipt.checkout_commit) && hexPattern.test(receipt.checkout_tree), 'Invalid checkout identity');
  insist(Number.isSafeInteger(receipt.run_id) && receipt.run_id > 0 && Number.isSafeInteger(receipt.run_attempt) && receipt.run_attempt > 0, 'Invalid workflow identity');
  insist(['api', 'dashboard'].includes(receipt.kind) && ['linux/amd64', 'linux/arm64'].includes(receipt.platform), 'Invalid image kind or platform');
}
function checksComplete(receipt: any) {
  const expected = ['reproducible_no_cache_builds', 'native_runtime_and_package_gates',
    'retained_image_import_and_descriptor', 'dashboard_http_and_descriptor'];
  insist(Object.keys(receipt.checks ?? {}).length === expected.length &&
    expected.every((key) => receipt.checks[key] === 'passed'), 'Incomplete native checks');
}
function dockerJson(argv: string[]): any {
  const result = spawnSync('docker', argv, { encoding: 'utf8', maxBuffer: 1024 * 1024 });
  insist(result.status === 0, `Docker descriptor inspection failed: ${result.stderr}`);
  return JSON.parse(result.stdout);
}
function gitRevision(revision: string): string {
  const result = spawnSync('git', ['rev-parse', '--verify', revision], { encoding: 'utf8', maxBuffer: 1024 });
  insist(result.status === 0, `Git cannot resolve ${revision}`);
  return result.stdout.trim();
}
async function verifyReceipt(receipt: any, archivePath: string, evidencePath?: string) {
  sourceIdentity(receipt);
  checksComplete(receipt);
  insist(receipt.archive_name === basename(archivePath), 'Archive filename mismatch');
  const actual = await readArchive(archivePath);
  for (const key of ['archive_sha256', 'archive_bytes', 'platform_manifest_digest', 'config_digest', 'platform']) {
    insist(receipt[key] === actual[key], `${key} mismatch`);
  }
  insist(actual.os === 'linux' && actual.descriptor_platform === receipt.platform, 'OCI platform descriptors disagree');
  if (evidencePath) {
    const evidence = await hashFile(evidencePath);
    insist(JSON.stringify(evidence) === JSON.stringify(receipt.evidence), 'Missing or corrupt runtime evidence');
  }
  return actual;
}

if (command === 'inspect') {
  const result = await readArchive(required('archive'));
  insist(result.os === 'linux' && result.platform === required('platform') && result.descriptor_platform === required('platform'), 'Wrong OCI platform');
  console.log(JSON.stringify(result));
} else if (command === 'check-docker') {
  const actual = await readArchive(required('archive'));
  const platform = required('platform');
  insist(actual.platform === platform && actual.descriptor_platform === platform, 'Wrong OCI platform');
  const image = dockerJson(['image', 'inspect', '--platform', platform, required('image'), '--format',
    '{"Descriptor":{{json .Descriptor}},"Id":{{json .Id}},"Architecture":{{json .Architecture}},"Os":{{json .Os}}}']);
  insist(image.Descriptor?.digest === actual.platform_manifest_digest, 'Imported image platform manifest differs from OCI');
  insist(image.Id === actual.platform_manifest_digest, 'Imported image ID differs from OCI manifest');
  insist(`linux/${image.Architecture}` === platform && image.Os === 'linux', 'Imported image has wrong platform');
  if (args.has('container')) {
    const container = dockerJson(['container', 'inspect', required('container'), '--format',
      '{"running":{{json .State.Running}},"Image":{{json .Image}},"ImageManifestDescriptor":{{json .ImageManifestDescriptor}}}']);
    insist(container.running === true, 'Container is not running');
    insist(container.ImageManifestDescriptor?.digest === actual.platform_manifest_digest, 'Running container manifest differs from OCI');
    insist(`${container.ImageManifestDescriptor?.platform?.os}/${container.ImageManifestDescriptor?.platform?.architecture}` === platform, 'Running container has wrong platform descriptor');
    insist(container.Image === actual.platform_manifest_digest, 'Running container image differs from OCI manifest');
  }
  console.log(JSON.stringify({ checked: true, platform, manifest: actual.platform_manifest_digest, config: actual.config_digest }));
} else if (command === 'create') {
  const archive = required('archive');
  const platform = required('platform');
  const actual = await readArchive(archive);
  insist(actual.os === 'linux' && actual.platform === platform && actual.descriptor_platform === platform, 'Wrong OCI platform');
  const commit = required('checkout-commit');
  const tree = required('checkout-tree');
  const head = required('run-head');
  insist(hexPattern.test(commit) && hexPattern.test(tree) && hexPattern.test(head), 'Invalid source revision or tree');
  insist(commit === head, 'Workflow head and actual checkout differ; refuse ambiguous source binding');
  insist(!process.env.GITHUB_REPOSITORY || process.env.GITHUB_REPOSITORY === REPO, 'Wrong workflow repository');
  insist(gitRevision('HEAD') === commit && gitRevision('HEAD^{tree}') === tree, 'Actual checkout commit or tree differs');
  const receipt = { schema: 'aelira-candidate-image-v1', repository: REPO,
    checkout_commit: commit, checkout_tree: tree, workflow_head_sha: head,
    run_id: Number(required('run-id')), run_attempt: Number(required('run-attempt')),
    kind: required('kind'), platform, archive_name: basename(archive), ...actual,
    evidence: await hashFile(required('evidence')),
    checks: { reproducible_no_cache_builds: 'passed', native_runtime_and_package_gates: 'passed', retained_image_import_and_descriptor: 'passed', dashboard_http_and_descriptor: 'passed' } };
  sourceIdentity(receipt);
  checksComplete(receipt);
  await writeFile(required('output'), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ receipt: required('output'), bytes: actual.archive_bytes, sha256: actual.archive_sha256 }));
} else if (command === 'verify-zip') {
  const artifact = await verifyZipDigest(required('zip'), required('artifact-json'), required('artifact-id'), required('kind'));
  console.log(JSON.stringify({ verified_zip: true, artifact_id: artifact.id, digest: artifact.digest }));
} else if (command === 'verify') {
  const receipt = await readJsonFile(required('receipt'));
  const peer = await readJsonFile(required('peer-receipt'));
  sourceIdentity(receipt);
  sourceIdentity(peer);
  checksComplete(receipt);
  checksComplete(peer);
  insist(receipt.checkout_commit === required('checkout-commit') && receipt.checkout_tree === required('checkout-tree'), 'Wrong checkout source or tree');
  insist(gitRevision(`${receipt.checkout_commit}^{tree}`) === receipt.checkout_tree, 'Checkout commit does not contain the recorded tree');
  insist(receipt.workflow_head_sha === receipt.checkout_commit, 'Ambiguous workflow source binding');
  insist(receipt.platform === required('platform') && receipt.kind === required('kind'), 'Wrong platform or kind');
  insist(peer.kind !== receipt.kind && peer.platform === receipt.platform && peer.checkout_commit === receipt.checkout_commit && peer.checkout_tree === receipt.checkout_tree && peer.run_id === receipt.run_id && peer.run_attempt === receipt.run_attempt && peer.workflow_head_sha === receipt.workflow_head_sha, 'Mixed or incomplete API/dashboard pair');
  const run = await readJsonFile(required('run-json'));
  insist(run.id === receipt.run_id && run.run_attempt === receipt.run_attempt && run.head_sha === receipt.checkout_commit, 'Wrong workflow run, attempt, or head');
  insist(run.event === 'workflow_dispatch' && run.status === 'completed' && run.conclusion === 'success' && run.name === 'CI', 'Workflow did not complete successfully');
  insist(run.repository?.full_name === REPO && /^(?:Aelira-AI\/aelira-core\/)?\.github\/workflows\/ci\.yml(?:@.+)?$/.test(run.path ?? ''), 'Wrong workflow repository or path');
  const artifact = await verifyZipDigest(required('zip'), required('artifact-json'), required('artifact-id'), receipt.kind);
  const peerArtifact = await verifyZipDigest(required('peer-zip'), required('peer-artifact-json'), required('peer-artifact-id'), peer.kind);
  for (const [metadata, bound] of [[artifact, receipt], [peerArtifact, peer]]) {
    insist(metadata.workflow_run?.id === run.id, 'Artifact belongs to another workflow run');
    insist(metadata.workflow_run?.head_sha === bound.checkout_commit, 'Artifact workflow head differs from checkout');
    insist(metadata.name === `candidate-${bound.kind}-${bound.platform.slice(6)}-${bound.checkout_commit}-${bound.run_attempt}`, 'Artifact identity differs from receipt');
  }
  insist(peerArtifact.id !== artifact.id, 'API and dashboard must have separate artifact IDs');
  await verifyZipContents(required('zip'), receipt.kind, required('archive'), required('receipt'), required('evidence'));
  await verifyZipContents(required('peer-zip'), peer.kind, required('peer-archive'), required('peer-receipt'), required('peer-evidence'));
  const actual = await verifyReceipt(receipt, required('archive'), required('evidence'));
  await verifyReceipt(peer, required('peer-archive'), required('peer-evidence'));
  console.log(JSON.stringify({ verified: true, artifact_id: artifact.id, peer_artifact_id: peerArtifact.id, ...actual }));
} else {
  throw new Error('Use inspect, check-docker, create, verify-zip, or verify');
}
