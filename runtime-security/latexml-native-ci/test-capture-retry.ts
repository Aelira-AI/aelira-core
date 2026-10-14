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
console.log(JSON.stringify({ schema: 'aelira-latexml-capture-retry-controls-v1',
  positive: 'actual-public-capture-refusal', controls, missing_receipts_refused: true, status: 'pass' }));
