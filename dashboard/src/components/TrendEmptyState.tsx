import React from 'react';

/**
 * Empty state for compliance trend and projection surfaces.
 *
 * When a workspace upgraded to v0.10.0, trend tracking restarted: snapshots
 * recorded before the upgrade counted unassessed items as 0% and are withheld
 * from every chart and projection. If the API reports such withheld history,
 * this component explains the restart; otherwise it falls back to the neutral
 * insufficient-data message. Plain text keeps it screen-reader reachable and
 * it never relies on color alone to carry the meaning.
 */

export const TREND_RESTART_EXPLANATION =
  'Trend tracking restarted in v0.10.0 with a corrected method. Snapshots saved before the upgrade counted unassessed items as 0%, so they are not shown here. Predictions return after 7 days of new data.';

export const TREND_NEUTRAL_MESSAGE =
  'Not enough data yet. Upload more scans to see progress.';

interface TrendEmptyStateProps {
  hasWithheldHistory: boolean;
}

export function TrendEmptyState({
  hasWithheldHistory,
}: TrendEmptyStateProps): React.ReactElement {
  return (
    <p className="text-sm text-center text-[var(--content-secondary)] max-w-md">
      {hasWithheldHistory ? TREND_RESTART_EXPLANATION : TREND_NEUTRAL_MESSAGE}
    </p>
  );
}
