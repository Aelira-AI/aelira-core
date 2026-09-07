import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const documentSource = readFileSync(
  new URL('../../src/pages/DocumentReviewPage.tsx', import.meta.url),
  'utf8',
);
const queueSource = readFileSync(
  new URL('../../src/pages/ReviewQueuePage.tsx', import.meta.url),
  'utf8',
);
const fixCardSource = readFileSync(
  new URL('../../src/components/review/FixCard.tsx', import.meta.url),
  'utf8',
);
const visualAnalysisSource = readFileSync(
  new URL('../../src/components/review/VisualAnalysisStatusPanel.tsx', import.meta.url),
  'utf8',
);

test('document review sends the API-supported batch action', () => {
  assert.match(documentSource, /action:\s*['"]approve['"]/);
  assert.doesNotMatch(documentSource, /approve_all/);
});

test('real document review contains no simulated visual evidence or save success', () => {
  assert.doesNotMatch(documentSource, /demoTableStructure|demoReadingOrderData/);
  assert.doesNotMatch(documentSource, /Table structure saved|Reading order saved/);
  assert.doesNotMatch(documentSource, /Dr\. Smith|CS 101|Document Title/);
});

test('queue consumes the server-provided pagination boundary', () => {
  assert.match(queueSource, /has_more/);
  assert.doesNotMatch(queueSource, /queue\.length\s*<\s*PAGE_SIZE/);
});

test('document review downloads all evidence formats through the authenticated client', () => {
  for (const format of ['json', 'csv', 'pdf']) {
    assert.match(documentSource, new RegExp(`value: ['"]${format}['"]`));
  }
  assert.match(documentSource, /responseType:\s*['"]blob['"]/);
  assert.match(documentSource, /response\.headers\[['"]content-disposition['"]\]/);
  assert.match(documentSource, /URL\.createObjectURL/);
  assert.match(documentSource, /Downloading/);
});

test('download failures are reported without a false success path', () => {
  assert.match(documentSource, /toast\.error[\s\S]*Evidence Download/);
  assert.match(documentSource, /toast\.success[\s\S]*Evidence Download/);
  assert.match(documentSource, /setDownloadingFormat\(null\)/);
});

test('document review exposes all controlled deferral filters', () => {
  for (const state of ['deferred_active', 'deferred_expired', 'deferred_revoked', 'deferred_resolved']) {
    assert.match(documentSource, new RegExp(state));
  }
});

test('deferral controls submit accountability fields and support revocation', () => {
  assert.match(documentSource, /fixes\/\$\{fixId\}\/deferral/);
  assert.match(documentSource, /owner/);
  assert.match(documentSource, /reason/);
  assert.match(documentSource, /expires_at/);
  assert.match(documentSource, /deferral\/revoke/);
  assert.match(fixCardSource, /Defer/);
  assert.match(fixCardSource, /Revoke deferral/);
});

test('dashboard explains the evidence boundary for deferrals', () => {
  assert.match(fixCardSource, /operational decision/i);
  assert.match(fixCardSource, /not remediation/i);
  assert.match(fixCardSource, /conformance evidence/i);
});

test('document review renders durable visual analysis lifecycle states', () => {
  assert.match(documentSource, /VisualAnalysisStatusPanel/);
  for (const state of ['queued', 'running', 'retryable_failure', 'terminal_failure', 'review_required']) {
    assert.match(visualAnalysisSource, new RegExp(state));
  }
  assert.match(visualAnalysisSource, /Failure category/);
  assert.match(visualAnalysisSource, /Attempt \{analysis\.attempt_count\} of \{analysis\.max_attempts\}/);
});

test('machine visual output is never presented as accepted alt text', () => {
  assert.match(visualAnalysisSource, /Machine output is a proposal until the linked fix is accepted in review/);
  assert.match(visualAnalysisSource, /Machine proposal — not approved/);
  assert.doesNotMatch(visualAnalysisSource, /approved alt text/i);
});
