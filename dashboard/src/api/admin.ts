import { apiClient } from './client';
import type { User, UserRole, Invitation, InvitationStatus } from '../types';

// ============================================================================
// Request/Response Types
// ============================================================================

export interface UsersListResponse {
  users: User[];
  total?: number;
}

export interface InviteUserRequest {
  email: string;
  role: UserRole;
}

export interface InviteUserResponse {
  invitation_id: string;
  email: string;
  status: string;
  message?: string;
}

export interface UpdateRoleResponse {
  user_id: string;
  role: UserRole;
  message?: string;
}

export interface InvitationsListResponse {
  invitations: Invitation[];
  total?: number;
}

export interface DepartmentStats {
  total_users: number;
  total_scans: number;
  historical_scan_count: number;
  enrolled_document_count: number;
  verified_document_count: number;
  unverified_document_count: number;
  total_issues: number;
  avg_compliance_score: number | null;
  scans_this_month: number;
  issues_resolved: number;
  active_users: number;
}

export interface ExportOptions {
  date_from?: string;
  date_to?: string;
}

export interface BulkExportOptions extends ExportOptions {
  include_pdfs?: boolean;
  include_evidence_report?: boolean;
}

export type LMSAIProvider = 'ollama' | 'gemini' | 'openai' | 'anthropic' | 'xai';

export interface LMSAIProviderReadiness {
  ready: boolean;
  reason: string;
  locality: 'local' | 'remote';
  credential_source: 'local' | 'department_byok' | 'platform' | null;
}

export interface LMSAIPolicy {
  schema_version: 1;
  policy_revision: number;
  enabled: boolean;
  provider: LMSAIProvider | null;
  remediation_enabled: boolean;
  alt_text_enabled: boolean;
  pilot_gemini_approved: boolean;
  provider_readiness: Record<LMSAIProvider, LMSAIProviderReadiness>;
}

export interface LMSAIPolicyUpdate {
  enabled: boolean;
  provider: LMSAIProvider | null;
  remediation_enabled: boolean;
  alt_text_enabled: boolean;
  expected_revision: number;
}

export type WorkerHealthState =
  | 'worker_unavailable'
  | 'expired_lease'
  | 'stuck_processing'
  | 'healthy_processing'
  | 'stuck_runnable_backlog'
  | 'healthy_advancing'
  | 'healthy_idle';

export interface WorkerStatusResponse {
  generated_at: string;
  status: 'healthy' | 'degraded';
  health_state: WorkerHealthState;
  queue: {
    pending: number;
    processing: number;
    completed: number;
    failed: number;
  };
  workers: {
    live: number;
    draining: number;
    latest_heartbeat_at: string | null;
    latest_heartbeat_age_seconds: number | null;
  };
  progress: {
    jobs_claimed: number;
    jobs_completed: number;
    jobs_failed: number;
    oldest_pending_created_at: string | null;
    oldest_pending_age_seconds: number | null;
    oldest_processing_heartbeat_at: string | null;
    oldest_running_job_age_seconds: number | null;
    runnable_pending: number;
    expired_processing: number;
    stalled_processing: number;
    latest_progress_at: string | null;
    latest_progress_age_seconds: number | null;
  };
  maintenance: {
    artifact_cleanup_due: number;
  };
  weekly_summary_scheduler: {
    state: 'not_started' | 'healthy' | 'stale' | 'failed';
    last_success_at: string | null;
    last_success_age_seconds: number | null;
    last_error_code: 'weekly_summary_scheduler_failed' | null;
  };
  reconciliation: {
    required: number;
    manual_required: number;
    failed_manual: number;
  };
  orphans: {
    pending_move: number;
    quarantined: number;
    restore_required: number;
    reviewed: number;
    purging: number;
  };
}

// ============================================================================
// API Methods
// ============================================================================

