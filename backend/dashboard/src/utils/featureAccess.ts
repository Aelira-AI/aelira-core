/**
 * Tier-based feature access configuration
 *
 * Defines which features are available for each subscription tier.
 * Individual faculty on free tier should see a simplified experience
 * without infrastructure options like custom API keys or Ollama.
 */

import type { Tier } from '../types';

// ============================================================================
// Types
// ============================================================================

export interface TierFeatures {
  // Navigation
  showIntegrations: boolean;
  showBulkUpload: boolean;
  showAdmin: boolean;

  // Settings
  showAIProviderSettings: boolean;
  showOllamaConfig: boolean;
  showCustomAPIKeys: boolean;

  // Features
  maxFilesPerUpload: number;
  showTeamFeatures: boolean;
  showDepartmentInfo: boolean;
  showQuotaBar: boolean;

  // Scan Types
  scanTypes: string[];
  scanTypesExcluded: string[];

  // Advanced Features
  showVideoProcessing: boolean;
  showAdvancedAnalytics: boolean;
  showPrioritySupport: boolean;
  showBulkAPI: boolean;
  showSSO: boolean;
  showWhiteLabel: boolean;
  showDedicatedSupport: boolean;
  showComplianceCertificates: boolean;

  // Labels
  departmentLabel: string;
  tierDisplayName: string;
}

export type FeatureKey = keyof TierFeatures;

// ============================================================================
// Tier Constants
// ============================================================================

export const TIERS = {
  INDIVIDUAL_FREE: 'individual_free',
  INDIVIDUAL_PLUS: 'individual_plus',
  INDIVIDUAL_PRO: 'individual_pro',
  TRIAL: 'trial',
  DEPARTMENT: 'department',
  UNIVERSITY: 'university',
  ENTERPRISE: 'enterprise',
} as const;

export type TierKey = (typeof TIERS)[keyof typeof TIERS];

// ============================================================================
// Feature Configuration
// ============================================================================

