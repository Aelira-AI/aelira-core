import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet, scanIdFromUrl } from './support/scans';
import { dismissCookieBanner, webFixtureUrl } from './support/stack';

const siteRoot = resolve(import.meta.dirname, '../../../tests/journeys/site');

interface WebIssue {
  page_url: string;
  criterion: string;
  description: string;
  element: string;
  fix: string;
  generated_code_fix: string | null;
}

/**
 * The target is a static site served from inside the stack's own network on a
 * routable-looking address (the product refuses private and loopback targets).
 * Every finding below must be traceable to markup in tests/journeys/site.
 */
test('web: a controlled local site is scanned and every finding traces to its markup', async ({ page }, testInfo) => {
  const index = await readFile(resolve(siteRoot, 'index.html'), 'utf8');

  await page.goto('/upload');
  await dismissCookieBanner(page);
  await page.getByRole('button', { name: /^Website WCAG/ }).click();
  await assertNoBlockingViolations(page, 'web-scan-form', testInfo);
  await page.getByLabel('Website URL').fill(webFixtureUrl);
  await page.getByLabel('Crawl Depth').selectOption({ label: '2 levels (+ linked pages)' });
  await page.getByRole('button', { name: 'Scan Website' }).click();

  await expect(page.getByText('Compliance Score').first()).toBeVisible({ timeout: 180_000 });
  const scanId = scanIdFromUrl(page.url());
  const record = await apiGet<{
    scan: { status: string; result: { compliance_score: number; issues: WebIssue[] } };
  }>(page.request, `/education/scans/${scanId}`);
  expect(record.scan.status.toLowerCase()).toBe('completed');
  const issues = record.scan.result.issues;

  // Findings come from the page we served, on the address we served it from.
  expect(issues.length).toBeGreaterThan(0);
  const syllabusUrl = new URL('syllabus.html', webFixtureUrl).toString();
  const syllabus = await readFile(resolve(siteRoot, 'syllabus.html'), 'utf8');
  for (const issue of issues) {
    expect([webFixtureUrl, syllabusUrl], 'no finding may come from anywhere else').toContain(issue.page_url);
    const source = issue.page_url === syllabusUrl ? syllabus : index;
    expect(source, `the element in "${issue.description}" must exist in the fixture`).toContain(issue.element);
  }
  const onIndex = issues.filter((i) => i.page_url === webFixtureUrl);
  const described = (needle: RegExp) => onIndex.filter((i) => needle.test(i.description));
  expect(described(/buttons have discernible text/i)[0]?.element).toBe('<button></button>');
  expect(described(/alternative text/i)[0]?.element).toBe('<img src="chart.svg">');
  expect(described(/links have discernible text/i)[0]?.element).toBe('<a href="syllabus.html"></a>');
  expect(described(/every form element has a label/i)[0]?.element).toContain('name="student"');
  expect(described(/lang attribute/i).length).toBe(1);
  expect(described(/contrast between foreground/i)[0]?.fix).toMatch(/color contrast of 1\.91/);
  // The linked page was crawled too, and only its own picture is reported against it.
  const onSyllabus = issues.filter((i) => i.page_url === syllabusUrl);
  expect(onSyllabus.map((i) => i.element)).toEqual(['<img src="week1.svg">']);
  expect(new Set(issues.map((i) => i.criterion))).toEqual(
    new Set(['1.1.1', '1.4.3', '2.4.4', '3.1.1', '4.1.2', 'best-practice']),
  );

  // The report shows the same findings the API holds.
  await expect(page.getByRole('heading', { name: `Rule-based findings (${issues.length})` })).toBeVisible();
  await expect(page.getByText('Criterion 1.1.1: 2 total issues')).toBeVisible();
  await assertNoBlockingViolations(page, 'web-scan-report', testInfo);

  // No AI provider is configured here, so no proposed code change may exist.
  expect(issues.every((i) => i.generated_code_fix === null)).toBe(true);
  const review = await apiGet<{
    total_fixes: number;
    fixes: { review_status: string; fix_method: string; review_digest: string }[];
  }>(page.request, `/api/reviews/${scanId}`);
  expect(review.total_fixes).toBe(issues.length);
  for (const fix of review.fixes) {
    expect(fix.review_status).toBe('pending');
    expect(fix.fix_method).toBe('heuristic');
    expect(fix.review_digest).toMatch(/^[a-f0-9]{64}$/);
  }

  // The remediation page lists the same findings and does not pretend a job ran.
  await page.getByRole('link', { name: 'Open Remediation Review' }).click();
  await expect(page.getByRole('button', { name: 'Start Remediation' })).toBeVisible();
  const refused = page.waitForResponse((r) => r.url().includes('/education/remediate/'));
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  expect((await refused).status(), 'a website has no saved file to remediate').toBe(400);
  await expect(page.getByRole('heading', { name: 'Remediation could not be started' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Download/ })).toHaveCount(0);
  await assertNoBlockingViolations(page, 'web-remediation-not-started', testInfo);
});
