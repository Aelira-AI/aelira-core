import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle,
  Clock,
  Download,
  FileText,
  Loader,
  Play,
  RefreshCw,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import { scansApi } from '../api/scans';
import type { RemediationJobStatus } from '../api/scans';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';
import { useToast } from '../context/toast-context';
import { trackEvent } from '../utils/analytics';
import {
  classifyRemediationJob,
  createRemediationStartCoordinator,
  pollRemediationJob,
} from '../utils/remediationJob';
import type { RemediationJobState } from '../utils/remediationJob';

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
  result?: { compliance_score?: number };
}

type PageState =
  | 'idle'
  | RemediationJobState
  | 'client_timeout'
  | 'monitoring_error'
  | 'request_failed';

interface StatePresentation {
  title: string;
  description: string;
  icon: LucideIcon;
  color: string;
  surface: string;
  animate?: boolean;
}

const STATE_PRESENTATION: Record<PageState, StatePresentation> = {
  idle: {
    title: 'Ready to start',
    description: 'Remediation runs as a durable background job. You can leave this page after it starts.',
    icon: Clock,
    color: 'text-[var(--content-secondary)]',
    surface: 'bg-[var(--surface-tertiary)]',
  },
  queued: {
    title: 'Queued',
    description: 'The server accepted this remediation job and will keep it available if you reload.',
    icon: Clock,
    color: 'text-[var(--feature-info-content)]',
    surface: 'bg-[var(--feature-info-surface)]',
  },
  running: {
    title: 'Remediation in progress',
    description: 'The server is processing this document. Progress below comes from the durable job.',
    icon: Loader,
    color: 'text-[var(--feature-info-content)]',
    surface: 'bg-[var(--feature-info-surface)]',
    animate: true,
  },
  completed: {
    title: 'Remediation complete',
    description: 'The server completed the job. Only recorded aggregate results are shown below.',
    icon: CheckCircle,
    color: 'text-[var(--feature-success-content)]',
    surface: 'bg-[var(--feature-success-surface)]',
  },
  partial: {
    title: 'Manual review required',
    description: 'Some work could not be completed automatically. No partial artifact was published.',
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    surface: 'bg-[var(--feature-warning-surface)]',
  },
  timed_out: {
    title: 'Remediation timed out',
    description: 'The server ended this job after its execution limit. No completion is claimed.',
    icon: XCircle,
    color: 'text-[var(--feature-danger-content)]',
    surface: 'bg-[var(--feature-danger-surface)]',
  },
  failed: {
    title: 'Remediation failed',
    description: 'The server reported a terminal failure. The original document remains available.',
    icon: XCircle,
    color: 'text-[var(--feature-danger-content)]',
    surface: 'bg-[var(--feature-danger-surface)]',
  },
  client_timeout: {
    title: 'Still running in the background',
    description: 'This page stopped waiting after a bounded period. The server job may still be running.',
    icon: Clock,
    color: 'text-[var(--feature-warning-content)]',
    surface: 'bg-[var(--feature-warning-surface)]',
  },
  monitoring_error: {
    title: 'Job status temporarily unavailable',
    description: 'The last server state is preserved. Check again without starting a duplicate job.',
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    surface: 'bg-[var(--feature-warning-surface)]',
  },
  request_failed: {
    title: 'Remediation could not be started',
    description: 'The server did not confirm a queued job. Try again when the service is available.',
    icon: XCircle,
    color: 'text-[var(--feature-danger-content)]',
    surface: 'bg-[var(--feature-danger-surface)]',
  },
};

function displayScore(score: number | null | undefined): string {
  return typeof score === 'number' ? `${Math.round(score)}/100` : 'Not available';
}

