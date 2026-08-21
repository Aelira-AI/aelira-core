import { expect, test, type Page, type Request, type Route } from '@playwright/test';

const API_ORIGIN = 'http://localhost:8000';
const FULL_KEY = 'aelira_live_visible_once_123';

const department = { id: 'dept-1', name: 'Accessibility', tier: 'trial' };

function user(id: string) {
  return {
    id,
    email: `${id}@example.edu`,
    name: `User ${id}`,
    role: 'faculty',
    email_verified: true,
  };
}

function keyMetadata(overrides: Record<string, unknown> = {}) {
  return {
    id: 'key-1',
    name: 'Existing CLI',
    key_prefix: 'aelira_old',
    rate_limit_per_hour: 100,
    created_at: '2026-08-20T00:00:00Z',
    last_used_at: null,
    expires_at: null,
    is_active: true,
    ...overrides,
  };
}

function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    const text = message.text();
    const isExternalFontFailure = text.includes('downloadable font: download failed');
    if (message.type() === 'error' && !isExternalFontFailure) {
      errors.push(`console: ${text}`);
    }
  });
  return errors;
}

async function installApiMocks(
  page: Page,
  options: {
    authMethod?: 'session' | 'lti' | 'api_key';
    userId?: () => string;
    onRequest?: (request: Request) => void;
    keys?: () => ReturnType<typeof keyMetadata>[];
    revokedCurrentKey?: boolean;
  } = {},
) {
  await page.route(`${API_ORIGIN}/**`, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    options.onRequest?.(request);

    if (path === '/auth/session/validate') {
      if (options.authMethod === 'api_key') {
        return route.fulfill({ status: 401, json: { detail: 'No session' } });
      }
      return route.fulfill({
        json: {
          valid: true,
          auth_method: options.authMethod ?? 'session',
          user: user(options.userId?.() ?? 'user-1'),
          department,
          expires_at: 1999999999,
        },
      });
    }
    if (path === '/auth/validate') {
      return route.fulfill({
        json: {
          valid: true,
          user: user(options.userId?.() ?? 'user-1'),
          department,
        },
      });
    }
    if (path === '/auth/keys' && request.method() === 'GET') {
      return route.fulfill({ json: options.keys?.() ?? [keyMetadata()] });
    }
    if (path === '/auth/keys' && request.method() === 'POST') {
      return route.fulfill({
        json: {
          api_key: keyMetadata({ id: 'key-new', name: 'Automation', key_prefix: 'aelira_new' }),
          full_key: FULL_KEY,
          warning: 'Store this API key securely. It will not be shown again.',
        },
      });
    }
    if (path === '/auth/keys/key-1' && request.method() === 'DELETE') {
      return route.fulfill({
        json: {
          success: true,
          message: 'API key revoked',
          revoked_current_key: options.revokedCurrentKey ?? false,
        },
      });
    }
    if (path === '/auth/profile') {
      return route.fulfill({ json: { ...user(options.userId?.() ?? 'user-1'), created_at: '2026-01-01T00:00:00Z', timezone: 'UTC' } });
    }
    if (path === '/auth/sessions') return route.fulfill({ json: { sessions: [] } });
    if (path === '/account/deletion-status') return route.fulfill({ json: { status: null } });
    if (path === '/education/scans') return route.fulfill({ json: { scans: [] } });
    if (path.includes('/education/compliance/') && path.endsWith('/issues')) return route.fulfill({ json: [] });
    if (path.includes('/education/compliance/') && path.endsWith('/trend')) return route.fulfill({ json: [] });
    if (path === '/api/reviews/department-summary') {
      return route.fulfill({ json: { total_documents: 0, pending_count: 0 } });
    }
    if (path.includes('/email')) return route.fulfill({ json: {} });
    return route.fulfill({ json: {} });
  });
}

