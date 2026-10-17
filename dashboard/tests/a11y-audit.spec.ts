import { test, expect, type Locator, type Page } from '@playwright/test';
import { injectAxe, getViolations } from 'axe-playwright';
import { CLOUD_FILE_ID, SCAN_ID, installApiMock, type MockOptions } from './fixtures/a11y-api';

/**
 * Dashboard accessibility regression suite (issue #376).
 *
 * Every route and main state is rendered against synthetic API fixtures and
 * checked with axe-core (WCAG 2.0/2.1 A and AA rules) in the light and dark
 * themes at 1440px and 375px. Keyboard behaviour that axe cannot see (dialog
 * focus management, switch names, toast timing, announcements, titles,
 * reflow and control boundaries) is driven with real key presses.
 *
 * Automated checks do not establish conformance. Screen-reader coverage is
 * recorded separately in dashboard/SCREEN_READER_TESTING.md.
 */

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const THEMES = ['light', 'dark'] as const;
const WIDTHS = [1440, 375] as const;
const CONTENT_URL = `/canvas/courses/101/content/${CLOUD_FILE_ID}/review`;

interface AuditState {
  name: string;
  path: string;
  mock?: MockOptions;
  /** Locator that is visible once the state is fully rendered. */
  ready: (page: Page) => Locator;
  /** What to wait for before `act` runs, when `ready` only appears after it. */
  loaded?: (page: Page) => Locator;
  /** Moves the page into the state being audited (open a dialog, expand a row). */
  act?: (page: Page) => Promise<void>;
  /** Leave the first-visit cookie notice in place. */
  keepCookieNotice?: boolean;
}

const heading = (name: string | RegExp) => (page: Page) =>
  page.getByRole('heading', { name }).first();
const main = (text: string | RegExp) => (page: Page) =>
  page.locator('#main-content').getByText(text).locator('visible=true').first();

