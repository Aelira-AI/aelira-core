import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const appSource = readFileSync(
  new URL('../../src/App.tsx', import.meta.url),
  'utf8',
);
const integrationsSource = readFileSync(
  new URL('../../src/pages/Integrations.tsx', import.meta.url),
  'utf8',
);
const coursesSource = readFileSync(
  new URL('../../src/pages/BrightspaceCourses.tsx', import.meta.url),
  'utf8',
);

test('registers a protected standalone Brightspace course-list route', () => {
  assert.match(appSource, /import BrightspaceCourses from '.\/pages\/BrightspaceCourses'/);
  assert.match(
    appSource,
    /path="\/integrations\/brightspace"[\s\S]*?<BrightspaceCourses \/>/,
  );
});

test('connected Brightspace integrations expose the course-list action', () => {
  assert.match(
    integrationsSource,
    /name="D2L Brightspace"[\s\S]*?actionUrl="\/integrations\/brightspace"[\s\S]*?actionLabel="Browse Courses"/,
  );
});

test('course rows use provider identity and encoded content-scan links', () => {
  assert.match(coursesSource, /course\.name/);
  assert.match(coursesSource, /course\.code/);
  assert.match(coursesSource, /encodeURIComponent\(course\.orgUnitId\)/);
  assert.match(
    coursesSource,
    /`\/brightspace\/courses\/\$\{encodeURIComponent\(course\.orgUnitId\)\}\/content`/,
  );
});

test('renders distinct loading, empty, error, and successful list states', () => {
  assert.match(coursesSource, /Loading Brightspace courses/);
  assert.match(coursesSource, /No Brightspace courses found/);
  assert.match(coursesSource, /Unable to load Brightspace courses/);
  assert.match(coursesSource, /Retry/);
  assert.match(coursesSource, /Browse Brightspace courses/);
});

test('preserves embedded LTI and direct Brightspace content routes', () => {
  assert.match(appSource, /path="course\/:courseId" element=\{<LTICourseRoute \/>\}/);
  assert.match(
    appSource,
    /path="\/brightspace\/courses\/:orgUnitId\/content"[\s\S]*?<BrightspaceContentRoute \/>/,
  );
});
