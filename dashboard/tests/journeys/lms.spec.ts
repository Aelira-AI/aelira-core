/* global process */
import { test, expect, type Browser, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet } from './support/scans';
import { apiUrl, dismissCookieBanner, sha256 } from './support/stack';
import { canvasHttpCredentials } from './support/canvas-gate';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

/**
 * Canvas LMS journey against the Canvas test instance, using a course copy,
 * a Teacher who is enrolled only in that copy, and two developer keys that exist
 * only for this suite. Canvas cannot reach the runner, so the tool is registered
 * with a static public key and localhost redirect URIs; only this browser and the
 * tool's own outbound calls cross the boundary.
 *
 * Canvas pages are not ours, so axe runs on the dashboard pages only.
 */
const canvas = process.env.JOURNEY_CANVAS_URL ?? '';
const courseId = process.env.JOURNEY_CANVAS_COURSE_ID ?? '';
const ltiClientId = process.env.JOURNEY_LTI_CLIENT_ID ?? '';
const ltiDeploymentId = process.env.JOURNEY_LTI_DEPLOYMENT_ID ?? '';
const teacher = process.env.JOURNEY_CANVAS_TEACHER_LOGIN ?? '';
const teacherPassword = process.env.JOURNEY_CANVAS_TEACHER_PASSWORD ?? '';
const required = process.env.JOURNEY_LMS_REQUIRED === 'true';
const haveCredentials = [canvas, courseId, ltiClientId, ltiDeploymentId, teacher, teacherPassword].every(Boolean);

// The files this copy of the course holds, by type.
const pdfName = 'ENG101-Syllabus-Fall2025.pdf';
const docxName = 'ENG101-Essay2-Rhetorical-Analysis.docx';

// The steps share state and run in file order; a failure must not hide the steps after it.

test.beforeAll(() => {
  if (!haveCredentials && required) {
    throw new Error('The Canvas journey is required here but its credentials are missing');
  }
});
test.beforeEach(() => {
  test.skip(!haveCredentials, 'not run: Canvas credentials unavailable (fork pull requests receive no secrets)');
});

