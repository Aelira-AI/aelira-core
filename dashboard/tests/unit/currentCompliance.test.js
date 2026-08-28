import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { normalizeCurrentComplianceStats } from '../../src/utils/currentCompliance.ts';

test('normalizes current document coverage separately from scan attempts', () => {
  assert.deepEqual(
    normalizeCurrentComplianceStats({
      historical_scan_count: 5,
      enrolled_document_count: 3,
      verified_document_count: 2,
      unverified_document_count: 1,
      avg_compliance_score: 85,
      total_issues: 4,
      scans_this_month: 3,
    }),
    {
      historicalScanCount: 5,
      enrolledDocuments: 3,
      verifiedDocuments: 2,
      unverifiedDocuments: 1,
      avgCompliance: 85,
      issuesFound: 4,
      scansThisMonth: 3,
    },
  );
});

test('preserves a measured zero and keeps an absent score unknown', () => {
  assert.equal(
    normalizeCurrentComplianceStats({ avg_compliance_score: 0 }).avgCompliance,
    0,
  );
  assert.equal(normalizeCurrentComplianceStats({}).avgCompliance, null);
});

test('retains the historical total_scans compatibility alias', () => {
  const normalized = normalizeCurrentComplianceStats({ total_scans: 7 });
  assert.equal(normalized.historicalScanCount, 7);
  assert.equal(normalized.enrolledDocuments, 0);
});

test('scan views render absent results as unverified rather than zero', () => {
  for (const page of ['Dashboard.tsx', 'History.tsx', 'ScanDetail.tsx']) {
    const source = readFileSync(new URL(`../../src/pages/${page}`, import.meta.url), 'utf8');
    assert.match(source, /Unverified/);
    assert.doesNotMatch(source, /compliance_score:\s*[^\n]*\|\|\s*0/);
  }
});

test('analytics renders nullable trend scores as unassessed', () => {
  const source = readFileSync(
    new URL('../../src/components/AnalyticsDashboard.tsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /current_avg_score: number \| null/);
  assert.match(source, /previous_avg_score: number \| null/);
  assert.match(source, /Not assessed/);
  assert.match(source, /Not enough data/);
});
