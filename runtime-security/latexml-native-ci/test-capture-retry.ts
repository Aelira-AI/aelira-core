import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { attemptRefusalClass, cacheFallbackEligible, cachedInputArgs, cachedInputLocks, captureRefused,
  fallbackClasses, parseCaptureProcess, refusalClasses } from './capture-retry.ts';

const bytes = await readFile(new URL('./fixtures/capture-refusal.json', import.meta.url));
if (createHash('sha256').update(bytes).digest('hex') !== '67096ed230fca4689edad03c649045c5a94e83a821095c26afe5f98e8f9a4f64') throw new Error('Frozen public receipt changed');
const original = JSON.parse(bytes.toString('utf8'));
const id = { revision: original.operator_ref, tree: original.operator_tree,
  architecture: original.architecture, remote: original.remote_ref,
  runner_sha256: original.selected_sources['runtime-security/latexml-native-ci/run.py'].sha256 };
if (!captureRefused(original, 2, id)) throw new Error('Actual capture refusal was not recognized');
const controls: string[] = [];
function refuse(name: string, mutate: (row: any) => void, exit = 2) {
  const row = structuredClone(original); mutate(row);
  if (captureRefused(row, exit, id)) throw new Error('Retry boundary failed');
  controls.push(name);
}
refuse('success-exit', () => {}, 0);
refuse('signal-or-launch-exit', () => {}, 1);
refuse('unavailable-capture', r => { r.stages['input-capture'] = { state: 'unavailable', exit: null }; });
refuse('wrong-checkout', r => { r.operator_ref = '0'.repeat(40); });
refuse('wrong-tree', r => { r.operator_tree = '0'.repeat(40); });
refuse('wrong-architecture', r => { r.architecture = 'arm64'; });
refuse('wrong-remote', r => { r.remote_ref = 'refs/heads/other'; });
refuse('synthetic-source', r => { r.source_kind = 'synthetic'; });
refuse('changed-runner', r => { r.selected_sources['runtime-security/latexml-native-ci/run.py'].sha256 = '0'.repeat(64); });
refuse('source-check-failed', r => { r.stages['source-check'].exit = 2; });
refuse('native-check-failed', r => { r.stages['native-check'].exit = 2; });
refuse('capture-lock-present', r => { r.captured_lock_sha256 = '0'.repeat(64); });
refuse('producer-present', r => { r.producer_status_sha256 = '0'.repeat(64); });
refuse('producer-diagnostic-present', r => { r.failure_diagnostic = { state: 'valid', record: {} }; });
for (const stage of ['input-pin', 'prepare', 'bootstrap', 'native-run', 'caller-export', 'evidence-export', 'evidence-check']) {
  refuse(stage + '-started', r => { r.stages[stage] = { state: 'executed', exit: 2 }; });
}
refuse('extra-stage', r => { r.stages.unexpected = { state: 'not_run', exit: null }; });
refuse('missing-stage', r => { delete r.stages['input-pin']; });
if (captureRefused(original, 2, { ...id, runner_sha256: '0'.repeat(64) })) throw new Error('Wrong independent runner accepted');
controls.push('wrong-independent-runner');
if (captureRefused(null, 2, id) || captureRefused({}, 2, id)) throw new Error('Missing receipt accepted');
const aptBytes = await readFile(new URL('./fixtures/capture-apt-refusal.json', import.meta.url));
const processBytes = await readFile(new URL('./fixtures/capture-apt-process.json', import.meta.url));
if (createHash('sha256').update(aptBytes).digest('hex') !== '72c46950b2008f7c6078f5a5153c618a1763e4c440476f786c0cb0c3e8f94da5' ||
  createHash('sha256').update(processBytes).digest('hex') !== '5d741ddcacfdace58b03d0b81af8ec9067a655458e4f7cb15a08860744c9722f') throw new Error('Frozen native APT receipts changed');
const apt = JSON.parse(aptBytes.toString('utf8')), processRow = parseCaptureProcess(processBytes);
const aptId = { revision: apt.operator_ref, tree: apt.operator_tree, architecture: apt.architecture, remote: apt.remote_ref,
  runner_sha256: apt.selected_sources['runtime-security/latexml-native-ci/run.py'].sha256 };
