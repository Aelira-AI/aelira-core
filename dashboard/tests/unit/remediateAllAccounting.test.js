import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { remediateAllAccounting } from '../../src/utils/remediateAllAccounting.ts';

describe('remediateAllAccounting', () => {
  it('uses every visible merged row as total, including unscanned rows', () => {
    const result = remediateAllAccounting(5, 2);
    assert.deepEqual(result, { total: 5, attempted: 2, skipped: 3 });
    assert.equal(result.attempted + result.skipped, result.total);
  });
});
