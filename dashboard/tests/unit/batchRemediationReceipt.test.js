import { it } from 'node:test';
import assert from 'node:assert/strict';
import { batchRemediationReceiptIsConfirmed } from '../../src/utils/batchRemediationReceipt.ts';

const scanIds = ['scan-a', 'scan-b'];
const receipt = { success: true, total_scans: 2, scans_queued: [...scanIds], job_ids: ['job-a', 'job-b'] };

it('confirms accepted jobs for exactly the requested documents regardless of order', () => {
  assert.equal(batchRemediationReceiptIsConfirmed(receipt, scanIds), true);
  assert.equal(batchRemediationReceiptIsConfirmed({ ...receipt, scans_queued: [...scanIds].reverse() }, scanIds), true);
});

it('rejects missing, partial, duplicate, mismatched and malformed queue receipts', () => {
  for (const value of [null, {}, { ...receipt, success: false }, { ...receipt, total_scans: 1 },
    { ...receipt, scans_queued: ['scan-a'] }, { ...receipt, scans_queued: ['scan-a', 'scan-a'] },
    { ...receipt, scans_queued: ['scan-a', 'different'] }, { ...receipt, job_ids: [] },
    { ...receipt, job_ids: ['same', 'same'] }, { ...receipt, job_ids: ['job-a', null] }]) {
    assert.equal(batchRemediationReceiptIsConfirmed(value, scanIds), false);
  }
  assert.equal(batchRemediationReceiptIsConfirmed(receipt, []), false);
});
