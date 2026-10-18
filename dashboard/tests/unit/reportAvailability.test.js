import assert from 'node:assert/strict';
import test from 'node:test';

import { canDownloadComplianceReport } from '../../src/utils/reportAvailability.ts';

test('compliance PDF availability follows assessment status, including a measured zero', () => {
  assert.equal(canDownloadComplianceReport(null), false);
  assert.equal(canDownloadComplianceReport(undefined), false);
  assert.equal(canDownloadComplianceReport(0), true);
  assert.equal(canDownloadComplianceReport(82), true);
});
