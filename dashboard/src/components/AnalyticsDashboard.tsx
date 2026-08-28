import React, { useState, useEffect } from 'react';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Calendar,
  Target,
  Clock,
  AlertCircle,
} from 'lucide-react';
import { scansApi } from '../api/scans';
import type { DeadlineProjection, TrendAnalysis } from '../api/scans';
import { unwrapResponse } from '../utils/apiUnwrap';
import { hasDatedDeadline } from '../types/deadline';

// ============================================================================
// Types
// ============================================================================

interface AnalyticsDashboardProps {
  departmentId: string;
}

type TrendDirection = 'improving' | 'declining' | 'stable' | 'insufficient_data';

interface IssueStats {
  total_issues: number;
  open_issues: number;
  in_progress_issues: number;
  resolved_issues: number;
  resolution_rate: number;
  auto_fixable_issues: number;
  auto_fixed_issues: number;
}

interface TrendIconProps {
  direction: TrendDirection | undefined;
  size?: number;
}

// ============================================================================
// Component
// ============================================================================

/**
 * Analytics Dashboard Component
 *
 * Displays advanced analytics including:
 * - Automated scan-score projection at the configured target date
 * - Week-over-week trend analysis
 * - Issue resolution rate
 */
export function AnalyticsDashboard({ departmentId }: AnalyticsDashboardProps): React.ReactElement {
  const [projection, setProjection] = useState<DeadlineProjection | null>(null);
  const [analysis, setAnalysis] = useState<TrendAnalysis | null>(null);
  const [issueStats, setIssueStats] = useState<IssueStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAnalytics = async (): Promise<void> => {
      if (!departmentId) return;

      setLoading(true);
      try {
        // Fetch all analytics data in parallel
        const [projectionData, analysisData, statsData] = await Promise.all([
          scansApi.getDeadlineProjection(departmentId).catch(() => null),
          scansApi.getTrendAnalysis(departmentId, 7, 7).catch(() => null),
          scansApi.getIssueStats(departmentId).catch(() => null),
        ]);

        // Backend may wrap responses — handle both wrapped and unwrapped
        if (projectionData) setProjection(projectionData.projection);
        if (analysisData) setAnalysis(analysisData.analysis);
        if (statsData) setIssueStats(unwrapResponse<IssueStats>(statsData, 'stats'));
      } catch (err) {
        console.error('Failed to fetch analytics:', err);
        setError('Failed to load analytics data');
      } finally {
        setLoading(false);
      }
    };

    fetchAnalytics();
  }, [departmentId]);

  if (loading) {
    return (
      <div className="card-glass">
        <h2 className="text-xl font-semibold text-primary mb-4">Analytics & Projections</h2>
        <div className="flex items-center justify-center h-32">
          <div className="text-tertiary">Loading analytics...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card-glass">
        <h2 className="text-xl font-semibold text-primary mb-4">Analytics & Projections</h2>
        <div className="flex items-center justify-center h-32">
          <div className="text-secondary">{error}</div>
        </div>
      </div>
    );
  }

  // Format the trend direction icon
  const TrendIcon: React.FC<TrendIconProps> = ({ direction, size = 5 }) => {
    const className = `w-${size} h-${size}`;
    if (direction === 'improving') {
      return <TrendingUp className={`${className} text-[var(--feature-success-content)]`} />;
    } else if (direction === 'declining') {
      return <TrendingDown className={`${className} text-[var(--feature-danger-content)]`} />;
    }
    return <Minus className={`${className} text-tertiary`} />;
  };

  return (
    <div className="card-glass">
      <h2 className="text-xl font-semibold text-primary mb-6">Analytics & Projections</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Deadline Projection Card */}
        {projection && projection.projection_available && hasDatedDeadline(projection.deadline) ? (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <Calendar className="w-5 h-5 text-accent" />
              <h3 className="font-semibold text-primary">{projection.deadline.deadline_label} Target</h3>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Days Remaining</span>
                <span className="font-bold text-primary">{projection.days_until_deadline}</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Current Score</span>
                <span
                  className={`font-bold ${
                    (projection.current_avg_score ?? 0) >= 90
                      ? 'text-[var(--feature-success-content)]'
                      : (projection.current_avg_score ?? 0) >= 70
                        ? 'text-[var(--feature-warning-content)]'
                        : 'text-[var(--feature-danger-content)]'
                  }`}
                >
                  {projection.current_avg_score}/100
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Projected Scan Score</span>
                <span
                  className={`font-bold ${
                    (projection.projected_score_at_deadline ?? 0) >= 90
                      ? 'text-[var(--feature-success-content)]'
                      : (projection.projected_score_at_deadline ?? 0) >= 70
                        ? 'text-[var(--feature-warning-content)]'
                        : 'text-[var(--feature-danger-content)]'
                  }`}
                >
                  {projection.projected_score_at_deadline}/100
                </span>
              </div>

              <div
                className={`mt-4 p-3 rounded-lg ${
                  projection.will_meet_deadline
                    ? 'bg-[var(--feature-success-surface)]'
                    : 'bg-[var(--feature-danger-surface)]'
                }`}
              >
                <div className="flex items-center space-x-2">
                  {projection.will_meet_deadline ? (
                    <Target className="w-5 h-5 text-[var(--feature-success-content)]" />
                  ) : (
                    <AlertCircle className="w-5 h-5 text-[var(--feature-danger-content)]" />
                  )}
                  <span
                    className={`text-sm font-medium ${
                      projection.will_meet_deadline
                        ? 'text-[var(--feature-success-content)]'
                        : 'text-[var(--feature-danger-content)]'
                    }`}
                  >
                    {projection.will_meet_deadline
                      ? 'Scan Score on Target'
                      : `Need ${projection.required_improvement_per_day?.toFixed(2)} pts/day`}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <Calendar className="w-5 h-5 text-accent" />
              <h3 className="font-semibold text-primary">
                {hasDatedDeadline(projection?.deadline)
                  ? `${projection.deadline.deadline_label} Target`
                  : 'Accessibility Target'}
              </h3>
            </div>
            <p className="text-sm text-secondary">
              {projection?.message || 'Need more verified scan data to project the target score.'}
            </p>
          </div>
        )}

        {/* Week-over-Week Analysis Card */}
        {analysis ? (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <TrendIcon direction={analysis.trend_direction} />
              <h3 className="font-semibold text-primary">Weekly Trend</h3>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">This Week</span>
                <span
                  className={`font-bold ${
                    analysis.current_avg_score === null
                      ? 'text-tertiary'
                      : analysis.current_avg_score >= 90
                      ? 'text-[var(--feature-success-content)]'
                      : analysis.current_avg_score >= 70
                        ? 'text-[var(--feature-warning-content)]'
                        : 'text-[var(--feature-danger-content)]'
                  }`}
                >
                  {analysis.current_avg_score === null
                    ? 'Not assessed'
                    : `${analysis.current_avg_score}/100`}
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Last Week</span>
                <span className="text-primary">
                  {analysis.previous_avg_score === null
                    ? 'Not assessed'
                    : `${analysis.previous_avg_score}/100`}
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Change</span>
                <span
                  className={`font-bold ${
                    analysis.score_change === null
                      ? 'text-tertiary'
                      : analysis.score_change > 0
                      ? 'text-[var(--feature-success-content)]'
                      : analysis.score_change < 0
                        ? 'text-[var(--feature-danger-content)]'
                        : 'text-tertiary'
                  }`}
                >
                  {analysis.score_change === null || analysis.score_change_pct === null ? (
                    'Not enough data'
                  ) : (
                    <>
                      {analysis.score_change > 0 ? '+' : ''}
                      {analysis.score_change} pts ({analysis.score_change_pct > 0 ? '+' : ''}
                      {analysis.score_change_pct}%)
                    </>
                  )}
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Issues Change</span>
                <span
                  className={`font-bold ${
                    analysis.issues_change < 0
                      ? 'text-[var(--feature-success-content)]'
                      : analysis.issues_change > 0
                        ? 'text-[var(--feature-danger-content)]'
                        : 'text-tertiary'
                  }`}
                >
                  {analysis.issues_change > 0 ? '+' : ''}
                  {analysis.issues_change}
                </span>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <Minus className="w-5 h-5 text-tertiary" />
              <h3 className="font-semibold text-primary">Weekly Trend</h3>
            </div>
            <p className="text-sm text-secondary">Need more historical data for trend analysis.</p>
          </div>
        )}

        {/* Issue Resolution Stats Card */}
        {issueStats ? (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <Clock className="w-5 h-5 text-accent" />
              <h3 className="font-semibold text-primary">Issue Tracking</h3>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Total Issues</span>
                <span className="font-bold text-primary">{issueStats.total_issues}</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Open</span>
                <span className="font-bold text-[var(--feature-danger-content)]">
                  {issueStats.open_issues}
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">In Progress</span>
                <span className="font-bold text-[var(--feature-warning-content)]">
                  {issueStats.in_progress_issues}
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-sm text-secondary">Resolved</span>
                <span className="font-bold text-[var(--feature-success-content)]">
                  {issueStats.resolved_issues}
                </span>
              </div>

              <div className="mt-2 pt-2 border-t border-primary">
                <div className="flex justify-between items-center">
                  <span className="text-sm text-secondary">Resolution Rate</span>
                  <span
                    className={`font-bold ${
                      issueStats.resolution_rate >= 80
                        ? 'text-[var(--feature-success-content)]'
                        : issueStats.resolution_rate >= 50
                          ? 'text-[var(--feature-warning-content)]'
                          : 'text-[var(--feature-danger-content)]'
                    }`}
                  >
                    {issueStats.resolution_rate}%
                  </span>
                </div>
              </div>

              {issueStats.auto_fixable_issues > 0 && (
                <div className="mt-2 p-2 rounded bg-surface-accent-subtle">
                  <span className="text-xs text-accent">
                    {issueStats.auto_fixable_issues} issues can be auto-fixed (
                    {issueStats.auto_fixed_issues} already fixed)
                  </span>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-lg glass-subtle">
            <div className="flex items-center space-x-2 mb-3">
              <Clock className="w-5 h-5 text-accent" />
              <h3 className="font-semibold text-primary">Issue Tracking</h3>
            </div>
            <p className="text-sm text-secondary">
              No tracked issues yet. Issues from scans will appear here.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
