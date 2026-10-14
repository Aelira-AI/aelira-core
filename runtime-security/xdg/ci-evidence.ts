import { copyFile, mkdir, mkdtemp, readdir, realpath, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join } from 'node:path';
import { tmpdir } from 'node:os';
import { assert, command, hashFile, hashManifest, imageContents, imageIdentity, json, keys, pathName, regular, same, sha, small } from './evidence-core.ts';
import { clientCase } from './client-contract.ts';

export const RECIPE = ['README.md', 'build.sh', 'prepare-native.sh', 'prepare-client.sh',
  'build-in-container.sh', 'run-installed-client.sh', 'build-source.py', 'test-source.py',
  'test-mailto-native.sh', 'test-installed-client.py', 'verify-provenance.py',
  'verify-client-provenance.py', 'inputs.sha256', 'changelog-entry.txt',
  'upstream-5f3f563.patch', 'upstream-700e589.patch', 'ubuntu-1.2.1-mailto-backport.patch',
  'ubuntu-frontend-compatibility.patch'];
const BASES = {
  source: 'ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7',
  client: 'debian@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f',
};
const KEYRINGS = {
  source: ['ubuntu-archive-keyring.gpg', '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'],
  client: ['debian-archive-keyring.gpg', '506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017'],
};
export const SOURCE_FILES = ['xdg-utils_1.2.1.orig.tar.gz', 'xdg-utils_1.2.1-2ubuntu2.debian.tar.xz', 'xdg-utils_1.2.1-2ubuntu2.dsc'];
export const OUTPUTS = ['xdg-utils_1.2.1.orig.tar.gz', 'xdg-utils_1.2.1-2ubuntu2+aelira2.dsc',
  'xdg-utils_1.2.1-2ubuntu2+aelira2.debian.tar.xz', 'xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb'];
const REPAIRS = ['upstream-5f3f563.patch', 'ubuntu-1.2.1-mailto-backport.patch', 'ubuntu-frontend-compatibility.patch', 'changelog-entry.txt'];
export const SOURCE_CONTROLS = ['exact-inputs-real-source-package', ...[...SOURCE_FILES, ...REPAIRS, 'input-symlink', 'missing-source'].map(n => 'preflight-refusal:' + n), 'existing-output-preserved'];
export const CLIENT_CASES = ['plain', 'literal-text', 'apostrophe-recipient', 'multiple-recipients',
  'quoted-local-part', 'multiple-explicit-attachments', 'kde-generic-attachment-fallback',
  'uri-lf-body', 'encoded-recipient-list', 'recipient-percent-once', 'uri-attachment-refused', 'empty-to-with-cc'];
export const CLIENT_CONTROLS = [...CLIENT_CASES, 'raw-crlf-front-refused', 'terminal-lf-front-refused', 'explicit-attachment-mailer-refused'];
export const EXPORT_CONTROLS = ['real-native-complete-export', 'all-exported-bytes-rehashed',
  'existing-export-preserved', ...[3, 4, 5, 6, 7, 8].map(n => 'strict-json:' + n),
  'integer-token-contract', 'benign-image-order-and-time', 'image-path-traversal',
  'image-absolute-path', 'image-symlink', 'image-duplicate-member', 'image-truncation',
  'image-nonzero-trailer', 'image-header-checksum', 'image-payload-identity',
  'wrong-revision', 'wrong-tree', 'failed-build-exit', 'unsupported-architecture',
  'typed-build-exit:false', 'typed-build-exit:0.0', 'typed-build-exit:0e0',
  'incomplete-build', 'changed-recipe', 'changed-authenticated-source', 'unequal-package-pair',
  'source-receipt-byte-forgery', 'missing-source-control', 'float-source-control-count',
  'native-log-missing-footer', 'client-claim-drift', 'physical-compose-body-drift',
  'physical-compose-recipient-drift', 'physical-compose-attachment-drift',
  'physical-compose-parser-drift', 'physical-refusal-exit-drift',
  'all-real-compose-case-oracles', 'evidence-symlink', 'evidence-path-traversal',
  'all-real-apt-and-deb-manifests', 'auxiliary-hash-comment', 'auxiliary-hash-duplicate',
  'auxiliary-hash-closure', 'auxiliary-hash-byte-drift',
  'exporter-apt-capture-comment', 'exporter-deb-capture-comment'];
