import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { verifyInputs } from './verify-inputs.ts';

let phase = 'platform';
const partial: any = { cases: [] };
async function observe() {
if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Native Windows x64 required');
const { dlopen, toArrayBuffer, ptr } = await import('bun:ffi');
const root = resolve(process.argv[2] ?? ''), output = process.argv[3];
if (!process.argv[2] || !output) throw new Error('Fixture directory and new observation file required');
phase = 'fixtures';
partial.architecture = process.arch;
partial.bun_version = Bun.version;
partial.observer_sha256 = createHash('sha256').update(await readFile(import.meta.path)).digest('hex');
const inputs = await verifyInputs(root);
const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
const manifestBytes = await readFile(join(root, 'manifest.json'));
phase = 'fixtures';
partial.manifest_sha256 = createHash('sha256').update(manifestBytes).digest('hex');
const manifest = JSON.parse(manifestBytes.toString());
const lexical = manifest.schema === 'aelira-upn-lexical-vectors-v1';
const baselines = manifest.schema === 'aelira-upn-unconstrained-vectors-v1';
if (manifest.schema !== (baselines ? 'aelira-upn-unconstrained-vectors-v1' : lexical ? 'aelira-upn-lexical-vectors-v1' : 'aelira-upn-synthetic-sentinels-v1') ||
  manifest.cases.length !== (baselines ? 13 : lexical ? 34 : 8) || inputs.cases !== manifest.cases.length) throw new Error('Invalid experiment contract');
const system = process.env.SystemRoot;
if (!system || !/^[A-Za-z]:\\Windows$/i.test(system)) throw new Error('Expected Windows system directory');
const cryptoPath = join(system, 'System32', 'crypt32.dll');
phase = 'native-libraries';
// Win64 HANDLE values are opaque uint64 values, rather than virtual addresses.
const crypt = dlopen(cryptoPath, {
  CertOpenStore: { args: ['ptr', 'u32', 'u64', 'u32', 'ptr'], returns: 'u64' },
  CertCloseStore: { args: ['u64', 'u32'], returns: 'i32' },
  CertCreateCertificateContext: { args: ['u32', 'ptr', 'u32'], returns: 'ptr' },
  CertFreeCertificateContext: { args: ['ptr'], returns: 'i32' },
  CertAddCertificateContextToStore: { args: ['u64', 'ptr', 'u32', 'ptr'], returns: 'i32' },
  CertCreateCertificateChainEngine: { args: ['ptr', 'ptr'], returns: 'i32' },
  CertFreeCertificateChainEngine: { args: ['u64'], returns: 'void' },
  CertGetCertificateChain: { args: ['u64', 'ptr', 'ptr', 'u64', 'ptr', 'u32', 'ptr', 'ptr'], returns: 'i32' },
  CertFreeCertificateChain: { args: ['ptr'], returns: 'void' },
  CertVerifyCertificateChainPolicy: { args: ['u64', 'ptr', 'ptr', 'ptr'], returns: 'i32' },
}).symbols;
const kernel = dlopen(join(system, 'System32', 'kernel32.dll'), {
  GetLastError: { args: [], returns: 'u32' }, GetUserDefaultLCID: { args: [], returns: 'u32' }, GetSystemDefaultLCID: { args: [], returns: 'u32' },
}).symbols;
const ntdll = dlopen(join(system, 'System32', 'ntdll.dll'), { RtlGetVersion: { args: ['ptr'], returns: 'i32' } }).symbols;
const versionAPI = dlopen(join(system, 'System32', 'version.dll'), {
  GetFileVersionInfoSizeW: { args: ['ptr', 'ptr'], returns: 'u32' },
  GetFileVersionInfoW: { args: ['ptr', 'u32', 'u32', 'ptr'], returns: 'i32' },
  VerQueryValueW: { args: ['ptr', 'ptr', 'ptr', 'ptr'], returns: 'i32' },
}).symbols;
function checked(ok: unknown, call: string, suppliesLastError = true) {
  if (!ok) { const error: any = new Error('Native API failure'); error.name = 'NativeAPIError'; error.native_code = suppliesLastError ? kernel.GetLastError() : null; partial.native_call = call; throw error; }
}
const aligned = (size: number) => new BigUint64Array(Math.ceil(size / 8));
const data = (array: BigUint64Array) => new DataView(array.buffer);
function memory(pointer: number, size: number): Buffer {
  if (!Number.isSafeInteger(pointer) || pointer <= 0 || size < 1 || size > 65_536) throw new Error('Native memory bound');
  return Buffer.from(toArrayBuffer(pointer, 0, size));
}
const pointer = (bytes: Buffer, offset: number) => {
  const value = bytes.readBigUInt64LE(offset);
  if (value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Pointer precision');
  return Number(value);
};
const flags = 0x00000004 | 0x00000100 | 0x00002000;
const fileTime = aligned(8);
fileTime[0] = BigInt(Date.parse(manifest.fixed_validation_time)) * 10_000n + 116_444_736_000_000_000n;
const provider = Buffer.from('Memory\0');
const usageOID = Buffer.from('1.3.6.1.5.5.7.3.1\0');
const usagePointers = aligned(8); usagePointers[0] = BigInt(ptr(usageOID));
const contexts = new Map<string, number>();
const rows: any[] = partial.cases;
try {
  phase = 'certificate-import';
  for (const [name, identity] of Object.entries(manifest.certificates) as [string, any][]) {
    if (!/^[a-z0-9_]+$/.test(name) || identity.file !== name + '.der') throw new Error('Unsafe certificate name');
    const bytes = await readFile(join(root, identity.file));
    if (bytes.length > 65_536 || bytes.length !== identity.size || sha(bytes) !== identity.sha256) throw new Error('Changed certificate');
    const context = crypt.CertCreateCertificateContext(1, bytes, bytes.length);
    checked(context, 'CertCreateCertificateContext');
    contexts.set(name, context);
  }
  for (const row of manifest.cases) {
    phase = row.id;
    let anchor = 0n, issuers = 0n;
    const engineOutput = aligned(8); let chain = 0;
    try {
      anchor = crypt.CertOpenStore(provider, 0, 0n, 0x2000, null);
      checked(anchor, 'CertOpenStore-anchor');
      issuers = crypt.CertOpenStore(provider, 0, 0n, 0x2000, null);
      checked(issuers, 'CertOpenStore-issuers');
      checked(crypt.CertAddCertificateContextToStore(anchor, contexts.get('root'), 1, null), 'CertAddCertificateContextToStore-anchor');
      checked(crypt.CertAddCertificateContextToStore(issuers, contexts.get(row.issuer), 1, null), 'CertAddCertificateContextToStore-issuer');
      const additionalStores = aligned(8); additionalStores[0] = issuers;
      const config = aligned(88), configView = data(config);
      // The SDK makes exclusive and restricted stores mutually exclusive.
      configView.setUint32(0, 88, true); configView.setUint32(32, 1, true);
      configView.setBigUint64(40, BigInt(ptr(additionalStores)), true);
      configView.setUint32(48, 4 | 0x2000, true); configView.setBigUint64(64, anchor, true);
      checked(crypt.CertCreateCertificateChainEngine(config, engineOutput), 'CertCreateCertificateChainEngine');
      if (additionalStores[0] !== issuers) throw new Error('Additional issuer store changed');
      const parameters = aligned(96); data(parameters).setUint32(0, 96, true);
      data(parameters).setUint32(16, 1, true); data(parameters).setBigUint64(24, BigInt(ptr(usagePointers)), true);
      const chainOutput = aligned(8);
      const built = crypt.CertGetCertificateChain(engineOutput[0], contexts.get(row.name), fileTime, issuers, parameters, flags, null, chainOutput);
      const buildError = built ? null : kernel.GetLastError();
      if (built) chain = Number(chainOutput[0]);
      const result: any = { id: row.id, name: row.name, build_bool: built, build_last_error: buildError,
        intended_path: row.intended_path.map((n: string) => manifest.certificates[n].sha256), simple_chains: [] };
      rows.push(result);
      if (usagePointers[0] !== BigInt(ptr(usageOID))) throw new Error('Requested usage pointer changed');
      if (built) {
        const header = memory(chain, 72);
        if (header.readUInt32LE(0) < 72 || header.readUInt32LE(12) < 1 || header.readUInt32LE(12) > 8) throw new Error('Chain ABI or count mismatch');
        result.error_status = header.readUInt32LE(4); result.info_status = header.readUInt32LE(8);
        const chains = memory(pointer(header, 16), header.readUInt32LE(12) * 8);
        for (let i = 0; i < header.readUInt32LE(12); i++) {
          const simple = memory(pointer(chains, i * 8), 40), count = simple.readUInt32LE(12);
          if (simple.readUInt32LE(0) < 40 || count < 1 || count > 8) throw new Error('Simple chain ABI or count mismatch');
          const elements = memory(pointer(simple, 16), count * 8), observations = [];
          for (let j = 0; j < count; j++) {
            const element = memory(pointer(elements, j * 8), 56);
            if (element.readUInt32LE(0) < 56) throw new Error('Element ABI mismatch');
            const certificate = memory(pointer(element, 8), 40), length = certificate.readUInt32LE(16);
            observations.push({ sha256: sha(memory(pointer(certificate, 8), length)), error_status: element.readUInt32LE(16), info_status: element.readUInt32LE(20) });
          }
          result.simple_chains.push({ error_status: simple.readUInt32LE(4), info_status: simple.readUInt32LE(8), elements: observations });
        }
        const policy = aligned(16), status = aligned(24); data(policy).setUint32(0, 16, true); data(status).setUint32(0, 24, true);
        const evaluated = crypt.CertVerifyCertificateChainPolicy(1n, chain, policy, status);
        result.policy = { bool: evaluated, last_error: evaluated ? null : kernel.GetLastError(), error: data(status).getUint32(4, true), chain_index: data(status).getInt32(8, true), element_index: data(status).getInt32(12, true) };
        result.path_matches = result.simple_chains.length === 1 && JSON.stringify(result.simple_chains[0].elements.map((e: any) => e.sha256)) === JSON.stringify(result.intended_path);
        result.clean = result.path_matches && result.error_status === 0 && result.simple_chains.every((c: any) => c.error_status === 0 && c.elements.every((e: any) => e.error_status === 0)) && evaluated !== 0 && result.policy.error === 0;
      }
      if ((baselines || rows.length <= 2) && result.clean !== true) break;
    } finally {
      if (chain) crypt.CertFreeCertificateChain(chain);
      if (engineOutput[0]) crypt.CertFreeCertificateChainEngine(engineOutput[0]);
      if (issuers) crypt.CertCloseStore(issuers, 0);
      if (anchor) crypt.CertCloseStore(anchor, 0);
    }
  }
} finally { for (const context of contexts.values()) crypt.CertFreeCertificateContext(context); }
phase = 'metadata';
const version = aligned(284); data(version).setUint32(0, 284, true);
if (ntdll.RtlGetVersion(version) !== 0) throw new Error('Native OS version unavailable');
const widePath = Uint16Array.from((cryptoPath + '\0').split('').map(c => c.charCodeAt(0)));
const versionSize = versionAPI.GetFileVersionInfoSizeW(widePath, null);
if (versionSize < 52 || versionSize > 1_048_576) throw new Error('DLL version resource bound');
const versionBytes = new Uint32Array(Math.ceil(versionSize / 4));
checked(versionAPI.GetFileVersionInfoW(widePath, 0, versionSize, versionBytes), 'GetFileVersionInfoW');
const versionPointer = aligned(8), versionLength = new Uint32Array(1);
checked(versionAPI.VerQueryValueW(versionBytes, new Uint16Array([92, 0]), versionPointer, versionLength), 'VerQueryValueW', false);
if (versionLength[0] < 52) throw new Error('Short DLL fixed version');
const fixedVersion = memory(Number(versionPointer[0]), 52);
if (fixedVersion.readUInt32LE(0) !== 0xfeef04bd) throw new Error('DLL version signature');
const ms = fixedVersion.readUInt32LE(8), ls = fixedVersion.readUInt32LE(12);
const status = rows.length !== manifest.cases.length || (baselines && rows.some(r => !r.clean)) ? 'baseline-failed' : rows.some(r => !r.build_bool || !r.path_matches || !r.policy?.bool) ? 'experiment-failed' : 'observed';
const result = { schema: baselines ? 'aelira-native-windows-upn-unconstrained-observation-v1' : lexical ? 'aelira-native-windows-upn-lexical-observation-v1' : 'aelira-native-windows-upn-observation-v1', status,
  manifest_sha256: sha(manifestBytes), observer_sha256: sha(await readFile(import.meta.path)), architecture: process.arch, bun_version: Bun.version,
  os_version: [4, 8, 12].map(n => data(version).getUint32(n, true)), user_lcid: kernel.GetUserDefaultLCID(), system_lcid: kernel.GetSystemDefaultLCID(),
  crypt32_sha256: sha(await readFile(cryptoPath)), crypt32_version: [ms >>> 16, ms & 65535, ls >>> 16, ls & 65535], fixed_validation_time: manifest.fixed_validation_time,
  engine_flags: 4 | 0x2000, chain_flags: flags, policy_id: 1, policy_ignore_flags: 0, requested_usage_oid: usageOID.toString().slice(0, -1), exclusive_synthetic_anchor: true, restricted_stores: false, additional_issuer_store: true, cases: rows,
  scope: 'Empirical native Windows observations; no universal UPN matcher or GnuTLS repair acceptance' };
await writeFile(resolve(output), JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ schema: result.schema, status: result.status, cases: rows.length, os_version: result.os_version }));
if (result.status !== 'observed') process.exitCode = 1;
}
await observe().catch(async (error: any) => {
  const failure = { schema: 'aelira-native-windows-upn-observation-failure-v1', status: 'fail', phase,
    error_type: error instanceof Error ? error.name : 'Error', native_error: Number.isInteger(error?.native_code) ? error.native_code : null, ...partial };
  if (process.argv[3]) { try { await writeFile(resolve(process.argv[3]), JSON.stringify(failure, null, 2) + '\n', { flag: 'wx' }); } catch {} }
  console.log(JSON.stringify({ schema: failure.schema, status: failure.status, phase: failure.phase, error_type: failure.error_type, native_error: failure.native_error }));
  process.exitCode = 1;
});
