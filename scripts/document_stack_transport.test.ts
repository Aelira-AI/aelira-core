import assert from 'node:assert/strict';
import test from 'node:test';
import { authenticatedTarget } from './document_stack_transport.ts';

const api = new URL('http://localhost:18300');
test('relative and same-origin artifact URLs retain the authenticated origin', () => {
  assert.equal(authenticatedTarget('/education/project/original', api).href, 'http://localhost:18300/education/project/original');
  assert.equal(authenticatedTarget('http://localhost:18300/artifact', api).pathname, '/artifact');
});
for (const path of ['https://external.invalid', '//external.invalid', 'http://localhost:18301/artifact',
  'http://name:secret@localhost:18300/artifact', 'file:///tmp/artifact']) {
  test(`unsafe artifact URL is refused before authentication: ${path}`, () => {
    assert.throws(() => authenticatedTarget(path, api));
  });
}
