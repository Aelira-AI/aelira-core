/**
 * Tier-based feature access configuration
 *
 * Aelira Core has no pricing tiers — everything is free. The two tiers here
 * are workspace shapes, not plans: "individual" is a personal single-user
 * workspace, "department" is a shared multi-user one. Flags that differ
 * between them are structural (team features need a team), never paywalls.
 * This mirrors the backend TIER_QUOTAS in src/config/settings.py.
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
  showBulkAPI: boolean;
  showEvidenceReports: boolean;

  // Labels
  departmentLabel: string;
  tierDisplayName: string;
}

export type FeatureKey = keyof TierFeatures;

// ============================================================================
// Tier Constants
// ============================================================================

export const TIERS = {
  INDIVIDUAL: 'individual',
  DEPARTMENT: 'department',
} as const;

export type TierKey = (typeof TIERS)[keyof typeof TIERS];

const ALL_SCAN_TYPES = [
  'pdf',
  'word',
  'excel',
  'powerpoint',
  'image',
  'latex',
  'video',
  'website',
  'code',
  'chart',
];

// ============================================================================
// Feature Configuration
// ============================================================================

const TIER_FEATURES: Record<TierKey, TierFeatures> = {
  [TIERS.INDIVIDUAL]: {
    // Navigation — solo workspace, so no team/admin surfaces
    showIntegrations: true,
    showBulkUpload: true,
    showAdmin: false,

    // Settings — full AI provider control, like any self-hosted user
    showAIProviderSettings: true,
    showOllamaConfig: true,
    showCustomAPIKeys: true,

    // Features
    maxFilesPerUpload: 50,
    showTeamFeatures: false, // Solo user
    showDepartmentInfo: false, // Show "Account" instead
    showQuotaBar: false, // Unlimited by default

    // Scan Types — everything (matches backend TIER_QUOTAS)
    scanTypes: ALL_SCAN_TYPES,
    scanTypesExcluded: [],

    // Advanced Features — everything
    showVideoProcessing: true,
    showBulkAPI: true,
    showEvidenceReports: true,

    // Labels
    departmentLabel: 'Account',
    tierDisplayName: 'Individual',
  },

  [TIERS.DEPARTMENT]: {
    // Navigation — full access
    showIntegrations: true,
    showBulkUpload: true,
    showAdmin: true,

    // Settings — full AI provider control
    showAIProviderSettings: true,
    showOllamaConfig: true,
    showCustomAPIKeys: true,

    // Features
    maxFilesPerUpload: 50,
    showTeamFeatures: true,
    showDepartmentInfo: true,
    showQuotaBar: false, // Unlimited by default

    // Scan Types — everything (matches backend TIER_QUOTAS)
    scanTypes: ALL_SCAN_TYPES,
    scanTypesExcluded: [],

    // Advanced Features — everything
    showVideoProcessing: true,
    showBulkAPI: true,
    showEvidenceReports: true,

    // Labels
    departmentLabel: 'Department',
    tierDisplayName: 'Department',
  },
};

// Default features (fallback for unknown/legacy tier values)
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
 * Check if the tier is an individual (single-user) workspace
 */
export function isIndividualTier(tier: Tier | TierKey | string): boolean {
  // startsWith tolerates legacy tier values in existing databases
  return typeof tier === 'string' && tier.startsWith('individual');
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
 * Get excluded scan types for a tier
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
