/**
 * Turns a batch action's raw counts + server error strings into a truthful,
 * human-readable result — never plain "success" styling when nothing (or
 * fewer than everything) actually succeeded.
 *
 * Written after a real incident: batch-approve returned
 * {approved_count: 0, skipped_count: 4, errors: [...]} and the client
 * showed a green success toast reading "Approved 0 items." — technically
 * true, entirely misleading. The client had ignored skipped_count/errors
 * and always rendered success styling regardless of what happened.
 *
 * Pure function — no I/O, no React. Both CanvasContentPage and
 * LTICourseView call this for both batch-approve and batch-writeback so
 * the two views' notion of "did this actually work" can't drift.
 */

export type BatchResultStatus = 'success' | 'mixed' | 'zero';

export interface BatchResultBucket {
  /** e.g. "skipped", "stale", "failed" */
  label: string;
  count: number;
}

export interface BatchOutcome {
  /** Verb for the headline, e.g. "Approved", "Wrote back". */
  verb: string;
  succeededCount: number;
  /** Named failure/skip buckets. Zero-valued buckets are omitted from the message. */
  buckets: BatchResultBucket[];
  /** Raw error strings from the server, one per failed/skipped item (or a
   * single whole-batch error like "No approved items found for this course"). */
  errors: string[];
}

export interface BatchResultSummary {
  status: BatchResultStatus;
  message: string;
}

/** Known server error suffixes mapped to a friendlier, actionable phrase.
 * Matched against the error text AFTER stripping a leading "{id}: " prefix
 * (per-item errors are formatted that way server-side; whole-batch errors
 * have no such prefix and are matched as-is). */
const KNOWN_REASONS: { pattern: RegExp; friendly: string }[] = [
  {
    pattern: /no remediated content/i,
    friendly: 'no remediated version yet — remediate them first',
  },
  {
    pattern: /content is stale/i,
    friendly: 'content changed in Canvas since scan — rescan first',
  },
];

const MAX_REASON_LENGTH = 140;

function stripIdPrefix(error: string): string {
  const idx = error.indexOf(': ');
  return idx !== -1 ? error.slice(idx + 2) : error;
}

function friendlyReason(errors: string[]): string | null {
  if (errors.length === 0) return null;

  const reasons = errors.map(stripIdPrefix).map((reason) => {
    const known = KNOWN_REASONS.find((k) => k.pattern.test(reason));
    return known ? known.friendly : reason;
  });

  const distinct = Array.from(new Set(reasons));
  const joined = distinct.join('; ');
  return joined.length > MAX_REASON_LENGTH
    ? `${joined.slice(0, MAX_REASON_LENGTH - 3)}...`
    : joined;
}

/**
 * Summarize a batch action's outcome. Only ever returns status 'success'
 * when something succeeded AND nothing was skipped/failed/errored —
 * zero succeeded is always 'zero', partial success is always 'mixed'.
 */
export function summarizeBatchOutcome(outcome: BatchOutcome): BatchResultSummary {
  const { verb, succeededCount, buckets, errors } = outcome;
  const nonZeroBuckets = buckets.filter((b) => b.count > 0);

  if (succeededCount > 0 && nonZeroBuckets.length === 0 && errors.length === 0) {
    return {
      status: 'success',
      message: `${verb} ${succeededCount} item${succeededCount !== 1 ? 's' : ''}.`,
    };
  }

  const parts = [
    `${verb} ${succeededCount}`,
    ...nonZeroBuckets.map((b) => `${b.count} ${b.label}`),
  ];
  const reason = friendlyReason(errors);
  let message = parts.join(' · ');
  if (reason) message += ` (${reason})`;
  message += '.';

  return {
    status: succeededCount > 0 ? 'mixed' : 'zero',
    message,
  };
}
