import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, TrendingUp, AlertTriangle, Loader, Upload, BookOpen, Settings, X, Sparkles, CheckCircle, Eye, Download, Wrench, Calendar, ScanLine, Clock, ClipboardCheck } from 'lucide-react';
import { scansApi } from '../api/scans';
import type { DepartmentReviewSummary } from '../api/scans';
import { unwrapResponse } from '../utils/apiUnwrap';
import { TrendGraph } from '../components/TrendGraph';

import { AnalyticsDashboard } from '../components/AnalyticsDashboard';
import { EvidenceReportAction } from '../components/EvidenceReportAction';
import { useAuth } from '../context/auth-context';
import { useToast } from '../context/toast-context';
import { useFeatureAccess } from '../hooks/useFeatureAccess';
import { trackEvent } from '../utils/analytics';
import {
  normalizeCurrentComplianceStats,
  type CurrentDashboardStats,
  type RawCurrentComplianceStats,
} from '../utils/currentCompliance';
import {
  hasDatedDeadline,
} from '../types/deadline';

const WELCOME_BANNER_KEY = 'aelira_welcome_dismissed';

interface PriorityIssue {
  file_name: string;
  issue_count: number;
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

export function Dashboard(): React.ReactElement {
  const [stats, setStats] = useState<CurrentDashboardStats | null>(null);
  const [priorityIssues, setPriorityIssues] = useState<PriorityIssue[]>([]);
  const [recentScans, setRecentScans] = useState<RecentScan[]>([]);
  const [trendData, setTrendData] = useState<TrendDataPoint[]>([]);
  const [trendLoading, setTrendLoading] = useState<boolean>(true);
  const [reviewSummary, setReviewSummary] = useState<DepartmentReviewSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showWelcomeBanner, setShowWelcomeBanner] = useState<boolean>(false);
  const [downloadingReport, setDownloadingReport] = useState<string | null>(null);
  const navigate = useNavigate();
  const { authMethod, department, user } = useAuth();
  const toast = useToast();
  const { hasFeature } = useFeatureAccess();

  // Check if this is a first-time user (no scans yet and banner not dismissed)
  useEffect(() => {
    const bannerDismissed = localStorage.getItem(WELCOME_BANNER_KEY);
    if (!bannerDismissed) {
      setShowWelcomeBanner(true);
    }
  }, []);

  const dismissWelcomeBanner = (): void => {
    localStorage.setItem(WELCOME_BANNER_KEY, 'true');
    setShowWelcomeBanner(false);
  };

  // Use department ID from auth context, fallback to default for testing
  const departmentId = department?.id || 'default-dept-001';

