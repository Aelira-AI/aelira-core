import React from 'react';

// ---------------------------------------------------------------------------
// Tailwind class map — fill color per tone (no inline backgroundColor)
// ---------------------------------------------------------------------------

const TONE_CLASS: Record<NonNullable<ProgressBarProps['tone']>, string> = {
  accent:  'bg-(--accent)',
  success: 'bg-(--content-success)',
  warning: 'bg-(--content-warning)',
  danger:  'bg-(--content-error)',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ProgressBarProps {
  /** Current value */
  value: number;
  /** Maximum value (defaults to 100) */
  max?: number;
  /** Color tone — defaults to 'accent' (indigo) */
  tone?: 'accent' | 'success' | 'warning' | 'danger';
  /** Accessible label for the progressbar role */
  'aria-label'?: string;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * ProgressBar — Clarity Design System (dashboard)
 *
 * Track: `var(--surface-tertiary)`, fill: semantic token per tone.
 * No raw hex colors. Smooth CSS transition on width change.
 */
export function ProgressBar({
  value,
  max = 100,
  tone = 'accent',
  'aria-label': ariaLabel,
  className = '',
}: ProgressBarProps): React.ReactElement {
  const percentage = max > 0 ? Math.min((value / max) * 100, 100) : 0;

  return (
    <div
      className={['h-1.5 rounded-full overflow-hidden bg-(--surface-tertiary)', className].filter(Boolean).join(' ')}
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={ariaLabel}
    >
      <div
        className={['h-full rounded-full transition-all duration-300', TONE_CLASS[tone]].join(' ')}
        style={{ width: `${percentage}%` }}
      />
    </div>
  );
}