if (!captureRefused(apt, 2, aptId, processRow)) throw new Error('Actual APT capture refusal was not recognized');
const download = structuredClone(processRow);
download.stages.update.exit = 0; download.stages.download = { state: 'executed', exit: 100 };
if (!captureRefused(apt, 2, aptId, download)) throw new Error('APT download-stage boundary was not recognized');
const aptControls: string[] = [];
function aptRefuse(name: string, mutate: (outer: any, process: any) => void, exit = 2) {
  const outer = structuredClone(apt), process = structuredClone(processRow); mutate(outer, process);
  if (captureRefused(outer, exit, aptId, process)) throw new Error('APT retry boundary failed');
  aptControls.push(name);
}
aptRefuse('wrapper-success', () => {}, 0);
aptRefuse('wrapper-signal-or-launch', () => {}, 1);
aptRefuse('docker-status-mismatch', (_, p) => { p.docker_exit = 2; });
aptRefuse('string-status', (_, p) => { p.stages.update.exit = '100'; });
aptRefuse('setup-failed', (_, p) => { p.stages.setup.exit = 100; });
aptRefuse('update-unavailable', (_, p) => { p.stages.update = { state: 'unavailable', exit: null }; });
aptRefuse('unexpected-update-exit', (_, p) => { p.stages.update.exit = 1; });
aptRefuse('selection-started', (_, p) => { p.stages.select = { state: 'executed', exit: 0 }; });
aptRefuse('restoration-started', (_, p) => { p.stages.restore = { state: 'executed', exit: 0 }; });
aptRefuse('wrong-process-checkout', (_, p) => { p.operator_ref = '0'.repeat(40); });
aptRefuse('wrong-process-tree', (_, p) => { p.operator_tree = '0'.repeat(40); });
aptRefuse('wrong-process-architecture', (_, p) => { p.architecture = 'amd64'; });
aptRefuse('wrong-process-remote', (_, p) => { p.remote_ref = 'refs/heads/other'; });
aptRefuse('wrong-snapshot', (_, p) => { p.snapshot_id = '20261014T060000Z'; });
aptRefuse('wrong-keyring', (_, p) => { p.keyring_sha256 = '0'.repeat(64); });
aptRefuse('wrong-ca-package', (_, p) => { p.ca_package_sha256 = '0'.repeat(64); });
aptRefuse('wrong-ca-bundle', (_, p) => { p.ca_bundle_sha256 = '0'.repeat(64); });
aptRefuse('sandbox-root', (_, p) => { p.apt_sandbox_user = 'root'; });
aptRefuse('wrong-apt-uid', (_, p) => { p.apt_uid = 0; });
aptRefuse('changed-capabilities', (_, p) => { p.capabilities.push('SYS_ADMIN'); });
aptRefuse('extra-process-stage', (_, p) => { p.stages.unexpected = { state: 'not_run', exit: null }; });
aptRefuse('extra-status-field', (_, p) => { p.stages.update.extra = true; });
aptRefuse('captured-lock-present', o => { o.captured_lock_sha256 = '0'.repeat(64); });
for (const stage of ['input-pin', 'prepare', 'bootstrap', 'native-run', 'caller-export', 'evidence-export', 'evidence-check']) {
  aptRefuse(stage + '-started', o => { o.stages[stage] = { state: 'executed', exit: 2 }; });
}
if (captureRefused(apt, 2, aptId) || captureRefused(apt, 2, aptId, {})) throw new Error('Missing APT process accepted');
const parallelOuter = structuredClone(apt), parallel = structuredClone(processRow);
parallelOuter.stages['input-capture'].exit = 123;
Object.assign(parallel, { schema: 'aelira-latexml-capture-process-v3', docker_exit: 123, container_absent: true,
  plan: { state: 'executed', exit: 0 }, archive_check: { state: 'not_run', exit: null },
  download_workers: 8, download_plan_sha256: 'a'.repeat(64), downloads: [0, 100].map((exit, i) => ({
    file: `bounded_${i}_all.deb`, size: 512, sha256: 'b'.repeat(64),
    apt: { state: 'executed', exit }, result: { state: 'executed', exit } })) });
