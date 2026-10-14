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
  const baselines = digest === '8fb7659eeedc63059fbd7a78f3804d0de5b73495b8167ab19bf8d73c29cf3e67';
  const paths = digest === '77c08090f8033f8469a890c9ce15fbbc41a450362676f1da6e9148354f427df8';
  const at = digest === 'c785c5cf1ffba0726ce968adc8cf8e929e7acfd4ec4c3b9289835687011c4559';
  if (!lexical && !baselines && !paths && !at && digest !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19') throw new Error('Frozen manifest changed');
  const manifest = JSON.parse(raw.toString());
  const names = Object.keys(manifest.certificates);
  const healthIds = ['A0','A1',...Array.from({length:11},(_,i)=>'C'+i)];
  const ids = at ? [...healthIds,...Array.from({length:8},(_,i)=>'Q'+i)] : paths ? [...healthIds,...Array.from({length:5},(_,i)=>'P'+i)] : baselines ? healthIds : lexical ? ['A0','A1',...Array.from({length:32},(_,i)=>'B'+i)] : ['A0','A1','A2','A3','A4','A5','A6','A7'];
  if (names.length !== (at ? 25 : paths ? 26 : baselines ? 15 : lexical ? 68 : 13) || manifest.cases.map((c: any) => c.id).join(',') !== ids.join(',')) throw new Error('Frozen experiment contract');
  const files = ['manifest.json', 'der-inspection.json', ...(lexical || baselines || paths || at ? ['plan.json'] : []), ...names.map(n => manifest.certificates[n].file)].sort();
  if (JSON.stringify((await readdir(root)).sort()) !== JSON.stringify(files)) throw new Error('Exact fixture closure required');
  const inspection = await regular(join(root, 'der-inspection.json'), lexical ? 100_000 : 65_536);
  if (sha(inspection) !== (at ? '0ae917df4f6b3a0f881884bab3db79c26ba82c008d604006ce4c0c2e3f8ccb87' : paths ? 'ad1ed61100870d6d80402cec9baee7c8f02d71ae8efb7beef2740aba0308a143' : baselines ? '32d61c573daeec3824947b3dc17e2923b2d202a000797878c671c4925713e724' : lexical ? '5756730bb1c6947b03ed68c536a0b06d863e2c82bf86338653f44ae1f51a6500' : '4e732c656b44f72b38d554fc61dbbbeb8c788915c7e37565cdce538a98ee61e0')) throw new Error('Frozen DER inspection changed');
  if ((lexical || baselines || paths || at) && sha(await regular(join(root, 'plan.json'), 16_384)) !== (at ? '5f28bbf11db2aca017c2553874e13df9ecf852644e2e7c3859eee6ad2a814452' : paths ? 'b8016ba9144055a4e966b2e331f12ca5d81ec362c96689c34d974e44f8003d66' : baselines ? 'f5f0c770ff9e97cedfa2687db6a7c2be3faecf293f31fd526ff20fdc9af1c542' : '89551f701e5d0bd8ce40b8a52fac5fd2d4cc12bcd928c12d709cd33ce887d30f')) throw new Error('Frozen experiment plan changed');
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
