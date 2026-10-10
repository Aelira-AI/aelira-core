import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  outcomePresentation,
  pairIssuesWithFixes,
  outcomeExplanation,
  findingDisplayText,
  outcomeGroup,
  reviewRequirement,
} from '../../src/utils/remediationIssueOutcomes.ts';

function fix(overrides) {
  return {
    id: 'fix-1',
    category: 'structure',
    severity: 'medium',
    description: 'Metadata identifier is absent',
    location: 'XMP metadata',
    page_number: 1,
    fix_method: 'rule',
    needs_review: false,
    review_status: 'auto_approved',
    ...overrides,
  };
}

describe('persisted remediation issue outcomes', () => {
  it('separates saved-file checks from human review and manual work', () => {
    const issues = [{ id: 'image', description: 'Image' }, { id: 'root', description: 'Root' }, { id: 'heading', description: 'Heading' }];
    const rows = pairIssuesWithFixes(issues, [], {
      total_issues: 3, issue_outcomes: [
        { source_index: 0, issue_id: 'image', status: 'fixed', verification_passed: true, verification_scope: 'saved_file_finding', needs_review: true },
        { source_index: 1, issue_id: 'root', status: 'fixed', verification_passed: true, verification_scope: 'saved_file_finding', needs_review: false },
        { source_index: 2, issue_id: 'heading', status: 'manual' },
      ],
    });
    assert.deepEqual(rows.map(outcomeGroup), ['review', 'applied', 'manual']);
    assert.equal(reviewRequirement(rows[0]), 'Human review required');
    assert.equal(reviewRequirement(rows[1]), null);
    assert.equal(outcomePresentation(undefined, rows[0].outcomeSource, rows[0].recordedOutcome, rows[0].recordedDetails).label, 'Change applied · automated check passed');
  });

  it('does not infer saved-file verification or approval from applied status or a bare boolean', () => {
    for (const fields of [{}, { verification_passed: true }, { verification_passed: false, verification_scope: 'saved_file_finding' }]) {
      const row = { issue: { description: 'Image' }, outcomeSource: 'recorded_job', recordedOutcome: 'fixed', recordedDetails: { status: 'fixed', ...fields } };
      assert.equal(outcomePresentation(undefined, row.outcomeSource, row.recordedOutcome, row.recordedDetails).label, 'Change applied · verification not reported');
      assert.equal(reviewRequirement(row), 'Human review requirement not recorded');
    }
  });

  it('a stale pending fix never classifies a withheld job change as applied or reviewable', () => {
    const [row] = pairIssuesWithFixes([{ description: 'Image' }], [fix({ description: 'Image', location: undefined, page_number: null, needs_review: true, review_status: 'pending' })], {
      total_issues: 1, issue_outcomes: [{ source_index: 0, source_index_scope: 'original_scan', status: 'withheld', needs_review: true }],
    });
    assert.equal(outcomeGroup(row), 'other');
    assert.equal(reviewRequirement(row), null);
  });
  it('shows recorded reasons and attempts only for the exact source finding', () => {
    const rows = pairIssuesWithFixes([{ id: 'one', description: 'Heading' }, { id: 'two', description: 'Root' }], [], {
      total_issues: 2,
      issue_outcomes: [{ source_index: 0, issue_id: 'one', status: 'manual',
        reason: 'Distinct source run unavailable.', next_step: 'Correct the source heading.', attempt: 'not_applied' },
      { source_index: 1, issue_id: 'two', status: 'withheld', reason: 'Output verification failed.',
        next_step: 'Review the findings.', attempt: 'candidate_change' }],
    });
    assert.equal(outcomeExplanation(rows[0]).reason, 'Distinct source run unavailable.');
    assert.equal(outcomeExplanation(rows[1]).attempt, 'A change was attempted in a candidate file.');
    const ambiguous = pairIssuesWithFixes([{ id: 'one', description: 'Heading' }], [], {
      total_issues: 1, issue_outcomes: [{ source_index: 0, issue_id: 'other', status: 'manual', reason: 'Wrong reason' }],
    });
    assert.equal(outcomeExplanation(ambiguous[0]), null);
  });

  it('explains missing historical evidence without guessing the original refusal', () => {
    const [row] = pairIssuesWithFixes([{ description: 'Image', suggested_fix: 'Supply an accurate description.' }], [], {
      total_issues: 1, issue_outcomes: [{ source_index: 0, source_index_scope: 'original_scan', status: 'manual' }],
    });
    assert.match(outcomeExplanation(row).reason, /did not save its detailed reason/);
    assert.equal(outcomeExplanation(row).nextStep, 'Supply an accurate description.');
  });

  it('removes the legacy pending claim for display without changing source attribution', () => {
    const legacy = 'Image missing alternative text - AI analysis pending';
    assert.equal(findingDisplayText(legacy), 'Image missing alternative text');
    const [row] = pairIssuesWithFixes([{ message: legacy }], [fix({ description: legacy, location: undefined, page_number: null })]);
    assert.ok(row.fix);
    assert.equal(row.issue.message, legacy);
  });
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

  it('maps a verified approved subset back to no-ID original rows without inventing unselected outcomes', () => {
    const issues = Array.from({ length: 17 }, (_, index) => ({ description: `Finding ${index}` }));
    const originalIndices = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 16];
    const rows = pairIssuesWithFixes(issues, [], { total_issues: 12,
      issue_outcomes: originalIndices.map((original, selected) => ({
        source_index: selected, source_index_scope: 'approved_subset', issue_id: `source-${original}`,
        original_source_index: original, status: 'fixed', verification_passed: true,
        verification_scope: 'saved_file_finding', needs_review: selected < 4,
      })),
    });
    assert.equal(rows.filter(row => row.recordedOutcome === 'fixed').length, 12);
    assert.equal(rows.filter(row => row.outcomeSource === 'unreported').length, 5);
    assert.deepEqual(rows.flatMap((row, index) => row.recordedOutcome === 'fixed' ? [index] : []), originalIndices);
    assert.equal(rows.filter(row => outcomeGroup(row) === 'manual').length, 0);
  });

  it('rejects unverified, duplicate, forged-ID and malformed original-index projections', () => {
    const valid = { source_index: 0, source_index_scope: 'approved_subset', issue_id: 'source-1',
      original_source_index: 1, status: 'fixed', verification_passed: true, verification_scope: 'saved_file_finding' };
    for (const outcomes of [
      [{ ...valid, verification_passed: false }], [{ ...valid, verification_scope: undefined }],
      [{ ...valid, status: 'manual' }], [{ ...valid, original_source_index: '1' }],
      [{ ...valid, original_source_index: -1 }], [{ ...valid, original_source_index: 99 }],
      [valid, { ...valid, source_index: 1 }],
    ]) {
      const rows = pairIssuesWithFixes([{ description: 'First' }, { description: 'Second' }], [], { total_issues: 1, issue_outcomes: outcomes });
      assert.ok(rows.every(row => row.outcomeSource === 'unreported'));
    }
    const rows = pairIssuesWithFixes([{ id: 'first' }, { id: 'actual-second' }], [], { total_issues: 1, issue_outcomes: [valid] });
    assert.ok(rows.every(row => row.outcomeSource === 'unreported'));
  });

  it('does not fall back to stale approvals when current job attribution is invalid', () => {
    const rows = pairIssuesWithFixes([{ description: 'Title' }], [fix({ description: 'Title', location: undefined, page_number: null })], {
      total_issues: 1,
      issue_outcomes: [{ source_index: 0, source_index_scope: 'approved_subset', status: 'fixed' }],
    });
    assert.equal(outcomePresentation(rows[0].fix, rows[0].outcomeSource, rows[0].recordedOutcome).label, 'Outcome not reported');
  });

  it('pairs synthetic findings with shuffled persisted fixes', () => {
    const issues = [
      { description: 'Heading order needs review', location: 'Beginning of document', page_number: 1 },
      { description: 'Document title is absent', location: 'Document metadata', page_number: 1 },
      { description: 'Metadata identifier is absent', location: 'XMP metadata', page_number: 1 },
    ];
    const fixes = [
      fix({ id: 'pdfua' }),
      fix({
        id: 'heading',
        category: 'heading',
        description: 'Heading order needs review',
        location: 'Beginning of document',
        fix_method: 'heuristic',
        needs_review: true,
        review_status: 'pending',
      }),
      fix({
        id: 'title',
        category: 'title',
        description: 'Document title is absent',
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
    assert.equal(rows[0].issue.description, 'Metadata identifier is absent');
  });

  it('renders failed and rejected persisted states without upgrading them to fixed', () => {
    assert.equal(outcomePresentation(fix({ review_status: 'apply_failed' })).label, 'Apply failed');
    assert.equal(outcomePresentation(fix({ review_status: 'rejected' })).label, 'Rejected in review');
  });

  it('reconciles a synthetic eight-issue job without inventing ambiguous outcomes', () => {
    const issues = [
      { description: 'Heading order needs review' },
      { description: 'Document language is absent' },
      { description: 'Document title is absent' },
      { description: 'Document structure is absent' },
      { description: 'List lacks structure tags' },
      { description: 'First table lacks structure tags' },
      { description: 'Second table lacks structure tags' },
      { description: 'Third table lacks structure tags' },
    ];
    const fixes = issues.slice(0, 4).map((issue, index) => fix({
      id: `fix-${index}`,
      description: issue.description,
      location: undefined,
      page_number: null,
    }));
    const rows = pairIssuesWithFixes(issues, fixes, {
      total_issues: 8,
      fixed_count: 4,
      remaining_count: 4,
      manual_count: 4,
      failed_count: 0,
      skipped_count: 0,
    });

    assert.equal(rows.length, 8);
    assert.deepEqual(rows.map((row) => row.outcomeSource), [
      'persisted_fix',
      'persisted_fix',
      'persisted_fix',
      'persisted_fix',
      'aggregate_manual',
      'aggregate_manual',
      'aggregate_manual',
      'aggregate_manual',
    ]);
    assert.equal(outcomePresentation(rows[4].fix, rows[4].outcomeSource).label, 'Manual remediation required');
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
