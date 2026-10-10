import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
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
import type { RemediationFixSummary, RemediationJobStatus } from '../api/scans';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';
import { useToast } from '../context/toast-context';
import { trackEvent } from '../utils/analytics';
import {
  classifyRemediationJob,
  classifyRemediationStartFailure,
  createRemediationStartCoordinator,
  pollRemediationJob,
} from '../utils/remediationJob';
import type { RemediationJobState } from '../utils/remediationJob';
import { remediationScore } from '../utils/remediationScore';
import { AggregateResults, RecordedOutcomeGroups } from "../components/results/RemediationOutcomeBreakdown";
import { ScoreComparison } from '../components/ScoreComparison';
import {
  pairIssuesWithFixes,
} from '../utils/remediationIssueOutcomes';
import { parsePDFCloudContext } from '../utils/pdfStructureEditor';
import type {
  RemediationIssueLike,
} from '../utils/remediationIssueOutcomes';

type Issue = RemediationIssueLike;

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
  | 'permission_denied'
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
    description: 'The server completed the job. Recorded results are shown below.',
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
  policy_denied: {
    title: 'LMS AI remediation is blocked by policy',
    description: 'Your institution’s LMS AI policy does not permit this remediation. Ask your institution administrator to review the LMS AI policy for remediation and alt text. You can return to the scan to review its findings.',
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    surface: 'bg-[var(--feature-warning-surface)]',
  },
  permission_denied: {
    title: 'Remediation permission denied',
    description: 'The server refused this request. Ask your administrator to check your access to this document and remediation. You can return to the scan to review its findings.',
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    surface: 'bg-[var(--feature-warning-surface)]',
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
    description: 'The server did not confirm whether a job was queued. Check the recorded status before retrying.',
    icon: XCircle,
    color: 'text-[var(--feature-danger-content)]',
    surface: 'bg-[var(--feature-danger-surface)]',
  },
};

