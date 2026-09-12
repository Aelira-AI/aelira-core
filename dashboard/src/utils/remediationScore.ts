import type { RemediationJobStatus } from '../api/scans';

export type RemediationScoreJob = Partial<Pick<RemediationJobStatus,
  'original_score' | 'remediated_score' | 'score_verified' | 'human_review_required'
  | 'score_verification_reason' | 'score_measurement'>>;

const REASONS = {
  original_file_missing: 'The original file is unavailable, so its score could not be measured.',
  original_scan_failed: 'The original file could not be scored.',
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

export function remediationScore(job: RemediationScoreJob,
  scan: { compliance_score?: number | null; result?: { compliance_score?: number | null } }) {
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
  const title = delta === null ? 'Output needs review'
    : delta < 0 ? 'Score decreased — review required'
    : delta === 0 ? 'Score unchanged' : 'Measured score increased';
  const description = delta === null
    ? `${reasonCode ? REASONS[reasonCode] : REASONS.incomplete_comparison} Review the output before use.`
    : delta < 0 ? 'The output scored lower on the same scanner. Review the remaining findings and changes before use.'
    : 'Both scores use the same scanner. Automated scores do not establish accessibility conformance. Review all changes before use.';
  const roundedDelta = delta === null ? null : Number(delta.toFixed(1));
  return { before, after, success, title, description, reasonCode,
    deltaLabel: roundedDelta === null ? 'Not available' : `${roundedDelta > 0 ? '+' : ''}${roundedDelta.toFixed(1)} points` };
}
