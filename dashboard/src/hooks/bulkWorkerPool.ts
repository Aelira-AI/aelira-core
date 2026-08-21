export interface BulkPoolSettled<TItem, TResult> {
  item: TItem;
  status: 'fulfilled' | 'rejected';
  value?: TResult;
  reason?: unknown;
}

export interface BulkPoolResult<TItem, TResult> {
  settled: BulkPoolSettled<TItem, TResult>[];
  unstarted: TItem[];
  stopped: boolean;
}

interface BulkWorkerPoolOptions<TItem, TResult> {
  items: readonly TItem[];
  concurrency: number;
  process: (item: TItem) => Promise<TResult>;
}

export interface BulkWorkerPool<TItem, TResult> {
  run: () => Promise<BulkPoolResult<TItem, TResult>>;
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

export function createBulkWorkerPool<TItem, TResult>(
  options: BulkWorkerPoolOptions<TItem, TResult>,
): BulkWorkerPool<TItem, TResult> {
  if (!Number.isInteger(options.concurrency) || options.concurrency < 1) {
    throw new Error('Bulk worker pool concurrency must be a positive integer');
  }

  const items = [...options.items];
  const settled: BulkPoolSettled<TItem, TResult>[] = [];
  let nextIndex = 0;
  let paused = false;
  let stopped = false;
  let completion: Promise<BulkPoolResult<TItem, TResult>> | null = null;
  let releasePause: (() => void) | null = null;
  let pauseGate: Promise<void> | null = null;

  const waitUntilRunnable = async (): Promise<void> => {
    while (paused && !stopped) {
      if (!pauseGate) {
        pauseGate = new Promise((resolve) => {
          releasePause = resolve;
        });
      }
      await pauseGate;
    }
  };

  const worker = async (): Promise<void> => {
    while (true) {
      await waitUntilRunnable();
      if (stopped || nextIndex >= items.length) return;

      const item = items[nextIndex];
      nextIndex += 1;
      try {
        const value = await options.process(item);
        settled.push({ item, status: 'fulfilled', value });
      } catch (reason) {
        settled.push({ item, status: 'rejected', reason });
      }
    }
  };

  return {
    run: () => {
      if (!completion) {
        completion = Promise.all(
          Array.from(
            { length: Math.min(options.concurrency, items.length) },
            () => worker(),
          ),
        ).then(() => ({
          settled,
          unstarted: items.slice(nextIndex),
          stopped,
        }));
      }
      return completion;
    },
    pause: () => {
      if (!stopped) paused = true;
    },
    resume: () => {
      paused = false;
      const release = releasePause;
      releasePause = null;
      pauseGate = null;
      release?.();
    },
    stop: () => {
      stopped = true;
      paused = false;
      const release = releasePause;
      releasePause = null;
      pauseGate = null;
      release?.();
    },
  };
}

export function summarizeBulkPoolResult<TItem, TResult>(
  result: BulkPoolResult<TItem, TResult>,
): {
  succeeded: number;
  failed: number;
  unstarted: number;
  total: number;
  stopped: boolean;
} {
  return {
    succeeded: result.settled.filter((entry) => entry.status === 'fulfilled').length,
    failed: result.settled.filter((entry) => entry.status === 'rejected').length,
    unstarted: result.unstarted.length,
    total: result.settled.length + result.unstarted.length,
    stopped: result.stopped,
  };
}
