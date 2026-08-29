import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  classifyRemediationJob,
  createRemediationStartCoordinator,
  pollRemediationJob,
} from '../../src/utils/remediationJob.ts';

const scansApiSource = readFileSync(new URL('../../src/api/scans.ts', import.meta.url), 'utf8');

function status(statusValue, errorCode = null) {
  return {
    job_id: 'job-1',
    scan_id: 'scan-1',
    status: statusValue,
    status_url: '/education/remediation/jobs/job-1',
    progress: 0,
    progress_message: null,
    created_at: '2026-08-28T00:00:00Z',
    updated_at: '2026-08-28T00:00:00Z',
    started_at: null,
    completed_at: null,
    error_code: errorCode,
    fixed_count: null,
    manual_count: null,
    failed_count: null,
    skipped_count: null,
    remaining_count: null,
    total_issues: null,
    original_score: null,
    remediated_score: null,
    improvement: null,
    artifact_id: null,
    download_available: false,
    download_url: null,
  };
}

describe('durable remediation API contract', () => {
  it('uses asynchronous start, relative status, latest, and artifact endpoints', () => {
    assert.match(scansApiSource, /startRemediationJob/);
    assert.match(scansApiSource, /Prefer: 'respond-async'/);
    assert.match(scansApiSource, /headers: \{ Prefer: 'respond-async' \}, signal/);
    assert.match(scansApiSource, /getRemediationJobStatus/);
    assert.match(scansApiSource, /\/education\/scans\/\$\{encodeURIComponent\(scanId\)\}\/remediation\/latest/);
    assert.match(scansApiSource, /downloadRemediationJob/);
    assert.match(scansApiSource, /responseType: 'blob'/);
  });
});

describe('createRemediationStartCoordinator', () => {
  it('blocks a deferred start continuation after navigation changes the scan', async () => {
    const coordinator = createRemediationStartCoordinator();
    coordinator.activate('scan-1');
    const attempt = coordinator.begin('scan-1');
    let resolveStart;
    const deferredStart = new Promise((resolve) => {
      resolveStart = resolve;
    });
    let monitored = false;

    const continuation = deferredStart.then(() => {
      if (coordinator.isCurrent(attempt)) monitored = true;
    });
    coordinator.activate('scan-2');
    resolveStart();
    await continuation;

    assert.equal(attempt.signal.aborted, true);
    assert.equal(monitored, false);
  });
});

describe('classifyRemediationJob', () => {
  it('maps every durable server outcome without inferring issue-level results', () => {
    assert.equal(classifyRemediationJob(status('pending')), 'queued');
    assert.equal(classifyRemediationJob(status('processing')), 'running');
    assert.equal(classifyRemediationJob(status('completed')), 'completed');
    assert.equal(classifyRemediationJob(status('failed', 'manual_required')), 'partial');
    assert.equal(classifyRemediationJob(status('failed', 'job_execution_timeout')), 'timed_out');
    assert.equal(classifyRemediationJob(status('failed', 'remediation_failed')), 'failed');
  });
});

describe('pollRemediationJob', () => {
  it('polls a long-running job with capped exponential backoff until completion', async () => {
    const responses = [
      status('pending'),
      status('processing'),
      status('processing'),
      status('processing'),
      status('completed'),
    ];
    const delays = [];
    const updates = [];
    let clock = 0;

    const result = await pollRemediationJob(
      async () => responses.shift(),
      {
        timeoutMs: 10_000,
        initialDelayMs: 100,
        maxDelayMs: 250,
        backoffMultiplier: 2,
        now: () => clock,
        wait: async (delayMs) => {
          delays.push(delayMs);
          clock += delayMs;
        },
        onUpdate: (_job, stateValue) => updates.push(stateValue),
      }
    );

    assert.deepEqual(delays, [100, 200, 250, 250]);
    assert.deepEqual(updates, ['queued', 'running', 'running', 'running', 'completed']);
    assert.equal(result.outcome, 'terminal');
    assert.equal(result.state, 'completed');
  });

  it('reports a client deadline without changing the last known server state', async () => {
    const delays = [];
    let clock = 0;
    let requests = 0;

    const result = await pollRemediationJob(
      async () => {
        requests += 1;
        return status('processing');
      },
      {
        timeoutMs: 250,
        initialDelayMs: 100,
        maxDelayMs: 200,
        backoffMultiplier: 2,
        now: () => clock,
        wait: async (delayMs) => {
          delays.push(delayMs);
          clock += delayMs;
        },
      }
    );

    assert.deepEqual(delays, [100, 150]);
    assert.equal(requests, 2);
    assert.deepEqual(result, {
      outcome: 'client_timeout',
      state: 'client_timeout',
      job: status('processing'),
    });
  });

  it('stops before making a request when already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    let requests = 0;

    await assert.rejects(
      pollRemediationJob(
        async () => {
          requests += 1;
          return status('completed');
        },
        { signal: controller.signal }
      ),
      { name: 'AbortError' }
    );
    assert.equal(requests, 0);
  });

  it('aborts an in-flight status request at the hard elapsed deadline', async () => {
    const result = await pollRemediationJob(
      (signal) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          const error = new Error('request aborted');
          error.name = 'CanceledError';
          error.code = 'ERR_CANCELED';
          reject(error);
        }, { once: true });
      }),
      { timeoutMs: 20 }
    );

    assert.deepEqual(result, {
      outcome: 'client_timeout',
      state: 'client_timeout',
      job: null,
    });
  });

  it('ignores an abort-ignoring terminal response that resolves after the deadline', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    let resolveRequest;
    let requestSignal;
    const updates = [];
    const polling = pollRemediationJob(
      (signal) => {
        requestSignal = signal;
        return new Promise((resolve) => {
          resolveRequest = resolve;
        });
      },
      {
        timeoutMs: 25,
        onUpdate: (_job, stateValue) => updates.push(stateValue),
      }
    );

    t.mock.timers.tick(25);
    assert.deepEqual(await polling, {
      outcome: 'client_timeout',
      state: 'client_timeout',
      job: null,
    });
    assert.equal(requestSignal.aborted, true);

    resolveRequest(status('completed'));
    await Promise.resolve();
    assert.deepEqual(updates, []);
  });

  it('returns at the hard deadline when the status request never settles', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    let requestSignal;
    const polling = pollRemediationJob(
      (signal) => {
        requestSignal = signal;
        return new Promise(() => {});
      },
      { timeoutMs: 50 }
    );

    t.mock.timers.tick(50);
    assert.deepEqual(await polling, {
      outcome: 'client_timeout',
      state: 'client_timeout',
      job: null,
    });
    assert.equal(requestSignal.aborted, true);
  });

  it('keeps caller abort distinct when the status request ignores cancellation', async () => {
    const controller = new AbortController();
    const polling = pollRemediationJob(
      () => new Promise(() => {}),
      { signal: controller.signal, timeoutMs: 1_000 }
    );

    controller.abort();
    await assert.rejects(polling, { name: 'AbortError' });
  });

  it('normalizes an Axios-shaped caller cancellation to AbortError', async () => {
    const controller = new AbortController();
    const polling = pollRemediationJob(
      (signal) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          const error = new Error('request canceled');
          error.name = 'CanceledError';
          error.code = 'ERR_CANCELED';
          reject(error);
        }, { once: true });
      }),
      { signal: controller.signal, timeoutMs: 1_000 }
    );

    controller.abort();
    await assert.rejects(polling, { name: 'AbortError' });
  });
});
