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

it('renders separate anchored groups and source-authored manual guidance in normal document flow', () => {
  const markup = render(RecordedOutcomeGroups, { rows });
  for (const text of ['Changes requiring human review (1)', 'Other applied changes (1)', 'Manual work remaining (1)', 'Candidate order was rolled back.', 'Correct the source order and rescan.', 'Page 9']) assert.ok(markup.includes(text));
  assert.match(markup, /href="#outcomes-review"/);
  assert.match(markup, /id="outcomes-manual"/);
  assert.doesNotMatch(markup, /overflow-y-auto|max-h-/);
});

it('renders saved automated checks independently from human approval and never infers missing proof', () => {
  const markup = render(RecordedOutcomeGroups, { rows });
  assert.match(markup, /Change applied · automated check passed/);
  assert.match(markup, /Human review required/);
  assert.match(markup, /Change applied · verification not reported/);
  assert.doesNotMatch(markup, /Approved for publication|fully compliant|certified/i);
});