const STATES: AuditState[] = [
  { name: 'sign-in', path: '/login', mock: { authenticated: false }, ready: heading('Welcome Back') },
  {
    name: 'sign-in with cookie notice', path: '/login', mock: { authenticated: false }, keepCookieNotice: true,
    ready: (page) => page.getByRole('dialog', { name: 'Cookie Notice' }),
  },
  {
    name: 'sign-in cookie preferences', path: '/login', mock: { authenticated: false }, keepCookieNotice: true,
    loaded: (page) => page.getByRole('dialog', { name: 'Cookie Notice' }),
    ready: (page) => page.getByRole('dialog', { name: 'Cookie Preferences' }),
    act: async (page) => {
      await page.getByRole('dialog', { name: 'Cookie Notice' }).getByRole('button', { name: 'Preferences' }).click();
    },
  },
  {
    name: 'sign-in with SSO options', path: '/login', mock: { authenticated: false },
    loaded: (page) => page.getByLabel('Email Address'),
    ready: (page) => page.getByRole('button', { name: 'Google' }),
    act: async (page) => { await page.getByLabel('Email Address').fill('faculty@uni.edu'); },
  },
  {
    name: 'sign-in API key form', path: '/login', mock: { authenticated: false },
    loaded: (page) => page.getByRole('button', { name: /Use API Key/ }),
    ready: (page) => page.getByLabel('API Key'),
    act: async (page) => { await page.getByRole('button', { name: /Use API Key/ }).click(); },
  },
  {
    name: 'sign-in magic link sent', path: '/login', mock: { authenticated: false },
    loaded: (page) => page.getByLabel('Email Address'),
    ready: (page) => page.getByRole('status').filter({ hasText: 'Check your email' }),
    act: async (page) => {
      await page.route('**/auth/magic-link/request', (route) =>
        route.request().method() === 'OPTIONS'
          ? route.fallback()
          : route.fulfill({ status: 200, contentType: 'application/json', body: '{}', headers: { 'access-control-allow-origin': 'http://localhost:5173', 'access-control-allow-credentials': 'true' } }));
      await page.getByLabel('Email Address').fill('faculty@uni.edu');
      await page.getByRole('button', { name: 'Send Magic Link' }).click();
    },
  },
  {
    name: 'sign-in error', path: '/login', mock: { authenticated: false },
    loaded: (page) => page.getByLabel('Email Address'),
    ready: (page) => page.getByRole('alert').filter({ hasText: 'Too many requests' }),
    act: async (page) => {
      await page.route('**/auth/magic-link/request', (route) =>
        route.request().method() === 'OPTIONS'
          ? route.fallback()
          : route.fulfill({ status: 429, contentType: 'application/json', body: '{"detail":"Too many requests"}', headers: { 'access-control-allow-origin': 'http://localhost:5173', 'access-control-allow-credentials': 'true' } }));
      await page.getByLabel('Email Address').fill('faculty@uni.edu');
      await page.getByRole('button', { name: 'Send Magic Link' }).click();
    },
  },
  { name: 'dashboard', path: '/dashboard', ready: heading(/Good (morning|afternoon|evening), Alex/) },
  { name: 'dashboard empty', path: '/dashboard', mock: { scenario: 'empty' }, ready: heading(/Good (morning|afternoon|evening), Alex/) },
  {
    name: 'navigation menu open', path: '/dashboard', ready: heading(/Good (morning|afternoon|evening), Alex/),
    act: async (page) => {
      const toggle = page.getByRole('button', { name: 'Open navigation menu' });
      if (await toggle.isVisible()) await toggle.click();
    },
  },
  {
    name: 'account menu open', path: '/dashboard', ready: heading(/Good (morning|afternoon|evening), Alex/),
    act: async (page) => { await page.getByRole('button', { name: /Account menu/ }).click(); },
  },
  { name: 'upload', path: '/upload', ready: heading('New Scan') },
  {
    name: 'upload PDF', path: '/upload', loaded: heading('PDF Documents'),
    ready: (page) => page.getByRole('button', { name: 'Browse files' }),
    act: async (page) => { await page.getByRole('heading', { name: 'PDF Documents' }).click(); },
  },
  {
    name: 'upload website', path: '/upload', loaded: heading('Website'),
    ready: (page) => page.getByLabel('Crawl Depth'),
    act: async (page) => { await page.getByRole('heading', { name: 'Website', exact: true }).click(); },
  },
  { name: 'scan history', path: '/history', ready: main('Quarterly enrolment report.pdf') },
  { name: 'scan history empty', path: '/history', mock: { scenario: 'empty' }, ready: heading('Scan History') },
  { name: 'scan history error', path: '/history', mock: { scenario: 'error' }, ready: (page) => page.getByRole('alert').filter({ hasText: 'Error loading scan history' }) },
  { name: 'scan history loading', path: '/history', mock: { scenario: 'loading' }, ready: (page) => page.getByRole('status', { name: 'Loading scan history' }) },
  {
    name: 'scan history delete dialog', path: '/history', loaded: (page) => page.getByRole('button', { name: 'Delete Quarterly enrolment report.pdf' }),
    ready: (page) => page.getByRole('dialog', { name: 'Delete Scan' }),
    act: async (page) => { await page.getByRole('button', { name: 'Delete Quarterly enrolment report.pdf' }).click(); },
  },
  { name: 'issues', path: '/issues', ready: main('Image is missing alternative text') },
  { name: 'issues empty', path: '/issues', mock: { scenario: 'empty' }, ready: heading('Issue Management') },
  {
    name: 'issues expanded', path: '/issues', ready: main('Image is missing alternative text'),
    act: async (page) => { await page.locator('#main-content button[aria-expanded]').first().click(); },
  },
  { name: 'scan detail', path: `/scan/${SCAN_ID}`, ready: heading('Quarterly enrolment report.pdf') },
  { name: 'scan detail processing', path: `/scan/${SCAN_ID}`, mock: { scenario: 'processing' }, ready: (page) => page.getByRole('progressbar', { name: 'Scan progress' }) },
  { name: 'scan detail error', path: `/scan/${SCAN_ID}`, mock: { scenario: 'error' }, ready: (page) => page.getByRole('alert').filter({ hasText: 'could not be loaded' }) },
  { name: 'scan detail loading', path: `/scan/${SCAN_ID}`, mock: { scenario: 'loading' }, ready: (page) => page.getByRole('status', { name: /Loading/ }) },
  { name: 'remediation', path: `/remediate/${SCAN_ID}`, ready: heading('Auto-Remediation') },
  { name: 'review queue', path: '/review', ready: main('Quarterly enrolment report.pdf') },
  { name: 'review queue empty', path: '/review', mock: { scenario: 'empty' }, ready: heading('Review Queue') },
  { name: 'review queue error', path: '/review', mock: { scenario: 'error' }, ready: heading('Review Queue') },
  { name: 'document review', path: `/review/${SCAN_ID}`, ready: main('Image is missing alternative text') },
  {
    name: 'document review fix expanded', path: `/review/${SCAN_ID}`, ready: main('Image is missing alternative text'),
    act: async (page) => { await page.getByRole('button', { name: /Image is missing alternative text/ }).click(); },
  },
  {
    name: 'document review reading order', path: `/review/${SCAN_ID}`, loaded: (page) => page.getByRole('button', { name: 'Show comparison' }),
    ready: (page) => page.getByRole('button', { name: 'Hide comparison' }),
    act: async (page) => { await page.getByRole('button', { name: 'Show comparison' }).click(); },
  },
  {
    name: 'document review PDF structure editor', path: `/review/${SCAN_ID}`, loaded: (page) => page.getByRole('button', { name: 'Edit PDF structure' }),
    ready: (page) => page.getByRole('button', { name: /Close|Hide|Cancel/ }).first(),
    act: async (page) => { await page.getByRole('button', { name: 'Edit PDF structure' }).click(); },
  },
  { name: 'settings', path: '/settings', ready: heading('Settings') },
  {
    name: 'settings delete account dialog', path: '/settings', loaded: heading('Settings'),
    ready: (page) => page.getByRole('dialog', { name: 'Delete Account' }),
    act: async (page) => { await page.getByRole('button', { name: 'Delete Account' }).click(); },
  },
  { name: 'admin', path: '/admin', ready: heading('Admin Dashboard') },
  {
    name: 'admin invite dialog', path: '/admin', loaded: heading('Admin Dashboard'),
    ready: (page) => page.getByRole('dialog', { name: 'Invite New User' }),
    act: async (page) => { await page.getByRole('button', { name: 'Invite User' }).click(); },
  },
  { name: 'integrations', path: '/integrations', ready: heading('Platform Integrations') },
  { name: 'alert settings', path: '/integrations/settings', ready: heading('Alert Settings') },
  { name: 'bulk upload', path: '/bulk-upload', ready: heading('Bulk Upload') },
  { name: 'cloud files', path: '/integrations/files', ready: main('Syllabus.docx') },
  { name: 'cloud files empty', path: '/integrations/files', mock: { scenario: 'empty' }, ready: heading('No files found') },
  { name: 'canvas courses', path: '/integrations/canvas', ready: heading('Introduction to Statistics') },
  {
    name: 'canvas course expanded', path: '/integrations/canvas', loaded: (page) => page.getByRole('button', { name: /Introduction to Statistics/ }),
    ready: main('Week 1 lecture notes.pdf'),
    act: async (page) => {
      await page.getByRole('button', { name: /Introduction to Statistics/ }).click();
      await page.mouse.move(700, 700);
    },
  },
  { name: 'canvas course content', path: '/canvas/courses/101/content', ready: main('Week 1 overview page') },
  { name: 'canvas content review', path: CONTENT_URL, ready: (page) => page.getByRole('region', { name: 'Original content' }) },
  { name: 'brightspace courses', path: '/integrations/brightspace', ready: (page) => page.getByRole('link', { name: /Introduction to Statistics/ }) },
  { name: 'brightspace course content', path: '/brightspace/courses/1/content', ready: main('Week 1 overview page') },
  { name: 'brightspace content review', path: `/brightspace/courses/1/content/${CLOUD_FILE_ID}/review`, ready: main('Image is missing alternative text') },
];

