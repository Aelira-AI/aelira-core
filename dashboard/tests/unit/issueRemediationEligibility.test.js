import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeFindingAutoFix,
  matchesFindingAutoFixFilter,
  countFindingAutoFix,
  selectEligibleIssueScanIds,
} from '../../src/utils/issueRemediationEligibility.ts';

describe('Issues remediation eligibility', () => {
  it('keeps real scanner findings without capability flags unknown', () => {
    for (const finding of [
      { description: 'PDF document title not set in metadata', type: 'title', page_number: 1 },
      { issue_type: 'missing_alt_text', shape_name: 'Picture 1', slide_number: 2 },
      { type: 'heading', paragraph_index: 0, suggested_fix: 'Use a heading style' },
    ]) {
      assert.deepEqual(normalizeFindingAutoFix(finding), { state: 'unknown', reason: 'not_reported' });
    }
  });

  it('preserves explicit true and false from either field without truthy coercion', () => {
    for (const key of ['can_auto_fix', 'auto_fix_available']) {
      assert.deepEqual(normalizeFindingAutoFix({ [key]: true }), { state: 'available', reason: 'reported' });
      assert.deepEqual(normalizeFindingAutoFix({ [key]: false }), { state: 'unavailable', reason: 'reported' });
      for (const value of ['true', 'false', 1, 0, null]) {
        assert.deepEqual(normalizeFindingAutoFix({ [key]: value }), { state: 'unknown', reason: 'invalid_flags' });
      }
    }
    assert.equal(normalizeFindingAutoFix({ can_auto_fix: true, auto_fix_available: true }).state, 'available');
    assert.equal(normalizeFindingAutoFix({ can_auto_fix: false, auto_fix_available: false }).state, 'unavailable');
  });

  it('treats contradictory or malformed flags as unknown with a reason', () => {
    for (const finding of [
      { can_auto_fix: false, auto_fix_available: true },
      { can_auto_fix: true, auto_fix_available: false },
    ]) {
      assert.deepEqual(normalizeFindingAutoFix(finding), { state: 'unknown', reason: 'conflicting_flags' });
    }
    assert.deepEqual(normalizeFindingAutoFix({ can_auto_fix: false, auto_fix_available: 'true' }), {
      state: 'unknown', reason: 'invalid_flags',
    });
  });

  it('separates available, unavailable and unknown findings in filters and counts', () => {
    const capabilities = [true, false, undefined].map((value) => normalizeFindingAutoFix({ can_auto_fix: value }));
    for (const state of ['available', 'unavailable', 'unknown']) {
      assert.deepEqual(capabilities.filter((capability) => matchesFindingAutoFixFilter(capability, state)).map((capability) => capability.state), [state]);
    }
    assert.equal(capabilities.filter((capability) => matchesFindingAutoFixFilter(capability, 'all')).length, 3);
    assert.deepEqual(countFindingAutoFix(capabilities), { available: 1, unavailable: 1, unknown: 1 });
  });

  it('queues unique eligible documents from the filtered findings regardless of finding capability', () => {
    const scans = [
      { id: 'eligible', remediation_eligibility: { eligible: true, reason: null } },
      { id: 'filtered-out', remediation_eligibility: { eligible: true, reason: null } },
      { id: 'blocked', remediation_eligibility: { eligible: false, reason: 'source_unavailable' } },
      { id: 'old-server' },
      { id: 'malformed', remediation_eligibility: { eligible: 'true', reason: null } },
    ];
    const findings = [
      { scanId: 'eligible', description: 'PDF title missing' },
      { scanId: 'eligible', can_auto_fix: false },
      { scanId: 'blocked', can_auto_fix: true },
      { scanId: 'old-server', can_auto_fix: true },
      { scanId: 'malformed', can_auto_fix: true },
      { scanId: 'not-found', can_auto_fix: true },
    ];
    const before = structuredClone(findings);
    assert.deepEqual(selectEligibleIssueScanIds(findings, scans), ['eligible']);
    assert.deepEqual(findings, before, 'selection must not mark findings resolved or infer remediation results');
    assert.deepEqual(selectEligibleIssueScanIds([], scans), []);
    assert.deepEqual(selectEligibleIssueScanIds(findings.filter((finding) => finding.scanId !== 'eligible'), scans), []);
  });
});
