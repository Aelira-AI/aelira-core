import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { describeIssueFinding } from '../../src/utils/issueFindingDescription.ts';

describe('source-backed Issues descriptions', () => {
  it('displays the actual message-only PDF heading finding', () => {
    const finding = Object.freeze({
      element: 'First heading: Synthetic metadata failure...',
      impact: 'Improper heading hierarchy affects navigation',
      location: 'Beginning of document',
      message: 'Document should start with H1 heading',
      page_number: 1,
      rule: 'WCAG 1.3.1',
      severity: 'medium',
    });
    assert.equal(describeIssueFinding(finding), finding.message);
  });

  it('prefers description, then message, then title over a recommendation', () => {
    const finding = {
      description: 'Source description', message: 'Source message',
      title: 'Source title', suggested_fix: 'Suggested fix', suggested_alt_text: 'Suggested alt',
    };
    for (const field of ['description', 'message', 'title', 'suggested_fix', 'suggested_alt_text']) {
      assert.equal(describeIssueFinding(finding), finding[field]);
      delete finding[field];
    }
  });

  it('ignores blank and non-string text fields without coercion or throwing', () => {
    for (const value of [null, undefined, '', ' \n ', false, true, 0, 12, [], {}, ['text']]) {
      assert.equal(describeIssueFinding({ description: value, message: value, title: 'Actual title' }), 'Actual title');
      assert.equal(describeIssueFinding({ issue_type: value, type: value, text: value, shape_name: value }), 'Finding description unavailable');
    }
    assert.equal(describeIssueFinding({ message: '  Scanner message  ' }), 'Scanner message');
  });

  it('retains legacy type and source-text fallbacks', () => {
    assert.equal(describeIssueFinding({ type: 'heading', issue_type: 'missing_h1', text: 'Introduction' }, { heading: 'Heading' }), 'Heading: missing h1 — Introduction');
    assert.equal(describeIssueFinding({ issue_type: 'missing_alt_text', shape_name: 'Picture 1' }), 'Issue: missing alt text — Picture 1');
    assert.equal(describeIssueFinding({ type: 'contrast' }), 'contrast: contrast');
    assert.equal(describeIssueFinding({ text: 'Source text' }), 'Issue: Source text');
  });

  it('does not invent a description from unrelated metadata or mutate the finding', () => {
    const finding = { severity: 'critical', location: 'Page 1', can_auto_fix: true, status: 'OPEN' };
    const before = structuredClone(finding);
    assert.equal(describeIssueFinding(finding), 'Finding description unavailable');
    assert.deepEqual(finding, before);
  });
});
