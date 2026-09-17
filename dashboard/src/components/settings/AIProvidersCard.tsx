import React from 'react';
import type { ConfigureProviderOptions } from '../../api/llmProviders';
import {
  MODEL_FIELDS,
  canSaveOllama,
  isWorkspaceAIDisabled,
  modelDraftFromProvider,
  modelIdentifierError,
  ollamaConfigurationOptions,
} from '../../utils/llmProviderSettings';
import {
  CheckCircle,
  XCircle,
  Loader2,
  Zap,
} from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

type ProviderKey = 'ollama' | 'gemini' | 'openai' | 'anthropic' | 'xai';

interface IconProps {
  className?: string;
  style?: React.CSSProperties;
}

interface ProviderInfo {
  name: string;
  description: string;
  icon: React.FC<IconProps>;
  requiresKey: boolean;
  isLocal: boolean;
}

interface Provider {
  configured?: boolean;
  is_available?: boolean;
  text_model?: string | null;
  code_model?: string | null;
  vision_model?: string | null;
}

interface AIProvidersCardProps {
  showAIProviderSettings: boolean;
  providers: Record<string, Provider>;
  primaryProvider: ProviderKey | null;
  fallbackProvider: ProviderKey | null;
  loadingProviders: boolean;
  providerStateReady: boolean;
  providerLoadError: boolean;
  testingProvider: ProviderKey | null;
  configuringProvider: ProviderKey | null;
  providerMutationPending: boolean;
  modelDraftVersion: number;
  onTestProvider: (key: ProviderKey) => Promise<void>;
  onSetPrimary: (key: ProviderKey) => Promise<void>;
  onSetFallback: (key: ProviderKey | null) => Promise<void>;
  onConfigureProvider: (key: ProviderKey, options: ConfigureProviderOptions) => Promise<boolean>;
  onDisableAI: () => Promise<void>;
  onRetry: () => Promise<void>;
}

// ============================================================================
// Brand Icons
// ============================================================================

const OllamaIcon = ({ className }: IconProps): React.ReactElement => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-2-9.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5zm4 0c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5zm-5.5 4c0 .28.22.5.5.5h6c.28 0 .5-.22.5-.5v-1c0-.28-.22-.5-.5-.5H9c-.28 0-.5.22-.5.5v1z"/>
  </svg>
);

const GeminiIcon = ({ className }: IconProps): React.ReactElement => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
    <circle cx="12" cy="12" r="3" fill="currentColor"/>
    <path d="M12 2v20M2 12h20" stroke="currentColor" strokeWidth="0.5" fill="none"/>
  </svg>
);

const OpenAIIcon = ({ className }: IconProps): React.ReactElement => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.8956zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997z"/>
  </svg>
);

const AnthropicIcon = ({ className }: IconProps): React.ReactElement => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M13.827 3.52h3.603L24 20.48h-3.603l-6.57-16.96zm-7.258 0h3.767L16.906 20.48h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522zm3.174 5.47L7.32 14.58h4.847l-2.424-5.59z"/>
  </svg>
);

const XAIIcon = ({ className }: IconProps): React.ReactElement => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M3 3l7.5 9L3 21h2.5l6-7.5L17.5 21H21l-7.5-9L21 3h-2.5l-6 7.5L6.5 3H3z"/>
  </svg>
);

const PROVIDER_INFO: Record<ProviderKey, ProviderInfo> = {
  ollama: {
    name: 'Ollama',
    description: 'Models hosted on your Ollama service',
    icon: OllamaIcon,
    requiresKey: false,
    isLocal: true,
  },
  gemini: {
    name: 'Google Gemini',
    description: 'Google-hosted AI models',
    icon: GeminiIcon,
    requiresKey: true,
    isLocal: false,
  },
  openai: {
    name: 'OpenAI',
    description: 'OpenAI-hosted AI models',
    icon: OpenAIIcon,
    requiresKey: true,
    isLocal: false,
  },
  anthropic: {
    name: 'Anthropic',
    description: 'Anthropic-hosted Claude models',
    icon: AnthropicIcon,
    requiresKey: true,
    isLocal: false,
  },
  xai: {
    name: 'xAI (Grok)',
    description: 'xAI-hosted Grok models',
    icon: XAIIcon,
    requiresKey: true,
    isLocal: false,
  },
};

// ============================================================================
// Provider information shown when the current principal cannot administer it.
// ============================================================================

