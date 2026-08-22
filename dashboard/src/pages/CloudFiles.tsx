import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Cloud, Filter, RefreshCw, ExternalLink, File, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { apiClient } from '../api/client';
import { FeatureGate } from '../components/FeatureGate';

// Type definitions
type ProviderFilter = 'all' | 'google' | 'microsoft' | 'canvas';

interface CloudFile {
  id: string;
  provider: 'google' | 'microsoft' | 'canvas';
  file_name: string;
  file_type: string;
  file_size_bytes: number | null;
  last_scanned_at: string | null;
  last_compliance_score: number | null;
  web_view_link: string | null;
  needs_rescan: boolean;
}

interface ComplianceScoreProps {
  score: number | null | undefined;
}

// Provider icons (reuse from Integrations)
function GoogleIcon(): React.ReactElement {
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  );
}

function MicrosoftIcon(): React.ReactElement {
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24">
      <path fill="#F25022" d="M1 1h10v10H1z"/>
      <path fill="#7FBA00" d="M13 1h10v10H13z"/>
      <path fill="#00A4EF" d="M1 13h10v10H1z"/>
      <path fill="#FFB900" d="M13 13h10v10H13z"/>
    </svg>
  );
}

function CanvasIcon(): React.ReactElement {
  return (
    <div className="w-5 h-5 rounded-full bg-[#E74C3C] flex items-center justify-center">
      <span className="text-white font-bold text-[10px]">C</span>
    </div>
  );
}

