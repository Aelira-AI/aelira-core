import React, { useState, useEffect, useRef, ChangeEvent, FormEvent, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Cloud, Settings, ExternalLink, Check, X, Loader2, FolderTree as FolderTreeIcon, AlertTriangle } from 'lucide-react';
import { apiClient } from '../api/client';
import { FolderSelectionModal } from '../components/FolderSelectionModal';
import { useToast } from '../context/toast-context';
import { FeatureGate } from '../components/FeatureGate';
import { trackEvent } from '../utils/analytics';

// Type definitions
type ProviderKey = 'google' | 'microsoft' | 'canvas' | 'blackboard' | 'moodle' | 'brightspace';
type LMSProvider = 'canvas' | 'blackboard' | 'moodle' | 'brightspace';

interface IntegrationStatus {
  connected: boolean;
  email?: string;
  lastSync?: string;
  fileCount?: number;
}

interface IntegrationsState {
  google: IntegrationStatus;
  microsoft: IntegrationStatus;
  canvas: IntegrationStatus;
  blackboard: IntegrationStatus;
  moodle: IntegrationStatus;
  brightspace: IntegrationStatus;
}

interface LMSUrlModalState {
  isOpen: boolean;
  provider: LMSProvider | null;
}

interface DisconnectModalState {
  isOpen: boolean;
  provider: ProviderKey | null;
}

interface ProviderConfig {
  name: string;
  placeholder: string;
  description: string;
}

interface LMSUrlInputModalProps {
  provider: LMSProvider | null;
  isOpen: boolean;
  onSubmit: (url: string) => void;
  onCancel: () => void;
  isConnecting: boolean;
}

interface DisconnectConfirmModalProps {
  provider: ProviderKey | null;
  isOpen: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  isDisconnecting: boolean;
}

interface IntegrationCardProps {
  name: string;
  description: string;
  icon: ReactNode;
  connected: boolean;
  email?: string;
  lastSync?: string;
  fileCount?: number;
  isLTI?: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  connecting: boolean;
  actionUrl?: string;
  actionLabel?: string;
}

// Platform icons
function GoogleIcon(): React.ReactElement {
  return (
    <svg className="w-10 h-10" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  );
}

function MicrosoftIcon(): React.ReactElement {
  return (
    <svg className="w-10 h-10" viewBox="0 0 24 24">
      <path fill="#F25022" d="M1 1h10v10H1z"/>
      <path fill="#7FBA00" d="M13 1h10v10H13z"/>
      <path fill="#00A4EF" d="M1 13h10v10H1z"/>
      <path fill="#FFB900" d="M13 13h10v10H13z"/>
    </svg>
  );
}

function CanvasIcon(): React.ReactElement {
  return (
    <div className="w-10 h-10 rounded-full bg-[#E74C3C] flex items-center justify-center">
      <span className="text-white font-bold text-lg">C</span>
    </div>
  );
}

function BlackboardIcon(): React.ReactElement {
  return (
    <div className="w-10 h-10 rounded bg-[#262626] flex items-center justify-center">
      <span className="text-white font-bold text-sm">Bb</span>
    </div>
  );
}

function MoodleIcon(): React.ReactElement {
  return (
    <div className="w-10 h-10 rounded-full bg-[#F98012] flex items-center justify-center">
      <span className="text-white font-bold text-lg">M</span>
    </div>
  );
}

function BrightspaceIcon(): React.ReactElement {
  return (
    <div className="w-10 h-10 rounded bg-[#FF6F00] flex items-center justify-center">
      <span className="text-white font-bold text-sm">D2L</span>
    </div>
  );
}

