import { apiClient } from './client';
import {
  normalizeProviderListResponse,
  normalizeProviderSelectionResponse,
  normalizeProviderTestResponse,
} from '../utils/llmProviderContract';
import type {
  LLMProviderListWireResponse,
  LLMProviderName,
  LLMProvidersListResponse,
  ProviderSelectionWireResponse,
  ProviderTestWireResponse,
  SetPrimaryProviderResponse,
  TestProviderResponse,
} from '../utils/llmProviderContract';

export type {
  LLMProvider,
  LLMProviderName,
  LLMProvidersListResponse,
  SetPrimaryProviderResponse,
  TestProviderResponse,
} from '../utils/llmProviderContract';

// ============================================================================
// Types
// ============================================================================

export interface SetPrimaryProviderRequest {
  provider: LLMProviderName;
  as_fallback: boolean;
}

export interface UpdateModelsOptions {
  textModel?: string | null;
  codeModel?: string | null;
  visionModel?: string | null;
}

export interface UpdateModelsResponse {
  message: string;
  provider: LLMProviderName;
  text_model: string | null;
  code_model: string | null;
  vision_model: string | null;
}

export interface ConfigureProviderOptions extends UpdateModelsOptions {
  apiKey?: string;
}

export interface LLMHealthStatus {
  provider: LLMProviderName;
  status: 'healthy' | 'degraded' | 'unavailable';
  latency_ms?: number;
  last_check: string;
  error?: string;
}

export interface LLMHealthResponse {
  overall_status: 'healthy' | 'degraded' | 'unavailable';
  providers: LLMHealthStatus[];
}

// ============================================================================
// API Methods
// ============================================================================

/**
 * LLM Provider Management API
 *
 * Manages user preferences for AI model providers.
 * Supports: Ollama (local), Gemini, OpenAI, Anthropic, Grok (XAI)
 */
export const llmProvidersApi = {
  /**
   * List all available LLM providers and their status.
   * Returns information about each provider's availability and configured models.
   */
  listProviders: async (): Promise<LLMProvidersListResponse> => {
    const response = await apiClient.get<LLMProviderListWireResponse>('/llm/providers');
    return normalizeProviderListResponse(response.data);
  },

  /** Configure one workspace-owned provider without exposing its stored key. */
  configureProvider: async (
    provider: LLMProviderName,
    expectedRevision: number,
    options: ConfigureProviderOptions = {},
  ): Promise<LLMProvidersListResponse> => {
    const response = await apiClient.put<LLMProviderListWireResponse>(`/llm/providers/${provider}`, {
      expected_revision: expectedRevision,
      api_key: options.apiKey,
      text_model: options.textModel,
      code_model: options.codeModel,
      vision_model: options.visionModel,
    });
    return normalizeProviderListResponse(response.data);
  },

  /** Atomically replace the workspace's durable primary/fallback selection. */
  updateSelection: async (
    expectedRevision: number,
    primary: LLMProviderName | null,
    fallback: LLMProviderName | null,
  ): Promise<LLMProvidersListResponse> => {
    const response = await apiClient.put<LLMProviderListWireResponse>('/llm/providers/selection', {
      expected_revision: expectedRevision,
      primary,
      fallback,
    });
    return normalizeProviderListResponse(response.data);
  },

  /**
   * Set the primary LLM provider.
   * The provider must already be configured and available.
   *
   * @param provider - Provider name (ollama, gemini, openai, anthropic, xai)
   * @param asFallback - Set as fallback instead of primary
   */
  setPrimaryProvider: async (
    provider: LLMProviderName,
    asFallback: boolean = false
  ): Promise<SetPrimaryProviderResponse> => {
    const response = await apiClient.post<ProviderSelectionWireResponse>('/llm/providers/primary', {
      provider,
      as_fallback: asFallback,
    });
    return normalizeProviderSelectionResponse(response.data);
  },

  /**
   * Test a provider with a simple prompt.
   * If no provider specified, tests the primary provider.
   *
   * @param provider - Optional provider to test
   */
  testProvider: async (provider: LLMProviderName | null = null): Promise<TestProviderResponse> => {
    const params = provider ? `?provider=${provider}` : '';
    const response = await apiClient.post<ProviderTestWireResponse>(`/llm/providers/test${params}`);
    return normalizeProviderTestResponse(response.data);
  },

  /**
   * Update models for an existing provider without changing API key.
   *
   * @param provider - Provider name
   * @param models - Model configuration
   */
  updateModels: async (
    provider: LLMProviderName,
    models: UpdateModelsOptions = {}
  ): Promise<UpdateModelsResponse> => {
    const response = await apiClient.put<UpdateModelsResponse>(`/llm/providers/${provider}/models`, {
      text_model: models.textModel || null,
      code_model: models.codeModel || null,
      vision_model: models.visionModel || null,
    });
    return response.data;
  },

  /**
   * Get health status of all LLM providers.
   */
  getHealth: async (): Promise<LLMHealthResponse> => {
    const response = await apiClient.get<LLMHealthResponse>('/llm/health');
    return response.data;
  },
};

export default llmProvidersApi;
