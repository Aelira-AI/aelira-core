import React from 'react';

// ---------------------------------------------------------------------------
// Band helper — single source of truth for score thresholds across the app.
// Thresholds match Dashboard.tsx getScoreBgColor / ComplianceScore.tsx getScoreColor:
//   ≥90 → success, 70–89 → warning, <70 → danger, null → neutral
// ---------------------------------------------------------------------------

export type ScoreBand = 'success' | 'warning' | 'danger' | 'neutral';

export function bandForScore(score: number | null): ScoreBand {
  if (score === null) return 'neutral';
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'danger';
}

// ---------------------------------------------------------------------------
// Token map — soft surface + matching content token per band
// ---------------------------------------------------------------------------

const BAND_CLASSES: Record<ScoreBand, string> = {
  neutral: 'text-[var(--content-secondary)] bg-[var(--surface-secondary)]',
  success: 'text-[var(--content-success)] bg-[var(--surface-success-subtle)]',
  warning: 'text-[var(--content-warning)] bg-[var(--surface-warning-subtle)]',
  danger:  'text-[var(--content-error)] bg-[var(--surface-error-subtle)]',
};

const BASE =
  'inline-flex items-center justify-center px-2.5 py-1 rounded-full text-xs font-semibold font-mono tabular-nums';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface ScoreChipProps {
  score: number | null;
  className?: string;
}

/**
 * ScoreChip — Clarity Design System (dashboard)
 *
 * Renders a compliance score as a banded pill:
 *   null   → neutral "···"
 *   ≥ 90   → success band
 *   70–89  → warning band
 *   < 70   → danger band
 *
 * Uses semantic CSS tokens on soft surfaces; mono tabular numerals.
 * Reuse `bandForScore` wherever band logic is needed (ComplianceRing, etc.).
 */
export function ScoreChip({ score, className = '' }: ScoreChipProps): React.ReactElement {
  const band = bandForScore(score);
  const classes = [BASE, BAND_CLASSES[band], className].filter(Boolean).join(' ');
  const label = score === null ? '···' : `${Math.round(score)}/100`;

  return <span className={classes}>{label}</span>;
}
