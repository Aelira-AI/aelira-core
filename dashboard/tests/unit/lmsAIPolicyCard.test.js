import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../src/components/admin/LMSAIPolicyCard.tsx', import.meta.url), 'utf8');
const dashboardSource = readFileSync(new URL('../../src/pages/AdminDashboard.tsx', import.meta.url), 'utf8');

test('LMS AI policy card exposes accessible grouped controls and live status', () => {
  for (const token of ['<fieldset', '<legend', 'aria-live="polite"', 'aria-describedby=', 'ref={errorSummaryRef}']) {
    assert.ok(source.includes(token), `missing ${token}`);
  }
});

test('LMS AI policy card handles revision conflicts and never accepts secret fields', () => {
  assert.ok(source.includes('policy_revision'));
  assert.ok(source.includes('policy_revision_conflict'));
  assert.ok(source.includes('Reload current policy'));
  for (const secret of ['api_key:', 'credentials:', 'host:', 'model:']) {
    assert.equal(source.includes(secret), false, `secret field ${secret} must not exist`);
  }
});

test('provider readiness conflict replaces current state and focuses actionable reason', () => {
  for (const token of [
    "detail?.code === 'provider_not_ready'",
    'setPolicy(detail.current)',
    'setForm(editable(detail.current))',
    'reasonText[detail.reason]',
    'role="alert"',
    'errorSummaryRef.current?.focus()',
  ]) assert.ok(source.includes(token), `missing ${token}`);
});

test('policy card remains independently mounted during dashboard loading and errors', () => {
  assert.ok(dashboardSource.includes('<LMSAIPolicyCard />'));
  assert.ok(dashboardSource.includes('aria-busy={loading}'));
  assert.equal(/if \(loading\) \{\s*return/.test(dashboardSource), false);
  assert.equal(/if \(error\) \{\s*return/.test(dashboardSource), false);
});
