import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  completedCourseAverage,
  completedScanBadge,
  completedScanNotice,
} from '../../src/utils/canvasScanStatus.ts';

const scan = (compliance_score, issues_count) => ({ compliance_score, issues_count });

describe('Canvas completed scan presentation', () => {
  it('keeps zero findings distinct from a measured overall score', () => {
    assert.deepEqual(completedScanBadge(scan(null, 0)), {
      label: 'Unassessed', intent: 'neutral',
    });
    assert.match(completedScanNotice([scan(null, 0)]), /Overall assessment unavailable/);
    assert.doesNotMatch(completedScanNotice([scan(null, 0)]), /accessible|compliant/i);
  });

  it('retains a caption finding alongside an unavailable overall assessment', () => {
    assert.equal(completedScanBadge(scan(null, 1)).label, 'Unassessed · 1 finding');
    assert.match(completedScanNotice([scan(null, 1)]), /1 recorded finding/);
    assert.equal(completedCourseAverage([scan(null, 1)]), null);
  });

  it('does not convert missing issue counts into zero or null text', () => {
    assert.equal(completedScanBadge(scan(null, null)).label, 'Unassessed');
    const notice = completedScanNotice([scan(null, null)]);
    assert.match(notice, /Finding count unavailable/);
    assert.doesNotMatch(notice, /0 recorded|NaN|null/);
  });

  it('makes mixed batch totals and course averages unavailable when evidence is partial', () => {
    const notice = completedScanNotice([scan(100, 0), scan(null, 1)]);
    assert.match(notice, /1 overall assessment unavailable/);
    assert.match(notice, /1 recorded finding/);
    assert.equal(completedCourseAverage([scan(100, 0), scan(null, 1)]), null);
    assert.doesNotMatch(completedScanNotice([scan(100, 0), scan(null, null)]), /0 recorded/);
  });

  it('preserves a measured zero without claiming accessibility conformance', () => {
    assert.equal(completedCourseAverage([scan(0, 1), scan(100, 0)]), 50);
    assert.equal(completedScanBadge(scan(0, 0)).label, 'Scanned · no findings');
    assert.doesNotMatch(completedScanNotice([scan(0, 0)]), /accessible|compliant/i);
  });
});