parallel.stages.update.exit = 0; parallel.stages.download = { state: 'executed', exit: 123 };
if (!captureRefused(parallelOuter, 2, aptId, parallel)) throw new Error('Typed parallel boundary was not recognized');
const v3Controls: string[] = [];
for (const text of ['null', '[]', '100', 'true', '"record"']) {
  let parsed: any = {}, refused = false;
  try { parsed = parseCaptureProcess(Buffer.from(text)); } catch { refused = true; }
  const outer = structuredClone(apt); outer.stages['input-capture'].exit = 2;
  if (!refused || captureRefused(outer, 2, aptId, parsed)) throw new Error('Present non-record process accepted');
  v3Controls.push('present-process-' + text + '-refused');
}
function parallelRefuse(name: string, mutate: (outer: any, process: any) => void) {
  const outer = structuredClone(parallelOuter), process = structuredClone(parallel); mutate(outer, process);
  if (captureRefused(outer, 2, aptId, process)) throw new Error('Parallel boundary failed');
  v3Controls.push(name);
}
parallelRefuse('aggregate-substituted-100', (o, p) => { o.stages['input-capture'].exit = p.docker_exit = p.stages.download.exit = 100; });
parallelRefuse('aggregate-mismatch', (_, p) => { p.docker_exit = 100; });
parallelRefuse('cleanup-unavailable', (_, p) => { p.container_absent = false; });
parallelRefuse('different-worker-bound', (_, p) => { p.download_workers = 9; });
parallelRefuse('plan-unavailable', (_, p) => { p.plan = { state: 'unavailable', exit: null }; });
parallelRefuse('plan-digest-missing', (_, p) => { p.download_plan_sha256 = null; });
parallelRefuse('archive-check-started', (_, p) => { p.archive_check = { state: 'executed', exit: 100 }; });
parallelRefuse('no-failed-worker', (_, p) => { p.downloads[1].apt.exit = p.downloads[1].result.exit = 0; });
parallelRefuse('missing-workers', (_, p) => { p.downloads = []; });
parallelRefuse('worker-unavailable', (_, p) => { p.downloads[1].apt = { state: 'unavailable', exit: null }; });
parallelRefuse('worker-unstarted', (_, p) => { p.downloads[1].result = { state: 'not_run', exit: null }; });
parallelRefuse('worker-signal', (_, p) => { p.downloads[1].apt.exit = p.downloads[1].result.exit = 153; });
parallelRefuse('worker-validation-refusal', (_, p) => { p.downloads[0].result.exit = 2; });
parallelRefuse('worker-string-exit', (_, p) => { p.downloads[1].apt.exit = p.downloads[1].result.exit = '100'; });
parallelRefuse('worker-extra-status', (_, p) => { p.downloads[1].result.extra = true; });
parallelRefuse('worker-extra-field', (_, p) => { p.downloads[1].extra = true; });
parallelRefuse('worker-unsafe-name', (_, p) => { p.downloads[1].file = '../outside.deb'; });
parallelRefuse('worker-duplicate-name', (_, p) => { p.downloads[1].file = p.downloads[0].file; });
parallelRefuse('worker-size-zero', (_, p) => { p.downloads[1].size = 0; });
parallelRefuse('worker-file-bound', (_, p) => { p.downloads[1].size = 200_000_001; });
parallelRefuse('worker-size-string', (_, p) => { p.downloads[1].size = '512'; });
parallelRefuse('worker-hash-invalid', (_, p) => { p.downloads[1].sha256 = 'c'; });
parallelRefuse('worker-total-bound', (_, p) => { p.downloads = Array.from({ length: 6 }, (_, i) => ({
  ...structuredClone(p.downloads[1]), file: `large_${i}_all.deb`, size: 200_000_000 })); });
parallelRefuse('worker-count-bound', (_, p) => { p.downloads = Array.from({ length: 1101 }, (_, i) => ({
  ...structuredClone(p.downloads[1]), file: `small_${i}_all.deb` })); });
