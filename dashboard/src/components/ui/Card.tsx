import React from 'react';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  children?: React.ReactNode;
  className?: string;
}

const CARD_BASE =
  'bg-[var(--surface-primary)] border border-[var(--border-primary)] rounded-[16px] p-6';

/**
 * Card — Clarity Design System (dashboard)
 *
 * Hairline border, 16px radius, surface-primary background.
 * No shadow, no glassmorphism, no backdrop-filter.
 */
export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ children, className = '', ...props }, ref) => {
    const classes = [CARD_BASE, className].filter(Boolean).join(' ');
    return (
      <div ref={ref} className={classes} {...props}>
        {children}
      </div>
    );
  }
);
Card.displayName = 'Card';

export interface CardHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  children?: React.ReactNode;
  className?: string;
}

export const CardHeader = React.forwardRef<HTMLDivElement, CardHeaderProps>(
  ({ children, className = '', ...props }, ref) => {
    const classes = ['mb-4', className].filter(Boolean).join(' ');
    return (
      <div ref={ref} className={classes} {...props}>
        {children}
      </div>
    );
  }
);
CardHeader.displayName = 'CardHeader';

export interface CardTitleProps extends React.HTMLAttributes<HTMLHeadingElement> {
  children?: React.ReactNode;
  className?: string;
}

export const CardTitle = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  ({ children, className = '', ...props }, ref) => {
    const classes = [
      'text-base font-semibold text-[var(--content-primary)] leading-snug',
      className,
    ]
      .filter(Boolean)
      .join(' ');
    return (
      <h3 ref={ref} className={classes} {...props}>
        {children}
      </h3>
    );
  }
);
CardTitle.displayName = 'CardTitle';

export interface CardBodyProps extends React.HTMLAttributes<HTMLDivElement> {
  children?: React.ReactNode;
  className?: string;
}

export const CardBody = React.forwardRef<HTMLDivElement, CardBodyProps>(
  ({ children, className = '', ...props }, ref) => {
    const classes = ['text-sm text-[var(--content-secondary)]', className]
      .filter(Boolean)
      .join(' ');
    return (
      <div ref={ref} className={classes} {...props}>
        {children}
      </div>
    );
  }
);
CardBody.displayName = 'CardBody';
