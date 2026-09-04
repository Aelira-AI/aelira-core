import assert from 'node:assert/strict';
import test from 'node:test';

import {
  getDeferralLifecycle,
  getReviewQueueStatus,
  isAttentionRequired,
  REVIEW_QUEUE_STATUSES,
  summarizeReviewFixes,
} from '../../src/utils/reviewState.ts';

test('review queue exposes only API-supported document states', () => {
  assert.deepEqual(REVIEW_QUEUE_STATUSES, ['pending', 'approved', 'rejected']);
});

test('summary counts derive from the canonical fix list', () => {
  const summary = summarizeReviewFixes([
    { review_status: 'pending' },
    { review_status: 'approved' },
    { review_status: 'edited' },
    { review_status: 'auto_approved' },
    { review_status: 'rejected' },
  ]);

  assert.deepEqual(summary, {
    total_fixes: 5,
    needs_review_count: 1,
    auto_approved_count: 1,
    reviewed_count: 3,
  });
});

test('unknown states remain pending instead of becoming approved', () => {
  assert.deepEqual(summarizeReviewFixes([{ review_status: 'legacy_unknown' }]), {
    total_fixes: 1,
    needs_review_count: 1,
    auto_approved_count: 0,
    reviewed_count: 0,
  });
});

test('document status gives unresolved work precedence over rejection', () => {
  assert.equal(
    getReviewQueueStatus([
      { review_status: 'rejected' },
      { review_status: 'pending' },
    ]),
    'pending',
  );
  assert.equal(
    getReviewQueueStatus([
      { review_status: 'rejected' },
      { review_status: 'approved' },
    ]),
    'rejected',
  );
  assert.equal(getReviewQueueStatus([]), 'approved');
});

test('deferral lifecycle filters distinguish every reportable state', () => {
  assert.equal(getDeferralLifecycle({ lifecycle: 'active' }), 'active');
  assert.equal(getDeferralLifecycle({ lifecycle: 'expired' }), 'expired');
  assert.equal(getDeferralLifecycle({ lifecycle: 'revoked' }), 'revoked');
  assert.equal(getDeferralLifecycle({ lifecycle: 'resolved' }), 'resolved');
  assert.equal(getDeferralLifecycle(null), null);
});

test('attention returns at expiry but not during an active deferral', () => {
  assert.equal(
    isAttentionRequired({ review_status: 'pending', deferral: { lifecycle: 'active' } }),
    false,
  );
  assert.equal(
    isAttentionRequired({ review_status: 'pending', deferral: { lifecycle: 'expired' } }),
    true,
  );
  assert.equal(
    isAttentionRequired({ review_status: 'approved', deferral: { lifecycle: 'resolved' } }),
    false,
  );
});
