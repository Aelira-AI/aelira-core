import React from 'react';
import { Card } from './Card';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StatCardProps {
  /** Mono-label eyebrow shown above the value */
  label: string;
  /** Primary metric value */
  value: string | number;
  /** Optional secondary line below the value */
  sublabel?: React.ReactNode;
  /**
   * `default` — hairline Card with mono label + large value.
   * `accent`  — indigo-filled card (for the ADA deadline counter, etc.).
   */
  variant?: 'default' | 'accent';
  /**
   * Optional delta indicator (e.g. "+12 this month").
   * Rendered below value in default variant only.
   */
  delta?: string | number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const LABEL_BASE =
  'font-mono uppercase text-xs tracking-wider text-[var(--content-tertiary)]';

const VALUE_BASE = 'text-3xl font-bold leading-tight';

const SUBLABEL_BASE = 'text-sm mt-1';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * StatCard — Clarity Design System (dashboard)
 *
 * `default` variant: hairline Card + mono eyebrow + large value.
 * `accent` variant: indigo-filled surface for high-prominence stats.
 *
 * No raw hex colors — semantic tokens only.
 */
export function StatCard({
  label,
  value,
  sublabel,
  variant = 'default',
  delta,
  className = '',
}: StatCardProps): React.ReactElement {
  if (variant === 'accent') {
    return (
      <div
        className={[
          'rounded-[16px] p-6 flex flex-col bg-[var(--surface-accent-strong)] text-[var(--interactive-primary-fg)]',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {/* Solid interactive-primary-fg (white in both themes) — opacity dimming
            dropped because it failed AA on the lighter dark-mode accent surface;
            hierarchy comes from size/weight/mono-case. */}
        <span className="font-mono uppercase text-xs tracking-wider mb-2 text-[var(--interactive-primary-fg)]">
          {label}
        </span>
        <span className={[VALUE_BASE, 'mb-1 text-[var(--interactive-primary-fg)]'].join(' ')}>
          {value}
        </span>
        {sublabel && (
          <span className={[SUBLABEL_BASE, 'text-[var(--interactive-primary-fg)]'].join(' ')}>
            {sublabel}
          </span>
        )}
      </div>
    );
  }

  // default variant
  return (
    <Card className={['flex flex-col', className].filter(Boolean).join(' ')}>
      <span className={[LABEL_BASE, 'mb-2'].join(' ')}>{label}</span>
      <span className={[VALUE_BASE, 'text-[var(--content-primary)]'].join(' ')}>
        {value}
      </span>
      {delta !== undefined && (
        <span className="text-xs text-[var(--content-secondary)] mt-0.5">
          {delta}
        </span>
      )}
      {sublabel && (
        <span className={[SUBLABEL_BASE, 'text-[var(--content-tertiary)]'].join(' ')}>
          {sublabel}
        </span>
      )}
    </Card>
  );
}
