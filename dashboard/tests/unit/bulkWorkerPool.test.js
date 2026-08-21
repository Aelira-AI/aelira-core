import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createBulkWorkerPool, summarizeBulkPoolResult } from '../../src/hooks/bulkWorkerPool.ts';

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('createBulkWorkerPool', () => {
  it('drains ten items without exceeding concurrency three', async () => {
    let active = 0;
    let maximum = 0;
    const processed = [];
    const pool = createBulkWorkerPool({
      items: Array.from({ length: 10 }, (_, index) => index),
      concurrency: 3,
      process: async (item) => {
        active += 1;
        maximum = Math.max(maximum, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        processed.push(item);
        active -= 1;
        return item * 2;
      },
    });

    const result = await pool.run();

    assert.equal(maximum, 3);
    assert.deepEqual(processed.toSorted((a, b) => a - b), Array.from({ length: 10 }, (_, index) => index));
    assert.equal(result.settled.length, 10);
    assert.equal(result.unstarted.length, 0);
    assert.equal(result.stopped, false);
  });

  it('drains one thousand items through a bounded pool', async () => {
    let active = 0;
    let maximum = 0;
    const pool = createBulkWorkerPool({
      items: Array.from({ length: 1000 }, (_, index) => index),
      concurrency: 7,
      process: async (item) => {
        active += 1;
        maximum = Math.max(maximum, active);
        await Promise.resolve();
        active -= 1;
        return item;
      },
    });

    const result = await pool.run();

    assert.equal(maximum, 7);
    assert.equal(result.settled.length, 1000);
    assert.equal(result.unstarted.length, 0);
  });

  it('pause prevents new starts and resume drains the queue', async () => {
    const firstWave = deferred();
    const started = [];
    const pool = createBulkWorkerPool({
      items: [0, 1, 2, 3, 4],
      concurrency: 2,
      process: async (item) => {
        started.push(item);
        if (item < 2) await firstWave.promise;
        return item;
      },
    });

    const completion = pool.run();
    await Promise.resolve();
    assert.deepEqual(started, [0, 1]);

    pool.pause();
    firstWave.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(started, [0, 1]);

    pool.resume();
    const result = await completion;
    assert.deepEqual(started, [0, 1, 2, 3, 4]);
    assert.equal(result.settled.length, 5);
  });

  it('stop lets active work settle and reports untouched items as unstarted', async () => {
    const activeWork = deferred();
    const started = [];
    const items = [0, 1, 2, 3, 4];
    const pool = createBulkWorkerPool({
      items,
      concurrency: 2,
      process: async (item) => {
        started.push(item);
        await activeWork.promise;
        return item;
      },
    });

    const completion = pool.run();
    await Promise.resolve();
    pool.stop();
    activeWork.resolve();
    const result = await completion;

    assert.deepEqual(started, [0, 1]);
    assert.equal(result.settled.length, 2);
    assert.deepEqual(result.unstarted, [2, 3, 4]);
    assert.equal(result.stopped, true);
  });

  it('records fulfilled and rejected items from actual settled work', async () => {
    const pool = createBulkWorkerPool({
      items: ['ok-1', 'bad', 'ok-2'],
      concurrency: 2,
      process: async (item) => {
        if (item === 'bad') throw new Error('upload failed');
        return item.toUpperCase();
      },
    });

    const result = await pool.run();
    const summary = summarizeBulkPoolResult(result);

    assert.equal(summary.succeeded, 2);
    assert.equal(summary.failed, 1);
    assert.equal(summary.unstarted, 0);
    assert.deepEqual(
      result.settled.filter((entry) => entry.status === 'rejected').map((entry) => entry.item),
      ['bad'],
    );
  });

  it('returns an accurate zero-item result without starting workers', async () => {
    let calls = 0;
    const pool = createBulkWorkerPool({
      items: [],
      concurrency: 3,
      process: async () => {
        calls += 1;
      },
    });

    const result = await pool.run();

    assert.equal(calls, 0);
    assert.deepEqual(summarizeBulkPoolResult(result), {
      succeeded: 0,
      failed: 0,
      unstarted: 0,
      total: 0,
      stopped: false,
    });
  });

  it('rejects invalid concurrency instead of silently running unbounded', () => {
    assert.throws(
      () => createBulkWorkerPool({ items: [1], concurrency: 0, process: async () => 1 }),
      /positive integer/i,
    );
  });
});