function CloudAIInfoCard(): React.ReactElement {
  return (
    <div id="ai-provider-settings" className="card mb-6">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
          <Zap className="w-5 h-5" />
          AI Provider Settings
        </h2>
      </div>
      <div className="px-6 py-4">
        <div
          className="flex items-start gap-3 p-4 rounded-lg"
          style={{
            backgroundColor: 'var(--surface-tertiary)',
            border: '1px solid var(--border-primary)',
          }}
        >
          <Zap className="w-5 h-5 shrink-0 mt-0.5" style={{ color: 'var(--content-tertiary)' }} />
          <div>
            <p className="text-sm font-medium text-primary">
              Workspace-managed AI
            </p>
            <p className="text-sm text-secondary mt-1">
              A workspace administrator can configure local Ollama or institution-supplied keys
              for Gemini, OpenAI, Anthropic, and xAI. Contact them to confirm whether AI is
              available in this workspace.
            </p>
          </div>
        </div>

        <p className="text-xs text-tertiary mt-4">
          Provider controls are available to workspace administrators outside LTI launch sessions.
        </p>
      </div>
    </div>
  );
}

// ============================================================================
// AI Provider Settings Card (for self-hosted users)
// ============================================================================

function OllamaModelEditor({
  provider,
  pending,
  saving,
  onSave,
}: {
  provider?: Provider;
  pending: boolean;
  saving: boolean;
  onSave: (options: ConfigureProviderOptions) => Promise<boolean>;
}): React.ReactElement {
  const [draft, setDraft] = React.useState(() => modelDraftFromProvider(provider));
  const canSave = canSaveOllama(draft, pending);

  return (
    <form
      className="mt-4 space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) void onSave(ollamaConfigurationOptions(draft));
      }}
    >
      <fieldset disabled={pending} aria-describedby="ollama-model-help">
        <legend className="text-sm font-medium text-primary">Ollama model identifiers</legend>
        <p id="ollama-model-help" className="text-xs text-tertiary mt-1 mb-3">
          Enter all three identifiers exactly as installed on your Ollama service (maximum 128 characters).
          Saving stores these identifiers; it does not download models or run a test.
          Select a primary provider to use workspace AI. A fallback is used only if the primary fails.
        </p>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {MODEL_FIELDS.map(({ key, label }) => {
            const error = modelIdentifierError(draft[key]);
            const id = `ollama-${key}`;
            return (
              <div key={key} className="min-w-0">
                <label htmlFor={id} className="text-sm font-medium text-secondary">{label}</label>
                <input
                  id={id}
                  type="text"
                  className="input w-full mt-1"
                  value={draft[key]}
                  onChange={(event) => setDraft(current => ({ ...current, [key]: event.target.value }))}
                  maxLength={128}
                  required
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? `${id}-error` : 'ollama-model-help'}
                />
                {error && <p id={`${id}-error`} className="text-xs mt-1 text-secondary">{error}</p>}
              </div>
            );
          })}
        </div>
      </fieldset>
      <button
        type="submit"
        id="provider-ollama-configure"
        disabled={!canSave}
        aria-busy={saving}
        className="btn-secondary px-3 py-1.5 text-sm"
      >
        {saving ? 'Saving Ollama models…' : 'Save Ollama models'}
      </button>
    </form>
  );
}

