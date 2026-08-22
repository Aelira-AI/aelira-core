import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  brightspaceApprovalIds,
  brightspaceApprovalSummary,
} from '../../src/utils/brightspaceBatchSelection.ts';

describe('brightspaceApprovalIds', () => {
  it('selects only the server-computed approval eligible items', () => {
    const ids = brightspaceApprovalIds([
      { cloud_file_id: 'artifact', approval_eligible: true },
      { cloud_file_id: 'html', approval_eligible: true },
      { cloud_file_id: 'flag-only', approval_eligible: false },
      { cloud_file_id: 'terminal', approval_eligible: false },
    ]);
    assert.deepEqual(ids, ['artifact', 'html']);
  });
});

describe('brightspaceApprovalSummary', () => {
  it('never reports all-ineligible approval as success', () => {
    const summary = brightspaceApprovalSummary({
      requested_count: 2,
      approved_count: 0,
      skipped_count: 2,
      failed_count: 0,
      outcomes: [
        { cloud_file_id: 'one', status: 'skipped', reason: 'no_durable_remediation_authority' },
        { cloud_file_id: 'two', status: 'skipped', reason: 'already_terminal' },
      ],
      errors: [
        'one: no_durable_remediation_authority',
        'two: already_terminal',
      ],
    });

    assert.equal(summary.status, 'zero');
    assert.match(summary.message, /Approved 0 · 2 skipped/);
  });

  it('reports partial approval as mixed rather than green success', () => {
    const summary = brightspaceApprovalSummary({
      requested_count: 2,
      approved_count: 1,
      skipped_count: 0,
      failed_count: 1,
      outcomes: [
        { cloud_file_id: 'one', status: 'approved', reason: null },
        { cloud_file_id: 'two', status: 'failed', reason: 'artifact_approval_validation_failed' },
      ],
      errors: ['two: artifact_approval_validation_failed'],
    });

    assert.equal(summary.status, 'mixed');
    assert.match(summary.message, /Approved 1 · 1 failed/);
  });
});
