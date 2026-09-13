import { it } from 'node:test';
import assert from 'node:assert/strict';
import { wcagCriterion } from '../../src/utils/wcagCriterion.ts';

it('uses the recorded WCAG field aliases and explicit WCAG rules', () => {
  for (const key of ['criterion', 'wcag_criterion', 'wcag_criteria', 'rule']) {
    assert.equal(wcagCriterion({ [key]: 'WCAG 2.1 Level AA: 1.1.1 Non-text content' }), '1.1.1');
  }
  assert.equal(wcagCriterion({ rule: 'WCAG 1.3.1' }), '1.3.1');
});

it('does not relabel other frameworks, versions, or unspecified findings as WCAG', () => {
  assert.equal(wcagCriterion({ rule: 'PDF/UA 7.1.2' }), null);
  assert.equal(wcagCriterion({ criterion: 'Matterhorn 09-004' }), null);
  assert.equal(wcagCriterion({ criterion: 'WCAG 2.1' }), null);
  assert.equal(wcagCriterion({ rule: 'Other 1.2.3' }), null);
  assert.equal(wcagCriterion({}), null);
  assert.equal(wcagCriterion({ wcag_criteria: { unexpected: true } }), null);
});
