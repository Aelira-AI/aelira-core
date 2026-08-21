export interface RemediateAllAccounting {
  total: number;
  attempted: number;
  skipped: number;
}

export function remediateAllAccounting(
  visibleCount: number,
  attemptedCount: number
): RemediateAllAccounting {
  const total = Math.max(0, visibleCount);
  const attempted = Math.min(total, Math.max(0, attemptedCount));
  return { total, attempted, skipped: total - attempted };
}
