import { test, expect } from '@playwright/test';

const sessionBody = {
  auth_method: 'session',
  user: { id: 'user-1', email: 'prof@example.edu', name: 'Professor', role: 'faculty' },
  department: { id: 'dept-1', name: 'Example', tier: 'department' },
};

test.describe('authentication continuation', () => {
  test('magic verification navigates to a safe next path after success', async ({ page }) => {
    let verified = false;
    await page.route('**/auth/session/validate', async (route) => {
      await route.fulfill(
        verified
          ? { status: 200, contentType: 'application/json', body: JSON.stringify(sessionBody) }
          : { status: 401, contentType: 'application/json', body: '{"detail":"no session"}' }
      );
    });
    await page.route('**/auth/magic-link/verify', async (route) => {
      verified = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{"success":true}',
      });
    });

    await page.goto(
      '/auth/verify?email=prof%40example.edu&token=token&next=%2Fcanvas%2Fcourses%2F42%2Fcontent'
    );
    await page.getByRole('button', { name: 'Verify & Sign In' }).click();

    await expect(page).toHaveURL(/\/canvas\/courses\/42\/content$/, { timeout: 5000 });
  });

  test('magic verification falls back for a malicious next path', async ({ page }) => {
    let verified = false;
    await page.route('**/auth/session/validate', async (route) => {
      await route.fulfill(
        verified
          ? { status: 200, contentType: 'application/json', body: JSON.stringify(sessionBody) }
          : { status: 401, contentType: 'application/json', body: '{"detail":"no session"}' }
      );
    });
    await page.route('**/auth/magic-link/verify', async (route) => {
      verified = true;
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"success":true}' });
    });

    await page.goto(
      '/auth/verify?email=prof%40example.edu&token=token&next=%2F%5Cevil.example'
    );
    await page.getByRole('button', { name: 'Verify & Sign In' }).click();

    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 5000 });
  });

  for (const provider of ['Google', 'Microsoft']) {
    test(`${provider} login URL carries an encoded safe next path`, async ({ page }) => {
      await page.route('**/auth/session/validate', (route) =>
        route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"no session"}' })
      );
      await page.route('**/auth/oauth/status**', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            oauth_allowed: true,
            google_available: true,
            microsoft_available: true,
          }),
        })
      );

      await page.goto('/login?next=%2Fcanvas%2Fcourses%2F42%2Fcontent%3Ftab%3Dfiles');
      await page.getByLabel('Email Address').fill('prof@example.edu');
      const requestPromise = page.waitForRequest(
        (request) => request.url().includes(`/auth/${provider.toLowerCase()}/login`)
      );
      await page.getByRole('button', { name: provider, exact: true }).click();
      const request = await requestPromise;

      expect(new URL(request.url()).searchParams.get('next')).toBe(
        '/canvas/courses/42/content?tab=files'
      );
    });
  }
});
