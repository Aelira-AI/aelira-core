import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'node:fs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/workspace-api.json', import.meta.url), 'utf8'));

async function setup(page: import('@playwright/test').Page, score: number | null = 78, configurationRequired = false, tier = 'department') {
  await page.route(/http:\/\/(localhost:8000|127\.0\.0\.1:5280)\//, async route => {
    const path = new URL(route.request().url()).pathname;
    let response = fixture[path];
    if (path === '/auth/session/validate') response = {...response, department: {...response.department, tier}};
    if (path === '/education/stats') response = { ...response, avg_compliance_score: score, deadline: configurationRequired ? {...response.deadline, applicability: 'configuration_required', days_remaining: null} : response.deadline };
    await route.fulfill({status: response === undefined ? 404 : 200,
      contentType: 'application/json', body: JSON.stringify(response ?? {detail: 'Synthetic endpoint unavailable'})});
  });
  await page.goto('/dashboard', {waitUntil: 'domcontentloaded'});
  await expect(page.getByRole('heading', { name: /Good (morning|afternoon|evening), Alex/ })).toBeVisible();
  const cookies = page.getByRole('button', {name: 'Reject All', exact: true});
  if (await cookies.isVisible()) await cookies.click();
}

for (const theme of ['light', 'dark'] as const) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px: readable overview, working guide and keyboard disclosure`, async ({page}) => {
      await page.setViewportSize({width, height: 900});
      await setup(page);
      const toggle = page.getByRole('button', {name: `Switch to ${theme} mode`, exact: true});
      if (await toggle.isVisible()) await toggle.click();
      const guide = page.getByRole('link', {name: /^Read the guide/});
      await expect(guide).toHaveAttribute('href', 'https://aelira.ai/docs');
      await expect(guide).toHaveAttribute('rel', 'noopener noreferrer');
      const main = page.locator('#main-content');
      await expect(main.getByText('6 verified · 2 unverified', {exact: true})).toBeVisible();
      await expect(main.getByRole('table', {name:'Recent scans'}).getByText('Unverified', {exact:true})).toBeVisible();
      const menu = page.getByRole('button', {name:'Account menu for Alex Taylor'});
      await menu.click();
      await expect(page.getByRole('button', {name: 'Sign out', exact:true})).toBeVisible();
      await menu.press('Escape');
      await expect(menu).toBeFocused();
      await expect(menu).toHaveAttribute('aria-expanded', 'false');
      const size = await page.evaluate(() => ({width:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth}));
      expect(size.scroll).toBeLessThanOrEqual(size.width + 1);
      const audit = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
      expect(audit.violations.map(v => ({id:v.id, nodes:v.nodes.map(n=>({target:n.target, summary:n.failureSummary}))}))).toEqual([]);
    });
  }
}

test('verified zero is displayed as a score, absent score remains unverified', async ({page}) => {
  await setup(page, 0);
  await expect(page.getByText('Needs attention', {exact:true})).toBeVisible();
  await expect(page.getByText('No scans yet', {exact:true})).toHaveCount(0);
  await page.unrouteAll({behavior: 'wait'});
  await setup(page, null);
  await expect(page.getByText('Unverified', {exact:true})).toHaveCount(3);
});

test('welcome upload button navigates and reduced motion is respected', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await setup(page);
  const step = page.getByRole('button', {name: /^Upload a document PDF/});
  expect(await step.evaluate(el => parseFloat(getComputedStyle(el).transitionDuration))).toBeLessThan(0.001);
  await step.click();
  await expect(page).toHaveURL(/\/upload$/);
});

test('incomplete institution setup remains visible and its action reaches the profile', async ({page}) => {
  await setup(page, 78, true);
  await expect(page.getByText('Finish your institution setup, then scan a document and review the findings.', {exact:true})).toBeVisible();
  await page.getByRole('button', {name: /^Configure your institution Verify/}).click();
  await expect(page).toHaveURL(/\/settings#regulatory-profile$/);
});

test('score ring stays square with its stroke inside the SVG at constrained widths', async ({page}) => {
  for (const width of [390, 1024, 1600]) {
    await page.setViewportSize({width, height:900});
    await setup(page);
    const ring = page.getByText('Avg scan score', {exact:true}).locator('..').locator('svg');
    const dimensions = await ring.evaluate(el => {
      const rect = el.getBoundingClientRect();
      const circle = el.querySelector('circle')!;
      const outer = Number(circle.getAttribute('r')) + Number(circle.getAttribute('stroke-width')) / 2;
      const center = Number(circle.getAttribute('cx'));
      return {width:rect.width, height:rect.height, inner:center - outer, outer:center + outer, size:el.viewBox.baseVal.width};
    });
    expect(dimensions.width).toBeCloseTo(dimensions.height, 1);
    expect(dimensions.width).toBe(46);
    expect(dimensions.inner).toBeGreaterThanOrEqual(1);
    expect(dimensions.outer).toBeLessThanOrEqual(dimensions.size - 1);
  }
});
