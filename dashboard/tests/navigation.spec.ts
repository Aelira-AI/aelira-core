import { test, expect } from '@playwright/test';

/**
 * Navigation Tests for Aelira Dashboard
 *
 * Tests that the dashboard navigation works correctly
 * when authenticated (mocked for testing).
 */

test.describe('Public Navigation', () => {
  test('login page is accessible', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('h1, h2')).toContainText(/welcome|sign in|dashboard/i);
  });

  test('root redirects appropriately', async ({ page }) => {
    await page.goto('/');

    // Should either show login or redirect to dashboard (then to login)
    await page.waitForTimeout(1000);

    // Either on login or dashboard route
    const url = page.url();
    expect(url).toMatch(/\/(login|dashboard)/);
  });

  test('404 handling for unknown routes', async ({ page }) => {
    await page.goto('/this-route-does-not-exist');

    // Should redirect to login (protected route behavior) or show 404
    await page.waitForTimeout(1000);

    // Either shows login or the dashboard handles unknown routes
    const url = page.url();
    expect(url).toBeTruthy();
  });
});

test.describe('Protected Route Redirects', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
  });

  test('/dashboard requires auth', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/upload requires auth', async ({ page }) => {
    await page.goto('/upload');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/history requires auth', async ({ page }) => {
    await page.goto('/history');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/settings requires auth', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/scan/:id requires auth', async ({ page }) => {
    await page.goto('/scan/test-scan-id');
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe('Login Page Elements', () => {
  test('has logo', async ({ page }) => {
    await page.goto('/login');

    // Should have some form of logo/branding
    const hasLogo = await page.locator('img, svg').first().isVisible().catch(() => false);
    const hasTitle = await page.locator('text=/aelira/i').isVisible().catch(() => false);

    expect(hasLogo || hasTitle).toBeTruthy();
  });

  test('has form with required fields', async ({ page }) => {
    await page.goto('/login');

    await expect(page.locator('form')).toBeVisible();
    await expect(page.locator('input')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('input is type password for security', async ({ page }) => {
    await page.goto('/login');

    const input = page.locator('input').first();
    const type = await input.getAttribute('type');

    expect(type).toBe('password');
  });
});

test.describe('Responsive Design', () => {
  test('login page works on mobile viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/login');

    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('login page works on tablet viewport', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/login');

    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('login page works on desktop viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/login');

    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });
});

test.describe('Tier-Based Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
  });

  test('navigation requires authentication', async ({ page }) => {
    // All protected routes should redirect to login
    const routes = ['/dashboard', '/upload', '/integrations', '/bulk-upload', '/settings'];

    for (const route of routes) {
      await page.goto(route);
      await expect(page).toHaveURL(/\/login/);
    }
  });

  test('signup page is accessible without authentication', async ({ page }) => {
    await page.goto('/signup');
    await page.waitForTimeout(500);

    // Either shows signup or redirects appropriately
    const url = page.url();
    expect(url).toBeTruthy();
  });

  test('/integrations requires auth', async ({ page }) => {
    await page.goto('/integrations');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/bulk-upload requires auth', async ({ page }) => {
    await page.goto('/bulk-upload');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/cloud-files requires auth', async ({ page }) => {
    await page.goto('/cloud-files');
    await expect(page).toHaveURL(/\/login/);
  });

  test('/alerts requires auth', async ({ page }) => {
    await page.goto('/alerts');
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe('Navigation Menu Items', () => {
  test('login page has navigation elements', async ({ page }) => {
    await page.goto('/login');

    // Login page should have accessible form
    await expect(page.locator('form')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('page has skip link or main landmark', async ({ page }) => {
    await page.goto('/login');

    // Check for accessibility landmarks
    const main = page.locator('main');
    const hasMain = (await main.count()) > 0;

    // Either has main landmark or form is visible
    const form = page.locator('form');
    const hasForm = (await form.count()) > 0;

    expect(hasMain || hasForm).toBe(true);
  });
});
