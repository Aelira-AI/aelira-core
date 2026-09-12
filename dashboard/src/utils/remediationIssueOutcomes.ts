import type { RemediationFixSummary } from '../api/scans';

export interface RemediationIssueLike {
  id?: string;
  description?: string;
  message?: string;
  title?: string;
  category?: string;
  severity?: string;
  rule?: string;
  location?: string | null;
  page_number?: number | null;
}

export interface RemediationIssueRow {
  issue: RemediationIssueLike;
  fix?: RemediationFixSummary;
  outcomeSource: 'persisted_fix' | 'aggregate_manual' | 'unreported';
}

export interface RemediationOutcomeCounts {
  total_issues?: number | null;
  fixed_count?: number | null;
  remaining_count?: number | null;
  manual_count?: number | null;
  failed_count?: number | null;
  skipped_count?: number | null;
}

export function issueDescription(issue: RemediationIssueLike): string {
  return issue.description || issue.message || issue.title || 'Accessibility issue';
}

function issueSignature(issue: RemediationIssueLike): string {
  return JSON.stringify([
    issueDescription(issue).trim(),
    issue.location?.trim() || '',
    issue.page_number ?? null,
  ]);
}

export function pairIssuesWithFixes(
  issues: RemediationIssueLike[],
  fixes: RemediationFixSummary[],
  counts?: RemediationOutcomeCounts,
): RemediationIssueRow[] {
  const fixesBySignature = new Map<string, RemediationFixSummary[]>();
  for (const fix of fixes) {
    const signature = issueSignature(fix);
    const matches = fixesBySignature.get(signature) || [];
    matches.push(fix);
    fixesBySignature.set(signature, matches);
  }

  const rows = issues.map((issue): RemediationIssueRow => {
    const matches = fixesBySignature.get(issueSignature(issue));
    const fix = matches?.shift();
    return { issue, fix, outcomeSource: fix ? 'persisted_fix' : 'unreported' };
  });

  for (const matches of fixesBySignature.values()) {
    for (const fix of matches) {
      rows.push({ issue: fix, fix, outcomeSource: 'persisted_fix' });
    }
  }

  const unmatchedRows = rows.filter((row) => row.outcomeSource === 'unreported');
  const manualAttributionIsProven = Boolean(
    counts
      && counts.total_issues === issues.length
      && counts.fixed_count === fixes.length
      && counts.manual_count === unmatchedRows.length
      && counts.remaining_count === unmatchedRows.length
      && counts.failed_count === 0
      && counts.skipped_count === 0,
  );
  if (manualAttributionIsProven) {
    for (const row of unmatchedRows) row.outcomeSource = 'aggregate_manual';
  }
  return rows;
}

export function outcomePresentation(
  fix?: RemediationFixSummary,
  outcomeSource: RemediationIssueRow['outcomeSource'] = fix ? 'persisted_fix' : 'unreported',
): { label: string; className: string } {
  if (outcomeSource === 'aggregate_manual') {
    return {
      label: 'Manual remediation required',
      className: 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]',
    };
  }
  if (!fix) {
    return {
      label: 'Outcome not reported',
      className: 'bg-[var(--surface-tertiary)] text-tertiary',
    };
  }
  if (fix.review_status === 'apply_failed') {
    return {
      label: 'Apply failed',
      className: 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]',
    };
  }
  if (fix.review_status === 'rejected') {
    return {
      label: 'Rejected in review',
      className: 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]',
    };
  }
  if (fix.needs_review && ['pending', 'in_review'].includes(fix.review_status)) {
    return {
      label: 'Fix proposed · review required',
      className: 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]',
    };
  }
  if (['auto_approved', 'applied'].includes(fix.review_status)) {
    return {
      label: 'Change applied · verification not reported',
      className: 'bg-[var(--surface-tertiary)] text-tertiary',
    };
  }
  if (fix.review_status === 'approved') {
    return {
      label: 'Approved for remediation',
      className: 'bg-[var(--feature-info-surface)] text-[var(--feature-info-content)]',
    };
  }
  if (fix.review_status === 'edited') {
    return {
      label: 'Edited and approved',
      className: 'bg-[var(--feature-info-surface)] text-[var(--feature-info-content)]',
    };
  }
  return {
    label: 'Fix recorded · status unknown',
    className: 'bg-[var(--surface-tertiary)] text-tertiary',
  };
}
