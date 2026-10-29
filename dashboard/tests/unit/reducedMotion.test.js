import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { prefersReducedMotion } from '../../src/utils/motion.ts';

const source = (path) => readFileSync(new URL(`../../src/${path}`, import.meta.url), 'utf8');

function withMatchMedia(matches, run) {
  const previous = globalThis.window;
  globalThis.window = { matchMedia: (query) => ({ matches: matches && query === '(prefers-reduced-motion: reduce)' }) };
  try {
    run();
  } finally {
    globalThis.window = previous;
  }
}

test('reduced motion follows the system setting', () => {
  withMatchMedia(true, () => assert.equal(prefersReducedMotion(), true));
  withMatchMedia(false, () => assert.equal(prefersReducedMotion(), false));
});

test('reduced motion is off when there is no window or matchMedia', () => {
  const previous = globalThis.window;
  try {
    delete globalThis.window;
    assert.equal(prefersReducedMotion(), false);
    globalThis.window = {};
    assert.equal(prefersReducedMotion(), false);
  } finally {
    globalThis.window = previous;
  }
});

test('the score skips its count-up under reduced motion and keeps its label with its number', () => {
  const score = source('components/results/ComplianceScore.tsx');
  assert.match(score, /if \(reduceMotion\) return undefined;/);
  assert.match(score, /const shown = reduceMotion \? score : animatedScore;/);
  assert.match(score, /\{shown\}<\/span>/);
  assert.match(score, /getScoreLabel\(shown\)/);
  assert.match(score, /getScoreIcon\(shown\)/);
  assert.match(score, /getScoreColor\(shown\)/);
  assert.doesNotMatch(score, /getScoreLabel\(score\)/);
});

test('the scan page charts do not animate under reduced motion', () => {
  const wcag = source('components/charts/WCAGCriteriaChart.tsx');
  assert.equal((wcag.match(/<Bar /g) || []).length, (wcag.match(/isAnimationActive=\{animate\}/g) || []).length);
  const byType = source('components/charts/IssuesByTypeChart.tsx');
  assert.match(byType, /<Bar [^>]*isAnimationActive=\{animate\}/);
  assert.match(byType, /<Pie\s+isAnimationActive=\{animate\}/);
  for (const chart of [wcag, byType]) assert.match(chart, /const animate = !prefersReducedMotion\(\);/);
});
