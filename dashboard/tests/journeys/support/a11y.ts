import { expect, type Page, type TestInfo } from '@playwright/test';
import { appendFile, mkdir } from 'node:fs/promises';
import { injectAxe, getViolations } from 'axe-playwright';

const BLOCKING = new Set(['serious', 'critical']);
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

export interface AxeSummary {
  page: string;
  url: string;
  blocking: number;
  minorOrModerate: number;
}

export interface AxeOptions {
  /** CSS selectors excluded from the scan. Every use needs a written reason. */
  exclude?: string[];
}

/**
 * Run axe-core on the page as it currently renders and fail on serious or
 * critical violations. Lesser findings are recorded in the report, not hidden.
 */
export async function assertNoBlockingViolations(
  page: Page,
  label: string,
  testInfo: TestInfo,
  options: AxeOptions = {},
): Promise<AxeSummary> {
  // axe reads colours as rendered, so let entrance transitions (toasts fade in over
  // 300ms) finish; a half-faded element is a measurement artefact, not a finding.
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))));
  await injectAxe(page);
  const context = options.exclude?.length ? { exclude: options.exclude.map((s) => [s]) } : undefined;
  const violations = await getViolations(page, context as never, {
    runOnly: { type: 'tag', values: WCAG_TAGS },
  });
  const blocking = violations.filter((v) => BLOCKING.has(v.impact ?? ''));
  const summary: AxeSummary = {
    page: label,
    url: page.url(),
    blocking: blocking.length,
    minorOrModerate: violations.length - blocking.length,
  };
  await mkdir('journey-results', { recursive: true });
  await appendFile('journey-results/axe-summary.jsonl', `${JSON.stringify(summary)}\n`);
  await testInfo.attach(`axe-${label}`, {
    body: JSON.stringify({ ...summary, violations }, null, 2),
    contentType: 'application/json',
  });
  expect(
    blocking.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      targets: v.nodes.map((n) => n.target.join(' ')),
      detail: v.nodes.map((n) => n.any[0]?.message ?? n.failureSummary),
    })),
    `axe found serious or critical violations on "${label}" (${page.url()})`,
  ).toEqual([]);
  return summary;
}