parallelRefuse('generic-refusal-with-process', (o, p) => { o.stages['input-capture'].exit = p.docker_exit = 2; });
const v3Update = structuredClone(parallel), v3Outer = structuredClone(apt);
Object.assign(v3Update, { docker_exit: 100, downloads: [], download_plan_sha256: null, plan: { state: 'not_run', exit: null } });
v3Update.stages.update.exit = 100; v3Update.stages.download = { state: 'not_run', exit: null };
if (!captureRefused(v3Outer, 2, aptId, v3Update)) throw new Error('V3 update boundary was not recognized');
v3Update.stages.update.exit = 0; v3Update.stages.download = { state: 'executed', exit: 100 };
v3Update.plan = { state: 'executed', exit: 100 };
if (!captureRefused(v3Outer, 2, aptId, v3Update)) throw new Error('V3 plan boundary was not recognized');
const newRunner = createHash('sha256').update(await readFile(new URL('./run.py', import.meta.url))).digest('hex');
const productionId = { ...aptId, runner_sha256: newRunner }, productionOuter = structuredClone(apt);
productionOuter.selected_sources['runtime-security/latexml-native-ci/run.py'].sha256 = newRunner;
if (captureRefused(productionOuter, 2, productionId, processRow)) throw new Error('Production accepted legacy process');
v3Controls.push('production-refuses-legacy-process');
productionOuter.stages['input-capture'].exit = 123;
if (!captureRefused(productionOuter, 2, productionId, parallel)) throw new Error('Production parallel positive refused');
// Refusal classes (#737): each class first round-trips from a well-formed private
// record on an actual refused receipt; malformed records and non-capture failures
// never yield an upstream class.
const classControls: string[] = [];
const record = (name: string) => ({ schema: 'aelira-latexml-capture-refusal-class-v1', class: name });
for (const name of refusalClasses) {
  if (attemptRefusalClass(2, original, record(name)) !== name) throw new Error('Refusal class not recorded: ' + name);
  classControls.push('records-' + name);
}
if (attemptRefusalClass(2, apt, record('upstream_http_auth')) !== 'upstream_http_auth') throw new Error('APT refusal class not recorded');
function classRefuse(name: string, exit: number, row: any, rec: any, expected: string | null) {
  if (attemptRefusalClass(exit, row, rec) !== expected) throw new Error('Refusal class boundary failed: ' + name);
  classControls.push(name);
}
classRefuse('success-has-no-class', 0, original, record('upstream_http_auth'), null);
const later = structuredClone(original);
later.stages['input-capture'] = { state: 'executed', exit: 0 }; later.stages['input-pin'] = { state: 'executed', exit: 2 };
classRefuse('post-capture-failure-has-no-class', 2, later, record('upstream_http_auth'), null);
classRefuse('missing-record-unknown', 2, original, null, 'unknown');
classRefuse('missing-outer-unknown', 2, null, null, 'unknown');
classRefuse('undeclared-class-unknown', 2, original, record('upstream_http_418'), 'unknown');
classRefuse('wrong-record-schema-unknown', 2, original, { ...record('upstream_http_auth'), schema: 'other' }, 'unknown');
classRefuse('extra-record-field-unknown', 2, original, { ...record('upstream_http_auth'), url: 'x' }, 'unknown');
classRefuse('array-record-unknown', 2, original, ['upstream_http_auth'], 'unknown');
classRefuse('string-record-unknown', 2, original, 'upstream_http_auth', 'unknown');
const unavailable = structuredClone(original); unavailable.stages['input-capture'] = { state: 'unavailable', exit: null };
classRefuse('unavailable-capture-keeps-timeout', 2, unavailable, record('timeout'), 'timeout');
// Cached input (#840): only three retryable snapshot refusals, each an upstream
// availability class, fall back, and the runner then receives the reviewed lock.
const fallbackControls: string[] = [];
const refusedAttempt = (attempt: number, refusal_class = 'upstream_http_5xx') => ({ attempt, source: 'snapshot',
  exit: 2, outer_sha256: '0'.repeat(64), capture_process_sha256: null, capture_refusal: true, refusal_class });
