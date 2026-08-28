import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

import { hasDatedDeadline } from '../../src/types/deadline.ts';

const dated = {
  applicability: 'dated_deadline',
  has_deadline: true,
  deadline_date: '2028-04-26',
  deadline_label: 'April 26, 2028',
  days_remaining: 607,
  framework_code: 'US_ADA_TITLE_II',
  framework_name: 'DOJ Title II ADA',
  standard: 'WCAG 2.1 Level AA',
  message: 'A dated accessibility target is configured.',
  urgency: 'none',
  is_past_deadline: false,
};

test('recognizes only complete server-supplied dated deadlines', () => {
  assert.equal(hasDatedDeadline(dated), true);
  assert.equal(hasDatedDeadline({ ...dated, has_deadline: false }), false);
  assert.equal(hasDatedDeadline({ ...dated, applicability: 'configuration_required' }), false);
  assert.equal(hasDatedDeadline({ ...dated, deadline_date: null }), false);
  assert.equal(hasDatedDeadline({ ...dated, days_remaining: null }), false);
  assert.equal(hasDatedDeadline({ ...dated, is_past_deadline: true, days_remaining: 0 }), false);
});

test('dashboard contains no independent deadline business constants', () => {
  const deletedUtility = new URL('../../src/utils/deadlines.ts', import.meta.url);
  assert.equal(existsSync(deletedUtility), false);

  for (const relative of [
    '../../src/pages/Dashboard.tsx',
    '../../src/pages/CourseOverview.tsx',
    '../../src/components/AnalyticsDashboard.tsx',
  ]) {
    const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /2027-04-26|2028-04-26|April 2027 Deadline/);
  }
});

test('projection API type matches the wrapped server contract', () => {
  const source = readFileSync(
    new URL('../../src/api/scans.ts', import.meta.url),
    'utf8',
  );
  assert.match(source, /Promise<DeadlineProjectionResponse>/);
  assert.match(source, /projection: DeadlineProjection/);
  assert.match(source, /deadline: DeadlineInfo/);
});
