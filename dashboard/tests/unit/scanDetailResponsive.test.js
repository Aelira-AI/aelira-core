import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../../src/pages/ScanDetail.tsx', import.meta.url),
  'utf8',
);

test('scan detail wraps document actions instead of widening mobile viewports', () => {
  assert.match(source, /flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between/);
  assert.match(source, /flex flex-wrap items-center gap-3 sm:shrink-0/);
  assert.match(source, /min-w-0/);
  assert.match(source, /break-words/);
  assert.doesNotMatch(source, /flex items-center gap-3 shrink-0/);
});
