/**
 * Unit tests for the `next`-param redirect safety guard.
 * Uses Node.js native test runner (same pattern as featureAccess.test.js).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { isSafeNextPath, resolveSafeNext } from '../../src/utils/safeNext.ts';

describe('isSafeNextPath', () => {
  it('accepts a plain relative path', () => {
    assert.equal(isSafeNextPath('/canvas/courses/123/content'), true);
  });

  it('accepts a relative path with a query string', () => {
    assert.equal(isSafeNextPath('/lti/course/123?code=abc'), true);
  });

  it('rejects protocol-relative URLs', () => {
    assert.equal(isSafeNextPath('//evil.com'), false);
  });

  it('rejects absolute http(s) URLs', () => {
    assert.equal(isSafeNextPath('https://evil.com'), false);
    assert.equal(isSafeNextPath('http://evil.com/path'), false);
  });

  it('rejects backslash-variant protocol-relative URLs', () => {
    assert.equal(isSafeNextPath('/\\evil.com'), false);
  });

  it('rejects paths not starting with a slash', () => {
    assert.equal(isSafeNextPath('evil.com'), false);
    assert.equal(isSafeNextPath('javascript:alert(1)'), false);
  });

  it('rejects null, undefined, and empty string', () => {
    assert.equal(isSafeNextPath(null), false);
    assert.equal(isSafeNextPath(undefined), false);
    assert.equal(isSafeNextPath(''), false);
  });
});

describe('resolveSafeNext', () => {
  it('returns the next path when safe', () => {
    assert.equal(resolveSafeNext('/canvas/courses/1/content'), '/canvas/courses/1/content');
  });

  it('falls back to /dashboard when unsafe', () => {
    assert.equal(resolveSafeNext('//evil.com'), '/dashboard');
    assert.equal(resolveSafeNext(null), '/dashboard');
  });

  it('honors a custom fallback', () => {
    assert.equal(resolveSafeNext(null, '/login'), '/login');
  });
});
