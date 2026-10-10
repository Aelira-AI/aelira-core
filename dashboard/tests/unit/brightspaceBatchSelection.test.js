import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  brightspaceApprovalIds,
  brightspaceApprovalSummary,
  brightspaceWritebackSummary,
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

describe('brightspaceWritebackSummary', () => {
  it('reports unsupported file skips and stale-only batches without green success', () => {
    for (const result of [
      { written_count: 0, failed_count: 0, stale_count: 0, skipped_count: 3, errors: ['Managed file write-back unavailable; download and upload manually'] },
      { written_count: 0, failed_count: 0, stale_count: 2 },
    ]) {
      const summary = brightspaceWritebackSummary(result);
      assert.equal(summary.status, 'zero');
      assert.match(summary.message, /Wrote back 0/);
    }
    assert.match(brightspaceWritebackSummary({ written_count: 0, failed_count: 0, stale_count: 0, skipped_count: 3, errors: ['Download and upload manually'] }).message, /3 skipped.*Download and upload manually/);
  });

  it('reserves success for confirmed writes without skips, stale items or errors', () => {
    assert.equal(brightspaceWritebackSummary({ written_count: 2, failed_count: 0, stale_count: 0 }).status, 'success');
    assert.equal(brightspaceWritebackSummary({ written_count: 1, failed_count: 0, stale_count: 0, skipped_count: 1 }).status, 'mixed');
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
