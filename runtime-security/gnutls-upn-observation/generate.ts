import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

const output = resolve(process.argv[2] ?? '');
if (!process.argv[2]) throw new Error('New fixture directory required');
await mkdir(output, { mode: 0o700 });
const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
async function openssl(...args: string[]) {
  const child = Bun.spawn(['openssl', ...args], { cwd: output, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr, exit] = await Promise.all([new Response(child.stdout).arrayBuffer(), new Response(child.stderr).arrayBuffer(), child.exited]);
  if (exit !== 0) throw new Error('Synthetic certificate creation failed');
  return Buffer.from(stdout);
}
await openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', 'root.key');
await openssl('req', '-new', '-x509', '-sha256', '-key', 'root.key', '-days', '2', '-subj', '/CN=Synthetic GnuTLS Root', '-out', 'root.pem',
  '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign');
let serial = 2;
async function issue(name: string, issuer: string, extension: string, ca = false) {
  await openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', name + '.key');
  await openssl('req', '-new', '-utf8', '-key', name + '.key', '-subj', ca ? '/O=Good/CN=Synthetic Intermediate' : '/CN=localhost', '-out', name + '.csr');
  const configuration = ca
    ? 'basicConstraints=critical,CA:TRUE,pathlen:3\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n'
    : 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n';
  await writeFile(join(output, name + '.cnf'), configuration + extension + '\n', { flag: 'wx', mode: 0o600 });
  await openssl('x509', '-req', '-sha256', '-in', name + '.csr', '-CA', issuer + '.pem', '-CAkey', issuer + '.key',
    '-set_serial', String(serial++), '-days', '2', '-extfile', name + '.cnf', '-out', name + '.pem');
}
const upn = (s: string) => 'otherName:1.3.6.1.4.1.311.20.2.3;UTF8:' + s;
await issue('plain', 'root', '', true);
await issue('upn_excluded', 'root', 'nameConstraints=critical,excluded;' + upn('blocked@corp.example'), true);
await issue('upn_permitted', 'root', 'nameConstraints=critical,permitted;' + upn('allowed@corp.example'), true);
await issue('unknown_constrained', 'root', 'nameConstraints=critical,excluded;otherName:1.2.3.4;UTF8:blocked', true);
const cases = [
  { id: 'A0', name: 'unconstrained_allowed', issuer: 'plain', san: upn('allowed@corp.example') + ',DNS:localhost', original_expected: true },
  { id: 'A1', name: 'unconstrained_blocked', issuer: 'plain', san: upn('blocked@corp.example') + ',DNS:localhost', original_expected: true },
  { id: 'A2', name: 'upn_excluded_good', issuer: 'upn_excluded', san: upn('allowed@corp.example') + ',DNS:localhost', original_expected: true },
  { id: 'A3', name: 'upn_excluded_bad', issuer: 'upn_excluded', san: upn('blocked@corp.example') + ',DNS:localhost', original_expected: false },
  { id: 'A4', name: 'upn_permitted_good', issuer: 'upn_permitted', san: upn('allowed@corp.example') + ',DNS:localhost', original_expected: true },
  { id: 'A5', name: 'upn_permitted_bad', issuer: 'upn_permitted', san: upn('blocked@corp.example') + ',DNS:localhost', original_expected: false },
  { id: 'A6', name: 'upn_absent', issuer: 'upn_excluded', san: 'DNS:localhost', original_expected: true },
  { id: 'A7', name: 'unknown_othername', issuer: 'unknown_constrained', san: 'otherName:1.2.3.4;UTF8:allowed,DNS:localhost', original_expected: null },
];
for (const row of cases) await issue(row.name, row.issuer, 'subjectAltName=' + row.san);
const certificates: Record<string, any> = {};
const decoded: Record<string, X509Certificate> = {};
for (const name of ['root', 'plain', 'upn_excluded', 'upn_permitted', 'unknown_constrained', ...cases.map(c => c.name)]) {
  const cert = decoded[name] = new X509Certificate(await readFile(join(output, name + '.pem')));
  await writeFile(join(output, name + '.der'), cert.raw, { flag: 'wx' });
  const issuer = name === 'root' ? 'root' : cases.find(c => c.name === name)?.issuer ?? 'root';
  certificates[name] = { file: name + '.der', sha256: sha(cert.raw), size: cert.raw.length, issuer,
    not_before: new Date(cert.validFrom).toISOString(), not_after: new Date(cert.validTo).toISOString() };
}
for (const [name, cert] of Object.entries(decoded)) if (!cert.verify(decoded[certificates[name].issuer].publicKey)) throw new Error('Synthetic signature mismatch');
const fixedTime = Math.max(...Object.values(certificates).map(c => Date.parse(c.not_before))) + 3_600_000;
if (!Object.values(certificates).every(c => fixedTime < Date.parse(c.not_after))) throw new Error('Invalid fixed validation time');
const manifest = { schema: 'aelira-upn-synthetic-sentinels-v1', generator_sha256: sha(await readFile(import.meta.path)),
  fixed_validation_time: new Date(fixedTime).toISOString(), certificates,
  cases: cases.map(c => ({ ...c, intended_path: [c.name, c.issuer, 'root'] })),
  scope: 'Eight empirical Windows chain-policy sentinels; original GnuTLS expectations and matcher remain separate' };
await writeFile(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ schema: manifest.schema, status: 'pass', cases: cases.length, certificates: Object.keys(certificates).length, fixed_validation_time: manifest.fixed_validation_time }));