function AggregateResults({ job }: { job: RemediationJobStatus }): React.ReactElement | null {
  const values = [
    { label: 'Fixed', value: job.fixed_count, color: 'text-[var(--feature-success-content)]' },
    { label: 'Remaining', value: job.remaining_count, color: 'text-[var(--feature-warning-content)]' },
    { label: 'Total issues', value: job.total_issues, color: 'text-primary' },
    { label: 'Manual review', value: job.manual_count, color: 'text-[var(--feature-warning-content)]' },
    { label: 'Failed', value: job.failed_count, color: 'text-[var(--feature-danger-content)]' },
    { label: 'Skipped', value: job.skipped_count, color: 'text-[var(--content-secondary)]' },
  ].filter((item): item is { label: string; value: number; color: string } =>
    typeof item.value === 'number'
  );

  if (values.length === 0) return null;

  return (
    <div className={`grid gap-3 sm:grid-cols-2 ${values.length > 2 ? 'lg:grid-cols-4' : ''}`}>
      {values.map((item) => (
        <div key={item.label} className="rounded-lg border border-[var(--border-primary)] p-4 text-center">
          <p className={`text-2xl font-bold ${item.color}`}>{item.value}</p>
          <p className="mt-1 text-sm text-tertiary">{item.label}</p>
        </div>
      ))}
    </div>
  );
}

function ScoreComparison({ job, scan }: { job: RemediationJobStatus; scan: Scan }): React.ReactElement {
  const originalScore = job.original_score ?? scan.compliance_score ?? scan.result?.compliance_score;

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Original score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(originalScore)}</p>
      </div>
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Remediated score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(job.remediated_score)}</p>
      </div>
    </div>
  );
}

function RecordedIssueRow({ issue }: { issue: Issue }): React.ReactElement {
  return (
    <div className="flex flex-col items-start justify-between gap-3 border-b border-[var(--border-primary)] p-3 last:border-b-0 sm:flex-row sm:gap-4">
      <div className="flex min-w-0 items-start gap-3">
        <div className="rounded bg-[var(--surface-tertiary)] p-1.5">
          <FileText className="h-4 w-4 text-[var(--content-tertiary)]" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium text-primary">
            {issue.description || issue.message || issue.title || 'Accessibility issue'}
          </p>
          <p className="text-xs text-tertiary">
            {issue.category || issue.severity || issue.rule || 'Recorded scan finding'}
          </p>
        </div>
      </div>
      <span className="shrink-0 rounded bg-[var(--surface-tertiary)] px-2 py-1 text-xs text-tertiary sm:self-start">
        Outcome not reported
      </span>
    </div>
  );
}

