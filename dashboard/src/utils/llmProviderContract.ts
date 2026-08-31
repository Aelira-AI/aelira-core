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
  configured: boolean;
  is_available: boolean;
  is_local: boolean;
  status: string;
  text_model: string | null;
  code_model: string | null;
  vision_model: string | null;
}

export interface LLMProviderListWireResponse {
  schema_version: 1;
  config_revision: number;
  primary: LLMProviderName | null;
  fallback: LLMProviderName | null;
  providers: Record<string, LLMProviderWireInfo>;
}

export interface LLMProvider extends Omit<LLMProviderWireInfo, 'name'> {
  name: LLMProviderName;
}

export interface LLMProvidersListResponse {
  providers: LLMProvider[];
  config_revision: number;
  primary_provider: LLMProviderName | null;
  fallback_provider: LLMProviderName | null;
}

export interface ProviderSelectionWireResponse {
  success: boolean;
  message: string;
  primary: LLMProviderName | null;
  fallback: LLMProviderName | null;
  config_revision: number;
}

export interface SetPrimaryProviderResponse {
  success: boolean;
  message: string;
  primary_provider: LLMProviderName | null;
  fallback_provider: LLMProviderName | null;
  config_revision: number;
}

export interface ProviderTestWireResponse {
  success: boolean;
  provider: string;
  model: string;
  inference_time: number;
  error: string | null;
}

export interface TestProviderResponse {
  success: boolean;
  provider: string;
  model: string;
  response_time_ms: number;
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
    || typeof value.configured !== 'boolean'
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
    configured: value.configured,
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

  const { schema_version: schemaVersion, config_revision: configRevision, primary, fallback, providers } = value;
  if (
    schemaVersion !== 1
    || !Number.isInteger(configRevision)
    || (configRevision as number) < 0
    || !isSelection(primary)
    || !isSelection(fallback)
    || !isRecord(providers)
  ) {
    throw contractError();
  }

  return {
    providers: SUPPORTED_LLM_PROVIDERS.map(name => (
      normalizeProviderInfo(providers[name], name)
    )),
    config_revision: configRevision as number,
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
    || !Number.isInteger(value.config_revision)
    || (value.config_revision as number) < 0
  ) {
    throw contractError();
  }

  return {
    success: value.success,
    message: value.message,
    primary_provider: value.primary,
    fallback_provider: value.fallback,
    config_revision: value.config_revision as number,
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
    || !isNullableString(value.error)
  ) {
    throw contractError();
  }

  return {
    success: value.success,
    provider: value.provider,
    model: value.model,
    response_time_ms: Math.round(value.inference_time * 1000),
    error: value.error,
  };
}

export function normalizeProviderRevisionConflict(error: unknown): LLMProvidersListResponse | null {
  if (!isRecord(error) || !isRecord(error.response)) {
    return null;
  }
  const data = error.response.data;
  if (!isRecord(data) || !isRecord(data.detail)) {
    return null;
  }
  const { detail } = data;
  if (detail.code !== 'provider_config_revision_conflict') {
    return null;
  }
  try {
    return normalizeProviderListResponse(detail.current);
  } catch {
    return null;
  }
}
