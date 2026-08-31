import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('admin dashboard mounts the institution compliance surface', () => {
  const adminDashboard = readFileSync(
    new URL('../../src/pages/AdminDashboard.tsx', import.meta.url),
    'utf8',
  );
  assert.match(adminDashboard, /InstitutionComplianceCard/);
});

test('institution compliance declares metric hierarchy and every coverage count', () => {
  const component = readFileSync(
    new URL('../../src/components/admin/InstitutionComplianceCard.tsx', import.meta.url),
    'utf8',
  );

  assert.match(component, /Document-weighted institution score/);
  assert.match(component, /Secondary: flat department mean/);
  for (const label of ['Enrolled', 'Scanned', 'Verified', 'Stale', 'Failed', 'Total coverage']) {
    assert.match(component, new RegExp(label));
  }
});

test('institution compliance has explicit loading, error, empty, and populated states', () => {
  const component = readFileSync(
    new URL('../../src/components/admin/InstitutionComplianceCard.tsx', import.meta.url),
    'utf8',
  );

  assert.match(component, /Loading institution compliance/);
  assert.match(component, /Unable to load institution compliance/);
  assert.match(component, /No documents are enrolled/);
  assert.match(component, /Department drill-down/);
  assert.match(component, /role="status"/);
  assert.match(component, /role="alert"/);
  assert.match(component, /aria-label="Department compliance table"/);
  assert.match(component, /tabIndex=\{0\}/);
});

test('institution analytics client uses the derived-scope endpoint', () => {
  const scansApi = readFileSync(new URL('../../src/api/scans.ts', import.meta.url), 'utf8');
  assert.match(scansApi, /\/analytics\/institution/);
  assert.doesNotMatch(scansApi, /\/analytics\/institution\/\$\{departmentId\}/);
});
