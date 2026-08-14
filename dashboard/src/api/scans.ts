import { apiClient } from './client';
import type {
  Scan,
  ScanDetailResponse,
  ScanProgressResponse,
  Issue,
  IssueStatus,
  DashboardStats,
  ComplianceTrendPoint,
} from '../types';

// ============================================================================
// Request/Response Types
// ============================================================================

export interface UploadOptions {
  // Image analysis options
  generate_alt_text?: boolean;
  enhance_descriptions?: boolean;
  validate_alt_text?: boolean;
  comprehensive_analysis?: boolean;
  detect_decorative?: boolean;
  detail_level?: 'brief' | 'standard' | 'detailed';
  // Multimedia/Video options
  generate_audio_descriptions?: boolean;
  generate_spoken_descriptions?: boolean;
  detect_flashing?: boolean;
  generate_transcript?: boolean;
  // Remediation options
  auto_remediate?: boolean;
  latex_formats?: string[];
}

export interface ListScansFilters {
  status?: string;
  scan_type?: string;
  limit?: number;
  offset?: number;
}

export interface IssueFilters {
  status?: IssueStatus;
  severity?: string;
  assigned_to?: string;
  limit?: number;
  offset?: number;
}

export interface ReportOptions {
  include_ai_recommendations?: boolean;
  include_trends?: boolean;
  include_issues?: boolean;
}

export interface RemediationOptions {
  use_ai?: boolean;
  verify_fixes?: boolean;
  latex_formats?: string[];
}

export interface UploadResponse {
  scan_id: string;
  status: string;
  message?: string;
  id?: string;
  progress?: number;
  progress_message?: string;
  [key: string]: unknown;
}

export interface TrendAnalysis {
  current_period: {
    avg_score: number;
    scan_count: number;
    issues_found: number;
  };
  previous_period: {
    avg_score: number;
    scan_count: number;
    issues_found: number;
  };
  change: {
    score_change: number;
    scan_change: number;
    issues_change: number;
  };
}

export interface DeadlineProjection {
  current_score: number;
  target_score: number;
  projected_date: string | null;
  on_track: boolean;
  recommendations: string[];
}

export interface IssueStats {
  total: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  resolution_rate: number;
}

export interface CertificateEligibility {
  eligible: boolean;
  current_score: number;
  required_score: number;
  missing_requirements: string[];
}

export interface RemediationResult {
  scan_id: string;
  status: string;
  issues_fixed: number;
  issues_remaining: number;
  download_url?: string;
  fixed_count?: number;
  total_issues?: number;
  fixed_issues?: { description: string }[];
  manual_issues?: { description: string }[];
  failed_issues?: { description: string }[];
  output_file?: string;
  original_compliance_score?: number;
  remediated_compliance_score?: number;
  // API returns these names (from remediation_routes.py)
  original_score?: number;
  remediated_score?: number;
}

export interface BatchRemediationResult {
  job_id: string;
  scan_ids: string[];
  status: string;
}

export interface AvailableFormat {
  format: string;
  filename: string;
  size_bytes: number;
  download_url: string;
}

export interface AvailableFormatsResponse {
  scan_id: string;
  scan_type: string;
  available_formats: AvailableFormat[];
}

export interface DepartmentReviewSummary {
  total_documents: number;
  reviewed_percent: number;
  approved_count: number;
  pending_count: number;
  rejected_count: number;
  avg_confidence: number;
}

// ============================================================================
// API Methods
// ============================================================================

