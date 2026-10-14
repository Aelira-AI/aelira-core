import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { captureRefused } from './capture-retry.ts';

const bytes = await readFile(new URL('./fixtures/capture-refusal.json', import.meta.url));
if (createHash('sha256').update(bytes).digest('hex') !== '67096ed230fca4689edad03c649045c5a94e83a821095c26afe5f98e8f9a4f64') throw new Error('Frozen public receipt changed');
const original = JSON.parse(bytes.toString('utf8'));
const id = { revision: original.operator_ref, tree: original.operator_tree,
  architecture: original.architecture, remote: original.remote_ref };
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
if (captureRefused(null, 2, id) || captureRefused({}, 2, id)) throw new Error('Missing receipt accepted');
const aptBytes = await readFile(new URL('./fixtures/capture-apt-refusal.json', import.meta.url));
const processBytes = await readFile(new URL('./fixtures/capture-apt-process.json', import.meta.url));
if (createHash('sha256').update(aptBytes).digest('hex') !== '72c46950b2008f7c6078f5a5153c618a1763e4c440476f786c0cb0c3e8f94da5' ||
  createHash('sha256').update(processBytes).digest('hex') !== '5d741ddcacfdace58b03d0b81af8ec9067a655458e4f7cb15a08860744c9722f') throw new Error('Frozen native APT receipts changed');
const apt = JSON.parse(aptBytes.toString('utf8')), processRow = JSON.parse(processBytes.toString('utf8'));
const aptId = { revision: apt.operator_ref, tree: apt.operator_tree, architecture: apt.architecture, remote: apt.remote_ref };
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
console.log(JSON.stringify({ schema: 'aelira-latexml-capture-retry-controls-v2',
  positive: 'actual-public-capture-refusal', controls, apt_positive: 'actual-native-apt-update-refusal',
  download_stage_positive: true, apt_controls: aptControls, missing_receipts_refused: true, status: 'pass' }));
