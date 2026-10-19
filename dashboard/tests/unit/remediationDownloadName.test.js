import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { remediationDownloadName } from '../../src/utils/remediationDownloadName.ts';

describe('remediation download name', () => {
  it('keeps the existing name for same-format working files', () => {
    assert.equal(remediationDownloadName('syllabus.pdf', 'application/pdf'), 'remediated-syllabus.pdf');
    assert.equal(remediationDownloadName(undefined, ''), 'remediated-document');
  });

  it('names generated captions as WebVTT, never as the source media', () => {
    assert.equal(remediationDownloadName('teaching-clip.mp4', 'text/vtt'), 'teaching-clip-captions.vtt');
    assert.equal(
      remediationDownloadName('lecture.final.mov', 'text/vtt; charset=utf-8'),
      'lecture.final-captions.vtt',
    );
    assert.equal(remediationDownloadName(undefined, 'text/vtt'), 'captions.vtt');
  });
});