function RemediateScan({ scanId }: { scanId?: string }): React.ReactElement {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const pollController = useRef<AbortController | null>(null);
  const startCoordinator = useRef(createRemediationStartCoordinator());
  const fixLoadGeneration = useRef(0);
  const scanSnapshot = useRef<Scan | null>(null);

  const [scan, setScan] = useState<Scan | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageState, setPageState] = useState<PageState>('idle');
  const [job, setJob] = useState<RemediationJobStatus | null>(null);
  const [recordedFixes, setRecordedFixes] = useState<RemediationFixSummary[] | null>(null);
  const [starting, setStarting] = useState(false);
  const [statusRetry, setStatusRetry] = useState(0);

  const loadRecordedFixes = useCallback(async (
    terminalJob: RemediationJobStatus
  ): Promise<void> => {
    const generation = ++fixLoadGeneration.current;
    if (!scanId || typeof terminalJob.fixed_count !== 'number') {
      setRecordedFixes(null);
      return;
    }
    try {
      const fixes = await scansApi.getRemediationFixes(scanId);
      if (generation === fixLoadGeneration.current) {
        setRecordedFixes(fixes.length === terminalJob.fixed_count ? fixes : null);
      }
    } catch {
      if (generation === fixLoadGeneration.current) setRecordedFixes(null);
    }
  }, [scanId]);

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
      await loadRecordedFixes(outcome.job);
      if (outcome.state === 'completed') {
        const scores = remediationScore(outcome.job, scanSnapshot.current || {});
        if (scores.success) toast.success(scores.description, scores.title);
        else toast.warning(scores.description, scores.title);
      } else if (outcome.state === 'partial') {
        toast.warning('Manual review is required', 'Remediation stopped');
      } else if (outcome.state === 'policy_denied') {
        toast.warning(STATE_PRESENTATION.policy_denied.description, STATE_PRESENTATION.policy_denied.title);
      } else if (outcome.state === 'failed' || outcome.state === 'timed_out') {
        toast.error('Remediation did not complete', 'Remediation stopped');
      }
    } catch (error) {
      if ((error as Error)?.name !== 'AbortError') {
        setPageState('monitoring_error');
      }
    }
  }, [loadRecordedFixes, toast]);

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
        const loadedScan = { ...(data as unknown as Scan), issues };
        scanSnapshot.current = loadedScan;
        setScan(loadedScan);

        try {
          const latest = await scansApi.getLatestRemediationJob(scanId);
          if (!active) return;
          if (latest === null) {
            setJob(null);
            setPageState('idle');
            return;
          }
          const latestState = classifyRemediationJob(latest);
          setJob(latest);
          setPageState(latestState);
          if (latestState === 'queued' || latestState === 'running') {
            void monitorJob(latest.status_url);
          } else {
            await loadRecordedFixes(latest);
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
      fixLoadGeneration.current += 1;
      coordinator.invalidate();
      pollController.current?.abort();
    };
  }, [loadRecordedFixes, monitorJob, scanId, toast, statusRetry]);

  const startRemediation = async (): Promise<void> => {
    if (!scanId || !scan || starting) return;
    pollController.current?.abort();
    const attempt = startCoordinator.current.begin(scanId);
    setStarting(true);
    setJob(null);
    fixLoadGeneration.current += 1;
    setRecordedFixes(null);
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
    } catch (error) {
      if (!startCoordinator.current.isCurrent(attempt)) return;
      const failure = classifyRemediationStartFailure(error);
      setPageState(failure);
      toast.error(STATE_PRESENTATION[failure].description, STATE_PRESENTATION[failure].title);
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

  const comparison = job ? remediationScore(job, scan) : null;
  const presentation = pageState === 'completed' && comparison
    ? { ...STATE_PRESENTATION[comparison.success ? 'completed' : 'partial'], title: comparison.title, description: comparison.description }
    : STATE_PRESENTATION[pageState];
  const StatusIcon = presentation.icon;
  const canStart =
    ['idle', 'completed', 'partial', 'timed_out', 'failed', 'policy_denied', 'permission_denied'].includes(pageState);
  const canResume = ['client_timeout', 'monitoring_error', 'request_failed'].includes(pageState);
  const isDenied = pageState === 'policy_denied' || pageState === 'permission_denied';
  const canDownload = job?.download_available === true && typeof job.download_url === 'string';
  const displayedProgress = job?.progress ?? (pageState === 'completed' ? 100 : 0);
  const issueRows = pairIssuesWithFixes(scan.issues || [], recordedFixes || [], job || undefined);
  const reviewQuery = new URLSearchParams();
  const cloudContext = parsePDFCloudContext(location.search);
  if (cloudContext.kind === 'cloud') reviewQuery.set('cloud_file_id', cloudContext.id);
  const reviewHref = `/review/${encodeURIComponent(scanId || '')}${reviewQuery.size ? `?${reviewQuery}` : ''}`;

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
          <div className="flex w-full flex-wrap gap-3 sm:w-auto">
          {job && job.status === 'completed' && cloudContext.kind !== 'invalid' && <Link to={reviewHref} className="btn-primary flex items-center justify-center gap-2">Review changes</Link>}
          {canDownload && (
            <button onClick={downloadArtifact} className="btn-secondary flex items-center justify-center gap-2">
              <Download className="h-4 w-4" aria-hidden="true" />
              Download improved working file
            </button>
          )}
          </div>
        </div>

        <section className="card mb-6" aria-labelledby="remediation-status-heading">
          <div className={`flex flex-col items-stretch justify-between gap-4 ${isDenied ? '' : 'sm:flex-row sm:items-start'}`}>
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <div className={`rounded-lg p-2 ${presentation.surface}`}>
                <StatusIcon
                  className={`h-5 w-5 ${presentation.color} ${presentation.animate ? 'animate-spin' : ''}`}
                  aria-hidden="true"
                />
              </div>
              <div className="min-w-0" aria-live="polite" aria-atomic="true">
                <h2 id="remediation-status-heading" className="text-lg font-semibold text-primary">
                  {presentation.title}
                </h2>
                <p className="mt-1 text-sm text-secondary">{presentation.description}</p>
                {job?.progress_message && (pageState === 'queued' || pageState === 'running') && (
                  <p className="mt-2 text-sm font-medium text-primary">{job.progress_message}</p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-3 sm:shrink-0">
              {canStart && (
                <button
                  onClick={startRemediation}
                  className="btn-primary flex items-center justify-center gap-2 sm:shrink-0"
                  disabled={starting}
                >
                  {starting ? <Loader className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                  {pageState === 'policy_denied' ? 'Try again after policy review'
                    : pageState === 'permission_denied' ? 'Try again after access review'
                    : pageState === 'idle' ? 'Start Remediation' : 'Run Again'}
                </button>
              )}
              {canResume && (
                <button
                  onClick={() => job ? void monitorJob(job.status_url) : setStatusRetry((value) => value + 1)}
                  className="btn-secondary flex items-center justify-center gap-2 sm:shrink-0"
                >
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  Check Status
                </button>
              )}
              {isDenied && (
                <button
                  onClick={() => navigate(`/scan/${scanId}`)}
                  className="btn-secondary flex items-center justify-center sm:shrink-0"
                >
                  Back to Scan
                </button>
              )}
            </div>
          </div>

          {pageState !== 'idle' && !isDenied && (
            <div className="mt-5 border-t border-[var(--border-primary)] pt-4">
              {job && ['completed', 'failed'].includes(job.status) ? (
                <p className="text-sm font-medium text-primary" role="status">
                  {pageState === 'completed' ? 'Automatic processing ended.' : 'Automatic processing stopped.'}
                  {pageState === 'partial' && ' Manual review is required.'}
                </p>
              ) : (<>
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
              </>)}
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
                Applied changes and unresolved findings are counted separately. An improved score does not approve the document for publication.
              </p>
            </div>
            <AggregateResults job={job} rows={issueRows} />
            {job.issue_outcomes?.some(outcome => outcome.source_index_scope === 'approved_subset') && <p className="text-sm text-secondary">This run reports the selected reviewed changes. Findings outside that selection are shown below without a new per-finding outcome; the remaining count comes from checking the whole saved file.</p>}
            {canDownload && <p className="text-sm text-secondary">The working file includes delivered automatic improvements. Review changes before publishing to an LMS or cloud storage; remaining manual work can be completed from this file.</p>}
            <ScoreComparison job={job} scan={scan} />
          </section>
        )}

        <section className="card" aria-labelledby="recorded-issues-heading">
          <div className="mb-4">
            <h2 id="recorded-issues-heading" className="text-lg font-semibold text-primary">
              Recorded Findings and Changes ({issueRows.length})
            </h2>
            <p className="mt-1 text-sm text-tertiary">
              {job?.issue_outcomes?.length
                ? 'Outcomes come from the recorded job and its source findings. Withheld changes were not delivered. Application alone does not verify a fix.'
                : recordedFixes
                ? 'Applied changes come from persisted remediation records; manual outcomes are shown only when the job totals reconcile exactly. Application alone does not verify a fix.'
                : 'Per-issue remediation outcomes are not available for this job.'}
            </p>
          </div>
          <RecordedOutcomeGroups rows={issueRows} />
        </section>
      </div>
    </div>
  );
}

export function Remediate(): React.ReactElement {
  const { scanId } = useParams<{ scanId: string }>();
  return <RemediateScan key={scanId || 'missing'} scanId={scanId} />;
}
