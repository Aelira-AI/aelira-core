import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { wcagBadgeLabel } from '../../src/utils/wcagCriterion.ts';
import {
  categoryLabel,
  fixMethodLabel,
  humanizeSlug,
  issueCategory,
  issueCountLabel,
} from '../../src/utils/displayText.ts';
import { derivePageLabel } from '../../src/utils/pageLabel.ts';
import { workingFileName } from '../../src/utils/remediationDownloadName.ts';

test('criterion badge never doubles the WCAG prefix', () => {
  assert.equal(wcagBadgeLabel('WCAG 1.3.1'), 'WCAG 1.3.1');
  assert.equal(wcagBadgeLabel('wcag 1.3.1'), 'WCAG 1.3.1');
  assert.equal(wcagBadgeLabel('1.3.1'), 'WCAG 1.3.1');
  assert.equal(wcagBadgeLabel('  WCAG   2.4.2 '), 'WCAG 2.4.2');
  assert.equal(wcagBadgeLabel('Matterhorn 09-004'), 'Matterhorn 09-004');
  assert.equal(wcagBadgeLabel(''), null);
  assert.equal(wcagBadgeLabel(undefined), null);
  assert.equal(wcagBadgeLabel({ unexpected: true }), null);
  const list = readFileSync(new URL('../../src/components/results/IssueList.tsx', import.meta.url), 'utf8');
  assert.match(list, /wcagBadgeLabel\(issue\.criterion \|\| issue\.wcag_criterion\)/);
  assert.doesNotMatch(list, /WCAG \{/);
});

test('issue counts use the singular for one', () => {
  assert.equal(issueCountLabel(0), '0 issues');
  assert.equal(issueCountLabel(1), '1 issue');
  assert.equal(issueCountLabel(2), '2 issues');
  const chart = readFileSync(new URL('../../src/components/charts/WCAGCriteriaChart.tsx', import.meta.url), 'utf8');
  assert.match(chart, /issueCountLabel\(item\.total\)/);
  assert.doesNotMatch(chart, /\{item\.total\} issues\)/);
});

test('the review breadcrumb never shows the scan id', () => {
  const id = '7ccea3f4-f870-47e3-beb6-39c8a1b2c3d4';
  assert.equal(derivePageLabel(`/review/${id}`), 'Document Review');
  assert.equal(derivePageLabel(`/scan/${id}`), 'Scan Details');
  assert.equal(derivePageLabel(`/remediate/${id}`), 'Remediate');
  // Any other route ending in an opaque id is labelled by the segment before it.
  assert.equal(derivePageLabel(`/reports/${id}`), 'Reports');
  assert.equal(derivePageLabel('/review-queue'), 'Review Queue');
  assert.equal(derivePageLabel('/some-page'), 'Some page');
});

test('a working file is named after the uploaded file, not after storage', () => {
  assert.equal(workingFileName('course-overview.docx', 'source_remediated.docx'), 'remediated-course-overview.docx');
  assert.equal(workingFileName('Syllabus Fall.pdf', 'source_remediated.pdf'), 'remediated-Syllabus Fall.pdf');
  assert.equal(workingFileName('syllabus.pdf', 'review-edit.pdf'), 'remediated-syllabus.pdf');
  assert.equal(workingFileName('teaching-clip.mp4', 'source_remediated.vtt'), 'teaching-clip-captions.vtt');
  assert.equal(workingFileName(undefined, 'source_remediated.docx'), 'remediated-document.docx');
  // A name that already reflects a real file is kept as the API gave it.
  assert.equal(workingFileName('a.pdf', 'Improved working file.pdf'), 'Improved working file.pdf');
  assert.equal(workingFileName('a.pdf', 'lecture_remediated.pdf'), 'lecture_remediated.pdf');
});

