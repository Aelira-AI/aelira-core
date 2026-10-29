/* global process, Buffer */
import { test, expect, type Page } from '@playwright/test';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rename, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { promisify } from 'node:util';
import { inboxSnapshot, newMagicLink, adminEmail } from '../journeys/support/stack';
import { scanIdFromUrl } from '../journeys/support/scans';

const run = promisify(execFile);

// The Canvas course view needs a Canvas with the journey's OAuth developer key and
// a demo course with generic files, taught by a teacher enrolled in nothing else.
// See docs/assets/readme/screenshots/README.md.
const canvas = process.env.JOURNEY_CANVAS_URL ?? '';
const courseId = process.env.README_CANVAS_COURSE_ID ?? '';
const teacher = process.env.README_CANVAS_TEACHER_LOGIN ?? '';
const teacherPassword = process.env.README_CANVAS_TEACHER_PASSWORD ?? '';

const fixtures = resolve(import.meta.dirname, '../../../tests/fixtures');
const journeyFixtures = resolve(import.meta.dirname, '../../../tests/journeys/fixtures');
const out = process.env.README_SHOTS_OUT || resolve(import.meta.dirname, '../../../docs/assets/readme/screenshots');

interface DemoFile {
  card: RegExp;
  source: string;
  name: string;
  mimeType: string;
}

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

const demoFiles: DemoFile[] = [
  { card: /^Word Documents/, source: `${fixtures}/document_stack/course.docx`, name: 'course-overview.docx', mimeType: DOCX },
  { card: /^Word Documents/, source: `${journeyFixtures}/picture-without-alt.docx`, name: 'plant-growth-practical.docx', mimeType: DOCX },
  { card: /^PDF Documents/, source: `${fixtures}/document_stack/metadata.pdf`, name: 'unit-outline.pdf', mimeType: 'application/pdf' },
];

async function signIn(page: Page): Promise<void> {
  // Record an essential-only cookie choice before any page loads. The banner
  // appears half a second after each load, so dismissing it once is not enough.
  await page.addInitScript(() => {
    localStorage.setItem(
      'aelira-cookie-consent',
      JSON.stringify({ essential: true, functional: false, analytics: false, timestamp: Date.now(), version: '1.0' }),
    );
  });
  const seen = await inboxSnapshot();
  await page.goto('/login');
  await page.getByLabel('Email Address').fill(adminEmail);
  await page.getByRole('button', { name: 'Send Magic Link' }).click();
  await page.goto(await newMagicLink(adminEmail, seen));
  await page.getByRole('button', { name: 'Verify & Sign In' }).click();
  await page.waitForURL((url) => url.pathname === '/dashboard');
}

async function scan(page: Page, file: DemoFile): Promise<string> {
  await page.goto('/upload');
  await page.getByRole('button', { name: file.card }).click();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Browse files' }).click();
  await (await chooser).setFiles({ name: file.name, mimeType: file.mimeType, buffer: await readFile(file.source) });
  await page.getByRole('button', { name: /^Scan 1 file/ }).click();
  await expect(page.getByText(`${file.name} scan complete`, { exact: true })).toBeVisible({ timeout: 120_000 });
  await page.getByRole('link', { name: 'View Full Report' }).click();
  await expect(page.getByText('Loading scan details')).toHaveCount(0);
  return scanIdFromUrl(page.url());
}

/**
 * Save a full-page capture once the page has stopped changing. The score
 * count-up and the charts animate in script, which `animations: 'disabled'`
 * does not stop, and toasts would cover the content.
 */
async function shot(page: Page, name: string): Promise<void> {
  const toasts = page.getByRole('button', { name: 'Close notification' });
  while ((await toasts.count()) > 0) await toasts.first().click();
  await page.mouse.move(0, 0);
  await page.evaluate(() => window.scrollTo(0, 0));
  let previous: Buffer | undefined;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const current = await page.screenshot({ fullPage: true, animations: 'disabled' });
    if (previous?.equals(current)) {
      await page.screenshot({ path: `${out}/${name}.png`, fullPage: true, animations: 'disabled' });
      return;
    }
    previous = current;
    await page.waitForTimeout(500);
  }
  throw new Error(`${name}: the page did not settle`);
}

