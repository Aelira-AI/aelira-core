import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { displayScore, remediationScore } from '../../src/utils/remediationScore.ts';

describe('measured remediation scores', () => {
  const scan = { compliance_score: 95.7 };
  const job = {
    original_score: 95.7, remediated_score: 98.2, score_verified: true,
    human_review_required: false,
    score_measurement: {
      method_version: 'document-scan-v1', source_sha256: 'a'.repeat(64),
      output_sha256: 'b'.repeat(64), source_score: 95.7, output_score: 98.2,
    },
  };
  const withOutput = (score) => ({ ...job, remediated_score: score,
    score_measurement: { ...job.score_measurement, output_score: score } });

  it('shows measured decimals and a positive comparison', () => {
    const result = remediationScore(job, scan);
    assert.equal(displayScore(result.before), '95.7/100');
    assert.equal(displayScore(result.after), '98.2/100');
    assert.equal(result.deltaLabel, '+2.5 points');
    assert.equal(result.success, true);
    assert.equal(displayScore(100), '100.0/100');
  });
  it('reports regressions and unchanged scores without success', () => {
    const lower = remediationScore(withOutput(95), scan);
    assert.equal(lower.title, 'Score decreased — review required');
    assert.equal(lower.deltaLabel, '-0.7 points');
    assert.equal(lower.success, false);
    const unchanged = remediationScore(withOutput(95.7), scan);
    assert.equal(unchanged.title, 'Score unchanged');
    assert.equal(unchanged.deltaLabel, '0.0 points');
    assert.equal(unchanged.success, false);
  });
  it('rejects legacy estimates, missing provenance and invalid scores', () => {
    for (const score_verified of [undefined, false]) {
      assert.equal(remediationScore({ ...job, score_verified }, scan).after, null);
    }
    for (const score_measurement of [undefined, null, {}, { ...job.score_measurement, output_sha256: 'invalid' }]) {
      assert.equal(remediationScore({ ...job, score_measurement }, scan).after, null);
    }
    for (const score of [null, undefined, NaN, Infinity, -Infinity, -1, 101]) {
      const result = remediationScore(withOutput(score), scan);
      assert.equal(displayScore(result.after), 'Not available');
      assert.equal(result.deltaLabel, 'Not available');
      assert.equal(result.success, false);
    }
    assert.equal(remediationScore({ ...job, score_verified: undefined }, {}).before, null);
  });
  it('rejects historical baselines and measurement values that disagree', () => {
    for (const candidate of [
      { ...job, original_score: 75 },
      { ...job, score_measurement: { ...job.score_measurement, source_score: 75 } },
      { ...job, score_measurement: { ...job.score_measurement, output_score: 100 } },
    ]) {
      const result = remediationScore(candidate, scan);
      assert.equal(result.before, 95.7);
      assert.equal(result.after, null);
      assert.equal(result.reasonCode, 'baseline_mismatch');
      assert.equal(result.success, false);
    }
    assert.equal(remediationScore(job, { compliance_score: 75 }).after, null);
  });
  it('requires an explicit no-review result before using success styling', () => {
    for (const human_review_required of [undefined, true]) {
      assert.equal(remediationScore({ ...job, human_review_required }, scan).success, false);
    }
  });
  it('preserves a measured zero and uses the measured baseline when no stored score exists', () => {
    const zero = { ...withOutput(0), original_score: 0,
      score_measurement: { ...job.score_measurement, source_score: 0, output_score: 0 } };
    assert.equal(remediationScore(zero, {}).before, 0);
    assert.equal(remediationScore(zero, {}).after, 0);
    assert.equal(remediationScore(zero, {}).deltaLabel, '0.0 points');
    assert.equal(remediationScore(job, {}).before, 95.7);
    assert.equal(remediationScore(job, { result: { compliance_score: 75 } }).after, null);
  });
  it('explains missing original, output scan failure, and incomplete comparison', () => {
    for (const [reasonCode, message] of [
      ['original_file_missing', /original file is unavailable/i],
      ['output_scan_failed', /output could not be scored/i],
      ['incomplete_comparison', /before-and-after comparison is incomplete/i],
    ]) {
      const result = remediationScore({ score_verified: false, score_verification_reason: reasonCode }, scan);
      assert.equal(result.reasonCode, reasonCode);
      assert.match(result.description, message);
      assert.equal(result.after, null);
    }
  });
  it('never returns raw unknown reason strings', () => {
    for (const score_verification_reason of ['unexpected detail <script>', '__proto__', 'constructor', '', null]) {
      const result = remediationScore({ score_verified: false, score_verification_reason }, scan);
      assert.equal(result.reasonCode, 'legacy_unverified');
      assert.doesNotMatch(result.description, /unexpected detail|script|__proto__|constructor/);
    }
  });
  it('does not instruct people to review an output that was not published', () => {
    const unavailable = remediationScore({ score_verified: false, download_available: false, score_verification_reason: 'incomplete_comparison' }, scan);
    assert.match(unavailable.description, /Review the original document and unresolved findings/);
    assert.doesNotMatch(unavailable.description, /Review the output/);
    assert.equal(unavailable.title, 'Manual review required');
    const available = remediationScore({ score_verified: false, download_available: true }, scan);
    assert.match(available.description, /Review the output before use/);
  });
  it('honors every supported failure reason even if a stale verified flag is present', () => {
    for (const score_verification_reason of ['original_file_missing', 'original_scan_failed',
      'output_file_missing', 'output_scan_failed', 'incomplete_comparison', 'baseline_mismatch',
      'unsupported_scan_type', 'legacy_unverified', 'artifact_mismatch']) {
      const result = remediationScore({ ...job, score_verification_reason }, scan);
      assert.equal(result.reasonCode, score_verification_reason);
      assert.equal(result.after, null);
      assert.equal(result.success, false);
    }
  });
});
