import { copyFile, lstat, mkdir, readFile, readdir, symlink, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { assert, command, hashFile, hashManifest, imageContents, json, regular, same, small } from './evidence-core.ts';
import { CLIENT_CASES, EXPORT_CONTROLS, OUTPUTS, RECIPE, SOURCE_FILES, exportEvidence } from './ci-evidence.ts';
import { clientCase } from './client-contract.ts';

const values: Record<string, string> = Object.create(null);
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i]?.slice(2);
  assert(process.argv[i]?.startsWith('--') && ['source', 'repo', 'scratch', 'dest', 'arch'].includes(key) && !(key in values) && process.argv[i + 1], 'Invalid test arguments');
  values[key] = process.argv[i + 1];
}
assert(['source', 'repo', 'scratch', 'dest', 'arch'].every(k => k in values), 'Missing test arguments');
const source = resolve(values.source), repo = resolve(values.repo), scratch = resolve(values.scratch), dest = resolve(values.dest);
try { await lstat(dest); throw new Error('Existing evidence destination'); }
catch (error: any) { if (error.code !== 'ENOENT') throw error; }
await mkdir(scratch, { mode: 0o700 });
const options = { source, repo, arch: values.arch, revision: (await command('git', ['-C', repo, 'rev-parse', 'HEAD'])).toString().trim(), tree: (await command('git', ['-C', repo, 'rev-parse', 'HEAD^{tree}'])).toString().trim(), buildExit: 0 };
const controls: string[] = [];
let phase = 'real-export';
async function refused(name: string, action: () => unknown | Promise<unknown>): Promise<void> {
  let blocked = false; try { await action(); } catch { blocked = true; }
  assert(blocked, 'Refusal did not fire: ' + name); controls.push(name);
}
function tar(entries: [string, string, number?][], stamp = '00000000000'): Buffer {
  const rows: Buffer[] = [];
  for (const [name, content, kind = 48] of entries) {
    const data = Buffer.from(content), header = Buffer.alloc(512);
    header.write(name, 0, 100); header.write('0000644\0', 100); header.write('0000000\0', 108); header.write('0000000\0', 116);
    header.write(data.length.toString(8).padStart(11, '0') + '\0', 124); header.write(stamp + '\0', 136);
    header.fill(32, 148, 156); header[156] = kind; header.write('ustar\0', 257); header.write('00', 263);
    const sum = [...header].reduce((a, b) => a + b, 0);
    header.write(sum.toString(8).padStart(6, '0') + '\0 ', 148);
    rows.push(header, data, Buffer.alloc((512 - data.length % 512) % 512));
  }
  rows.push(Buffer.alloc(1024)); return Buffer.concat(rows);
}
async function archive(name: string, data: Buffer): Promise<string> {
  const path = join(scratch, name + '.tar'); await writeFile(path, data); return path;
}

