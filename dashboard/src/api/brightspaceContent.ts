import { get, post } from './client';

// ============================================================================
// Request Types
// ============================================================================

export interface BrightspaceContentScanRequest {
  org_unit_id: number;
  scan_types?: string;
  module_id?: number;
}

export interface BatchApproveRequest {
  cloud_file_ids: string[];
}

export interface BatchWritebackRequest {
  org_unit_id: number;
}

export interface BatchRemediateRequest {
  org_unit_id: number;
  cloud_file_ids: string[];
  use_ai?: boolean;
  generate_alt_text?: boolean;
}

// ============================================================================
// Response Types
// ============================================================================

export interface BrightspaceContentScanResponse {
  total_items: number;
  jobs_queued: number;
  skipped: number;
}

export interface ContentItemStatus {
  cloud_file_id: string;
  title: string;
  content_type: string;
  compliance_score: number | null;
  issue_count: number;
  writeback_status: string | null;
  has_remediated_version: boolean;
  approval_eligible: boolean;
  remediation_origin: 'automatic' | 'manual' | null;
  module_path: string;
}

export interface CourseContentStatusResponse {
  org_unit_id: number;
  total_items: number;
  scanned_items: number;
  average_compliance: number | null;
  items: ContentItemStatus[];
}

export interface ContentDiffResponse {
  cloud_file_id: string;
  content_type: string;
  title: string;
  original_html: string;
  remediated_html: string;
  issues_fixed: number;
  issues_remaining: number;
}

export interface ContentActionResponse {
  success: boolean;
  message: string;
}

export interface BatchApproveOutcome {
  cloud_file_id: string;
  status: 'approved' | 'skipped' | 'failed';
  reason: string | null;
}

export interface BatchApproveResponse {
  requested_count: number;
  approved_count: number;
  skipped_count: number;
  failed_count: number;
  outcomes: BatchApproveOutcome[];
  errors: string[];
}

export interface BatchWritebackResponse {
  written_count: number;
  failed_count: number;
  stale_count: number;
}

export interface RemediateResponse {
  cloud_file_id: string;
  status: 'completed' | 'manual_required' | 'no_op' | 'failed';
  fixed_count: number;
  manual_count: number;
  failed_count: number;
  skipped_count: number;
  has_remediated_version: boolean;
  ai_used: boolean;
  external_ai_used: boolean;
  providers: string[];
  purpose_decisions: Record<string, string>;
  error_code?: string | null;
}

export interface BatchRemediateResponse {
  status: 'completed';
  requested_count: number;
  completed_count: number;
  manual_count: number;
  failed_count: number;
  fixed_count: number;
  results: RemediateResponse[];
}

export interface AuditLogEntry {
  id: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  written_back_at: string | null;
}

export interface AuditLogResponse {
  cloud_file_id: string;
  entries: AuditLogEntry[];
}

export interface ScannableItem {
  topic_id: number;
  org_unit_id: number;
  title: string;
  content_type: string;
  file_name: string | null;
  module_path: string;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Scan all Brightspace content in a course for accessibility issues.
 * POST /brightspace/content/scan
 */
export async function scanCourseContent(
  request: BrightspaceContentScanRequest
): Promise<BrightspaceContentScanResponse> {
  return post<BrightspaceContentScanResponse, BrightspaceContentScanRequest>(
    '/brightspace/content/scan',
    request
  );
}

/**
 * Get the current accessibility status for all content in a course.
 * GET /brightspace/content/courses/{orgUnitId}/status
 */
export async function getCourseContentStatus(
  orgUnitId: number
): Promise<CourseContentStatusResponse> {
  return get<CourseContentStatusResponse>(
    `/brightspace/content/courses/${encodeURIComponent(orgUnitId)}/status`
  );
}

/**
 * List all files in a Brightspace course.
 * GET /brightspace/courses/{orgUnitId}/files
 */
export async function listCourseFiles(orgUnitId: number): Promise<ScannableItem[]> {
  return get<ScannableItem[]>(
    `/brightspace/courses/${encodeURIComponent(orgUnitId)}/files`
  );
}

/**
 * Get the diff between original and remediated content for review.
 * GET /brightspace/content/{cloudFileId}/diff
 */
export async function getContentDiff(cloudFileId: string): Promise<ContentDiffResponse> {
  return get<ContentDiffResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/diff`
  );
}

/**
 * Approve remediated content for write-back to Brightspace.
 * POST /brightspace/content/{cloudFileId}/approve
 */
export async function approveContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/approve`
  );
}

/**
 * Reject remediated content and mark it for manual review.
 * POST /brightspace/content/{cloudFileId}/reject
 */
export async function rejectContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/reject`
  );
}

/**
 * Write approved remediated content back to Brightspace.
 * POST /brightspace/content/{cloudFileId}/writeback
 */
export async function writeBackContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/writeback`
  );
}

/**
 * Approve multiple remediated items in a single request.
 * POST /brightspace/content/batch-approve
 */
export async function batchApproveContent(
  request: BatchApproveRequest
): Promise<BatchApproveResponse> {
  return post<BatchApproveResponse, BatchApproveRequest>(
    '/brightspace/content/batch-approve',
    request
  );
}

/**
 * Write back all approved content for a course in a single request.
 * POST /brightspace/content/batch-writeback
 */
export async function batchWriteBack(
  request: BatchWritebackRequest
): Promise<BatchWritebackResponse> {
  return post<BatchWritebackResponse, BatchWritebackRequest>(
    '/brightspace/content/batch-writeback',
    request
  );
}

/**
 * Remediate a single content item's accessibility issues.
 * POST /brightspace/content/{cloudFileId}/remediate
 */
export async function remediateContent(cloudFileId: string): Promise<RemediateResponse> {
  return post<RemediateResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/remediate`
  );
}

/**
 * Remediate all scanned content items with issues for a course.
 * POST /brightspace/content/batch-remediate
 */
export async function batchRemediateContent(
  request: BatchRemediateRequest
): Promise<BatchRemediateResponse> {
  return post<BatchRemediateResponse, BatchRemediateRequest>(
    '/brightspace/content/batch-remediate',
    request
  );
}

/**
 * Roll back previously written content to its original version in Brightspace.
 * POST /brightspace/content/{cloudFileId}/rollback
 */
export async function rollbackContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/rollback`
  );
}

/**
 * Roll back all written-back content for a course to original versions.
 * POST /brightspace/content/batch-rollback
 */
export async function batchRollback(
  request: { org_unit_id: number }
): Promise<{ rolled_back_count: number; failed_count: number }> {
  return post<{ rolled_back_count: number; failed_count: number }, { org_unit_id: number }>(
    '/brightspace/content/batch-rollback',
    request
  );
}

/**
 * Get the full audit log for a content item.
 * GET /brightspace/content/{cloudFileId}/audit
 */
export async function getAuditLog(cloudFileId: string): Promise<AuditLogResponse> {
  return get<AuditLogResponse>(
    `/brightspace/content/${encodeURIComponent(cloudFileId)}/audit`
  );
}