const SCRIPT = '61f66c933cf71ab2786ded6d7038c748406bea5e309af0cd6b737f93fa1cdc26';
const VERSION = '1.2.1-2ubuntu2+aelira2';
const CLIENT_VERSION = '1:140.17.0esr-1~deb13u1';
const PATCHED = { 'scripts/xdg-email.in': 'ebff44d09d67d423cdb3408528b073fe624fd06e0cf373579c0bd41454a84399',
  'autotests/t-xdg-email.sh': 'b4b2a584c7d676ae37f4b39ace131f8433db13b59c834fab3acd86dec460277f' };
type Options = { source: string; dest: string; repo: string; arch: string; revision: string; tree: string; buildExit: number };
type Identity = { sha256: string; size: number };
const hex = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

function packages(data: Buffer, arch: string): Map<string, [string, string]> {
  const result = new Map<string, [string, string]>();
  for (const line of data.toString().trimEnd().split('\n')) {
    const match = /^([a-z0-9][a-z0-9+.-]*)\t([A-Za-z0-9.+:~\-]+)\t(all|amd64|arm64)$/.exec(line);
    assert(match && !result.has(match[1]) && ['all', arch].includes(match[3]), 'Invalid package inventory');
    result.set(match[1], [match[2], match[3]]);
  }
  assert(result.size > 0 && result.size <= 2000, 'Package inventory count'); return result;
}
function signedIndexName(name: string, role: 'source' | 'client'): boolean {
  const prefix = role === 'source' ? '(?:archive\\.ubuntu\\.com_ubuntu|ports\\.ubuntu\\.com_ubuntu-ports|security\\.ubuntu\\.com_ubuntu)_dists_resolute(?:-updates|-security|-backports)?' : '(?:deb\\.debian\\.org_debian|deb\\.debian\\.org_debian-security)_dists_trixie(?:-updates|-security)?';
  return new RegExp('^' + prefix + '_(?:InRelease|(?:main|universe|restricted|multiverse|contrib|non-free|non-free-firmware)_(?:source_Sources|binary-(?:amd64|arm64|all)_Packages)(?:\\.lz4)?)$').test(name);
}

