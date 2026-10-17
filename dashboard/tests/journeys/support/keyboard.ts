import { expect, type Locator, type Page } from '@playwright/test';

/**
 * Move focus with Tab until the target holds it. Failing here means a real
 * user without a pointer could not reach the control.
 */
export async function tabTo(page: Page, target: Locator, maxTabs = 120): Promise<void> {
  for (let presses = 0; presses <= maxTabs; presses += 1) {
    const focused = await target
      .evaluate((el) => el === document.activeElement, undefined, { timeout: 2_000 })
      .catch(() => false);
    if (focused) return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`Keyboard focus never reached ${target} after ${maxTabs} Tab presses`);
}

/** Reach a control with Tab and activate it with Enter. */
export async function pressWithKeyboard(page: Page, target: Locator): Promise<void> {
  await expect(target).toBeVisible();
  await tabTo(page, target);
  await page.keyboard.press('Enter');
}

/** Start from the top of the document so Tab order is measured from a known place. */
export async function resetFocus(page: Page): Promise<void> {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
  });
}

/** Type into a field that was reached with the keyboard. */
export async function typeInto(page: Page, field: Locator, text: string): Promise<void> {
  await tabTo(page, field);
  await page.keyboard.type(text);
}

/** Dismiss the cookie notice the way a keyboard user must. */
export async function rejectCookiesWithKeyboard(page: Page): Promise<void> {
  // The notice appears half a second after load.
  const reject = page.getByRole('button', { name: 'Reject All', exact: true });
  await expect(reject).toBeVisible();
  await pressWithKeyboard(page, reject);
  await expect(reject).toBeHidden();
}
