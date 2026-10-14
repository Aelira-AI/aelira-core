import { readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { assert, hashFile, imageIdentity, json, keys, pathName, regular, same, small } from './evidence-core.ts';
import { CLIENT_CASES, EXPORT_CONTROLS, RECIPE } from './ci-evidence.ts';
import { clientCase } from './client-contract.ts';

const values: Record<string, string> = Object.create(null);
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i]?.slice(2);
  assert(process.argv[i]?.startsWith('--') && ['source', 'repo', 'arch', 'revision', 'tree'].includes(key) && !(key in values) && process.argv[i + 1], 'Invalid verification arguments');
  values[key] = process.argv[i + 1];
}
assert(['source', 'repo', 'arch', 'revision', 'tree'].every(k => k in values), 'Missing verification arguments');
const source = resolve(values.source), repo = resolve(values.repo);
const parsed = json(await small(source, 'component.json')), receipt = parsed.value;
if (process.env.AELIRA_XDG_CI_EXIT !== undefined && process.env.AELIRA_XDG_CI_EXIT !== '0') {
  if (receipt.status === 'pass') receipt.phase = 'export-controls';
  receipt.status = 'fail';
  await writeFile(join(source, 'component.json'), JSON.stringify(receipt, null, 2) + '\n');
}
assert(receipt.schema === 'aelira-xdg-native-evidence-v1' && receipt.status === 'pass' && receipt.phase === 'complete' && parsed.integers.has('/build_exit') && receipt.build_exit === 0, 'Incomplete component');
assert(receipt.architecture === values.arch && receipt.revision === values.revision && receipt.tree === values.tree && ['amd64', 'arm64'].includes(values.arch), 'Wrong component identity');
assert(/^[a-f0-9]{40}$/.test(values.revision) && /^[a-f0-9]{40}$/.test(values.tree), 'Invalid checkout identity');
const names = Object.keys(receipt.files);
assert(names.length > 400 && names.length < 1500, 'Invalid component closure');
let total = 0;
for (const name of names) {
  assert(pathName(name) && name !== 'component.json', 'Invalid receipt path');
  const row = receipt.files[name]; keys(row, ['size', 'sha256']);
  assert(Number.isSafeInteger(row.size) && row.size >= 0 && /^[a-f0-9]{64}$/.test(row.sha256) && parsed.integers.has('/files/' + name.replaceAll('~', '~0').replaceAll('/', '~1') + '/size'), 'Invalid file identity');
  total += row.size; assert(total <= 5 * 1024 ** 3, 'Component exceeds bound');
  assert(same(await hashFile(await regular(source, name)), row), 'Changed component bytes');
}
let visited = 0;
async function walk(prefix = '', depth = 0): Promise<string[]> {
  assert(depth <= 16, 'Component directory depth bound');
  const paths: string[] = [];
  for (const item of await readdir(join(source, prefix), { withFileTypes: true })) {
    assert(++visited <= 2000, 'Component directory entry bound');
    assert(!item.isSymbolicLink(), 'Component symlink');
    const name = prefix + item.name;
    if (item.isDirectory()) paths.push(...await walk(name + '/', depth + 1));
    else { assert(item.isFile(), 'Unexpected component file kind'); paths.push(name); }
    assert(paths.length <= 1500, 'Component path bound');
  }
  return paths;
}
assert(same((await walk()).sort(), [...names, 'component.json'].sort()), 'Undeclared component bytes');
for (const name of RECIPE) assert(same(await hashFile(await regular(source, 'frozen-recipe/' + name)), await hashFile(await regular(join(repo, 'runtime-security/xdg'), name))), 'Wrong producer source');
for (const name of ['ci-evidence.ts', 'evidence-core.ts', 'client-contract.ts', 'test-ci-evidence.ts', 'verify-export.ts']) assert(same(await hashFile(await regular(source, 'export-tools/' + name)), await hashFile(await regular(join(repo, 'runtime-security/xdg'), name))), 'Wrong export source');
assert(same(await hashFile(await regular(source, 'export-tools/ci-workflow.yml')), await hashFile(await regular(repo, '.github/workflows/ci.yml'))), 'Wrong workflow source');
const controls = json(await small(source, 'export-controls.json'));
assert(controls.value.schema === 'aelira-xdg-export-controls-v1' && controls.value.status === 'pass' && controls.integers.has('/count') && controls.value.count === EXPORT_CONTROLS.length && same(controls.value.controls, EXPORT_CONTROLS), 'Incomplete export controls');
for (const name of CLIENT_CASES) clientCase(name, json(await small(source, `installed/${name}-result.json`)).value);
const sourceLayers = await imageIdentity(await regular(source, 'source-provenance/builder.tar'), receipt.builders.source, values.arch);
const clientLayers = await imageIdentity(await regular(source, 'client-provenance/builder.tar'), receipt.builders.client, values.arch);
const installedLayers = await imageIdentity(await regular(source, 'installed-client.tar'), receipt.installed_client, values.arch);
assert(sourceLayers.length > 0 && same(installedLayers.slice(0, clientLayers.length), clientLayers), 'Installed client ancestry mismatch');
console.log(JSON.stringify({ schema: 'aelira-xdg-data-only-intake-v1', status: 'pass', architecture: values.arch, revision: values.revision, tree: values.tree, files: names.length, saved_images: 3 }));
