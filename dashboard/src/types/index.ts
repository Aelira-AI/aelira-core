/**
 * Type Definitions Index
 * Re-exports all types for convenient importing
 *
 * Usage:
 *   import type { User, Scan, Issue } from '@/types';
 *   import type { AuthContextType } from '@/types';
 */

// API Types - Backend model mirrors
export type {
  // Enums
  ScanType,
  ScanStatus,
  IssueStatus,
  IssuePriority,
  IssueSeverity,
  UserRole,
  Tier,
  CloudProvider,
  // Core Entities
  Department,
  User,
  Scan,
  Issue,
  ScanResult,
  ScanSummary,
  // API Responses
  ListScansResponse,
  ScanDetailResponse,
  UploadResponse,
  ScanProgressResponse,
  RemediationResponse,
  // Analytics
  DashboardStats,
  ComplianceTrendPoint,
  IssuesByCategory,
  WCAGCriteria,
  // Integrations
  IntegrationStatus,
  IntegrationsStatusResponse,
  CloudFile,
  CloudFolder,
  SyncFolder,
  // Billing
  QuotaStatus,
  BillingInfo,
  // AI/LLM
  LLMProvider,
  LLMProviderConfig,
  // Auth
  SessionInfo,
  APIKey,
  // Admin / Invitations
  InvitationStatus,
  Invitation,
  // Utility
  APIError,
  PaginatedResponse,
} from './api';

// Feature Access Types
export type {
  FeatureFlags,
  TierConfig,
  TierQuotas,
  ScanTypeKey,
  ScanTypeConfig,
  FeatureCheckResult,
  ScanTypeCheckResult,
} from './features';

// Context Types
export type {
  AuthMethod,
  AuthState,
  LoginResult,
  AuthContextType,
  Theme,
  ThemeContextType,
  ToastType,
  Toast,
  ToastOptions,
  ToastContextType,
} from './context';

// Component Props Types
export type {
  BaseProps,
  WithIcon,
  SidebarProps,
  NavbarProps,
  ProtectedRouteProps,
  FileUploaderProps,
  ScanTypeSelectorProps,
  WebsiteScannerProps,
  IssueListProps,
  ComplianceScoreProps,
  EnhancedComplianceScoreProps,
  CVDSimulationPreviewProps,
  DownloadFixesProps,
  EngineComparisonStatsProps,
  IssuesByTypeChartProps,
  WCAGCriteriaChartProps,
  TrendGraphProps,
  AnalyticsDashboardProps,
  FolderTreeProps,
  FolderSelectionModalProps,
  QuotaBarProps,
  FeatureGateProps,
  LogoProps,
  ThemeToggleProps,
  CookieBannerProps,
} from './components';