test.describe('API-key retirement recovery', () => {
  test('session recovery clears stale auth before key CRUD and reveals a new key once', async ({ page, context }) => {
    const errors = trackPageErrors(page);
    const keyRequests: Request[] = [];
    let listCount = 0;
    let revoked = false;
    await page.addInitScript(() => localStorage.setItem('apiKey', 'retired-dashboard-key'));
    await context.addCookies([
      { name: 'aelira_access', value: 'session-cookie', url: 'http://localhost:5173' },
      { name: 'csrf_token', value: 'csrf-123', url: 'http://localhost:5173' },
    ]);
    await installApiMocks(page, {
      onRequest: request => {
        if (new URL(request.url()).pathname.startsWith('/auth/keys')) keyRequests.push(request);
      },
      keys: () => {
        listCount += 1;
        if (revoked) return [keyMetadata({ is_active: false, last_used_at: '2026-08-21T01:02:03Z' })];
        if (listCount > 1) return [keyMetadata(), keyMetadata({ id: 'key-new', name: 'Automation', key_prefix: 'aelira_new' })];
        return [keyMetadata()];
      },
    });

    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'API Key Management' })).toBeVisible();
    await expect.poll(() => keyRequests.length).toBeGreaterThan(0);
    expect(keyRequests[0].headers().authorization).toBeUndefined();
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();

    await page.getByLabel('Key name').fill('Automation');
    await page.getByRole('button', { name: 'Create API key' }).click();
    await expect(page.getByText(FULL_KEY)).toBeVisible();
    const create = keyRequests.find(request => request.method() === 'POST');
    expect(create?.headers()['x-csrf-token']).toBe('csrf-123');
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
    await page.getByRole('button', { name: 'Dismiss key' }).click();
    await expect(page.getByText(FULL_KEY)).toBeHidden();

    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Revoke Existing CLI' }).click();
    await expect(page.getByRole('cell', { name: 'Revoked' })).toBeVisible();
    const revoke = keyRequests.find(request => request.method() === 'DELETE');
    expect(revoke?.headers()['x-csrf-token']).toBe('csrf-123');

    revoked = true;
    await page.getByRole('button', { name: 'Refresh keys' }).click();
    await expect(page.getByRole('cell', { name: /8\/21\/2026|21\/08\/2026/ })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('session banner acknowledgement is versioned per user', async ({ page }) => {
    const errors = trackPageErrors(page);
    let currentUser = 'user-1';
    await installApiMocks(page, { userId: () => currentUser });

    await page.goto('/dashboard');
    const banner = page.getByRole('alert').filter({ hasText: 'Legacy dashboard API keys have been retired' });
    await expect(banner).toBeVisible();
    await banner.getByRole('button', { name: 'I understand' }).click();
    await expect(banner).toBeHidden();
    expect(await page.evaluate(() => localStorage.getItem('aelira_api_key_retirement_ack:v1:user-1'))).toBe('1');

    await page.reload();
    await expect(banner).toBeHidden();
    currentUser = 'user-2';
    await page.reload();
    await expect(banner).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('LTI validation preserves the launch token and never shows the session banner', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.addInitScript(() => localStorage.setItem('apiKey', 'lti-launch-token'));
    await installApiMocks(page, { authMethod: 'lti' });

    await page.goto('/dashboard');
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByText('Legacy dashboard API keys have been retired')).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBe('lti-launch-token');
    expect(errors).toEqual([]);
  });

  test('API-key self-revoke clears auth and redirects immediately', async ({ page }) => {
    const requests: Request[] = [];
    await page.addInitScript(() => localStorage.setItem('apiKey', 'current-dashboard-key'));
    await installApiMocks(page, {
      authMethod: 'api_key',
      revokedCurrentKey: true,
      onRequest: request => requests.push(request),
    });

    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'API Key Management' })).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Revoke Existing CLI' }).click();

    await expect(page).toHaveURL(/\/login/);
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
    expect(requests.some(request => new URL(request.url()).pathname === '/auth/session/logout')).toBe(true);
  });

  test('session key revoke never logs out even if response claims current', async ({ page }) => {
    const requests: Request[] = [];
    await installApiMocks(page, {
      revokedCurrentKey: true,
      onRequest: request => requests.push(request),
    });

    await page.goto('/settings');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Revoke Existing CLI' }).click();

    await expect(page).toHaveURL(/\/settings/);
    expect(requests.some(request => new URL(request.url()).pathname === '/auth/session/logout')).toBe(false);
  });

  test('LTI settings show unavailable card and make no API-key calls', async ({ page }) => {
    const keyRequests: Request[] = [];
    await installApiMocks(page, {
      authMethod: 'lti',
      onRequest: request => {
        if (new URL(request.url()).pathname.startsWith('/auth/keys')) keyRequests.push(request);
      },
    });

    await page.goto('/settings');
    await expect(page.getByText('API key management is unavailable in an LTI launch session.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create API key' })).toHaveCount(0);
    expect(keyRequests).toEqual([]);
  });
});
