import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isUnscoredAltReview } from '../../src/utils/aiReviewFindings.ts';

test('only the exact AI alt-quality marker separates an unscored review', () => {
  const review = { issue_type: 'ai_alt_quality_review', assessment_type: 'ai_alt_quality_review',
    review_only: true, scoring_included: false };
  assert.equal(isUnscoredAltReview(review), true);
  for (const field of Object.keys(review)) {
    const incomplete = { ...review };
    delete incomplete[field];
    assert.equal(isUnscoredAltReview(incomplete), false);
  }
  assert.equal(isUnscoredAltReview({ ...review, scoring_included: true }), false);
  assert.equal(isUnscoredAltReview({ ...review, issue_type: 'missing_alt_text' }), false);
  assert.equal(isUnscoredAltReview({ review_only: true }), false);
});