export async function exportEvidence(options: Options): Promise<any> {
  const rows: Record<string, Identity> = Object.create(null);
  const sources: Record<string, string> = Object.create(null);
  let total = 0, phase = 'checkout';
  const receipt: any = { schema: 'aelira-xdg-native-evidence-v1', status: 'fail',
    architecture: ['arm64', 'amd64'].includes(options.arch) ? options.arch : null,
    revision: /^[a-f0-9]{40}$/.test(options.revision) ? options.revision : null,
    tree: /^[a-f0-9]{40}$/.test(options.tree) ? options.tree : null,
    build_exit: Number.isSafeInteger(options.buildExit) ? options.buildExit : null,
    scope: 'Native xdg source/package/client component; final images and advisory acceptance remain separate' };
  assert(isAbsolute(options.dest) && await realpath(dirname(options.dest)) === dirname(options.dest), 'Noncanonical destination parent');
  await mkdir(options.dest, { mode: 0o700 });
  async function copy(name: string, expected?: Identity, actualFile?: string): Promise<Identity> {
    assert(pathName(name) && !(name in rows), 'Invalid duplicate export path');
    const path = actualFile ?? await regular(options.source, name);
    const identity = await hashFile(path);
    if (expected) assert(same(identity, expected), 'Evidence identity mismatch');
    total += identity.size; assert(total <= 5 * 1024 ** 3 && Object.keys(rows).length < 1500, 'Bundle exceeds bound');
    rows[name] = identity; sources[name] = path; return identity;
  }
  try {
    assert(receipt.architecture && receipt.revision && receipt.tree && options.buildExit === 0, 'Invalid build identity');
    assert(isAbsolute(options.source) && await realpath(options.source) === options.source, 'Noncanonical source');
    assert(isAbsolute(options.repo) && await realpath(options.repo) === options.repo, 'Noncanonical repo');
    for (const [ref, expected] of [['HEAD', options.revision], ['HEAD^{tree}', options.tree]]) {
      assert((await command('git', ['-C', options.repo, 'rev-parse', ref])).toString().trim() === expected, 'Checkout mismatch');
    }
    assert(!(await command('git', ['-C', options.repo, 'status', '--porcelain', '--', 'runtime-security/xdg', '.github/workflows/ci.yml'])).length, 'Dirty recipe checkout');
    phase = 'build-status';
    const status = json(await small(options.source, 'build-status.json'));
    assert(status.integers.has('/exit') && same(status.value, { schema: 'aelira-xdg-build-status-v1', phase: 'complete', exit: 0 }), 'Native build incomplete');
    await copy('build-status.json');
    phase = 'recipe';
    assert(same((await readdir(join(options.source, 'frozen-recipe'))).sort(), [...RECIPE].sort()), 'Recipe closure mismatch');
    for (const name of RECIPE) {
      const expected = await hashFile(await regular(join(options.repo, 'runtime-security/xdg'), name));
      await copy('frozen-recipe/' + name, expected);
    }
    const expectedRecipe = [...RECIPE].sort().map(n => `${rows['frozen-recipe/' + n].sha256}  ${n}\n`).join('');
    assert((await small(options.source, 'recipe-sha256.txt')).toString() === expectedRecipe, 'Executed recipe mismatch');
    await copy('recipe-sha256.txt');
    for (const name of ['ci-evidence.ts', 'evidence-core.ts', 'client-contract.ts', 'test-ci-evidence.ts', 'verify-export.ts']) {
      await copy('export-tools/' + name, undefined, await regular(join(options.repo, 'runtime-security/xdg'), name));
    }
    await copy('export-tools/ci-workflow.yml', undefined, await regular(options.repo, '.github/workflows/ci.yml'));
    const sourcePins: Record<string, Identity> = Object.create(null);
    for (const line of (await small(options.source, 'frozen-recipe/inputs.sha256')).toString().trimEnd().split('\n')) {
      const match = /^([a-f0-9]{64})  (.+)$/.exec(line);
      assert(match && SOURCE_FILES.includes(match[2]) && !(match[2] in sourcePins), 'Invalid source pin');
      const data = await small(options.source, 'source-input/' + match[2]);
      assert(sha(data) === match[1], 'Original source mismatch'); sourcePins[match[2]] = { sha256: match[1], size: data.length };
    }
    assert(same(Object.keys(sourcePins).sort(), [...SOURCE_FILES].sort()), 'Source closure mismatch');
    phase = 'packages';
    for (const name of OUTPUTS) {
      const first = await copy('build-a/packages/' + name);
      await copy('build-b/packages/' + name, first);
    }
    for (const attempt of ['a', 'b']) {
      const controls = json(await small(options.source, `build-${attempt}/source-controls.json`));
      assert(controls.integers.has('/count') && same(controls.value, { schema: 'aelira-xdg-source-controls-v1', status: 'pass', count: 11, controls: SOURCE_CONTROLS }), 'Source control contract mismatch');
      await copy(`build-${attempt}/source-controls.json`);
      const source = json(await small(options.source, `build-${attempt}/source-receipt.json`)).value;
      keys(source, ['schema', 'status', 'version', 'inputs', 'patched_sources', 'artifacts', 'scope']);
      assert(source.schema === 'aelira-xdg-source-v1' && source.status === 'pass' && source.version === VERSION && same(source.patched_sources, PATCHED), 'Patched source identity mismatch');
      const inputs = { ...sourcePins };
      for (const name of REPAIRS) inputs[name] = rows['frozen-recipe/' + name];
      assert(same(source.inputs, inputs) && same(source.artifacts, Object.fromEntries(OUTPUTS.slice(0, 3).map(n => [n, rows[`build-${attempt}/packages/${n}`]]))), 'Source receipt does not bind actual bytes');
      await copy(`build-${attempt}/source-receipt.json`);
      const log = await small(options.source, `build-${attempt}/console-private.log`);
      assert(log.toString().endsWith(`native_xdg_package=pass architecture=${options.arch} version=${VERSION}\n`) && !log.toString().includes('TEST FAILURES:'), 'Incomplete native package log');
      await copy(`build-${attempt}/console-private.log`);
    }
    phase = 'client-fields';
    const client = json(await small(options.source, 'installed/frontend-controls.json')).value;
    keys(client, ['schema', 'status', 'passed', 'failed', 'script_sha256', 'architecture', 'package_version', 'client_version', 'scope']);
    assert(client.schema === 'aelira-xdg-installed-thunderbird-controls-v1' && client.status === 'pass' && client.script_sha256 === SCRIPT && client.architecture === options.arch && client.package_version === VERSION && client.client_version === CLIENT_VERSION && same(client.passed, CLIENT_CONTROLS) && same(client.failed, []), 'Installed client control mismatch');
    await copy('installed/frontend-controls.json');
    const console = (await small(options.source, 'client-console-private.log')).toString();
    const lines = console.trimEnd().split('\n');
    assert(lines.length === CLIENT_CONTROLS.length + 1 && same(json(lines.pop()!).value, client), 'Missing final client footer');
    const outcomes = lines.map(line => json(line));
    assert(same(outcomes.map(r => r.value.case), CLIENT_CONTROLS) && outcomes.every(r => r.value.status === 'pass'), 'Missing physical client outcome');
    await copy('client-console-private.log');
    for (const name of CLIENT_CASES) {
      const result = json(await small(options.source, `installed/${name}-result.json`)).value;
      const fields = clientCase(name, result);
      const outcome = outcomes[CLIENT_CASES.indexOf(name)].value;
      keys(outcome, ['case', 'status', 'fields']);
      assert(same(outcome.fields, fields), 'Physical console differs from real compose fields');
      await copy(`installed/${name}-result.json`);
      await copy(`installed/${name}-handoff.log`);
    }
    for (const [index, exit] of [1, 1, 3].entries()) {
      const outcome = outcomes[CLIENT_CASES.length + index];
      keys(outcome.value, ['case', 'status', 'exit']);
      assert(outcome.integers.has('/exit') && outcome.value.exit === exit, 'Changed refusal outcome');
    }
    const builders: Record<string, string> = Object.create(null);
    const scratch = await mkdtemp(join(tmpdir(), 'xdg-image-replay-'));
    async function validateImage(image: string, base: string, archive: string, exportName: string): Promise<void> {
      assert(/^sha256:[a-f0-9]{64}$/.test(image), 'Invalid immutable image ID');
      assert((await command('docker', ['image', 'inspect', '--format', '{{.Architecture}}', image])).toString().trim() === options.arch, 'Image architecture mismatch');
      const baseLayers = json(await command('docker', ['image', 'inspect', '--format', '{{json .RootFS.Layers}}', base])).value;
      const layers = json(await command('docker', ['image', 'inspect', '--format', '{{json .RootFS.Layers}}', image])).value;
      assert(Array.isArray(baseLayers) && baseLayers.length > 0 && Array.isArray(layers) && same(layers.slice(0, baseLayers.length), baseLayers), 'Image base ancestry mismatch');
      const captured = await regular(options.source, archive);
      const identity = await hashFile(captured);
      const fresh = join(scratch, 'image-comparison.tar');
      await command('docker', ['save', image, '-o', fresh], 300_000);
      await imageIdentity(captured, image, options.arch);
      assert(same(await imageContents(captured), await imageContents(fresh)), 'Saved image differs from actual immutable image');
      await copy(exportName, identity, captured);
      // The complete producer archive remains preserved; only this successful
      // temporary comparison copy is emptied before comparing the next image.
      await writeFile(fresh, Buffer.alloc(0));
    }
    phase = 'builders';
    for (const role of ['source', 'client'] as const) {
      const prefix = role + '-provenance/';
      assert((await small(options.source, prefix + 'base-image.txt')).toString() === BASES[role] + '\n', 'Pinned base mismatch');
      const image = (await small(options.source, prefix + 'builder-id.txt')).toString();
      assert(/^sha256:[a-f0-9]{64}\n$/.test(image), 'Invalid builder ID'); builders[role] = image.trim();
      const inventory = await small(options.source, prefix + 'toolchain-packages.tsv');
      const baseInventory = await small(options.source, prefix + 'base-packages.tsv');
      for (const [id, expected] of [[builders[role], inventory], [BASES[role], baseInventory]] as const) {
        const actual = await command('docker', ['run', '--rm', '--entrypoint', '/usr/bin/dpkg-query', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', `${process.getuid!()}:${process.getgid!()}`, id, '-W', '-f=${Package}\t${Version}\t${Architecture}\n']);
        assert(actual.equals(expected), 'Actual image inventory mismatch');
      }
      const basePackages = packages(baseInventory, options.arch), buildPackages = packages(inventory, options.arch);
      const delta = [...buildPackages].filter(([name, values]) => !same(basePackages.get(name), values)).map(([name, values]) => [name, ...values].join('\t')).sort();
      const declared = (await small(options.source, prefix + 'toolchain-delta.tsv')).toString().trimEnd().split('\n').sort();
      assert(same(delta, declared), 'Declared installed delta mismatch');
      const keyring = KEYRINGS[role]; const keyFile = await small(options.source, prefix + keyring[0]);
      assert(sha(keyFile) === keyring[1], 'Trust keyring mismatch');
      const indexNames = await readdir(join(options.source, prefix, 'apt-lists'));
      for (const name of indexNames) { assert(signedIndexName(name, role), 'Unexpected index path'); await regular(options.source, prefix + 'apt-lists/' + name, 128 * 1024 ** 2); }
      const debNames = await readdir(join(options.source, prefix, 'toolchain-debs'));
      for (const name of debNames) { assert(/^[a-z0-9][a-z0-9+._:%~\-]*_(?:all|amd64|arm64)\.deb$/.test(name), 'Unexpected package path'); await regular(options.source, prefix + 'toolchain-debs/' + name, 256 * 1024 ** 2); }
      for (const name of ['base-image.txt', 'builder-id.txt', 'base-packages.tsv', 'toolchain-packages.tsv', 'toolchain-delta.tsv', 'apt-index-sha256.txt', 'toolchain-debs.sha256']) await copy(prefix + name);
      await copy(prefix + keyring[0], { sha256: keyring[1], size: keyFile.length });
    }
    phase = 'signed-inputs';
    for (const role of ['source', 'client'] as const) {
      const prefix = role + '-provenance/';
      const verified = json(await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', `${process.getuid!()}:${process.getgid!()}`, '--memory', '1g', '--pids-limit', '64', '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '-e', 'HOME=/tmp', '-v', join(options.source, 'frozen-recipe') + ':/recipe:ro', '-v', join(options.source, 'source-input') + ':/source-input:ro', '-v', join(options.source, role + '-provenance') + ':/provenance:ro', builders.source, 'python3', '-B', '/recipe/' + (role === 'source' ? 'verify-provenance.py' : 'verify-client-provenance.py')], 180_000));
      const signed = verified.value;
      keys(signed, role === 'source' ? ['schema', 'status', 'source_files', 'source_stanzas', 'toolchain_files', 'authenticated_files', 'keyring_sha256'] : ['schema', 'status', 'toolchain_files', 'authenticated_files', 'keyring_sha256']);
      assert(signed.schema === (role === 'source' ? 'aelira-xdg-ubuntu-inputs-v1' : 'aelira-xdg-client-inputs-v1') && signed.status === 'pass' && signed.keyring_sha256 === KEYRINGS[role][1], 'Signed input receipt mismatch');
      if (role === 'source') {
        assert(verified.integers.has('/source_stanzas') && signed.source_stanzas > 0 && same(signed.source_files, sourcePins), 'Signed source closure mismatch');
        for (const [name, identity] of Object.entries(sourcePins)) await copy('source-input/' + name, identity);
      }
      const signedDebs = Object.entries(signed.toolchain_files) as [string, any][];
      const expectedDelta = (await small(options.source, prefix + 'toolchain-delta.tsv')).toString().trimEnd().split('\n').sort();
      assert(same(signedDebs.map(([, r]) => `${r.package}\t${r.version}\t${r.architecture}`).sort(), expectedDelta), 'Signed toolchain does not match actual delta');
      assert(same(signedDebs.map(([n]) => n).sort(), (await readdir(join(options.source, prefix, 'toolchain-debs'))).sort()), 'Toolchain archive closure mismatch');
      hashManifest(await small(options.source, prefix + 'toolchain-debs.sha256'), signed.toolchain_files);
      for (const [name, row] of signedDebs) {
        keys(row, ['package', 'version', 'architecture', 'sha256', 'size']);
        assert(hex(row.sha256) && Number.isSafeInteger(row.size) && row.size > 0 && verified.integers.has('/toolchain_files/' + name.replaceAll('~', '~0').replaceAll('/', '~1') + '/size'), 'Invalid signed package identity');
        await copy(prefix + 'toolchain-debs/' + name, { sha256: row.sha256, size: row.size });
      }
      const indexIdentities: Record<string, Identity> = { ...signed.authenticated_files };
      const extraReleases: Record<string, Identity> = Object.create(null);
      for (const name of await readdir(join(options.source, prefix, 'apt-lists'))) {
        if (name in indexIdentities) continue;
        assert(signedIndexName(name, role) && name.endsWith('_InRelease'), 'Unsigned index in capture');
        await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', `${process.getuid!()}:${process.getgid!()}`, '-v', join(options.source, role + '-provenance') + ':/provenance:ro', '--entrypoint', '/usr/bin/gpgv', builders.source, '--keyring', '/provenance/' + KEYRINGS[role][0], '/provenance/apt-lists/' + name]);
        indexIdentities[name] = extraReleases[name] = await hashFile(await regular(options.source, prefix + 'apt-lists/' + name, 128 * 1024 ** 2));
      }
      hashManifest(await small(options.source, prefix + 'apt-index-sha256.txt'), indexIdentities);
      for (const [name, row] of Object.entries(indexIdentities)) {
        keys(row, ['sha256', 'size']); assert(signedIndexName(name, role) && hex(row.sha256) && Number.isSafeInteger(row.size) && row.size > 0, 'Invalid signed index identity');
        await copy(prefix + 'apt-lists/' + name, row);
      }
      const extras = { schema: 'aelira-xdg-extra-release-signatures-v1', status: 'pass', keyring_sha256: KEYRINGS[role][1], files: extraReleases };
      const extraFile = join(scratch, role + '-additional-release-signatures.json');
      await writeFile(extraFile, JSON.stringify(extras, null, 2) + '\n');
      await copy(prefix + 'additional-release-signatures.json', undefined, extraFile);
      const original = json(await small(options.source, role === 'source' ? 'build-a/signed-chain.json' : prefix + 'signed-chain.json')).value;
      assert(same(original, signed), 'Captured signature receipt mismatch');
      await copy(role === 'source' ? 'build-a/signed-chain.json' : prefix + 'signed-chain.json');
      await validateImage(builders[role], BASES[role], prefix + 'builder.tar', prefix + 'builder.tar');
    }
    const packageData = (await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', `${process.getuid!()}:${process.getgid!()}`, '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m', '-e', 'HOME=/tmp', '-v', join(options.source, 'build-a/packages') + ':/packages:ro', '--entrypoint', '/bin/bash', builders.source, '-c', 'set -eu; cd /tmp; dpkg-source -x /packages/xdg-utils_1.2.1-2ubuntu2+aelira2.dsc source >/tmp/extract.log 2>&1; sha256sum source/scripts/xdg-email.in source/autotests/t-xdg-email.sh; dpkg-deb -f /packages/xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb Package Version Architecture; dpkg-deb -x /packages/xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb binary; sha256sum binary/usr/bin/xdg-email'], 120_000)).toString();
    assert(packageData === `${PATCHED['scripts/xdg-email.in']}  source/scripts/xdg-email.in\n${PATCHED['autotests/t-xdg-email.sh']}  source/autotests/t-xdg-email.sh\nPackage: xdg-utils\nVersion: ${VERSION}\nArchitecture: all\n${SCRIPT}  binary/usr/bin/xdg-email\n`, 'Candidate source/binary payload mismatch');
    await writeFile(join(scratch, 'package-replay.txt'), packageData);
    await copy('package-replay.txt', undefined, join(scratch, 'package-replay.txt'));
    phase = 'installed-client';
    const installedBytes = await small(options.source, 'installed-client-id.txt');
    assert(/^sha256:[a-f0-9]{64}\n$/.test(installedBytes.toString()), 'Invalid installed image ID');
    const installed = installedBytes.toString().trim();
    const actualScript = await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--entrypoint', '/usr/bin/sha256sum', installed, '/usr/bin/xdg-email']);
    assert(actualScript.toString() === SCRIPT + '  /usr/bin/xdg-email\n', 'Installed script mismatch');
    const actualVersion = await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--entrypoint', '/usr/bin/dpkg-query', installed, '-W', '-f=${Version}', 'xdg-utils']);
    assert(actualVersion.toString() === VERSION, 'Installed package version mismatch');
    const installedInventory = packages(await command('docker', ['run', '--rm', '--network', 'none', '--read-only', '--entrypoint', '/usr/bin/dpkg-query', installed, '-W', '-f=${Package}\t${Version}\t${Architecture}\n']), options.arch);
    const expectedInventory = packages(await small(options.source, 'client-provenance/toolchain-packages.tsv'), options.arch);
    expectedInventory.set('xdg-utils', [VERSION, 'all']);
    assert(same([...installedInventory].sort(), [...expectedInventory].sort()), 'Installed image inventory mismatch');
    await copy('installed-client-id.txt');
    await copy('client-install-private.log');
    await validateImage(installed, builders.client, 'installed-client.tar', 'installed-client.tar');
    receipt.builders = builders; receipt.installed_client = installed;
    phase = 'copy';
    for (const [name, identity] of Object.entries(rows)) {
      const target = join(options.dest, name); await mkdir(dirname(target), { recursive: true });
      await copyFile(sources[name], target);
      assert(same(await hashFile(target), identity), 'Changed evidence during copy');
    }
    receipt.status = 'pass'; phase = 'complete';
  } catch (error) {
    receipt.error_type = error instanceof Error ? error.name : 'Error';
  }
  receipt.phase = phase; receipt.files = receipt.status === 'pass' ? rows : {};
  await writeFile(join(options.dest, 'component.json'), JSON.stringify(receipt, null, 2) + '\n');
  return receipt;
}

if (import.meta.main) {
  const values = Object.create(null);
  const allowed = new Set(['source', 'dest', 'repo', 'arch', 'revision', 'tree', 'build-exit']);
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i]?.slice(2);
    assert(process.argv[i]?.startsWith('--') && allowed.has(key) && !(key in values) && process.argv[i + 1] !== undefined, 'Invalid arguments');
    values[key] = process.argv[i + 1];
  }
  assert([...allowed].every(k => k in values) && /^-?\d+$/.test(values['build-exit']), 'Missing arguments');
  const receipt = await exportEvidence({ ...values, buildExit: Number(values['build-exit']) });
  console.log(JSON.stringify({ schema: receipt.schema, status: receipt.status, phase: receipt.phase, architecture: receipt.architecture, revision: receipt.revision, tree: receipt.tree, files: Object.keys(receipt.files).length }));
  if (receipt.status !== 'pass') process.exitCode = 1;
}
