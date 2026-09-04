import React from 'react';
import { AlertCircle, Clock3, Eye, Loader } from 'lucide-react';

export type VisualAnalysisStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'retryable_failure'
  | 'terminal_failure'
  | 'review_required';

export interface VisualAnalysisSummary {
  id: string;
  source_kind: 'image' | 'chart';
  source_locator: Record<string, string | number> | null;
  purpose: 'alt_text' | 'chart_description' | 'image_type' | 'alt_text_validation' | 'audio_description';
  status: VisualAnalysisStatus;
  attempt_count: number;
  max_attempts: number;
  failure_category: string | null;
  proposal: Record<string, unknown> | null;
  proposal_sha256: string | null;
  review_fix_id: string | null;
  review_status: string | null;
}

interface VisualAnalysisStatusPanelProps {
  analyses: VisualAnalysisSummary[];
}

const STATUS_LABELS: Record<VisualAnalysisStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Analysis succeeded',
  retryable_failure: 'Retry available',
  terminal_failure: 'Analysis failed',
  review_required: 'Proposal ready for review',
};

function proposalText(proposal: Record<string, unknown> | null): string | null {
  if (!proposal) return null;
  for (const key of ['alt_text', 'short_description', 'detailed_description', 'description', 'recommended_alt']) {
    const value = proposal[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

function locatorText(locator: Record<string, string | number> | null): string {
  if (!locator) return 'Source location unavailable';
  if (locator.kind === 'page_image') return `Page ${locator.page_number}, image ${locator.image_xref}`;
  if (locator.kind === 'slide_shape') return `Slide ${locator.slide_number}, shape ${locator.shape_id}`;
  if (locator.kind === 'media_frame') return `Frame ${Number(locator.timestamp_ms) / 1000}s`;
  return 'Source location unavailable';
}

function failureText(category: string | null): string | null {
  if (!category) return null;
  return category.split('_').join(' ');
}

export function VisualAnalysisStatusPanel({ analyses }: VisualAnalysisStatusPanelProps): React.ReactElement | null {
  if (analyses.length === 0) return null;

  return (
    <section className="border-b border-[var(--border-primary)] p-4" aria-labelledby="visual-analysis-heading">
      <div className="mb-3">
        <h2 id="visual-analysis-heading" className="text-sm font-semibold text-primary">Visual analysis</h2>
        <p className="mt-1 text-xs text-tertiary">
          Machine output is a proposal until the linked fix is accepted in review.
        </p>
      </div>
      <ul className="space-y-2">
        {analyses.map((analysis) => {
          const text = proposalText(analysis.proposal);
          const failure = failureText(analysis.failure_category);
          const active = analysis.status === 'queued' || analysis.status === 'running';
          const failed = analysis.status === 'retryable_failure' || analysis.status === 'terminal_failure';
          return (
            <li key={analysis.id} className="rounded-lg border border-[var(--border-primary)] bg-[var(--surface-secondary)] p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {active ? (
                  analysis.status === 'running'
                    ? <Loader className="h-4 w-4 animate-spin text-[var(--feature-info-content)]" aria-hidden="true" />
                    : <Clock3 className="h-4 w-4 text-[var(--content-tertiary)]" aria-hidden="true" />
                ) : failed ? (
                  <AlertCircle className="h-4 w-4 text-[var(--feature-danger-content)]" aria-hidden="true" />
                ) : (
                  <Eye className="h-4 w-4 text-[var(--feature-warning-content)]" aria-hidden="true" />
                )}
                <span className="font-medium text-primary">{STATUS_LABELS[analysis.status]}</span>
                <span className="text-tertiary">{analysis.source_kind}</span>
                <span className="text-tertiary">{locatorText(analysis.source_locator)}</span>
                <span className="text-tertiary">Attempt {analysis.attempt_count} of {analysis.max_attempts}</span>
              </div>
              {failure && <p className="mt-2 text-xs text-[var(--feature-danger-content)]">Failure category: {failure}</p>}
              {text && (
                <div className="mt-2 rounded bg-[var(--surface-tertiary)] p-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-tertiary">Machine proposal — not approved</p>
                  <p className="mt-1 text-sm text-primary">{text}</p>
                  {analysis.review_status && <p className="mt-1 text-xs text-tertiary">Review status: {analysis.review_status}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