function RemediateScan({ scanId }: { scanId?: string }): React.ReactElement {
  const navigate = useNavigate();
  const toast = useToast();
  const pollController = useRef<AbortController | null>(null);
  const startCoordinator = useRef(createRemediationStartCoordinator());

  const [scan, setScan] = useState<Scan | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageState, setPageState] = useState<PageState>('idle');
  const [job, setJob] = useState<RemediationJobStatus | null>(null);
  const [starting, setStarting] = useState(false);

  const monitorJob = useCallback(async (statusUrl: string): Promise<void> => {
    pollController.current?.abort();
    const controller = new AbortController();
    pollController.current = controller;

    try {
      const outcome = await pollRemediationJob(
        (signal) => scansApi.getRemediationJobStatus(statusUrl, signal),
        {
          signal: controller.signal,
          onUpdate: (updatedJob, state) => {
            setJob(updatedJob);
            setPageState(state);
          },
        }
      );
      if (outcome.outcome === 'client_timeout') {
        if (outcome.job) setJob(outcome.job);
        setPageState('client_timeout');
        return;
      }
      setJob(outcome.job);
      setPageState(outcome.state);
      if (outcome.state === 'completed') {
        toast.success('Remediation completed', 'Complete');
      } else if (outcome.state === 'partial') {
        toast.warning('Manual review is required', 'Remediation stopped');
      } else if (outcome.state === 'failed' || outcome.state === 'timed_out') {
        toast.error('Remediation did not complete', 'Remediation stopped');
      }
    } catch (error) {
      if ((error as Error)?.name !== 'AbortError') {
        setPageState('monitoring_error');
      }
    }
  }, [toast]);

  useEffect(() => {
    let active = true;
    const coordinator = startCoordinator.current;
    coordinator.activate(scanId);

    const load = async (): Promise<void> => {
      if (!scanId) return;
      try {
        const data = await scansApi.getScan(scanId);
        if (!active) return;
        const scanData = data as unknown as Record<string, unknown>;
        const result = scanData.result as Record<string, unknown> | undefined;
        const issues = (scanData.issues as Issue[]) || (result?.issues as Issue[]) || [];
        setStarting(false);
        setScan({ ...(data as unknown as Scan), issues });

        try {
          const latest = await scansApi.getLatestRemediationJob(scanId);
          if (!active) return;
          if (latest === null) return;
          const latestState = classifyRemediationJob(latest);
          setJob(latest);
          setPageState(latestState);
          if (latestState === 'queued' || latestState === 'running') {
            void monitorJob(latest.status_url);
          }
        } catch {
          if (active) setPageState('monitoring_error');
        }
      } catch {
        if (active) {
          setScan(null);
          toast.error('Failed to load scan details', 'Error');
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    void load();
    return () => {
      active = false;
      coordinator.invalidate();
      pollController.current?.abort();
    };
  }, [monitorJob, scanId, toast]);

  const startRemediation = async (): Promise<void> => {
    if (!scanId || !scan || starting) return;
    pollController.current?.abort();
    const attempt = startCoordinator.current.begin(scanId);
    setStarting(true);
    setJob(null);
    setPageState('queued');
    trackEvent('dash-remediate-started', {
      scan_type: scan.file_name?.split('.').pop() || 'unknown',
    });

    try {
      const started = await scansApi.startRemediationJob(scanId, {
        use_ai: true,
        verify_fixes: true,
      }, attempt.signal);
      if (!startCoordinator.current.isCurrent(attempt)) return;
      setPageState(started.status === 'processing' ? 'running' : 'queued');
      void monitorJob(started.status_url);
    } catch {
      if (!startCoordinator.current.isCurrent(attempt)) return;
      setPageState('request_failed');
      toast.error('Remediation could not be queued', 'Error');
    } finally {
      if (startCoordinator.current.isCurrent(attempt)) {
        setStarting(false);
      }
    }
  };

  const downloadArtifact = async (): Promise<void> => {
    if (!job?.download_available || !job.download_url) return;
    trackEvent('dash-download-fixed', {
      scan_type: scan?.file_name?.split('.').pop() || 'unknown',
    });
    try {
      const blob = await scansApi.downloadRemediationJob(job.download_url);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `remediated-${scan?.file_name || 'document'}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Download started', 'Success');
    } catch {
      toast.error('The remediated artifact is not available', 'Download failed');
    }
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center" role="status" aria-label="Loading remediation">
        <Loader className="h-8 w-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading remediation data...</span>
      </div>
    );
  }

  if (!scan) {
    return (
      <div className="p-4 sm:p-8">
        <div className="mx-auto max-w-4xl">
          <div className="card py-12 text-center">
            <XCircle className="mx-auto mb-4 h-12 w-12 text-[var(--feature-danger-content)]" aria-hidden="true" />
            <p className="mb-2 text-lg font-medium text-primary">Scan not found</p>
            <button onClick={() => navigate('/history')} className="btn-primary mt-4">
              Back to History
            </button>
          </div>
        </div>
      </div>
    );
  }

  const presentation = STATE_PRESENTATION[pageState];
  const StatusIcon = presentation.icon;
  const canStart =
    ['idle', 'completed', 'partial', 'timed_out', 'failed', 'request_failed'].includes(pageState)
    || (pageState === 'monitoring_error' && job === null);
  const canResume = Boolean(job?.status_url) && ['client_timeout', 'monitoring_error'].includes(pageState);
  const canDownload = job?.download_available === true && typeof job.download_url === 'string';
  const displayedProgress = job?.progress ?? (pageState === 'completed' ? 100 : 0);

  return (
    <div className="p-4 sm:p-8">
      <div className="mx-auto max-w-4xl">
        <Breadcrumbs items={[
          { label: 'History', href: '/history' },
          { label: scan.file_name || 'Document', href: `/scan/${scanId}` },
          { label: 'Remediate' },
        ]} />

        <div className="mb-6 flex flex-col items-start gap-4 sm:flex-row">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-bold text-primary">Auto-Remediation</h1>
            <div className="mt-1 flex items-center gap-2">
              <FileText className="h-4 w-4 text-tertiary" aria-hidden="true" />
              <span className="truncate text-sm text-secondary">{scan.file_name || 'Document'}</span>
            </div>
          </div>
          {canDownload && (
            <button onClick={downloadArtifact} className="btn-primary flex w-full items-center justify-center gap-2 sm:w-auto">
              <Download className="h-4 w-4" aria-hidden="true" />
              Download Remediated File
            </button>
          )}
        </div>

        <section className="card mb-6" aria-labelledby="remediation-status-heading">
          <div className="flex flex-col items-stretch justify-between gap-4 sm:flex-row sm:items-start">
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <div className={`rounded-lg p-2 ${presentation.surface}`}>
                <StatusIcon
                  className={`h-5 w-5 ${presentation.color} ${presentation.animate ? 'animate-spin' : ''}`}
                  aria-hidden="true"
                />
              </div>
              <div>
                <h2 id="remediation-status-heading" className="text-lg font-semibold text-primary">
                  {presentation.title}
                </h2>
                <p className="mt-1 text-sm text-secondary">{presentation.description}</p>
                {job?.progress_message && (pageState === 'queued' || pageState === 'running') && (
                  <p className="mt-2 text-sm font-medium text-primary">{job.progress_message}</p>
                )}
              </div>
            </div>

            {canStart && (
              <button
                onClick={startRemediation}
                className="btn-primary flex items-center justify-center gap-2 sm:shrink-0"
                disabled={starting}
              >
                {starting ? <Loader className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {pageState === 'idle' ? 'Start Remediation' : 'Run Again'}
              </button>
            )}
            {canResume && job && (
              <button
                onClick={() => void monitorJob(job.status_url)}
                className="btn-secondary flex items-center justify-center gap-2 sm:shrink-0"
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                Check Status
              </button>
            )}
          </div>

          {pageState !== 'idle' && (
            <div className="mt-5 border-t border-[var(--border-primary)] pt-4">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="text-secondary">Server progress</span>
                <span className="font-medium text-primary">{displayedProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-tertiary)]">
                <div
                  className="h-full rounded-full bg-[var(--accent-solid)] transition-all duration-500"
                  style={{ width: `${Math.min(100, Math.max(0, displayedProgress))}%` }}
                />
              </div>
            </div>
          )}
        </section>

        {job && !['queued', 'running'].includes(pageState) && (
          <section className="card mb-6 space-y-4" aria-labelledby="recorded-results-heading">
            <div>
              <h2 id="recorded-results-heading" className="text-lg font-semibold text-primary">
                Recorded Results
              </h2>
              <p className="mt-1 text-sm text-tertiary">
                These are document-level values returned by the remediation job.
              </p>
            </div>
            <AggregateResults job={job} />
            <ScoreComparison job={job} scan={scan} />
          </section>
        )}

        <section className="card" aria-labelledby="recorded-issues-heading">
          <div className="mb-4">
            <h2 id="recorded-issues-heading" className="text-lg font-semibold text-primary">
              Recorded Issues ({scan.issues?.length || 0})
            </h2>
            <p className="mt-1 text-sm text-tertiary">
              Per-issue remediation outcomes are not available for this job.
            </p>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {(scan.issues || []).map((issue, index) => (
              <RecordedIssueRow key={`${issue.description || issue.message || 'issue'}-${index}`} issue={issue} />
            ))}
            {(!scan.issues || scan.issues.length === 0) && (
              <div className="py-8 text-center">
                <FileText className="mx-auto mb-4 h-12 w-12 text-tertiary" aria-hidden="true" />
                <p className="font-medium text-primary">No recorded issues are available for this scan.</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

export function Remediate(): React.ReactElement {
  const { scanId } = useParams<{ scanId: string }>();
  return <RemediateScan key={scanId || 'missing'} scanId={scanId} />;
}