function formatFileSize(bytes: number | null): string {
  if (!bytes) return 'N/A';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

function formatDate(isoString: string | null): string {
  if (!isoString) return 'Never';
  const date = new Date(isoString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

function ComplianceScore({ score }: ComplianceScoreProps): React.ReactElement {
  if (score === null || score === undefined) {
    return (
      <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
        Not scanned
      </span>
    );
  }

  const getColor = (): string => {
    if (score >= 90) return 'var(--status-success-text)';
    if (score >= 70) return 'var(--status-warning-text)';
    return 'var(--status-error-text)';
  };

  const getIcon = (): React.ReactElement => {
    if (score >= 90) return <CheckCircle2 className="w-4 h-4" />;
    return <AlertCircle className="w-4 h-4" />;
  };

  return (
    <div className="flex items-center gap-2" style={{ color: getColor() }}>
      {getIcon()}
      <span className="font-semibold">{score.toFixed(0)}%</span>
    </div>
  );
}

export function CloudFiles(): React.ReactElement {
  const [files, setFiles] = useState<CloudFile[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState<ProviderFilter>('all');
  const [refreshing, setRefreshing] = useState<boolean>(false);

  const fetchFiles = useCallback(async (): Promise<void> => {
    try {
      setRefreshing(true);
      const params = providerFilter !== 'all' ? { provider: providerFilter } : {};
      const response = await apiClient.get('/integrations/files', { params });
      setFiles(response.data.files || []);
    } catch (err) {
      console.error('Failed to fetch cloud files:', err);
      setError('Failed to load cloud files. Please try again.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [providerFilter]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  if (loading) {
    return (
      <FeatureGate
        feature="showIntegrations"
        featureName="Cloud Files"
        description="View and manage files synced from your connected cloud storage providers."
      >
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--accent)' }} />
      </div>
      </FeatureGate>
    );
  }

  return (
    <FeatureGate
      feature="showIntegrations"
      featureName="Cloud Files"
      description="View and manage files synced from your connected cloud storage providers."
    >
    <div className="max-w-7xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--content-primary)' }}>
            Cloud Files
          </h1>
          <p style={{ color: 'var(--content-secondary)' }}>
            Files synced from your connected cloud storage providers
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={fetchFiles}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <Link
            to="/integrations"
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors"
            style={{ backgroundColor: 'var(--accent-solid)' }}
          >
            <Cloud className="w-4 h-4" />
            Manage Integrations
          </Link>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-6 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4" style={{ color: 'var(--content-secondary)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--content-secondary)' }}>
            Filter by:
          </span>
        </div>
        <div className="flex gap-2">
          {(['all', 'google', 'microsoft', 'canvas'] as ProviderFilter[]).map((filter) => (
            <button
              key={filter}
              onClick={() => setProviderFilter(filter)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                providerFilter === filter ? 'text-white' : ''
              }`}
              style={{
                backgroundColor: providerFilter === filter ? 'var(--accent-solid)' : 'var(--surface-tertiary)',
                color: providerFilter === filter ? 'white' : 'var(--content-primary)',
              }}
            >
              {filter.charAt(0).toUpperCase() + filter.slice(1)}
            </button>
          ))}
        </div>
        <span className="ml-auto text-sm" style={{ color: 'var(--content-secondary)' }}>
          {files.length} file{files.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Error message */}
      {error && (
        <div
          className="mb-6 p-4 rounded-lg"
          style={{ backgroundColor: 'var(--status-error-bg)', color: 'var(--status-error-text)' }}
        >
          {error}
        </div>
      )}

      {/* Files table */}
      {files.length === 0 ? (
        <div
          className="rounded-xl p-12 text-center"
          style={{ backgroundColor: 'var(--surface-secondary)' }}
        >
          <Cloud className="w-16 h-16 mx-auto mb-4" style={{ color: 'var(--content-tertiary)' }} />
          <h3 className="text-lg font-semibold mb-2" style={{ color: 'var(--content-primary)' }}>
            No files found
          </h3>
          <p className="mb-6" style={{ color: 'var(--content-secondary)' }}>
            {providerFilter === 'all'
              ? 'Connect a cloud storage provider to start syncing files.'
              : `No files found from ${providerFilter}.`}
          </p>
          <Link
            to="/integrations"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
            style={{ backgroundColor: 'var(--accent-solid)' }}
          >
            <Cloud className="w-4 h-4" />
            Connect Provider
          </Link>
        </div>
      ) : (
        <div
          className="rounded-xl overflow-hidden"
          style={{ backgroundColor: 'var(--surface-secondary)' }}
        >
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Provider
                  </th>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    File Name
                  </th>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Type
                  </th>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Size
                  </th>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Last Scanned
                  </th>
                  <th className="text-left px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Compliance
                  </th>
                  <th className="text-right px-6 py-4 text-sm font-semibold" style={{ color: 'var(--content-secondary)' }}>
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {files.map((file, index) => (
                  <tr
                    key={file.id}
                    style={{
                      borderBottom: index !== files.length - 1 ? '1px solid var(--border-primary)' : 'none',
                    }}
                  >
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        {file.provider === 'google' ? <GoogleIcon /> : file.provider === 'canvas' ? <CanvasIcon /> : <MicrosoftIcon />}
                        <span className="text-sm capitalize" style={{ color: 'var(--content-secondary)' }}>
                          {file.provider}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <File className="w-4 h-4" style={{ color: 'var(--content-secondary)' }} />
                        <span className="font-medium" style={{ color: 'var(--content-primary)' }}>
                          {file.file_name}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className="px-2 py-1 rounded text-xs font-medium uppercase"
                        style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-secondary)' }}
                      >
                        {file.file_type}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                        {formatFileSize(file.file_size_bytes)}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                        {formatDate(file.last_scanned_at)}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <ComplianceScore score={file.last_compliance_score} />
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {file.web_view_link && (
                          <a
                            href={file.web_view_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="p-2 rounded-lg transition-colors"
                            style={{ backgroundColor: 'var(--surface-tertiary)' }}
                            title="Open in provider"
                          >
                            <ExternalLink className="w-4 h-4" style={{ color: 'var(--content-secondary)' }} />
                          </a>
                        )}
                        {file.needs_rescan ? (
                          /* This was a Scan button with no click handler and
                             no provider-agnostic endpoint behind it. Scans
                             start from the integration's own page, so this
                             states the file's condition instead of offering
                             an action that never happened. */
                          <span
                            className="px-3 py-1.5 rounded-lg text-sm font-medium"
                            style={{
                              backgroundColor: 'var(--surface-tertiary)',
                              color: 'var(--content-secondary)',
                            }}
                            title="Start a scan from this file's integration page"
                          >
                            Needs rescan
                          </span>
                        ) : file.last_scanned_at ? (
                          <Link
                            to={`/scan/${file.id}`}
                            className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                            style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
                          >
                            View Results
                          </Link>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
    </FeatureGate>
  );
}

export default CloudFiles;
