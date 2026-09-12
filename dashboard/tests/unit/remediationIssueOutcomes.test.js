import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  outcomePresentation,
  pairIssuesWithFixes,
} from '../../src/utils/remediationIssueOutcomes.ts';

function fix(overrides) {
  return {
    id: 'fix-1',
    category: 'structure',
    severity: 'medium',
    description: 'PDF/UA identifier not set in XMP metadata',
    location: 'XMP metadata',
    page_number: 1,
    fix_method: 'rule',
    needs_review: false,
    review_status: 'auto_approved',
    ...overrides,
  };
}

describe('persisted remediation issue outcomes', () => {
  it('uses source-indexed outcomes to distinguish withheld changes from delivered fixes', () => {
    const rows = pairIssuesWithFixes([{ id: 'a', description: 'Title' }, { id: 'b', description: 'Heading' }], [], {
      total_issues: 2,
      issue_outcomes: [{ source_index: 0, issue_id: 'a', status: 'withheld' }, { source_index: 1, issue_id: 'b', status: 'manual' }],
    });
    assert.equal(outcomePresentation(rows[0].fix, rows[0].outcomeSource, rows[0].recordedOutcome).label, 'Change withheld · not delivered');
    assert.equal(outcomePresentation(rows[1].fix, rows[1].outcomeSource, rows[1].recordedOutcome).label, 'Manual remediation required');
  });

  it('does not attribute duplicate indices or mismatched source identities', () => {
    for (const outcomes of [
      [{ source_index: 0, status: 'fixed' }, { source_index: 0, status: 'manual' }],
      [{ source_index: 0, issue_id: 'different', status: 'fixed' }],
      [{ source_index: 1, status: 'fixed' }],
    ]) {
      const rows = pairIssuesWithFixes([{ id: 'source', description: 'Finding' }], [], { total_issues: 1, issue_outcomes: outcomes });
      assert.equal(rows[0].recordedOutcome, undefined);
      assert.equal(outcomePresentation(rows[0].fix, rows[0].outcomeSource, rows[0].recordedOutcome).label, 'Outcome not reported');
    }
  });

  it('recorded job outcomes override stale persisted approval records', () => {
    const rows = pairIssuesWithFixes([{ description: 'Title' }], [fix({ description: 'Title', location: undefined, page_number: null })], {
      total_issues: 1, issue_outcomes: [{ source_index: 0, source_index_scope: 'original_scan', status: 'withheld' }],
    });
    assert.equal(outcomePresentation(rows[0].fix, rows[0].outcomeSource, rows[0].recordedOutcome).label, 'Change withheld · not delivered');
  });

  it('matches an approved subset by exact ID rather than its reordered subset index', () => {
    const issues = [{ id: 'one', description: 'First' }, { id: 'two', description: 'Second' }];
    const rows = pairIssuesWithFixes(issues, [], {
      total_issues: 1,
      issue_outcomes: [{ source_index: 0, source_index_scope: 'approved_subset', issue_id: 'two', status: 'withheld' }],
    });
    assert.equal(rows[0].recordedOutcome, undefined);
    assert.equal(rows[1].recordedOutcome, 'withheld');
  });

  it('requires original-scan scope to attribute findings without IDs', () => {
    const issues = [{ description: 'Finding' }];
    for (const scope of [undefined, 'approved_subset', 'original_scan']) {
      const rows = pairIssuesWithFixes(issues, [], {
        total_issues: 1,
        issue_outcomes: [{ source_index: 0, source_index_scope: scope, status: 'manual' }],
      });
      assert.equal(rows[0].recordedOutcome, scope === 'original_scan' ? 'manual' : undefined);
    }
  });

  it('does not fall back to stale approvals when current job attribution is invalid', () => {
    const rows = pairIssuesWithFixes([{ description: 'Title' }], [fix({ description: 'Title', location: undefined, page_number: null })], {
      total_issues: 1,
      issue_outcomes: [{ source_index: 0, source_index_scope: 'approved_subset', status: 'fixed' }],
    });
    assert.equal(outcomePresentation(rows[0].fix, rows[0].outcomeSource, rows[0].recordedOutcome).label, 'Outcome not reported');
  });

  it('pairs the observed production findings with shuffled persisted fixes', () => {
    const issues = [
      { description: 'Document should start with H1 heading', location: 'Beginning of document', page_number: 1 },
      { description: 'PDF document title not set in metadata', location: 'Document metadata', page_number: 1 },
      { description: 'PDF/UA identifier not set in XMP metadata', location: 'XMP metadata', page_number: 1 },
    ];
    const fixes = [
      fix({ id: 'pdfua' }),
      fix({
        id: 'heading',
        category: 'heading',
        description: 'Document should start with H1 heading',
        location: 'Beginning of document',
        fix_method: 'heuristic',
        needs_review: true,
        review_status: 'pending',
      }),
      fix({
        id: 'title',
        category: 'title',
        description: 'PDF document title not set in metadata',
        location: 'Document metadata',
      }),
    ];

    const rows = pairIssuesWithFixes(issues, fixes);

    assert.deepEqual(rows.map((row) => row.fix?.id), ['heading', 'title', 'pdfua']);
    assert.equal(outcomePresentation(rows[0].fix).label, 'Fix proposed · review required');
    assert.equal(outcomePresentation(rows[1].fix).label, 'Change applied · verification not reported');
    assert.equal(outcomePresentation(rows[2].fix).label, 'Change applied · verification not reported');
  });

  it('consumes duplicate signatures one-to-one without inventing another match', () => {
    const issue = { description: 'Repeated finding', location: 'Page', page_number: 1 };
    const rows = pairIssuesWithFixes([issue, issue], [fix({ description: 'Repeated finding', location: 'Page' })]);

    assert.ok(rows[0].fix);
    assert.equal(rows[1].fix, undefined);
    assert.equal(outcomePresentation(rows[1].fix).label, 'Outcome not reported');
  });

  it('keeps unmatched persisted fixes visible as sourced rows', () => {
    const rows = pairIssuesWithFixes([], [fix({ id: 'persisted-only' })]);

    assert.equal(rows.length, 1);
    assert.equal(rows[0].fix?.id, 'persisted-only');
    assert.equal(rows[0].issue.description, 'PDF/UA identifier not set in XMP metadata');
  });

  it('renders failed and rejected persisted states without upgrading them to fixed', () => {
    assert.equal(outcomePresentation(fix({ review_status: 'apply_failed' })).label, 'Apply failed');
    assert.equal(outcomePresentation(fix({ review_status: 'rejected' })).label, 'Rejected in review');
  });

  it('does not present review approval as proof that a fix was applied', () => {
    assert.equal(outcomePresentation(fix({ review_status: 'approved' })).label, 'Approved for remediation');
    assert.equal(outcomePresentation(fix({ review_status: 'edited' })).label, 'Edited and approved');
  });

  it('reconciles a partial job only when every aggregate count proves attribution', () => {
    const issues = [
      { description: 'Fixed issue' },
      { description: 'Manual issue A' },
      { description: 'Manual issue B' },
    ];
    const rows = pairIssuesWithFixes(
      issues,
      [fix({ description: 'Fixed issue', location: undefined, page_number: null })],
      {
        total_issues: 3,
        fixed_count: 1,
        remaining_count: 2,
        manual_count: 2,
        failed_count: 0,
        skipped_count: 0,
      },
    );

    assert.deepEqual(rows.map((row) => row.outcomeSource), [
      'persisted_fix',
      'aggregate_manual',
      'aggregate_manual',
    ]);
    assert.equal(outcomePresentation(rows[1].fix, rows[1].outcomeSource).label, 'Manual remediation required');
  });

  it('leaves unmatched rows unreported when aggregate counts do not prove attribution', () => {
    const issues = [
      { description: 'Fixed issue' },
      { description: 'Ambiguous issue A' },
      { description: 'Ambiguous issue B' },
    ];
    const rows = pairIssuesWithFixes(
      issues,
      [fix({ description: 'Fixed issue', location: undefined, page_number: null })],
      {
        total_issues: 3,
        fixed_count: 1,
        remaining_count: 1,
        manual_count: 1,
        failed_count: 1,
        skipped_count: 0,
      },
    );

    assert.equal(rows[1].outcomeSource, 'unreported');
    assert.equal(rows[2].outcomeSource, 'unreported');
  });
});
