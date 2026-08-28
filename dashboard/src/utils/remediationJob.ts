import type { RemediationJobStatus } from '../api/scans';

export type RemediationJobState =
  | 'queued'
  | 'running'
  | 'partial'
  | 'timed_out'
  | 'failed'
  | 'completed';

export type RemediationJobTerminalState = Exclude<RemediationJobState, 'queued' | 'running'>;

export type RemediationPollResult =
  | {
      outcome: 'terminal';
      state: RemediationJobTerminalState;
      job: RemediationJobStatus;
    }
  | {
      outcome: 'client_timeout';
      state: 'client_timeout';
      job: RemediationJobStatus | null;
    };

export interface RemediationPollingOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  initialDelayMs?: number;
  maxDelayMs?: number;
  backoffMultiplier?: number;
  now?: () => number;
  wait?: (delayMs: number, signal?: AbortSignal) => Promise<void>;
  onUpdate?: (job: RemediationJobStatus, state: RemediationJobState) => void;
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

export function classifyRemediationJob(
  job: Pick<RemediationJobStatus, 'status' | 'error_code'>
): RemediationJobState {
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

export async function pollRemediationJob(
  getStatus: (signal?: AbortSignal) => Promise<RemediationJobStatus>,
  options: RemediationPollingOptions = {}
): Promise<RemediationPollResult> {
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
  let latestJob: RemediationJobStatus | null = null;
  const deadlineController = new AbortController();
  let deadlineElapsed = timeoutMs === 0;
  const handleCallerAbort = () => deadlineController.abort();
  throwIfAborted(options.signal);
  options.signal?.addEventListener('abort', handleCallerAbort, { once: true });
  const deadlineTimer = globalThis.setTimeout(() => {
    deadlineElapsed = true;
    deadlineController.abort();
  }, timeoutMs);

  try {
    while (now() < deadline) {
      throwIfAborted(options.signal);
      latestJob = await getStatus(deadlineController.signal);
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
    if (!deadlineElapsed) {
      throw error;
    }
  } finally {
    globalThis.clearTimeout(deadlineTimer);
    options.signal?.removeEventListener('abort', handleCallerAbort);
  }

  return { outcome: 'client_timeout', state: 'client_timeout', job: latestJob };
}