async function openState(page: Page, state: AuditState, theme: (typeof THEMES)[number], width: number): Promise<void> {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(state.path, { waitUntil: 'domcontentloaded' });
  const loaded = state.loaded ?? state.ready;
  await expect(loaded(page).or(page.getByRole('heading', { name: 'Something went wrong' }))).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('heading', { name: 'Something went wrong' })).toHaveCount(0);
  if (!state.keepCookieNotice) {
    const reject = page.getByRole('button', { name: 'Reject All', exact: true });
    if (await reject.isVisible().catch(() => false)) await reject.click();
  }
  if (state.act) await state.act(page);
  await expect(state.ready(page)).toBeVisible();
  // Let hover/focus colour transitions settle so contrast is measured at rest.
  await page.mouse.move(width - 2, 2);
  await page.waitForTimeout(450);
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  await injectAxe(page);
  const violations = await getViolations(page, undefined, { runOnly: { type: 'tag', values: WCAG_TAGS } });
  expect(
    violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
    })),
  ).toEqual([]);
}

test.describe('axe-core on every route and main state', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const state of STATES) {
    test(`${state.name}: no WCAG 2.1 A/AA violations in both themes at 1440px and 375px`, async ({ page }) => {
      test.setTimeout(120_000);
      await installApiMock(page, state.mock);
      for (const theme of THEMES) {
        for (const width of WIDTHS) {
          await test.step(`${theme} ${width}px`, async () => {
            await openState(page, state, theme, width);
            await expectNoAxeViolations(page);
          });
        }
      }
    });
  }
});