export const adminApi = {
  getWorkerStatus: async (): Promise<WorkerStatusResponse> => {
    const response = await apiClient.get<WorkerStatusResponse>('/api/jobs/worker-status');
    return response.data;
  },

  getLMSAIPolicy: async (): Promise<LMSAIPolicy> => {
    const response = await apiClient.get<LMSAIPolicy>('/llm/lms-policy');
    return response.data;
  },

  updateLMSAIPolicy: async (policy: LMSAIPolicyUpdate): Promise<LMSAIPolicy> => {
    const response = await apiClient.put<LMSAIPolicy>('/llm/lms-policy', policy);
    return response.data;
  },

  // ==================== User Management ====================

  /**
   * List all users in department
   */
  listUsers: async (): Promise<UsersListResponse> => {
    const response = await apiClient.get<UsersListResponse>('/admin/users');
    return response.data;
  },

  /**
   * Invite a new user
   */
  inviteUser: async (email: string, role: UserRole = 'faculty'): Promise<InviteUserResponse> => {
    const response = await apiClient.post<InviteUserResponse>('/admin/users/invite', {
      email,
      role,
    });
    return response.data;
  },

  /**
   * Remove user from department
   */
  removeUser: async (userId: string): Promise<{ message: string }> => {
    const response = await apiClient.delete<{ message: string }>(`/admin/users/${userId}`);
    return response.data;
  },

  /**
   * Update user role
   */
  updateUserRole: async (userId: string, role: UserRole): Promise<UpdateRoleResponse> => {
    const response = await apiClient.patch<UpdateRoleResponse>(`/admin/users/${userId}/role`, {
      role,
    });
    return response.data;
  },

  // ==================== Invitation Management ====================

  /**
   * List all invitations
   */
  listInvitations: async (status: InvitationStatus | null = null): Promise<InvitationsListResponse> => {
    const params = status ? { status } : {};
    const response = await apiClient.get<InvitationsListResponse>('/admin/invitations', { params });
    return response.data;
  },

  /**
   * Revoke an invitation
   */
  revokeInvitation: async (invitationId: string): Promise<{ message: string }> => {
    const response = await apiClient.delete<{ message: string }>(`/admin/invitations/${invitationId}`);
    return response.data;
  },

  /**
   * Resend an invitation
   */
  resendInvitation: async (invitationId: string): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>(`/admin/invitations/${invitationId}/resend`);
    return response.data;
  },

  // ==================== Department Stats ====================

  /**
   * Get department statistics
   */
  getDepartmentStats: async (): Promise<DepartmentStats> => {
    const response = await apiClient.get<DepartmentStats>('/admin/stats');
    return response.data;
  },

  // ==================== Export Functions ====================

  /**
   * Export scans as CSV
   */
  exportCSV: async (departmentId: string, options: ExportOptions = {}): Promise<Blob> => {
    const params = new URLSearchParams();
    if (options.date_from) params.append('date_from', options.date_from);
    if (options.date_to) params.append('date_to', options.date_to);

    const response = await apiClient.get<Blob>(
      `/analytics/export/${departmentId}/csv${params.toString() ? '?' + params.toString() : ''}`,
      {
        responseType: 'blob',
        timeout: 60000,
      }
    );
    return response.data;
  },

  /**
   * Export scans as Excel
   */
  exportExcel: async (departmentId: string, options: ExportOptions = {}): Promise<Blob> => {
    const params = new URLSearchParams();
    if (options.date_from) params.append('date_from', options.date_from);
    if (options.date_to) params.append('date_to', options.date_to);

    const response = await apiClient.get<Blob>(
      `/analytics/export/${departmentId}/excel${params.toString() ? '?' + params.toString() : ''}`,
      {
        responseType: 'blob',
        timeout: 60000,
      }
    );
    return response.data;
  },

  /**
   * Bulk export as ZIP
   */
  bulkExport: async (departmentId: string, options: BulkExportOptions = {}): Promise<Blob> => {
    const params = new URLSearchParams();
    if (options.include_pdfs !== undefined) params.append('include_pdfs', String(options.include_pdfs));
    if (options.include_evidence_report !== undefined) params.append('include_evidence_report', String(options.include_evidence_report));
    if (options.date_from) params.append('date_from', options.date_from);
    if (options.date_to) params.append('date_to', options.date_to);

    const response = await apiClient.get<Blob>(
      `/analytics/export/${departmentId}/bulk${params.toString() ? '?' + params.toString() : ''}`,
      {
        responseType: 'blob',
        timeout: 300000, // 5 minutes for full export with PDFs
      }
    );
    return response.data;
  },
};

export default adminApi;
