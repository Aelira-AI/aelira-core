import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  canManageRegulatoryProfile,
  classifyRegulatoryProfileFailure,
  editableRegulatoryProfile,
  regulatoryProfileUpdate,
  unsupportedCurrentFramework,
  withChangedLegalContext,
} from '../../src/utils/regulatoryProfileForm.ts';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const apiSource = read('../../src/api/regulatoryProfile.ts');
const cardSource = read('../../src/components/settings/RegulatoryProfileCard.tsx');
const formSource = read('../../src/utils/regulatoryProfileForm.ts');
const settingsSource = read('../../src/pages/Settings.tsx');
const dashboardSource = read('../../src/pages/Dashboard.tsx');
const courseOverviewSource = read('../../src/pages/CourseOverview.tsx');

test('regulatory profile API is typed and only uses the tenant-scoped admin endpoint', () => {
  for (const token of [
    'RegulatoryProfile',
    'RegulatoryProfileUpdate',
    "'/admin/regulatory-profile'",
    'expected_revision',
    'custom_deadline_verified',
    'supported_frameworks',
  ]) assert.ok(apiSource.includes(token), `missing ${token}`);

  for (const forbidden of ['department_id', 'api_key', 'credentials']) {
    assert.equal(apiSource.includes(forbidden), false, `client contract must not expose ${forbidden}`);
  }
});

test('settings mounts an access-aware regulatory profile card at a stable anchor', () => {
  assert.ok(settingsSource.includes('<RegulatoryProfileCard'));
  assert.ok(cardSource.includes('id="regulatory-profile"'));
  assert.ok(cardSource.includes('canManageRegulatoryProfile(authMethod, user?.role)'));
  assert.ok(cardSource.includes('if (!canEdit)'));
  assert.ok(cardSource.includes('return <EditableRegulatoryProfileCard />'));
  assert.ok(cardSource.includes('The privileged component is only mounted after the outer access guard.'));
});

test('access decision prevents privileged requests for faculty and every LTI launch', () => {
  assert.equal(canManageRegulatoryProfile('session', 'admin'), true);
  assert.equal(canManageRegulatoryProfile('api_key', 'super_admin'), true);
  assert.equal(canManageRegulatoryProfile('session', 'faculty'), false);
  assert.equal(canManageRegulatoryProfile('lti', 'admin'), false);
});

const profile = (overrides = {}) => ({
  schema_version: 1,
  profile_revision: 4,
  country_code: 'US',
  regulatory_framework: 'US_ADA_TITLE_II',
  title_ii_entity_class: 'large',
  custom_deadline: null,
  custom_deadline_verified: false,
  configuration_complete: true,
  deadline: {},
  supported_frameworks: [
    { code: 'US_ADA_TITLE_II', name: 'DOJ Title II ADA', default_country_code: 'US', requires_explicit_selection: true, requires_title_ii_entity_class: true, allows_custom_deadline: true },
    { code: 'NONE', name: 'No applicable framework', default_country_code: null, requires_explicit_selection: true, requires_title_ii_entity_class: false, allows_custom_deadline: false },
  ],
  ...overrides,
});

test('legacy unsupported framework remains visible for repair but never becomes an option', () => {
  const legacy = profile({ regulatory_framework: 'EU_WAD', configuration_complete: false });
  assert.equal(unsupportedCurrentFramework(legacy), 'EU_WAD');
  assert.equal(editableRegulatoryProfile(legacy).regulatory_framework, '');
  assert.equal(legacy.supported_frameworks.some(({ code }) => code === 'EU_WAD'), false);
});

test('payload builder strips a custom date when the selected framework forbids it', () => {
  const current = profile({ regulatory_framework: 'NONE', title_ii_entity_class: null });
  const update = regulatoryProfileUpdate(current, {
    country_code: 'US',
    regulatory_framework: 'NONE',
    title_ii_entity_class: '',
    custom_deadline: '2030-01-01',
    custom_deadline_verified: true,
  });
  assert.equal(update.custom_deadline, null);
  assert.equal(update.custom_deadline_verified, false);
});

test('changing country or framework clears the custom-date attestation', () => {
  const form = {
    country_code: 'US', regulatory_framework: 'US_ADA_TITLE_II',
    title_ii_entity_class: 'large', custom_deadline: '2030-01-01',
    custom_deadline_verified: true,
  };
  assert.deepEqual(
    withChangedLegalContext(form, { country_code: 'CA' }),
    { ...form, country_code: 'CA', custom_deadline: '', custom_deadline_verified: false },
  );
  assert.equal(withChangedLegalContext(form, {
    regulatory_framework: 'NONE', title_ii_entity_class: '',
  }).custom_deadline, '');
  assert.deepEqual(
    withChangedLegalContext(form, { title_ii_entity_class: 'small_or_special_district' }),
    { ...form, title_ii_entity_class: 'small_or_special_district', custom_deadline: '', custom_deadline_verified: false },
  );
});

test('API failures classify stale revisions and field validation for actionable UI', () => {
  const current = profile();
  assert.deepEqual(classifyRegulatoryProfileFailure({ response: { data: { detail: {
    code: 'regulatory_profile_revision_conflict', current,
  } } } }), {
    kind: 'conflict', current,
    message: 'Another administrator changed this profile. Review the current values before saving again.',
  });
  assert.deepEqual(classifyRegulatoryProfileFailure({ response: { data: { detail: {
    code: 'invalid_regulatory_profile', field: 'country_code', message: 'Choose a real country.',
  } } } }), {
    kind: 'validation', field: 'country_code', message: 'Choose a real country.',
  });
});

test('form exposes conditional and verified deadline controls without hard-coded legal dates', () => {
  for (const token of [
    'Country code',
    'Regulatory framework',
    'Title II entity class',
    'custom-deadline-verified',
    'aria-live="polite"',
    'aria-invalid=',
    'aria-describedby=',
    'Reset changes',
    'Reload current profile',
    'Clear profile',
    'pendingFocusRef',
    '(target ?? errorSummaryRef.current)?.focus()',
    'disabled:cursor-not-allowed disabled:opacity-50',
    'is not available in this version',
    'Canonical deadline',
  ]) assert.ok(cardSource.includes(token), `missing ${token}`);

  assert.equal(/20\d\d-\d\d-\d\d/.test(cardSource), false, 'React must not hard-code a legal date');
  assert.ok(formSource.includes('custom_deadline: customDeadline'));
  assert.ok(cardSource.includes('autoComplete="off"'));
});

test('dashboard makes incomplete configuration visible and role-aware', () => {
  for (const token of [
    "stats?.deadline?.applicability === 'configuration_required'",
    "'/settings#regulatory-profile'",
    "authMethod !== 'lti'",
    "user?.role === 'admin'",
    'Contact an institution administrator',
  ]) assert.ok(dashboardSource.includes(token), `missing ${token}`);

  assert.ok(dashboardSource.includes("configurationRequired ? 'Finish your institution setup"));
  assert.ok(courseOverviewSource.includes("data.deadline?.applicability === 'configuration_required'"));
  assert.ok(courseOverviewSource.includes('Contact an institution administrator'));
});
