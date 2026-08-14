import { useMemo } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  getFeatures,
  hasFeature as hasFeatureUtil,
  isFreeTier as isFreeTierUtil,
  isPaidTier as isPaidTierUtil,
  getTierDisplayName,
  getDepartmentLabel,
  TIERS,
  TierFeatures,
  FeatureKey,
  TierKey,
} from '../utils/featureAccess';

// ============================================================================
// Types
// ============================================================================

export interface UseFeatureAccessResult {
  // Current tier
  tier: TierKey;
  tierDisplayName: string;
  departmentLabel: string;

  // Tier checks
  isFreeTier: boolean;
  isPaidTier: boolean;

  // All features
  features: TierFeatures;

  // Convenience accessors for common features
  showIntegrations: boolean;
  showBulkUpload: boolean;
  showAdmin: boolean;
  showAIProviderSettings: boolean;
  showOllamaConfig: boolean;
  showCustomAPIKeys: boolean;
  showTeamFeatures: boolean;
  showDepartmentInfo: boolean;
  showQuotaBar: boolean;
  maxFilesPerUpload: number;

  // Advanced feature accessors
  showVideoProcessing: boolean;
  showAdvancedAnalytics: boolean;
  showPrioritySupport: boolean;
  showBulkAPI: boolean;
  showSSO: boolean;
  showWhiteLabel: boolean;
  showDedicatedSupport: boolean;
  showComplianceCertificates: boolean;

  // Helper function for custom feature checks
  hasFeature: (featureName: FeatureKey) => boolean;
}

// ============================================================================
// Hook
// ============================================================================

/**
 * Hook to access tier-based feature flags
 * @returns Feature access utilities and flags
 */
export function useFeatureAccess(): UseFeatureAccessResult {
  const { department } = useAuth();
  const tier = (department?.tier || TIERS.DEPARTMENT) as TierKey;

  const features = useMemo(() => getFeatures(tier), [tier]);

  return {
    // Current tier
    tier,
    tierDisplayName: getTierDisplayName(tier),
    departmentLabel: getDepartmentLabel(tier),

    // Tier checks
    isFreeTier: isFreeTierUtil(tier),
    isPaidTier: isPaidTierUtil(tier),

    // All features
    features,

    // Convenience accessors for common features
    showIntegrations: features.showIntegrations,
    showBulkUpload: features.showBulkUpload,
    showAdmin: features.showAdmin,
    showAIProviderSettings: features.showAIProviderSettings,
    showOllamaConfig: features.showOllamaConfig,
    showCustomAPIKeys: features.showCustomAPIKeys,
    showTeamFeatures: features.showTeamFeatures,
    showDepartmentInfo: features.showDepartmentInfo,
    showQuotaBar: features.showQuotaBar,
    maxFilesPerUpload: features.maxFilesPerUpload,

    // Advanced feature accessors
    showVideoProcessing: features.showVideoProcessing,
    showAdvancedAnalytics: features.showAdvancedAnalytics,
    showPrioritySupport: features.showPrioritySupport,
    showBulkAPI: features.showBulkAPI,
    showSSO: features.showSSO,
    showWhiteLabel: features.showWhiteLabel,
    showDedicatedSupport: features.showDedicatedSupport,
    showComplianceCertificates: features.showComplianceCertificates,

    // Helper function for custom feature checks
    hasFeature: (featureName: FeatureKey) => hasFeatureUtil(tier, featureName),
  };
}
