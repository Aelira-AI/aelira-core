import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

const original = resolve(process.argv[2] ?? ''), output = resolve(process.argv[3] ?? '');
if (process.argv.length !== 4 || original === output) throw new Error('Original synthetic signing scratch and new private scratch required');
const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex');
const planBytes = await readFile(new URL('./upn-baseline-plan-v1.json', import.meta.url));
const plan = JSON.parse(planBytes.toString('utf8'));
if (plan.principals.length !== 11 || new Set(plan.principals).size !== 11) throw new Error('Distinct frozen principals required');
const lexicalBytes = await readFile(new URL('./lexical-fixtures/manifest.json', import.meta.url));
if (sha(lexicalBytes) !== plan.lexical_manifest_sha256) throw new Error('Frozen lexical input refused');
const lexical = JSON.parse(lexicalBytes.toString('utf8'));
if (JSON.stringify([...new Set(lexical.cases.slice(2).map((c: any) => c.principal))]) !== JSON.stringify(plan.principals)) throw new Error('Exact lexical principal coverage required');
const oldBytes = await readFile(join(original, 'manifest.json'));
if (sha(oldBytes) !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19') throw new Error('Original manifest refused');
const old = JSON.parse(oldBytes.toString('utf8'));
await mkdir(output, { mode: 0o700 });
await writeFile(join(output, 'plan.json'), planBytes, { flag: 'wx', mode: 0o600 });
async function openssl(...args: string[]) {
  const child = Bun.spawn(['openssl', ...args], { cwd: output, stdout: 'pipe', stderr: 'pipe' });
  const [_, __, exit] = await Promise.all([new Response(child.stdout).arrayBuffer(), new Response(child.stderr).arrayBuffer(), child.exited]);
  if (exit !== 0) throw new Error('Synthetic certificate creation failed');
}
function der(tag: number, value: Buffer): Buffer {
  const length = value.length < 128 ? Buffer.from([value.length]) : value.length < 256 ? Buffer.from([129, value.length]) : Buffer.from([130, value.length >>> 8, value.length & 255]);
  return Buffer.concat([Buffer.from([tag]), length, value]);
}
const other = (s: string) => der(160, Buffer.concat([der(6, Buffer.from('2b060104018237140203', 'hex')), der(160, der(12, Buffer.from(s, 'utf8')))]));
const san = (s: string) => der(48, Buffer.concat([other(s), der(130, Buffer.from('localhost'))]));
const inspected = JSON.parse(await readFile(join(original, 'der-inspection.json'), 'utf8'));
if (san('allowed@corp.example').toString('hex') !== inspected.certificates.unconstrained_allowed.extensions.find((e: any) => e.oid_hex === '551d11').der_hex) throw new Error('Positive DER encoding control failed');
const issuers: Record<string, string> = { root: 'root', plain: 'root', unconstrained_allowed: 'plain', unconstrained_blocked: 'plain' };
const cases = old.cases.slice(0, 2);
for (const [i, principal] of plan.principals.entries()) {
  const name = 'c' + String(i).padStart(2, '0');
  await openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', name + '.key');
  await openssl('req', '-new', '-utf8', '-key', name + '.key', '-subj', '/CN=localhost', '-out', name + '.csr');
  await writeFile(join(output, name + '.cnf'), 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n2.5.29.17=DER:' + san(principal).toString('hex') + '\n', { flag: 'wx', mode: 0o600 });
  await openssl('x509', '-req', '-sha256', '-in', name + '.csr', '-CA', join(original, 'plain.pem'), '-CAkey', join(original, 'plain.key'), '-set_serial', String(2000 + i), '-days', '2', '-extfile', name + '.cnf', '-out', name + '.pem');
  issuers[name] = 'plain';
  cases.push({ id: 'C' + i, name, issuer: 'plain', principal, original_expected: null, lexical_cases: lexical.cases.filter((c: any) => c.principal === principal).map((c: any) => c.id), intended_path: [name, 'plain', 'root'] });
}
const certificates: Record<string, any> = {}, decoded: Record<string, X509Certificate> = {};
for (const [name, issuer] of Object.entries(issuers)) {
  const bytes = name in old.certificates ? await readFile(join(original, old.certificates[name].file)) : new X509Certificate(await readFile(join(output, name + '.pem'))).raw;
  if (name in old.certificates && sha(bytes) !== old.certificates[name].sha256) throw new Error('Original DER changed');
  const cert = decoded[name] = new X509Certificate(bytes);
  await writeFile(join(output, name + '.der'), bytes, { flag: 'wx', mode: 0o644 });
  certificates[name] = { file: name + '.der', issuer, sha256: sha(bytes), size: bytes.length, not_before: new Date(cert.validFrom).toISOString(), not_after: new Date(cert.validTo).toISOString() };
}
const time = Date.parse(lexical.fixed_validation_time);
for (const [name, cert] of Object.entries(decoded)) if (!cert.verify(decoded[certificates[name].issuer].publicKey) || !(Date.parse(cert.validFrom) <= time && time < Date.parse(cert.validTo))) throw new Error('Signature or shared lexical validity refused');
const manifest = { schema: 'aelira-upn-unconstrained-vectors-v1', plan_sha256: sha(planBytes), generator_sha256: sha(await readFile(import.meta.path)), original_manifest_sha256: sha(oldBytes), lexical_manifest_sha256: sha(lexicalBytes), fixed_validation_time: lexical.fixed_validation_time, certificates, cases, scope: plan.scope, deferred: plan.deferred };
await writeFile(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o644 });
console.log(JSON.stringify({ schema: manifest.schema, status: 'prepared', cases: cases.length, certificates: Object.keys(certificates).length, plan_sha256: manifest.plan_sha256, manifest_sha256: sha(await readFile(join(output, 'manifest.json'))), fixed_validation_time: manifest.fixed_validation_time }));
