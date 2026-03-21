import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { BarChart3, ArrowUpRight, AlertCircle } from 'lucide-react';
import { apiClient } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { trackEvent } from './Analytics';

// ============================================================================
// Types
// ============================================================================

interface QuotaUsage {
  used: number;
  limit: number;
}

interface QuotaResponse {
  scans?: QuotaUsage;
  pages?: QuotaUsage;
  unlimited?: boolean;
  resets_at?: string;
}

interface ProgressBarProps {
  value: number;
  max: number;
  label: string;
  warningThreshold?: number;
}

// ============================================================================
// ProgressBar Component
// ============================================================================

function ProgressBar({
  value,
  max,
  label,
  warningThreshold = 80,
}: ProgressBarProps): React.ReactElement {
  const percentage = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const isWarning = percentage >= warningThreshold;
  const isExceeded = percentage >= 100;

  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-secondary truncate">{label}</span>
        <span
          className="text-xs font-medium"
          style={{
            color: isExceeded
              ? 'var(--content-error)'
              : isWarning
                ? 'var(--content-warning)'
                : 'var(--content-secondary)',
          }}
        >
          {max === -1 ? 'Unlimited' : `${value}/${max}`}
        </span>
      </div>
      <div
        className="h-1.5 rounded-full overflow-hidden"
        style={{ backgroundColor: 'var(--surface-tertiary)' }}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: max === -1 ? '0%' : `${percentage}%`,
            backgroundColor: isExceeded
              ? 'var(--content-error)'
              : isWarning
                ? 'var(--content-warning)'
                : 'var(--accent-primary)',
          }}
        />
      </div>
    </div>
  );
}

// ============================================================================
// QuotaBar Component
// ============================================================================

export function QuotaBar(): React.ReactElement | null {
  const { isAuthenticated } = useAuth();
  const [quota, setQuota] = useState<QuotaResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchQuota = useCallback(async (): Promise<void> => {
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const response = await apiClient.get<QuotaResponse>('/auth/quota');
      setQuota(response.data);
    } catch (err) {
      const fetchErr = err as Error;
      setError(fetchErr.message);
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  // Fetch immediately and refresh every 5 minutes
  usePolling(fetchQuota, 5 * 60 * 1000, isAuthenticated);

  // Don't show for unlimited tiers
  if (quota?.unlimited) {
    return null;
  }

  // Loading state
  if (loading) {
    return (
      <div
        className="rounded-lg p-3 animate-pulse"
        style={{ backgroundColor: 'var(--surface-secondary)' }}
      >
        <div className="h-4 w-24 rounded" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
      </div>
    );
  }

  // Error state — show a subtle indicator so user knows quota couldn't load
  if (error || !quota) {
    return (
      <div
        className="rounded-lg p-3 flex items-center gap-2"
        style={{ backgroundColor: 'var(--surface-secondary)' }}
      >
        <AlertCircle className="w-4 h-4 flex-shrink-0 text-tertiary" />
        <span className="text-xs text-tertiary">Usage data unavailable</span>
      </div>
    );
  }

  const scansUsed = quota.scans?.used || 0;
  const scansLimit = quota.scans?.limit || 10;
  const pagesUsed = quota.pages?.used || 0;
  const pagesLimit = quota.pages?.limit || 500;

  const scansPercentage = scansLimit > 0 ? (scansUsed / scansLimit) * 100 : 0;
  const isNearLimit = scansPercentage >= 80;

  // Format reset date
  let resetText = 'Resets monthly';
  if (quota.resets_at) {
    const resetDate = new Date(quota.resets_at);
    const now = new Date();
    const daysUntilReset = Math.ceil((resetDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
    resetText = daysUntilReset <= 1 ? 'Resets tomorrow' : `Resets in ${daysUntilReset} days`;
  }

  return (
    <div
      className="rounded-lg p-3"
      style={{
        backgroundColor: isNearLimit ? 'var(--surface-warning-subtle)' : 'var(--surface-secondary)',
        border: isNearLimit ? '1px solid var(--content-warning)' : 'none',
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-secondary" />
          <span className="text-sm font-medium text-primary">Free Plan Usage</span>
        </div>
        {isNearLimit && (
          <span
            className="text-xs px-2 py-0.5 rounded-full font-medium"
            style={{
              backgroundColor: 'var(--content-warning)',
              color: 'white',
            }}
          >
            Near Limit
          </span>
        )}
      </div>

      <div className="space-y-3">
        <ProgressBar value={scansUsed} max={scansLimit} label="Documents" />
        <ProgressBar value={pagesUsed} max={pagesLimit} label="Pages" />
      </div>

      <div className="mt-3 flex items-center justify-between">
        <span className="text-xs text-tertiary">{resetText}</span>
        <a
          href="/pricing"
          className="text-xs font-medium flex items-center gap-1 hover:opacity-80 transition-opacity"
          style={{ color: 'var(--accent-primary)' }}
          onClick={() => trackEvent('dash-upgrade-click', { source: 'quota_bar', target_tier: 'unknown' })}
        >
          Upgrade
          <ArrowUpRight className="w-3 h-3" />
        </a>
      </div>

      {scansPercentage >= 100 && (
        <div
          className="mt-3 rounded-lg p-2 flex items-center gap-2"
          style={{
            backgroundColor: 'var(--surface-error-subtle)',
            border: '1px solid var(--content-error)',
          }}
        >
          <AlertCircle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--content-error)' }} />
          <p className="text-xs" style={{ color: 'var(--content-error)' }}>
            Monthly limit reached. Upgrade or wait for reset.
          </p>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// QuotaBarCompact Component - Compact version for sidebar
// ============================================================================

export function QuotaBarCompact(): React.ReactElement | null {
  const { isAuthenticated } = useAuth();
  const [quota, setQuota] = useState<QuotaResponse | null>(null);

  useEffect(() => {
    async function fetchQuota(): Promise<void> {
      if (!isAuthenticated) return;

      try {
        const response = await apiClient.get<QuotaResponse>('/auth/quota');
        setQuota(response.data);
      } catch {
        // Silently fail
      }
    }

    fetchQuota();
  }, [isAuthenticated]);

  // Don't show for unlimited tiers
  if (!quota || quota.unlimited) {
    return null;
  }

  const scansUsed = quota.scans?.used || 0;
  const scansLimit = quota.scans?.limit || 10;
  const remaining = scansLimit - scansUsed;

  return (
    <div className="px-3 py-2 rounded-lg text-xs" style={{ backgroundColor: 'var(--surface-secondary)' }}>
      <span className="text-secondary">
        {remaining > 0 ? (
          <>
            <span className="font-medium text-primary">{remaining}</span> scans left
          </>
        ) : (
          <span style={{ color: 'var(--content-error)' }}>Limit reached</span>
        )}
      </span>
    </div>
  );
}
