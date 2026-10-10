import React from "react";
import { FileText } from "lucide-react";
import type { RemediationJobStatus } from "../../api/scans";
import { issueDescription, findingDisplayText, outcomeExplanation, outcomePresentation, outcomeGroup, reviewRequirement } from "../../utils/remediationIssueOutcomes";
import type { RemediationIssueRow } from "../../utils/remediationIssueOutcomes";

export function AggregateResults({ job, rows }: { job: RemediationJobStatus; rows?: RemediationIssueRow[] }): React.ReactElement | null {
  const values = [
    { label: 'Changes applied', value: job.fixed_count, color: 'text-[var(--feature-success-content)]' },
    { label: 'Human review changes', value: rows?.filter(row => outcomeGroup(row) === 'review').length, color: 'text-[var(--feature-warning-content)]' },
    { label: 'Manual work remaining', value: job.manual_count, color: 'text-[var(--feature-warning-content)]' },
    { label: 'Remaining', value: job.remaining_count, color: 'text-[var(--feature-warning-content)]' },
    { label: 'Total issues', value: job.total_issues, color: 'text-primary' },
    { label: 'Failed', value: job.failed_count, color: 'text-[var(--feature-danger-content)]' },
    { label: 'Skipped', value: job.skipped_count, color: 'text-[var(--content-secondary)]' },
    { label: 'Withheld changes', value: job.withheld_count, color: 'text-[var(--content-secondary)]' },
    { label: 'Outcome not reported', value: job.outcome_unreported_count, color: 'text-[var(--content-secondary)]' },
  ].filter((item): item is { label: string; value: number; color: string } =>
    typeof item.value === 'number' && Number.isFinite(item.value)
  );

  if (values.length === 0) return null;

  return (
    <div className={`grid gap-3 sm:grid-cols-2 ${values.length > 2 ? 'lg:grid-cols-3' : ''}`}>
      {values.map((item) => (
        <div key={item.label} className="rounded-lg border border-[var(--border-primary)] p-4 text-center">
          <p className={`text-2xl font-bold ${item.color}`}>{item.value}</p>
          <p className="mt-1 text-sm text-tertiary">{item.label}</p>
        </div>
      ))}
    </div>
  );
}

export function RecordedIssueRow(row: RemediationIssueRow): React.ReactElement {
  const { issue, fix, outcomeSource, recordedOutcome, recordedDetails } = row;
  const outcome = outcomePresentation(fix, outcomeSource, recordedOutcome, recordedDetails);
  const explanation = outcomeExplanation(row);
  const review = reviewRequirement(row);
  return (
    <div className="flex flex-col items-start justify-between gap-3 border-b border-[var(--border-primary)] p-3 last:border-b-0 sm:flex-row sm:gap-4">
      <div className="flex min-w-0 items-start gap-3">
        <div className="rounded bg-[var(--surface-tertiary)] p-1.5">
          <FileText className="h-4 w-4 text-[var(--content-secondary)]" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium leading-5 text-primary">
            {findingDisplayText(issueDescription(issue))}
          </p>
          <p className="mt-1 text-xs leading-4 text-tertiary">
            {issue.category || issue.severity || issue.rule || 'Recorded scan finding'}
            {issue.page_number != null && ` · Page ${issue.page_number}`}
          </p>
          {explanation && (
            <dl className="mt-3 space-y-2 text-sm leading-5 text-secondary">
              <div><dt className="font-semibold text-primary">Why</dt><dd>{explanation.reason}</dd></div>
              <div><dt className="font-semibold text-primary">What was attempted</dt><dd>{explanation.attempt}</dd></div>
              <div><dt className="font-semibold text-primary">Next step</dt><dd>{explanation.nextStep}</dd></div>
            </dl>
          )}
        </div>
      </div>
      <div className="flex max-w-64 shrink-0 flex-col items-start gap-2 sm:items-end">
      <span className={`rounded px-2 py-1 text-xs leading-4 sm:text-right ${outcome.className}`}>
        {outcome.label}
      </span>
      {review && <span className="rounded bg-[var(--feature-warning-surface)] px-2 py-1 text-xs leading-4 text-[var(--feature-warning-content)] sm:text-right">{review}</span>}
      </div>
    </div>
  );
}

const OUTCOME_GROUPS = [
  { key: 'review', title: 'Changes requiring human review', description: 'Compare these changes with the source. Approve, edit or reject them in Review changes.' },
  { key: 'manual', title: 'Manual work remaining', description: 'These findings still need correction. You can work from the improved file where a download is available.' },
  { key: 'applied', title: 'Other applied changes', description: 'Application, automated checks and publication approval are separate. Check each recorded result before use.' },
  { key: 'other', title: 'Withheld, failed or unreported outcomes', description: 'These records do not establish that a usable change was delivered.' },
] as const;

export function RecordedOutcomeGroups({ rows }: { rows: RemediationIssueRow[] }): React.ReactElement {
  const groups = OUTCOME_GROUPS.map(group => ({ ...group, rows: rows.filter(row => outcomeGroup(row) === group.key) })).filter(group => group.rows.length > 0);
  return <>
    <nav className="mb-5 flex flex-wrap gap-3" aria-label="Jump to remediation outcomes">
      {groups.map(group => <a key={group.key} className="btn-secondary text-sm" href={`#outcomes-${group.key}`}>{group.title} ({group.rows.length})</a>)}
    </nav>
    <div className="space-y-6">
      {groups.map(group => <section key={group.key} aria-labelledby={`outcomes-${group.key}`}>
        <h3 id={`outcomes-${group.key}`} className="scroll-mt-24 text-base font-semibold text-primary">{group.title} ({group.rows.length})</h3>
        <p className="mb-3 mt-1 text-sm text-secondary">{group.description}</p>
        <div className="rounded-lg border border-[var(--border-primary)]">
          {group.rows.map((row, index) => <RecordedIssueRow key={row.fix?.id || `${issueDescription(row.issue)}-${index}`} {...row} />)}
        </div>
      </section>)}
      {rows.length === 0 && <p className="py-8 text-center text-secondary">No recorded issues are available for this scan.</p>}
    </div>
  </>;
}
