import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const card = readFileSync(new URL('../../src/components/settings/APIKeyManagementCard.tsx', import.meta.url), 'utf8');

test('API-key table contains intrinsic width without losing local horizontal access', () => {
  assert.ok(card.includes('className="overflow-x-auto"'));
  assert.ok(card.includes("style={{ contain: 'layout paint' }}"));
  assert.ok(card.includes('<caption className="sr-only">Your API keys and their status</caption>'));
});

test('API-key status and revoke action remain exposed to assistive input', () => {
  assert.ok(card.includes("key.is_active ? 'Active' : 'Revoked'"));
  assert.match(card, /<td className="text-right"><button type="button".*?aria-label=\{`Revoke \$\{key\.name\}`\}/);
});
