import React from 'react';
import { AlertTriangle } from 'lucide-react';

export interface PageErrorProps {
  /** Page-level heading (rendered as the page's h1). */
  title: string;
  /** What went wrong, in words the reader can act on. */
  message: string;
  /** Retries the failed request. Defaults to reloading the page. */
  onRetry?: () => void;
  retryLabel?: string;
  /** Extra link or button shown beside the retry action. */
  secondaryAction?: React.ReactNode;
}

/**
 * Full-page failure state: names the page (so the view still has a heading),
 * announces the problem as an alert, and offers a way forward instead of
 * ending on bare error text.
 */
export function PageError({
  title,
  message,
  onRetry,
  retryLabel = 'Try again',
  secondaryAction,
}: PageErrorProps): React.ReactElement {
  return (
    <div className="p-4 sm:p-8">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-3xl font-bold text-primary mb-4">{title}</h1>
        <div
          className="rounded-lg p-4 bg-[var(--surface-error-subtle)] border border-[var(--content-error)] text-[var(--content-error)] flex items-start gap-3"
          role="alert"
        >
          <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
          <p className="text-sm min-w-0 break-words">{message}</p>
        </div>
        <div className="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={onRetry ?? (() => window.location.reload())}
            className="btn-primary"
          >
            {retryLabel}
          </button>
          {secondaryAction}
        </div>
      </div>
    </div>
  );
}
