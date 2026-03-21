/**
 * Feature Access Types
 * Types for the tier-based feature gating system
 */

import type { Tier } from './api';

// ============================================================================
// Feature Flags
// ============================================================================

export interface FeatureFlags {
  // Navigation & UI
  showIntegrations: boolean;
  showBulkUpload: boolean;
  showAdmin: boolean;
  showTeamFeatures: boolean;
  showDepartmentInfo: boolean;
  showQuotaBar: boolean;

  // AI/LLM Features
  showAIProviderSettings: boolean;
  showOllamaConfig: boolean;
  showCustomAPIKeys: boolean;

  // Scan Types
  scanTypes: string[];
  scanTypesExcluded: string[];

  // Limits
  maxFilesPerUpload: number;
  maxFileSize: number; // in MB

  // Labels
  departmentLabel: string;
  tierDisplayName: string;
}

// ============================================================================
// Tier Configuration
// ============================================================================

export interface TierConfig {
  tier: Tier;
  displayName: string;
  features: FeatureFlags;
  quotas: TierQuotas;
}

export interface TierQuotas {
  scansPerMonth: number | 'unlimited';
  pagesPerMonth: number | 'unlimited';
  imagesPerMonth: number | 'unlimited';
  maxFileSize: number; // in MB
  maxFilesPerUpload: number;
}

// ============================================================================
// Scan Type Access
// ============================================================================

export type ScanTypeKey =
  | 'PDF'
  | 'POWERPOINT'
  | 'WORD'
  | 'EXCEL'
  | 'IMAGE'
  | 'LATEX'
  | 'VIDEO'
  | 'WEBSITE'
  | 'CODE';

export interface ScanTypeConfig {
  key: ScanTypeKey;
  label: string;
  description: string;
  icon: string;
  requiredTier: Tier;
  acceptedExtensions: string[];
}

// ============================================================================
// Feature Check Results
// ============================================================================

export interface FeatureCheckResult {
  allowed: boolean;
  reason?: string;
  requiredTier?: Tier;
  upgradeUrl?: string;
}

export interface ScanTypeCheckResult extends FeatureCheckResult {
  scanType: ScanTypeKey;
  locked: boolean;
  tierDisplayName?: string;
}