test.describe('reflow at 320 CSS pixels', () => {
  const reflowRoutes = [
    '/dashboard', '/upload', '/history', '/issues', `/scan/${SCAN_ID}`, `/remediate/${SCAN_ID}`, '/review',
    `/review/${SCAN_ID}`, '/settings', '/admin', '/integrations', '/integrations/settings', '/bulk-upload',
    '/integrations/files', '/integrations/canvas', '/canvas/courses/101/content', CONTENT_URL,
    '/brightspace/courses/1/content', `/brightspace/courses/1/content/${CLOUD_FILE_ID}/review`,
  ];
  for (const route of reflowRoutes) {
    test(`${route} fits 320px without whole-page horizontal scrolling`, async ({ page }) => {
      await installApiMock(page);
      await page.setViewportSize({ width: 320, height: 800 });
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#main-content h1, #main-content h2').first()).toBeVisible({ timeout: 15000 });
      const reject = page.getByRole('button', { name: 'Reject All', exact: true });
      if (await reject.isVisible().catch(() => false)) await reject.click();
      await page.waitForTimeout(500);
      const size = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }));
      expect(size.scroll).toBeLessThanOrEqual(size.client + 1);
    });
  }
});

async function setup(page: Page, path: string, mock?: MockOptions, width = 1440): Promise<void> {
  await installApiMock(page, mock);
  await page.setViewportSize({ width, height: 900 });
  await page.goto(path, { waitUntil: 'domcontentloaded' });
}

async function dismissCookieNotice(page: Page): Promise<void> {
  const reject = page.getByRole('button', { name: 'Reject All', exact: true });
  await reject.waitFor({ state: 'visible', timeout: 5000 }).catch(() => undefined);
  if (await reject.isVisible().catch(() => false)) await reject.click();
}

/** True when the focused element is inside the given dialog. */
async function focusIsInside(dialog: Locator): Promise<boolean> {
  return dialog.evaluate((element) => element.contains(document.activeElement));
}

