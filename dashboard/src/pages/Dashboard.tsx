import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FileText, TrendingUp, Loader, Upload, Eye, Download, Wrench,
  Calendar, ScanLine,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { TooltipContentProps } from 'recharts/types/component/Tooltip';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';
import { scansApi } from '../api/scans';
import type { DepartmentReviewSummary } from '../api/scans';
import { unwrapResponse } from '../utils/apiUnwrap';
import { AnalyticsDashboard } from '../components/AnalyticsDashboard';
import { EvidenceReportAction } from '../components/EvidenceReportAction';
import { ConfidenceBadge } from '../components/review/ConfidenceBadge';
import { useAuth } from '../context/auth-context';
import { useToast } from '../context/toast-context';
import { useFeatureAccess } from '../hooks/useFeatureAccess';
import { trackEvent } from '../utils/analytics';
import {
  normalizeCurrentComplianceStats,
  type CurrentDashboardStats,
  type RawCurrentComplianceStats,
} from '../utils/currentCompliance';
import { hasDatedDeadline } from '../types/deadline';
import { DashboardWelcome } from '../components/DashboardWelcome';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { ScoreChip } from '../components/ui/ScoreChip';
import { StatCard } from '../components/ui/StatCard';
import { ComplianceRing } from '../components/ui/ComplianceRing';
import { ProgressBar } from '../components/ui/ProgressBar';
import { DataTable } from '../components/ui/DataTable';

const WELCOME_BANNER_KEY = 'aelira_welcome_dismissed';

interface PriorityIssue {
  file_name: string;
  // Backend PriorityIssue payload does not include a per-file issue count
  // (see docs/planning/DASHBOARD_STATS_AUDIT.md finding #10) — optional
  // until the backend adds one.
  issue_count?: number;
  scan_type: string;
  compliance_score: number;
  severity: string;
  scan_id: string;
}

interface TrendDataPoint {
  date: string;
  score: number;
  scans: number;
}

interface RecentScan {
  id: string;
  filename: string;
  type: string;
  uploaded_at: string;
  compliance_score: number | null;
  issues_count: number | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const formatRelativeDate = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffHours / 24);
  if (diffHours < 1) return 'Just now';
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const complianceBandLabel = (score: number): string => {
  if (score >= 90) return 'High scan score';
  if (score >= 70) return 'Needs improvement';
  return 'Needs attention';
};

// ---------------------------------------------------------------------------
// Trend tooltip
// ---------------------------------------------------------------------------