async function snap(page: Page, testInfo: TestInfo, label: string) {
  await testInfo.attach(label, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
}

function csrfHeader(cookies: { name: string; value: string }[]) {
  const token = cookies.find((c) => c.name === 'csrf_token')?.value;
  expect(token, 'the session carries a CSRF token').toBeTruthy();
  return { 'X-CSRF-Token': decodeURIComponent(token as string) };
}

async function signInToCanvas(page: Page) {
  await page.getByLabel(/email|login/i).first().fill(teacher);
  await page.getByLabel(/password/i).first().fill(teacherPassword);
  await page.getByRole('button', { name: /log in/i }).click();
}

test('Canvas is reachable and the tool publishes the key Canvas was given', async ({ page }) => {
  const jwks = await page.request.get(`${canvas}/api/lti/security/jwks`);
  expect(jwks.ok()).toBe(true);
  const tool = await apiGet<{ keys: { kty: string; kid: string }[] }>(page.request, '/lti/jwks');
  expect(tool.keys, 'the tool publishes exactly one signing key').toHaveLength(1);
  expect(tool.keys[0].kty).toBe('RSA');
});

test('the administrator registers the Canvas tool as a self-hoster does', async ({ page }) => {
  await page.goto('/dashboard');
  const cookies = await page.context().cookies(apiUrl);
  const response = await page.request.post(new URL('/integrations/lti/registrations', apiUrl).toString(), {
    headers: { 'Content-Type': 'application/json', ...csrfHeader(cookies) },
    data: {
      platform: 'canvas',
      platform_name: 'Canvas journey course',
      // A self-hosted Canvas names itself as the issuer, and the tool derives its
      // authorization and token endpoints from the same address.
      issuer: canvas,
      client_id: ltiClientId,
      deployment_id: ltiDeploymentId,
      auth_login_url: `${canvas}/api/lti/authorize_redirect`,
      auth_token_url: `${canvas}/login/oauth2/token`,
      jwks_url: `${canvas}/api/lti/security/jwks`,
    },
  });
  expect(response.status(), await response.text()).toBe(200);
  const listed = await apiGet<{ registrations: { client_id: string; is_active: boolean }[] }>(
    page.request,
    '/integrations/lti/registrations',
  );
  expect(listed.registrations.filter((r) => r.client_id === ltiClientId && r.is_active)).toHaveLength(1);
});

test('the administrator connects Canvas through the dashboard and the teacher consents', async ({ page }, testInfo) => {
  await page.goto('/integrations');
  await dismissCookieBanner(page);
  await expect(page.getByRole('button', { name: 'Connect to Canvas LMS' })).toBeVisible();
  await assertNoBlockingViolations(page, 'integrations-before-connect', testInfo);
  await page.getByRole('button', { name: 'Connect to Canvas LMS' }).click();
  await page.getByRole('textbox', { name: 'Canvas instance URL' }).fill(canvas);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  // Canvas takes over: sign in as the teacher, then accept the API key's request.
  await page.waitForURL((url) => url.origin === new URL(canvas).origin, { timeout: 60_000 });
  if (/\/login/.test(new URL(page.url()).pathname)) await signInToCanvas(page);
  const accept = page.getByRole('button', { name: /accept|authorize/i });
  await Promise.race([
    accept.waitFor({ timeout: 30_000 }).then(() => accept.click()),
    page.waitForURL((url) => url.pathname === '/integrations', { timeout: 30_000 }),
  ]).catch(async (error) => {
    await snap(page, testInfo, 'canvas-consent');
    throw error;
  });
  await page.waitForURL((url) => url.origin === new URL(page.url()).origin && url.pathname === '/integrations');
  await expect(page).toHaveURL(/canvas=connected/);
  await expect(page.getByRole('button', { name: 'Disconnect from Canvas LMS' })).toBeVisible();
  await assertNoBlockingViolations(page, 'integrations-connected', testInfo);
  const status = await apiGet<{ connected: boolean; canvas_instance_url: string }>(page.request, '/canvas/status');
  expect(status.connected).toBe(true);
  expect(status.canvas_instance_url).toBe(canvas);
  // The teacher's token reaches the one course the teacher is enrolled in, and no other.
  const courses = await apiGet<{ id: number | string }[]>(page.request, '/canvas/courses');
  expect(courses.map((c) => String(c.id))).toEqual([courseId]);
});


interface CanvasFile {
  id: number;
  display_name: string;
  size: number;
  updated_at: string;
  url: string;
}

/** One Canvas login shared by the steps that read or tidy the course. */
let teacherSession: BrowserContext | undefined;
async function canvasSession(browser: Browser): Promise<BrowserContext> {
  if (teacherSession) return teacherSession;
  // A context made here does not inherit the project's options, so pass the gate on.
  teacherSession = await browser.newContext({ httpCredentials: canvasHttpCredentials() });
  const page = await teacherSession.newPage();
  await page.goto(`${canvas}/login/canvas`);
  await signInToCanvas(page);
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
  await page.close();
  return teacherSession;
}

async function canvasJson<T>(context: BrowserContext, path: string): Promise<T> {
  const response = await context.request.get(`${canvas}${path}`, { headers: { Accept: 'application/json' } });
  expect(response.ok(), `Canvas ${path} answered ${response.status()}`).toBe(true);
  return JSON.parse((await response.text()).replace(/^while\(1\);/, '')) as T;
}

async function courseFiles(context: BrowserContext): Promise<CanvasFile[]> {
  return canvasJson<CanvasFile[]>(context, `/api/v1/courses/${courseId}/files?per_page=100`);
}

async function deleteCanvasFile(context: BrowserContext, id: number) {
  const cookies = await context.cookies(canvas);
  const csrf = decodeURIComponent(cookies.find((c) => c.name === '_csrf_token')?.value ?? '');
  const response = await context.request.delete(`${canvas}/api/v1/files/${id}`, {
    headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' },
  });
  expect(response.ok(), `deleting Canvas file ${id} answered ${response.status()}`).toBe(true);
}

const accessibleCopy = (name: string) => name.replace(/(\.[^.]+)$/, '_accessible$1');
// Kept on disk: Playwright starts a fresh worker after a failed step.
const snapshotPath = 'journey-results/canvas-course-snapshot.json';
const describe = (files: CanvasFile[]) =>
  files
    .map((f) => ({ name: f.display_name, size: f.size, updated_at: f.updated_at }))
    .sort((a, b) => a.name.localeCompare(b.name));

test('the course copy is in a known state before anything is written', async ({ browser }) => {
  const context = await canvasSession(browser);
  // A run that was interrupted may have left remediated copies behind; remove them first.
  for (const file of await courseFiles(context)) {
    if (/_accessible\.[^.]+$/.test(file.display_name)) await deleteCanvasFile(context, file.id);
  }
  const files = await courseFiles(context);
  expect(files.map((f) => f.display_name)).toEqual(expect.arrayContaining([pdfName, docxName]));
  await mkdir('journey-results', { recursive: true });
  await writeFile(snapshotPath, JSON.stringify(describe(files)));
});

test('the teacher launches from the course, then scans, remediates, approves and writes back a PDF and a DOCX', async ({ page }, testInfo) => {
  test.setTimeout(900_000);
  const context = await canvasSession(page.context().browser() as Browser);

  // Launch from the course the way a teacher does.
  await page.goto(`${canvas}/login/canvas`);
  if (/\/login/.test(new URL(page.url()).pathname)) await signInToCanvas(page);
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
  await page.goto(`${canvas}/courses/${courseId}`);
  await page.getByRole('link', { name: 'Accessibility Scanner (CI)' }).click();
  // Canvas will not frame a plain-HTTP tool and offers a new window instead, which is
  // also the first-party context a locally run tool needs.
  const popup = page.waitForEvent('popup');
  await page.getByRole('button', { name: /Load Accessibility Scanner \(CI\) in a new window/ }).click();
  const tool = await popup;
  await tool.waitForURL((url) => url.origin === 'http://localhost:15373', { timeout: 90_000 });
  await dismissCookieBanner(tool);

  await test.step('the launch lands in the launching course, and only there', async () => {
    await expect(tool.getByRole('heading', { name: /CI Journeys/ })).toBeVisible({ timeout: 90_000 });
    expect(new URL(tool.url()).pathname).toBe(`/canvas/courses/${courseId}/content`);
    await expect(tool.getByRole('row').filter({ hasText: pdfName })).toBeVisible();
    await expect(tool.getByRole('row').filter({ hasText: docxName })).toBeVisible();
    await assertNoBlockingViolations(tool, 'lti-course-content', testInfo);
  });

  await test.step('scan the course content', async () => {
    const queued = tool.waitForResponse((r) => r.url().endsWith('/canvas/content/scan') && r.request().method() === 'POST');
    await tool.getByRole('button', { name: 'Scan Content' }).click();
    const response = await queued;
    expect(response.status()).toBe(200);
    console.log(`Scan request answered: ${JSON.stringify(await response.json())}`);
    // The scan is a queued job. The page polls for a while; refresh it ourselves after that.
    for (const name of [pdfName, docxName]) {
      await expect
        .poll(
          async () => {
            const refresh = tool.getByRole('button', { name: 'Refresh' });
            if (await refresh.isEnabled()) await refresh.click();
            return (await tool.getByRole('row').filter({ hasText: name }).innerText()).includes('Unscanned');
          },
          { message: `${name} was never scanned`, timeout: 300_000, intervals: [5_000] },
        )
        .toBe(false);
    }
    await expect(tool.getByRole('button', { name: 'Scan Content' })).toBeVisible({ timeout: 120_000 });
    await snap(tool, testInfo, 'content-scanned');
    await assertNoBlockingViolations(tool, 'lti-course-content-scanned', testInfo);
  });

  const approved: Record<string, { sha256: string }> = {};
  for (const name of [pdfName, docxName]) {
    await test.step(`remediate and approve ${name}`, async () => {
      await tool.goto(`/canvas/courses/${courseId}/content`);
      const row = tool.getByRole('row').filter({ hasText: name });
      await expect(row).toBeVisible();
      await row.getByRole('button', { name: 'Remediate' }).click();
      await expect(row.getByRole('button', { name: 'Remediate' })).toHaveCount(0, { timeout: 240_000 });
      await snap(tool, testInfo, `remediated-${name}`);
      await row.getByRole('button', { name: 'Review' }).click();
      const panel = tool.getByRole('region', { name: 'Managed remediation artifact' });
      await expect(panel).toBeVisible({ timeout: 60_000 });
      await assertNoBlockingViolations(tool, `lti-review-${name}`, testInfo);
      await tool.getByRole('button', { name: 'Review document fixes and working file' }).click();
      await expect(tool.getByRole('heading', { name })).toBeVisible();
      const reviewFixes = tool.getByRole('group', { name: 'Review fixes' });
      await expect(reviewFixes).toBeVisible();
      expect((await reviewFixes.innerText()).trim(), 'the teacher can inspect the recorded fixes').not.toBe('');
      const approvePending = tool.getByRole('button', { name: /Approve All \(\d+\) pending fixes/ });
      if (await approvePending.count()) {
        await approvePending.click();
        await expect(approvePending).toHaveCount(0, { timeout: 60_000 });
      }
      const workingFile = tool.getByRole('region', { name: 'Working file and publication approval' });
      const confirmation = workingFile.getByRole('checkbox', {
        name: 'I have reviewed this working file, its changes and the remaining findings.',
      });
      await expect(confirmation).toBeEnabled({ timeout: 60_000 });
      const shown = (await workingFile.innerText()).match(/SHA-256 ([a-f0-9]{12})/)?.[1];
      expect(shown, 'the review shows the artifact digest').toBeTruthy();
      const pending = tool.waitForEvent('download');
      await workingFile.getByRole('button', { name: 'Download improved working file' }).click();
      const bytes = await readFile(await (await pending).path());
      expect(sha256(bytes).startsWith(shown as string), 'the downloaded bytes are the artifact the review shows').toBe(true);
      approved[name] = { sha256: sha256(bytes) };
      await confirmation.check();
      await workingFile.getByRole('button', { name: 'Approve this file for publication' }).click();
      await expect(workingFile.getByText('Approved for publication')).toBeVisible({ timeout: 60_000 });
    });
  }

  await test.step('write the approved files back and check what Canvas now holds', async () => {
    await tool.goto(`/canvas/courses/${courseId}/content`);
    await tool.getByRole('button', { name: 'Write Back All Approved' }).click();
    await expect(tool.getByText('Write Back Complete').first()).toBeVisible({ timeout: 240_000 });
    for (const name of [pdfName, docxName]) {
      let copy: CanvasFile | undefined;
      await expect
        .poll(async () => (copy = (await courseFiles(context)).find((f) => f.display_name === accessibleCopy(name))), {
          message: `${accessibleCopy(name)} never appeared in the course`,
          timeout: 240_000,
        })
        .toBeDefined();
      const held = await (await context.request.get((copy as CanvasFile).url)).body();
      expect(sha256(held), `Canvas holds the approved bytes of ${name}`).toBe(approved[name].sha256);
    }
    await snap(tool, testInfo, 'written-back');
  });
});

test('the course copy is back to its starting state', async ({ browser }) => {
  const context = await canvasSession(browser);
  for (const file of await courseFiles(context)) {
    if (/_accessible\.[^.]+$/.test(file.display_name)) await deleteCanvasFile(context, file.id);
  }
  expect(describe(await courseFiles(context))).toEqual(JSON.parse(await readFile(snapshotPath, 'utf8')));
});

test.afterAll(async () => {
  // Leave nothing behind even when a step failed part-way.
  if (!teacherSession) return;
  try {
    for (const file of await courseFiles(teacherSession)) {
      if (/_accessible\.[^.]+$/.test(file.display_name)) await deleteCanvasFile(teacherSession, file.id);
    }
  } finally {
    await teacherSession.close();
  }
});
