import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const adminApiSource = readFileSync(new URL('../../src/api/admin.ts', import.meta.url), 'utf8');
const adminDashboardSource = readFileSync(
  new URL('../../src/pages/AdminDashboard.tsx', import.meta.url),
  'utf8',
);
const cardSource = readFileSync(
  new URL('../../src/components/admin/OperationsHealthCard.tsx', import.meta.url),
  'utf8',
);

test('typed admin client reads the existing worker-status authority', () => {
  assert.match(adminApiSource, /interface WorkerStatusResponse/);
  assert.match(adminApiSource, /getWorkerStatus/);
  assert.match(adminApiSource, /apiClient\.get<WorkerStatusResponse>\(['"]\/api\/jobs\/worker-status['"]\)/);
});

test('operations card mounts only for the super-admin role', () => {
  assert.match(adminDashboardSource, /OperationsHealthCard/);
  assert.match(adminDashboardSource, /currentUserRole === ['"]super_admin['"][\s\S]*<OperationsHealthCard/);
});

test('card exposes every bounded health domain without client-side reclassification', () => {
  for (const field of [
    'snapshot.status',
    'snapshot.health_state',
    'snapshot.queue',
    'snapshot.workers',
    'snapshot.progress',
    'snapshot.maintenance',
    'snapshot.weekly_summary_scheduler',
    'snapshot.reconciliation',
    'snapshot.orphans',
    'snapshot.generated_at',
  ]) {
    assert.match(cardSource, new RegExp(field.replace('.', '\\.')));
  }
  assert.doesNotMatch(cardSource, /latest_heartbeat_age_seconds\s*[<>]/);
  assert.doesNotMatch(cardSource, /stalled_processing\s*[<>]/);
});

test('loading, unavailable, empty, healthy, stale, and degraded states are explicit and accessible', () => {
  for (const label of [
    'Loading operations snapshot',
    'Operations snapshot unavailable',
    'No jobs queued or processing',
    'Healthy',
    'Degraded — needs attention',
    'Stale',
    'Current snapshot',
  ]) {
    assert.match(cardSource, new RegExp(label));
  }
  assert.match(cardSource, /role="status"/);
  assert.match(cardSource, /role="alert"/);
  assert.match(cardSource, /aria-label="Refresh operations snapshot"/);
});

test('operations card is read-only and makes no uptime or SLO claim', () => {
  assert.match(cardSource, /This is a current snapshot, not uptime history or an SLO\./);
  assert.doesNotMatch(cardSource, /apiClient\.(post|put|patch|delete)/);
  assert.doesNotMatch(cardSource, /adminApi\.(retry|purge|reconcile|start|stop|drain)/);
});
