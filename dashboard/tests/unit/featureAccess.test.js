/**
 * Unit tests for featureAccess utility functions
 * Uses Node.js native test runner
 *
 * Aelira Core has two free workspace shapes, not pricing tiers:
 * "individual" (single-user) and "department" (multi-user).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  TIERS,
  getFeatures,
  hasFeature,
  isIndividualTier,
  getTierDisplayName,
  getDepartmentLabel,
  hasScanType,
  getAvailableScanTypes,
  getExcludedScanTypes,
  getMaxFilesPerUpload,
} from '../../src/utils/featureAccess.ts';

describe('TIERS constant', () => {
  it('defines exactly the two workspace shapes', () => {
    assert.equal(TIERS.INDIVIDUAL, 'individual');
    assert.equal(TIERS.DEPARTMENT, 'department');
    assert.equal(Object.keys(TIERS).length, 2);
  });
});

describe('getFeatures', () => {
  it('individual workspace: full features, solo-shaped', () => {
    const features = getFeatures(TIERS.INDIVIDUAL);

    // Structural differences only — this is a solo workspace
    assert.equal(features.showAdmin, false);
    assert.equal(features.showTeamFeatures, false);
    assert.equal(features.showDepartmentInfo, false);
    assert.equal(features.departmentLabel, 'Account');
    assert.equal(features.tierDisplayName, 'Individual');

    // Nothing is paywalled
    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAIProviderSettings, true);
    assert.equal(features.showOllamaConfig, true);
    assert.equal(features.showCustomAPIKeys, true);
    assert.equal(features.showVideoProcessing, true);
    assert.equal(features.showBulkAPI, true);
    assert.equal(features.showEvidenceReports, true);
    assert.equal(features.showQuotaBar, false);
  });

  it('department workspace: full features, team-shaped', () => {
    const features = getFeatures(TIERS.DEPARTMENT);

    assert.equal(features.showAdmin, true);
    assert.equal(features.showTeamFeatures, true);
    assert.equal(features.showDepartmentInfo, true);
    assert.equal(features.departmentLabel, 'Department');
    assert.equal(features.tierDisplayName, 'Department');

    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAIProviderSettings, true);
    assert.equal(features.showOllamaConfig, true);
    assert.equal(features.showCustomAPIKeys, true);
    assert.equal(features.showVideoProcessing, true);
    assert.equal(features.showBulkAPI, true);
    assert.equal(features.showEvidenceReports, true);
    assert.equal(features.showQuotaBar, false);
  });

  it('unknown/legacy tier values fall back to the department config', () => {
    const features = getFeatures('some_legacy_tier');
    assert.deepEqual(features, getFeatures(TIERS.DEPARTMENT));
  });
});

describe('hasFeature', () => {
  it('returns boolean flags for both shapes', () => {
    assert.equal(hasFeature(TIERS.INDIVIDUAL, 'showAdmin'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showAdmin'), true);
    assert.equal(hasFeature(TIERS.INDIVIDUAL, 'showBulkUpload'), true);
  });

  it('returns false for non-boolean features', () => {
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'maxFilesPerUpload'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'tierDisplayName'), false);
  });
});

describe('isIndividualTier', () => {
  it('recognizes the individual shape', () => {
    assert.equal(isIndividualTier(TIERS.INDIVIDUAL), true);
    assert.equal(isIndividualTier(TIERS.DEPARTMENT), false);
  });

  it('tolerates legacy individual_* values from existing databases', () => {
    assert.equal(isIndividualTier('individual_free'), true);
    assert.equal(isIndividualTier('individual_pro'), true);
  });
});

describe('display helpers', () => {
  it('getTierDisplayName', () => {
    assert.equal(getTierDisplayName(TIERS.INDIVIDUAL), 'Individual');
    assert.equal(getTierDisplayName(TIERS.DEPARTMENT), 'Department');
  });

  it('getDepartmentLabel', () => {
    assert.equal(getDepartmentLabel(TIERS.INDIVIDUAL), 'Account');
    assert.equal(getDepartmentLabel(TIERS.DEPARTMENT), 'Department');
  });
});

describe('scan types', () => {
  it('every scan type is available in both shapes', () => {
    for (const tier of [TIERS.INDIVIDUAL, TIERS.DEPARTMENT]) {
      assert.equal(getExcludedScanTypes(tier).length, 0);
      assert.equal(hasScanType(tier, 'pdf'), true);
      assert.equal(hasScanType(tier, 'video'), true);
      assert.equal(hasScanType(tier, 'latex'), true);
      assert.ok(getAvailableScanTypes(tier).length >= 10);
    }
  });
});

describe('getMaxFilesPerUpload', () => {
  it('returns the shape limit', () => {
    assert.equal(getMaxFilesPerUpload(TIERS.INDIVIDUAL), 50);
    assert.equal(getMaxFilesPerUpload(TIERS.DEPARTMENT), 50);
  });
});