// Modal for entering LMS instance URL
function LMSUrlInputModal({ provider, isOpen, onSubmit, onCancel, isConnecting }: LMSUrlInputModalProps): React.ReactElement | null {
  const [url, setUrl] = useState<string>('');
  const inputRef = useRef<HTMLInputElement>(null);

  const providerConfig: Record<LMSProvider, ProviderConfig> = {
    canvas: {
      name: 'Canvas',
      placeholder: 'https://canvas.university.edu',
      description: 'Enter your institution\'s Canvas URL'
    },
    blackboard: {
      name: 'Blackboard',
      placeholder: 'https://blackboard.university.edu',
      description: 'Enter your institution\'s Blackboard URL'
    },
    moodle: {
      name: 'Moodle',
      placeholder: 'https://moodle.university.edu',
      description: 'Enter your institution\'s Moodle URL'
    },
    brightspace: {
      name: 'Brightspace',
      placeholder: 'https://university.brightspace.com',
      description: 'Enter your institution\'s Brightspace URL'
    }
  };

  const config = provider ? providerConfig[provider] : { name: provider || '', placeholder: '', description: '' };

  // Focus input and reset URL when modal opens
  useEffect(() => {
    if (isOpen) {
      // Use setTimeout to reset URL after render to avoid setState in effect warning
      const timeoutId = setTimeout(() => {
        setUrl('');
        if (inputRef.current) {
          inputRef.current.focus();
        }
      }, 0);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen]);

  const handleSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (url.trim()) {
      onSubmit(url.trim());
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="lms-url-modal-title"
    >
      <div
        className="w-full max-w-md rounded-xl p-6"
        style={{ backgroundColor: 'var(--surface-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="lms-url-modal-title"
          className="text-xl font-semibold mb-2"
          style={{ color: 'var(--content-primary)' }}
        >
          Connect to {config.name}
        </h2>
        <p className="text-sm mb-4" style={{ color: 'var(--content-secondary)' }}>
          {config.description}
        </p>

        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            type="url"
            value={url}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
            placeholder={config.placeholder}
            required
            className="w-full px-4 py-3 rounded-lg mb-4 focus-visible:outline-2 focus-visible:outline-[var(--accent-primary)] focus-visible:outline-offset-2"
            style={{
              backgroundColor: 'var(--surface-tertiary)',
              color: 'var(--content-primary)',
              borderColor: 'var(--border-primary)'
            }}
            aria-label={`${config.name} instance URL`}
          />

          <div className="flex gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={isConnecting}
              className="flex-1 px-4 py-2 rounded-lg font-medium transition-colors"
              style={{
                backgroundColor: 'var(--surface-tertiary)',
                color: 'var(--content-primary)'
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!url.trim() || isConnecting}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
              style={{
                backgroundColor: 'var(--interactive-primary-bg)',
                color: 'var(--interactive-primary-fg)'
              }}
            >
              {isConnecting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                  Connecting...
                </>
              ) : (
                'Connect'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Modal for confirming disconnect
function DisconnectConfirmModal({ provider, isOpen, onConfirm, onCancel, isDisconnecting }: DisconnectConfirmModalProps): React.ReactElement | null {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isOpen && cancelButtonRef.current) {
      cancelButtonRef.current.focus();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const providerName = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : 'this integration';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="disconnect-modal-title"
    >
      <div
        className="w-full max-w-md rounded-xl p-6"
        style={{ backgroundColor: 'var(--surface-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-4">
          <div
            className="p-2 rounded-full"
            style={{ backgroundColor: 'var(--status-error-bg)' }}
          >
            <AlertTriangle className="w-6 h-6" style={{ color: 'var(--status-error-text)' }} aria-hidden="true" />
          </div>
          <h2
            id="disconnect-modal-title"
            className="text-xl font-semibold"
            style={{ color: 'var(--content-primary)' }}
          >
            Disconnect {providerName}?
          </h2>
        </div>

        <p className="text-sm mb-6" style={{ color: 'var(--content-secondary)' }}>
          This will remove the connection and stop syncing files from {providerName}.
          You can reconnect at any time.
        </p>

        <div className="flex gap-3">
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={onCancel}
            disabled={isDisconnecting}
            className="flex-1 px-4 py-2 rounded-lg font-medium transition-colors"
            style={{
              backgroundColor: 'var(--surface-tertiary)',
              color: 'var(--content-primary)'
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isDisconnecting}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
            style={{
              backgroundColor: 'var(--status-error-bg)',
              color: 'var(--status-error-text)'
            }}
          >
            {isDisconnecting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                Disconnecting...
              </>
            ) : (
              'Disconnect'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function IntegrationCard({
  name,
  description,
  icon,
  connected,
  email,
  lastSync,
  fileCount,
  isLTI,
  onConnect,
  onDisconnect,
  connecting,
  actionUrl,
  actionLabel,
}: IntegrationCardProps): React.ReactElement {
  return (
    <div
      className="rounded-xl border p-6"
      style={{
        backgroundColor: 'var(--surface-secondary)',
        borderColor: connected ? 'var(--accent-primary)' : 'var(--border-primary)'
      }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-4">
          {icon}
          <div>
            <h3 className="font-semibold text-lg" style={{ color: 'var(--content-primary)' }}>
              {name}
            </h3>
            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
              {description}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {connected ? (
            <span
              className="flex items-center gap-1 text-xs px-2 py-1 rounded-full"
              style={{ backgroundColor: 'var(--status-success-bg)', color: 'var(--status-success-text)' }}
            >
              <Check className="w-3 h-3" aria-hidden="true" />
              Connected
            </span>
          ) : (
            <span
              className="text-xs px-2 py-1 rounded-full"
              style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-secondary)' }}
            >
              Not Connected
            </span>
          )}
        </div>
      </div>

      {connected && (
        <div className="mb-4 space-y-2">
          {email && (
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--content-secondary)' }}>Account</span>
              <span style={{ color: 'var(--content-primary)' }}>{email}</span>
            </div>
          )}
          {fileCount !== undefined && (
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--content-secondary)' }}>Files Tracked</span>
              <span style={{ color: 'var(--content-primary)' }}>{fileCount.toLocaleString()}</span>
            </div>
          )}
          {lastSync && (
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--content-secondary)' }}>Last Sync</span>
              <span style={{ color: 'var(--content-primary)' }}>{lastSync}</span>
            </div>
          )}
        </div>
      )}

      <div className="flex gap-3">
        {isLTI ? (
          <a
            href={`/docs/${name.toLowerCase().replace(' ', '-')}-lti`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{
              backgroundColor: 'var(--surface-tertiary)',
              color: 'var(--content-primary)'
            }}
            aria-label={`Open LTI setup guide for ${name}`}
          >
            <ExternalLink className="w-4 h-4" aria-hidden="true" />
            LTI Setup Guide
          </a>
        ) : connected ? (
          <>
            <button
              onClick={onDisconnect}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors border"
              style={{
                borderColor: 'var(--status-error-text)',
                color: 'var(--status-error-text)',
                backgroundColor: 'transparent'
              }}
              aria-label={`Disconnect from ${name}`}
            >
              Disconnect
            </button>
            <Link
              to={actionUrl || '/integrations/files'}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium text-center transition-colors"
              style={{
                backgroundColor: 'var(--interactive-primary-bg)',
                color: 'var(--interactive-primary-fg)'
              }}
              aria-label={`View files synced from ${name}`}
            >
              {actionLabel || 'View Files'}
            </Link>
          </>
        ) : (
          <button
            onClick={onConnect}
            disabled={connecting}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            style={{
              backgroundColor: 'var(--interactive-primary-bg)',
              color: 'var(--interactive-primary-fg)'
            }}
            aria-label={connecting ? `Connecting to ${name}` : `Connect to ${name}`}
          >
            {connecting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                Connecting...
              </>
            ) : (
              <>
                <Cloud className="w-4 h-4" aria-hidden="true" />
                Connect
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}

export function Integrations(): React.ReactElement {
  const [integrations, setIntegrations] = useState<IntegrationsState>({
    google: { connected: false },
    microsoft: { connected: false },
    canvas: { connected: false },
    blackboard: { connected: false },
    moodle: { connected: false },
    brightspace: { connected: false },
  });
  const [loading, setLoading] = useState<boolean>(true);
  const [connecting, setConnecting] = useState<ProviderKey | null>(null);
  const [disconnecting, setDisconnecting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [folderSelectionProvider, setFolderSelectionProvider] = useState<'google' | 'microsoft' | null>(null);
  const [lmsUrlModal, setLmsUrlModal] = useState<LMSUrlModalState>({ isOpen: false, provider: null });
  const [disconnectModal, setDisconnectModal] = useState<DisconnectModalState>({ isOpen: false, provider: null });
  const toast = useToast();

  useEffect(() => {
    fetchIntegrationStatus();
  }, []);

  const fetchIntegrationStatus = async (): Promise<void> => {
    try {
      const response = await apiClient.get('/integrations/status');
      setIntegrations(response.data);
    } catch (err) {
      console.error('Failed to fetch integration status:', err);
    } finally {
      setLoading(false);
    }
  };

  // For LMS providers that require an instance URL, show the modal
  const handleConnect = (provider: ProviderKey): void => {
    trackEvent('dash-integration-connect', { provider });
    const lmsProviders: LMSProvider[] = ['canvas', 'blackboard', 'moodle', 'brightspace'];
    if (lmsProviders.includes(provider as LMSProvider)) {
      setLmsUrlModal({ isOpen: true, provider: provider as LMSProvider });
    } else {
      // For Google/Microsoft, directly initiate OAuth
      initiateConnection(provider);
    }
  };

  // Actually initiate the connection (called directly for Google/Microsoft, or after modal for LMS)
  const initiateConnection = async (provider: ProviderKey, instanceUrl: string | null = null): Promise<void> => {
    setConnecting(provider);
    setError(null);
    try {
      const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const requestData: Record<string, string> = {
        redirect_uri: `${apiBaseUrl}/${provider}/callback`,
      };

      // Add instance URL for LMS providers
      if (provider === 'canvas' && instanceUrl) {
        requestData.canvas_instance_url = instanceUrl;
      } else if (provider === 'blackboard' && instanceUrl) {
        requestData.blackboard_instance_url = instanceUrl;
      } else if (provider === 'moodle' && instanceUrl) {
        requestData.moodle_instance_url = instanceUrl;
      } else if (provider === 'brightspace' && instanceUrl) {
        requestData.brightspace_instance_url = instanceUrl;
      }

      const response = await apiClient.post(`/${provider}/connect`, requestData);

      if (response.data.auth_url || response.data.authorization_url) {
        window.location.href = response.data.auth_url || response.data.authorization_url;
      }
    } catch {
      toast.error(`Failed to connect to ${provider}. Please try again.`);
      setConnecting(null);
      setLmsUrlModal({ isOpen: false, provider: null });
    }
  };

  const handleLmsUrlSubmit = (url: string): void => {
    if (lmsUrlModal.provider) {
      initiateConnection(lmsUrlModal.provider, url);
    }
    // Modal stays open with loading state until redirect
  };

  const handleLmsUrlCancel = (): void => {
    setLmsUrlModal({ isOpen: false, provider: null });
    setConnecting(null);
  };

  // Show disconnect confirmation modal
  const handleDisconnect = (provider: ProviderKey): void => {
    setDisconnectModal({ isOpen: true, provider });
  };

  // Actually perform the disconnect
  const confirmDisconnect = async (): Promise<void> => {
    const provider = disconnectModal.provider;
    if (!provider) return;

    setDisconnecting(true);

    try {
      await apiClient.delete(`/${provider}/disconnect`);
      await fetchIntegrationStatus();
      toast.success(`Successfully disconnected from ${provider}.`);
      setDisconnectModal({ isOpen: false, provider: null });
    } catch {
      toast.error(`Failed to disconnect ${provider}. Please try again.`);
    } finally {
      setDisconnecting(false);
    }
  };

  const cancelDisconnect = (): void => {
    setDisconnectModal({ isOpen: false, provider: null });
  };

  const handleOpenFolderSelection = (): void => {
    // Determine which provider to show folder selection for
    // Priority: Google > Microsoft (if both connected, show Google)
    if (integrations.google.connected) {
      setFolderSelectionProvider('google');
    } else if (integrations.microsoft.connected) {
      setFolderSelectionProvider('microsoft');
    } else {
      toast.warning('Please connect Google Workspace or Microsoft 365 first.');
    }
  };

  const handleCloseFolderSelection = (): void => {
    setFolderSelectionProvider(null);
  };

  const handleFolderSelectionSave = (): void => {
    // Refresh integration status to show updated sync times
    fetchIntegrationStatus();
    setFolderSelectionProvider(null);
  };

  if (loading) {
    return (
      <FeatureGate
        feature="showIntegrations"
        featureName="Platform Integrations"
        description="Connect Google Drive, Microsoft OneDrive, Canvas, Blackboard, and other platforms to automatically scan your documents."
      >
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--accent-primary)' }} />
      </div>
      </FeatureGate>
    );
  }

  return (
    <FeatureGate
      feature="showIntegrations"
      featureName="Platform Integrations"
      description="Connect Google Drive, Microsoft OneDrive, Canvas, Blackboard, and other platforms to automatically scan your documents."
    >
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--content-primary)' }}>
            Platform Integrations
          </h1>
          <p style={{ color: 'var(--content-secondary)' }}>
            Connect your cloud storage and LMS platforms to automatically scan documents.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleOpenFolderSelection}
            disabled={!integrations.google.connected && !integrations.microsoft.connected}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              backgroundColor: 'var(--interactive-primary-bg)',
              color: 'var(--interactive-primary-fg)'
            }}
            aria-label="Select folders to sync from connected cloud storage"
          >
            <FolderTreeIcon className="w-4 h-4" aria-hidden="true" />
            Select Folders to Sync
          </button>
          <Link
            to="/integrations/settings"
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
            aria-label="Configure alert notification settings"
          >
            <Settings className="w-4 h-4" aria-hidden="true" />
            Alert Settings
          </Link>
        </div>
      </div>

      {/* Info message about folder selection */}
      <div
        className="mb-6 p-4 rounded-lg"
        style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
      >
        <p className="text-sm font-medium mb-1">Privacy-First Folder Selection</p>
        <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
          For privacy and control, you must select specific folders to sync instead of syncing your entire drive.
          Click "Select Folders to Sync" above to choose which folders to scan for accessibility issues.
        </p>
      </div>

      {/* Error message */}
      {error && (
        <div
          className="mb-6 p-4 rounded-lg flex items-center gap-3"
          style={{ backgroundColor: 'var(--status-error-bg)', color: 'var(--status-error-text)' }}
          role="alert"
        >
          <X className="w-5 h-5" aria-hidden="true" />
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-auto"
            aria-label="Dismiss error message"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Cloud Storage Section */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--content-primary)' }}>
          Cloud Storage
        </h2>
        <p className="text-xs mb-4" style={{ color: 'var(--content-tertiary)' }}>
          Your files are never stored on our servers. Aelira scans files in place and only saves accessibility results.
        </p>
        <div className="grid md:grid-cols-2 gap-6">
          <IntegrationCard
            name="Google Workspace"
            description="Scan Google Docs, Slides, and Sheets"
            icon={<GoogleIcon />}
            connected={integrations.google.connected}
            email={integrations.google.email}
            lastSync={integrations.google.lastSync}
            fileCount={integrations.google.fileCount}
            onConnect={() => handleConnect('google')}
            onDisconnect={() => handleDisconnect('google')}
            connecting={connecting === 'google'}
          />
          <IntegrationCard
            name="Microsoft 365"
            description="Scan OneDrive and SharePoint documents"
            icon={<MicrosoftIcon />}
            connected={integrations.microsoft.connected}
            email={integrations.microsoft.email}
            lastSync={integrations.microsoft.lastSync}
            fileCount={integrations.microsoft.fileCount}
            onConnect={() => handleConnect('microsoft')}
            onDisconnect={() => handleDisconnect('microsoft')}
            connecting={connecting === 'microsoft'}
          />
        </div>
      </div>

      {/* LMS Section */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--content-primary)' }}>
          Learning Management Systems
        </h2>
        <div className="grid md:grid-cols-2 gap-6">
          <IntegrationCard
            name="Canvas LMS"
            description="OAuth 2.0 integration for course file scanning and remediation"
            icon={<CanvasIcon />}
            connected={integrations.canvas.connected}
            email={integrations.canvas.email}
            lastSync={integrations.canvas.lastSync}
            fileCount={integrations.canvas.fileCount}
            onConnect={() => handleConnect('canvas')}
            onDisconnect={() => handleDisconnect('canvas')}
            connecting={connecting === 'canvas'}
            actionUrl="/integrations/canvas"
            actionLabel="Browse Courses"
          />
          <IntegrationCard
            name="Blackboard Learn"
            description="OAuth 2.0 integration for course content scanning and remediation"
            icon={<BlackboardIcon />}
            connected={integrations.blackboard.connected}
            email={integrations.blackboard.email}
            lastSync={integrations.blackboard.lastSync}
            fileCount={integrations.blackboard.fileCount}
            onConnect={() => handleConnect('blackboard')}
            onDisconnect={() => handleDisconnect('blackboard')}
            connecting={connecting === 'blackboard'}
          />
          <IntegrationCard
            name="Moodle LMS"
            description="World's most-used LMS - OAuth 2.0 integration for course file scanning"
            icon={<MoodleIcon />}
            connected={integrations.moodle.connected}
            email={integrations.moodle.email}
            lastSync={integrations.moodle.lastSync}
            fileCount={integrations.moodle.fileCount}
            onConnect={() => handleConnect('moodle')}
            onDisconnect={() => handleDisconnect('moodle')}
            connecting={connecting === 'moodle'}
          />
          <IntegrationCard
            name="D2L Brightspace"
            description="Community college favorite - Valence API integration for course content"
            icon={<BrightspaceIcon />}
            connected={integrations.brightspace.connected}
            email={integrations.brightspace.email}
            lastSync={integrations.brightspace.lastSync}
            fileCount={integrations.brightspace.fileCount}
            onConnect={() => handleConnect('brightspace')}
            onDisconnect={() => handleDisconnect('brightspace')}
            connecting={connecting === 'brightspace'}
          />
        </div>
      </div>

      {/* Info section */}
      <div
        className="p-6 rounded-xl"
        style={{ backgroundColor: 'var(--surface-tertiary)' }}
      >
        <h3 className="font-semibold mb-2" style={{ color: 'var(--content-primary)' }}>
          How it works
        </h3>
        <ul className="space-y-2 text-sm" style={{ color: 'var(--content-secondary)' }}>
          <li className="flex items-start gap-2">
            <span className="font-bold" style={{ color: 'var(--accent-primary)' }}>1.</span>
            Connect your cloud storage or LMS using OAuth 2.0 / LTI 1.3
          </li>
          <li className="flex items-start gap-2">
            <span className="font-bold" style={{ color: 'var(--accent-primary)' }}>2.</span>
            Aelira automatically discovers and syncs your files
          </li>
          <li className="flex items-start gap-2">
            <span className="font-bold" style={{ color: 'var(--accent-primary)' }}>3.</span>
            Files are scanned for accessibility issues using AI
          </li>
          <li className="flex items-start gap-2">
            <span className="font-bold" style={{ color: 'var(--accent-primary)' }}>4.</span>
            Auto-remediation fixes issues and uploads corrected files back
          </li>
          <li className="flex items-start gap-2">
            <span className="font-bold" style={{ color: 'var(--accent-primary)' }}>5.</span>
            Real-time webhooks detect changes and re-scan automatically
          </li>
        </ul>
      </div>

      {/* Folder Selection Modal */}
      <FolderSelectionModal
        provider={folderSelectionProvider || 'google'}
        isOpen={folderSelectionProvider !== null}
        onClose={handleCloseFolderSelection}
        onSave={handleFolderSelectionSave}
      />

      {/* LMS URL Input Modal */}
      <LMSUrlInputModal
        provider={lmsUrlModal.provider}
        isOpen={lmsUrlModal.isOpen}
        onSubmit={handleLmsUrlSubmit}
        onCancel={handleLmsUrlCancel}
        isConnecting={connecting === lmsUrlModal.provider}
      />

      {/* Disconnect Confirmation Modal */}
      <DisconnectConfirmModal
        provider={disconnectModal.provider}
        isOpen={disconnectModal.isOpen}
        onConfirm={confirmDisconnect}
        onCancel={cancelDisconnect}
        isDisconnecting={disconnecting}
      />
    </div>
    </FeatureGate>
  );
}

export default Integrations;
