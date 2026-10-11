const BLOCKERS: Record<string, string> = {
  fixes_pending_review: 'Approve, edit or reject the pending changes first.',
  verification_not_passed: 'The saved-file verification requirements have not passed.',
  no_fixes: 'No remediation changes are recorded for this scan.',
  no_accepted_fix: 'No change has been accepted.',
  fix_approval_digest_invalid: 'A review decision changed. Refresh and review the current changes.',
  reviewed_output_required: 'Rebuild the working file to apply edited changes and remove rejected changes.',
  fix_review_digest_invalid: 'The current changes need a new valid review record.',
  fix_occurrence_identity_invalid: 'The change identity could not be verified. Run remediation again.',
  output_membership_unrecorded: 'The working file does not record which reviewed changes it contains. Rebuild a reviewed PDF or run remediation again before approval.',
  source_hash_unavailable: 'The original scan has no verified source fingerprint. Rescan the source and remediate it before approval.',
  source_changed_since_scan: 'The source changed since its scan. Rescan the current source and remediate it before approval.',
  saved_file_verification_unavailable: 'The working file has no valid saved-file verification. Run remediation again and review its result.',
  applied_fix_membership_unavailable: 'The changes could not be bound to this working file. Run remediation again before approval.',
  manual_edit_preservation_unverified: 'This saved edit does not prove that all earlier repairs remain in the current PDF. The earlier fix decisions and score describe the prior file. Download this candidate for review, or create a new verified remediation output before approval.',
  reviewed_changes_not_in_output: 'The working file is not yet bound to the accepted review decisions. Complete pending reviews first. If decisions changed, rebuild from accepted changes before approval.',
};

export function approvalBlockerMessage(code: string, manualEdits: boolean): string {
  if (manualEdits && code === 'output_membership_unrecorded') {
    return 'This candidate has no record of which accepted remediation fixes its bytes contain. Manual structure edits cannot create that record.';
  }
  if (manualEdits && code === 'no_fixes') {
    return 'No remediation fixes were recorded for this scan. The saved structure edit is a separate candidate, not an accepted remediation fix.';
  }
  return BLOCKERS[code] ?? 'A saved-file or specialist review requirement remains. Complete the review before publication.';
}

export function lacksRemediationEvidence(blockers: readonly string[]): boolean {
  return blockers.includes('no_fixes') || blockers.includes('output_membership_unrecorded');
}

export function manualCandidateGuidance({ missingRemediationEvidence, hasBlockers, editorAvailable }: {
  missingRemediationEvidence: boolean;
  hasBlockers: boolean;
  editorAvailable: boolean;
}): string {
  const savedEdit = editorAvailable
    ? ' To preserve its edits when making another structure change, choose Current saved PDF in the editor below.'
    : '';
  const nextStep = missingRemediationEvidence
    ? ' This candidate lacks a verified record of accepted remediation fixes in its bytes. More manual structure edits cannot create that record or make this candidate approvable in the app. To pursue publication approval, start a separate scan and remediation lifecycle that records accepted fixes and passes saved-file checks. Rescanning does not approve this candidate.'
    : hasBlockers
      ? ' Complete the review requirements below before publication.'
      : ' Review this candidate and use the approval control below when ready.';
  return `This manually edited PDF is a saved candidate you can download.${savedEdit}${nextStep}`;
}