async function expectModalKeyboardContract(
  page: Page,
  dialog: Locator,
  trigger: Locator,
  initiallyFocused: Locator,
): Promise<void> {
  await expect(dialog).toBeVisible();
  await expect(initiallyFocused).toBeFocused();
  for (let press = 0; press < 12; press += 1) {
    await page.keyboard.press('Tab');
    expect(await focusIsInside(dialog), `Tab press ${press + 1} left the dialog`).toBe(true);
  }
  for (let press = 0; press < 12; press += 1) {
    await page.keyboard.press('Shift+Tab');
    expect(await focusIsInside(dialog), `Shift+Tab press ${press + 1} left the dialog`).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
}

test.describe('dialogs: focus moves in, stays in, Escape closes, focus returns', () => {
  test('delete scan confirmation', async ({ page }) => {
    await setup(page, '/history');
    await dismissCookieNotice(page);
    const trigger = page.getByRole('button', { name: 'Delete Quarterly enrolment report.pdf' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Delete Scan' });
    await expectModalKeyboardContract(page, dialog, trigger, dialog.getByRole('button', { name: 'Cancel' }));
  });

  test('delete account', async ({ page }) => {
    await setup(page, '/settings');
    await dismissCookieNotice(page);
    const trigger = page.getByRole('button', { name: 'Delete Account' });
    await trigger.scrollIntoViewIfNeeded();
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Delete Account' });
    await expectModalKeyboardContract(page, dialog, trigger, dialog.getByLabel(/Type DELETE to continue/));
  });

  test('invite a user', async ({ page }) => {
    await setup(page, '/admin');
    await dismissCookieNotice(page);
    const trigger = page.getByRole('button', { name: 'Invite User' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Invite New User' });
    await expectModalKeyboardContract(page, dialog, trigger, dialog.getByLabel('Email Address'));
  });

  test('remove a user', async ({ page }) => {
    await setup(page, '/admin');
    await dismissCookieNotice(page);
    const trigger = page.getByRole('button', { name: 'Remove sam@example.test' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('alertdialog', { name: 'Remove User' });
    await expectModalKeyboardContract(page, dialog, trigger, dialog.getByRole('button', { name: 'Cancel' }));
  });

  test('cookie preferences', async ({ page }) => {
    await setup(page, '/login', { authenticated: false });
    const banner = page.getByRole('dialog', { name: 'Cookie Notice' });
    await expect(banner).toBeVisible();
    const trigger = banner.getByRole('button', { name: 'Preferences' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Cookie Preferences' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Close preferences' })).toBeFocused();
    for (let press = 0; press < 10; press += 1) {
      await page.keyboard.press('Tab');
      expect(await focusIsInside(dialog)).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('dialog', { name: 'Cookie Notice' }).getByRole('button', { name: 'Preferences' })).toBeFocused();
  });

  test('navigation menu (control: already correct before this change)', async ({ page }) => {
    await setup(page, '/dashboard', undefined, 375);
    await dismissCookieNotice(page);
    const toggle = page.getByRole('button', { name: /navigation menu/ });
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    for (let press = 0; press < 16; press += 1) {
      await page.keyboard.press('Tab');
      expect(await page.evaluate(() => !!document.activeElement?.closest('#mobile-sidebar'))).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toBeFocused();
  });
});

test.describe('controls named for assistive technology', () => {
  test('email notification switches have names, state and descriptions', async ({ page }) => {
    await setup(page, '/settings');
    await dismissCookieNotice(page);
    const scanComplete = page.getByRole('switch', { name: 'Scan Complete' });
    await expect(scanComplete).toHaveAttribute('aria-checked', 'true');
    await expect(scanComplete).toHaveAccessibleDescription('Get notified when a document scan finishes');
    await scanComplete.focus();
    await page.keyboard.press('Space');
    await expect(scanComplete).toHaveAttribute('aria-checked', 'false');
    await expect(page.getByRole('switch', { name: 'Remediation Complete' })).toHaveAttribute('aria-checked', 'false');
    await expect(page.getByRole('combobox', { name: 'Day of the week for the weekly summary' })).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Hour of the day for the weekly summary, UTC' })).toBeVisible();
  });

  test('alert settings controls are named and the pause switch reports state', async ({ page }) => {
    await setup(page, '/integrations/settings');
    await dismissCookieNotice(page);
    const pause = page.getByRole('switch', { name: 'Pause All Alerts' });
    await expect(pause).toHaveAttribute('aria-checked', 'false');
    await pause.focus();
    await page.keyboard.press('Space');
    await expect(pause).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('button', { name: 'Remove alex@example.test' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Email address to add' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add email address' })).toBeVisible();
    await expect(page.getByLabel('Day')).toBeVisible();
    await expect(page.getByLabel('Time (UTC)')).toBeVisible();
  });

  test('profile fields and session sign-out buttons identify what they act on', async ({ page }) => {
    await setup(page, '/settings');
    await dismissCookieNotice(page);
    await page.getByRole('button', { name: 'Edit' }).first().click();
    await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveAttribute('autocomplete', 'name');
    await expect(page.getByRole('combobox', { name: 'Timezone' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Sign out .+ on .+/ })).toBeVisible();
  });

  test('issue and fix toggles expose state without repeating it in their name', async ({ page }) => {
    await setup(page, `/review/${SCAN_ID}`);
    await dismissCookieNotice(page);
    const fix = page.getByRole('button', { name: /Image is missing alternative text/ });
    await expect(fix).toHaveAttribute('aria-expanded', 'false');
    expect(await fix.evaluate((element) => element.getAttribute('aria-label'))).toBeNull();
    expect(await fix.evaluate((element) => /click to (expand|collapse)/i.test(element.textContent ?? ''))).toBe(false);
  });

  test('Canvas content review: view mode is a pressed-state toggle and the original pane notes missing alt text', async ({ page }) => {
    await setup(page, CONTENT_URL);
    await dismissCookieNotice(page);
    const group = page.getByRole('group', { name: 'View mode' });
    await expect(group.getByRole('button', { name: 'Preview' })).toHaveAttribute('aria-pressed', 'true');
    await expect(group.getByRole('button', { name: 'HTML Diff' })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('tablist')).toHaveCount(0);
    const original = page.getByRole('region', { name: 'Original content' });
    await expect(original.getByRole('img', { name: 'Image with no alternative text in the original' })).toBeVisible();
    const remediated = page.getByRole('region', { name: 'Remediated content' });
    await expect(remediated.getByRole('img', { name: 'Bar chart of enrolment' })).toBeVisible();
  });
});

test.describe('announcements and titles', () => {
  test('each route sets its own document title and navigation is announced', async ({ page }) => {
    await setup(page, '/dashboard');
    await dismissCookieNotice(page);
    await expect(page).toHaveTitle('Dashboard | Aelira Dashboard');
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'History' }).click();
    await expect(page).toHaveTitle('Scan history | Aelira Dashboard');
    await expect(page.locator('[data-route-announcer]')).toHaveText('Scan history');
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Settings' }).click();
    await expect(page).toHaveTitle('Settings | Aelira Dashboard');
    await expect(page.locator('[data-route-announcer]')).toHaveText('Settings');
  });

  test('titles are distinct across the audited routes', async ({ page }) => {
    await installApiMock(page);
    const titles = new Map<string, string>();
    for (const route of ['/dashboard', '/upload', '/history', '/issues', '/review', '/settings', '/admin', '/integrations', '/integrations/files', '/integrations/canvas']) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await expect.poll(() => page.title()).not.toBe('Aelira Dashboard - Accessibility Compliance');
      titles.set(route, await page.title());
    }
    expect(new Set(titles.values()).size).toBe(titles.size);
  });

  test('scan progress is exposed as a progress bar with a polite status message', async ({ page }) => {
    await setup(page, `/scan/${SCAN_ID}`, { scenario: 'processing' });
    await dismissCookieNotice(page);
    const bar = page.getByRole('progressbar', { name: 'Scan progress' });
    await expect(bar).toHaveAttribute('aria-valuenow', '40', { timeout: 10_000 });
    await expect(bar).toHaveAttribute('aria-valuemin', '0');
    await expect(bar).toHaveAttribute('aria-valuemax', '100');
    await expect(page.getByRole('status').filter({ hasText: 'Checking images for alternative text' })).toBeVisible();
  });

  test('loading states are announced', async ({ page }) => {
    for (const [route, name] of [
      ['/history', 'Loading scan history'],
      [`/scan/${SCAN_ID}`, /Loading/],
      ['/integrations/files', 'Loading cloud files'],
      ['/integrations', 'Loading integrations'],
    ] as const) {
      await setup(page, route, { scenario: 'loading' });
      await expect(page.getByRole('status', { name })).toBeVisible();
    }
  });
});

test.describe('notifications', () => {
  async function deleteFirstScanWithKeyboard(page: Page): Promise<void> {
    await page.getByRole('button', { name: 'Delete Quarterly enrolment report.pdf' }).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('dialog', { name: 'Delete Scan' }).getByRole('button', { name: /^Delete$/ }).click();
  }

  test('a status notification waits while it has focus and is dismissed by Escape', async ({ page }) => {
    await page.clock.install();
    await setup(page, '/history');
    await dismissCookieNotice(page);
    await page.clock.runFor(1000);
    await deleteFirstScanWithKeyboard(page);

    const region = page.getByRole('region', { name: 'Notifications' });
    const toast = region.getByRole('status').getByText('has been deleted');
    await expect(toast).toBeVisible();
    await region.getByRole('button', { name: 'Close notification' }).focus();

    // Far longer than the 5 second display time: focus must hold it open.
    await page.clock.runFor(30_000);
    await expect(toast).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(toast).toBeHidden();
  });

  test('a status notification still expires once focus leaves it', async ({ page }) => {
    await page.clock.install();
    await setup(page, '/history');
    await dismissCookieNotice(page);
    await page.clock.runFor(1000);
    await deleteFirstScanWithKeyboard(page);
    const toast = page.getByRole('region', { name: 'Notifications' }).getByText('has been deleted');
    await expect(toast).toBeVisible();
    await page.clock.runFor(6000);
    await expect(toast).toBeHidden();
  });

  test('errors interrupt through an alert, other messages through a polite status region', async ({ page }) => {
    await setup(page, '/settings');
    await dismissCookieNotice(page);
    const region = page.getByRole('region', { name: 'Notifications' });
    await expect(region.getByRole('alert').getByText('Failed to load LLM providers')).toBeVisible();
    await expect(region.getByRole('status')).toHaveAttribute('aria-live', 'polite');
    expect(await region.getByRole('alert').first().getAttribute('aria-live')).toBeNull();
  });
});

test.describe('non-text contrast of form controls (WCAG 1.4.11)', () => {
  async function weakestControlBoundary(page: Page): Promise<{ name: string; ratio: number }[]> {
    return page.evaluate(() => {
      const parse = (value: string) => {
        const match = value.match(/rgba?\(([^)]+)\)/);
        if (!match) return null;
        const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
        return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
      };
      type Colour = { r: number; g: number; b: number; a: number };
      const luminance = (c: Colour) => {
        const channel = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
      };
      const ratio = (a: Colour, b: Colour) => {
        const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
      };
      const over = (top: Colour, bottom: Colour): Colour => ({
        r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a),
        b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
      });
      const backdrop = (element: Element | null): Colour => {
        const layers: Colour[] = [];
        for (let node = element; node; node = node.parentElement) {
          const colour = parse(getComputedStyle(node).backgroundColor);
          if (colour && colour.a > 0) layers.push(colour);
          if (colour && colour.a === 1) break;
        }
        let base: Colour = document.documentElement.classList.contains('dark')
          ? { r: 0, g: 0, b: 0, a: 1 } : { r: 255, g: 255, b: 255, a: 1 };
        for (const layer of layers.reverse()) base = over(layer, base);
        return base;
      };
      const failures: { name: string; ratio: number }[] = [];
      const controls = document.querySelectorAll<HTMLElement>(
        '#main-content input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=hidden]), #main-content select, #main-content textarea',
      );
      for (const control of controls) {
        if (!control.getClientRects().length) continue;
        const style = getComputedStyle(control);
        const outside = backdrop(control.parentElement);
        const border = parse(style.borderTopColor);
        const fill = parse(style.backgroundColor);
        const borderRatio = parseFloat(style.borderTopWidth) > 0 && border ? ratio(over(border, outside), outside) : 0;
        const fillRatio = fill && fill.a > 0 ? ratio(over(fill, outside), outside) : 1;
        const best = Math.max(borderRatio, fillRatio);
        if (best < 3) {
          failures.push({ name: control.getAttribute('aria-label') ?? control.id ?? control.tagName, ratio: Math.round(best * 100) / 100 });
        }
      }
      return failures;
    });
  }

  for (const theme of THEMES) {
    for (const route of ['/issues', '/settings', '/integrations/settings', '/review', '/admin']) {
      test(`${route} control boundaries reach 3:1 in the ${theme} theme`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: theme });
        await setup(page, route);
        await expect(page.locator('#main-content h1').first()).toBeVisible({ timeout: 15000 });
        await dismissCookieNotice(page);
        await page.waitForTimeout(400);
        expect(await weakestControlBoundary(page)).toEqual([]);
      });
    }
  }
});

