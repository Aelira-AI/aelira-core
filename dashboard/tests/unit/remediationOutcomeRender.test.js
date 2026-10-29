import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL?.match(/\.tsx?$/) && !/\.tsx?$/.test(specifier)) {
      for (const extension of ['.ts', '.tsx']) {
        const candidate = new URL(`${specifier}${extension}`, context.parentURL);
        if (existsSync(fileURLToPath(candidate))) return { shortCircuit: true, url: candidate.href };
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (/\.tsx?$/.test(url)) {
      return {
        format: 'module', shortCircuit: true,
        source: ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), {
          compilerOptions: { esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});

const { AggregateResults, RecordedOutcomeGroups } = await import('../../src/components/results/RemediationOutcomeBreakdown.tsx');
const { pairIssuesWithFixes } = await import('../../src/utils/remediationIssueOutcomes.ts');

const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const rows = [
  { issue: { description: 'Source image' }, outcomeSource: 'recorded_job', recordedOutcome: 'fixed', recordedDetails: { status: 'fixed', needs_review: true, verification_passed: true, verification_scope: 'saved_file_finding' } },
  { issue: { description: 'Document root' }, outcomeSource: 'recorded_job', recordedOutcome: 'fixed', recordedDetails: { status: 'fixed', needs_review: false } },
  { issue: { description: 'Complex reading order', page_number: 9 }, outcomeSource: 'recorded_job', recordedOutcome: 'manual', recordedDetails: { status: 'manual', reason: 'Candidate order was rolled back.', attempt: 'not_applied', next_step: 'Correct the source order and rescan.' } },
];

it('renders delivered change totals and review count even when historical score verification fails', () => {
  const markup = render(AggregateResults, { job: { fixed_count: 12, total_issues: 17, remaining_count: 5, manual_count: 5, score_verified: false }, rows });
  for (const text of ['12', 'Changes applied', 'Human review changes', 'Manual work remaining', '17']) assert.ok(markup.includes(text));
  assert.doesNotMatch(render(AggregateResults, { job: {} }), /Changes applied|Human review changes/);
});

it('counts accepted slide orders without claiming outcomes for three original scan findings', () => {
  const findings = [
    { id: 'alt-text', description: 'Image missing alternative text' },
    { id: 'slide-1-title', description: 'Slide 1 missing title' },
    { id: 'slide-2-title', description: 'Slide 2 missing title' },
  ];
  const job = {
    fixed_count: 1, manual_count: 0, remaining_count: 0, total_issues: 1,
    outcome_unreported_count: 0, original_score: 49, remediated_score: 49,
    issue_outcomes: [{ source_index: 0, source_index_scope: 'accepted_pptx_order',
      issue_id: 'pptx-reading-order-0', status: 'fixed', needs_review: true }],
  };
  const rows = pairIssuesWithFixes(findings, [{ id: 'order-fix', category: 'reading_order',
    description: 'Reviewer accepted a complete slide shape order', needs_review: true,
    review_status: 'pending' }], job);
  assert.equal(rows.length, 4);
  assert.ok(rows.slice(0, 3).every(row => row.outcomeSource === 'unreported'));
  const markup = render(AggregateResults, { job, rows });
  for (const [count, label] of [[1, 'Slide orders applied'], [1, 'Slide orders needing human review'],
    [0, 'Order requests not applied'], [1, 'Accepted slide order requests']]) {
    assert.match(markup, new RegExp(`>${count}</p><p[^>]*>${label}</p>`));
  }
  assert.doesNotMatch(markup, /Total issues|Manual work remaining|Remaining</);
  assert.match(render(RecordedOutcomeGroups, { rows }), /Withheld, failed or unreported outcomes \(3\)/);

  const unknownReview = { ...job, issue_outcomes: [{ ...job.issue_outcomes[0], needs_review: undefined }] };
  assert.doesNotMatch(render(AggregateResults, { job: unknownReview, rows }), /Slide orders needing human review/);
});

it('retains finding summary labels for original scans and approved subsets', () => {
  for (const scope of ['original_scan', 'approved_subset']) {
    const markup = render(AggregateResults, { job: { fixed_count: 1, remaining_count: 2,
      total_issues: 3, manual_count: 2, issue_outcomes: [{ source_index: 0,
        source_index_scope: scope, status: 'fixed' }] }, rows });
    for (const label of ['Changes applied', 'Manual work remaining', 'Remaining', 'Total issues']) {
      assert.ok(markup.includes(label));
    }
    assert.doesNotMatch(markup, /Accepted slide order requests/);
  }
});

it('renders separate anchored groups and source-authored manual guidance in normal document flow', () => {
  const markup = render(RecordedOutcomeGroups, { rows });
  for (const text of ['Changes requiring human review (1)', 'Other applied changes (1)', 'Manual work remaining (1)', 'Candidate order was rolled back.', 'Correct the source order and rescan.', 'Page 9']) assert.ok(markup.includes(text));
  assert.match(markup, /href="#outcomes-review"/);
  assert.match(markup, /id="outcomes-manual"/);
  assert.doesNotMatch(markup, /overflow-y-auto|max-h-/);
});

it('renders saved automated checks independently from human approval and never infers missing proof', () => {
  const markup = render(RecordedOutcomeGroups, { rows });
  assert.match(markup, /Change applied · finding no longer detected · human review required/);
  assert.doesNotMatch(markup, /automated check passed/);
  assert.match(markup, /Human review required/);
  assert.match(markup, /Change applied · verification not reported/);
  assert.doesNotMatch(markup, /Approved for publication|fully compliant|certified/i);
});

it('shows readable categories under finding titles, never the raw slug', () => {
  const slugRows = ['font_size', 'heading', 'title'].map(category => ({
    issue: { description: `Recorded finding ${category.length}`, category },
    outcomeSource: 'recorded_job', recordedOutcome: 'fixed', recordedDetails: { status: 'fixed', needs_review: false },
  }));
  const markup = render(RecordedOutcomeGroups, { rows: slugRows });
  for (const label of ['Font Size', 'Heading Structure', 'Document Title']) assert.ok(markup.includes(`>${label}<`), label);
  for (const slug of ['font_size', '>heading<', '>title<']) assert.ok(!markup.includes(slug), slug);
});
