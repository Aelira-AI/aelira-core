/** Authenticated acceptance requests must stay inside the disposable API origin. */
import assert from 'node:assert/strict';

export function authenticatedTarget(path: string, api: URL): URL {
  const target = new URL(path, api);
  assert.equal(target.origin, api.origin, 'Refuse cross-origin authenticated request');
  assert.equal(target.username, '', 'Credentials in request URLs are forbidden');
  assert.equal(target.password, '', 'Credentials in request URLs are forbidden');
  return target;
}
