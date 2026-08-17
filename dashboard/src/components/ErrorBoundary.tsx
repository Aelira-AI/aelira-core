import React, { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  scope?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

// ============================================================================
// Chart-Specific Error Fallback
// ============================================================================

export function ChartErrorFallback(): React.ReactElement {
  return (
    <div
      role="alert"
      aria-live="polite"
      className="flex flex-col items-center justify-center h-64 p-6 rounded-lg bg-[var(--surface-secondary)] border border-[var(--border-primary)]"
    >
      <AlertTriangle
        className="w-10 h-10 text-[var(--feature-warning-content)] mb-3"
        aria-hidden="true"
      />
      <p className="text-sm font-medium text-[var(--content-primary)] mb-1">
        Unable to load chart
      </p>
      <p className="text-xs text-[var(--content-tertiary)] text-center">
        The chart data could not be displayed. Please try refreshing the page.
      </p>
    </div>
  );
}

// ============================================================================
// Main ErrorBoundary Component
// ============================================================================

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log error for debugging
    console.error(`ErrorBoundary caught error in ${this.props.scope || 'unknown scope'}:`, error, errorInfo);

    // Call optional onError callback (e.g., to report to an error-tracking service)
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  handleGoHome = (): void => {
    // Inside an LMS iframe the main dashboard refuses to be framed, so
    // navigating in place turns an error into a blank "refused to
    // connect". Escaping to a new tab is the pattern the LTI views
    // already use for the same reason.
    if (window.top !== window.self) {
      window.open('/dashboard', '_blank', 'noopener,noreferrer');
      return;
    }
    window.location.href = '/dashboard';
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // Default accessible fallback UI
      return (
        <div
          role="alert"
          aria-live="assertive"
          className="flex flex-col items-center justify-center min-h-[400px] p-8 m-4 rounded-xl bg-[var(--surface-secondary)] border border-[var(--border-primary)]"
        >
          <AlertTriangle
            className="w-16 h-16 text-[var(--feature-danger-content)] mb-4"
            aria-hidden="true"
          />

          <h2 className="text-xl font-semibold text-[var(--content-primary)] mb-2">
            Something went wrong
          </h2>

          <p className="text-sm text-[var(--content-secondary)] text-center mb-6 max-w-md">
            {this.props.scope
              ? `An error occurred in the ${this.props.scope} section.`
              : 'An unexpected error occurred.'}
            {' '}Please try again or return to the dashboard.
          </p>

          {/* Error details for debugging (only in development) */}
          {import.meta.env.DEV && this.state.error && (
            <details className="mb-6 p-4 rounded-lg bg-[var(--surface-tertiary)] max-w-lg w-full">
              <summary className="text-sm font-medium text-[var(--content-primary)] cursor-pointer">
                Error details
              </summary>
              <pre className="mt-2 text-xs text-[var(--content-tertiary)] overflow-auto whitespace-pre-wrap">
                {this.state.error.message}
                {'\n\n'}
                {this.state.error.stack}
              </pre>
            </details>
          )}

          <div className="flex gap-3">
            <button
              onClick={this.handleRetry}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-[var(--interactive-primary-bg)] text-white hover:bg-[var(--interactive-primary-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--content-accent)] focus-visible:ring-offset-2"
            >
              <RefreshCw className="w-4 h-4" aria-hidden="true" />
              Try again
            </button>

            <button
              onClick={this.handleGoHome}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-[var(--border-primary)] text-[var(--content-primary)] hover:bg-[var(--surface-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--content-accent)] focus-visible:ring-offset-2"
            >
              <Home className="w-4 h-4" aria-hidden="true" />
              Go to Dashboard
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
