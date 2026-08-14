import React from 'react';
import { Zap, Search, Eye, TrendingUp, Target } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface ScanResult {
  engines_used?: string[];
  scan_mode?: 'quick' | 'comprehensive' | 'deep';
  axe_issues?: number;
  pa11y_issues?: number;
  issues_found_by_both?: number;
  unique_issues?: number;
  estimated_coverage_pct?: number;
  axe_duration_ms?: number;
  pa11y_duration_ms?: number;
}

interface EngineComparisonStatsProps {
  scanResult: ScanResult | null | undefined;
}

interface ModeInfo {
  label: string;
  color: string;
  desc: string;
}

// ============================================================================
// Component
// ============================================================================

/**
 * Engine Comparison Stats Component
 *
 * Displays multi-engine scanning statistics showing:
 * - Which engines were used (axe-core, Pa11y, AI vision)
 * - Issues found by each engine
 * - Duplicate issues detected by multiple engines
 * - Unique issues after deduplication
 * - Estimated WCAG coverage percentage
 * - Scan duration per engine
 *
 * Part of Pa11y integration (multi-engine scanning).
 */
export function EngineComparisonStats({
  scanResult,
}: EngineComparisonStatsProps): React.ReactElement | null {
  // Only show if multi-engine data is available
  if (!scanResult?.engines_used || scanResult.engines_used.length <= 1) {
    return null;
  }

  const {
    engines_used = [],
    scan_mode,
    axe_issues = 0,
    pa11y_issues = 0,
    issues_found_by_both = 0,
    unique_issues = 0,
    estimated_coverage_pct = 90,
    axe_duration_ms,
    pa11y_duration_ms,
  } = scanResult;

  const hasAxe = engines_used.includes('axe-core');
  const hasPa11y = engines_used.includes('pa11y');
  const hasAI = engines_used.includes('ai-vision');

  // Calculate incremental value of Pa11y
  const pa11yOnlyIssues = hasPa11y ? pa11y_issues - issues_found_by_both : 0;
  const incrementalValue =
    hasPa11y && axe_issues > 0 ? Math.round((pa11yOnlyIssues / unique_issues) * 100) : 0;

  // Mode descriptions
  const modeInfoMap: Record<string, ModeInfo> = {
    quick: { label: 'Quick Scan', color: 'blue', desc: '~90% coverage' },
    comprehensive: { label: 'Comprehensive Scan', color: 'purple', desc: '~95%+ coverage' },
    deep: { label: 'Deep Scan', color: 'indigo', desc: 'Maximum confidence' },
  };
  const modeInfo: ModeInfo = scan_mode
    ? modeInfoMap[scan_mode] || { label: 'Standard Scan', color: 'gray', desc: 'Multi-engine' }
    : { label: 'Standard Scan', color: 'gray', desc: 'Multi-engine' };

  return (
    <div className="card bg-[var(--surface-secondary)] border-[var(--border-primary)]">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-lg font-semibold text-primary flex items-center gap-2">
            <Target className="w-5 h-5" />
            Multi-Engine Scan Results
          </h3>
          <p className="text-sm text-secondary mt-1">
            {modeInfo.label} • {modeInfo.desc}
          </p>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold text-primary">
            {Math.round(estimated_coverage_pct)}%
          </div>
          <div className="text-xs text-secondary">WCAG Coverage</div>
        </div>
      </div>

      {/* Engine Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        {/* axe-core stats */}
        {hasAxe && (
          <div className="p-4 bg-[var(--surface-primary)] rounded-lg border border-[var(--border-primary)]">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="w-4 h-4 text-[var(--content-info)]" />
              <span className="text-sm font-medium text-[var(--feature-info-content)]">axe-core</span>
            </div>
            <div className="text-2xl font-bold text-primary">{axe_issues}</div>
            <div className="text-xs text-secondary">issues found</div>
            {axe_duration_ms && (
              <div className="text-xs text-secondary mt-1">
                {(axe_duration_ms / 1000).toFixed(1)}s scan time
              </div>
            )}
          </div>
        )}

        {/* Pa11y stats */}
        {hasPa11y && (
          <div className="p-4 bg-[var(--surface-primary)] rounded-lg border border-[var(--border-accent)]">
            <div className="flex items-center gap-2 mb-2">
              <Search className="w-4 h-4 text-[var(--content-accent)]" />
              <span className="text-sm font-medium text-[var(--feature-primary-content)]">
                Pa11y
              </span>
            </div>
            <div className="text-2xl font-bold text-primary">{pa11y_issues}</div>
            <div className="text-xs text-secondary">issues found</div>
            {pa11y_duration_ms && (
              <div className="text-xs text-secondary mt-1">
                {(pa11y_duration_ms / 1000).toFixed(1)}s scan time
              </div>
            )}
          </div>
        )}

        {/* AI Vision stats */}
        {hasAI && (
          <div className="p-4 bg-[var(--surface-primary)] rounded-lg border border-[var(--border-accent)]">
            <div className="flex items-center gap-2 mb-2">
              <Eye className="w-4 h-4 text-[var(--content-accent)]" />
              <span className="text-sm font-medium text-[var(--feature-primary-content)]">
                AI Vision
              </span>
            </div>
            <div className="text-2xl font-bold text-primary">-</div>
            <div className="text-xs text-secondary">visual analysis</div>
          </div>
        )}
      </div>

      {/* Deduplication Stats */}
      {hasPa11y && (
        <div className="p-4 bg-[var(--surface-primary)] rounded-lg border border-[var(--border-primary)]">
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="text-sm text-secondary mb-1">Found by Both</div>
              <div className="text-xl font-bold text-[var(--feature-success-content)]">
                {issues_found_by_both}
              </div>
              <div className="text-xs text-secondary">duplicates</div>
            </div>
            <div>
              <div className="text-sm text-secondary mb-1">Pa11y Only</div>
              <div className="text-xl font-bold text-[var(--content-accent)]">
                {pa11yOnlyIssues}
              </div>
              <div className="text-xs text-secondary">unique issues</div>
            </div>
            <div>
              <div className="text-sm text-secondary mb-1">Total Unique</div>
              <div className="text-xl font-bold text-primary">{unique_issues}</div>
              <div className="text-xs text-secondary">after deduplication</div>
            </div>
          </div>

          {/* Incremental Value Indicator */}
          {incrementalValue > 0 && (
            <div className="mt-4 p-3 bg-[var(--feature-primary-surface)] rounded-lg border border-[var(--border-accent)]">
              <div className="flex items-center gap-2 text-sm">
                <TrendingUp className="w-4 h-4 text-[var(--content-accent)]" />
                <span className="font-medium text-[var(--feature-primary-content)]">
                  Pa11y found {incrementalValue}% more unique issues
                </span>
              </div>
              <p className="text-xs text-[var(--feature-primary-content)] mt-1">
                Multi-engine scanning catches edge cases that single-engine scans miss.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Mode Upgrade Suggestion */}
      {scan_mode === 'quick' && (
        <div className="mt-4 p-3 bg-[var(--feature-info-surface)] rounded-lg border border-[var(--border-primary)]">
          <p className="text-sm text-[var(--feature-info-content)]">
            <strong>Tip:</strong> Try <strong>Comprehensive mode</strong> to run both axe-core and
            Pa11y for ~95%+ WCAG coverage.
          </p>
        </div>
      )}
    </div>
  );
}
