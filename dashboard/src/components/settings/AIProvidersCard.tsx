import React from 'react';
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
  is_available?: boolean;
  text_model?: string;
  code_model?: string;
  vision_model?: string;
}

interface AIProvidersCardProps {
  showAIProviderSettings: boolean;
  providers: Record<string, Provider>;
  primaryProvider: ProviderKey | null;
  fallbackProvider: ProviderKey | null;
  loadingProviders: boolean;
  testingProvider: ProviderKey | null;
  onTestProvider: (key: ProviderKey) => void;
  onSetPrimary: (key: ProviderKey) => void;
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
    description: 'Local models: Llama 3.2, Qwen 2.5, Mistral (free, private)',
    icon: OllamaIcon,
    requiresKey: false,
    isLocal: true,
  },
  gemini: {
    name: 'Google Gemini',
    description: 'Gemini 3 Flash/Pro (fast, affordable, multimodal)',
    icon: GeminiIcon,
    requiresKey: true,
    isLocal: false,
  },
  openai: {
    name: 'OpenAI',
    description: 'GPT-4.1, o3, GPT-5.2 reasoning models',
    icon: OpenAIIcon,
    requiresKey: true,
    isLocal: false,
  },
  anthropic: {
    name: 'Anthropic',
    description: 'Claude Opus 4.5, Sonnet 4.5, Haiku 4.5',
    icon: AnthropicIcon,
    requiresKey: true,
    isLocal: false,
  },
  xai: {
    name: 'xAI (Grok)',
    description: 'Grok 4.1, Grok 4.1 Thinking',
    icon: XAIIcon,
    requiresKey: true,
    isLocal: false,
  },
};

// ============================================================================
// Cloud AI Info Card (for non-self-hosted users)
// ============================================================================

function CloudAIInfoCard(): React.ReactElement {
  return (
    <div className="card mb-6">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
          <Zap className="w-5 h-5" />
          AI Processing
        </h2>
      </div>
      <div className="px-6 py-4">
        <div
          className="flex items-start gap-3 p-4 rounded-lg"
          style={{
            backgroundColor: 'var(--surface-success-subtle)',
            border: '1px solid var(--content-success)',
          }}
        >
          <CheckCircle className="w-5 h-5 shrink-0 mt-0.5" style={{ color: 'var(--content-success)' }} />
          <div>
            <p className="text-sm font-medium" style={{ color: 'var(--content-success)' }}>
              Cloud AI Included
            </p>
            <p className="text-sm text-secondary mt-1">
              Your plan includes cloud-hosted AI processing powered by Google Gemini 3.
              No setup required - just upload your documents and we handle the rest.
            </p>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
            <span className="text-sm text-secondary">Alt text generation</span>
          </div>
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
            <span className="text-sm text-secondary">WCAG analysis</span>
          </div>
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
            <span className="text-sm text-secondary">Code fix suggestions</span>
          </div>
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4" style={{ color: 'var(--content-success)' }} />
            <span className="text-sm text-secondary">Document remediation</span>
          </div>
        </div>

        <p className="text-xs text-tertiary mt-4">
          Enterprise customers with self-hosted deployments can configure their own AI providers including local Ollama models.
        </p>
      </div>
    </div>
  );
}

// ============================================================================
// AI Provider Settings Card (for self-hosted users)
// ============================================================================

function ProviderSettingsCard({
  providers,
  primaryProvider,
  fallbackProvider,
  loadingProviders,
  testingProvider,
  onTestProvider,
  onSetPrimary,
}: Omit<AIProvidersCardProps, 'showAIProviderSettings'>): React.ReactElement {
  return (
    <div className="card mb-6">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
          <Zap className="w-5 h-5" />
          AI Provider Settings
        </h2>
        <p className="text-sm text-tertiary mt-1">
          Choose which AI provider to use for accessibility analysis
        </p>
      </div>
      <div className="px-6 py-4 space-y-4">
        {loadingProviders ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-tertiary" />
            <span className="ml-2 text-tertiary">Loading providers...</span>
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
              <div className="flex items-center gap-4 text-sm">
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
            </div>

            <div className="space-y-3">
              {(Object.entries(PROVIDER_INFO) as [ProviderKey, ProviderInfo][]).map(([key, info]) => {
                const provider = providers[key];
                const isAvailable = provider?.is_available;
                const isPrimary = primaryProvider === key;
                const isFallback = fallbackProvider === key;
                const ProviderIcon = info.icon;

                return (
                  <div
                    key={key}
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
                    <div className="flex items-start justify-between">
                      <div className="flex items-start gap-3">
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
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {isAvailable && (
                          <>
                            <button
                              onClick={() => onTestProvider(key)}
                              disabled={testingProvider === key}
                              className="btn-secondary px-3 py-1.5 text-sm"
                            >
                              {testingProvider === key ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                'Test'
                              )}
                            </button>
                            {!isPrimary && (
                              <button
                                onClick={() => onSetPrimary(key)}
                                className="btn-primary px-3 py-1.5 text-sm"
                              >
                                Set Primary
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </div>
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
