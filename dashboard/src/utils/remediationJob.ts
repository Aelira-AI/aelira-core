import type { RemediationJobStatus } from '../api/scans';

export interface RemediationJobLike {
  status: RemediationJobStatus['status'];
  error_code?: string | null;
}

export type RemediationJobState =
  | 'queued'
  | 'running'
  | 'partial'
  | 'timed_out'
  | 'failed'
  | 'completed';

export type RemediationJobTerminalState = Exclude<RemediationJobState, 'queued' | 'running'>;

export type RemediationPollResult<T extends RemediationJobLike = RemediationJobStatus> =
  | {
      outcome: 'terminal';
      state: RemediationJobTerminalState;
      job: T;
    }
  | {
      outcome: 'client_timeout';
      state: 'client_timeout';
      job: T | null;
    };

export interface RemediationPollingOptions<
  T extends RemediationJobLike = RemediationJobStatus
> {
  signal?: AbortSignal;
  timeoutMs?: number;
  initialDelayMs?: number;
  maxDelayMs?: number;
  backoffMultiplier?: number;
  now?: () => number;
  wait?: (delayMs: number, signal?: AbortSignal) => Promise<void>;
  onUpdate?: (job: T, state: RemediationJobState) => void;
}

export interface RemediationStartAttempt {
  scanId: string;
  generation: number;
  signal: AbortSignal;
}

export interface RemediationStartCoordinator {
  activate: (scanId?: string) => void;
  begin: (scanId: string) => RemediationStartAttempt;
  invalidate: () => void;
  isCurrent: (attempt: RemediationStartAttempt) => boolean;
}

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const DEFAULT_INITIAL_DELAY_MS = 1000;
const DEFAULT_MAX_DELAY_MS = 10_000;
const DEFAULT_BACKOFF_MULTIPLIER = 1.5;

export function classifyRemediationJob(job: RemediationJobLike): RemediationJobState {
  if (job.status === 'pending') {
    return 'queued';
  }
  if (job.status === 'processing') {
    return 'running';
  }
  if (job.status === 'completed') {
    return 'completed';
  }
  if (job.error_code === 'manual_required') {
    return 'partial';
  }
  if (job.error_code === 'job_execution_timeout') {
    return 'timed_out';
  }
  return 'failed';
}

function abortError(): Error {
  const error = new Error('Remediation polling aborted');
  error.name = 'AbortError';
  return error;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw abortError();
  }
}

function defaultWait(delayMs: number, signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(() => {
      signal?.removeEventListener('abort', handleAbort);
      resolve();
    }, delayMs);
    const handleAbort = () => {
      globalThis.clearTimeout(timeout);
      signal?.removeEventListener('abort', handleAbort);
      reject(abortError());
    };
    signal?.addEventListener('abort', handleAbort, { once: true });
  });
}

function nonNegativeFinite(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isFinite(value) && value >= 0 ? value : fallback;
}

function positiveFinite(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isFinite(value) && value > 0 ? value : fallback;
}

export function createRemediationStartCoordinator(): RemediationStartCoordinator {
  let generation = 0;
  let controller: AbortController | null = null;
  let activeScanId: string | undefined;

  return {
    activate(scanId) {
      generation += 1;
      controller?.abort();
      controller = null;
      activeScanId = scanId;
    },
    begin(scanId) {
      controller?.abort();
      controller = new AbortController();
      generation += 1;
      return { scanId, generation, signal: controller.signal };
    },
    invalidate() {
      generation += 1;
      controller?.abort();
      controller = null;
      activeScanId = undefined;
    },
    isCurrent(attempt) {
      return (
        !attempt.signal.aborted
        && attempt.generation === generation
        && attempt.scanId === activeScanId
      );
    },
  };
}

export async function pollRemediationJob<T extends RemediationJobLike = RemediationJobStatus>(
  getStatus: (signal?: AbortSignal) => Promise<T>,
  options: RemediationPollingOptions<T> = {}
): Promise<RemediationPollResult<T>> {
  const now = options.now ?? Date.now;
  const wait = options.wait ?? defaultWait;
  const timeoutMs = nonNegativeFinite(options.timeoutMs, DEFAULT_TIMEOUT_MS);
  const maxDelayMs = positiveFinite(options.maxDelayMs, DEFAULT_MAX_DELAY_MS);
  let delayMs = Math.min(
    positiveFinite(options.initialDelayMs, DEFAULT_INITIAL_DELAY_MS),
    maxDelayMs
  );
  const backoffMultiplier = Math.max(
    1,
    positiveFinite(options.backoffMultiplier, DEFAULT_BACKOFF_MULTIPLIER)
  );
  const deadline = now() + timeoutMs;
  let latestJob: T | null = null;
  const deadlineController = new AbortController();
  let stopPolling: ((reason: 'caller_abort' | 'client_timeout') => void) | undefined;
  const stopPromise = new Promise<'caller_abort' | 'client_timeout'>((resolve) => {
    stopPolling = resolve;
  });
  let stopReason: 'caller_abort' | 'client_timeout' | null = null;
  const stop = (reason: 'caller_abort' | 'client_timeout') => {
    if (stopReason !== null) return;
    stopReason = reason;
    deadlineController.abort();
    stopPolling?.(reason);
  };
  const handleCallerAbort = () => stop('caller_abort');
  throwIfAborted(options.signal);
  options.signal?.addEventListener('abort', handleCallerAbort, { once: true });
  const deadlineTimer = globalThis.setTimeout(() => stop('client_timeout'), timeoutMs);

  try {
    while (now() < deadline) {
      throwIfAborted(options.signal);
      const requestPromise = getStatus(deadlineController.signal).then(
        (job) => ({ kind: 'job' as const, job }),
        (error: unknown) => ({ kind: 'error' as const, error })
      );
      const requestOutcome = await Promise.race([
        requestPromise,
        stopPromise.then((reason) => reason === 'caller_abort'
          ? { kind: 'caller_abort' as const }
          : { kind: 'client_timeout' as const }),
      ]);

      if (requestOutcome.kind === 'caller_abort') {
        throw abortError();
      }
      if (requestOutcome.kind === 'client_timeout') {
        break;
      }
      if (requestOutcome.kind === 'error') {
        throw requestOutcome.error;
      }

      latestJob = requestOutcome.job;
      throwIfAborted(options.signal);

      const state = classifyRemediationJob(latestJob);
      options.onUpdate?.(latestJob, state);
      if (state !== 'queued' && state !== 'running') {
        return { outcome: 'terminal', state, job: latestJob };
      }

      const remainingMs = deadline - now();
      if (remainingMs <= 0) {
        break;
      }
      await wait(Math.min(delayMs, remainingMs), deadlineController.signal);
      delayMs = Math.min(maxDelayMs, Math.ceil(delayMs * backoffMultiplier));
    }
  } catch (error) {
    if (options.signal?.aborted) {
      throw abortError();
    }
    if (stopReason !== 'client_timeout') {
      throw error;
    }
  } finally {
    globalThis.clearTimeout(deadlineTimer);
    options.signal?.removeEventListener('abort', handleCallerAbort);
  }

  return { outcome: 'client_timeout', state: 'client_timeout', job: latestJob };
}