/** Connect Canvas through the dashboard as an administrator would, with the teacher's consent. */
async function connectCanvas(page: Page): Promise<void> {
  await page.goto('/integrations');
  await page.getByRole('button', { name: 'Connect to Canvas LMS' }).click();
  await page.getByRole('textbox', { name: 'Canvas instance URL' }).fill(canvas);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.waitForURL((url) => url.origin === new URL(canvas).origin, { timeout: 60_000 });
  if (/\/login/.test(new URL(page.url()).pathname)) {
    await page.getByLabel(/email|login/i).first().fill(teacher);
    await page.getByLabel(/password/i).first().fill(teacherPassword);
    await page.getByRole('button', { name: /log in/i }).click();
  }
  const accept = page.getByRole('button', { name: /accept|authorize/i });
  await Promise.race([
    accept.waitFor({ timeout: 30_000 }).then(() => accept.click()),
    page.waitForURL((url) => url.pathname === '/integrations', { timeout: 30_000 }),
  ]);
  await page.waitForURL((url) => url.pathname === '/integrations', { timeout: 60_000 });
  await expect(page.getByRole('button', { name: 'Disconnect from Canvas LMS' })).toBeVisible();
}

/** Scan the course content and wait until no item is left unscanned. */
async function scanCourse(page: Page): Promise<void> {
  await page.goto(`/canvas/courses/${courseId}/content`);
  const items = page.getByRole('row').filter({ hasText: /\.(docx|pdf|pptx|xlsx)/i });
  await expect(items.first()).toBeVisible({ timeout: 90_000 });
  const total = await items.count();
  const queued = page.waitForResponse((r) => r.url().endsWith('/canvas/content/scan') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Scan Content' }).click();
  expect((await queued).status()).toBe(200);
  // The scan is a queued job. Count only once every item row is back, so a
  // reloading table cannot pass for a finished scan.
  await expect
    .poll(
      async () => {
        const refresh = page.getByRole('button', { name: 'Refresh' });
        if (await refresh.isEnabled()) await refresh.click();
        if ((await items.count()) !== total) return -1;
        return items.filter({ hasText: 'Unscanned' }).count();
      },
      { message: 'course content was never scanned', timeout: 300_000, intervals: [5_000] },
    )
    .toBe(0);
  await expect(page.getByRole('button', { name: 'Scan Content' })).toBeVisible({ timeout: 120_000 });
}

/** Render the first page of a downloaded PDF at the README width. */
async function pdfShot(pdfPath: string, name: string): Promise<void> {
  const work = await mkdtemp(join(tmpdir(), 'readme-pdf-'));
  try {
    await run('pdftoppm', ['-png', '-f', '1', '-l', '1', '-scale-to-x', '1440', '-scale-to-y', '-1', '-singlefile', pdfPath, join(work, 'page')]);
    await rename(join(work, 'page.png'), `${out}/${name}.png`);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

test('capture the README screenshots', async ({ page }) => {
  await signIn(page);

  const scanIds: string[] = [];
  for (const file of demoFiles) scanIds.push(await scan(page, file));
  const [courseScan] = scanIds;

  await page.goto(`/scan/${courseScan}`);
  await expect(page.getByRole('heading', { name: /^Rule-based findings/ })).toBeVisible();
  await shot(page, 'scan-results');

  await page.goto('/issues');
  await expect(page.getByText('Loading', { exact: false })).toHaveCount(0, { timeout: 30_000 });
  await shot(page, 'issue-review');

  await page.goto(`/remediate/${courseScan}`);
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByRole('button', { name: 'Download improved working file' })).toBeVisible({ timeout: 180_000 });

  // Review every change, then show the publication approval of the exact
  // saved file with its confirmation ticked, before it is submitted.
  await page.goto(`/review/${courseScan}`);
  await page.getByRole('button', { name: /^Approve All \(\d+\) pending fixes$/ }).click();
  await page.getByLabel(/I have reviewed this working file/).check();
  await expect(page.getByRole('button', { name: 'Approve this file for publication' })).toBeEnabled();
  await shot(page, 'remediation-review');

  await page.getByRole('button', { name: 'Approve this file for publication' }).click();
  await expect(page.getByText(/· Approved for publication/)).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download PDF review evidence' }).click();
  const evidence = await download;
  const evidencePath = await evidence.path();
  expect(evidence.suggestedFilename()).toMatch(/^accessibility-review-evidence.*\.pdf$/);
  await pdfShot(evidencePath, 'evidence-package');

  if (!canvas) {
    console.log('canvas-course not captured: JOURNEY_CANVAS_URL is not set');
    return;
  }
  await connectCanvas(page);
  await scanCourse(page);
  await shot(page, 'canvas-course');
});
