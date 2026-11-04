import type { RecordedRemediationOutcome, RemediationJobStatus } from '../api/scans';

export interface RemediationIssueLike {
  id?: string;
  description?: string;
  title?: string;
  severity?: string;
  issue_type?: string;
}

export type RemediationScanLike = {
  compliance_score?: number | null;
  result?: { compliance_score?: number | null; issues?: RemediationIssueLike[] };
  issues?: RemediationIssueLike[];
};

export type RemediationScoreJob = Partial<Pick<RemediationJobStatus,
  'original_score' | 'remediated_score' | 'score_verified' | 'human_review_required'
  | 'score_verification_reason' | 'score_measurement' | 'download_available'
  | 'fresh_score_comparison'>> & {
    issue_outcomes?: RecordedRemediationOutcome[] | null;
  };

const REASONS = {
  original_file_missing: 'The original file is unavailable, so its score could not be measured.',
  original_scan_failed: 'The original file could not be scored.',
  source_text_mapping_unavailable: 'The PDF’s text encoding could not be verified. Export a new PDF from the source document with readable Unicode text, or have its font mappings repaired manually.',
  source_text_scope_unsupported: 'The PDF contains text in a Form or another stream scope that this remediation path cannot verify. Export a new PDF from the source document or use a reviewed recovery workflow.',
  output_file_missing: 'The output file is unavailable, so its score could not be measured.',
  output_scan_failed: 'The output could not be scored.',
  incomplete_comparison: 'The measured before-and-after comparison is incomplete.',
  baseline_mismatch: 'The recorded scores do not match the measured comparison.',
  unsupported_scan_type: 'Measured comparison is unavailable for this document type.',
  legacy_unverified: 'This job has no verified measurement record. Earlier score estimates are not shown.',
  artifact_mismatch: 'The output file does not match the measured artifact.',
} as const;
type ReasonCode = keyof typeof REASONS;

export function measuredScore(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    ? value : null;
}

export function displayScore(value: unknown): string {
  const score = measuredScore(value);
  return score === null ? 'Not available' : `${score.toFixed(1)}/100`;
}

export function freshScoreComparison(job: RemediationScoreJob) {
  const receipt = job.fresh_score_comparison;
  if (job.download_available !== true || !receipt || !/^[a-f0-9]{64}$/.test(receipt.source_sha256)
    || !/^[a-f0-9]{64}$/.test(receipt.output_sha256)
    || ![
      'office-word-strict-v1', 'office-powerpoint-strict-v1',
      'office-excel-strict-v1', 'pdf-strict-v1', 'code-static-v1',
      'latex-source-v1',
    ].includes(receipt.method_version)) return null;
  const source = measuredScore(receipt.source_score);
  const output = measuredScore(receipt.output_score);
  if (source === null || output === null) return null;
  const delta = Number((output - source).toFixed(1));
  return {
    source, output, methodVersion: receipt.method_version,
    deltaLabel: `${delta > 0 ? '+' : ''}${delta.toFixed(1)} points`,
  };
}

