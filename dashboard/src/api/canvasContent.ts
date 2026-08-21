import { get, post } from './client';

// ============================================================================
// Request Types
// ============================================================================

export interface CanvasContentScanRequest {
  course_id: string;
  department_id?: string;
  generate_alt_text?: boolean;
  auto_remediate?: boolean;
  detect_decorative?: boolean;
}

export interface BatchApproveRequest {
  cloud_file_ids: string[];
}

export interface BatchWritebackRequest {
  course_id: string;
}

// ============================================================================
// Response Types
// ============================================================================

export interface CanvasContentScanResponse {
  total_items: number;
  jobs_queued: number;
  skipped: number;
  by_type: Record<string, number>;
}

export interface ContentTypeStatus {
  content_type: string;
  total: number;
  scanned: number;
  average_compliance: number | null;
  issues: number;
}

export interface ContentItemStatus {
  cloud_file_id: string;
  provider_file_id: string | null;
  provider: string;
  provider_parent_id: string;
  title: string;
  content_type: string;
  compliance_score: number | null;
  issue_count: number;
  writeback_status: string | null;
  has_remediated_version: boolean;
  current_remediation_artifact_id: string | null;
  last_scanned_at: string | null;
  content_updated_at: string | null;
  scan_id: string | null;
}

export interface CourseContentStatusResponse {
  course_id: string;
  overall_compliance: number | null;
  by_type: ContentTypeStatus[];
  items: ContentItemStatus[];
}

/** A single real accessibility finding from the last scan's stored
 * axe-core violation. Every field is read straight off scan data —
 * never generated client-side. No per-issue fixed/remaining status:
 * that attribution isn't tracked for Canvas content — issues_fixed/
 * issues_remaining on ContentDiffResponse are aggregate-only. */
export interface ContentIssue {
  id: string;
  impact: string | null;
  description: string | null;
  help: string | null;
  wcag_tags: string[];
  nodes_affected: number;
}

export interface ContentDiffResponse {
  cloud_file_id: string;
  content_type: string;
  title: string;
  original_html: string;
  remediated_html: string;
  issues_fixed: number;
  issues_remaining: number;
  /** True when the two counts above come from a rescan of the remediated
   * copy. False means no rescan was recorded and the split is unknown, so
   * the UI must say unverified rather than show a confident zero. */
  issues_verified_by_rescan: boolean;
  issues: ContentIssue[];
}

export interface ContentActionResponse {
  success: boolean;
  message: string;
}

export interface BatchApproveResponse {
  approved_count: number;
  skipped_count: number;
  errors: string[];
}

export interface WritebackResponse {
  success: boolean;
  message: string;
}

export interface BatchWritebackResponse {
  written_count: number;
  failed_count: number;
  stale_count: number;
  // Items the server declined to attempt at all. Files are written back
  // now, so this is normally zero; a non-zero value still has to be shown
  // rather than folded into a success message.
  skipped_count: number;
  errors: string[];
}

export interface AuditLogEntry {
  id: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  written_back_at: string | null;
  rollback_status: string | null;
  rolled_back_at: string | null;
}

export interface AuditLogResponse {
  cloud_file_id: string;
  entries: AuditLogEntry[];
}

// ============================================================================
// Course Overview Types
// ============================================================================

export interface CourseOverviewItem {
  course_id: string;
  course_name: string;
  course_code: string | null;
  total_items: number;
  scanned_items: number;
  avg_compliance: number | null;
  total_issues: number;
  written_back: number;
  status: 'not_started' | 'critical' | 'at_risk' | 'on_track' | 'compliant';
}

export interface CourseOverviewResponse {
  total_courses: number;
  total_items: number;
  total_scanned: number;
  avg_compliance: number | null;
  total_issues: number;
  courses: CourseOverviewItem[];
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Get institution-wide compliance overview across all courses.
 * GET /canvas/content/overview
 */
export async function getCourseOverview(): Promise<CourseOverviewResponse> {
  return get<CourseOverviewResponse>('/canvas/content/overview');
}

/**
 * Scan all Canvas content in a course for accessibility issues.
 * POST /canvas/content/scan
 */
export async function scanCourseContent(
  request: CanvasContentScanRequest
): Promise<CanvasContentScanResponse> {
  return post<CanvasContentScanResponse, CanvasContentScanRequest>(
    '/canvas/content/scan',
    request
  );
}

/**
 * Scan a specific content type within a course.
 * POST /canvas/content/scan/{contentType}
 */
export async function scanContentType(
  contentType: string,
  request: CanvasContentScanRequest
): Promise<CanvasContentScanResponse> {
  return post<CanvasContentScanResponse, CanvasContentScanRequest>(
    `/canvas/content/scan/${encodeURIComponent(contentType)}`,
    request
  );
}

/**
 * Get the current accessibility status for all content in a course.
 * GET /canvas/content/courses/{courseId}/status
 */
export async function getCourseContentStatus(
  courseId: string
): Promise<CourseContentStatusResponse> {
  return get<CourseContentStatusResponse>(
    `/canvas/content/courses/${encodeURIComponent(courseId)}/status`
  );
}

/**
 * Get the diff between original and remediated content for review.
 * GET /canvas/content/{cloudFileId}/diff
 */
export async function getContentDiff(cloudFileId: string): Promise<ContentDiffResponse> {
  return get<ContentDiffResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/diff`
  );
}

/**
 * Approve remediated content for write-back to Canvas.
 * POST /canvas/content/{cloudFileId}/approve
 */
export async function approveContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/approve`
  );
}

/**
 * Reject remediated content and mark it for manual review.
 * POST /canvas/content/{cloudFileId}/reject
 */
export async function rejectContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/reject`
  );
}

/**
 * Approve multiple remediated items in a single request.
 * POST /canvas/content/batch-approve
 */
export async function batchApproveContent(
  request: BatchApproveRequest
): Promise<BatchApproveResponse> {
  return post<BatchApproveResponse, BatchApproveRequest>(
    '/canvas/content/batch-approve',
    request
  );
}

/**
 * Write approved remediated content back to Canvas.
 * POST /canvas/content/{cloudFileId}/writeback
 */
export async function writeBackContent(cloudFileId: string): Promise<WritebackResponse> {
  return post<WritebackResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/writeback`
  );
}

/**
 * Write back all approved content for a course in a single request.
 * POST /canvas/content/batch-writeback
 */
export async function batchWriteBack(
  request: BatchWritebackRequest
): Promise<BatchWritebackResponse> {
  return post<BatchWritebackResponse, BatchWritebackRequest>(
    '/canvas/content/batch-writeback',
    request
  );
}

/**
 * Roll back previously written content to its original version in Canvas.
 * POST /canvas/content/{cloudFileId}/rollback
 */
export async function rollbackContent(cloudFileId: string): Promise<ContentActionResponse> {
  return post<ContentActionResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/rollback`
  );
}

/**
 * Get the full audit log for a content item.
 * GET /canvas/content/{cloudFileId}/audit
 */
export async function getAuditLog(cloudFileId: string): Promise<AuditLogResponse> {
  return get<AuditLogResponse>(
    `/canvas/content/${encodeURIComponent(cloudFileId)}/audit`
  );
}