const TIER_FEATURES: Record<TierKey, TierFeatures> = {
  [TIERS.INDIVIDUAL_FREE]: {
    // Navigation
    showIntegrations: false,      // No LMS integrations for individuals
    showBulkUpload: false,        // Limited to single file uploads
    showAdmin: false,             // No admin features

    // Settings
    showAIProviderSettings: false, // Uses our cloud infrastructure only
    showOllamaConfig: false,      // No self-hosted AI
    showCustomAPIKeys: false,     // Can't bring own API keys

    // Features
    maxFilesPerUpload: 1,
    showTeamFeatures: false,      // Solo user
    showDepartmentInfo: false,    // Show "Account" instead
    showQuotaBar: true,           // Show usage limits

    // Scan Types Available (matches backend TIER_QUOTAS)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image'],
    // Scan Types NOT available on free tier
    scanTypesExcluded: ['latex', 'video', 'website', 'code', 'chart'],

    // Advanced Features (not available)
    showVideoProcessing: false,
    showAdvancedAnalytics: false,
    showPrioritySupport: false,
    showBulkAPI: false,
    showSSO: false,
    showWhiteLabel: false,
    showDedicatedSupport: false,
    showComplianceCertificates: false,

    // Labels
    departmentLabel: 'Account',
    tierDisplayName: 'Faculty Starter (Free)',
  },

  [TIERS.INDIVIDUAL_PLUS]: {
    // Navigation - limited access
    showIntegrations: false,      // No LMS integrations for individuals
    showBulkUpload: false,        // Limited to single file uploads
    showAdmin: false,             // No admin features

    // Settings
    showAIProviderSettings: false, // Uses our cloud infrastructure only
    showOllamaConfig: false,      // No self-hosted AI
    showCustomAPIKeys: false,     // Can't bring own API keys

    // Features
    maxFilesPerUpload: 1,
    showTeamFeatures: false,      // Solo user
    showDepartmentInfo: false,    // Show "Account" instead
    showQuotaBar: true,           // Show usage limits

    // Scan Types Available (matches backend TIER_QUOTAS)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex'],
    scanTypesExcluded: ['video', 'website', 'code', 'chart'],

    // Advanced Features (limited)
    showVideoProcessing: false,
    showAdvancedAnalytics: false,
    showPrioritySupport: true,    // Priority support included
    showBulkAPI: false,
    showSSO: false,
    showWhiteLabel: false,
    showDedicatedSupport: false,
    showComplianceCertificates: false,

    departmentLabel: 'Account',
    tierDisplayName: 'Faculty Plus ($29/mo)',
  },

  [TIERS.INDIVIDUAL_PRO]: {
    // Navigation - limited access
    showIntegrations: false,      // No LMS integrations for individuals
    showBulkUpload: true,         // Bulk upload available
    showAdmin: false,             // No admin features

    // Settings
    showAIProviderSettings: false, // Uses our cloud infrastructure only
    showOllamaConfig: false,      // No self-hosted AI
    showCustomAPIKeys: false,     // Can't bring own API keys

    // Features
    maxFilesPerUpload: 10,
    showTeamFeatures: false,      // Solo user
    showDepartmentInfo: false,    // Show "Account" instead
    showQuotaBar: false,          // Unlimited scans

    // Scan Types Available (matches backend TIER_QUOTAS)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex', 'video', 'website', 'code', 'chart'],
    scanTypesExcluded: [],

    // Advanced Features
    showVideoProcessing: true,
    showAdvancedAnalytics: false,
    showPrioritySupport: true,
    showBulkAPI: true,
    showSSO: false,
    showWhiteLabel: false,
    showDedicatedSupport: false,
    showComplianceCertificates: false,

    departmentLabel: 'Account',
    tierDisplayName: 'Faculty Pro ($79/mo)',
  },

  [TIERS.TRIAL]: {
    // Navigation - limited access
    showIntegrations: true,       // Can try integrations
    showBulkUpload: true,         // Can try bulk upload
    showAdmin: false,             // No admin during trial

    // Settings - can explore but limited
    showAIProviderSettings: false, // Uses our cloud during trial
    showOllamaConfig: false,      // No self-hosted during trial
    showCustomAPIKeys: false,     // No custom keys during trial

    // Features
    maxFilesPerUpload: 10,
    showTeamFeatures: false,      // No team features during trial
    showDepartmentInfo: true,
    showQuotaBar: true,           // Trial has quotas

    // Scan Types Available (matches backend TIER_QUOTAS)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex', 'video', 'website', 'code', 'chart'],
    scanTypesExcluded: [],

    // Advanced Features (limited)
    showVideoProcessing: false,   // Not in trial
    showAdvancedAnalytics: false, // Not in trial
    showPrioritySupport: false,   // Standard support only
    showBulkAPI: false,           // Not in trial
    showSSO: false,               // Not in trial
    showWhiteLabel: false,        // Not in trial
    showDedicatedSupport: false,  // Not in trial
    showComplianceCertificates: false, // Not in trial

    departmentLabel: 'Department',
    tierDisplayName: 'Trial',
  },

  [TIERS.DEPARTMENT]: {
    // Navigation - full access
    showIntegrations: true,
    showBulkUpload: true,
    showAdmin: true,

    // Settings - can choose between our Gemini or our Ollama (privacy option)
    showAIProviderSettings: true,  // Can switch between Gemini/Ollama
    showOllamaConfig: true,        // Can use our hosted Ollama (privacy)
    showCustomAPIKeys: false,      // Can't bring own API keys (uses our infra)

    // Features
    maxFilesPerUpload: 50,
    showTeamFeatures: true,
    showDepartmentInfo: true,
    showQuotaBar: false,          // Unlimited scans

    // Scan Types Available (all types)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex', 'video', 'website', 'code', 'chart'],
    scanTypesExcluded: [],

    // Advanced Features (core paid features)
    showVideoProcessing: false,   // University+ only
    showAdvancedAnalytics: false, // University+ only
    showPrioritySupport: false,   // University+ only
    showBulkAPI: true,            // API access included
    showSSO: false,               // Enterprise only
    showWhiteLabel: false,        // Enterprise only
    showDedicatedSupport: false,  // Enterprise only
    showComplianceCertificates: true, // Can generate certificates

    departmentLabel: 'Department',
    tierDisplayName: 'Department',
  },

  [TIERS.UNIVERSITY]: {
    // Navigation - full access
    showIntegrations: true,
    showBulkUpload: true,
    showAdmin: true,

    // Settings - can choose between our Gemini or our Ollama (privacy option)
    showAIProviderSettings: true,  // Can switch between Gemini/Ollama
    showOllamaConfig: true,        // Can use our hosted Ollama (privacy)
    showCustomAPIKeys: false,      // Can't bring own API keys (uses our infra)

    // Features
    maxFilesPerUpload: 100,
    showTeamFeatures: true,
    showDepartmentInfo: true,
    showQuotaBar: false,

    // Scan Types Available (all types)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex', 'video', 'website', 'code', 'chart'],
    scanTypesExcluded: [],

    // Advanced Features (premium features)
    showVideoProcessing: true,    // Video/audio processing
    showAdvancedAnalytics: true,  // Historical trends, reports
    showPrioritySupport: true,    // Priority email support
    showBulkAPI: true,            // API access included
    showSSO: false,               // Enterprise only
    showWhiteLabel: false,        // Enterprise only
    showDedicatedSupport: false,  // Enterprise only
    showComplianceCertificates: true,

    departmentLabel: 'Department',
    tierDisplayName: 'University',
  },

  [TIERS.ENTERPRISE]: {
    // Navigation - full access
    showIntegrations: true,
    showBulkUpload: true,
    showAdmin: true,

    // Settings - Enterprise can self-host or use our cloud
    // Self-hosted deployments get full AI configuration
    showAIProviderSettings: true,  // Can configure providers (self-hosted option)
    showOllamaConfig: true,        // Can use local Ollama
    showCustomAPIKeys: true,       // Can bring own API keys

    // Features
    maxFilesPerUpload: -1,        // Unlimited
    showTeamFeatures: true,
    showDepartmentInfo: true,
    showQuotaBar: false,

    // Scan Types Available (all types)
    scanTypes: ['pdf', 'word', 'excel', 'powerpoint', 'image', 'latex', 'video', 'website', 'code', 'chart'],
    scanTypesExcluded: [],

    // Advanced Features (all features)
    showVideoProcessing: true,    // Video/audio processing
    showAdvancedAnalytics: true,  // Historical trends, reports
    showPrioritySupport: true,    // Priority support
    showBulkAPI: true,            // API access included
    showSSO: true,                // SAML/OAuth SSO
    showWhiteLabel: true,         // Custom branding
    showDedicatedSupport: true,   // Dedicated account manager
    showComplianceCertificates: true,

    departmentLabel: 'Organization',
    tierDisplayName: 'Enterprise',
  },
};

