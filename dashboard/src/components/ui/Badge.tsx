import React from 'react';

export interface BadgeProps {
  variant?: 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
  children: React.ReactNode;
  className?: string;
}

const VARIANTS: Record<NonNullable<BadgeProps['variant']>, string> = {
  neutral:
    'text-[var(--content-secondary)] bg-[var(--surface-secondary)]',
  accent:
    'text-[var(--content-accent)] bg-[var(--surface-accent-subtle)]',
  success:
    'text-[var(--content-success)] bg-[var(--surface-success-subtle)]',
  warning:
    'text-[var(--content-warning)] bg-[var(--surface-warning-subtle)]',
  danger:
    'text-[var(--content-error)] bg-[var(--surface-error-subtle)]',
};

const BASE =
  'inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono tracking-wide';

/**
 * Badge — Clarity Design System (dashboard)
 *
 * Semantic variants using CSS tokens on soft surfaces.
 * Pill radius (rounded-full), mono label sizing.
 * Replaces hardcoded rainbow colors (text-green-400, bg-purple-500/10, etc.).
 */
export function Badge({
  variant = 'neutral',
  children,
  className = '',
}: BadgeProps): React.ReactElement {
  const classes = [BASE, VARIANTS[variant], className].filter(Boolean).join(' ');
  return <span className={classes}>{children}</span>;
}
