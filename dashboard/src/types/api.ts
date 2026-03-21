/**
 * API Response Types
 * These types mirror the backend Pydantic models from backend/src/db/models.py
 */

// ============================================================================
// Enums
// ============================================================================

export type ScanType =
  | 'PDF'
  | 'POWERPOINT'
  | 'WORD'
  | 'EXCEL'
  | 'LATEX'
  | 'BATCH'
  | 'IMAGE'
  | 'VIDEO'
  | 'WEBSITE'
  | 'CODE';

export type ScanStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export type IssueStatus =
  | 'OPEN'
  | 'IN_PROGRESS'
  | 'RESOLVED'
  | 'WONT_FIX'
  | 'FALSE_POSITIVE';

export type IssuePriority = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

export type IssueSeverity = 'critical' | 'high' | 'medium' | 'low';

export type UserRole = 'faculty' | 'admin' | 'super_admin';

export type Tier =
  | 'individual_free'
  | 'individual_plus'
  | 'individual_pro'
  | 'trial'
  | 'department'
  | 'university'
  | 'enterprise';

export type CloudProvider =
  | 'GOOGLE'
  | 'MICROSOFT'
  | 'CANVAS'
  | 'BLACKBOARD'
  | 'MOODLE'
  | 'BRIGHTSPACE';

// ============================================================================
// Core Entities
// ============================================================================

export interface Department {
  id: string;
  name: string;
  institution: string;
  tier: Tier;
  contact_email: string;
  scans_this_month?: number;
  pages_this_month?: number;
  images_this_month?: number;
  max_scans_per_month?: number;
  max_pages_per_month?: number;
  max_images_per_month?: number;
  created_at?: string;
  updated_at?: string;
}

export interface User {
  id: string;
  email: string;
  name?: string;
  picture_url?: string;
  role: UserRole;
  department_id: string;
  department?: Department;
  created_at?: string;
  last_login?: string;
}

export interface Scan {
  id: string;
  scan_id?: string; // API sometimes uses this alias
  scan_type: ScanType;
  status: ScanStatus;
  file_name: string;
  file_path?: string;
  compliance_score?: number;
  progress?: number;
  progress_message?: string;
  issues_count?: number;
  critical_count?: number;
  high_count?: number;
  medium_count?: number;
  low_count?: number;
  created_at: string;
  completed_at?: string;
  department_id?: string;
  user_id?: string;
}

export interface Issue {
  id?: string;
  description: string;
  severity: IssueSeverity;
  category?: string;
  location?: string;
  wcag_criteria?: string;
  wcag_level?: string;
  can_auto_fix?: boolean;
  auto_fix_available?: boolean;
  fix_suggestion?: string;
  ai_fix?: string;
  status?: IssueStatus;
  priority?: IssuePriority;
  assigned_to?: string;
  notes?: string;
  element_html?: string;
  screenshot_url?: string;
}

export interface ScanResult {
  scan: Scan;
  issues: Issue[];
  compliance_score: number;
  summary?: ScanSummary;
}

export interface ScanSummary {
  total_issues: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  auto_fixable: number;
}

// ============================================================================
// API Responses
// ============================================================================

export interface ListScansResponse {
  scans: Scan[];
  total?: number;
  page?: number;
  page_size?: number;
}

export interface ScanDetailResult {
  compliance_score: number;
  wcag_level?: string;
  issues: Issue[];
  summary?: ScanSummary;
  structure?: Record<string, unknown>;
  suggestions?: unknown[];
  ocr_used?: boolean;
  ollama_used?: boolean;
  [key: string]: unknown;
}

export interface ScanDetailResponse extends Scan {
  scan_id?: string;
  result?: ScanDetailResult | null;
  issues: Issue[];
  compliance_score: number;
  summary?: ScanSummary;
}

export interface UploadResponse {
  scan_id: string;
  status: ScanStatus;
  message?: string;
}

export interface ScanProgressResponse {
  scan_id: string;
  status: ScanStatus;
  progress: number;
  progress_message?: string;
  compliance_score?: number;
  issues_count?: number;
  error_message?: string;
}

export interface RemediationResponse {
  success: boolean;
  download_url?: string;
  issues_fixed?: number;
  issues_remaining?: number;
  error?: string;
}

// ============================================================================
// Analytics & Dashboard
// ============================================================================

export interface DashboardStats {
  total_scans: number;
  scans_this_month: number;
  total_issues: number;
  issues_resolved: number;
  average_compliance_score: number;
  compliance_trend?: ComplianceTrendPoint[];
}

export interface ComplianceTrendPoint {
  date: string;
  score: number;
  scans?: number;
}

export interface IssuesByCategory {
  category: string;
  count: number;
  percentage?: number;
}

export interface WCAGCriteria {
  criteria: string;
  level: string;
  count: number;
  description?: string;
}

// ============================================================================
// Integrations
// ============================================================================

export interface IntegrationStatus {
  provider: CloudProvider;
  connected: boolean;
  email?: string;
  name?: string;
  last_sync?: string;
  error?: string;
}

export interface IntegrationsStatusResponse {
  google: IntegrationStatus | null;
  microsoft: IntegrationStatus | null;
  canvas_lti: IntegrationStatus | null;
  blackboard_lti: IntegrationStatus | null;
  moodle_lti: IntegrationStatus | null;
  brightspace_lti: IntegrationStatus | null;
}

export interface CloudFile {
  id: string;
  name: string;
  mime_type?: string;
  size?: number;
  modified_at?: string;
  path?: string;
  is_folder?: boolean;
  provider: CloudProvider;
}

export interface CloudFolder {
  id: string;
  name: string;
  path?: string;
  children?: CloudFolder[];
}

export interface SyncFolder {
  id: string;
  folder_id: string;
  folder_name: string;
  folder_path?: string;
  provider: CloudProvider;
  last_sync?: string;
}

// ============================================================================
// Billing & Quotas
// ============================================================================

export interface QuotaStatus {
  scans_used: number;
  scans_limit: number;
  pages_used: number;
  pages_limit: number;
  images_used: number;
  images_limit: number;
  reset_date: string;
  unlimited: boolean;
}

export interface BillingInfo {
  tier: Tier;
  status: 'active' | 'past_due' | 'canceled' | 'trialing';
  current_period_end?: string;
  cancel_at_period_end?: boolean;
}

// ============================================================================
// AI/LLM Providers
// ============================================================================

export interface LLMProvider {
  id: string;
  name: string;
  type: 'ollama' | 'openai' | 'anthropic' | 'gemini' | 'xai';
  enabled: boolean;
  is_default: boolean;
  model?: string;
  api_key_set?: boolean;
}

export interface LLMProviderConfig {
  provider_type: string;
  model: string;
  api_key?: string;
  base_url?: string;
  enabled: boolean;
}

// ============================================================================
// Auth
// ============================================================================

export interface SessionInfo {
  id: string;
  user_agent?: string;
  ip_address?: string;
  created_at: string;
  last_used: string;
  is_current: boolean;
}

export interface APIKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used?: string;
  expires_at?: string;
}

// ============================================================================
// Admin / Invitations
// ============================================================================

export type InvitationStatus = 'pending' | 'accepted' | 'expired' | 'revoked';

export interface Invitation {
  id: string;
  email: string;
  role: UserRole;
  status: InvitationStatus;
  invited_by: string;
  department_id: string;
  created_at: string;
  expires_at: string;
  accepted_at?: string;
}

// ============================================================================
// Utility Types
// ============================================================================

export interface APIError {
  detail: string;
  status_code?: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
