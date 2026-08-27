import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { consumeInvitationToken } from '../../src/utils/invitationToken.ts';

const page = readFileSync(new URL('../../src/pages/AcceptInvitation.tsx', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../src/App.tsx', import.meta.url), 'utf8');
const client = readFileSync(new URL('../../src/api/client.ts', import.meta.url), 'utf8');

test('public invitation route consumes fragment before legacy query and scrubs browser history', () => {
  assert.ok(app.includes('path="/accept-invitation"'));
  let scrubbedUrl = '';
  const result = consumeInvitationToken(
    {
      href: 'https://dashboard.example.edu/accept-invitation?token=legacy&source=email#token=fragment',
    },
    {
      state: { route: 'invitation' },
      replaceState: (_state, _unused, url) => {
        scrubbedUrl = String(url);
      },
    }
  );

  assert.equal(result.token, 'fragment');
  assert.equal(result.hadToken, true);
  assert.equal(scrubbedUrl, '/accept-invitation?source=email');
  assert.equal(scrubbedUrl.includes('fragment'), false);
  assert.equal(scrubbedUrl.includes('legacy'), false);
  assert.equal(page.includes('localStorage.setItem'), false);
  assert.equal(page.includes('sessionStorage.setItem'), false);
  assert.equal(page.includes('trackEvent('), false);
});

test('invitation acceptance bypasses API-key auth and sends only bounded profile fields', () => {
  assert.ok(client.includes("'/auth/accept-invitation'"));
  assert.ok(page.includes("'/auth/accept-invitation'"));
  assert.ok(page.includes('{ _skipApiKeyAuth: true }'));
  assert.ok(page.includes('email: email.trim().toLowerCase()'));
  assert.ok(page.includes("name: name.trim() || null"));
  assert.ok(page.includes('maxLength={200}'));
});

test('invitation page exposes replay-safe success and role-appropriate login continuation', () => {
  assert.ok(page.includes("response.data.outcome === 'already_accepted'"));
  assert.ok(page.includes('Access already active'));
  assert.ok(page.includes('Your access is ready'));
  assert.ok(page.includes("'/login?next=%2Fadmin'"));
  assert.ok(page.includes("'/login?next=%2Fdashboard'"));
  assert.ok(page.includes("response.data.role === 'admin'"));
  assert.ok(page.includes('role="alert"'));
  assert.ok(page.includes('aria-live="polite"'));
});
