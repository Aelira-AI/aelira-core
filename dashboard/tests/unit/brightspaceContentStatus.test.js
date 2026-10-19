import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as brightspaceStatus from '../../src/utils/brightspaceContentStatus.ts';

const { getBrightspaceContentAssessmentLabels, resolveBrightspaceContentStatus } = brightspaceStatus;

function contentItem(overrides = {}) {
  return {
    cloud_file_id: 'cf-1',
    title: 'Course content',
    content_type: 'topic_html',
    compliance_score: 72,
    issue_count: 2,
    writeback_status: 'pending_review',
    has_remediated_version: true,
    remediation_origin: null,
    module_path: 'Module 1',
    ...overrides,
  };
}

describe('Brightspace persisted remediation status', () => {
  it('shows a completed scan without a score as unassessed in both row badges', () => {
    assert.deepEqual(
      getBrightspaceContentAssessmentLabels(contentItem({ compliance_score: null, scan_status: 'COMPLETED' })),
      { score: 'Unassessed', fallbackStatus: 'Unassessed' }
    );
  });

  it('keeps never-scanned and incomplete scans distinct from completed unassessed scans', () => {
    for (const scan_status of [null, 'PENDING', 'PROCESSING', 'FAILED']) {
      assert.deepEqual(
        getBrightspaceContentAssessmentLabels(contentItem({ compliance_score: null, scan_status })),
        { score: 'Not scanned', fallbackStatus: 'Pending' },
        `${scan_status ?? 'null'} must not be presented as unassessed`
      );
    }
  });

  it('preserves measured zero and other scores regardless of scan status', () => {
    for (const [compliance_score, scan_status, score] of [[0, null, '0%'], [84.2, 'COMPLETED', '84%'], [100, 'COMPLETED', '100%']]) {
      assert.deepEqual(
        getBrightspaceContentAssessmentLabels(contentItem({ compliance_score, scan_status })),
        { score, fallbackStatus: 'Scanned' }
      );
    }
  });

  it('keeps persisted writeback status ahead of an unassessed fallback', () => {
    const item = contentItem({ compliance_score: null, scan_status: 'COMPLETED', writeback_status: 'written_back' });
    assert.equal(getBrightspaceContentAssessmentLabels(item).fallbackStatus, 'Unassessed');
    assert.deepEqual(resolveBrightspaceContentStatus(item), { label: 'Written back', variant: 'success' });
  });

  it('renders an automatic pending-review row from persisted provenance', () => {
    assert.deepEqual(
      resolveBrightspaceContentStatus(
        contentItem({ content_type: 'file', remediation_origin: 'automatic' })
      ),
      { label: 'Auto-remediated · pending review', variant: 'neutral' }
    );
  });

  it('renders a manual pending-review row from persisted provenance', () => {
    assert.deepEqual(
      resolveBrightspaceContentStatus(
        contentItem({ content_type: 'topic_html', remediation_origin: 'manual' })
      ),
      { label: 'Manually remediated · pending review', variant: 'neutral' }
    );
  });

  it('renders a neutral generic pending-review row for legacy null provenance', () => {
    assert.deepEqual(resolveBrightspaceContentStatus(contentItem()), {
      label: 'Remediated · pending review',
      variant: 'neutral',
    });
  });

  it('preserves terminal status badge behavior ahead of remediation provenance', () => {
    const cases = [
      ['approved', 'Approved', 'accent'],
      ['written_back', 'Written back', 'success'],
      ['stale', 'Stale', 'warning'],
      ['rolled_back', 'Rolled back', 'neutral'],
    ];

    for (const [writeback_status, label, variant] of cases) {
      assert.deepEqual(
        resolveBrightspaceContentStatus(
          contentItem({ writeback_status, remediation_origin: 'automatic' })
        ),
        { label, variant }
      );
    }
  });

  it('is consumed by BrightspaceContentPage for each persisted item', () => {
    const source = readFileSync(
      new URL('../../src/pages/BrightspaceContentPage.tsx', import.meta.url),
      'utf8'
    );
    assert.match(source, /resolveBrightspaceContentStatus\(item\)/);
    assert.match(source, /getBrightspaceContentAssessmentLabels\(item\)/);
    assert.match(source, /assessmentLabels\.score/);
    assert.match(source, /assessmentLabels\.fallbackStatus/);
    assert.doesNotMatch(source, /remediation_origin\s*\?[^:]*content_type|content_type\s*\?[^:]*remediation_origin/);
  });

  it('keeps pending-review and legacy remediated rows approval-eligible', () => {
    assert.equal(typeof brightspaceStatus.isBrightspaceContentApprovable, 'function');
    for (const writeback_status of [null, 'remediated', 'pending_review']) {
      assert.equal(
        brightspaceStatus.isBrightspaceContentApprovable(
          contentItem({ writeback_status })
        ),
        true,
        `${writeback_status ?? 'null'} should remain approval-eligible`
      );
    }
    assert.equal(
      brightspaceStatus.isBrightspaceContentApprovable(
        contentItem({
          writeback_status: 'remediated',
          has_remediated_version: false,
        })
      ),
      true,
      'legacy remediated slices without the new flag remain approval-eligible'
    );
  });

  it('does not make terminal writeback rows approval-eligible', () => {
    assert.equal(typeof brightspaceStatus.isBrightspaceContentApprovable, 'function');
    for (const writeback_status of ['approved', 'written_back', 'stale', 'rolled_back']) {
      assert.equal(
        brightspaceStatus.isBrightspaceContentApprovable(
          contentItem({ writeback_status })
        ),
        false,
        `${writeback_status} should remain terminal`
      );
    }
  });
});
