/**
 * Account Management API client.
 *
 * Handles account deactivation, GDPR deletion, and data export.
 */
import { apiClient } from './client';

// ============================================================================
// Types
// ============================================================================

export interface DeactivateRequest {
  confirm: boolean;
  reason?: string;
}

export interface DeactivateResponse {
  message: string;
  sessions_revoked: number;
  keys_deactivated: number;
}

export interface DeletionRequestResponse {
  message: string;
  code_expires_at: string;
}

export interface DeletionConfirmRequest {
  code: string;
  reason?: string;
}

export interface DeletionConfirmResponse {
  message: string;
  scheduled_for: string;
}

export interface DeletionStatusResponse {
  deletion_pending: boolean;
  scheduled_for: string | null;
  days_remaining: number | null;
  can_cancel: boolean;
}

export interface CancelDeletionResponse {
  message: string;
}

export interface DataExportResponse {
  exported_at: string;
  user: Record<string, unknown>;
  department: Record<string, unknown>;
  preferences: Record<string, unknown>;
  scans: unknown[];
  api_keys: unknown[];
  audit_logs: unknown[];
}

// ============================================================================
// API Methods
// ============================================================================

export const accountApi = {
  /**
   * Deactivate account (soft delete).
   * Revokes sessions and API keys, blocks re-registration for 90 days.
   */
  deactivateAccount: async (reason?: string): Promise<DeactivateResponse> => {
    const response = await apiClient.post<DeactivateResponse>('/account/deactivate', {
      confirm: true,
      reason,
    });
    return response.data;
  },

  /**
   * Request a 6-digit deletion confirmation code (sent via email).
   */
  requestDeletionCode: async (): Promise<DeletionRequestResponse> => {
    const response = await apiClient.post<DeletionRequestResponse>('/account/deletion/request');
    return response.data;
  },

  /**
   * Confirm account deletion with the 6-digit code.
   * Schedules permanent deletion in 30 days.
   */
  confirmDeletion: async (code: string, reason?: string): Promise<DeletionConfirmResponse> => {
    const response = await apiClient.post<DeletionConfirmResponse>('/account/deletion/confirm', {
      code,
      reason,
    });
    return response.data;
  },

  /**
   * Cancel pending account deletion within the 30-day grace period.
   */
  cancelDeletion: async (): Promise<CancelDeletionResponse> => {
    const response = await apiClient.post<CancelDeletionResponse>('/account/deletion/cancel');
    return response.data;
  },

  /**
   * Get current deletion status.
   */
  getDeletionStatus: async (): Promise<DeletionStatusResponse> => {
    const response = await apiClient.get<DeletionStatusResponse>('/account/deletion/status');
    return response.data;
  },

  /**
   * Export all user data as JSON (GDPR Article 20).
   */
  exportData: async (): Promise<DataExportResponse> => {
    const response = await apiClient.get<DataExportResponse>('/account/export');
    return response.data;
  },
};

export default accountApi;