/**
 * Effective colours measured the way a browser paints them. Computed colours
 * can be oklch/oklab with alpha, so they are resolved by painting each
 * ancestor background, then the text, onto a one pixel canvas.
 */
async function measureTextContrast(locator: Locator): Promise<number> {
  return locator.evaluate((element) => {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext('2d', { willReadFrequently: true })!;
    const paint = (css: string) => { context.fillStyle = css; context.fillRect(0, 0, 1, 1); };
    const read = () => Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3));
    const luminance = ([r, g, b]: number[]) => {
      const channel = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    paint(document.documentElement.classList.contains('dark') ? '#000' : '#fff');
    const chain: Element[] = [];
    for (let node: Element | null = element; node; node = node.parentElement) chain.unshift(node);
    for (const node of chain) paint(getComputedStyle(node).backgroundColor);
    const background = read();
    paint(getComputedStyle(element).color);
    const foreground = read();
    const [hi, lo] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
    return (hi + 0.05) / (lo + 0.05);
  });
}

test.describe('text contrast that axe-core cannot resolve', () => {
  for (const theme of THEMES) {
    test(`review category chip and confidence badge reach 4.5:1 in the ${theme} theme`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await setup(page, `/review/${SCAN_ID}`);
      await expect(page.getByRole('button', { name: /Image is missing alternative text/ })).toBeVisible({ timeout: 15000 });
      await dismissCookieNotice(page);
      const row = page.getByRole('button', { name: /Image is missing alternative text/ });
      expect(await measureTextContrast(row.getByText('Alt Text', { exact: true }))).toBeGreaterThanOrEqual(4.5);
      expect(await measureTextContrast(row.getByText('62%', { exact: true }))).toBeGreaterThanOrEqual(4.5);
    });

    test(`LMS brand tiles and notification descriptions reach 4.5:1 in the ${theme} theme`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await setup(page, '/integrations');
      await expect(page.getByRole('heading', { name: 'Platform Integrations' })).toBeVisible({ timeout: 15000 });
      await dismissCookieNotice(page);
      for (const mark of ['D2L', 'M', 'C', 'Bb']) {
        expect(await measureTextContrast(page.getByText(mark, { exact: true }).first()), mark).toBeGreaterThanOrEqual(4.5);
      }
      await setup(page, '/settings');
      const description = page.getByText('Get notified when a document scan finishes');
      await expect(description).toBeVisible({ timeout: 15000 });
      expect(await measureTextContrast(description)).toBeGreaterThanOrEqual(4.5);
    });
  }
});

