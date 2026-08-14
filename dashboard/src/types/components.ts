/**
 * Component Props Types
 * Common prop types for React components
 */

import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import type {
  Issue,
  Scan,
  ScanType,
  CloudFolder,
  ComplianceTrendPoint,
  IssuesByCategory,
  WCAGCriteria,
} from './api';

// ============================================================================
// Common Props
// ============================================================================

export interface BaseProps {
  className?: string;
  children?: ReactNode;
}

export interface WithIcon {
  icon?: LucideIcon;
  iconSize?: number;
}

// ============================================================================
// Layout Components
// ============================================================================

export interface SidebarProps extends BaseProps {
  collapsed?: boolean;
  onToggle?: () => void;
}

export interface NavbarProps extends BaseProps {
  onMenuClick?: () => void;
}

// ============================================================================
// Auth Components
// ============================================================================

export interface ProtectedRouteProps extends BaseProps {
  requiredRole?: 'faculty' | 'admin' | 'super_admin';
  redirectTo?: string;
}

// ============================================================================
// Upload Components
// ============================================================================

export interface FileUploaderProps extends BaseProps {
  scanType: ScanType | string;
  onUploadStart?: () => void;
  onUploadComplete?: (scan: Scan) => void;
  onUploadError?: (error: Error) => void;
  maxFiles?: number;
  maxFileSize?: number;
  acceptedFileTypes?: string[];
}

export interface ScanTypeSelectorProps extends BaseProps {
  value?: string;
  onChange?: (scanType: string) => void;
  disabled?: boolean;
}

export interface WebsiteScannerProps extends BaseProps {
  onScanStart?: () => void;
  onScanComplete?: (scan: Scan) => void;
  onScanError?: (error: Error) => void;
}

// ============================================================================
// Results Components
// ============================================================================

export interface IssueListProps extends BaseProps {
  issues: Issue[];
  scanType?: ScanType | string;
  showFilters?: boolean;
  onIssueSelect?: (issue: Issue) => void;
}

export interface ComplianceScoreProps extends BaseProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
  animated?: boolean;
}

export interface EnhancedComplianceScoreProps extends ComplianceScoreProps {
  issuesSummary?: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
}

export interface CVDSimulationPreviewProps extends BaseProps {
  imageUrl: string;
  simulationType?: 'protanopia' | 'deuteranopia' | 'tritanopia' | 'achromatopsia';
}

export interface DownloadFixesProps extends BaseProps {
  scanId: string;
  hasAutoFixes?: boolean;
  disabled?: boolean;
}

export interface EngineComparisonStatsProps extends BaseProps {
  engines: Array<{
    name: string;
    issues: number;
    score?: number;
  }>;
}

// ============================================================================
// Chart Components
// ============================================================================

export interface IssuesByTypeChartProps extends BaseProps {
  data: IssuesByCategory[];
  height?: number;
}

export interface WCAGCriteriaChartProps extends BaseProps {
  data: WCAGCriteria[];
  height?: number;
}

export interface TrendGraphProps extends BaseProps {
  data: ComplianceTrendPoint[];
  height?: number;
  showLegend?: boolean;
}

// ============================================================================
// Analytics Components
// ============================================================================

export interface AnalyticsDashboardProps extends BaseProps {
  departmentId?: string;
  dateRange?: {
    start: Date;
    end: Date;
  };
}

// ============================================================================
// Integration Components
// ============================================================================

export interface FolderTreeProps extends BaseProps {
  folders: CloudFolder[];
  selectedFolders?: string[];
  onSelectionChange?: (selectedIds: string[]) => void;
  loading?: boolean;
}

export interface FolderSelectionModalProps extends BaseProps {
  isOpen: boolean;
  onClose: () => void;
  provider: string;
  onSave?: (folders: CloudFolder[]) => void;
}

// ============================================================================
// General Components
// ============================================================================

export interface QuotaBarProps extends BaseProps {
  used: number;
  limit: number;
  label?: string;
  showPercentage?: boolean;
  unlimited?: boolean;
}

export interface FeatureGateProps extends BaseProps {
  feature: string;
  fallback?: ReactNode;
}

export interface LogoProps extends BaseProps {
  size?: 'sm' | 'md' | 'lg';
  showText?: boolean;
}

export interface ThemeToggleProps extends BaseProps {
  size?: 'sm' | 'md' | 'lg';
}

export interface CookieBannerProps extends BaseProps {
  onAccept?: () => void;
  onReject?: () => void;
}
