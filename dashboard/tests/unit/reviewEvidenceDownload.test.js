import assert from 'node:assert/strict';
import test from 'node:test';

import {
  evidenceContentType,
  evidenceFilename,
} from '../../src/utils/reviewEvidenceDownload.ts';

test('safe server disposition and content type are preserved', () => {
  assert.equal(
    evidenceFilename('attachment; filename="audit-scan-001.csv"', 'ignored', 'csv'),
    'audit-scan-001.csv',
  );
  assert.equal(evidenceContentType('text/csv; charset=utf-8', 'csv'), 'text/csv; charset=utf-8');
});

test('unsafe or missing disposition filenames use stable bounded fallbacks', () => {
  assert.equal(evidenceFilename('attachment; filename="../../secret.csv"', 'scan:001', 'csv'), 'audit-scan001.csv');
  assert.equal(evidenceFilename("attachment; filename*=UTF-8''%E0%A4%A", 'scan-001', 'json'), 'audit-scan-001.json');
  assert.equal(evidenceFilename(undefined, '../', 'pdf'), 'accessibility-review-evidence-scan.pdf');
  assert.equal(evidenceFilename(undefined, 'x'.repeat(100), 'json'), `audit-${'x'.repeat(64)}.json`);
});

test('missing content types use format-specific media types', () => {
  assert.equal(evidenceContentType(undefined, 'json'), 'application/json');
  assert.equal(evidenceContentType(undefined, 'csv'), 'text/csv');
  assert.equal(evidenceContentType(undefined, 'pdf'), 'application/pdf');
});
