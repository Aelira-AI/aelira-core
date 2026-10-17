import test from 'node:test';
import assert from 'node:assert/strict';
import { APP_NAME, titleForPath } from '../../src/utils/routeTitles.ts';

test('every dashboard route resolves to a title of its own', () => {
  const paths = [
    '/login', '/dashboard', '/upload', '/bulk-upload', '/history', '/scan/abc', '/issues', '/remediate/abc',
    '/review', '/review/abc', '/settings', '/admin', '/integrations', '/integrations/settings', '/integrations/files',
    '/integrations/canvas', '/integrations/brightspace', '/canvas/courses/1/content',
    '/canvas/courses/1/content/f/review', '/brightspace/courses/1/content', '/brightspace/courses/1/content/f/review',
  ];
  const titles = paths.map(titleForPath);
  for (const title of titles) {
    assert.notEqual(title, APP_NAME, 'a known route must not fall back to the generic title');
    assert.ok(title.endsWith(` | ${APP_NAME}`));
  }
  assert.equal(new Set(titles).size, titles.length, 'titles must be unique per route');
});

test('aliases share a title and unknown paths fall back to the application name', () => {
  assert.equal(titleForPath('/alerts'), titleForPath('/integrations/settings'));
  assert.equal(titleForPath('/cloud-files'), titleForPath('/integrations/files'));
  assert.equal(titleForPath('/no/such/page'), APP_NAME);
});

test('a document review route is not mistaken for the review queue', () => {
  assert.notEqual(titleForPath('/review/123'), titleForPath('/review'));
});