// A real successful export establishes the success path before refusal claims.
try {
const good = await exportEvidence({ ...options, dest });
assert(good.status === 'pass', 'Real export failed in phase ' + good.phase);
controls.push('real-native-complete-export');
assert(Object.keys(good.files).length > 400, 'Incomplete real closure');
for (const [name, identity] of Object.entries(good.files)) assert(same(await hashFile(await regular(dest, name)), identity), 'Exported byte mismatch');
controls.push('all-exported-bytes-rehashed');
phase = 'contracts';
await refused('existing-export-preserved', () => exportEvidence({ ...options, dest }));
assert(same(json(await small(dest, 'component.json')).value, good), 'Existing evidence changed');

for (const text of ['{"exit":0,"exit":0}', '{"exit":0}trailer', '{"exit":NaN}', '{"exit":00}', '\u00a0{"exit":0}', '{"exit":1e999}']) {
  await refused('strict-json:' + controls.length, () => json(text));
}
for (const [token, integer] of [['0', true], ['0.0', false], ['0e0', false], ['false', false], ['"0"', false]] as const) {
  assert(json('{"exit":' + token + '}').integers.has('/exit') === integer, 'JSON integer-token mismatch');
}
controls.push('integer-token-contract');
const originalTar = await archive('good-image', tar([['one', 'payload'], ['two', 'second']]));
const reordered = await archive('reordered-image', tar([['two', 'second'], ['one', 'payload']], '00000000100'));
assert(same(await imageContents(originalTar), await imageContents(reordered)), 'Benign image metadata/order rejected');
controls.push('benign-image-order-and-time');
for (const [name, data] of [
  ['image-path-traversal', tar([['../escape', 'x']])],
  ['image-absolute-path', tar([['/escape', 'x']])],
  ['image-symlink', tar([['link', '', 50]])],
  ['image-duplicate-member', tar([['one', 'x'], ['one', 'x']])],
  ['image-truncation', tar([['one', 'x']]).subarray(0, 600)],
  ['image-nonzero-trailer', Buffer.concat([tar([['one', 'x']]), Buffer.from('x')])],
  ['image-header-checksum', Buffer.from(tar([['one', 'x']])).fill(65, 0, 1)],
] as [string, Buffer][]) await refused(name, async () => imageContents(await archive(name, data)));
assert(!same(await imageContents(originalTar), await imageContents(await archive('changed-payload', tar([['one', 'changed'], ['two', 'second']])))), 'Changed payload accepted as equal');
controls.push('image-payload-identity');

const localNames = ['build-status.json', 'recipe-sha256.txt', 'client-console-private.log',
  ...RECIPE.map(n => 'frozen-recipe/' + n), ...SOURCE_FILES.map(n => 'source-input/' + n),
  ...['a', 'b'].flatMap(a => ['source-controls.json', 'source-receipt.json', 'console-private.log', ...OUTPUTS.map(n => 'packages/' + n)].map(n => `build-${a}/` + n)),
  'installed/frontend-controls.json', ...CLIENT_CASES.flatMap(n => [`installed/${n}-result.json`, `installed/${n}-handoff.log`])];
let attempt = 0;
phase = 'local-refusals';
async function mutation(name: string, phase: string, target: string | null, change: (data: Buffer) => Buffer, override: Record<string, unknown> = {}, provenance = false): Promise<void> {
  const root = join(scratch, 'mutation-' + attempt++); await mkdir(root);
  for (const file of localNames) {
    await mkdir(dirname(join(root, file)), { recursive: true });
    if (target === file) await writeFile(join(root, file), change(await readFile(await regular(source, file))));
    else await copyFile(await regular(source, file), join(root, file));
  }
  if (provenance) {
    await copyFile(await regular(source, 'build-a/signed-chain.json'), join(root, 'build-a/signed-chain.json'));
    for (const role of ['source', 'client']) {
      const prefix = role + '-provenance'; await mkdir(join(root, prefix));
      for (const entry of await readdir(join(source, prefix), { withFileTypes: true })) {
        const name = prefix + '/' + entry.name;
        if (entry.isDirectory()) {
          assert(['apt-lists', 'toolchain-debs'].includes(entry.name), 'Unexpected provenance directory');
          await mkdir(join(root, name));
          for (const leaf of await readdir(join(source, name))) await copyFile(await regular(source, name + '/' + leaf), join(root, name, leaf), constants.COPYFILE_FICLONE);
        } else if (name === target) await writeFile(join(root, name), change(await readFile(await regular(source, name))));
        else if (entry.name !== 'builder.tar' || (role === 'source' && !target?.startsWith('source-provenance/'))) await copyFile(await regular(source, name), join(root, name), constants.COPYFILE_FICLONE);
      }
    }
  }
  const destination = join(scratch, 'refusal-' + attempt);
  const row = await exportEvidence({ ...options, source: root, dest: destination, ...override });
  assert(row.status === 'fail' && row.phase === phase && same(row.files, {}), 'Wrong refusal phase: ' + name);
  if (provenance) assert(row.error_type === 'HashManifestError', 'Refusal came from a different guard: ' + name);
  assert(same(await readdir(destination), ['component.json']), 'Refusal exported unvalidated files'); controls.push(name);
}
const changeJSON = (action: (row: any) => void) => (data: Buffer) => {
  const value = json(data).value; action(value); return Buffer.from(JSON.stringify(value));
};
await mutation('wrong-revision', 'checkout', null, d => d, { revision: '0'.repeat(40) });
await mutation('wrong-tree', 'checkout', null, d => d, { tree: '0'.repeat(40) });
await mutation('failed-build-exit', 'checkout', null, d => d, { buildExit: 1 });
await mutation('unsupported-architecture', 'checkout', null, d => d, { arch: 'ppc64' });
for (const token of ['false', '0.0', '0e0']) await mutation('typed-build-exit:' + token, 'build-status', 'build-status.json', d => Buffer.from(d.toString().replace('"exit":0', '"exit":' + token).replace('"exit": 0', '"exit":' + token)));
await mutation('incomplete-build', 'build-status', 'build-status.json', changeJSON(r => r.phase = 'offline-build-b'));
await mutation('changed-recipe', 'recipe', 'frozen-recipe/build-source.py', d => Buffer.concat([d, Buffer.from('\n# changed\n')]));
await mutation('changed-authenticated-source', 'recipe', 'source-input/' + SOURCE_FILES[0], d => { const r = Buffer.from(d); r[0] ^= 1; return r; });
await mutation('unequal-package-pair', 'packages', 'build-b/packages/' + OUTPUTS[3], d => Buffer.concat([d, Buffer.from('x')]));
await mutation('source-receipt-byte-forgery', 'packages', 'build-a/source-receipt.json', changeJSON(r => r.artifacts[OUTPUTS[0]].sha256 = '0'.repeat(64)));
await mutation('missing-source-control', 'packages', 'build-a/source-controls.json', changeJSON(r => r.controls.pop()));
await mutation('float-source-control-count', 'packages', 'build-a/source-controls.json', d => Buffer.from(d.toString().replace('"count": 11', '"count": 11.0').replace('"count":11,', '"count":11.0,')));
await mutation('native-log-missing-footer', 'packages', 'build-a/console-private.log', d => Buffer.from(d.toString().split('\n').slice(0, -2).join('\n')));
await mutation('client-claim-drift', 'client-fields', 'installed/frontend-controls.json', changeJSON(r => r.failed.push('missing-control')));
await mutation('physical-compose-body-drift', 'client-fields', 'installed/plain-result.json', changeJSON(r => r.windows.find((w: any) => w.fields).fields.body = 'changed'));
await mutation('physical-compose-recipient-drift', 'client-fields', 'installed/quoted-local-part-result.json', changeJSON(r => r.windows.find((w: any) => w.fields).fields.recipientMailboxes[0].mailboxes = ['truncated@example.invalid']));
await mutation('physical-compose-attachment-drift', 'client-fields', 'installed/multiple-explicit-attachments-result.json', changeJSON(r => r.windows.find((w: any) => w.fields).fields.attachments.pop()));
await mutation('physical-compose-parser-drift', 'client-fields', 'installed/plain-result.json', changeJSON(r => r.windows.find((w: any) => w.fields).parsed.attachment = 'file:///work/sentinel.txt'));
await mutation('physical-refusal-exit-drift', 'client-fields', 'client-console-private.log', d => Buffer.from(d.toString().replace('"exit": 3', '"exit": false')));

for (const name of CLIENT_CASES) clientCase(name, json(await small(source, `installed/${name}-result.json`)).value);
controls.push('all-real-compose-case-oracles');
await symlink(join(source, 'build-status.json'), join(scratch, 'symlink.json'));
await refused('evidence-symlink', () => regular(scratch, 'symlink.json'));
await refused('evidence-path-traversal', () => regular(scratch, '../outside'));
for (const role of ['source', 'client']) {
  for (const [folder, manifest] of [['apt-lists', 'apt-index-sha256.txt'], ['toolchain-debs', 'toolchain-debs.sha256']]) {
    const identities: Record<string, { sha256: string; size: number }> = Object.create(null);
    for (const name of await readdir(join(source, role + '-provenance', folder))) identities[name] = await hashFile(await regular(source, role + '-provenance/' + folder + '/' + name));
    hashManifest(await small(source, role + '-provenance/' + manifest), identities);
  }
}
controls.push('all-real-apt-and-deb-manifests');
phase = 'manifest-contracts';
const hashes = { 'one.deb': { sha256: 'a'.repeat(64), size: 1 }, 'two.deb': { sha256: 'b'.repeat(64), size: 2 } };
const manifest = Buffer.from(`${hashes['one.deb'].sha256}  ./one.deb\n${hashes['two.deb'].sha256}  ./two.deb\n`);
hashManifest(manifest, hashes);
await refused('auxiliary-hash-comment', () => hashManifest(Buffer.concat([manifest, Buffer.from('# capture text\n')]), hashes));
await refused('auxiliary-hash-duplicate', () => hashManifest(Buffer.concat([manifest, manifest]), hashes));
await refused('auxiliary-hash-closure', () => hashManifest(Buffer.from(manifest.toString().split('\n')[0] + '\n'), hashes));
await refused('auxiliary-hash-byte-drift', () => hashManifest(Buffer.from(manifest.toString().replace('a', 'c')), hashes));
for (const [name, target] of [
  ['exporter-apt-capture-comment', 'source-provenance/apt-index-sha256.txt'],
  ['exporter-deb-capture-comment', 'client-provenance/toolchain-debs.sha256'],
]) {
  phase = name;
  await mutation(name, 'signed-inputs', target, d => Buffer.concat([d, Buffer.from('# unexpected metadata\n')]), {}, true);
}
const receipt = { schema: 'aelira-xdg-export-controls-v1', status: 'pass', count: controls.length, controls };
assert(same(controls, EXPORT_CONTROLS), 'Export control closure changed');
await writeFile(join(dest, 'export-controls.json'), JSON.stringify(receipt, null, 2) + '\n');
good.files['export-controls.json'] = await hashFile(join(dest, 'export-controls.json'));
await writeFile(join(dest, 'component.json'), JSON.stringify(good, null, 2) + '\n');
console.log(JSON.stringify(receipt));
} catch (error: any) {
  const status = { schema: 'aelira-xdg-export-test-failure-v1', status: 'fail', phase,
    completed: controls, count: controls.length, error_type: error instanceof Error ? error.name : 'Error',
    error_code: ['EPERM', 'EACCES', 'EXDEV', 'ENOENT', 'EEXIST', 'ENOSPC'].includes(error?.code) ? error.code : null };
  await writeFile(join(dest, 'export-test-failure.json'), JSON.stringify(status, null, 2) + '\n');
  const component = json(await small(dest, 'component.json')).value;
  if (component.status === 'pass') { component.status = 'fail'; component.phase = 'export-controls'; }
  component.files['export-test-failure.json'] = await hashFile(join(dest, 'export-test-failure.json'));
  await writeFile(join(dest, 'component.json'), JSON.stringify(component, null, 2) + '\n');
  console.log(JSON.stringify(status)); process.exitCode = 1;
}
