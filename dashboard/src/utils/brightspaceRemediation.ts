import {
  pollRemediationJob as pollDurableRemediationJob,
  type RemediationPollingOptions,
} from './remediationJob.ts';

export type BrightspaceRemediationOutcome =
  | 'completed'
  | 'manual_required'
  | 'no_op'
  | 'failed';

export interface BrightspaceRemediationResult {
  cloud_file_id: string;
  status: BrightspaceRemediationOutcome;
  fixed_count: number;
  manual_count: number;
  failed_count: number;
  skipped_count: number;
  has_remediated_version: boolean;
  ai_used: boolean | null;
  external_ai_used: boolean | null;
  providers: string[] | null;
  purpose_decisions: Record<string, string> | null;
  error_code?: string | null;
}

export interface BrightspaceRemediationBatchResult {
  status: 'completed';
  requested_count: number;
  completed_count: number;
  manual_count: number;
  failed_count: number;
  fixed_count: number;
  results: BrightspaceRemediationResult[];
}

export interface BrightspaceRemediationJobStatus {
  job_id: string;
  cloud_file_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  status_url: string;
  error_code?: string | null;
  outcome_status?: BrightspaceRemediationOutcome | null;
  fixed_count?: number | null;
  manual_count?: number | null;
  failed_count?: number | null;
  skipped_count?: number | null;
  download_available?: boolean | null;
  ai_used?: boolean | null;
  external_ai_used?: boolean | null;
  providers?: string[] | null;
  purpose_decisions?: Record<string, string> | null;
}

export interface BrightspaceRemediationPollingOptions
  extends RemediationPollingOptions<BrightspaceRemediationJobStatus> {
  getStatus: (
    statusUrl: string,
    signal?: AbortSignal
  ) => Promise<BrightspaceRemediationJobStatus>;
  maxConcurrentJobs?: number;
}

function requireTerminalValue<T>(value: T | null | undefined, field: string): T {
  if (value === null || value === undefined) {
    throw new Error(`Brightspace remediation response omitted ${field}`);
  }
  return value;
}

export function toBrightspaceRemediationResult(
  job: BrightspaceRemediationJobStatus
): BrightspaceRemediationResult {
  return {
    cloud_file_id: job.cloud_file_id,
    status: requireTerminalValue(job.outcome_status, 'outcome_status'),
    fixed_count: requireTerminalValue(job.fixed_count, 'fixed_count'),
    manual_count: requireTerminalValue(job.manual_count, 'manual_count'),
    failed_count: requireTerminalValue(job.failed_count, 'failed_count'),
    skipped_count: requireTerminalValue(job.skipped_count, 'skipped_count'),
    has_remediated_version: requireTerminalValue(
      job.download_available,
      'download_available'
    ),
    ai_used: job.ai_used ?? null,
    external_ai_used: job.external_ai_used ?? null,
    providers: job.providers ?? null,
    purpose_decisions: job.purpose_decisions ?? null,
    error_code: job.error_code,
  };
}

export async function resolveBrightspaceRemediationJob(
  initial: BrightspaceRemediationJobStatus,
  options: BrightspaceRemediationPollingOptions
): Promise<BrightspaceRemediationResult> {
  if (initial.status === 'completed' || initial.status === 'failed') {
    return toBrightspaceRemediationResult(initial);
  }
  const result = await pollDurableRemediationJob(
    (signal) => options.getStatus(initial.status_url, signal),
    options
  );
  if (result.outcome === 'client_timeout') {
    throw new Error('Brightspace remediation timed out before the worker finished');
  }
  return toBrightspaceRemediationResult(result.job);
}

export async function resolveBoundedBrightspaceRemediationBatch(
  jobs: BrightspaceRemediationJobStatus[],
  options: BrightspaceRemediationPollingOptions
): Promise<BrightspaceRemediationResult[]> {
  if (jobs.length === 0) return [];
  const requestedConcurrency = Number.isFinite(options.maxConcurrentJobs)
    ? Math.floor(options.maxConcurrentJobs ?? 5)
    : 5;
  const concurrency = Math.max(1, Math.min(jobs.length, requestedConcurrency, 8));
  const results = new Array<BrightspaceRemediationResult>(jobs.length);
  const controller = new AbortController();
  const handleCallerAbort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  else options.signal?.addEventListener('abort', handleCallerAbort, { once: true });
  if (controller.signal.aborted) {
    const error = new Error('Brightspace remediation polling aborted');
    error.name = 'AbortError';
    throw error;
  }
  let nextIndex = 0;
  let primaryError: unknown;
  const worker = async () => {
    while (!controller.signal.aborted && nextIndex < jobs.length) {
      const index = nextIndex;
      nextIndex += 1;
      try {
        results[index] = await resolveBrightspaceRemediationJob(jobs[index], {
          ...options,
          signal: controller.signal,
        });
      } catch (error) {
        if (primaryError === undefined) primaryError = error;
        controller.abort();
        throw error;
      }
    }
  };
  try {
    await Promise.allSettled(Array.from({ length: concurrency }, worker));
    if (primaryError !== undefined) throw primaryError;
    return results;
  } finally {
    options.signal?.removeEventListener('abort', handleCallerAbort);
  }
}

export function aggregateBrightspaceRemediationBatch(
  requestedCount: number,
  results: BrightspaceRemediationResult[]
): BrightspaceRemediationBatchResult {
  return {
    status: 'completed',
    requested_count: requestedCount,
    completed_count: results.filter((item) => item.status === 'completed').length,
    manual_count: results.reduce((total, item) => total + item.manual_count, 0),
    failed_count: results.filter((item) => item.status === 'failed').length,
    fixed_count: results.reduce((total, item) => total + item.fixed_count, 0),
    results,
  };
}
