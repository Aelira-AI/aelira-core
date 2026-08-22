import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const client = readFileSync(new URL('../../src/api/client.ts', import.meta.url), 'utf8');
const auth = readFileSync(new URL('../../src/context/AuthContext.tsx', import.meta.url), 'utf8');
const apiKeys = readFileSync(new URL('../../src/api/apiKeys.ts', import.meta.url), 'utf8');
const card = readFileSync(new URL('../../src/components/settings/APIKeyManagementCard.tsx', import.meta.url), 'utf8');
const banner = readFileSync(new URL('../../src/components/APIKeyRetirementBanner.tsx', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../src/App.tsx', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../src/pages/Settings.tsx', import.meta.url), 'utf8');

test('session and magic-link endpoints explicitly skip stored API-key auth', () => {
  assert.ok(client.includes('_skipApiKeyAuth?: boolean'));
  assert.ok(client.includes('config._skipApiKeyAuth'));
  for (const endpoint of ['/auth/session/validate', '/auth/session/refresh', '/auth/session/logout']) {
    assert.ok(auth.includes(`'${endpoint}'`));
  }
  assert.ok(auth.includes('_skipApiKeyAuth: true'));
});

test('successful session validation clears every stale API-key location', () => {
  assert.ok(client.includes('export function clearStoredApiKeyAuth'));
  assert.ok(client.includes("localStorage.removeItem('apiKey')"));
  assert.ok(client.includes('delete apiClient.defaults.headers.common.Authorization'));
  assert.ok(auth.includes('clearStoredApiKeyAuth();'));
  assert.ok(auth.includes('setApiKey(null);'));
  assert.ok(auth.includes("response.data.auth_method === 'session'"));
  assert.ok(auth.includes('setAuthMethod(response.data.auth_method)'));
  assert.ok(auth.includes("setAuthMethod('api_key')"));
});

test('LTI validation is authenticated without clearing its launch token', () => {
  assert.ok(auth.includes("auth_method: 'session' | 'lti'"));
  assert.ok(auth.includes("authMethod === 'lti'"));
  assert.ok(auth.includes("if (response.data.auth_method === 'session')"));
});

test('cookie-authenticated mutations send CSRF even if a stale Bearer header remains', () => {
  assert.ok(client.includes("readCookie('aelira_access')"));
  assert.ok(client.includes('(!config.headers.Authorization || hasSessionCookie)'));
});

test('typed API-key client exposes metadata-only list, create, and revoke payloads', () => {
  for (const token of ['APIKeyMetadata', 'CreateAPIKeyRequest', 'CreateAPIKeyResponse', 'list()', 'create(request', 'revoke(keyId']) {
    assert.ok(apiKeys.includes(token), `missing ${token}`);
  }
});

test('settings mounts accessible API-key CRUD with one-time reveal and errors', () => {
  assert.ok(settings.includes('<APIKeyManagementCard'));
  for (const token of ['aria-live="polite"', 'role="alert"', 'window.confirm', 'Dismiss key', 'Copy key', 'Create API key', 'Refresh keys']) {
    assert.ok(card.includes(token), `missing ${token}`);
  }
  assert.ok(card.includes('setCreatedKey(null)'));
  assert.equal(card.includes("localStorage.setItem('apiKey'"), false);
});

test('self-revoke logs out only API-key authentication', () => {
  assert.ok(card.includes('const { authMethod, logout } = useAuth();'));
  assert.ok(card.includes("result.revoked_current_key && authMethod === 'api_key'"));
  assert.ok(card.includes('await logout();'));
});

test('LTI settings render API-key management unavailable without mounting CRUD', () => {
  assert.ok(settings.includes("authMethod === 'lti' ?"));
  assert.ok(settings.includes('API key management is unavailable in an LTI launch session.'));
  assert.ok(settings.includes(': <APIKeyManagementCard />'));
});

test('retirement banner is session-only, version/user scoped, and storage-failure tolerant', () => {
  assert.ok(app.includes('<APIKeyRetirementBanner />'));
  for (const token of [
    "authMethod !== 'session'",
    "aelira_api_key_retirement_ack:${RETIREMENT_NOTICE_VERSION}:${userId}",
    'localStorage.getItem(storageKey)',
    'localStorage.setItem(storageKey',
    'setDismissed(true)',
    'role="alert"',
    'I understand',
    'to="/settings"',
  ]) assert.ok(banner.includes(token), `missing ${token}`);
});
