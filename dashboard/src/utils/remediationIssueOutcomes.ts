import type { RecordedRemediationOutcome, RemediationFixSummary } from '../api/scans';

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
  suggested_fix?: string;
  fix_suggestion?: string;
}

export interface RemediationIssueRow {
  issue: RemediationIssueLike;
  fix?: RemediationFixSummary;
  outcomeSource: 'persisted_fix' | 'aggregate_manual' | 'unreported' | 'recorded_job';
  recordedOutcome?: RecordedRemediationOutcome['status'];
  recordedDetails?: RecordedRemediationOutcome;
}

export interface RemediationOutcomeCounts {
  total_issues?: number | null;
  fixed_count?: number | null;
  remaining_count?: number | null;
  manual_count?: number | null;
  failed_count?: number | null;
  skipped_count?: number | null;
  issue_outcomes?: RecordedRemediationOutcome[] | null;
}

export function issueDescription(issue: RemediationIssueLike): string {
  return issue.description || issue.message || issue.title || 'Accessibility issue';
}

// Display compatibility only. Keep original text for persisted-fix attribution.
export function findingDisplayText(text: string): string {
  return text === 'Image missing alternative text - AI analysis pending'
    ? 'Image missing alternative text' : text;
}

export function outcomeExplanation(row: RemediationIssueRow): {
  reason: string; nextStep: string; attempt: string;
} | null {
  const details = row.recordedDetails;
  if (row.outcomeSource === 'recorded_job' && details?.reason && details.next_step) {
    const attempts = {
      not_recorded: 'Attempt details were not recorded.',
      not_attempted: 'This change was not attempted.',
      not_applied: 'No usable change was applied.',
      candidate_change: 'A change was attempted in a candidate file.',
      delivered_change: 'A change was delivered.',
    };
    return {
      reason: details.reason,
      nextStep: details.next_step,
      attempt: details.attempt ? attempts[details.attempt] || attempts.not_recorded : attempts.not_recorded,
    };
  }
  if (row.recordedOutcome === 'manual' || row.outcomeSource === 'aggregate_manual') {
    return {
      reason: 'This older job recorded a manual outcome but did not save its detailed reason.',
      nextStep: row.issue.suggested_fix || row.issue.fix_suggestion || 'Review and correct this finding in the source document, then rescan.',
      attempt: 'Attempt details were not recorded.',
    };
  }
  if (row.recordedOutcome === 'withheld') {
    return {
      reason: 'A change was recorded in a candidate, but no resulting file was delivered. This older job did not save the specific withholding reason.',
      nextStep: 'Review the job status and unresolved findings before retrying remediation.',
      attempt: 'A candidate change was recorded; it was not delivered.',
    };
  }
  if (row.recordedOutcome === 'failed') {
    return {
      reason: 'The job recorded a failed attempt without a detailed public reason.',
      nextStep: 'Review the scan guidance and retry after correcting the blocking condition.',
      attempt: 'A failed attempt was recorded.',
    };
  }
  return null;
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

  // The job's source index identifies the original finding, not a guessed text match.
  // Reject duplicate indices and conflicting identities rather than invent attribution.
  if (Array.isArray(counts?.issue_outcomes)) {
    const recorded = counts.issue_outcomes;
    for (const [index, issue] of issues.entries()) {
      rows[index].outcomeSource = 'unreported';
      const idIsUnique = typeof issue.id === 'string' && issues.filter((source) => source.id === issue.id).length === 1;
      const identityMatches = idIsUnique ? recorded.filter((item) => item.issue_id === issue.id) : [];
      const matches = identityMatches.length > 0 ? identityMatches : recorded.filter((item) => {
        if (item.source_index_scope === 'original_scan') {
          return counts.total_issues === issues.length && item.source_index === index;
        }
        // The API emits this projection only after binding the exact selected
        // finding to the original scan and current saved-file receipt.
        return item.source_index_scope === 'approved_subset'
          && item.status === 'fixed'
          && item.verification_scope === 'saved_file_finding'
          && item.verification_passed === true
          && Number.isInteger(item.original_source_index)
          && item.original_source_index === index
          && recorded.filter(record => record.original_source_index === index).length === 1;
      });
      if (matches.length !== 1) continue;
      const item = matches[0];
      const provenSubsetIndex = item.source_index_scope === 'approved_subset'
        && item.original_source_index === index && item.verification_scope === 'saved_file_finding'
        && item.verification_passed === true;
      if (item.issue_id != null && item.issue_id !== issue.id && !(issue.id == null && provenSubsetIndex)) continue;
      if (!['fixed', 'withheld', 'manual', 'failed', 'unreported'].includes(item.status)) continue;
      rows[index].outcomeSource = 'recorded_job';
      rows[index].recordedOutcome = item.status;
      rows[index].recordedDetails = item;
    }
  }

  const unmatchedRows = rows.filter((row) => row.outcomeSource === 'unreported');
  const manualAttributionIsProven = Boolean(
    counts
      && !Array.isArray(counts.issue_outcomes)
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
  recordedOutcome?: RecordedRemediationOutcome['status'],
  details?: RecordedRemediationOutcome,
): { label: string; className: string } {
  if (outcomeSource === 'recorded_job' && recordedOutcome) {
    if (recordedOutcome === 'fixed' && details?.verification_scope === 'saved_file_finding'
      && details.verification_passed === true) {
      return { label: 'Change applied · automated check passed',
        className: 'bg-[var(--feature-success-surface)] text-[var(--feature-success-content)]' };
    }
    const labels = {
      fixed: 'Change applied · verification not reported',
      withheld: 'Change withheld · not delivered',
      manual: 'Manual remediation required',
      failed: 'Remediation failed',
      unreported: 'Outcome not reported',
    };
    return { label: labels[recordedOutcome], className: 'bg-[var(--surface-tertiary)] text-secondary' };
  }
  if (outcomeSource === 'aggregate_manual') {
    return {
      label: 'Manual remediation required',
      className: 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]',
    };
  }
  if (!fix || outcomeSource === 'unreported') {
    return {
      label: 'Outcome not reported',
      className: 'bg-[var(--surface-tertiary)] text-secondary',
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
      className: 'bg-[var(--surface-tertiary)] text-secondary',
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
    className: 'bg-[var(--surface-tertiary)] text-secondary',
  };
}

export type RemediationOutcomeGroup = 'review' | 'applied' | 'manual' | 'other';

/** Delivery evidence and human review are independent of the aggregate score. */
export function outcomeGroup(row: RemediationIssueRow): RemediationOutcomeGroup {
  if (row.outcomeSource === 'recorded_job') {
    if (row.recordedOutcome === 'manual') return 'manual';
    if (row.recordedOutcome !== 'fixed') return 'other';
    return row.recordedDetails?.needs_review === true ? 'review' : 'applied';
  }
  if (row.outcomeSource === 'aggregate_manual') return 'manual';
  if (row.outcomeSource === 'persisted_fix' && row.fix) {
    if (row.fix.needs_review && ['pending', 'in_review'].includes(row.fix.review_status)) return 'review';
    if (['auto_approved', 'applied', 'approved', 'edited'].includes(row.fix.review_status)) return 'applied';
  }
  return 'other';
}

export function reviewRequirement(row: RemediationIssueRow): string | null {
  if (row.outcomeSource === 'recorded_job' && row.recordedOutcome === 'fixed') {
    return row.recordedDetails?.needs_review === true ? 'Human review required'
      : row.recordedDetails?.needs_review === false ? null : 'Human review requirement not recorded';
  }
  return row.outcomeSource === 'persisted_fix' && row.fix?.needs_review
    ? 'Review the current decision in Review changes' : null;
}
