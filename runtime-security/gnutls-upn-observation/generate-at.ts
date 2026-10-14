import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

const original = resolve(process.argv[2] ?? ''), baselines = resolve(process.argv[3] ?? ''), output = resolve(process.argv[4] ?? '');
if (process.argv.length !== 5 || output === original || output === baselines) throw new Error('Original signing scratch, public baselines and new private scratch required');
const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex');
const planBytes = await readFile(new URL('./upn-at-plan-v1.json', import.meta.url)), plan = JSON.parse(planBytes.toString('utf8'));
const oldBytes = await readFile(join(original, 'manifest.json')), baseBytes = await readFile(join(baselines, 'manifest.json'));
if (sha(oldBytes) !== '00bee665853ba68fb12e0ed547f61933e547f43c9da2114d6f39681b55a05b19' || sha(baseBytes) !== plan.baseline_manifest_sha256 || plan.roles.join(',') !== 'permitted,excluded' || plan.principals.length !== 4 || plan.constraint !== '@corp.example') throw new Error('Frozen source plan required');
const base = JSON.parse(baseBytes.toString('utf8'));
const healthy = new Set(base.cases.map((row: any) => row.principal ?? (row.id === 'A0' ? 'allowed@corp.example' : row.id === 'A1' ? 'blocked@corp.example' : null)));
if (!plan.principals.every((principal: string) => healthy.has(principal))) throw new Error('Every measured principal needs a frozen healthy counterpart');
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
const nc = (role: string, s: string) => der(48, der(role === 'permitted' ? 160 : 161, der(48, other(s))));
const inspection = JSON.parse(await readFile(join(original, 'der-inspection.json'), 'utf8'));
if (san('allowed@corp.example').toString('hex') !== inspection.certificates.unconstrained_allowed.extensions.find((e: any) => e.oid_hex === '551d11').der_hex || nc('excluded', 'blocked@corp.example').toString('hex') !== inspection.certificates.upn_excluded.extensions.find((e: any) => e.oid_hex === '551d1e').der_hex) throw new Error('Positive DER encoding control failed');
let serial = 4000;
async function issue(name: string, issuer: string, extension: string, ca: boolean) {
  if (!/^[a-z0-9_]+$/.test(name) || !/^[a-z0-9_]+$/.test(issuer)) throw new Error('Synthetic name refused');
  await openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', name + '.key');
  await openssl('req', '-new', '-utf8', '-key', name + '.key', '-subj', ca ? '/O=Good/CN=Synthetic UPN ' + name : '/CN=localhost', '-out', name + '.csr');
  const defaults = ca ? 'basicConstraints=critical,CA:TRUE,pathlen:3\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n' : 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n';
  await writeFile(join(output, name + '.cnf'), defaults + extension + '\n', { flag: 'wx', mode: 0o600 });
  const issuerPath = join(issuer === 'root' ? original : output, issuer);
  await openssl('x509', '-req', '-sha256', '-in', name + '.csr', '-CA', issuerPath + '.pem', '-CAkey', issuerPath + '.key', '-set_serial', String(serial++), '-days', '2', '-extfile', name + '.cnf', '-out', name + '.pem');
}
const added: Record<string, string> = {}, cases = structuredClone(base.cases);
let index = 0;
for (const role of plan.roles) {
  const issuer = 'at_' + role;
  await issue(issuer, 'root', '2.5.29.30=critical,DER:' + nc(role, plan.constraint).toString('hex'), true); added[issuer] = 'root';
  for (const principal of plan.principals) {
    const id = 'Q' + index, name = 'q' + String(index++).padStart(2, '0');
    await issue(name, issuer, '2.5.29.17=DER:' + san(principal).toString('hex'), false); added[name] = issuer;
    cases.push({ id, name, issuer, role, constraint: plan.constraint, principal, original_expected: null, intended_path: [name, issuer, 'root'] });
  }
}
if (index !== 8) throw new Error('Frozen measurement count refused');
const certificates: Record<string, any> = {}, decoded: Record<string, X509Certificate> = {};
for (const [name, identity] of Object.entries(base.certificates) as [string, any][]) {
  const bytes = await readFile(join(baselines, identity.file));
  if (sha(bytes) !== identity.sha256 || bytes.length !== identity.size) throw new Error('Frozen baseline DER changed');
  await writeFile(join(output, identity.file), bytes, { flag: 'wx', mode: 0o644 }); decoded[name] = new X509Certificate(bytes); certificates[name] = identity;
}
for (const [name, issuer] of Object.entries(added)) {
  const cert = decoded[name] = new X509Certificate(await readFile(join(output, name + '.pem'))), bytes = cert.raw;
  await writeFile(join(output, name + '.der'), bytes, { flag: 'wx', mode: 0o644 });
  certificates[name] = { file: name + '.der', issuer, sha256: sha(bytes), size: bytes.length, not_before: new Date(cert.validFrom).toISOString(), not_after: new Date(cert.validTo).toISOString() };
}
const time = Math.max(...Object.values(certificates).map((c: any) => Date.parse(c.not_before))) + 3_600_000;
for (const [name, cert] of Object.entries(decoded)) if (!cert.verify(decoded[certificates[name].issuer].publicKey) || !(Date.parse(cert.validFrom) <= time && time < Date.parse(cert.validTo))) throw new Error('Signature or common validity refused');
const manifest = { schema: 'aelira-upn-at-vectors-v1', plan_sha256: sha(planBytes), generator_sha256: sha(await readFile(import.meta.path)), baseline_manifest_sha256: sha(baseBytes), fixed_validation_time: new Date(time).toISOString(), health_rows: 13, certificates, cases, scope: plan.scope, deferred: plan.deferred };
await writeFile(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o644 });
console.log(JSON.stringify({ schema: manifest.schema, status: 'prepared', cases: cases.length, certificates: Object.keys(certificates).length, manifest_sha256: sha(await readFile(join(output, 'manifest.json'))), fixed_validation_time: manifest.fixed_validation_time }));
