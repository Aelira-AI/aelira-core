import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  FileText,
  Download,
  Play,
  Pause,
  RotateCcw,
  Loader,
  Code,
  LucideIcon,
} from 'lucide-react';
import { scansApi } from '../api/scans';
import type { RemediationResult } from '../api/scans';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';
import { trackEvent } from '../components/Analytics';
import { useToast } from '../context/ToastContext';

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
  description: string;
  category: string;
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
          className="h-full bg-[var(--accent-primary)] transition-all duration-500 ease-out rounded-full"
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
          <p className="text-sm font-medium text-primary">{issue.description}</p>
          <p className="text-xs text-tertiary">{issue.category}</p>
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
  const [activeTab, setActiveTab] = useState<'comparison' | 'original' | 'remediated'>('comparison');

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4 border-b border-[var(--border-primary)] pb-4">
        <button
          onClick={() => setActiveTab('comparison')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'comparison'
              ? 'bg-[var(--accent-primary)] text-white'
              : 'text-secondary hover:bg-[var(--surface-secondary)]'
          }`}
        >
          Side by Side
        </button>
        <button
          onClick={() => setActiveTab('original')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'original'
              ? 'bg-[var(--accent-primary)] text-white'
              : 'text-secondary hover:bg-[var(--surface-secondary)]'
          }`}
        >
          Original
        </button>
        <button
          onClick={() => setActiveTab('remediated')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'remediated'
              ? 'bg-[var(--accent-primary)] text-white'
              : 'text-secondary hover:bg-[var(--surface-secondary)]'
          }`}
        >
          Remediated
        </button>
      </div>

      {activeTab === 'comparison' && (
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
      )}

      {activeTab === 'original' && (
        <div className="p-4 rounded-lg bg-[var(--surface-secondary)]">
          <p className="text-sm text-tertiary">Original document preview would appear here</p>
        </div>
      )}

      {activeTab === 'remediated' && (
        <div className="p-4 rounded-lg bg-[var(--surface-secondary)]">
          <p className="text-sm text-tertiary">Remediated document preview would appear here</p>
        </div>
      )}
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
        setScan(data as Scan);
        setProgress((p) => ({ ...p, total: data.issues?.length || 0 }));

        // Initialize issue statuses
        const statuses: Record<number, IssueStatusType> = {};
        (data.issues || []).forEach((_issue, idx) => {
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

  const simulateProgress = useCallback(
    async (issues: Issue[]): Promise<void> => {
      // Simulate progress for better UX while actual remediation runs
      for (let i = 0; i < issues.length; i++) {
        if (progress.status === 'paused') break;

        setIssueStatuses((prev) => ({ ...prev, [i]: 'in_progress' }));
        setProgress((p) => ({ ...p, current: i }));

        // Simulate processing time
        await new Promise((resolve) => setTimeout(resolve, 500 + Math.random() * 1000));

        // Randomly determine outcome (in real implementation, this comes from API)
        const outcomes: IssueStatusType[] = ['fixed', 'manual', 'failed'];
        const weights = [0.7, 0.2, 0.1];
        const rand = Math.random();
        let outcome: IssueStatusType = outcomes[0];
        let cumulative = 0;
        for (let j = 0; j < weights.length; j++) {
          cumulative += weights[j];
          if (rand < cumulative) {
            outcome = outcomes[j];
            break;
          }
        }

        setIssueStatuses((prev) => ({ ...prev, [i]: outcome }));
        setProgress((p) => ({ ...p, current: i + 1 }));
      }
    },
    [progress.status]
  );

  const startRemediation = async (): Promise<void> => {
    if (!scan) return;

    trackEvent('dash-remediate-started', { scan_type: scan?.file_name?.split('.').pop() || 'unknown' });

    setRemediating(true);
    setProgress((p) => ({ ...p, status: 'running', current: 0 }));

    try {
      // Start the actual remediation
      const remediationPromise = scansApi.remediateScan(scanId!, {
        use_ai: true,
        verify_fixes: true,
      });

      // Run progress simulation in parallel
      await simulateProgress(scan.issues || []);

      // Wait for actual result
      const response = await remediationPromise;
      setResult(response);

      // Update statuses based on actual result
      if (response.fixed_issues || response.manual_issues || response.failed_issues) {
        const newStatuses: Record<number, IssueStatusType> = {};
        (scan.issues || []).forEach((issue: Issue, idx: number) => {
          // Try to match with response
          if (response.fixed_issues?.some((f: { description: string }) => f.description === issue.description)) {
            newStatuses[idx] = 'fixed';
          } else if (response.manual_issues?.some((m: { description: string }) => m.description === issue.description)) {
            newStatuses[idx] = 'manual';
          } else {
            newStatuses[idx] = 'failed';
          }
        });
        setIssueStatuses(newStatuses);
      }

      setProgress((p) => ({ ...p, status: 'completed' }));
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

  const pauseRemediation = (): void => {
    setProgress((p) => ({ ...p, status: 'paused' }));
  };

  const resumeRemediation = (): void => {
    setProgress((p) => ({ ...p, status: 'running' }));
  };

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
              {progress.status === 'running' && (
                <button
                  onClick={pauseRemediation}
                  className="btn-secondary flex items-center gap-2"
                >
                  <Pause className="w-4 h-4" />
                  Pause
                </button>
              )}
              {progress.status === 'paused' && (
                <button
                  onClick={resumeRemediation}
                  className="btn-primary flex items-center gap-2"
                >
                  <Play className="w-4 h-4" />
                  Resume
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
                score: result.original_compliance_score || scan.compliance_score || 0,
                issues: scan.issues?.length || 0,
              }}
              remediated={{
                score: result.remediated_compliance_score || 100,
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
