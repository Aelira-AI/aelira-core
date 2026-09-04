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
