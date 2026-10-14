import { copyFile, mkdir, readFile, readdir, rename, symlink, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { verifyInputs } from './verify-inputs.ts';
const source = resolve(process.argv[2]), scratch = resolve(process.argv[3]);
if (process.argv.length !== 4 || scratch === source || scratch.startsWith(source + '/')) throw new Error('Separate new scratch required');
await mkdir(scratch);
const positive = await verifyInputs(source);
const files = await readdir(source);
const controls: [string, (root: string) => Promise<unknown>][] = [
  ['changed-manifest', async root => writeFile(join(root, 'manifest.json'), '{}\n')],
  ['changed-certificate', async root => { const path = join(root, 'root.der'), bytes = await readFile(path); bytes[bytes.length - 1] ^= 1; await writeFile(path, bytes); }],
  ['changed-inspection', async root => writeFile(join(root, 'der-inspection.json'), '{}\n')],
  ['missing-certificate', async root => rename(join(root, 'root.der'), join(scratch, 'retained-root.der'))],
  ['extra-file', async root => writeFile(join(root, 'extra.json'), '{}\n')],
  ['symlink-certificate', async root => { await rename(join(root, 'root.der'), join(scratch, 'retained-symlink-root.der')); await symlink(join(source, 'root.der'), join(root, 'root.der')); }],
];
const rows = [];
for (const [name, mutate] of controls) {
  const root = join(scratch, name); await mkdir(root);
  for (const file of files) await copyFile(join(source, file), join(root, file));
  await mutate(root);
  let refused = false;
  try { await verifyInputs(root); } catch { refused = true; }
  if (!refused) throw new Error('Frozen-input refusal did not fire');
  rows.push({ name, refused });
}
console.log(JSON.stringify({ schema: 'aelira-upn-frozen-input-controls-v1', positive, controls: rows }));
