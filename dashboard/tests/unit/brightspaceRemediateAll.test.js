import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { remediateAllInChunks } from '../../src/utils/brightspaceRemediateAll.ts';

function responseFor(ids) {
  return {
    status: 'completed',
    requested_count: ids.length,
    completed_count: ids.length,
    manual_count: ids.length,
    failed_count: 0,
    fixed_count: ids.length * 2,
    results: ids.map((id) => ({
      cloud_file_id: id,
      status: 'completed',
      fixed_count: 2,
      manual_count: 1,
      failed_count: 0,
    })),
  };
}

describe('remediateAllInChunks', () => {
  it('processes every eligible id sequentially in batches no larger than 20', async () => {
    const ids = Array.from({ length: 45 }, (_, index) => `cf-${index}`);
    const calls = [];
    let active = 0;
    let maximumActive = 0;

    const summary = await remediateAllInChunks(ids, async (chunk) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      calls.push([...chunk]);
      await Promise.resolve();
      active -= 1;
      return responseFor(chunk);
    });

    assert.deepEqual(calls.map((chunk) => chunk.length), [20, 20, 5]);
    assert.deepEqual(calls.flat(), ids);
    assert.equal(maximumActive, 1);
    assert.equal(summary.requestedCount, 45);
    assert.equal(summary.processedCount, 45);
    assert.equal(summary.completedCount, 45);
    assert.equal(summary.fixedCount, 90);
    assert.equal(summary.manualCount, 45);
    assert.equal(summary.failedCount, 0);
    assert.deepEqual(summary.chunkFailures, []);
  });

  it('continues after a failed chunk without inventing item outcomes', async () => {
    const ids = Array.from({ length: 45 }, (_, index) => `cf-${index}`);
    let call = 0;

    const summary = await remediateAllInChunks(ids, async (chunk) => {
      call += 1;
      if (call === 2) throw new Error('gateway timeout');
      return responseFor(chunk);
    });

    assert.equal(call, 3);
    assert.equal(summary.requestedCount, 45);
    assert.equal(summary.processedCount, 25);
    assert.equal(summary.completedCount, 25);
    assert.equal(summary.failedCount, 0);
    assert.equal(summary.unreportedCount, 20);
    assert.deepEqual(summary.chunkFailures, [
      { chunkNumber: 2, requestedCount: 20, message: 'gateway timeout' },
    ]);
  });

  it('does not count duplicate or foreign server outcomes as processed', async () => {
    const summary = await remediateAllInChunks(['cf-1', 'cf-2'], async () => ({
      ...responseFor([]),
      requested_count: 2,
      results: [
        ...responseFor(['cf-1']).results,
        ...responseFor(['cf-1']).results,
        ...responseFor(['foreign']).results,
      ],
    }));

    assert.equal(summary.requestedCount, 2);
    assert.equal(summary.processedCount, 1);
    assert.equal(summary.completedCount, 1);
    assert.equal(summary.fixedCount, 2);
    assert.equal(summary.unreportedCount, 1);
  });
});
