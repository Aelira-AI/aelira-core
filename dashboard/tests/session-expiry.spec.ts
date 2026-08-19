import { expect, test, type Page } from '@playwright/test';

const apiPattern = (path: string): string => `**${path}`;

async function stubSessionValidation(page: Page, onRequest: () => void): Promise<void> {
  await page.route(apiPattern('/auth/session/validate'), async (route) => {
    onRequest();
    await route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"expired"}' });
  });
}

async function openClientHarness(page: Page): Promise<void> {
  await page.route(apiPattern('/auth/session/validate'), async (route) => {
    await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
  });
  await page.goto('/login?client-test=1');
  await page.waitForLoadState('domcontentloaded');
}

test.describe('expired login bootstrap', () => {
  test('expired login clears credentials, skips validation, and explains expiry', async ({ page }) => {
    let validationRequests = 0;
    await stubSessionValidation(page, () => validationRequests++);
    await page.addInitScript(() => localStorage.setItem('apiKey', 'stale-key'));

    await page.goto('/login?expired=1');

    await expect(page.getByRole('alert')).toContainText(/session.*expired/i);
    await expect.poll(() => validationRequests).toBe(0);
    await expect.poll(() => page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
  });

  test('normal login still validates an existing session', async ({ page }) => {
    let validationRequests = 0;
    await stubSessionValidation(page, () => validationRequests++);

    await page.goto('/login');

    await expect.poll(() => validationRequests).toBeGreaterThan(0);
  });
});

test.describe('401 recovery', () => {
  test('concurrent cookie 401s share one refresh and each retry once', async ({ page }) => {
    let refreshRequests = 0;
    let resourceRequests = 0;
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route('**/task8a-success-*', async (route) => {
      resourceRequests++;
      const attempt = Number(new URL(route.request().url()).searchParams.get('attempt'));
      await route.fulfill({
        status: attempt === 1 ? 401 : 200,
        contentType: 'application/json',
        body: attempt === 1 ? '{}' : '{"ok":true}',
      });
    });
    await openClientHarness(page);

    const results = await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      const attempts = new Map<string, number>();
      apiClient.interceptors.request.use((config) => {
        if (config.url?.includes('task8a-success-')) {
          const count = (attempts.get(config.url) ?? 0) + 1;
          attempts.set(config.url, count);
          config.params = { attempt: count };
        }
        return config;
      });
      return Promise.all([0, 1, 2].map((id) => apiClient.get(`/task8a-success-${id}`)))
        .then((responses) => responses.map((response) => response.data.ok));
    });

    expect(results).toEqual([true, true, true]);
    expect(refreshRequests).toBe(1);
    expect(resourceRequests).toBe(6);
  });

  test('a retried 401 does not refresh twice and terminates once', async ({ page }) => {
    let refreshRequests = 0;
    let logoutRequests = 0;
    let resourceRequests = 0;
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route(apiPattern('/auth/session/logout'), async (route) => {
      logoutRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route('**/task8a-retry-401', async (route) => {
      resourceRequests++;
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await openClientHarness(page);

    await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      await apiClient.get('/task8a-retry-401').catch(() => undefined);
    });

    await page.waitForURL('/login?expired=1');
    expect(refreshRequests).toBe(1);
    expect(resourceRequests).toBe(2);
    expect(logoutRequests).toBe(1);
  });

  test('concurrent failed cookie 401s share the refresh failure and terminate once', async ({ page }) => {
    let refreshRequests = 0;
    let logoutRequests = 0;
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await page.evaluate(() => localStorage.setItem('apiKey', 'stale-during-refresh'));
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: '{"detail":"refresh expired"}',
      });
    });
    await page.route(apiPattern('/auth/session/logout'), async (route) => {
      logoutRequests++;
      await new Promise((resolve) => setTimeout(resolve, 100));
      await route.fulfill({ status: 204 });
    });
    await page.route('**/task8a-failed-*', async (route) => {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await openClientHarness(page);

    const rejection = await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      const errors = await Promise.all(
        [0, 1, 2].map((id) => apiClient.get(`/task8a-failed-${id}`).catch((error) => error)),
      );
      return {
        sameFailure: errors.every((error) => error === errors[0]),
        statuses: errors.map((error) => error.response?.status),
      };
    });

    expect(rejection).toEqual({ sameFailure: true, statuses: [401, 401, 401] });
    await page.waitForURL('/login?expired=1');
    expect(refreshRequests).toBe(1);
    expect(logoutRequests).toBe(1);
    await expect.poll(() => page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
  });

  test('a stored API-key 401 terminates without refreshing', async ({ page }) => {
    let refreshRequests = 0;
    let logoutRequests = 0;
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route(apiPattern('/auth/session/logout'), async (route) => {
      logoutRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route('**/task8a-api-key', async (route) => {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await openClientHarness(page);
    await page.evaluate(() => localStorage.setItem('apiKey', 'stored-api-key'));

    await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      void apiClient.get('/task8a-api-key').catch(() => undefined);
    });

    await page.waitForURL('/login?expired=1');
    expect(refreshRequests).toBe(0);
    expect(logoutRequests).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
  });

  test('an explicit LTI bearer rejects without dashboard logout or credential clearing', async ({ page }) => {
    let refreshRequests = 0;
    let logoutRequests = 0;
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route(apiPattern('/auth/session/logout'), async (route) => {
      logoutRequests++;
      await route.fulfill({ status: 204 });
    });
    await page.route('**/task8a-lti', async (route) => {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await openClientHarness(page);
    await page.evaluate(() => localStorage.setItem('apiKey', 'dashboard-key'));

    const status = await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      return apiClient
        .get('/task8a-lti', { headers: { Authorization: 'Bearer lti-token' } })
        .catch((error) => error.response?.status);
    });

    expect(status).toBe(401);
    await page.waitForTimeout(150);
    expect(page.url()).toContain('/login?client-test=1');
    expect(refreshRequests).toBe(0);
    expect(logoutRequests).toBe(0);
    expect(await page.evaluate(() => localStorage.getItem('apiKey'))).toBe('dashboard-key');
  });

  test('repeated terminal failures tolerate logout failure and replace once', async ({ page }) => {
    let refreshRequests = 0;
    let logoutRequests = 0;
    let expiredDocumentRequests = 0;
    let csrfHeader: string | undefined;
    page.on('request', (request) => {
      if (request.resourceType() === 'document' && request.url().endsWith('/login?expired=1')) {
        expiredDocumentRequests++;
      }
    });
    await page.route(apiPattern('/auth/session/refresh'), async (route) => {
      refreshRequests++;
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await page.route(apiPattern('/auth/session/logout'), async (route) => {
      logoutRequests++;
      csrfHeader = route.request().headers()['x-csrf-token'];
      await new Promise((resolve) => setTimeout(resolve, 100));
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
    });
    await page.route('**/task8a-terminal-*', async (route) => {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    });
    await openClientHarness(page);
    await page.context().addCookies([
      { name: 'csrf_token', value: 'task8a-csrf', url: 'http://localhost:5173' },
    ]);
    await page.evaluate(() => localStorage.setItem('apiKey', 'terminal-key'));

    await page.evaluate(async () => {
      const { apiClient } = await import('/src/api/client.ts');
      await apiClient.get('/task8a-terminal-0').catch(() => undefined);
      void apiClient.get('/task8a-terminal-1').catch(() => undefined);
      void apiClient.get('/task8a-terminal-2').catch(() => undefined);
    });

    await page.waitForURL('/login?expired=1');
    await page.waitForTimeout(100);
    expect(logoutRequests).toBe(1);
    expect(refreshRequests).toBe(0);
    expect(expiredDocumentRequests).toBe(1);
    expect(csrfHeader).toBe('task8a-csrf');
  });
});
