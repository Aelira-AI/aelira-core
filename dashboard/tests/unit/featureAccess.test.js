/**
 * Unit tests for featureAccess utility functions
 * Uses Node.js native test runner
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  TIERS,
  getFeatures,
  hasFeature,
  isFreeTier,
  isPaidTier,
  getTierDisplayName,
  getDepartmentLabel,
} from '../../src/utils/featureAccess.ts';

describe('TIERS constant', () => {
  it('should define all tier levels', () => {
    assert.equal(TIERS.INDIVIDUAL_FREE, 'individual_free');
    assert.equal(TIERS.TRIAL, 'trial');
    assert.equal(TIERS.DEPARTMENT, 'department');
    assert.equal(TIERS.UNIVERSITY, 'university');
    assert.equal(TIERS.ENTERPRISE, 'enterprise');
  });

  it('should have 7 tier levels', () => {
    assert.equal(Object.keys(TIERS).length, 7);
  });
});

describe('getFeatures', () => {
  it('should return features for individual_free tier', () => {
    const features = getFeatures(TIERS.INDIVIDUAL_FREE);

    assert.equal(features.showIntegrations, false);
    assert.equal(features.showBulkUpload, false);
    assert.equal(features.showAdmin, false);
    assert.equal(features.showAIProviderSettings, false);
    assert.equal(features.showOllamaConfig, false);
    assert.equal(features.showCustomAPIKeys, false);
    assert.equal(features.maxFilesPerUpload, 1);
    assert.equal(features.showTeamFeatures, false);
    assert.equal(features.tierDisplayName, 'Faculty Starter (Free)');
  });

  it('should return features for trial tier', () => {
    const features = getFeatures(TIERS.TRIAL);

    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAdmin, false);
    assert.equal(features.showAIProviderSettings, false);
    assert.equal(features.showOllamaConfig, false);
    assert.equal(features.showCustomAPIKeys, false);
    assert.equal(features.maxFilesPerUpload, 10);
    assert.equal(features.tierDisplayName, 'Trial');
  });

  it('should return features for department tier', () => {
    const features = getFeatures(TIERS.DEPARTMENT);

    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAdmin, true);
    assert.equal(features.showAIProviderSettings, true);
    assert.equal(features.showOllamaConfig, true);
    assert.equal(features.showCustomAPIKeys, false);
    assert.equal(features.maxFilesPerUpload, 50);
    assert.equal(features.showTeamFeatures, true);
    assert.equal(features.showComplianceCertificates, true);
    assert.equal(features.tierDisplayName, 'Department');
  });

  it('should return features for university tier', () => {
    const features = getFeatures(TIERS.UNIVERSITY);

    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAdmin, true);
    assert.equal(features.showAIProviderSettings, true);
    assert.equal(features.showOllamaConfig, true);
    assert.equal(features.showCustomAPIKeys, false);
    assert.equal(features.maxFilesPerUpload, 100);
    assert.equal(features.showVideoProcessing, true);
    assert.equal(features.showAdvancedAnalytics, true);
    assert.equal(features.showPrioritySupport, true);
    assert.equal(features.tierDisplayName, 'University');
  });

  it('should return features for enterprise tier', () => {
    const features = getFeatures(TIERS.ENTERPRISE);

    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.showAdmin, true);
    assert.equal(features.showAIProviderSettings, true);
    assert.equal(features.showOllamaConfig, true);
    assert.equal(features.showCustomAPIKeys, true);
    assert.equal(features.maxFilesPerUpload, -1); // Unlimited
    assert.equal(features.showSSO, true);
    assert.equal(features.showWhiteLabel, true);
    assert.equal(features.showDedicatedSupport, true);
    assert.equal(features.tierDisplayName, 'Enterprise');
  });

  it('should return default features for unknown tier', () => {
    const features = getFeatures('unknown_tier');

    // Should fall back to department tier features
    assert.equal(features.showIntegrations, true);
    assert.equal(features.showBulkUpload, true);
    assert.equal(features.tierDisplayName, 'Department');
  });

  it('should return default features for null tier', () => {
    const features = getFeatures(null);

    // Should fall back to department tier features
    assert.equal(features.showIntegrations, true);
  });

  it('should return default features for undefined tier', () => {
    const features = getFeatures(undefined);

    // Should fall back to department tier features
    assert.equal(features.showIntegrations, true);
  });
});

describe('hasFeature', () => {
  it('should return true for enabled features', () => {
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showCustomAPIKeys'), true);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showIntegrations'), true);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showVideoProcessing'), true);
  });

  it('should return false for disabled features', () => {
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showIntegrations'), false);
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showCustomAPIKeys'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showAdmin'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showCustomAPIKeys'), false);
  });

  it('should return false for unknown features', () => {
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'unknownFeature'), false);
  });

  it('should handle unknown tier gracefully', () => {
    // Falls back to default tier (department)
    assert.equal(hasFeature('unknown', 'showIntegrations'), true);
  });
});

describe('isFreeTier', () => {
  it('should return true for individual_free tier', () => {
    assert.equal(isFreeTier(TIERS.INDIVIDUAL_FREE), true);
  });

  it('should return false for paid tiers', () => {
    assert.equal(isFreeTier(TIERS.TRIAL), false);
    assert.equal(isFreeTier(TIERS.DEPARTMENT), false);
    assert.equal(isFreeTier(TIERS.UNIVERSITY), false);
    assert.equal(isFreeTier(TIERS.ENTERPRISE), false);
  });

  it('should return false for unknown tier', () => {
    assert.equal(isFreeTier('unknown'), false);
  });

  it('should return false for null', () => {
    assert.equal(isFreeTier(null), false);
  });
});

describe('isPaidTier', () => {
  it('should return true for department tier', () => {
    assert.equal(isPaidTier(TIERS.DEPARTMENT), true);
  });

  it('should return true for university tier', () => {
    assert.equal(isPaidTier(TIERS.UNIVERSITY), true);
  });

  it('should return true for enterprise tier', () => {
    assert.equal(isPaidTier(TIERS.ENTERPRISE), true);
  });

  it('should return false for individual_free tier', () => {
    assert.equal(isPaidTier(TIERS.INDIVIDUAL_FREE), false);
  });

  it('should return false for trial tier', () => {
    assert.equal(isPaidTier(TIERS.TRIAL), false);
  });

  it('should return false for unknown tier', () => {
    assert.equal(isPaidTier('unknown'), false);
  });
});

describe('getTierDisplayName', () => {
  it('should return correct display names', () => {
    assert.equal(getTierDisplayName(TIERS.INDIVIDUAL_FREE), 'Faculty Starter (Free)');
    assert.equal(getTierDisplayName(TIERS.TRIAL), 'Trial');
    assert.equal(getTierDisplayName(TIERS.DEPARTMENT), 'Department');
    assert.equal(getTierDisplayName(TIERS.UNIVERSITY), 'University');
    assert.equal(getTierDisplayName(TIERS.ENTERPRISE), 'Enterprise');
  });

  it('should return default display name for unknown tier', () => {
    assert.equal(getTierDisplayName('unknown'), 'Department');
  });
});

describe('getDepartmentLabel', () => {
  it('should return Account for individual_free tier', () => {
    assert.equal(getDepartmentLabel(TIERS.INDIVIDUAL_FREE), 'Account');
  });

  it('should return Department for most tiers', () => {
    assert.equal(getDepartmentLabel(TIERS.TRIAL), 'Department');
    assert.equal(getDepartmentLabel(TIERS.DEPARTMENT), 'Department');
    assert.equal(getDepartmentLabel(TIERS.UNIVERSITY), 'Department');
  });

  it('should return Organization for enterprise tier', () => {
    assert.equal(getDepartmentLabel(TIERS.ENTERPRISE), 'Organization');
  });

  it('should return default label for unknown tier', () => {
    assert.equal(getDepartmentLabel('unknown'), 'Department');
  });
});

describe('Feature access combinations', () => {
  it('should correctly identify API key access by tier', () => {
    // Only Enterprise can use custom API keys
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showCustomAPIKeys'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showCustomAPIKeys'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showCustomAPIKeys'), false);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showCustomAPIKeys'), false);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showCustomAPIKeys'), true);
  });

  it('should correctly identify Ollama access by tier', () => {
    // Department and above can use Ollama
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showOllamaConfig'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showOllamaConfig'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showOllamaConfig'), true);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showOllamaConfig'), true);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showOllamaConfig'), true);
  });

  it('should correctly identify AI provider settings access', () => {
    // Department and above can see AI provider settings
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showAIProviderSettings'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showAIProviderSettings'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showAIProviderSettings'), true);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showAIProviderSettings'), true);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showAIProviderSettings'), true);
  });

  it('should correctly identify SSO access', () => {
    // Only Enterprise has SSO
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showSSO'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showSSO'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showSSO'), false);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showSSO'), false);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showSSO'), true);
  });

  it('should correctly identify video processing access', () => {
    // University and above have video processing
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showVideoProcessing'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showVideoProcessing'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showVideoProcessing'), false);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showVideoProcessing'), true);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showVideoProcessing'), true);
  });
});

describe('Max files per upload', () => {
  it('should have correct file limits per tier', () => {
    assert.equal(getFeatures(TIERS.INDIVIDUAL_FREE).maxFilesPerUpload, 1);
    assert.equal(getFeatures(TIERS.TRIAL).maxFilesPerUpload, 10);
    assert.equal(getFeatures(TIERS.DEPARTMENT).maxFilesPerUpload, 50);
    assert.equal(getFeatures(TIERS.UNIVERSITY).maxFilesPerUpload, 100);
    assert.equal(getFeatures(TIERS.ENTERPRISE).maxFilesPerUpload, -1); // Unlimited
  });
});

describe('Compliance certificates', () => {
  it('should be available for paid tiers only', () => {
    assert.equal(hasFeature(TIERS.INDIVIDUAL_FREE, 'showComplianceCertificates'), false);
    assert.equal(hasFeature(TIERS.TRIAL, 'showComplianceCertificates'), false);
    assert.equal(hasFeature(TIERS.DEPARTMENT, 'showComplianceCertificates'), true);
    assert.equal(hasFeature(TIERS.UNIVERSITY, 'showComplianceCertificates'), true);
    assert.equal(hasFeature(TIERS.ENTERPRISE, 'showComplianceCertificates'), true);
  });
});
