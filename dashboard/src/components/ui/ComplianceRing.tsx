import React from 'react';
import { bandForScore } from './scoreBand';
import type { ScoreBand } from './scoreBand';

// ---------------------------------------------------------------------------
// Band → stroke token
// Reuses bandForScore from ScoreChip — single source of band thresholds.
// ---------------------------------------------------------------------------

const BAND_STROKE: Record<ScoreBand, string> = {
  success: 'var(--content-success)',
  warning: 'var(--content-warning)',
  danger:  'var(--content-error)',
  neutral: 'var(--border-secondary)',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ComplianceRingProps {
  /** Compliance score 0–100 */
  score: number;
  /** Outer SVG dimension in px (defaults to 120) */
  size?: number;
  /** Stroke width in px (defaults to 10) */
  strokeWidth?: number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * ComplianceRing — Clarity Design System (dashboard)
 *
 * SVG stroke-dasharray ring, color-banded by score via `bandForScore()`.
 * Track: `var(--surface-tertiary)` — Fill ring: semantic content token.
 *
 * Ring math extracted from ComplianceScore.tsx (~lines 88-131).
 * No raw hex colors — semantic tokens only.
 *
 * Usage:
 *   <ComplianceRing score={78} size={120} />
 */
export function ComplianceRing({
  score,
  size = 120,
  strokeWidth = 10,
  className,
}: ComplianceRingProps): React.ReactElement {
  const radius = (size - strokeWidth) / 2 - 1;
  const circumference = radius * 2 * Math.PI;
  const strokeDashoffset = circumference - (Math.min(score, 100) / 100) * circumference;

  const band = bandForScore(score);
  const stroke = BAND_STROKE[band];

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      className={['block shrink-0 transform -rotate-90', className].filter(Boolean).join(' ')}
      aria-hidden="true"
    >
      {/* Track */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--surface-tertiary)"
        strokeWidth={strokeWidth}
      />
      {/* Progress arc */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={strokeDashoffset}
        className="transition-all duration-1000 ease-out"
      />
    </svg>
  );
}
