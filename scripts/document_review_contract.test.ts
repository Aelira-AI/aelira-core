import assert from 'node:assert/strict';
import test from 'node:test';
import { verifyAutoApprovedOnly } from './document_review_contract.ts';

const digest = 'a'.repeat(64);
const before = {
  status: 'approved', total_fixes: 1, needs_review_count: 0,
  auto_approved_count: 1, reviewed_count: 0,
  fixes: [{ id: 'fix-1', review_status: 'auto_approved', needs_review: false,
    fixed_content: 'Tagged form field', review_digest: digest, approved_review_digest: digest }],
};
const audit = {
  summary: { is_conformance_determination: false, total_fixes: 1,
    review_status_counts: { pending: 0, approved: 0, rejected: 0, edited: 0,
      auto_approved: 1, unresolved: 0, unavailable: 0 } },
  reviewer_decisions: [{ fix_id: 'fix-1', review_status: 'auto_approved',
    decision_source: 'automated', review_digest: digest, approved_review_digest: digest }],
  audit_trail: [{ action: 'remediation_complete' }],
};
const copy = <T>(value: T): T => structuredClone(value);

test('an unchanged rule-approved review has durable evidence without a human decision', () => {
  assert.deepEqual(verifyAutoApprovedOnly(copy(before), copy(before), copy(audit)), {
    reviewed: 0, auto_approved: 1, audit_actions: ['remediation_complete'],
  });
});

test('the rule-approved path rejects missing or pending fixes', () => {
  const missing = copy(before);
  missing.total_fixes = 0;
  missing.auto_approved_count = 0;
  missing.fixes = [];
  assert.throws(() => verifyAutoApprovedOnly(missing, copy(missing), copy(audit)));
  const pending = copy(before);
  pending.fixes[0].review_status = 'pending';
  pending.fixes[0].needs_review = true;
  assert.throws(() => verifyAutoApprovedOnly(pending, copy(pending), copy(audit)));
});

test('the rule-approved path rejects a changed fix or unbound digest', () => {
  const changed = copy(before);
  changed.fixes[0].fixed_content = 'Changed after recording';
  assert.throws(() => verifyAutoApprovedOnly(copy(before), changed, copy(audit)));
  const unbound = copy(before);
  unbound.fixes[0].approved_review_digest = 'b'.repeat(64);
  assert.throws(() => verifyAutoApprovedOnly(unbound, copy(unbound), copy(audit)));
});

test('the rule-approved path rejects invented human approval or audit counts', () => {
  const human = copy(audit);
  human.audit_trail.push({ action: 'fix_approve' });
  assert.throws(() => verifyAutoApprovedOnly(copy(before), copy(before), human));
  const counted = copy(audit);
  counted.summary.review_status_counts.approved = 1;
  assert.throws(() => verifyAutoApprovedOnly(copy(before), copy(before), counted));
  const decision = copy(audit);
  decision.reviewer_decisions[0].decision_source = 'human';
  assert.throws(() => verifyAutoApprovedOnly(copy(before), copy(before), decision));
});
