import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  FileText,
  Download,
  Play,
  RotateCcw,
  Loader,
  Code,
  LucideIcon,
} from 'lucide-react';
import { scansApi } from '../api/scans';
import type { RemediationResult } from '../api/scans';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';
import { trackEvent } from '../utils/analytics';
import { useToast } from '../context/toast-context';

type IssueStatusType = 'pending' | 'in_progress' | 'fixed' | 'manual' | 'failed';
type ProgressStatus = 'idle' | 'running' | 'paused' | 'completed';

interface StatusConfigItem {
  icon: LucideIcon;
  color: string;
  bg: string;
  label: string;
  animate?: boolean;
}

interface Issue {
  description?: string;
  message?: string;
  title?: string;
  category?: string;
  severity?: string;
  rule?: string;
}

interface Scan {
  file_name?: string;
  issues?: Issue[];
  compliance_score?: number;
}

interface Progress {
  current: number;
  total: number;
  status: ProgressStatus;
}

interface ProgressBarProps {
  current: number;
  total: number;
  label: string;
}

interface IssueRowProps {
  issue: Issue;
  status: IssueStatusType;
  fixDetails: Record<string, unknown> | null;
}

interface BeforeAfterComparisonProps {
  original: { score: number; issues: number };
  remediated: { score: number; remaining_issues: number };
}

const STATUS_CONFIG: Record<IssueStatusType, StatusConfigItem> = {
  pending: {
    icon: Clock,
    color: 'text-[var(--content-tertiary)]',
    bg: 'bg-[var(--surface-tertiary)]',
    label: 'Pending',
  },
  in_progress: {
    icon: Loader,
    color: 'text-[var(--feature-info-content)]',
    bg: 'bg-[var(--feature-info-surface)]',
    label: 'In Progress',
    animate: true,
  },
  fixed: {
    icon: CheckCircle,
    color: 'text-[var(--feature-success-content)]',
    bg: 'bg-[var(--feature-success-surface)]',
    label: 'Fixed',
  },
  manual: {
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    bg: 'bg-[var(--feature-warning-surface)]',
    label: 'Manual Review',
  },
  failed: {
    icon: XCircle,
    color: 'text-[var(--feature-danger-content)]',
    bg: 'bg-[var(--feature-danger-surface)]',
    label: 'Failed',
  },
};

function ProgressBar({ current, total, label }: ProgressBarProps): React.ReactElement {
  const percentage = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="text-secondary">{label}</span>
        <span className="text-primary font-medium">{percentage}%</span>
      </div>
      <div className="h-3 bg-[var(--surface-tertiary)] rounded-full overflow-hidden">
        <div
          className="h-full bg-[var(--accent-solid)] transition-all duration-500 ease-out rounded-full"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <p className="text-xs text-tertiary">
        {current} of {total} issues processed
      </p>
    </div>
  );
}

