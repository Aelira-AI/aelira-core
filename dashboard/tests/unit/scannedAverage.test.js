import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { scannedAverage } from '../../src/utils/scannedAverage.ts';

describe('complete averages for scanned course content', () => {
  it('leaves a Brightspace module unassessed when one completed item has no score', () => {
    const items = [
      { scan_status: 'completed', compliance_score: 100, issue_count: 0 },
      { scan_status: 'completed', compliance_score: null, issue_count: 1 },
      { scan_status: null, compliance_score: null, issue_count: null },
    ];
    assert.equal(
      scannedAverage(items, (item) => item.scan_status !== null, (item) => item.compliance_score),
      null
    );
    assert.equal(items[1].issue_count, 1);
  });

  it('excludes never-scanned courses and preserves measured zero', () => {
    const courses = [
      { scanned_items: 1, avg_compliance: 0 },
      { scanned_items: 1, avg_compliance: 100 },
      { scanned_items: 0, avg_compliance: null },
    ];
    assert.equal(
      scannedAverage(courses, (course) => course.scanned_items > 0, (course) => course.avg_compliance),
      50
    );
    courses.push({ scanned_items: 1, avg_compliance: null });
    assert.equal(
      scannedAverage(courses, (course) => course.scanned_items > 0, (course) => course.avg_compliance),
      null
    );
  });

  it('does not turn a cached score on an unscanned member into course evidence', () => {
    assert.equal(
      scannedAverage(
        [{ scanned: false, score: 100 }, { scanned: true, score: null }],
        (item) => item.scanned,
        (item) => item.score
      ),
      null
    );
  });

  it('keeps the LTI header bound to the authoritative course score and distinguishes null states', () => {
    const source = readFileSync(
      new URL('../../src/pages/LTICourseView.tsx', import.meta.url),
      'utf8'
    );
    assert.match(source, /const overallCompliance = courseMeta\.compliance_percentage;/);
    assert.match(source, /stats\.scanned > 0 \|\| contentStats\.scanned > 0/);
    assert.match(source, /Overall assessment unavailable/);
    assert.match(source, /No files scanned yet/);
  });
});