export function remediationScore(
  job: RemediationScoreJob,
  scan: RemediationScanLike
) {
  const persisted = measuredScore(scan.compliance_score) ?? measuredScore(scan.result?.compliance_score);
  const original = measuredScore(job.original_score);
  const output = measuredScore(job.remediated_score);
  const measurement = job.score_measurement;
  const hasMeasurement = measurement != null
    && typeof measurement.method_version === 'string' && measurement.method_version.length > 0
    && typeof measurement.source_sha256 === 'string' && /^[a-f0-9]{64}$/.test(measurement.source_sha256)
    && typeof measurement.output_sha256 === 'string' && /^[a-f0-9]{64}$/.test(measurement.output_sha256)
    && measuredScore(measurement.source_score) !== null && measuredScore(measurement.output_score) !== null;
  // A stored scan must never be silently replaced by a different measured baseline.
  const mismatch = job.score_verified === true && (
    (persisted !== null && original !== null && persisted !== original)
    || (hasMeasurement && (measurement.source_score !== original || measurement.output_score !== output))
  );
  const rawReason = job.score_verification_reason;
  let reasonCode: ReasonCode | null = typeof rawReason === 'string' && Object.hasOwn(REASONS, rawReason)
    ? rawReason as ReasonCode : null;
  if (mismatch) reasonCode = 'baseline_mismatch';
  else if (!reasonCode && (job.score_verified !== true || !hasMeasurement)) reasonCode = 'legacy_unverified';
  else if (!reasonCode && (original === null || output === null)) reasonCode = 'incomplete_comparison';

  const before = persisted ?? (job.score_verified === true && hasMeasurement ? original : null);
  const after = reasonCode === null && job.score_verified === true && hasMeasurement ? output : null;
  const delta = before !== null && after !== null ? after - before : null;
  const success = delta !== null && delta > 0 && job.human_review_required === false;

  const scanIssues = scan.issues || scan.result?.issues || [];
  const originalCriticalIssues = scanIssues.filter(
    (issue) => String(issue?.severity || '').toLowerCase() === 'critical'
  );

  const outcomes = job.issue_outcomes || [];
  const remainingCriticalIssues = originalCriticalIssues.filter((issue, index) => {
    const isFixed = outcomes.some(
      (outcome) =>
        ((outcome.issue_id && issue.id && outcome.issue_id === issue.id) ||
         (outcome.source_index === index)) &&
        outcome.status === 'fixed'
    );
    return !isFixed;
  });

  const criticalFindings = remainingCriticalIssues
    .map((issue) => issue.description || issue.title || issue.issue_type || 'Critical accessibility finding')
    .filter(Boolean);

  const CRITICAL_CAP = 49.0;
  const originalCapped = before !== null && Number(before.toFixed(1)) <= CRITICAL_CAP && (originalCriticalIssues.length > 0 || Number(before.toFixed(1)) === CRITICAL_CAP);
  const remediatedCapped = after !== null && Number(after.toFixed(1)) <= CRITICAL_CAP && (remainingCriticalIssues.length > 0 || (originalCriticalIssues.length === 0 && Number(after.toFixed(1)) === CRITICAL_CAP));

  const title = delta === null
    ? (job.download_available === true ? 'Output needs review' : 'Manual review required')
    : delta < 0
    ? 'Score decreased — review required'
    : delta === 0
    ? (remediatedCapped ? 'Score unchanged — held at critical-finding cap' : 'Score unchanged')
    : 'Measured score increased';

  const criticalFindingsList = criticalFindings.length > 0 ? `: ${criticalFindings.join(', ')}` : '';
  const capExplanation = remediatedCapped
    ? `The remediated score is held at the 49/100 cap because critical findings remain${criticalFindingsList}. The score cannot exceed 49 until all critical findings are resolved.`
    : originalCapped && after !== null && after > CRITICAL_CAP
    ? 'The original score was held at the 49/100 critical cap; resolving all critical findings lifted the cap.'
    : '';

  const defaultDescription = delta === null
    ? `${reasonCode ? REASONS[reasonCode] : REASONS.incomplete_comparison} ${job.download_available === true
      ? 'Review the output before use.' : 'Review the original document and unresolved findings; no downloadable output is available.'}`
    : delta < 0
    ? 'The output scored lower on the same scanner. Review the remaining findings and changes before use.'
    : 'Both scores use the same scanner. Automated scores do not establish accessibility conformance. Review all changes before use.';

  const description = capExplanation ? `${capExplanation} ${defaultDescription}` : defaultDescription;
  const roundedDelta = delta === null ? null : Number(delta.toFixed(1));

  return {
    before,
    after,
    success,
    title,
    description,
    reasonCode,
    originalCapped,
    remediatedCapped,
    criticalFindings,
    deltaLabel: roundedDelta === null ? 'Not available' : `${roundedDelta > 0 ? '+' : ''}${roundedDelta.toFixed(1)} points`,
  };
}
