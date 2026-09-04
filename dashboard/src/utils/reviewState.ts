export const REVIEW_QUEUE_STATUSES = ['pending', 'approved', 'rejected'] as const;

export type ReviewQueueStatus = (typeof REVIEW_QUEUE_STATUSES)[number];

interface ReviewFixState {
  review_status?: string | null;
  deferral?: ReviewDeferralState | null;
}

export interface ReviewDeferralState {
  lifecycle?: 'active' | 'expired' | 'revoked' | 'resolved' | null;
}

export interface ReviewSummary {
  total_fixes: number;
  needs_review_count: number;
  auto_approved_count: number;
  reviewed_count: number;
}

const TERMINAL_STATUSES = new Set([
  'approved',
  'edited',
  'auto_approved',
  'rejected',
]);
const HUMAN_REVIEWED_STATUSES = new Set(['approved', 'edited', 'rejected']);

export function isPendingReviewStatus(status?: string | null): boolean {
  return !TERMINAL_STATUSES.has(status ?? '');
}

export function isHumanReviewedStatus(status?: string | null): boolean {
  return HUMAN_REVIEWED_STATUSES.has(status ?? '');
}

export function getDeferralLifecycle(
  deferral?: ReviewDeferralState | null,
): ReviewDeferralState['lifecycle'] {
  return deferral?.lifecycle ?? null;
}

export function isAttentionRequired(fix: ReviewFixState): boolean {
  if (!isPendingReviewStatus(fix.review_status)) {
    return false;
  }
  return getDeferralLifecycle(fix.deferral) !== 'active';
}

export function getReviewQueueStatus(fixes: ReviewFixState[]): ReviewQueueStatus {
  if (fixes.some((fix) => isPendingReviewStatus(fix.review_status))) {
    return 'pending';
  }
  if (fixes.some((fix) => fix.review_status === 'rejected')) {
    return 'rejected';
  }
  return 'approved';
}

export function summarizeReviewFixes(fixes: ReviewFixState[]): ReviewSummary {
  return fixes.reduce<ReviewSummary>(
    (summary, fix) => {
      const status = fix.review_status;
      summary.total_fixes += 1;
      if (isAttentionRequired(fix)) {
        summary.needs_review_count += 1;
      }
      if (status === 'auto_approved') {
        summary.auto_approved_count += 1;
      }
      if (isHumanReviewedStatus(status)) {
        summary.reviewed_count += 1;
      }
      return summary;
    },
    {
      total_fixes: 0,
      needs_review_count: 0,
      auto_approved_count: 0,
      reviewed_count: 0,
    },
  );
}
