/**
 * Unit tests for summarizeBatchOutcome — truthful batch-action result
 * summaries. Zero-succeeded must never come back as 'success'.
 * Uses Node.js native test runner (same pattern as featureAccess.test.js).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { summarizeBatchOutcome } from '../../src/utils/batchActionResult.ts';

describe('summarizeBatchOutcome', () => {
  it('is a pure success when everything succeeded with no skips/errors', () => {
    const result = summarizeBatchOutcome({
      verb: 'Approved',
      succeededCount: 3,
      buckets: [],
      errors: [],
    });
    assert.equal(result.status, 'success');
    assert.equal(result.message, 'Approved 3 items.');
  });

  it('uses singular wording for a count of exactly 1', () => {
    const result = summarizeBatchOutcome({
      verb: 'Approved',
      succeededCount: 1,
      buckets: [],
      errors: [],
    });
    assert.equal(result.message, 'Approved 1 item.');
  });

  it('is the reported incident, verbatim: 0 approved must never be success', () => {
    const result = summarizeBatchOutcome({
      verb: 'Approved',
      succeededCount: 0,
      buckets: [{ label: 'skipped', count: 4 }],
      errors: [
        'cf-1: no remediated content',
        'cf-2: no remediated content',
        'cf-3: no remediated content',
        'cf-4: no remediated content',
      ],
    });
    assert.equal(result.status, 'zero');
    assert.notEqual(result.status, 'success');
    assert.equal(
      result.message,
      'Approved 0 · 4 skipped (no remediated version yet — remediate them first).'
    );
  });

  it('is mixed when some succeeded and some were skipped with a known reason', () => {
    const result = summarizeBatchOutcome({
      verb: 'Approved',
      succeededCount: 3,
      buckets: [{ label: 'skipped', count: 4 }],
      errors: [
        'cf-1: no remediated content',
        'cf-2: no remediated content',
        'cf-3: no remediated content',
        'cf-4: no remediated content',
      ],
    });
    assert.equal(result.status, 'mixed');
    assert.equal(
      result.message,
      'Approved 3 · 4 skipped (no remediated version yet — remediate them first).'
    );
  });

  it('maps the "content is stale" reason for batch-writeback', () => {
    const result = summarizeBatchOutcome({
      verb: 'Wrote back',
      succeededCount: 2,
      buckets: [
        { label: 'stale', count: 1 },
        { label: 'failed', count: 0 },
      ],
      errors: ['cf-9: content is stale'],
    });
    assert.equal(result.status, 'mixed');
    assert.equal(
      result.message,
      'Wrote back 2 · 1 stale (content changed in Canvas since scan — rescan first).'
    );
  });

  it('omits zero-valued buckets from the message', () => {
    const result = summarizeBatchOutcome({
      verb: 'Wrote back',
      succeededCount: 2,
      buckets: [
        { label: 'stale', count: 0 },
        { label: 'failed', count: 0 },
      ],
      errors: [],
    });
    assert.equal(result.status, 'success');
    assert.ok(!result.message.includes('stale'));
    assert.ok(!result.message.includes('failed'));
  });

  it('surfaces an unrecognized server error reason as-is', () => {
    const result = summarizeBatchOutcome({
      verb: 'Wrote back',
      succeededCount: 0,
      buckets: [{ label: 'failed', count: 1 }],
      errors: ['cf-7: Canvas API returned 500'],
    });
    assert.equal(result.status, 'zero');
    assert.equal(
      result.message,
      'Wrote back 0 · 1 failed (Canvas API returned 500).'
    );
  });

  it('handles a whole-batch error with no per-item id prefix', () => {
    const result = summarizeBatchOutcome({
      verb: 'Wrote back',
      succeededCount: 0,
      buckets: [],
      errors: ['No approved items found for this course'],
    });
    assert.equal(result.status, 'zero');
    assert.equal(
      result.message,
      'Wrote back 0 (No approved items found for this course).'
    );
  });

  it('dedupes identical reasons instead of repeating them', () => {
    const result = summarizeBatchOutcome({
      verb: 'Approved',
      succeededCount: 0,
      buckets: [{ label: 'skipped', count: 3 }],
      errors: [
        'cf-1: no remediated content',
        'cf-2: no remediated content',
        'cf-3: no remediated content',
      ],
    });
    // Should not repeat the same reason 3 times.
    const occurrences = result.message.split('no remediated version yet').length - 1;
    assert.equal(occurrences, 1);
  });

  it('joins distinct unknown reasons and truncates a very long combined message', () => {
    const longReasons = Array.from({ length: 10 }, (_, i) => `cf-${i}: unique failure reason number ${i} with extra padding text`);
    const result = summarizeBatchOutcome({
      verb: 'Wrote back',
      succeededCount: 0,
      buckets: [{ label: 'failed', count: 10 }],
      errors: longReasons,
    });
    assert.equal(result.status, 'zero');
    assert.ok(result.message.includes('...'));
    assert.ok(result.message.length < 220);
  });
});
