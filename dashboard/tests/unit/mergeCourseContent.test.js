/**
 * Unit tests for the course-content merge helper — DB status rows + live
 * Canvas files, unified into one list keyed by composite Canvas identity.
 * Uses Node.js native test runner (same pattern as featureAccess.test.js).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeCourseContent,
  groupContentByType,
  isRemediable,
  isApprovable,
  contentItemState,
  CONTENT_ITEM_STATE_COLOR,
} from '../../src/utils/mergeCourseContent.ts';

function statusItem(overrides = {}) {
  return {
    cloud_file_id: 'cf-1',
    provider_file_id: 'canvas-1',
    content_type: 'page',
    title: 'Syllabus',
    compliance_score: 92,
    issue_count: 1,
    writeback_status: null,
    has_remediated_version: false,
    remediation_origin: null,
    last_scanned_at: '2026-08-18T00:00:00Z',
    ...overrides,
  };
}

function liveFile(overrides = {}) {
  return {
    id: 'canvas-1',
    display_name: 'Notes.pdf',
    filename: 'Notes.pdf',
    content_type: 'application/pdf',
    size: 1024,
    ...overrides,
  };
}

describe('mergeCourseContent', () => {
  it('keeps page, assignment, and file with the same native id distinct', () => {
    const result = mergeCourseContent(
      [
        statusItem({
          cloud_file_id: 'cf-page',
          provider_file_id: '7',
          content_type: 'page',
        }),
        statusItem({
          cloud_file_id: 'cf-assignment',
          provider_file_id: '7',
          content_type: 'assignment',
        }),
      ],
      [liveFile({ id: '7' })],
      { provider: 'canvas', parentId: 'course-1' }
    );

    assert.equal(result.length, 3);
    assert.deepEqual(
      result.map((item) => item.content_type).sort(),
      ['assignment', 'file', 'page']
    );
  });

  it('keeps the same native id in different courses distinct', () => {
    const result = mergeCourseContent(
      [
        statusItem({
          cloud_file_id: 'cf-course-1',
          provider_file_id: '7',
          provider: 'canvas',
          provider_parent_id: 'course-1',
          content_type: 'page',
        }),
        statusItem({
          cloud_file_id: 'cf-course-2',
          provider_file_id: '7',
          provider: 'canvas',
          provider_parent_id: 'course-2',
          content_type: 'page',
        }),
      ],
      [],
      { provider: 'canvas', parentId: 'course-1' }
    );

    assert.equal(result.length, 2);
    assert.notEqual(result[0].identity_key, result[1].identity_key);
  });

  it('dedupes a matching live and DB file by composite identity', () => {
    const result = mergeCourseContent(
      [
        statusItem({
          provider_file_id: '7',
          provider: 'canvas',
          provider_parent_id: 'course-1',
          content_type: 'file',
          title: 'DB title',
        }),
      ],
      [liveFile({ id: '7', display_name: 'Live title' })],
      { provider: 'canvas', parentId: 'course-1' }
    );

    assert.equal(result.length, 1);
    assert.equal(result[0].title, 'DB title');
  });

  it('treats a legacy null content source as file for safe dedupe', () => {
    const result = mergeCourseContent(
      [
        statusItem({
          provider_file_id: '7',
          provider: 'canvas',
          provider_parent_id: 'course-1',
          content_type: null,
        }),
      ],
      [liveFile({ id: '7' })],
      { provider: 'canvas', parentId: 'course-1' }
    );

    assert.equal(result.length, 1);
    assert.equal(result[0].content_type, 'file');
  });

  it('passes DB-only items through unchanged, scan_status scanned', () => {
    const result = mergeCourseContent([statusItem()], []);
    assert.equal(result.length, 1);
    assert.equal(result[0].provider_file_id, 'canvas-1');
    assert.equal(result[0].title, 'Syllabus');
    assert.equal(result[0].scan_status, 'scanned');
    assert.equal(result[0].cloud_file_id, 'cf-1');
  });

  it('includes a live-only file (no DB row) as unscanned', () => {
    const result = mergeCourseContent([], [liveFile({ id: 'canvas-99' })]);
    assert.equal(result.length, 1);
    assert.equal(result[0].provider_file_id, 'canvas-99');
    assert.equal(result[0].cloud_file_id, null);
    assert.equal(result[0].scan_status, 'unscanned');
    assert.equal(result[0].content_type, 'file');
    assert.equal(result[0].title, 'Notes.pdf');
  });

  it('DB row wins when the same provider_file_id appears in both sources', () => {
    const result = mergeCourseContent(
      [statusItem({ provider_file_id: 'canvas-1', title: 'DB Title', content_type: 'file' })],
      [liveFile({ id: 'canvas-1', display_name: 'Live Title' })]
    );
    assert.equal(result.length, 1);
    assert.equal(result[0].title, 'DB Title');
    assert.equal(result[0].scan_status, 'scanned');
  });

  it('dedupes — a matched item never produces two rows', () => {
    const result = mergeCourseContent(
      [statusItem({ provider_file_id: 'canvas-1', content_type: 'file' })],
      [liveFile({ id: 'canvas-1' }), liveFile({ id: 'canvas-2' })]
    );
    assert.equal(result.length, 2);
    const ids = result.map((r) => r.provider_file_id).sort();
    assert.deepEqual(ids, ['canvas-1', 'canvas-2']);
  });

  it('returns an empty list when both sources are empty', () => {
    assert.deepEqual(mergeCourseContent([], []), []);
  });

  it('degrades gracefully when the live files call failed (null)', () => {
    const result = mergeCourseContent([statusItem()], null);
    assert.equal(result.length, 1);
    assert.equal(result[0].provider_file_id, 'canvas-1');
  });

  it('degrades gracefully when the live files call failed (undefined)', () => {
    const result = mergeCourseContent([statusItem()], undefined);
    assert.equal(result.length, 1);
  });

  it('handles a missing/null statusItems list — live files still show', () => {
    const result = mergeCourseContent(null, [liveFile({ id: 'canvas-5' })]);
    assert.equal(result.length, 1);
    assert.equal(result[0].scan_status, 'unscanned');
  });

  it('keeps non-file content types alongside file entries in one list', () => {
    const result = mergeCourseContent(
      [statusItem({ provider_file_id: 'p-1', content_type: 'page' })],
      [liveFile({ id: 'f-1' })]
    );
    assert.equal(result.length, 2);
    const types = result.map((r) => r.content_type).sort();
    assert.deepEqual(types, ['file', 'page']);
  });

  it('falls back to cloud_file_id as the key when provider_file_id is missing', () => {
    const result = mergeCourseContent(
      [statusItem({ provider_file_id: null, cloud_file_id: 'cf-legacy' })],
      []
    );
    assert.equal(result.length, 1);
    assert.equal(result[0].provider_file_id, 'cf-legacy');
  });

  it('marks a DB row with no last_scanned_at as unscanned', () => {
    const result = mergeCourseContent(
      [statusItem({ last_scanned_at: null })],
      []
    );
    assert.equal(result[0].scan_status, 'unscanned');
  });
});

function mergedItem(overrides = {}) {
  return {
    provider_file_id: 'canvas-1',
    cloud_file_id: 'cf-1',
    title: 'Item',
    content_type: 'page',
    compliance_score: 90,
    issue_count: 1,
    writeback_status: null,
    has_remediated_version: false,
    remediation_origin: null,
    last_scanned_at: '2026-08-18T00:00:00Z',
    content_updated_at: null,
    scan_id: 'scan-1',
    scan_status: 'scanned',
    ...overrides,
  };
}

describe('groupContentByType', () => {
  it('groups items by content_type and counts total per group', () => {
    const result = groupContentByType([
      mergedItem({ provider_file_id: 'p-1', content_type: 'page' }),
      mergedItem({ provider_file_id: 'p-2', content_type: 'page' }),
      mergedItem({ provider_file_id: 'f-1', content_type: 'file' }),
    ]);
    const byType = Object.fromEntries(result.map((r) => [r.content_type, r]));
    assert.equal(byType.page.total, 2);
    assert.equal(byType.file.total, 1);
  });

  it('excludes unscanned rows from the scanned count', () => {
    const result = groupContentByType([
      mergedItem({ provider_file_id: 'p-1', content_type: 'file', compliance_score: 88 }),
      mergedItem({
        provider_file_id: 'p-2',
        content_type: 'file',
        compliance_score: null,
        scan_status: 'unscanned',
      }),
    ]);
    assert.equal(result[0].total, 2);
    assert.equal(result[0].scanned, 1);
  });

  it('excludes unscanned rows from the average_compliance calculation', () => {
    const result = groupContentByType([
      mergedItem({ provider_file_id: 'p-1', content_type: 'file', compliance_score: 100 }),
      mergedItem({
        provider_file_id: 'p-2',
        content_type: 'file',
        compliance_score: null,
        scan_status: 'unscanned',
      }),
    ]);
    // Average must be 100 (only the scanned item), not diluted by the
    // unscanned item as if it scored 0.
    assert.equal(result[0].average_compliance, 100);
  });

  it('returns null average_compliance when nothing in the group is scanned', () => {
    const result = groupContentByType([
      mergedItem({
        provider_file_id: 'p-1',
        content_type: 'file',
        compliance_score: null,
        scan_status: 'unscanned',
      }),
    ]);
    assert.equal(result[0].average_compliance, null);
  });

  it('sums issue_count per group', () => {
    const result = groupContentByType([
      mergedItem({ provider_file_id: 'p-1', content_type: 'page', issue_count: 3 }),
      mergedItem({ provider_file_id: 'p-2', content_type: 'page', issue_count: 2 }),
    ]);
    assert.equal(result[0].issues, 5);
  });

  it('returns an empty list for empty input', () => {
    assert.deepEqual(groupContentByType([]), []);
  });
});

describe('isRemediable', () => {
  it('is true for a scanned item with issues, no remediated version, and a scan_id', () => {
    assert.equal(isRemediable(mergedItem()), true);
  });

  it('is false when unscanned (compliance_score null)', () => {
    assert.equal(
      isRemediable(mergedItem({ compliance_score: null, scan_status: 'unscanned' })),
      false
    );
  });

  it('is false when there are no issues', () => {
    assert.equal(isRemediable(mergedItem({ issue_count: 0 })), false);
  });

  it('is false when already remediated', () => {
    assert.equal(isRemediable(mergedItem({ has_remediated_version: true })), false);
  });

  it('is false when there is no scan_id to remediate against', () => {
    assert.equal(isRemediable(mergedItem({ scan_id: null })), false);
  });
});

describe('isApprovable', () => {
  // The reported incident: a remediated item with writeback_status: null
  // (never touched) must be approvable.
  it('is true when remediated and writeback_status is null', () => {
    assert.equal(
      isApprovable(mergedItem({ has_remediated_version: true, writeback_status: null })),
      true
    );
  });

  // The actual bug: 'pending_review' is the state approval exists to act
  // on, but the old `!item.writeback_status` check excluded it.
  it('is true when remediated and writeback_status is pending_review', () => {
    assert.equal(
      isApprovable(
        mergedItem({ has_remediated_version: true, writeback_status: 'pending_review' })
      ),
      true
    );
  });

  it('is false when writeback_status is approved (already done)', () => {
    assert.equal(
      isApprovable(mergedItem({ has_remediated_version: true, writeback_status: 'approved' })),
      false
    );
  });

  it('is false when writeback_status is written_back (already done)', () => {
    assert.equal(
      isApprovable(
        mergedItem({ has_remediated_version: true, writeback_status: 'written_back' })
      ),
      false
    );
  });

  it('is false when writeback_status is rejected', () => {
    assert.equal(
      isApprovable(mergedItem({ has_remediated_version: true, writeback_status: 'rejected' })),
      false
    );
  });

  it('is false when there is no remediation available at all', () => {
    assert.equal(
      isApprovable(mergedItem({ has_remediated_version: false, writeback_status: null })),
      false
    );
  });

  it('is true for a file-type item (has_remediated_version but no HTML body)', () => {
    // isApprovable only checks has_remediated_version, not remediated_body
    // (which mergeCourseContent's MergedContentItem doesn't even carry) —
    // this is the server-side half of the same bug class, verified here
    // client-side: a remediated file must be just as approvable as a
    // remediated HTML page.
    assert.equal(
      isApprovable(
        mergedItem({
          content_type: 'file',
          has_remediated_version: true,
          writeback_status: null,
        })
      ),
      true
    );
  });

  // Found while wiring up contentItemState(): rollback_content restores the
  // original content_body but never clears has_remediated_version, so
  // without 'rolled_back' in TERMINAL_WRITEBACK_STATUSES this item would
  // still read as approvable — silently re-offering "Approve All" on
  // something a human deliberately rolled back. A rollback demands fresh
  // review, not a re-approve.
  it('is false when writeback_status is rolled_back (has_remediated_version stays true after rollback)', () => {
    assert.equal(
      isApprovable(
        mergedItem({ has_remediated_version: true, writeback_status: 'rolled_back' })
      ),
      false
    );
  });
});

describe('contentItemState', () => {
  it('is unscanned when compliance_score is null', () => {
    const state = contentItemState(
      mergedItem({ compliance_score: null, scan_status: 'unscanned' })
    );
    assert.equal(state.key, 'unscanned');
    assert.equal(state.label, 'Unscanned');
  });

  it('is needs_remediation when scanned, has issues, no remediation yet', () => {
    const state = contentItemState(
      mergedItem({ issue_count: 3, has_remediated_version: false, writeback_status: null })
    );
    assert.equal(state.key, 'needs_remediation');
    assert.equal(state.label, 'Scanned · needs remediation');
  });

  it('uses persisted automatic origin for a remediated file without content-type inference', () => {
    const state = contentItemState(
      mergedItem({
        content_type: 'file',
        has_remediated_version: true,
        remediation_origin: 'automatic',
      })
    );
    assert.deepEqual(state, {
      key: 'auto_remediated_pending_review',
      label: 'Auto-remediated · pending review',
    });
  });

  it('uses persisted manual origin for otherwise identical remediated content', () => {
    const state = contentItemState(
      mergedItem({
        content_type: 'file',
        has_remediated_version: true,
        remediation_origin: 'manual',
      })
    );
    assert.deepEqual(state, {
      key: 'remediated_pending_review',
      label: 'Manually remediated · pending review',
    });
  });

  it('shows a neutral generic remediation label for legacy null origin', () => {
    const state = contentItemState(
      mergedItem({
        content_type: 'page',
        has_remediated_version: true,
        remediation_origin: null,
      })
    );
    assert.deepEqual(state, {
      key: 'remediated_pending_review',
      label: 'Remediated · pending review',
    });
  });

  it('carries persisted remediation origin through the course-content merge', () => {
    const [item] = mergeCourseContent(
      [statusItem({ remediation_origin: 'automatic', has_remediated_version: true })],
      []
    );
    assert.equal(item.remediation_origin, 'automatic');
  });

  it('is approved when writeback_status is approved', () => {
    const state = contentItemState(
      mergedItem({ has_remediated_version: true, writeback_status: 'approved' })
    );
    assert.equal(state.key, 'approved');
    assert.equal(state.label, 'Approved');
  });

  it('is written_back when writeback_status is written_back', () => {
    const state = contentItemState(
      mergedItem({ has_remediated_version: true, writeback_status: 'written_back' })
    );
    assert.equal(state.key, 'written_back');
    assert.equal(state.label, 'Written back');
  });

  it('is written_back for the writtenback spelling variant too', () => {
    const state = contentItemState(
      mergedItem({ has_remediated_version: true, writeback_status: 'writtenback' })
    );
    assert.equal(state.key, 'written_back');
  });

  it('is rejected when writeback_status is rejected', () => {
    const state = contentItemState(
      mergedItem({ has_remediated_version: true, writeback_status: 'rejected' })
    );
    assert.equal(state.key, 'rejected');
    assert.equal(state.label, 'Rejected');
  });

  it('is compliant when scanned with zero issues and no remediation', () => {
    const state = contentItemState(
      mergedItem({ issue_count: 0, has_remediated_version: false, writeback_status: null })
    );
    assert.equal(state.key, 'compliant');
    assert.equal(state.label, 'Compliant');
  });

  it('terminal writeback states win over has_remediated_version', () => {
    // A remediated file that's already been approved must read "Approved",
    // not "Remediated · pending review" — the review already happened.
    const state = contentItemState(
      mergedItem({
        content_type: 'file',
        has_remediated_version: true,
        writeback_status: 'approved',
      })
    );
    assert.equal(state.key, 'approved');
  });

  it('is rolled_back when writeback_status is rolled_back, even with has_remediated_version still true', () => {
    // Rollback restores original content_body but never clears
    // has_remediated_version — this must not misread as pending review.
    const state = contentItemState(
      mergedItem({
        has_remediated_version: true,
        writeback_status: 'rolled_back',
      })
    );
    assert.equal(state.key, 'rolled_back');
    assert.equal(state.label, 'Rolled back');
  });

  it('uses neutral styling for both pending-remediation states', () => {
    assert.equal(CONTENT_ITEM_STATE_COLOR.auto_remediated_pending_review, 'neutral');
    assert.equal(CONTENT_ITEM_STATE_COLOR.remediated_pending_review, 'neutral');
  });

  it('has a color mapping for every possible state key', () => {
    const keys = [
      'unscanned',
      'needs_remediation',
      'auto_remediated_pending_review',
      'remediated_pending_review',
      'approved',
      'written_back',
      'rejected',
      'rolled_back',
      'compliant',
    ];
    for (const key of keys) {
      assert.ok(CONTENT_ITEM_STATE_COLOR[key], `missing color for ${key}`);
    }
  });

  it('uses neutral styling for both pending-remediation provenance states', () => {
    assert.equal(CONTENT_ITEM_STATE_COLOR.auto_remediated_pending_review, 'neutral');
    assert.equal(CONTENT_ITEM_STATE_COLOR.remediated_pending_review, 'neutral');
  });
});
