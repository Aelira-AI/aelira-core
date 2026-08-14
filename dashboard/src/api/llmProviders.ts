import { apiClient } from './client';

// ============================================================================
// Types
// ============================================================================

export type LLMProviderName = 'ollama' | 'gemini' | 'openai' | 'anthropic' | 'xai';

export interface LLMProvider {
  name: LLMProviderName;
  display_name: string;
  is_available: boolean;
  is_primary: boolean;
  is_fallback: boolean;
  requires_api_key: boolean;
  has_api_key: boolean;
  text_model?: string;
  code_model?: string;
  vision_model?: string;
}

export interface LLMProvidersListResponse {
  providers: LLMProvider[];
  primary_provider: LLMProviderName | null;
  fallback_provider: LLMProviderName | null;
}

export interface SetPrimaryProviderRequest {
  provider: LLMProviderName;
  as_fallback: boolean;
}

export interface SetPrimaryProviderResponse {
  message: string;
  primary_provider: LLMProviderName;
  fallback_provider: LLMProviderName | null;
}

export interface AddProviderOptions {
  textModel?: string;
  codeModel?: string;
  visionModel?: string;
}

export interface AddProviderResponse {
  message: string;
  provider: LLMProviderName;
  status: string;
}

export interface TestProviderResponse {
  success: boolean;
  provider: LLMProviderName;
  response_time_ms: number;
  message?: string;
  error?: string;
}

export interface LLMModel {
  id: string;
  name: string;
  description?: string;
  context_length?: number;
  capabilities?: string[];
}

export interface ModelsListResponse {
  provider: LLMProviderName;
  models: LLMModel[];
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
    const response = await apiClient.get<LLMProvidersListResponse>('/llm/providers');
    return response.data;
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
    const response = await apiClient.post<SetPrimaryProviderResponse>('/llm/providers/primary', {
      provider,
      as_fallback: asFallback,
    });
    return response.data;
  },

  /**
   * Add or configure a provider with an API key.
   *
   * @param provider - Provider name (gemini, openai, anthropic, xai)
   * @param apiKey - API key for the provider
   * @param options - Optional model overrides
   */
  addProvider: async (
    provider: LLMProviderName,
    apiKey: string,
    options: AddProviderOptions = {}
  ): Promise<AddProviderResponse> => {
    const response = await apiClient.post<AddProviderResponse>('/llm/providers/add', {
      provider,
      api_key: apiKey,
      text_model: options.textModel,
      code_model: options.codeModel,
      vision_model: options.visionModel,
    });
    return response.data;
  },

  /**
   * Test a provider with a simple prompt.
   * If no provider specified, tests the primary provider.
   *
   * @param provider - Optional provider to test
   */
  testProvider: async (provider: LLMProviderName | null = null): Promise<TestProviderResponse> => {
    const params = provider ? `?provider=${provider}` : '';
    const response = await apiClient.post<TestProviderResponse>(`/llm/providers/test${params}`);
    return response.data;
  },

  /**
   * List available models for a specific provider.
   *
   * @param provider - Provider name
   */
  listModels: async (provider: LLMProviderName): Promise<ModelsListResponse> => {
    const response = await apiClient.get<ModelsListResponse>(`/llm/providers/${provider}/models`);
    return response.data;
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
