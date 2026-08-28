import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(
  new URL('../../src/pages/Remediate.tsx', import.meta.url),
  'utf8'
);

describe('durable remediation page contract', () => {
  it('renders every terminal state and keeps browser timeout distinct', () => {
    for (const label of [
      'Remediation complete',
      'Manual review required',
      'Remediation timed out',
      'Remediation failed',
      'Still running in the background',
    ]) {
      assert.match(pageSource, new RegExp(label));
    }
    assert.match(pageSource, /Check Status/);
  });

  it('uses only durable job URLs for status and artifact retrieval', () => {
    assert.match(pageSource, /startRemediationJob/);
    assert.match(pageSource, /getLatestRemediationJob/);
    assert.match(pageSource, /getRemediationJobStatus\(statusUrl, signal\)/);
    assert.match(pageSource, /downloadRemediationJob\(job\.download_url\)/);
    assert.match(pageSource, /job\?\.download_available === true/);
  });

  it('invalidates a delayed start before it can poll after navigation', () => {
    assert.match(pageSource, /createRemediationStartCoordinator/);
    assert.match(pageSource, /coordinator\.invalidate\(\)/);
    assert.match(pageSource, /startRemediationJob\(scanId,[\s\S]*attempt\.signal\)/);
    assert.match(pageSource, /coordinator\.activate\(scanId\)/);
    assert.match(pageSource, /isCurrent\(attempt\)/);
  });

  it('remounts route-scoped state when the scan parameter changes', () => {
    assert.match(pageSource, /<RemediateScan key=\{scanId \|\| 'missing'\} scanId=\{scanId\} \/>/);
  });

  it('does not invent scores or per-issue remediation outcomes', () => {
    assert.match(pageSource, /Remediated score/);
    assert.match(pageSource, /Not available/);
    assert.match(pageSource, /Outcome not reported/);
    assert.doesNotMatch(pageSource, /fixedDescs|manualDescs|matchedFixed|matchedManual/);
    assert.doesNotMatch(pageSource, /remediated_(?:score|compliance_score)[^\n]*\|\|\s*100/);
    assert.doesNotMatch(pageSource, /status=\{issueStatuses/);
  });

  it('keeps server-authored fixed, remaining, and total aggregate counts', () => {
    assert.match(pageSource, /label: 'Fixed', value: job\.fixed_count/);
    assert.match(pageSource, /label: 'Remaining', value: job\.remaining_count/);
    assert.match(pageSource, /label: 'Total issues', value: job\.total_issues/);
  });
});
