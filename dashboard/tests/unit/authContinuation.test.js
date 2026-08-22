import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const login = readFileSync(new URL('../../src/pages/Login.tsx', import.meta.url), 'utf8');
const verify = readFileSync(new URL('../../src/pages/VerifyMagicLink.tsx', import.meta.url), 'utf8');

test('login sends the resolved continuation through magic link and both OAuth URLs', () => {
  assert.match(login, /next:\s*resolveSafeNext\(searchParams\.get\('next'\)\)/);
  assert.match(
    login,
    /buildAuthContinuationUrl\([\s\S]*?'\/auth\/google\/login'[\s\S]*?searchParams\.get\('next'\)/
  );
  assert.match(
    login,
    /buildAuthContinuationUrl\([\s\S]*?'\/auth\/microsoft\/login'[\s\S]*?searchParams\.get\('next'\)/
  );
});

test('magic-link verification resolves the URL continuation before navigation', () => {
  assert.match(verify, /resolveSafeNext\(searchParams\.get\('next'\)\)/);
  assert.match(verify, /navigate\(nextPath,\s*\{ replace: true \}\)/);
});
