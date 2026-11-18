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

const { TrendEmptyState, TREND_NEUTRAL_MESSAGE } = await import('../../src/components/TrendEmptyState.tsx');

const render = (props) => renderToStaticMarkup(React.createElement(TrendEmptyState, props));

it('explains the v0.10.0 restart when earlier history was withheld', () => {
  const markup = render({ hasWithheldHistory: true });
  assert.match(markup, /Trend tracking restarted in v0\.10\.0 with a corrected method\./);
  assert.match(markup, /counted unassessed items as 0%, so they are not shown here\./);
  assert.match(markup, /Predictions return after 7 days of new data\./);
  assert.ok(!markup.includes(TREND_NEUTRAL_MESSAGE));
});

it('shows the neutral insufficient-data message when nothing was withheld', () => {
  const markup = render({ hasWithheldHistory: false });
  assert.match(markup, /Not enough data yet\. Upload more scans to see progress\./);
  assert.ok(!markup.includes('v0.10.0'));
});

it('carries the message as visible plain text, never hidden or color-only', () => {
  const markup = render({ hasWithheldHistory: true });
  assert.match(markup, /^<p\b/);
  assert.ok(!markup.includes('aria-hidden'));
  assert.ok(!markup.includes('sr-only'));
  // Body-text token (AA) instead of the tertiary hint token.
  assert.match(markup, /text-\[var\(--content-secondary\)\]/);
});

it('the dashboard trend chart switches on the withheld-history flag', () => {
  const dashboard = readFileSync(new URL('../../src/pages/Dashboard.tsx', import.meta.url), 'utf8');
  assert.match(dashboard, /trendResponse\?\.has_withheld_history === true/);
  assert.match(dashboard, /<TrendEmptyState hasWithheldHistory=\{trendWithheldHistory\} \/>/);
});

it('the projection card explains withheld history and falls back to the service message', () => {
  const analytics = readFileSync(new URL('../../src/components/AnalyticsDashboard.tsx', import.meta.url), 'utf8');
  assert.match(analytics, /projectionData\.has_withheld_history === true/);
  assert.match(analytics, /!projection\.projection_available && projectionWithheldHistory\s*\n?\s*\? TREND_RESTART_EXPLANATION/);
});

it('TrendGraph empty state follows the withheld-history prop', async () => {
  const { TrendGraph } = await import('../../src/components/TrendGraph.tsx');
  const explained = renderToStaticMarkup(
    React.createElement(TrendGraph, { data: [], hasWithheldHistory: true }),
  );
  assert.match(explained, /Trend tracking restarted in v0\.10\.0/);
  const neutral = renderToStaticMarkup(React.createElement(TrendGraph, { data: [] }));
  assert.match(neutral, /Not enough data yet\./);
  assert.ok(!neutral.includes('v0.10.0'));
});
