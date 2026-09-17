import type { ConfigureProviderOptions } from '../api/llmProviders';
import type { LLMProviderName } from './llmProviderContract';

export const MODEL_FIELDS = [
  { key: 'textModel', label: 'Text model' },
  { key: 'codeModel', label: 'Code model' },
  { key: 'visionModel', label: 'Vision model' },
] as const;

export type ModelDraft = Record<(typeof MODEL_FIELDS)[number]['key'], string>;

export function modelDraftFromProvider(provider?: {
  text_model?: string | null;
  code_model?: string | null;
  vision_model?: string | null;
}): ModelDraft {
  return {
    textModel: provider?.text_model ?? '',
    codeModel: provider?.code_model ?? '',
    visionModel: provider?.vision_model ?? '',
  };
}

export function modelIdentifierError(value: string): string | null {
  if (!value) return 'Enter a model identifier; blank values are not saved as defaults.';
  if (value.length > 128) return 'Use at most 128 characters.';
  // Unlike $, this end assertion rejects a trailing newline, matching server fullmatch.
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}(?![\s\S])/.test(value)) {
    return 'Start with a letter or number; use only letters, numbers, . _ : / + or -.';
  }
  return null;
}

export function ollamaConfigurationOptions(draft: ModelDraft): ModelDraft {
  for (const { key } of MODEL_FIELDS) {
    const error = modelIdentifierError(draft[key]);
    if (error) throw new Error(error);
  }
  // Preserve spelling, tags and casing exactly. Never trim or substitute defaults.
  return { ...draft };
}

export function providerConfigurationRequest(revision: number, options: ConfigureProviderOptions) {
  return {
    expected_revision: revision,
    api_key: options.apiKey,
    text_model: options.textModel,
    code_model: options.codeModel,
    vision_model: options.visionModel,
  };
}

export function providerSelectionRequest(
  revision: number,
  primary: LLMProviderName | null,
  fallback: LLMProviderName | null,
) {
  return { expected_revision: revision, primary, fallback };
}

export function isWorkspaceAIDisabled(
  primary: LLMProviderName | null,
  fallback: LLMProviderName | null,
): boolean {
  return primary === null && fallback === null;
}

export function canSaveOllama(draft: ModelDraft, pending: boolean): boolean {
  return !pending && MODEL_FIELDS.every(({ key }) => modelIdentifierError(draft[key]) === null);
}

export function ollamaTestFailure(model?: string | null): string {
  const modelLabel = model && modelIdentifierError(model) === null ? ` “${model}”` : '';
  return `Ollama text test failed. Check that the configured text model${modelLabel} is installed and the Ollama service is running. Code and vision models were not tested.`;
}