// Default features (fallback for unknown tiers)
const DEFAULT_FEATURES = TIER_FEATURES[TIERS.DEPARTMENT];

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Get feature flags for a given tier
 */
export function getFeatures(tier: Tier | TierKey | string): TierFeatures {
  return TIER_FEATURES[tier as TierKey] || DEFAULT_FEATURES;
}

/**
 * Check if a specific feature is enabled for a tier
 * Note: Only works for boolean features (show*, etc.)
 */
export function hasFeature(tier: Tier | TierKey | string, feature: FeatureKey): boolean {
  const features = getFeatures(tier);
  const value = features[feature];
  // Only return boolean features, default to false for non-boolean
  return typeof value === 'boolean' ? value : false;
}

/**
 * Check if the tier is a free/individual tier
 */
export function isFreeTier(tier: Tier | TierKey | string): boolean {
  return tier === TIERS.INDIVIDUAL_FREE;
}

/**
 * Check if the tier is an individual tier (free, plus, or pro)
 */
export function isIndividualTier(tier: Tier | TierKey | string): boolean {
  const individualTiers: string[] = [TIERS.INDIVIDUAL_FREE, TIERS.INDIVIDUAL_PLUS, TIERS.INDIVIDUAL_PRO];
  return individualTiers.includes(tier);
}

/**
 * Check if the tier has paid features
 */
export function isPaidTier(tier: Tier | TierKey | string): boolean {
  const paidTiers: string[] = [TIERS.DEPARTMENT, TIERS.UNIVERSITY, TIERS.ENTERPRISE];
  return paidTiers.includes(tier);
}

/**
 * Get the display name for a tier
 */
export function getTierDisplayName(tier: Tier | TierKey | string): string {
  return getFeatures(tier).tierDisplayName || tier;
}

/**
 * Check if a scan type is available for a tier
 */
export function hasScanType(tier: Tier | TierKey | string, scanType: string): boolean {
  const features = getFeatures(tier);
  const excluded = features.scanTypesExcluded || [];
  return !excluded.includes(scanType);
}

/**
 * Get all available scan types for a tier
 */
export function getAvailableScanTypes(tier: Tier | TierKey | string): string[] {
  const features = getFeatures(tier);
  return features.scanTypes || [];
}

/**
 * Get excluded scan types for a tier (for showing upgrade prompts)
 */
export function getExcludedScanTypes(tier: Tier | TierKey | string): string[] {
  const features = getFeatures(tier);
  return features.scanTypesExcluded || [];
}

/**
 * Get the label for "department" based on tier
 */
export function getDepartmentLabel(tier: Tier | TierKey | string): string {
  return getFeatures(tier).departmentLabel || 'Department';
}

/**
 * Get maximum files per upload for a tier
 * Returns -1 for unlimited
 */
export function getMaxFilesPerUpload(tier: Tier | TierKey | string): number {
  return getFeatures(tier).maxFilesPerUpload;
}
