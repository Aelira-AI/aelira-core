import { lstat, readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

export const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
async function regular(path: string, limit: number) {
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size < 1 || stat.size > limit) throw new Error('Regular bounded input required');
  const bytes = await readFile(path);
  if (bytes.length !== stat.size) throw new Error('Changed input size');
  return bytes;
}
export async function verifyInputs(root: string) {
  const directory = await lstat(root);
  if (!directory.isDirectory() || directory.isSymbolicLink()) throw new Error('Fixture directory required');
  const raw = await regular(join(root, 'manifest.json'), 65_536);
  if (sha(raw) !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19') throw new Error('Frozen manifest changed');
  const manifest = JSON.parse(raw.toString());
  const names = Object.keys(manifest.certificates);
  if (names.length !== 13 || manifest.cases.map((c: any) => c.id).join(',') !== 'A0,A1,A2,A3,A4,A5,A6,A7') throw new Error('Frozen sentinel contract');
  const files = ['manifest.json', 'der-inspection.json', ...names.map(n => manifest.certificates[n].file)].sort();
  if (JSON.stringify((await readdir(root)).sort()) !== JSON.stringify(files)) throw new Error('Exact fixture closure required');
  const inspection = await regular(join(root, 'der-inspection.json'), 65_536);
  if (sha(inspection) !== '4e732c656b44f72b38d554fc61dbbbeb8c788915c7e37565cdce538a98ee61e0') throw new Error('Frozen DER inspection changed');
  const certificates = new Map<string, X509Certificate>();
  for (const name of names) {
    const expected = manifest.certificates[name];
    if (!/^[a-z0-9_]+$/.test(name) || expected.file !== name + '.der') throw new Error('Certificate name refused');
    const bytes = await regular(join(root, expected.file), 65_536);
    if (bytes.length !== expected.size || sha(bytes) !== expected.sha256) throw new Error('Frozen certificate changed');
    certificates.set(name, new X509Certificate(bytes));
  }
  const time = Date.parse(manifest.fixed_validation_time);
  for (const [name, certificate] of certificates) {
    const issuer = certificates.get(manifest.certificates[name].issuer)!;
    if (!certificate.verify(issuer.publicKey) || !(Date.parse(certificate.validFrom) <= time && time <= Date.parse(certificate.validTo))) throw new Error('Signature or fixed-time validity refused');
  }
  return { schema: 'aelira-upn-frozen-input-check-v1', status: 'pass', manifest_sha256: sha(raw),
    certificates: names.length, cases: manifest.cases.length, files: files.length, fixed_validation_time: manifest.fixed_validation_time };
}
if (import.meta.main) {
  if (process.argv.length !== 3) throw new Error('Fixture directory required');
  console.log(JSON.stringify(await verifyInputs(resolve(process.argv[2]))));
}
