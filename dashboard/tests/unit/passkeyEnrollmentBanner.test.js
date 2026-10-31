import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const banner = readFileSync(
  new URL('../../src/components/PasskeyEnrollmentBanner.tsx', import.meta.url),
  'utf8'
);
const app = readFileSync(new URL('../../src/App.tsx', import.meta.url), 'utf8');

test('PasskeyEnrollmentBanner uses versioned and user-scoped storage key', () => {
  assert.ok(banner.includes("const ENROLLMENT_BANNER_VERSION = 'v1'"));
  assert.ok(
    banner.includes('aelira_passkey_prompt_dismissed:${ENROLLMENT_BANNER_VERSION}:${user.id}')
  );
});

test('PasskeyEnrollmentBanner restricts display to session auth and WebAuthn supported clients', () => {
  assert.ok(banner.includes("authMethod !== 'session'"));
  assert.ok(banner.includes('isPasskeySupported()'));
  assert.ok(banner.includes('listPasskeys()'));
  assert.ok(banner.includes('enrolledCount === null || enrolledCount > 0'));
});

test('PasskeyEnrollmentBanner conforms to accessibility contracts', () => {
  assert.ok(banner.includes('role="region"'));
  assert.ok(banner.includes('aria-label="Passkey enrollment recommendation"'));
  assert.ok(banner.includes('type="button"'));
  assert.ok(banner.includes('aria-label="Dismiss passkey recommendation"'));
  assert.ok(banner.includes('role="status"'));
});

test('PasskeyEnrollmentBanner exposes 1-click registration and Settings navigation', () => {
  assert.ok(banner.includes('registerPasskey('));
  assert.ok(banner.includes('to="/settings"'));
  assert.ok(banner.includes('Set up Passkey'));
});

test('AppLayout integrates PasskeyEnrollmentBanner beside APIKeyRetirementBanner', () => {
  assert.ok(
    app.includes("import { PasskeyEnrollmentBanner } from './components/PasskeyEnrollmentBanner'")
  );
  assert.ok(app.includes('<PasskeyEnrollmentBanner />'));
});
