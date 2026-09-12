import React from 'react';
import { displayScore, remediationScore } from '../utils/remediationScore';
import type { RemediationScoreJob } from '../utils/remediationScore';

export function ScoreComparison({ job, scan }: {
  job: RemediationScoreJob;
  scan: { compliance_score?: number | null; result?: { compliance_score?: number | null } };
}): React.ReactElement {
  const comparison = remediationScore(job, scan);
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Original score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(comparison.before)}</p>
      </div>
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Remediated score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(comparison.after)}</p>
      </div>
      <p className="text-sm text-secondary sm:col-span-2">Measured score change: {comparison.deltaLabel}. {comparison.description}</p>
      {comparison.reasonCode && (
        <p className="text-sm text-tertiary sm:col-span-2">Verification reason: <code>{comparison.reasonCode}</code></p>
      )}
    </div>
  );
}