export const scansApi = {
  /**
   * Upload file for scanning
   */
  uploadFile: async (
    file: File,
    type: string,
    options: UploadOptions = {}
  ): Promise<UploadResponse> => {
    const formData = new FormData();
    formData.append('file', file);

    // Build query params from options
    const params = new URLSearchParams();
    if (options.generate_alt_text !== undefined) {
      params.append('generate_alt_text', String(options.generate_alt_text));
    }
    if (options.enhance_descriptions !== undefined) {
      params.append('enhance_descriptions', String(options.enhance_descriptions));
    }
    if (options.validate_alt_text !== undefined) {
      params.append('validate_alt_text', String(options.validate_alt_text));
    }
    if (options.comprehensive_analysis !== undefined) {
      params.append('comprehensive_analysis', String(options.comprehensive_analysis));
    }
    if (options.detect_decorative !== undefined) {
      params.append('detect_decorative', String(options.detect_decorative));
    }
    if (options.detail_level !== undefined) {
      params.append('detail_level', options.detail_level);
    }
    // Multimedia/Video options
    if (options.generate_audio_descriptions !== undefined) {
      params.append('generate_audio_descriptions', String(options.generate_audio_descriptions));
    }
    if (options.generate_spoken_descriptions !== undefined) {
      params.append('generate_spoken_descriptions', String(options.generate_spoken_descriptions));
    }
    if (options.detect_flashing !== undefined) {
      params.append('detect_flashing', String(options.detect_flashing));
    }
    if (options.generate_transcript !== undefined) {
      params.append('generate_transcript', String(options.generate_transcript));
    }

    // Handle special endpoints for image analysis types
    let url: string;
    if (type === 'chart') {
      // Chart descriptions use the describe-chart endpoint
      formData.append('detail_level', options.detail_level || 'standard');
      url = '/education/image/describe-chart';
    } else if (type === 'image' && options.comprehensive_analysis) {
      // Comprehensive image analysis
      url = '/education/image/analyze-comprehensive';
    } else {
      // Standard scan endpoints
      url = `/education/${type}/scan${params.toString() ? '?' + params.toString() : ''}`;
    }

    const response = await apiClient.post<UploadResponse>(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000, // 120 seconds for AI analysis
    });

    return response.data;
  },

  /**
   * Get scan status and details.
   * Backend returns { success, scan: { ... } } — unwrap here.
   */
  getScan: async (scanId: string): Promise<ScanDetailResponse> => {
    const response = await apiClient.get(`/education/scans/${scanId}`);
    const data = response.data;
    // Backend wraps the scan in { success, scan: { ... } }
    return (data as Record<string, unknown>).scan
      ? ((data as Record<string, unknown>).scan as ScanDetailResponse)
      : (data as ScanDetailResponse);
  },

  /**
   * List all scans with optional filters
   */
  listScans: async (filters: ListScansFilters = {}): Promise<{ scans: Scan[]; total?: number }> => {
    const response = await apiClient.get<{ scans: Scan[]; total?: number }>('/education/scans', {
      params: filters,
    });
    return response.data;
  },

  /**
   * Delete a scan
   */
  deleteScan: async (scanId: string): Promise<void> => {
    await apiClient.delete(`/education/scans/${scanId}`);
  },

  /**
   * Download remediated file (HTML output)
   */
  downloadFix: async (scanId: string): Promise<Blob> => {
    const response = await apiClient.get<Blob>(`/education/scans/${scanId}/html`, {
      responseType: 'blob',
    });
    return response.data;
  },

  /**
   * Get scan progress (for polling during upload)
   */
  getScanProgress: async (scanId: string): Promise<ScanProgressResponse> => {
    const response = await apiClient.get<ScanProgressResponse>(
      `/education/scans/${scanId}/progress`,
      {
        timeout: 5000, // 5 seconds - this is just a DB query, should be instant
      }
    );
    return response.data;
  },

  /**
   * Download scan report (HTML report with results and recommendations)
   */
  downloadReport: async (scanId: string): Promise<Blob> => {
    const response = await apiClient.get<Blob>(`/education/scans/${scanId}/report`, {
      responseType: 'blob',
    });
    return response.data;
  },

  /**
   * Get department compliance stats
   */
  getDepartmentStats: async (departmentId: string): Promise<DashboardStats> => {
    const response = await apiClient.get<DashboardStats>(
      `/education/compliance/${departmentId}/stats`
    );
    return response.data;
  },

  /**
   * Get department priority issues
   */
  getPriorityIssues: async (departmentId: string, limit: number = 10): Promise<Issue[]> => {
    const response = await apiClient.get<Issue[]>(
      `/education/compliance/${departmentId}/issues`,
      {
        params: { limit },
      }
    );
    return response.data;
  },

  /**
   * Get general education stats (if no department ID)
   */
  getGeneralStats: async (): Promise<DashboardStats> => {
    const response = await apiClient.get<DashboardStats>('/education/stats');
    return response.data;
  },

  /**
   * Get compliance trend data (30-day history)
   */
  getComplianceTrend: async (
    departmentId: string,
    days: number = 30
  ): Promise<ComplianceTrendPoint[]> => {
    const response = await apiClient.get<ComplianceTrendPoint[]>(
      `/education/compliance/${departmentId}/trend`,
      {
        params: { days },
      }
    );
    return response.data;
  },

  // ==================== Analytics & Historical Trending ====================

  /**
   * Get historical trend from snapshots
   */
  getHistoricalTrend: async (
    departmentId: string,
    days: number = 30
  ): Promise<ComplianceTrendPoint[]> => {
    const response = await apiClient.get<ComplianceTrendPoint[]>(
      `/analytics/trend/${departmentId}`,
      {
        params: { days },
      }
    );
    return response.data;
  },

  /**
   * Get trend analysis comparing periods
   */
  getTrendAnalysis: async (
    departmentId: string,
    currentPeriod: number = 7,
    comparisonPeriod: number = 7
  ): Promise<TrendAnalysis> => {
    const response = await apiClient.get<TrendAnalysis>(
      `/analytics/trend/${departmentId}/analysis`,
      {
        params: { current_period: currentPeriod, comparison_period: comparisonPeriod },
      }
    );
    return response.data;
  },

  /**
   * Get deadline projection
   */
  getDeadlineProjection: async (departmentId: string): Promise<DeadlineProjection> => {
    const response = await apiClient.get<DeadlineProjection>(
      `/analytics/projection/${departmentId}`
    );
    return response.data;
  },

  /**
   * Capture daily snapshot (admin only)
   */
  captureSnapshot: async (departmentId: string): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>(
      `/analytics/snapshots/capture/${departmentId}`
    );
    return response.data;
  },

  // ==================== Issue Tracking ====================

  /**
   * Get tracked issues for department
   */
  getTrackedIssues: async (
    departmentId: string,
    filters: IssueFilters = {}
  ): Promise<{ issues: Issue[]; total: number }> => {
    const params = new URLSearchParams();
    if (filters.status) params.append('status', filters.status);
    if (filters.severity) params.append('severity', filters.severity);
    if (filters.assigned_to) params.append('assigned_to', filters.assigned_to);
    if (filters.limit) params.append('limit', String(filters.limit));
    if (filters.offset) params.append('offset', String(filters.offset));

    const response = await apiClient.get<{ issues: Issue[]; total: number }>(
      `/analytics/issues/${departmentId}${params.toString() ? '?' + params.toString() : ''}`
    );
    return response.data;
  },

  /**
   * Get issue statistics
   */
  getIssueStats: async (departmentId: string): Promise<IssueStats> => {
    const response = await apiClient.get<IssueStats>(`/analytics/issues/${departmentId}/stats`);
    return response.data;
  },

  /**
   * Update issue status
   */
  updateIssueStatus: async (
    issueId: string,
    status: IssueStatus,
    notes: string | null = null,
    method: string | null = null
  ): Promise<Issue> => {
    const response = await apiClient.patch<Issue>(`/analytics/issues/${issueId}/status`, {
      status,
      resolution_notes: notes,
      resolution_method: method,
    });
    return response.data;
  },

  /**
   * Assign issue to user
   */
  assignIssue: async (issueId: string, assignedTo: string): Promise<Issue> => {
    const response = await apiClient.post<Issue>(`/analytics/issues/${issueId}/assign`, {
      assigned_to: assignedTo,
    });
    return response.data;
  },

  /**
   * Add note to issue
   */
  addIssueNote: async (issueId: string, note: string): Promise<Issue> => {
    const response = await apiClient.post<Issue>(`/analytics/issues/${issueId}/note`, {
      note,
    });
    return response.data;
  },

  /**
   * Bulk update issues
   */
  bulkUpdateIssues: async (
    issueIds: string[],
    status: IssueStatus
  ): Promise<{ updated: number }> => {
    const response = await apiClient.post<{ updated: number }>('/analytics/issues/bulk-update', {
      issue_ids: issueIds,
      status,
    });
    return response.data;
  },

  // ==================== Compliance Reports & Certificates ====================

  /**
   * Generate compliance report (PDF)
   */
  generateComplianceReport: async (
    departmentId: string,
    options: ReportOptions = {}
  ): Promise<Blob> => {
    const params = new URLSearchParams();
    if (options.include_ai_recommendations !== undefined) {
      params.append('include_ai_recommendations', String(options.include_ai_recommendations));
    }
    if (options.include_trends !== undefined) {
      params.append('include_trends', String(options.include_trends));
    }
    if (options.include_issues !== undefined) {
      params.append('include_issues', String(options.include_issues));
    }

    const response = await apiClient.get<Blob>(
      `/analytics/report/${departmentId}${params.toString() ? '?' + params.toString() : ''}`,
      {
        responseType: 'blob',
        timeout: 120000, // 2 minutes for AI recommendations
      }
    );
    return response.data;
  },

  /**
   * Check certificate eligibility
   */
  checkCertificateEligibility: async (departmentId: string): Promise<CertificateEligibility> => {
    const response = await apiClient.get<CertificateEligibility>(
      `/analytics/certificate/${departmentId}/eligibility`
    );
    return response.data;
  },

  /**
   * Generate compliance certificate (PDF)
   */
  generateCertificate: async (departmentId: string): Promise<Blob> => {
    const response = await apiClient.get<Blob>(`/analytics/certificate/${departmentId}`, {
      responseType: 'blob',
      timeout: 30000,
    });
    return response.data;
  },

  // ==================== Auto-Remediation ====================

  /**
   * Remediate a scan (auto-fix accessibility issues)
   */
  remediateScan: async (
    scanId: string,
    options: RemediationOptions = {}
  ): Promise<RemediationResult> => {
    // Build query params for simple options
    const params = new URLSearchParams();
    if (options.use_ai !== undefined) {
      params.append('use_ai', String(options.use_ai));
    }
    if (options.verify_fixes !== undefined) {
      params.append('verify_fixes', String(options.verify_fixes));
    }

    // Build request body for complex options (latex_formats, etc.)
    const body: { latex_formats?: string[] } = {};
    if (options.latex_formats && options.latex_formats.length > 0) {
      body.latex_formats = options.latex_formats;
    }

    const response = await apiClient.post<RemediationResult>(
      `/education/remediate/${scanId}${params.toString() ? '?' + params.toString() : ''}`,
      Object.keys(body).length > 0 ? body : {},
      { timeout: 300000 } // 5 minutes for remediation
    );
    return response.data;
  },

  /**
   * Download remediated file
   */
  downloadRemediated: async (scanId: string, format?: string): Promise<Blob> => {
    const url = format
      ? `/education/scans/${scanId}/remediated?format=${encodeURIComponent(format)}`
      : `/education/scans/${scanId}/remediated`;
    const response = await apiClient.get<Blob>(url, {
      responseType: 'blob',
    });
    return response.data;
  },

  /**
   * Get available remediated formats for a scan
   */
  getRemediatedFormats: async (scanId: string): Promise<AvailableFormatsResponse> => {
    const response = await apiClient.get<AvailableFormatsResponse>(
      `/education/scans/${scanId}/remediated/formats`
    );
    return response.data;
  },

  /**
   * Batch remediate multiple scans
   */
  batchRemediate: async (
    scanIds: string[],
    options: RemediationOptions = {}
  ): Promise<BatchRemediationResult> => {
    const params = new URLSearchParams();
    if (options.use_ai !== undefined) {
      params.append('use_ai', String(options.use_ai));
    }

    const response = await apiClient.post<BatchRemediationResult>(
      `/education/remediate/batch${params.toString() ? '?' + params.toString() : ''}`,
      scanIds,
      { timeout: 60000 } // 1 minute for batch queue
    );
    return response.data;
  },

  // ==================== Review Summary ====================

  /**
   * Get department review summary (total docs, reviewed %, counts, avg confidence)
   */
  getDepartmentReviewSummary: async (): Promise<DepartmentReviewSummary> => {
    const response = await apiClient.get<DepartmentReviewSummary>(
      '/api/reviews/department-summary'
    );
    return response.data;
  },
};

export default scansApi;
