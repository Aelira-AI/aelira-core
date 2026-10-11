import assert from 'node:assert/strict';

type RecordedFix = {
  id: string;
  review_status: string;
  needs_review: boolean;
  review_digest: string;
  approved_review_digest: string;
};
type ReviewSnapshot = {
  status: string;
  total_fixes: number;
  needs_review_count: number;
  reviewed_count: number;
  auto_approved_count: number;
  fixes: RecordedFix[];
};
type AuditExport = {
  summary: {
    is_conformance_determination: boolean;
    total_fixes: number;
    review_status_counts: Record<string, number>;
  };
  reviewer_decisions: {
    fix_id: string;
    review_status: string;
    decision_source: string;
    review_digest: string;
    approved_review_digest: string;
  }[];
  audit_trail: { action: string }[];
};

/** Check the persisted review and audit evidence for a fixture with only rule-approved fixes. */
export function verifyAutoApprovedOnly(before: ReviewSnapshot, after: ReviewSnapshot, audit: AuditExport) {
  assert(before.total_fixes > 0, 'The fixture must record at least one real fix');
  assert.equal(before.fixes.length, before.total_fixes);
  assert.equal(before.status, 'approved');
  assert.equal(before.needs_review_count, 0, 'No pending fix may be silently bypassed');
  assert.equal(before.reviewed_count, 0, 'No human decision may be invented');
  assert.equal(before.auto_approved_count, before.total_fixes);
  for (const fix of before.fixes) {
    assert.equal(fix.review_status, 'auto_approved');
    assert.equal(fix.needs_review, false);
    assert.match(fix.review_digest, /^[a-f0-9]{64}$/);
    assert.equal(fix.approved_review_digest, fix.review_digest,
      'Rule approval binds the exact recorded content');
  }
  assert.deepEqual(after, before, 'A fresh read must retain every recorded fix without a review mutation');
  assert.equal(audit.summary.is_conformance_determination, false);
  assert.equal(audit.summary.total_fixes, before.total_fixes);
  assert.equal(audit.summary.review_status_counts.auto_approved, before.total_fixes);
  assert(Object.entries(audit.summary.review_status_counts)
    .every(([status, count]) => status === 'auto_approved' || count === 0),
  'Audit must not report pending, human-approved, or unknown decisions');
  assert.deepEqual(audit.reviewer_decisions.map((decision) => decision.fix_id).sort(),
    before.fixes.map((fix) => fix.id).sort());
  for (const decision of audit.reviewer_decisions) {
    const fix = before.fixes.find((recorded) => recorded.id === decision.fix_id)!;
    assert.equal(decision.review_status, 'auto_approved');
    assert.equal(decision.decision_source, 'automated');
    assert.equal(decision.review_digest, fix.review_digest);
    assert.equal(decision.approved_review_digest, fix.review_digest);
  }
  assert(!audit.audit_trail.some((entry) =>
    ['fix_approve', 'fix_batch_approve', 'batch_approve'].includes(entry.action)),
  'Audit must not claim a human approval that did not occur');
  return { reviewed: 0, auto_approved: before.total_fixes,
    audit_actions: audit.audit_trail.map((entry: any) => entry.action) };
}