const TrendTooltip = ({
  active,
  payload,
}: TooltipContentProps<ValueType, NameType>): React.ReactElement | null => {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload as TrendDataPoint;
  return (
    <div
      className="p-3 rounded-[11px] text-sm border border-[var(--border-primary)] bg-[var(--surface-primary)]"
      style={{ boxShadow: 'none' }}
    >
      <p className="font-semibold text-[var(--content-primary)]">{formatDate(point.date)}</p>
      <p className="font-mono font-bold text-[var(--content-primary)]">
        {Math.round(point.score)}/100
      </p>
      <p className="text-xs text-[var(--content-tertiary)]">
        {point.scans} scan{point.scans !== 1 ? 's' : ''}
      </p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Dashboard(): React.ReactElement {
  const [stats, setStats] = useState<CurrentDashboardStats | null>(null);
  const [priorityIssues, setPriorityIssues] = useState<PriorityIssue[]>([]);
  const [recentScans, setRecentScans] = useState<RecentScan[]>([]);
  const [trendData, setTrendData] = useState<TrendDataPoint[]>([]);
  const [trendLoading, setTrendLoading] = useState<boolean>(true);
  const [reviewSummary, setReviewSummary] = useState<DepartmentReviewSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showWelcomeBanner, setShowWelcomeBanner] = useState<boolean>(
    () => localStorage.getItem(WELCOME_BANNER_KEY) === null
  );
  const [downloadingReport, setDownloadingReport] = useState<string | null>(null);
  const navigate = useNavigate();
  const { department, user, authMethod } = useAuth();
  const toast = useToast();
  const { hasFeature } = useFeatureAccess();

  const dismissWelcomeBanner = (): void => {
    localStorage.setItem(WELCOME_BANNER_KEY, 'true');
    setShowWelcomeBanner(false);
  };

  const departmentId = department?.id || 'default-dept-001';

  useEffect(() => {
    const fetchDashboardData = async (): Promise<void> => {
      try {
        setLoading(true);

        const statsData = await scansApi.getGeneralStats();

        const statsResult = unwrapResponse<RawCurrentComplianceStats>(statsData, 'stats');
        setStats(normalizeCurrentComplianceStats(statsResult));

        try {
          const issuesData = await scansApi.getPriorityIssues(departmentId, 5);
          setPriorityIssues(unwrapResponse<PriorityIssue[]>(issuesData, 'issues'));
        } catch (issueErr) {
          console.warn('Priority issues not available:', issueErr);
        }

        try {
          const scansResponse = await scansApi.listScans({ limit: 5 });
          const scansList = scansResponse.scans || scansResponse || [];
          const transformed: RecentScan[] = scansList.map((s: {
            scan_id?: string; id?: string; file_name?: string;
            scan_type?: string; created_at?: string;
            compliance_score?: number | null; total_issues?: number | null;
          }) => ({
            id: s.scan_id || s.id || '',
            filename: s.file_name || 'Unknown',
            type: s.scan_type?.toLowerCase() || 'unknown',
            uploaded_at: s.created_at || '',
            compliance_score: s.compliance_score ?? null,
            issues_count: s.total_issues ?? null,
          }));
          setRecentScans(transformed);
        } catch (scansErr) {
          console.warn('Recent scans not available:', scansErr);
        }

        try {
          setTrendLoading(true);
          const trendResponse = await scansApi.getComplianceTrend(departmentId, 30);
          interface RawTrendPoint { date: string; avg_compliance_score?: number; scan_count?: number }
          const trendArray = unwrapResponse<RawTrendPoint[]>(trendResponse, 'trend');
          const chartData = trendArray.map((point) => ({
            date: point.date,
            score: point.avg_compliance_score || 0,
            scans: point.scan_count || 0,
          }));
          setTrendData(chartData);
        } catch (trendErr) {
          console.warn('Trend data not available:', trendErr);
        } finally {
          setTrendLoading(false);
        }

        try {
          const summaryData = await scansApi.getDepartmentReviewSummary();
          setReviewSummary(summaryData);
        } catch (summaryErr) {
          console.warn('Review summary not available:', summaryErr);
        }

      } catch (err: unknown) {
        console.error('Failed to fetch dashboard data:', err);
        const fetchError = err as Error;
        setError(fetchError.message || 'Failed to load dashboard data');
      } finally {
        setLoading(false);
      }
    };

    fetchDashboardData();
  }, [departmentId]);

  // ---------------------------------------------------------------------------
  // Loading skeleton
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="p-4 sm:p-7" role="status" aria-label="Loading dashboard">
        <div className="max-w-[1240px] mx-auto animate-pulse">
          <div className="h-8 w-56 rounded-[8px] mb-6 bg-[var(--surface-tertiary)]" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="rounded-[16px] p-6 border border-[var(--border-primary)] bg-[var(--surface-primary)]">
                <div className="h-3 w-20 rounded mb-3 bg-[var(--surface-tertiary)]" />
                <div className="h-8 w-16 rounded bg-[var(--surface-tertiary)]" />
              </div>
            ))}
          </div>
          <div className="rounded-[16px] p-6 mb-4 border border-[var(--border-primary)] bg-[var(--surface-primary)]">
            <div className="h-5 w-36 rounded mb-4 bg-[var(--surface-tertiary)]" />
            <div className="h-48 rounded bg-[var(--surface-tertiary)]" />
          </div>
          <div className="rounded-[16px] p-6 border border-[var(--border-primary)] bg-[var(--surface-primary)]">
            <div className="h-5 w-32 rounded mb-4 bg-[var(--surface-tertiary)]" />
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-12 rounded mb-3 bg-[var(--surface-tertiary)]" />
            ))}
          </div>
        </div>
        <span className="sr-only">Loading dashboard data...</span>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Error state
  // ---------------------------------------------------------------------------

  if (error) {
    return (
      <div className="p-4 sm:p-7">
        <div className="max-w-[1240px] mx-auto">
          <div
            className="rounded-[11px] p-4 border bg-[var(--surface-error-subtle)] border-[var(--content-error)] text-[var(--content-error)]"
            role="alert"
          >
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Computed values
  // ---------------------------------------------------------------------------

  const avg = stats?.avgCompliance ?? null;
  const datedDeadline = hasDatedDeadline(stats?.deadline) ? stats.deadline : null;
  const daysLeft = datedDeadline?.days_remaining ?? '—';
  const deadlineSublabel = datedDeadline?.deadline_label ?? 'No dated deadline configured';
  const configurationRequired = stats?.deadline?.applicability === 'configuration_required';
  const canConfigureRegulatoryProfile = configurationRequired
    && authMethod !== 'lti'
    && (user?.role === 'admin' || user?.role === 'super_admin');

  // Greeting time-of-day prefix
  const hour = new Date().getHours();
  const timePrefix = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const firstName = user?.name?.split(' ')[0];
  const greetingName = firstName ? `, ${firstName}` : '';
  const deptName = department?.name || 'your department';

  // Trend delta
  const trendDelta =
    trendData.length >= 2
      ? Math.round((trendData[trendData.length - 1].score - trendData[0].score))
      : null;

  const handleDownloadReport = async (scanId: string, filename: string): Promise<void> => {
    setDownloadingReport(scanId);
    try {
      trackEvent('dash-download-report', {});
      const blob = await scansApi.downloadReport(scanId);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `report-${filename.replace(/[^a-z0-9]/gi, '-').slice(0, 30)}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Report downloaded', 'Download Complete');
    } catch {
      toast.error('Failed to download report', 'Download Failed');
    } finally {
      setDownloadingReport(null);
    }
  };

  // ---------------------------------------------------------------------------
  // Severity dot color token per issue severity
  // ---------------------------------------------------------------------------

  const severityDotClass = (severity: string): string => {
    if (severity === 'critical' || severity === 'high') return 'bg-[var(--content-error)]';
    if (severity === 'medium') return 'bg-[var(--content-warning)]';
    return 'bg-[var(--content-tertiary)]';
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="p-4 sm:p-7">
      <div className="max-w-[1240px] mx-auto">

        {/* ------------------------------------------------------------------ */}
        {/* Greeting row                                                        */}
        {/* ------------------------------------------------------------------ */}
        <div className="flex items-start justify-between flex-wrap gap-3 mb-6">
          <div className="flex-1 min-w-0">
            <h1
              className="font-bold text-[var(--content-primary)] mb-1"
              style={{ fontSize: '30px', letterSpacing: '-0.025em' }}
            >
              {timePrefix}{greetingName}
            </h1>
            <p className="text-sm text-[var(--content-secondary)]">
              Here's the current accessibility picture for {deptName}.
            </p>
          </div>
          <Button variant="secondary" size="md" onClick={() => navigate('/review')}>
            Open review queue
          </Button>
        </div>

        {configurationRequired && (
          <section className="card mb-8 border border-[var(--feature-warning-border)]" aria-labelledby="regulatory-configuration-title">
            <h2 id="regulatory-configuration-title" className="font-semibold text-primary">Institution setup is incomplete</h2>
            <p className="mt-1 text-sm text-secondary">
              {canConfigureRegulatoryProfile ? 'Finish your institution setup in Settings.' : 'Contact an institution administrator to finish the regulatory profile.'}
            </p>
            {canConfigureRegulatoryProfile && <button type="button" className="btn-secondary mt-3" onClick={() => navigate('/settings#regulatory-profile')}>Open Settings</button>}
          </section>
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Welcome banner (first-time users)                                  */}
        {/* ------------------------------------------------------------------ */}
        {showWelcomeBanner && (
          <DashboardWelcome
            name={user?.name ?? undefined}
            configurationRequired={configurationRequired}
            canConfigure={canConfigureRegulatoryProfile}
            hasIntegrations={hasFeature('showIntegrations')}
            onDismiss={dismissWelcomeBanner}
            onUpload={() => { trackEvent('dash-onboarding-step', { step: 'upload' }); navigate('/upload'); }}
            onConfigure={() => {
              trackEvent('dash-onboarding-step', { step: 'integrations' });
              navigate(configurationRequired && canConfigureRegulatoryProfile
                ? '/settings#regulatory-profile' : hasFeature('showIntegrations') ? '/integrations' : '/settings');
            }}
            onGuide={() => trackEvent('dash-onboarding-step', { step: 'guide' })}
          />
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Stats section                                                       */}
        {/* ------------------------------------------------------------------ */}
        {stats && (
          <>
            {stats.enrolledDocuments === 0 && stats.historicalScanCount === 0 ? (
              /* Empty stats — encourage first upload */
              <Card className="mb-8 text-center py-8">
                <ScanLine className="w-10 h-10 text-[var(--content-tertiary)] mx-auto mb-3" aria-hidden="true" />
                <p className="text-lg font-medium text-[var(--content-primary)] mb-1">
                  Ready to check your first document
                </p>
                <p className="text-sm text-[var(--content-tertiary)] mb-4">
                  Upload a PDF, Word doc, or PowerPoint to see your scan results here.
                </p>
                <Button
                  onClick={() => navigate('/upload')}
                  leftIcon={<Upload className="w-4 h-4" aria-hidden="true" />}
                >
                  Upload Your First File
                </Button>
              </Card>
            ) : (
              /* ---- 5-col stat cards ---- */
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 2xl:grid-cols-6 gap-[14px] mb-[14px]">
                {/* Total scans */}
                <StatCard
                  label="Total scans"
                  value={stats.historicalScanCount.toLocaleString()}
                  sublabel={`${stats.scansThisMonth} this month`}
                />

                {/* Avg scan score — custom interior with ComplianceRing */}
                <Card className="flex flex-col">
                  <span className="font-mono uppercase text-xs tracking-wider text-[var(--content-tertiary)] mb-2">
                    Avg scan score
                  </span>
                  <div className="flex items-center gap-3">
                    <ComplianceRing
                      score={avg ?? 0}
                      size={46}
                      strokeWidth={5}
                    />
                    <div>
                      <div className="text-3xl font-bold leading-tight text-[var(--content-primary)]">
                        {avg == null ? 'Unverified' : Math.round(avg)}
                        {avg != null && (
                          <span className="text-base font-semibold text-[var(--content-tertiary)]">/100</span>
                        )}
                      </div>
                      {avg != null && (
                        <div className="text-xs font-semibold mt-1 text-[var(--content-secondary)]">
                          {complianceBandLabel(avg)}
                        </div>
                      )}
                    </div>
                  </div>
                </Card>

                {/* Files processed */}
                <StatCard
                  label="Current documents"
                  value={stats.enrolledDocuments.toLocaleString()}
                  sublabel={`${stats.verifiedDocuments} verified · ${stats.unverifiedDocuments} unverified`}
                />

                {/* Issues found */}
                <StatCard
                  label="Issues found"
                  value={stats.issuesFound.toLocaleString()}
                  sublabel="In current verified results"
                />

                <StatCard
                  label="CVD Accessibility"
                  value={stats.cvdAccessibilityRate == null ? 'Unverified' : `${Math.round(stats.cvdAccessibilityRate)}%`}
                  sublabel={stats.cvdFilesAnalyzed === 0 ? 'No CVD-analyzed documents' : `${stats.cvdAffectedFiles} affected · ${stats.cvdIssuesTotal} issues`}
                />

                {/* Days to deadline — accent variant */}
                <StatCard
                  variant="accent"
                  label="Days to target date"
                  value={daysLeft}
                  sublabel={deadlineSublabel}
                />
              </div>
            )}

            <div className="mb-8">
              <EvidenceReportAction departmentId={departmentId} />
            </div>

            {/* -------------------------------------------------------------- */}
            {/* Scan score trend + Review status (side-by-side)                 */}
            {/* -------------------------------------------------------------- */}
            <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-[14px] mb-[14px]">

              {/* ---- Scan score trend chart ---- */}
              <Card className="p-5">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <h2 className="text-base font-bold text-[var(--content-primary)] mb-0.5">
                      Scan score trend
                    </h2>
                    <p className="text-xs text-[var(--content-tertiary)]">30-day department average</p>
                  </div>
                  {trendDelta !== null && (
                    <div className="text-right">
                      <div className="font-bold text-[var(--content-primary)] leading-tight"
                        style={{ fontSize: '22px', letterSpacing: '-0.02em' }}>
                        {trendDelta >= 0 ? '+' : ''}{trendDelta}
                        <span className="text-sm text-[var(--content-tertiary)] font-normal">pts</span>
                      </div>
                      <div className="text-xs font-semibold text-[var(--content-secondary)]">across this period</div>
                    </div>
                  )}
                </div>

                {trendLoading ? (
                  <div className="h-[200px] flex items-center justify-center text-sm text-[var(--content-tertiary)]">
                    Loading trend data…
                  </div>
                ) : trendData.length === 0 ? (
                  <div className="h-[200px] flex items-center justify-center text-sm text-[var(--content-tertiary)]">
                    Not enough data yet. Upload more scans to see progress.
                  </div>
                ) : (
                  <div
                    role="img"
                    aria-label={`30-day scan score trend chart. ${trendData.length} data points.`}
                  >
                    <ResponsiveContainer width="100%" height={200}>
                      <AreaChart
                        data={trendData}
                        margin={{ top: 4, right: 4, left: -20, bottom: 0 }}
                      >
                        <defs>
                          <linearGradient id="trendAreaFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.10} />
                            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid
                          horizontal
                          vertical={false}
                          stroke="var(--border-primary)"
                          strokeWidth={1}
                          strokeDasharray=""
                          horizontalValues={[40, 70, 90]}
                        />
                        <XAxis
                          dataKey="date"
                          tickFormatter={formatDate}
                          tick={{
                            fontSize: '10.5px',
                            fontFamily: 'JetBrains Mono, monospace',
                            fill: 'var(--content-tertiary)',
                          }}
                          axisLine={false}
                          tickLine={false}
                          interval="preserveStartEnd"
                        />
                        <YAxis
                          domain={[0, 100]}
                          tick={{
                            fontSize: '10.5px',
                            fontFamily: 'JetBrains Mono, monospace',
                            fill: 'var(--content-tertiary)',
                          }}
                          axisLine={false}
                          tickLine={false}
                          width={30}
                          ticks={[40, 70, 90]}
                        />
                        <Tooltip content={TrendTooltip} cursor={{ stroke: 'var(--border-secondary)', strokeWidth: 1 }} />
                        <Area
                          type="monotone"
                          dataKey="score"
                          isAnimationActive={false}
                          stroke="var(--accent)"
                          strokeWidth={2.5}
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          fill="url(#trendAreaFill)"
                          dot={false}
                          activeDot={{ r: 4, fill: 'var(--accent)', strokeWidth: 0 }}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                    <div className="sr-only">
                      <ul>
                        {trendData.map((point) => (
                          <li key={point.date}>
                            {formatDate(point.date)}: Score {Math.round(point.score)}, {point.scans} scans
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
              </Card>

              {/* ---- Review status ---- */}
              {reviewSummary ? (
                <Card className="p-5">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-base font-bold text-[var(--content-primary)]">Review status</h2>
                    <button
                      onClick={() => {
                        trackEvent('dash-go-to-review', {});
                        navigate('/review');
                      }}
                      className="text-xs font-semibold text-[var(--content-accent)] hover:underline"
                    >
                      Queue →
                    </button>
                  </div>

                  {/* Progress bar */}
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="font-medium text-[var(--content-secondary)]">Fixes reviewed</span>
                    <span className="font-bold text-[var(--content-primary)]">{reviewSummary.reviewed_percent}%</span>
                  </div>
                  <ProgressBar
                    value={reviewSummary.reviewed_percent}
                    max={100}
                    tone="success"
                    aria-label={`Fixes reviewed: ${reviewSummary.reviewed_percent}%`}
                    className="mb-4 h-2"
                  />

                  {/* 2×2 counters */}
                  <div className="grid grid-cols-2 gap-[10px]">
                    <div className="border border-[var(--border-primary)] rounded-[11px] p-[13px]">
                      <div className="font-bold leading-tight text-[var(--content-primary)]"
                        style={{ fontSize: '22px', letterSpacing: '-0.02em' }}>
                        {reviewSummary.total_documents}
                      </div>
                      <div className="text-xs text-[var(--content-tertiary)] mt-0.5">Documents</div>
                    </div>
                    <div className="border border-[var(--border-primary)] rounded-[11px] p-[13px] bg-[var(--surface-success-subtle)]">
                      <div className="font-bold leading-tight text-[var(--content-success)]"
                        style={{ fontSize: '22px', letterSpacing: '-0.02em' }}>
                        {reviewSummary.approved_count}
                      </div>
                      <div className="text-xs text-[var(--content-tertiary)] mt-0.5">Approved</div>
                    </div>
                    <div className="border border-[var(--border-primary)] rounded-[11px] p-[13px] bg-[var(--surface-warning-subtle)]">
                      <div className="font-bold leading-tight text-[var(--content-warning)]"
                        style={{ fontSize: '22px', letterSpacing: '-0.02em' }}>
                        {reviewSummary.pending_count}
                      </div>
                      <div className="text-xs text-[var(--content-tertiary)] mt-0.5">Pending</div>
                    </div>
                    <div className="border border-[var(--border-primary)] rounded-[11px] p-[13px] bg-[var(--surface-error-subtle)]">
                      <div className="font-bold leading-tight text-[var(--content-error)]"
                        style={{ fontSize: '22px', letterSpacing: '-0.02em' }}>
                        {reviewSummary.rejected_count}
                      </div>
                      <div className="text-xs text-[var(--content-tertiary)] mt-0.5">Rejected</div>
                    </div>
                  </div>

                  {/* Average reported confidence */}
                  <div className="mt-4 pt-3.5 border-t border-[var(--border-primary)] text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="text-[var(--content-secondary)]">Average reported confidence</span>
                      <ConfidenceBadge confidence={reviewSummary.avg_confidence} size="sm" />
                    </div>
                    <p className="mt-1 text-[var(--content-tertiary)]">Fixes without reported confidence are excluded.</p>
                  </div>
                </Card>
              ) : (
                /* Placeholder when review data unavailable */
                <Card className="p-5 flex items-center justify-center">
                  <p className="text-sm text-[var(--content-tertiary)]">Review data unavailable</p>
                </Card>
              )}
            </div>

            {/* -------------------------------------------------------------- */}
            {/* Analytics Dashboard (Phase 4 component)                         */}
            {/* -------------------------------------------------------------- */}
            <div className="mb-[14px]">
              <AnalyticsDashboard departmentId={departmentId} />
            </div>

            {/* -------------------------------------------------------------- */}
            {/* Priority issues + Recent scans (side-by-side)                   */}
            {/* -------------------------------------------------------------- */}
            <div className={`grid grid-cols-1 ${priorityIssues.length > 0 ? 'lg:grid-cols-[1fr_1.4fr]' : ''} gap-[14px]`}>

              {/* ---- Priority issues ---- */}
              {priorityIssues.length > 0 && (
                <Card className="p-5">
                  <div className="flex items-center justify-between mb-3.5">
                    <h2 className="text-base font-bold text-[var(--content-primary)]">Priority issues</h2>
                    <button
                      onClick={() => navigate('/history')}
                      className="text-xs font-semibold text-[var(--content-accent)] hover:underline"
                    >
                      All →
                    </button>
                  </div>

                  <div className="flex flex-col gap-2">
                    {priorityIssues.map((issue, index) => (
                      <div
                        key={index}
                        className="flex items-center gap-3 p-[11px] rounded-[10px] border border-[var(--border-primary)] hover:bg-[var(--surface-secondary)] transition-colors cursor-default"
                        onClick={() => navigate(`/scan/${issue.scan_id}`)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => e.key === 'Enter' && navigate(`/scan/${issue.scan_id}`)}
                        aria-label={
                          typeof issue.issue_count === 'number'
                            ? `View ${issue.file_name} — ${issue.issue_count} issues`
                            : `View ${issue.file_name}`
                        }
                      >
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${severityDotClass(issue.severity)}`}
                          aria-hidden="true"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-semibold text-[var(--content-primary)] truncate">
                            {issue.file_name}
                          </div>
                          <div className="text-xs text-[var(--content-tertiary)]">
                            {typeof issue.issue_count === 'number' && `${issue.issue_count} issues · `}
                            {issue.scan_type.toUpperCase()}
                          </div>
                        </div>
                        <ScoreChip score={issue.compliance_score} />
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {/* ---- Recent scans ---- */}
              <Card className="p-5">
                <div className="flex items-center justify-between mb-3.5">
                  <h2 className="text-base font-bold text-[var(--content-primary)]">Recent scans</h2>
                  <button
                    onClick={() => navigate('/history')}
                    className="text-xs font-semibold text-[var(--content-accent)] hover:underline"
                  >
                    History →
                  </button>
                </div>

                {recentScans.length === 0 && stats.historicalScanCount === 0 ? (
                  <div className="text-center py-10">
                    <FileText className="w-10 h-10 text-[var(--content-tertiary)] mx-auto mb-3" aria-hidden="true" />
                    <p className="text-sm text-[var(--content-tertiary)] mb-4">
                      No scans yet. Upload your first document to get started!
                    </p>
                    <Button
                      onClick={() => navigate('/upload')}
                      leftIcon={<Upload className="w-4 h-4" aria-hidden="true" />}
                    >
                      Upload File
                    </Button>
                  </div>
                ) : recentScans.length > 0 ? (
                  <DataTable<RecentScan>
                    className="-mx-1"
                    caption="Recent scans"
                    rows={recentScans}
                    getRowKey={(scan) => scan.id}
                    columns={[
                      {
                        key: 'file',
                        header: 'File',
                        render: (scan) => (
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-[var(--content-primary)] truncate">
                              {scan.filename}
                            </div>
                            <div className="text-xs text-[var(--content-tertiary)] font-mono uppercase">
                              {scan.type} · {scan.issues_count == null ? 'Issues unavailable' : `${scan.issues_count} issues`}
                            </div>
                          </div>
                        ),
                      },
                      {
                        key: 'when',
                        header: 'When',
                        render: (scan) => (
                          <span className="flex items-center gap-1 text-xs text-[var(--content-tertiary)]">
                            <Calendar className="w-3 h-3 shrink-0" aria-hidden="true" />
                            {formatRelativeDate(scan.uploaded_at)}
                          </span>
                        ),
                      },
                      {
                        key: 'score',
                        header: 'Score',
                        render: (scan) => scan.compliance_score == null ? <span>Unverified</span> : <ScoreChip score={scan.compliance_score} />,
                      },
                      {
                        key: 'actions',
                        header: 'Actions',
                        align: 'right',
                        render: (scan) => (
                          <span className="flex gap-2 justify-end">
                            <button
                              onClick={() => navigate(`/scan/${scan.id}`)}
                              className="p-2 min-h-9 min-w-9 rounded-[7px] text-[var(--content-tertiary)] hover:text-[var(--content-accent)] hover:bg-[var(--surface-secondary)] transition-colors"
                              aria-label={`View ${scan.filename}`}
                              title="View details"
                            >
                              <Eye className="w-4 h-4" aria-hidden="true" />
                            </button>
                            <button
                              onClick={() => navigate(`/remediate/${scan.id}`)}
                              className="p-2 min-h-9 min-w-9 rounded-[7px] text-[var(--content-tertiary)] hover:text-[var(--content-success)] hover:bg-[var(--surface-secondary)] transition-colors"
                              aria-label={`Remediate ${scan.filename}`}
                              title="Remediate"
                            >
                              <Wrench className="w-4 h-4" aria-hidden="true" />
                            </button>
                            <button
                              onClick={() => handleDownloadReport(scan.id, scan.filename)}
                              disabled={downloadingReport === scan.id}
                              className="p-2 min-h-9 min-w-9 rounded-[7px] text-[var(--content-tertiary)] hover:text-[var(--content-accent)] hover:bg-[var(--surface-secondary)] transition-colors disabled:opacity-50"
                              aria-label={`Download report for ${scan.filename}`}
                              title="Download report"
                            >
                              {downloadingReport === scan.id ? (
                                <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                              ) : (
                                <Download className="w-4 h-4" aria-hidden="true" />
                              )}
                            </button>
                          </span>
                        ),
                      },
                    ]}
                  />
                ) : (
                  <div className="text-center py-8">
                    <TrendingUp className="w-8 h-8 text-[var(--content-accent)] mx-auto mb-2" aria-hidden="true" />
                    <p className="text-sm text-[var(--content-secondary)]">
                      You have {stats.historicalScanCount} scan{stats.historicalScanCount !== 1 ? 's' : ''} in your history.
                    </p>
                    <Button
                      className="mt-4"
                      onClick={() => navigate('/history')}
                    >
                      View Scan History
                    </Button>
                  </div>
                )}
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
