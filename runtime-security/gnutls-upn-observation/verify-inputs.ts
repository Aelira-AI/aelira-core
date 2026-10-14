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
  const digest = sha(raw);
  const lexical = digest === 'e1d96b1c056fdf553a3629c6193018eb4d2bbe104a7e5a36178e219c7852731d';
  if (!lexical && digest !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19') throw new Error('Frozen manifest changed');
  const manifest = JSON.parse(raw.toString());
  const names = Object.keys(manifest.certificates);
  const ids = lexical ? ['A0','A1',...Array.from({length:32},(_,i)=>'B'+i)] : ['A0','A1','A2','A3','A4','A5','A6','A7'];
  if (names.length !== (lexical ? 68 : 13) || manifest.cases.map((c: any) => c.id).join(',') !== ids.join(',')) throw new Error('Frozen experiment contract');
  const files = ['manifest.json', 'der-inspection.json', ...(lexical ? ['plan.json'] : []), ...names.map(n => manifest.certificates[n].file)].sort();
  if (JSON.stringify((await readdir(root)).sort()) !== JSON.stringify(files)) throw new Error('Exact fixture closure required');
  const inspection = await regular(join(root, 'der-inspection.json'), lexical ? 100_000 : 65_536);
  if (sha(inspection) !== (lexical ? '5756730bb1c6947b03ed68c536a0b06d863e2c82bf86338653f44ae1f51a6500' : '4e732c656b44f72b38d554fc61dbbbeb8c788915c7e37565cdce538a98ee61e0')) throw new Error('Frozen DER inspection changed');
  if (lexical && sha(await regular(join(root, 'plan.json'), 16_384)) !== '89551f701e5d0bd8ce40b8a52fac5fd2d4cc12bcd928c12d709cd33ce887d30f') throw new Error('Frozen lexical plan changed');
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
