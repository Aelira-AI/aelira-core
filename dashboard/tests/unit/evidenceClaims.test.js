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

const { ConfidenceBadge } = await import('../../src/components/review/ConfidenceBadge.tsx');
const { IssueList } = await import('../../src/components/results/IssueList.tsx');
const { IssuesByTypeChart } = await import('../../src/components/charts/IssuesByTypeChart.tsx');
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

it('unknown and invalid confidence never render a numeric certainty', () => {
  for (const confidence of [null, undefined, NaN, Infinity, -0.1, 1.1, '1']) {
    const markup = render(ConfidenceBadge, { confidence });
    assert.match(markup, /Confidence not reported/);
    assert.doesNotMatch(markup, /\d+%|NaN/);
  }
});

it('reported zero and other valid scores retain their values and explicit meaning', () => {
  for (const [confidence, expected] of [[0, '0%'], [0.55, '55%'], [1, '100%']]) {
    const markup = render(ConfidenceBadge, { confidence });
    assert.ok(markup.includes(expected));
    assert.match(markup, /Reported confidence/);
    assert.doesNotMatch(markup, /Confidence not reported/);
  }
});

it('empty document and website findings describe scanner limits', () => {
  for (const scanType of ['word', 'website']) {
    const markup = render(IssueList, { issues: [], scanType });
    assert.match(markup, /No issues detected by this scan/);
    assert.match(markup, /does not establish accessibility conformance/);
    assert.doesNotMatch(markup, /fully accessible|fully compliant/i);
  }
});

it('the empty severity chart does not assert conformance', () => {
  const markup = render(IssuesByTypeChart, { issues: [] });
  assert.match(markup, /No issues detected by this scan/);
  assert.match(markup, /does not establish accessibility conformance/);
  assert.doesNotMatch(markup, /fully accessible|fully compliant/i);
});
