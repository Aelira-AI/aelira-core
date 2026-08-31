import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  aggregateBrightspaceRemediationBatch,
  resolveBoundedBrightspaceRemediationBatch,
  resolveBrightspaceRemediationJob,
} from '../../src/utils/brightspaceRemediation.ts';

const brightspaceApiSource = readFileSync(
  new URL('../../src/api/brightspaceContent.ts', import.meta.url),
  'utf8'
);

function queued(id = 'job-1') {
  return {
    job_id: id,
    cloud_file_id: `cloud-${id}`,
    status: 'pending',
    status_url: `/brightspace/content/cloud-${id}/remediation/jobs/${id}`,
  };
}

function terminal(overrides = {}) {
  return {
    ...queued(),
    status: 'completed',
    outcome_status: 'completed',
    fixed_count: 2,
    manual_count: 0,
    failed_count: 0,
    skipped_count: 1,
    download_available: true,
    ai_used: true,
    external_ai_used: true,
    providers: ['gemini'],
    purpose_decisions: { remediation: 'used' },
    error_code: null,
    ...overrides,
  };
}

function immediatePolling(getStatus, overrides = {}) {
  let clock = 0;
  return {
    getStatus,
    now: () => clock,
    wait: async (delayMs) => {
      clock += delayMs;
    },
    initialDelayMs: 1,
    timeoutMs: 20,
    ...overrides,
  };
}