  useEffect(() => {
    const fetchDashboardData = async (): Promise<void> => {
      try {
        setLoading(true);

        // Fetch general stats (works without department ID)
        const statsData = await scansApi.getGeneralStats();

        // API may wrap response in { success, stats: {...} }
        const statsResult = unwrapResponse<RawCurrentComplianceStats>(statsData, 'stats');
        setStats(normalizeCurrentComplianceStats(statsResult));

        // Try to fetch priority issues (may require department ID)
        try {
          const issuesData = await scansApi.getPriorityIssues(departmentId, 5);
          setPriorityIssues(unwrapResponse<PriorityIssue[]>(issuesData, 'issues'));
        } catch (issueErr) {
          console.warn('Priority issues not available:', issueErr);
          // Continue without priority issues
        }

        // Try to fetch recent scans
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

        // Try to fetch trend data (may require department ID)
        try {
          setTrendLoading(true);
          const trendResponse = await scansApi.getComplianceTrend(departmentId, 30);
          // Transform trend data to chart format — backend may wrap in { trend: [...] }
          interface RawTrendPoint { date: string; avg_compliance_score?: number; scan_count?: number }
          const trendArray = unwrapResponse<RawTrendPoint[]>(trendResponse, 'trend');
          const chartData = trendArray.map((point) => ({
            date: point.date,
            score: point.avg_compliance_score || 0,
            scans: point.scan_count || 0
          }));
          setTrendData(chartData);
        } catch (trendErr) {
          console.warn('Trend data not available:', trendErr);
          // Continue without trend data
        } finally {
          setTrendLoading(false);
        }

        // Try to fetch review summary
        try {
          const summaryData = await scansApi.getDepartmentReviewSummary();
          setReviewSummary(summaryData);
        } catch (summaryErr) {
          console.warn('Review summary not available:', summaryErr);
          // Continue without review summary
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

  if (loading) {
    return (
      <div className="p-8" role="status" aria-label="Loading dashboard">
        <div className="max-w-7xl mx-auto animate-pulse">
          {/* Title skeleton */}
          <div className="h-8 w-48 rounded mb-6" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
          {/* Stats cards skeleton */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="card p-6">
                <div className="h-4 w-20 rounded mb-3" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
                <div className="h-8 w-16 rounded" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
              </div>
            ))}
          </div>
          {/* Chart skeleton */}
          <div className="card p-6 mb-6">
            <div className="h-5 w-36 rounded mb-4" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
            <div className="h-48 rounded" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
          </div>
          {/* Table skeleton */}
          <div className="card p-6">
            <div className="h-5 w-32 rounded mb-4" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 rounded" style={{ backgroundColor: 'var(--surface-tertiary)' }} />
              ))}
            </div>
          </div>
        </div>
        <span className="sr-only">Loading dashboard data...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-7xl mx-auto">
          <div
            className="rounded-lg p-4"
            style={{
              backgroundColor: 'var(--surface-error-subtle)',
              borderColor: 'var(--content-error)',
              border: '1px solid',
              color: 'var(--content-error)'
            }}
            role="alert"
          >
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  const formatCompliance = (score: number | null | undefined): string | number => {
    if (score == null) return '--';
    return Math.round(score);
  };

  const getScoreColor = (score: number): string => {
    if (score >= 90) return 'text-[var(--feature-success-content)]';
    if (score >= 70) return 'text-[var(--feature-warning-content)]';
    return 'text-[var(--feature-danger-content)]';
  };

  const getScoreBgColor = (score: number): string => {
    if (score >= 90) return 'text-[var(--feature-success-content)] bg-[var(--feature-success-surface)]';
    if (score >= 70) return 'text-[var(--feature-warning-content)] bg-[var(--feature-warning-surface)]';
    return 'text-[var(--feature-danger-content)] bg-[var(--feature-danger-surface)]';
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

  const configurationRequired = stats?.deadline?.applicability === 'configuration_required';
  const canConfigureRegulatoryProfile = configurationRequired
    && authMethod !== 'lti'
    && (user?.role === 'admin' || user?.role === 'super_admin');

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-primary mb-6">Dashboard</h1>

        {configurationRequired && (
          <section className="card mb-8 border border-[var(--feature-warning-border)]" aria-labelledby="regulatory-configuration-title">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 mt-0.5 text-[var(--feature-warning-content)]" aria-hidden="true" />
              <div>
                <h2 id="regulatory-configuration-title" className="font-semibold text-primary">Regulatory deadline setup required</h2>
                <p className="text-sm text-secondary mt-1">Aelira needs your institution’s verified country and regulatory framework before it can show a legal deadline.</p>
                {canConfigureRegulatoryProfile ? (
                  <button type="button" className="btn-primary mt-4" onClick={() => navigate('/settings#regulatory-profile')}>Configure regulatory profile</button>
                ) : (
                  <p className="text-sm text-tertiary mt-3">Contact an institution administrator to complete this setup.</p>
                )}
              </div>
            </div>
          </section>
        )}

        {/* First-Time User Welcome Banner */}
        {showWelcomeBanner && (
          <div className="mb-8 rounded-xl overflow-hidden border border-[var(--border-accent)] bg-[var(--surface-secondary)]">
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div className="flex items-start space-x-4">
                  <div className="p-3 rounded-full bg-[var(--feature-info-content)]/10">
                    <Sparkles className="w-6 h-6 text-[var(--feature-info-content)]" />
                  </div>
                  <div>
                    <h2 className="text-xl font-bold text-primary mb-2">
                      Welcome to Aelira{user?.name ? `, ${user.name.split(' ')[0]}` : ''}!
                    </h2>
                    <p className="text-secondary mb-4">
                      {configurationRequired ? 'Finish your institution setup, then start making your documents accessible:' : "You're all set to start making your documents accessible. Here's how to get started:"}
                    </p>
                  </div>
                </div>
                <button
                  onClick={dismissWelcomeBanner}
                  className="p-2 hover:bg-[var(--surface-tertiary)] rounded-lg transition-colors"
                  aria-label="Dismiss welcome banner"
                >
                  <X className="w-5 h-5 text-tertiary" />
                </button>
              </div>

              {/* Getting Started Steps */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
                <button
                  onClick={() => {
                    trackEvent('dash-onboarding-step', { step: 'upload' });
                    navigate('/upload');
                  }}
                  className="flex items-center space-x-3 p-4 rounded-lg bg-[var(--surface-primary)] hover:bg-[var(--surface-secondary)] border border-[var(--border-primary)] transition-all hover:border-[var(--feature-info-border)] group"
                >
                  <div className="p-2 rounded-lg bg-[var(--feature-success-surface)] group-hover:bg-[var(--feature-success-content)]/20 transition-colors">
                    <Upload className="w-5 h-5 text-[var(--feature-success-content)]" />
                  </div>
                  <div className="text-left">
                    <div className="font-semibold text-primary">1. Upload a Document</div>
                    <div className="text-sm text-tertiary">PDF, PowerPoint, Word, or Excel</div>
                  </div>
                </button>

                <button
                  onClick={() => {
                    trackEvent('dash-onboarding-step', { step: 'integrations' });
                    navigate(configurationRequired && canConfigureRegulatoryProfile ? '/settings#regulatory-profile' : hasFeature('showIntegrations') ? '/integrations' : '/settings');
                  }}
                  className="flex items-center space-x-3 p-4 rounded-lg bg-[var(--surface-primary)] hover:bg-[var(--surface-secondary)] border border-[var(--border-primary)] transition-all hover:border-[var(--feature-info-border)] group"
                >
                  <div className="p-2 rounded-lg bg-[var(--feature-info-surface)] group-hover:bg-[var(--feature-info-content)]/20 transition-colors">
                    <Settings className="w-5 h-5 text-[var(--feature-info-content)]" />
                  </div>
                  <div className="text-left">
                    {configurationRequired && canConfigureRegulatoryProfile ? (
                      <>
                        <div className="font-semibold text-primary">2. Configure Deadline</div>
                        <div className="text-sm text-tertiary">Verify your institution’s regulatory profile</div>
                      </>
                    ) : hasFeature('showIntegrations') ? (
                      <>
                        <div className="font-semibold text-primary">2. Connect Your LMS</div>
                        <div className="text-sm text-tertiary">Canvas, Blackboard, Google, Microsoft</div>
                      </>
                    ) : (
                      <>
                        <div className="font-semibold text-primary">2. Configure Settings</div>
                        <div className="text-sm text-tertiary">Set up your account preferences</div>
                      </>
                    )}
                  </div>
                </button>

                <a
                  href="https://example.com/docs/getting-started"
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => trackEvent('dash-onboarding-step', { step: 'guide' })}
                  className="flex items-center space-x-3 p-4 rounded-lg bg-[var(--surface-primary)] hover:bg-[var(--surface-secondary)] border border-[var(--border-primary)] transition-all hover:border-[var(--feature-info-border)] group"
                >
                  <div className="p-2 rounded-lg bg-[var(--feature-warning-surface)] group-hover:bg-[var(--feature-warning-content)]/20 transition-colors">
                    <BookOpen className="w-5 h-5 text-[var(--feature-warning-content)]" />
                  </div>
                  <div className="text-left">
                    <div className="font-semibold text-primary">3. Read the Guide</div>
                    <div className="text-sm text-tertiary">Quick start documentation</div>
                  </div>
                </a>
              </div>

              {/* Feature highlights */}
              <div className="mt-4 pt-4 border-t border-[var(--border-primary)]">
                <div className="flex flex-wrap gap-3 text-sm">
                  <span className="inline-flex items-center gap-1.5 text-secondary">
                    <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" />
                    AI-powered alt text generation
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-secondary">
                    <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" />
                    PDF & PowerPoint remediation
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-secondary">
                    <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" />
                    LaTeX to MathML conversion
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-secondary">
                    <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" />
                    Accessibility evidence reports with recorded findings and limitations
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Stats Cards */}
        {stats && (
          <>
            {stats.enrolledDocuments === 0 && stats.historicalScanCount === 0 ? (
              /* Empty stats - show encouraging prompt instead of zeros */
              <div className="card mb-8 text-center py-8">
                <ScanLine className="w-10 h-10 text-tertiary mx-auto mb-3" aria-hidden="true" />
                <p className="text-lg font-medium text-primary mb-1">Ready to check your first document</p>
                <p className="text-sm text-tertiary mb-4">Upload a PDF, Word doc, or PowerPoint to see your compliance stats here.</p>
                <button
                  onClick={() => navigate('/upload')}
                  className="btn-primary inline-flex items-center gap-2"
                >
                  <Upload className="w-4 h-4" aria-hidden="true" />
                  Upload Your First File
                </button>
              </div>
            ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-6 mb-8">
              <div className="card">
                <div className="text-sm font-medium text-secondary mb-1">Current Documents</div>
                <div className="text-3xl font-bold text-primary">{stats.enrolledDocuments}</div>
                <div className="text-sm text-tertiary mt-1">
                  {stats.verifiedDocuments} verified · {stats.unverifiedDocuments} awaiting results
                </div>
              </div>

              <div className="card">
                <div className="text-sm font-medium text-secondary mb-1">Avg Compliance</div>
                <div className={`text-3xl font-bold ${stats.avgCompliance == null ? 'text-primary' : getScoreColor(stats.avgCompliance)}`}>
                  {formatCompliance(stats.avgCompliance)}
                  {stats.avgCompliance != null && <span className="text-lg">/100</span>}
                </div>
                <div className="text-sm text-tertiary mt-1">
                  {stats.avgCompliance == null ? 'No verified results' : stats.avgCompliance >= 90 ? 'Excellent' : stats.avgCompliance >= 70 ? 'Needs improvement' : 'Poor'}
                </div>
              </div>

              <div className="card">
                <div className="text-sm font-medium text-secondary mb-1">Scan Attempts</div>
                <div className="text-3xl font-bold text-primary">{stats.historicalScanCount}</div>
                <div className="text-sm text-tertiary mt-1">{stats.scansThisMonth} this month</div>
              </div>

              <div className="card">
                <div className="text-sm font-medium text-secondary mb-1">Issues Found</div>
                <div className="text-3xl font-bold text-primary">{stats.issuesFound}</div>
                <div className="text-sm text-tertiary mt-1">In current verified results</div>
              </div>

              <div className="card">
                <div className="text-sm font-medium text-secondary mb-1">CVD Accessibility</div>
                <div
                  className={`text-3xl font-bold ${stats.cvdAccessibilityRate == null ? 'text-primary' : getScoreColor(stats.cvdAccessibilityRate)}`}
                  aria-label={stats.cvdAccessibilityRate == null ? 'CVD accessibility not assessed' : `CVD accessibility rate ${Math.round(stats.cvdAccessibilityRate)} percent`}
                >
                  {stats.cvdAccessibilityRate == null ? '--' : `${Math.round(stats.cvdAccessibilityRate)}%`}
                </div>
                <div className="text-sm text-tertiary mt-1">
                  {stats.cvdFilesAnalyzed === 0
                    ? 'No CVD-analyzed documents'
                    : `${stats.cvdFilesAnalyzed} analyzed · ${stats.cvdAffectedFiles} affected · ${stats.cvdIssuesTotal} findings`}
                </div>
              </div>

              {hasDatedDeadline(stats.deadline) && (() => {
                const daysLeft = stats.deadline.days_remaining;
                const avg = stats.avgCompliance;
                const isCritical = avg != null && daysLeft < 90 && avg < 90;
                const isWarning = avg != null && daysLeft < 180 && avg < 80;
                const isAhead = avg != null && avg >= 90;
                return (
                  <div className="card">
                    <div className="text-sm font-medium text-secondary mb-1 flex items-center gap-1.5">
                      <Calendar className="w-3.5 h-3.5" aria-hidden="true" />
                      Days to Target Date
                    </div>
                    <div className={`text-3xl font-bold ${isCritical ? 'text-[var(--content-error)]' : isWarning ? 'text-[var(--content-warning)]' : 'text-primary'}`}>
                      {daysLeft}
                    </div>
                    <div className="text-sm mt-1">
                      {isCritical ? (
                        <span className="text-[var(--content-error)] flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" aria-hidden="true" />
                          Target at Risk
                        </span>
                      ) : isWarning ? (
                        <span className="text-[var(--content-warning)] flex items-center gap-1">
                          <Clock className="w-3 h-3" aria-hidden="true" />
                          Needs Attention
                        </span>
                      ) : isAhead ? (
                        <span className="text-[var(--content-success)] flex items-center gap-1">
                          <CheckCircle className="w-3 h-3" aria-hidden="true" />
                          Scan Score on Target
                        </span>
                      ) : (
                        <span className="text-tertiary">{stats.deadline.deadline_label}</span>
                      )}
                    </div>
                  </div>
                );
              })()}
            </div>
            )}

            {/* Compliance Trend Graph (30 Days) */}
            <div className="mb-8">
              <TrendGraph data={trendData} loading={trendLoading} />
            </div>

            {/* Analytics Dashboard */}
            <div className="mb-8">
              <AnalyticsDashboard departmentId={departmentId} />
            </div>

            {/* Accessibility Evidence Report */}
            <div className="mb-8">
              <EvidenceReportAction departmentId={departmentId} />
            </div>

            {/* Review Summary Card */}
            {reviewSummary && (reviewSummary.total_documents > 0 || reviewSummary.pending_count > 0) && (
              <div className="card mb-8">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <ClipboardCheck className="w-5 h-5 text-accent" aria-hidden="true" />
                    <h2 className="text-xl font-semibold text-primary">Review Status</h2>
                  </div>
                  <button
                    onClick={() => {
                      trackEvent('dash-go-to-review', {});
                      navigate('/review');
                    }}
                    className="text-sm text-accent hover:underline"
                  >
                    Go to Review Queue
                  </button>
                </div>

                {/* Progress bar */}
                <div className="mb-4">
                  <div className="flex items-center justify-between text-sm mb-1.5">
                    <span className="text-secondary font-medium">
                      Reviewed
                    </span>
                    <span className="text-primary font-semibold">
                      {reviewSummary.reviewed_percent}%
                    </span>
                  </div>
                  <div
                    className="w-full h-2.5 rounded-full overflow-hidden"
                    style={{ backgroundColor: 'var(--surface-tertiary)' }}
                    role="progressbar"
                    aria-valuenow={reviewSummary.reviewed_percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${reviewSummary.reviewed_percent}% of fixes reviewed`}
                  >
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.min(reviewSummary.reviewed_percent, 100)}%`,
                        backgroundColor: reviewSummary.reviewed_percent >= 90
                          ? 'var(--feature-success-content)'
                          : reviewSummary.reviewed_percent >= 50
                            ? 'var(--feature-warning-content)'
                            : 'var(--accent-solid)',
                      }}
                    />
                  </div>
                </div>

                {/* Stat counters */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div className="text-center p-3 rounded-lg" style={{ backgroundColor: 'var(--surface-secondary)' }}>
                    <div className="text-2xl font-bold text-primary">{reviewSummary.total_documents}</div>
                    <div className="text-xs text-tertiary mt-0.5">Documents</div>
                  </div>
                  <div className="text-center p-3 rounded-lg" style={{ backgroundColor: 'var(--feature-success-surface)' }}>
                    <div className="text-2xl font-bold text-[var(--feature-success-content)]">{reviewSummary.approved_count}</div>
                    <div className="text-xs text-tertiary mt-0.5">Approved</div>
                  </div>
                  <div className="text-center p-3 rounded-lg" style={{ backgroundColor: 'var(--feature-warning-surface)' }}>
                    <div className="text-2xl font-bold text-[var(--feature-warning-content)]">{reviewSummary.pending_count}</div>
                    <div className="text-xs text-tertiary mt-0.5">Pending</div>
                  </div>
                  <div className="text-center p-3 rounded-lg" style={{ backgroundColor: 'var(--feature-danger-surface)' }}>
                    <div className="text-2xl font-bold text-[var(--feature-danger-content)]">{reviewSummary.rejected_count}</div>
                    <div className="text-xs text-tertiary mt-0.5">Rejected</div>
                  </div>
                </div>

                {/* Average confidence */}
                {reviewSummary.avg_confidence > 0 && (
                  <div className="mt-3 pt-3 border-t border-[var(--border-primary)]">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-secondary">Average AI Confidence</span>
                      <span className={`font-semibold ${
                        reviewSummary.avg_confidence >= 0.9
                          ? 'text-[var(--feature-success-content)]'
                          : reviewSummary.avg_confidence >= 0.7
                            ? 'text-[var(--feature-warning-content)]'
                            : 'text-[var(--feature-danger-content)]'
                      }`}>
                        {Math.round(reviewSummary.avg_confidence * 100)}%
                      </span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Priority Issues Section */}
            {priorityIssues.length > 0 && (
              <div className="card mb-8">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-xl font-semibold text-primary">Priority Issues</h2>
                  <button
                    onClick={() => navigate('/history')}
                    className="text-sm text-accent hover:underline"
                  >
                    View All
                  </button>
                </div>

                <div className="space-y-3">
                  {priorityIssues.map((issue, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between p-4 rounded-lg bg-surface-tertiary border border-primary"
                    >
                      <div className="flex items-center space-x-4">
                        <div>
                          <AlertTriangle
                            className={`w-5 h-5 ${
                              issue.severity === 'critical' ? 'text-[var(--feature-danger-content)]' :
                              issue.severity === 'high' ? 'text-[var(--feature-warning-content)]' :
                              issue.severity === 'medium' ? 'text-[var(--feature-info-content)]' :
                              'text-tertiary'
                            }`}
                            aria-hidden="true"
                          />
                        </div>
                        <div>
                          <div className="font-medium text-primary">{issue.file_name}</div>
                          <div className="text-sm text-secondary">
                            {issue.issue_count} issues · {issue.scan_type}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center space-x-4">
                        <div className="text-right">
                          <div className={`text-sm font-semibold ${getScoreColor(issue.compliance_score)}`}>
                            {Math.round(issue.compliance_score)}/100
                          </div>
                          <div className="text-xs text-tertiary">
                            {issue.severity} priority
                          </div>
                        </div>
                        <button
                          onClick={() => navigate(`/scan/${issue.scan_id}`)}
                          className="text-accent hover:underline text-sm"
                        >
                          View
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent Scans Section */}
            <div className="card">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-semibold text-primary">Recent Scans</h2>
                <button
                  onClick={() => navigate('/history')}
                  className="text-sm text-accent hover:underline"
                >
                  View All
                </button>
              </div>

              {recentScans.length === 0 && stats.historicalScanCount === 0 ? (
                <div className="text-center py-12">
                  <FileText className="w-12 h-12 text-tertiary mx-auto mb-4" aria-hidden="true" />
                  <p className="text-tertiary mb-4">No scans yet. Upload your first document to get started!</p>
                  <button
                    onClick={() => navigate('/upload')}
                    className="btn-primary"
                  >
                    Upload File
                  </button>
                </div>
              ) : recentScans.length > 0 ? (
                <div className="space-y-3">
                  {recentScans.map((scan) => (
                    <div
                      key={scan.id}
                      className="flex items-center justify-between p-4 rounded-lg border border-[var(--border-primary)] hover:bg-[var(--surface-secondary)] transition-colors"
                    >
                      <div className="flex items-center space-x-4 min-w-0">
                        <FileText className="w-5 h-5 text-tertiary shrink-0" aria-hidden="true" />
                        <div className="min-w-0">
                          <div className="font-medium text-primary truncate">{scan.filename}</div>
                          <div className="flex items-center gap-3 text-xs text-tertiary mt-0.5">
                            <span className="uppercase font-medium">{scan.type}</span>
                            <span className="flex items-center gap-1">
                              <Calendar className="w-3 h-3" />
                              {formatRelativeDate(scan.uploaded_at)}
                            </span>
                            <span>
                              {scan.issues_count == null ? 'Issues unavailable' : `${scan.issues_count} issues`}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0 ml-4">
                        <span className={`px-2.5 py-1 text-xs font-semibold rounded-full ${scan.compliance_score == null ? 'text-tertiary bg-[var(--surface-tertiary)]' : getScoreBgColor(scan.compliance_score)}`}>
                          {scan.compliance_score == null ? 'Unverified' : `${Math.round(scan.compliance_score)}/100`}
                        </span>
                        <button
                          onClick={() => navigate(`/scan/${scan.id}`)}
                          className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                          aria-label={`View ${scan.filename}`}
                          title="View details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => navigate(`/remediate/${scan.id}`)}
                          className="p-2 text-tertiary hover:text-[var(--feature-success-content)] transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                          aria-label={`Remediate ${scan.filename}`}
                          title="Remediate"
                        >
                          <Wrench className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDownloadReport(scan.id, scan.filename)}
                          disabled={downloadingReport === scan.id}
                          className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)] disabled:opacity-50"
                          aria-label={`Download report for ${scan.filename}`}
                          title="Download report"
                        >
                          {downloadingReport === scan.id ? (
                            <Loader className="w-4 h-4 animate-spin" />
                          ) : (
                            <Download className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8">
                  <TrendingUp className="w-8 h-8 text-accent mx-auto mb-2" aria-hidden="true" />
                  <p className="text-secondary">
                    You have {stats.historicalScanCount} scan{stats.historicalScanCount !== 1 ? 's' : ''} in your history.
                  </p>
                  <button
                    onClick={() => navigate('/history')}
                    className="mt-4 btn-primary"
                  >
                    View Scan History
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
