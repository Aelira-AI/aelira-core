export const SUPPORTED_LLM_PROVIDERS = [
  'ollama',
  'gemini',
  'openai',
  'anthropic',
  'xai',
] as const;

export type LLMProviderName = (typeof SUPPORTED_LLM_PROVIDERS)[number];

export const LLM_PROVIDER_CONTRACT_ERROR = 'Invalid LLM provider response';

export interface LLMProviderWireInfo {
  name: string;
  display_name: string;
  is_available: boolean;
  is_local: boolean;
  status: string;
  text_model: string | null;
  code_model: string | null;
  vision_model: string | null;
}

export interface LLMProviderListWireResponse {
  primary: LLMProviderName | null;
  fallback: LLMProviderName | null;
  providers: Record<string, LLMProviderWireInfo>;
}

export interface LLMProvider extends Omit<LLMProviderWireInfo, 'name'> {
  name: LLMProviderName;
}

export interface LLMProvidersListResponse {
  providers: LLMProvider[];
  primary_provider: LLMProviderName | null;
  fallback_provider: LLMProviderName | null;
}

export interface ProviderSelectionWireResponse {
  success: boolean;
  message: string;
  primary: LLMProviderName | null;
  fallback: LLMProviderName | null;
}

export interface SetPrimaryProviderResponse {
  success: boolean;
  message: string;
  primary_provider: LLMProviderName | null;
  fallback_provider: LLMProviderName | null;
}

export interface ProviderTestWireResponse {
  success: boolean;
  provider: string;
  model: string;
  inference_time: number;
  response_preview: string | null;
  error: string | null;
}

export interface TestProviderResponse {
  success: boolean;
  provider: string;
  model: string;
  response_time_ms: number;
  response_preview: string | null;
  error: string | null;
}

function contractError(): Error {
  return new Error(LLM_PROVIDER_CONTRACT_ERROR);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isProviderName(value: unknown): value is LLMProviderName {
  return typeof value === 'string'
    && SUPPORTED_LLM_PROVIDERS.includes(value as LLMProviderName);
}

function isSelection(value: unknown): value is LLMProviderName | null {
  return value === null || isProviderName(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function normalizeProviderInfo(
  value: unknown,
  expectedName: LLMProviderName,
): LLMProvider {
  if (
    !isRecord(value)
    || value.name !== expectedName
    || typeof value.display_name !== 'string'
    || typeof value.is_available !== 'boolean'
    || typeof value.is_local !== 'boolean'
    || typeof value.status !== 'string'
    || !isNullableString(value.text_model)
    || !isNullableString(value.code_model)
    || !isNullableString(value.vision_model)
  ) {
    throw contractError();
  }

  return {
    name: expectedName,
    display_name: value.display_name,
    is_available: value.is_available,
    is_local: value.is_local,
    status: value.status,
    text_model: value.text_model,
    code_model: value.code_model,
    vision_model: value.vision_model,
  };
}

export function normalizeProviderListResponse(value: unknown): LLMProvidersListResponse {
  if (!isRecord(value)) {
    throw contractError();
  }

  const { primary, fallback, providers } = value;
  if (
    !isSelection(primary)
    || !isSelection(fallback)
    || !isRecord(providers)
  ) {
    throw contractError();
  }

  return {
    providers: SUPPORTED_LLM_PROVIDERS.map(name => (
      normalizeProviderInfo(providers[name], name)
    )),
    primary_provider: primary,
    fallback_provider: fallback,
  };
}

export function normalizeProviderSelectionResponse(value: unknown): SetPrimaryProviderResponse {
  if (
    !isRecord(value)
    || typeof value.success !== 'boolean'
    || typeof value.message !== 'string'
    || !isSelection(value.primary)
    || !isSelection(value.fallback)
  ) {
    throw contractError();
  }

  return {
    success: value.success,
    message: value.message,
    primary_provider: value.primary,
    fallback_provider: value.fallback,
  };
}

export function normalizeProviderTestResponse(value: unknown): TestProviderResponse {
  if (
    !isRecord(value)
    || typeof value.success !== 'boolean'
    || typeof value.provider !== 'string'
    || value.provider.length === 0
    || typeof value.model !== 'string'
    || typeof value.inference_time !== 'number'
    || !Number.isFinite(value.inference_time)
    || value.inference_time < 0
    || !isNullableString(value.response_preview)
    || !isNullableString(value.error)
  ) {
    throw contractError();
  }

  return {
    success: value.success,
    provider: value.provider,
    model: value.model,
    response_time_ms: Math.round(value.inference_time * 1000),
    response_preview: value.response_preview,
    error: value.error,
  };
}