describe('Brightspace queued remediation journeys', () => {
  it('returns server-authored success, provider, purpose, and no-op outcomes intact', async () => {
    const completed = await resolveBrightspaceRemediationJob(
      queued(),
      immediatePolling(async () => terminal())
    );
    assert.deepEqual(completed, {
      cloud_file_id: 'cloud-job-1',
      status: 'completed',
      fixed_count: 2,
      manual_count: 0,
      failed_count: 0,
      skipped_count: 1,
      has_remediated_version: true,
      ai_used: true,
      external_ai_used: true,
      providers: ['gemini'],
      purpose_decisions: { remediation: 'used' },
      error_code: null,
    });

    const noOp = await resolveBrightspaceRemediationJob(
      terminal({
        outcome_status: 'no_op',
        fixed_count: 0,
        skipped_count: 3,
        download_available: false,
        ai_used: false,
        external_ai_used: false,
        providers: [],
        purpose_decisions: { remediation: 'not_needed' },
      })
    );
    assert.equal(noOp.status, 'no_op');
    assert.deepEqual(noOp.purpose_decisions, { remediation: 'not_needed' });
  });

  it('preserves manual-review and failed terminal outcomes', async () => {
    const manual = await resolveBrightspaceRemediationJob(
      queued(),
      immediatePolling(async () => terminal({
        status: 'failed',
        outcome_status: 'manual_required',
        manual_count: 1,
        download_available: false,
        error_code: 'manual_required',
      }))
    );
    assert.equal(manual.status, 'manual_required');
    assert.equal(manual.manual_count, 1);

    const failed = await resolveBrightspaceRemediationJob(
      queued(),
      immediatePolling(async () => terminal({
        status: 'failed',
        outcome_status: 'failed',
        fixed_count: 0,
        failed_count: 1,
        download_available: false,
        error_code: 'remediation_failed',
      }))
    );
    assert.equal(failed.status, 'failed');
    assert.equal(failed.error_code, 'remediation_failed');
  });

  it('preserves unknown AI and provider usage without inventing false or empty values', async () => {
    const result = await resolveBrightspaceRemediationJob(terminal({
      ai_used: undefined,
      external_ai_used: undefined,
      providers: undefined,
      purpose_decisions: undefined,
    }));

    assert.equal(result.ai_used, null);
    assert.equal(result.external_ai_used, null);
    assert.equal(result.providers, null);
    assert.equal(result.purpose_decisions, null);
  });

  it('ends a journey at the client timeout while leaving the worker state alone', async () => {
    await assert.rejects(
      resolveBrightspaceRemediationJob(
        queued(),
        immediatePolling(async () => queued(), { timeoutMs: 2 })
      ),
      /timed out before the worker finished/
    );
  });

  it('propagates caller abort and stops polling', async () => {
    const controller = new AbortController();
    let requests = 0;
    const polling = resolveBrightspaceRemediationJob(queued(), {
      signal: controller.signal,
      timeoutMs: 1_000,
      getStatus: (_url, signal) => new Promise((_resolve, reject) => {
        requests += 1;
        signal.addEventListener('abort', () => {
          const error = new Error('request canceled');
          error.name = 'CanceledError';
          reject(error);
        }, { once: true });
      }),
    });

    controller.abort();
    await assert.rejects(polling, { name: 'AbortError' });
    assert.equal(requests, 1);
  });

  it('bounds parallel batch polling and preserves input ordering', async () => {
    let active = 0;
    let peak = 0;
    const jobs = Array.from({ length: 7 }, (_, index) => queued(`job-${index}`));
    const results = await resolveBoundedBrightspaceRemediationBatch(jobs, {
      maxConcurrentJobs: 2,
      getStatus: async (statusUrl) => {
        active += 1;
        peak = Math.max(peak, active);
        await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
        active -= 1;
        const id = statusUrl.split('/').at(-1);
        return terminal({
          job_id: id,
          cloud_file_id: `cloud-${id}`,
          status_url: statusUrl,
        });
      },
      initialDelayMs: 1,
    });

    assert.equal(peak, 2);
    assert.deepEqual(
      results.map((result) => result.cloud_file_id),
      jobs.map((job) => job.cloud_file_id)
    );
  });

  it('aborts and settles sibling pollers before a failed batch rejects', async () => {
    const jobs = Array.from({ length: 5 }, (_, index) => queued(`job-${index}`));
    const requests = [];
    let active = 0;
    let peak = 0;
    let releaseFirst;
    const bothStarted = new Promise((resolve) => {
      releaseFirst = resolve;
    });

    await assert.rejects(
      resolveBoundedBrightspaceRemediationBatch(jobs, {
        maxConcurrentJobs: 2,
        getStatus: async (statusUrl, signal) => {
          const id = statusUrl.split('/').at(-1);
          requests.push(id);
          active += 1;
          peak = Math.max(peak, active);
          if (requests.length === 2) releaseFirst();
          if (id === 'job-0') {
            await bothStarted;
            active -= 1;
            throw new Error('terminal status request failed');
          }
          return new Promise((_resolve, reject) => {
            const rejectAborted = () => {
              active -= 1;
              const error = new Error('request canceled');
              error.name = 'CanceledError';
              reject(error);
            };
            if (signal.aborted) rejectAborted();
            else signal.addEventListener('abort', rejectAborted, { once: true });
          });
        },
      }),
      /terminal status request failed/
    );

    assert.equal(peak, 2);
    assert.equal(active, 0);
    assert.deepEqual(requests, ['job-0', 'job-1']);
  });

  it('starts no batch status requests when the owner already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    let requests = 0;

    await assert.rejects(
      resolveBoundedBrightspaceRemediationBatch([queued()], {
        signal: controller.signal,
        getStatus: async () => {
          requests += 1;
          return terminal();
        },
      }),
      { name: 'AbortError' }
    );
    assert.equal(requests, 0);
  });

  it('aggregates manual issue counts instead of counting manual jobs', () => {
    const results = [
      terminal({ cloud_file_id: 'cloud-1', manual_count: 2 }),
      terminal({
        cloud_file_id: 'cloud-2',
        status: 'failed',
        outcome_status: 'manual_required',
        manual_count: 3,
      }),
    ].map((job) => ({
      cloud_file_id: job.cloud_file_id,
      status: job.outcome_status,
      fixed_count: job.fixed_count,
      manual_count: job.manual_count,
      failed_count: job.failed_count,
      skipped_count: job.skipped_count,
      has_remediated_version: job.download_available,
      ai_used: job.ai_used,
      external_ai_used: job.external_ai_used,
      providers: job.providers,
      purpose_decisions: job.purpose_decisions,
      error_code: job.error_code,
    }));

    const aggregate = aggregateBrightspaceRemediationBatch(2, results);
    assert.equal(aggregate.manual_count, 5);
    assert.equal(aggregate.completed_count, 1);
    assert.match(
      brightspaceApiSource,
      /return aggregateBrightspaceRemediationBatch\(queued\.requested_count, results\)/
    );
  });

});
