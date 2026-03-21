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
  total_issues: number;
  avg_compliance_score: number;
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
  include_certificate?: boolean;
}

// ============================================================================
// API Methods
// ============================================================================

export const adminApi = {
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
    if (options.include_certificate !== undefined) params.append('include_certificate', String(options.include_certificate));
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