function IssueRow({ issue, status, fixDetails }: IssueRowProps): React.ReactElement {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const StatusIcon = config.icon;

  return (
    <div className="flex items-center justify-between p-3 border-b border-[var(--border-primary)] last:border-b-0">
      <div className="flex items-center gap-3">
        <div className={`p-1.5 rounded ${config.bg}`}>
          <StatusIcon
            className={`w-4 h-4 ${config.color} ${config.animate ? 'animate-spin' : ''}`}
          />
        </div>
        <div>
          <p className="text-sm font-medium text-primary">{issue.description || issue.message || issue.title || 'Accessibility issue'}</p>
          <p className="text-xs text-tertiary">{issue.category || issue.severity || issue.rule || ''}</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-xs px-2 py-1 rounded ${config.bg} ${config.color}`}>
          {config.label}
        </span>
        {fixDetails && (
          <button
            className="p-1.5 text-tertiary hover:text-accent transition-colors"
            title="View fix details"
          >
            <Code className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}

function BeforeAfterComparison({ original, remediated }: BeforeAfterComparisonProps): React.ReactElement {
  return (
    <div className="card">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <h4 className="text-sm font-medium text-tertiary mb-2">Original</h4>
          <div className="p-4 rounded-lg bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] border-opacity-20">
            <div className="flex items-center gap-2 mb-2">
              <XCircle className="w-4 h-4 text-[var(--feature-danger-content)]" />
              <span className="text-sm text-[var(--feature-danger-content)]">
                Score: {original?.score || 0}/100
              </span>
            </div>
            <p className="text-sm text-secondary">{original?.issues || 0} issues found</p>
          </div>
        </div>
        <div>
          <h4 className="text-sm font-medium text-tertiary mb-2">Remediated</h4>
          <div className="p-4 rounded-lg bg-[var(--feature-success-surface)] border border-[var(--feature-success-content)] border-opacity-20">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" />
              <span className="text-sm text-[var(--feature-success-content)]">
                Score: {remediated?.score || 0}/100
              </span>
            </div>
            <p className="text-sm text-secondary">
              {remediated?.remaining_issues || 0} issues remaining
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Remediate(): React.ReactElement {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  const [scan, setScan] = useState<Scan | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [remediating, setRemediating] = useState<boolean>(false);
  const [progress, setProgress] = useState<Progress>({
    current: 0,
    total: 0,
    status: 'idle',
  });
  const [issueStatuses, setIssueStatuses] = useState<Record<number, IssueStatusType>>({});
  const [result, setResult] = useState<RemediationResult | null>(null);

  useEffect(() => {
    const fetchScan = async (): Promise<void> => {
      try {
        setLoading(true);
        const data = await scansApi.getScan(scanId!);
        // Issues may be at data.issues or nested in data.result.issues
        const scanData = data as unknown as Record<string, unknown>;
        const issues = (scanData.issues as Issue[])
          || ((scanData.result as Record<string, unknown>)?.issues as Issue[])
          || [];
        const scanWithIssues = { ...data, issues } as Scan;
        setScan(scanWithIssues);
        setProgress((p) => ({ ...p, total: issues.length }));

        // Initialize issue statuses
        const statuses: Record<number, IssueStatusType> = {};
        issues.forEach((_issue, idx) => {
          statuses[idx] = 'pending';
        });
        setIssueStatuses(statuses);
      } catch (err) {
        console.error('Failed to fetch scan:', err);
        toast.error('Failed to load scan details', 'Error');
      } finally {
        setLoading(false);
      }
    };

    if (scanId) {
      fetchScan();
    }
  }, [scanId, toast]);

  // TODO: Replace with real progress polling when remediation is made async

  const startRemediation = async (): Promise<void> => {
    if (!scan) return;

    trackEvent('dash-remediate-started', { scan_type: scan?.file_name?.split('.').pop() || 'unknown' });

    setRemediating(true);
    setProgress((p) => ({ ...p, status: 'running', current: 0 }));

    try {
      // TODO: Make remediation async with a progress endpoint so we can
      // show real progress instead of waiting for the full response.
      // Currently the API is synchronous — runs entire remediation and
      // returns the result in one response.

      // Mark all issues as in_progress while we wait
      const issues = scan.issues || [];
      const inProgressStatuses: Record<number, IssueStatusType> = {};
      issues.forEach((_issue, idx) => {
        inProgressStatuses[idx] = 'in_progress';
      });
      setIssueStatuses(inProgressStatuses);
      setProgress((p) => ({ ...p, current: 0, status: 'running' }));

      // Run the actual remediation (synchronous API call)
      const response = await scansApi.remediateScan(scanId!, {
        use_ai: true,
        verify_fixes: true,
      });
      setResult(response);

      // Update statuses based on actual result
      const fixedCount = response.fixed_count || response.fixed_issues?.length || 0;
      const manualCount = response.manual_issues?.length || 0;
      const newStatuses: Record<number, IssueStatusType> = {};

      // Try to match by description/message first
      const fixedDescs = new Set((response.fixed_issues || []).map((f: { description: string }) => f.description));
      const manualDescs = new Set((response.manual_issues || []).map((m: { description: string }) => m.description));

      let matchedFixed = 0;
      let matchedManual = 0;

      issues.forEach((issue: Issue, idx: number) => {
        const desc = issue.description || issue.message || issue.title || '';
        if (fixedDescs.has(desc)) {
          newStatuses[idx] = 'fixed';
          matchedFixed++;
        } else if (manualDescs.has(desc)) {
          newStatuses[idx] = 'manual';
          matchedManual++;
        }
      });

      // If description matching didn't work, assign by count
      if (matchedFixed === 0 && fixedCount > 0) {
        let assigned = 0;
        issues.forEach((_issue: Issue, idx: number) => {
          if (newStatuses[idx]) return; // already matched
          if (assigned < fixedCount) {
            newStatuses[idx] = 'fixed';
            assigned++;
          }
        });
      }
      if (matchedManual === 0 && manualCount > 0) {
        let assigned = 0;
        issues.forEach((_issue: Issue, idx: number) => {
          if (newStatuses[idx]) return;
          if (assigned < manualCount) {
            newStatuses[idx] = 'manual';
            assigned++;
          }
        });
      }

      // Remaining are failed
      issues.forEach((_issue: Issue, idx: number) => {
        if (!newStatuses[idx]) {
          newStatuses[idx] = 'failed';
        }
      });

      setIssueStatuses(newStatuses);

      setProgress((p) => ({ ...p, current: issues.length, status: 'completed' }));
      toast.success(
        `Remediation complete! ${response.fixed_count || 0} issues fixed.`,
        'Success'
      );
    } catch (err: unknown) {
      console.error('Remediation failed:', err);
      const remediateError = err as Error;
      toast.error(remediateError.message || 'Remediation failed', 'Error');
      setProgress((p) => ({ ...p, status: 'idle' }));
    } finally {
      setRemediating(false);
    }
  };

  // Pause and Resume used to sit here. They only flipped a local label:
  // remediation is a single synchronous request with no cancellation, so
  // the work carried on regardless and the completion handler overwrote
  // whatever the user had clicked. A control that does nothing is worse
  // than no control, so they are gone until the server can be told to
  // stop.

  const downloadRemediated = async (): Promise<void> => {
    trackEvent('dash-download-fixed', { scan_type: scan?.file_name?.split('.').pop() || 'unknown' });
    try {
      const blob = await scansApi.downloadRemediated(scanId!);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `remediated-${scan?.file_name || 'document'}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Download started', 'Success');
    } catch {
      toast.error('Failed to download remediated file', 'Error');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading remediation">
        <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading remediation data...</span>
      </div>
    );
  }

  if (!scan) {
    return (
      <div className="p-8">
        <div className="max-w-4xl mx-auto">
          <div className="card text-center py-12">
            <XCircle className="w-12 h-12 mx-auto text-[var(--feature-danger-content)] mb-4" aria-hidden="true" />
            <p className="text-lg font-medium text-primary mb-2">Scan not found</p>
            <button onClick={() => navigate('/history')} className="btn-primary mt-4">
              Back to History
            </button>
          </div>
        </div>
      </div>
    );
  }

  const fixedCount = Object.values(issueStatuses).filter((s) => s === 'fixed').length;
  const manualCount = Object.values(issueStatuses).filter((s) => s === 'manual').length;
  const failedCount = Object.values(issueStatuses).filter((s) => s === 'failed').length;

  return (
    <div className="p-8">
      <div className="max-w-4xl mx-auto">
        {/* Breadcrumbs & Header */}
        <Breadcrumbs items={[
          { label: 'History', href: '/history' },
          { label: scan.file_name || 'Document', href: `/scan/${scanId}` },
          { label: 'Remediate' },
        ]} />

        <div className="flex items-center gap-4 mb-6">
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-primary">Auto-Remediation</h1>
            <div className="flex items-center gap-2 mt-1">
              <FileText className="w-4 h-4 text-tertiary" aria-hidden="true" />
              <span className="text-sm text-secondary">{scan.file_name || 'Document'}</span>
            </div>
          </div>
          {progress.status === 'completed' && result?.output_file && (
            <button onClick={downloadRemediated} className="btn-primary flex items-center gap-2">
              <Download className="w-4 h-4" />
              Download Fixed
            </button>
          )}
        </div>

        {/* Progress Card */}
        <div className="card mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-primary">Remediation Progress</h2>
            <div className="flex items-center gap-2">
              {progress.status === 'idle' && (
                <button
                  onClick={startRemediation}
                  className="btn-primary flex items-center gap-2"
                  disabled={remediating}
                >
                  <Play className="w-4 h-4" />
                  Start Remediation
                </button>
              )}
              {progress.status === 'completed' && (
                <button
                  onClick={() => {
                    setProgress({ current: 0, total: scan.issues?.length || 0, status: 'idle' });
                    setIssueStatuses({});
                    setResult(null);
                  }}
                  className="btn-secondary flex items-center gap-2"
                >
                  <RotateCcw className="w-4 h-4" />
                  Reset
                </button>
              )}
            </div>
          </div>

          <ProgressBar
            current={progress.current}
            total={progress.total}
            label={
              progress.status === 'completed'
                ? 'Remediation Complete'
                : progress.status === 'running'
                ? 'Processing issues...'
                : progress.status === 'paused'
                ? 'Paused'
                : 'Ready to start'
            }
          />

          {/* Stats */}
          {progress.current > 0 && (
            <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-[var(--border-primary)]">
              <div className="text-center">
                <p className="text-2xl font-bold text-[var(--feature-success-content)]">
                  {fixedCount}
                </p>
                <p className="text-sm text-tertiary">Fixed</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-[var(--feature-warning-content)]">
                  {manualCount}
                </p>
                <p className="text-sm text-tertiary">Manual Review</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-[var(--feature-danger-content)]">
                  {failedCount}
                </p>
                <p className="text-sm text-tertiary">Failed</p>
              </div>
            </div>
          )}
        </div>

        {/* Before/After Comparison */}
        {progress.status === 'completed' && result && (
          <div className="mb-6">
            <h2 className="text-lg font-semibold text-primary mb-4">Before & After</h2>
            <BeforeAfterComparison
              original={{
                score: result.original_score || result.original_compliance_score || ((scan as unknown as Record<string, Record<string, number>>)?.result?.compliance_score) || 0,
                issues: result.total_issues || scan?.issues?.length || 0,
              }}
              remediated={{
                score: result.remediated_score || result.remediated_compliance_score || 100,
                remaining_issues: manualCount + failedCount,
              }}
            />
          </div>
        )}

        {/* Issues List */}
        <div className="card">
          <h2 className="text-lg font-semibold text-primary mb-4">
            Issues ({scan.issues?.length || 0})
          </h2>
          <div className="max-h-96 overflow-y-auto">
            {(scan.issues || []).map((issue: Issue, idx: number) => (
              <IssueRow
                key={idx}
                issue={issue}
                status={issueStatuses[idx] || 'pending'}
                fixDetails={null}
              />
            ))}
            {(!scan.issues || scan.issues.length === 0) && (
              <div className="text-center py-8">
                <CheckCircle className="w-12 h-12 mx-auto text-[var(--feature-success-content)] mb-4" />
                <p className="text-primary font-medium">No issues to remediate!</p>
                <p className="text-sm text-tertiary">This document is already accessible.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
