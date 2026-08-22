import { apiClient } from './client';

export interface APIKeyMetadata {
  id: string;
  name: string;
  key_prefix: string;
  rate_limit_per_hour: number;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  is_active: boolean;
}

export interface CreateAPIKeyRequest {
  name: string;
  rate_limit_per_hour?: number;
  expires_days?: number | null;
}

export interface CreateAPIKeyResponse {
  api_key: APIKeyMetadata;
  full_key: string;
  warning: string;
}

export interface RevokeAPIKeyResponse {
  success: boolean;
  message: string;
  revoked_current_key: boolean;
}

export const apiKeysApi = {
  async list(): Promise<APIKeyMetadata[]> {
    const response = await apiClient.get<APIKeyMetadata[]>('/auth/keys');
    return response.data;
  },

  async create(request: CreateAPIKeyRequest): Promise<CreateAPIKeyResponse> {
    const response = await apiClient.post<CreateAPIKeyResponse>('/auth/keys', request);
    return response.data;
  },

  async revoke(keyId: string): Promise<RevokeAPIKeyResponse> {
    const response = await apiClient.delete<RevokeAPIKeyResponse>(
      `/auth/keys/${encodeURIComponent(keyId)}`
    );
    return response.data;
  },
};