test.describe('failure states name the page and offer a way forward', () => {
  for (const [route, title] of [
    ['/history', 'Scan History'],
    ['/review', 'Review Queue'],
    [`/scan/${SCAN_ID}`, 'Scan details'],
  ] as const) {
    test(`${route} error state has a heading, an alert and a retry action`, async ({ page }) => {
      await setup(page, route, { scenario: 'error' });
      await dismissCookieNotice(page);
      await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible({ timeout: 15000 });
      await expect(page.getByRole('alert').filter({ hasText: 'could not be loaded' }).or(page.getByRole('alert').filter({ hasText: 'Error loading' }))).toBeVisible();
      await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
    });
  }
});

test.describe('other keyboard and naming contracts', () => {
  test('toolbar buttons are named by their visible text', async ({ page }) => {
    await setup(page, '/admin');
    await dismissCookieNotice(page);
    for (const name of ['Invite User', 'Export CSV', 'Export Excel', 'Bulk Export (ZIP)']) {
      await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    }
  });

  test('provider filters report which one is pressed', async ({ page }) => {
    await setup(page, '/integrations/files');
    await dismissCookieNotice(page);
    const group = page.getByRole('group', { name: 'Filter files by provider' });
    await expect(group.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
    await group.getByRole('button', { name: 'Google' }).click();
    await expect(group.getByRole('button', { name: 'Google' })).toHaveAttribute('aria-pressed', 'true');
    await expect(group.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('scrolling tables are keyboard reachable regions at narrow widths', async ({ page }) => {
    await setup(page, '/brightspace/courses/1/content', undefined, 375);
    await dismissCookieNotice(page);
    const region = page.getByRole('region', { name: 'Content by module' });
    await region.focus();
    await expect(region).toBeFocused();
    await expect(page.getByRole('region', { name: 'Course content items' })).toHaveAttribute('tabindex', '0');
  });

  test('search fields are named and show a visible focus indicator', async ({ page }) => {
    await setup(page, '/canvas/courses/101/content');
    await dismissCookieNotice(page);
    const search = page.getByRole('textbox', { name: 'Search content items' });
    await search.focus();
    const outline = await search.evaluate((element) => {
      const wrapper = element.parentElement!;
      const style = getComputedStyle(wrapper);
      return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
    });
    expect(outline.style).not.toBe('none');
    expect(outline.width).toBeGreaterThanOrEqual(2);
    await expect(page.getByRole('combobox', { name: 'Filter by content type' })).toBeVisible();
  });
});