function ProviderSettingsCard({
  providers,
  primaryProvider,
  fallbackProvider,
  loadingProviders,
  providerStateReady,
  providerLoadError,
  testingProvider,
  configuringProvider,
  providerMutationPending,
  modelDraftVersion,
  onTestProvider,
  onSetPrimary,
  onSetFallback,
  onConfigureProvider,
  onDisableAI,
  onRetry,
}: Omit<AIProvidersCardProps, 'showAIProviderSettings'>): React.ReactElement {
  const [apiKeys, setApiKeys] = React.useState<Partial<Record<ProviderKey, string>>>({});
  const workspaceDisabled = isWorkspaceAIDisabled(primaryProvider, fallbackProvider);

  const configureProvider = async (key: ProviderKey): Promise<void> => {
    const apiKey = apiKeys[key]?.trim();
    if (await onConfigureProvider(key, { apiKey: apiKey || undefined })) {
      setApiKeys(current => ({ ...current, [key]: '' }));
    }
  };

  return (
    <div id="ai-provider-settings" tabIndex={-1} className="card mb-6">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
          <Zap className="w-5 h-5" />
          AI Provider Settings
        </h2>
        <p className="text-sm text-tertiary mt-1">
          Configure workspace provider credentials and a durable primary and fallback choice
        </p>
      </div>
      <div className="px-6 py-4 space-y-4">
        {loadingProviders ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-tertiary" />
            <span className="ml-2 text-tertiary">Loading providers...</span>
          </div>
        ) : providerLoadError || !providerStateReady ? (
          <div
            role="alert"
            className="p-4 rounded-lg border"
            style={{
              backgroundColor: 'var(--surface-danger-subtle)',
              borderColor: 'var(--content-danger)',
            }}
          >
            <p className="text-sm text-primary">
              Provider settings could not be loaded. No changes can be made until current
              workspace state is available.
            </p>
            <button
              type="button"
              onClick={() => void onRetry()}
              className="btn-secondary px-3 py-1.5 text-sm mt-3"
            >
              Retry
            </button>
          </div>
        ) : (
          <>
            <div
              className="p-4 rounded-lg border"
              style={{
                backgroundColor: 'var(--surface-tertiary)',
                borderColor: 'var(--border-primary)',
              }}
            >
              <p className="text-sm font-medium text-primary mb-2" role="status">
                {workspaceDisabled
                  ? 'Workspace AI is disabled'
                  : primaryProvider
                    ? 'Workspace AI has a selected primary provider'
                    : 'A fallback is saved; select a primary to use workspace AI.'}
              </p>
              <div className="flex flex-wrap items-center gap-4 text-sm">
                <div>
                  <span className="text-tertiary">Primary:</span>{' '}
                  <span className="font-medium text-primary">
                    {primaryProvider ? PROVIDER_INFO[primaryProvider]?.name : 'Not set'}
                  </span>
                </div>
                <div className="h-4 w-px" style={{ backgroundColor: 'var(--border-primary)' }} />
                <div>
                  <span className="text-tertiary">Fallback:</span>{' '}
                  <span className="font-medium text-primary">
                    {fallbackProvider ? PROVIDER_INFO[fallbackProvider]?.name : 'Not set'}
                  </span>
                </div>
              </div>
              <p className="text-xs text-tertiary mt-3">
                Disabling clears both provider selections for new workspace AI requests. Saved models and credentials are retained.
                In-flight work, embeddings and separate LMS AI policies are unchanged.
              </p>
              <button
                type="button"
                onClick={() => void onDisableAI()}
                disabled={providerMutationPending || workspaceDisabled}
                className="btn-secondary px-3 py-1.5 text-sm mt-3"
              >
                Disable workspace AI
              </button>
            </div>

            <div className="space-y-3">
              {(Object.entries(PROVIDER_INFO) as [ProviderKey, ProviderInfo][]).map(([key, info]) => {
                const provider = providers[key];
                const isAvailable = provider?.configured ?? provider?.is_available;
                const isPrimary = primaryProvider === key;
                const isFallback = fallbackProvider === key;
                const ProviderIcon = info.icon;

                return (
                  <div
                    key={key}
                    id={`provider-${key}-row`}
                    tabIndex={-1}
                    className="p-4 rounded-lg border transition-colors"
                    style={{
                      backgroundColor: isPrimary
                        ? 'var(--surface-accent)'
                        : 'var(--surface-secondary)',
                      borderColor: isPrimary
                        ? 'var(--content-accent)'
                        : isAvailable
                        ? 'var(--border-primary)'
                        : 'var(--border-subtle)',
                    }}
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex min-w-0 items-start gap-3">
                        <div
                          className="p-2 rounded-lg"
                          style={{
                            backgroundColor: info.isLocal
                              ? 'var(--surface-success-subtle)'
                              : 'var(--surface-info-subtle)',
                          }}
                        >
                          <ProviderIcon
                            className="w-5 h-5"
                            style={{
                              color: info.isLocal
                                ? 'var(--content-success)'
                                : 'var(--content-info)',
                            }}
                          />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="font-medium text-primary">{info.name}</h3>
                            {isAvailable ? (
                              <CheckCircle className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
                            ) : (
                              <XCircle className="w-4 h-4" style={{ color: 'var(--content-tertiary)' }} />
                            )}
                            {isPrimary && (
                              <span
                                className="px-2 py-0.5 text-xs font-medium rounded"
                                style={{
                                  backgroundColor: 'var(--content-accent)',
                                  color: 'var(--content-inverse)',
                                }}
                              >
                                Primary
                              </span>
                            )}
                            {isFallback && (
                              <span
                                className="px-2 py-0.5 text-xs font-medium rounded"
                                style={{
                                  backgroundColor: 'var(--content-warning)',
                                  color: 'var(--content-inverse)',
                                }}
                              >
                                Fallback
                              </span>
                            )}
                          </div>
                          <p className="text-sm text-tertiary mt-0.5">{info.description}</p>
                          <p className="text-xs text-secondary mt-1">{isAvailable ? 'Configured' : 'Not configured'}</p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center justify-start gap-2 sm:justify-end">
                        {isAvailable && (
                          <>
                            <button
                              onClick={() => onTestProvider(key)}
                              disabled={testingProvider === key || providerMutationPending}
                              aria-label={testingProvider === key
                                ? `Testing ${info.name} text model`
                                : `Test ${info.name} text model`}
                              aria-busy={testingProvider === key}
                              className="btn-secondary px-3 py-1.5 text-sm"
                            >
                              {testingProvider === key ? (
                                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                              ) : (
                                'Test text model'
                              )}
                            </button>
                            {!isPrimary && (
                              <button
                                onClick={() => onSetPrimary(key)}
                                id={`provider-${key}-primary`}
                                aria-label={`Set ${info.name} as primary provider`}
                                disabled={providerMutationPending}
                                className="btn-primary px-3 py-1.5 text-sm"
                              >
                                Set Primary
                              </button>
                            )}
                            {!isPrimary && (
                              <button
                                onClick={() => onSetFallback(isFallback ? null : key)}
                                aria-label={isFallback
                                  ? `Clear ${info.name} as fallback provider`
                                  : `Set ${info.name} as fallback provider`}
                                disabled={providerMutationPending}
                                className="btn-secondary px-3 py-1.5 text-sm"
                              >
                                {isFallback ? 'Clear Fallback' : 'Set Fallback'}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                    {isAvailable && (
                      <dl className="mt-3 grid grid-cols-1 gap-2 text-sm lg:grid-cols-3" aria-label={`${info.name} saved models`}>
                        {MODEL_FIELDS.map(({ key: modelKey, label }) => (
                          <div key={modelKey} className="min-w-0">
                            <dt className="text-tertiary">Saved {label.toLowerCase()}</dt>
                            <dd className="font-mono text-primary break-all">{modelDraftFromProvider(provider)[modelKey] || 'Not configured'}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    {key === 'ollama' ? (
                      <OllamaModelEditor
                        key={modelDraftVersion}
                        provider={provider}
                        pending={providerMutationPending}
                        saving={configuringProvider === key}
                        onSave={(options) => onConfigureProvider(key, options)}
                      />
                    ) : (
                      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
                        {info.requiresKey && (
                          <input
                            type="password"
                            value={apiKeys[key] || ''}
                            onChange={(event) => setApiKeys(current => ({
                              ...current,
                              [key]: event.target.value,
                            }))}
                            className="input w-full sm:max-w-xs"
                            aria-label={`${info.name} API key`}
                            id={`provider-${key}-api-key`}
                            placeholder={isAvailable ? 'Enter a replacement API key' : 'Enter API key'}
                            autoComplete="new-password"
                            autoCapitalize="none"
                            spellCheck={false}
                            disabled={providerMutationPending}
                          />
                        )}
                        <button
                          onClick={() => void configureProvider(key)}
                          id={`provider-${key}-configure`}
                          disabled={
                            configuringProvider === key
                            || providerMutationPending
                            || (info.requiresKey && !(apiKeys[key] || '').trim())
                          }
                          aria-label={configuringProvider === key
                            ? `Saving ${info.name} provider configuration`
                            : `${isAvailable ? 'Update' : 'Configure'} ${info.name} provider`}
                          aria-busy={configuringProvider === key}
                          className="btn-secondary px-3 py-1.5 text-sm whitespace-nowrap"
                        >
                          {configuringProvider === key ? (
                            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                          ) : isAvailable ? (
                            'Replace Key'
                          ) : (
                            'Configure'
                          )}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Main Export
// ============================================================================

export function AIProvidersCard(props: AIProvidersCardProps): React.ReactElement {
  if (props.showAIProviderSettings) {
    return <ProviderSettingsCard {...props} />;
  }
  return <CloudAIInfoCard />;
}