test('the review panel shows and saves the derived working file name', () => {
  const panel = readFileSync(new URL('../../src/components/review/ArtifactReviewPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /link\.download = workingName/);
  assert.match(panel, /\{workingName\} · SHA-256/);
  assert.doesNotMatch(panel, /\{artifact\.filename\} · SHA-256/);
  assert.doesNotMatch(panel, /link\.download = artifact\.filename/);
  const page = readFileSync(new URL('../../src/pages/DocumentReviewPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /fileName=\{review\.file_name\}/);
});

test('the Matterhorn bar is rendered for PDFs only', () => {
  const page = readFileSync(new URL('../../src/pages/DocumentReviewPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /review\.scan_type\.toLowerCase\(\) === 'pdf' && \(\s*<div className="shrink-0">\s*<MatterhornResultsBar/);
});

test('fix methods and categories show human labels for every recorded value', () => {
  assert.equal(fixMethodLabel('rule_based'), 'Rule-based');
  assert.equal(fixMethodLabel('rule'), 'Rule-based');
  assert.equal(fixMethodLabel('heuristic'), 'Heuristic');
  assert.equal(fixMethodLabel('ai'), 'AI');
  assert.equal(fixMethodLabel('ai_text'), 'AI Text');
  assert.equal(fixMethodLabel('ai_vision'), 'AI Vision');
  assert.equal(fixMethodLabel('ai_generated'), 'AI-generated');
  assert.equal(fixMethodLabel('template'), 'Template');
  assert.equal(fixMethodLabel('reviewed_recovery'), 'Reviewed recovery');
  assert.equal(fixMethodLabel('some_new_method'), 'Some New Method', 'an unmapped slug is still readable');
  assert.equal(humanizeSlug('WCAG 1.3.1'), 'WCAG 1.3.1', 'non-slugs are left alone');
  for (const [slug, label] of [['font_size', 'Font Size'], ['heading', 'Heading Structure'], ['title', 'Document Title']]) {
    assert.equal(categoryLabel(slug), label);
  }
  assert.equal(categoryLabel('totally_new'), 'Totally New');
});

test('the review chip uses the shared method labels', () => {
  const card = readFileSync(new URL('../../src/components/review/FixCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /\{fixMethodLabel\(fix\.fix_method\)\}/);
  assert.doesNotMatch(card, /METHOD_LABELS\[/);
});

test('document findings take their category from `category` when they have no `type`', () => {
  // The Word, PDF, Excel and PowerPoint scanners record `category` only.
  assert.equal(issueCategory({ category: 'font_size', severity: 'low' }), 'font_size');
  assert.equal(issueCategory({ category: 'heading' }), 'heading');
  // Web, code and LaTeX scanners record `type`, which keeps precedence.
  assert.equal(issueCategory({ type: 'missing_title', category: 'metadata' }), 'missing_title');
  assert.equal(issueCategory({}), 'other');
  assert.equal(issueCategory({ type: '  ', category: '' }), 'other');
  assert.equal(issueCategory({ type: 7, category: null }), 'other');
  const page = readFileSync(new URL('../../src/pages/Issues.tsx', import.meta.url), 'utf8');
  assert.match(page, /category: issueCategory\(rawIssue\)/);
});

test('every issue count on the history, dashboard, bulk upload and chart screens uses the singular', () => {
  const read = path => readFileSync(new URL(`../../src/${path}`, import.meta.url), 'utf8');
  const screens = [
    'pages/History.tsx', 'pages/Dashboard.tsx', 'pages/BulkUpload.tsx', 'pages/CourseOverview.tsx',
    'pages/Issues.tsx', 'components/charts/IssuesByTypeChart.tsx', 'components/charts/WCAGCriteriaChart.tsx',
  ];
  for (const path of screens) {
    const source = read(path);
    assert.match(source, /issueCountLabel\(/, `${path} formats counts with issueCountLabel`);
    // A count followed by a bare plural, in visible text or in an aria-label built from it.
    assert.doesNotMatch(source, /\}\s+issues\b(?!\s+can)/, `${path} has an unpluralised count`);
  }
  assert.match(read('components/AnalyticsDashboard.tsx'), /=== 1 \? '1 issue can'/);
  assert.equal(issueCountLabel(1), '1 issue');
  assert.equal(issueCountLabel(12), '12 issues');
});

test('the Issues page criterion reads every recorded field and never doubles the prefix', () => {
  const page = readFileSync(new URL('../../src/pages/Issues.tsx', import.meta.url), 'utf8');
  assert.match(page, /wcagBadgeLabel\(rawIssue\.criterion \|\| rawIssue\.wcag_criterion \|\| rawIssue\.wcag_criteria\)/);
  // Document findings record "WCAG 1.3.1" in wcag_criterion; web findings record a bare number in criterion.
  assert.equal(wcagBadgeLabel('WCAG 1.3.1'), 'WCAG 1.3.1');
  assert.equal(wcagBadgeLabel('1.4.3'), 'WCAG 1.4.3');
});