const three = () => [1, 2, 3].map(n => refusedAttempt(n));
for (const name of fallbackClasses) {
  if (!cacheFallbackEligible([1, 2, 3].map(n => refusedAttempt(n, name)))) throw new Error('Cache fallback class refused: ' + name);
  fallbackControls.push('falls-back-on-' + name);
}
if (!cacheFallbackEligible([refusedAttempt(1, 'transport'), refusedAttempt(2), refusedAttempt(3, 'timeout')])) throw new Error('Mixed upstream classes refused');
function fallbackRefuse(name: string, attempts: any) {
  if (cacheFallbackEligible(attempts)) throw new Error('Cache fallback boundary failed: ' + name);
  fallbackControls.push(name);
}
for (const name of refusalClasses.filter(c => !(fallbackClasses as readonly string[]).includes(c))) {
  fallbackRefuse('never-on-' + name, three().map((r, i) => i === 1 ? { ...r, refusal_class: name } : r));
}
fallbackRefuse('null-class', three().map((r, i) => i === 2 ? { ...r, refusal_class: null } : r));
fallbackRefuse('two-attempts', three().slice(0, 2));
fallbackRefuse('four-attempts', [...three(), refusedAttempt(4)]);
fallbackRefuse('not-retry-eligible', three().map((r, i) => i === 2 ? { ...r, capture_refusal: false } : r));
fallbackRefuse('later-success', three().map((r, i) => i === 2 ? { ...r, exit: 0, refusal_class: null } : r));
fallbackRefuse('other-exit', three().map((r, i) => i === 0 ? { ...r, exit: 1 } : r));
fallbackRefuse('cache-source', three().map((r, i) => i === 0 ? { ...r, source: 'cache' } : r));
fallbackRefuse('misnumbered', three().map((r, i) => i === 0 ? { ...r, attempt: 2 } : r));
fallbackRefuse('missing-receipt', null);
fallbackRefuse('array-like', { 0: refusedAttempt(1), 1: refusedAttempt(2), 2: refusedAttempt(3), length: 3 });
if (Object.keys(cachedInputLocks).sort().join(',') !== 'amd64,arm64' ||
  !Object.values(cachedInputLocks).every(v => /^[0-9a-f]{64}$/.test(v))) throw new Error('Reviewed input locks malformed');
const captureArgs = ['--operator-repo', '/w', '--capture-input', '--scratch', '/t/latexml-native-private'];
const cachedArgs = cachedInputArgs(captureArgs, '/t/latexml-apt-input', cachedInputLocks.arm64);
if (JSON.stringify(cachedArgs) !== JSON.stringify(['--operator-repo', '/w', '--apt-input', '/t/latexml-apt-input',
  '--apt-lock-sha256', cachedInputLocks.arm64, '--scratch', '/t/latexml-native-private'])) throw new Error('Cached input arguments differ');
fallbackControls.push('cached-input-arguments');
for (const [name, argv, lock] of [
  ['without-capture-flag', captureArgs.filter(a => a !== '--capture-input'), cachedInputLocks.arm64],
  ['two-capture-flags', [...captureArgs, '--capture-input'], cachedInputLocks.arm64],
  ['supplied-input-present', [...captureArgs, '--apt-input', '/x'], cachedInputLocks.arm64],
  ['malformed-lock', captureArgs, 'F'.repeat(64)],
] as [string, string[], string][]) {
  let refusedArgs = false;
  try { cachedInputArgs(argv, '/t/latexml-apt-input', lock); } catch { refusedArgs = true; }
  if (!refusedArgs) throw new Error('Cached input arguments accepted: ' + name);
  fallbackControls.push('arguments-' + name);
}
console.log(JSON.stringify({ schema: 'aelira-latexml-capture-retry-controls-v4',
  positive: 'actual-public-capture-refusal', controls, apt_positive: 'actual-native-apt-update-refusal',
  legacy_v2_download_positive: true, apt_controls: aptControls, parallel_positive: 'synthetic-original-worker-statuses',
  v3_update_and_plan_positives: true, parallel_controls: v3Controls, missing_receipts_refused: true,
  refusal_class_controls: classControls, cache_fallback_controls: fallbackControls, status: 'pass' }));
