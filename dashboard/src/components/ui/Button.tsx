import React from 'react';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'destructive';
  size?: 'sm' | 'md' | 'lg';
  leftIcon?: React.ReactNode;
  children?: React.ReactNode;
}

const BASE =
  'inline-flex items-center justify-center font-semibold rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--content-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface-primary)] disabled:pointer-events-none disabled:opacity-50';

const VARIANTS: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary:
    'bg-[var(--interactive-primary-bg)] text-[var(--interactive-primary-fg)] hover:bg-[var(--interactive-primary-hover)]',
  secondary:
    'bg-transparent border border-[var(--interactive-secondary-border)] text-[var(--interactive-secondary-fg)] hover:bg-[var(--interactive-secondary-hover-bg)]',
  destructive:
    'bg-transparent border border-[var(--content-error)] text-[var(--content-error)] hover:bg-[var(--feature-danger-surface)]',
};

const SIZES: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'h-8 px-3 py-1.5 text-sm gap-1.5',
  md: 'h-10 px-5 py-2 text-sm gap-2',
  lg: 'h-12 px-6 py-3 text-base gap-2',
};

/**
 * Button — Clarity Design System (dashboard)
 *
 * Pill radius (rounded-full), token-driven colors, no shadow.
 * Mirrors the website Button's variant structure for cross-app consistency.
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      leftIcon,
      children,
      className = '',
      ...props
    },
    ref
  ) => {
    const classes = [BASE, VARIANTS[variant], SIZES[size], className]
      .filter(Boolean)
      .join(' ');

    return (
      <button ref={ref} className={classes} {...props}>
        {leftIcon && <span className="shrink-0">{leftIcon}</span>}
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';
