import { test as setup, expect } from '@playwright/test';
import { adminEmail, inboxSnapshot, newMagicLink } from './support/stack';
import { assertNoBlockingViolations } from './support/a11y';
import { pressWithKeyboard, rejectCookiesWithKeyboard, resetFocus, typeInto } from './support/keyboard';

export const storageStatePath = 'journey-results/.auth/admin.json';

/**
 * First-run bootstrap exactly as a self-hosting administrator does it: request
 * a magic link for the first email, follow it from the delivered message and
 * confirm. On an empty database that person becomes the administrator. The
 * whole sequence is driven from the keyboard, and axe runs on each page.
 */
setup('the first administrator signs in with a magic link', async ({ page }, testInfo) => {
  const seen = await inboxSnapshot();

  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Welcome Back' })).toBeVisible();
  await assertNoBlockingViolations(page, 'login', testInfo);

  await resetFocus(page);
  await rejectCookiesWithKeyboard(page);
  await resetFocus(page);
  await typeInto(page, page.getByLabel('Email Address'), adminEmail);
  await pressWithKeyboard(page, page.getByRole('button', { name: 'Send Magic Link' }));

  const link = await newMagicLink(adminEmail, seen);
  await page.goto(link);
  await expect(page.getByRole('heading', { name: 'Complete Sign In' })).toBeVisible();
  await assertNoBlockingViolations(page, 'verify-magic-link', testInfo);
  await resetFocus(page);
  await pressWithKeyboard(page, page.getByRole('button', { name: 'Verify & Sign In' }));

  await page.waitForURL((url) => url.pathname === '/dashboard');
  await expect(page.getByRole('link', { name: 'Admin' }).first()).toBeVisible();
  await assertNoBlockingViolations(page, 'dashboard', testInfo);

  await page.context().storageState({ path: storageStatePath });
});
