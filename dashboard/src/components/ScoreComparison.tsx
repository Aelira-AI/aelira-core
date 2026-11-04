import React from 'react';
import { displayScore, freshScoreComparison, remediationScore } from '../utils/remediationScore';
import type { RemediationScoreJob } from '../utils/remediationScore';

export function ScoreComparison({ job, scan }: {
  job: RemediationScoreJob;
  scan: { compliance_score?: number | null; result?: { compliance_score?: number | null } };
}): React.ReactElement {
  const comparison = remediationScore(job, scan);
  const fresh = freshScoreComparison(job);
  if (fresh && comparison.reasonCode === 'baseline_mismatch') {
    return (
      <div className="space-y-4">
        <h3 className="font-semibold text-primary">Current rule-based comparison</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-[var(--border-primary)] p-4">
            <p className="text-sm text-tertiary">Source document</p>
            <p className="mt-2 text-xl font-semibold text-primary">{displayScore(fresh.source)}</p>
          </div>
          <div className="rounded-lg border border-[var(--border-primary)] p-4">
            <p className="text-sm text-tertiary">Improved working file</p>
            <p className="mt-2 text-xl font-semibold text-primary">{displayScore(fresh.output)}</p>
          </div>
        </div>
        <p className="text-sm text-secondary">
          Measured change: {fresh.deltaLabel}. Both files were checked with the same rules.
          AI description assessments do not affect these scores. Review the changes and remaining findings before use.
        </p>
        <p className="text-sm text-tertiary">
          Earlier recorded scan: {displayScore(comparison.before)}. This result is retained;
          it differs from the current source measurement and is not used to calculate this change.
        </p>
        <p className="text-sm text-secondary">Automated scores do not establish accessibility conformance.</p>
      </div>
    );
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Original score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(comparison.before)}</p>
        {comparison.originalCapped && (
          <p className="mt-1 text-xs font-medium text-[var(--feature-warning-content)]">Held at 49/100 critical cap</p>
        )}
      </div>
      <div className="rounded-lg border border-[var(--border-primary)] p-4">
        <p className="text-sm text-tertiary">Remediated score</p>
        <p className="mt-2 text-xl font-semibold text-primary">{displayScore(comparison.after)}</p>
        {comparison.remediatedCapped && (
          <p className="mt-1 text-xs font-medium text-[var(--feature-warning-content)]">Held at 49/100 critical cap</p>
        )}
      </div>
      <p className="text-sm text-secondary sm:col-span-2">Measured score change: {comparison.deltaLabel}. {comparison.description}</p>
      {comparison.remediatedCapped && comparison.criticalFindings.length > 0 && (
        <div className="rounded-lg border border-[var(--border-warning)] bg-[var(--bg-warning-subtle)] p-3 sm:col-span-2">
          <p className="text-sm font-semibold text-primary">Critical findings holding score at 49/100:</p>
          <ul className="mt-1 list-inside list-disc text-sm text-secondary">
            {comparison.criticalFindings.map((finding, idx) => (
              <li key={idx}>{finding}</li>
            ))}
          </ul>
        </div>
      )}
      {comparison.reasonCode && (
        <p className="text-sm text-tertiary sm:col-span-2">Verification reason: <code>{comparison.reasonCode}</code></p>
      )}
    </div>
  );
}
