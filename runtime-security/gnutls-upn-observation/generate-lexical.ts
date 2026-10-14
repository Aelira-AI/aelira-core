import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

const original = resolve(process.argv[2] ?? ''), output = resolve(process.argv[3] ?? '');
if (process.argv.length !== 4 || original === output) throw new Error('Original synthetic signing scratch and new private scratch required');
const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex');
const planBytes = await readFile(new URL('./upn-lexical-plan-v1.json', import.meta.url));
const plan = JSON.parse(planBytes.toString('utf8'));
const vectors: any[] = [];
for (const role of plan.roles) {
  for (const principal of plan.case_principals) vectors.push({ role, constraint: plan.case_constraint, principal, comparison_class: 'case' });
  for (const [constraint, principal] of plan.unicode_pairs) vectors.push({ role, constraint, principal, comparison_class: 'unicode' });
  for (const constraint of plan.namespace_constraints) for (const principal of plan.namespace_principals) vectors.push({ role, constraint, principal, comparison_class: 'namespace' });
}
if (vectors.length !== 32) throw new Error('Frozen vector count refused');
const old = JSON.parse(await readFile(join(original, 'manifest.json'), 'utf8'));
if (sha(await readFile(join(original, 'manifest.json'))) !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19') throw new Error('Original manifest refused');
await mkdir(output, { mode: 0o700 });
await writeFile(join(output, 'plan.json'), planBytes, { flag: 'wx', mode: 0o600 });
async function openssl(...args: string[]) {
  const child = Bun.spawn(['openssl', ...args], { cwd: output, stdout: 'pipe', stderr: 'pipe' });
  const [_, __, exit] = await Promise.all([new Response(child.stdout).arrayBuffer(), new Response(child.stderr).arrayBuffer(), child.exited]);
  if (exit !== 0) throw new Error('Synthetic certificate creation failed');
}
let serial = 1000;
async function issue(name: string, issuer: string, extension: string, ca: boolean) {
  await openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', name + '.key');
  await openssl('req', '-new', '-utf8', '-key', name + '.key', '-subj', ca ? '/O=Good/CN=Synthetic UPN ' + name : '/CN=localhost', '-out', name + '.csr');
  const defaults = ca ? 'basicConstraints=critical,CA:TRUE,pathlen:3\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n'
    : 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n';
  await writeFile(join(output, name + '.cnf'), defaults + extension + '\n', { flag: 'wx', mode: 0o600 });
  const issuerPath = issuer === 'root' ? join(original, issuer) : join(output, issuer);
  await openssl('x509', '-req', '-sha256', '-in', name + '.csr', '-CA', issuerPath + '.pem', '-CAkey', issuerPath + '.key',
    '-set_serial', String(serial++), '-days', '2', '-extfile', name + '.cnf', '-out', name + '.pem');
}
function der(tag: number, value: Buffer): Buffer {
  const length = value.length < 128 ? Buffer.from([value.length]) : value.length < 256 ? Buffer.from([129, value.length]) : Buffer.from([130, value.length >>> 8, value.length & 255]);
  return Buffer.concat([Buffer.from([tag]), length, value]);
}
const other = (value: string) => der(160, Buffer.concat([der(6, Buffer.from('2b060104018237140203', 'hex')), der(160, der(12, Buffer.from(value, 'utf8')))]));
const constraintDER = (role: string, value: string) => der(48, der(role === 'permitted' ? 160 : 161, der(48, other(value))));
const sanDER = (value: string) => der(48, Buffer.concat([other(value), der(130, Buffer.from('localhost'))]));
const accepted = JSON.parse(await readFile(join(original, 'der-inspection.json'), 'utf8'));
if (constraintDER('excluded', 'blocked@corp.example').toString('hex') !== accepted.certificates.upn_excluded.extensions.find((e: any) => e.oid_hex === '551d1e').der_hex ||
    sanDER('allowed@corp.example').toString('hex') !== accepted.certificates.upn_excluded_good.extensions.find((e: any) => e.oid_hex === '551d11').der_hex) throw new Error('Positive DER encoding control failed');
const issuers: Record<string, string> = { root: 'root', plain: 'root', unconstrained_allowed: 'plain', unconstrained_blocked: 'plain' };
const cases = old.cases.slice(0, 2);
for (let i = 0; i < vectors.length; i++) {
  const v = vectors[i], name = 'b' + String(i).padStart(2, '0'), issuer = name + '_issuer';
  await issue(issuer, 'root', '2.5.29.30=critical,DER:' + constraintDER(v.role, v.constraint).toString('hex'), true);
  await issue(name, issuer, '2.5.29.17=DER:' + sanDER(v.principal).toString('hex'), false);
  issuers[issuer] = 'root'; issuers[name] = issuer;
  cases.push({ id: 'B' + i, name, issuer, ...v, original_expected: null, intended_path: [name, issuer, 'root'] });
}
const certificates: Record<string, any> = {}, decoded: Record<string, X509Certificate> = {};
for (const [name, issuer] of Object.entries(issuers)) {
  const isOriginal = name in old.certificates;
  const bytes = isOriginal ? await readFile(join(original, old.certificates[name].file)) : new X509Certificate(await readFile(join(output, name + '.pem'))).raw;
  if (isOriginal && sha(bytes) !== old.certificates[name].sha256) throw new Error('Original DER changed');
  const certificate = decoded[name] = new X509Certificate(bytes);
  await writeFile(join(output, name + '.der'), bytes, { flag: 'wx', mode: 0o644 });
  certificates[name] = { file: name + '.der', issuer, sha256: sha(bytes), size: bytes.length,
    not_before: new Date(certificate.validFrom).toISOString(), not_after: new Date(certificate.validTo).toISOString() };
}
const time = Math.max(...Object.values(certificates).map((c: any) => Date.parse(c.not_before))) + 3_600_000;
for (const [name, certificate] of Object.entries(decoded)) {
  if (!certificate.verify(decoded[certificates[name].issuer].publicKey) || !(Date.parse(certificate.validFrom) <= time && time < Date.parse(certificate.validTo))) throw new Error('Signature or common validity refused');
}
const manifest = { schema: 'aelira-upn-lexical-vectors-v1', plan_sha256: sha(planBytes), generator_sha256: sha(await readFile(import.meta.path)),
  original_manifest_sha256: '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19', fixed_validation_time: new Date(time).toISOString(), certificates, cases,
  scope: plan.scope, deferred: plan.deferred };
await writeFile(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o644 });
console.log(JSON.stringify({ schema: manifest.schema, status: 'prepared', cases: cases.length, certificates: Object.keys(certificates).length,
  plan_sha256: manifest.plan_sha256, manifest_sha256: sha(await readFile(join(output, 'manifest.json'))), fixed_validation_time: manifest.fixed_validation_time }));
