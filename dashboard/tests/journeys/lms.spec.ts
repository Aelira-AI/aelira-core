/* global process */
import { test, expect, type Browser, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet, scanIdFromUrl, type RemediationJob } from './support/scans';
import { apiUrl, dismissCookieBanner, sha256 } from './support/stack';

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
      issuer: 'https://canvas.instructure.com',
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
  teacherSession = await browser.newContext();
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
let snapshot: { name: string; size: number; updated_at: string }[] = [];
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
  snapshot = describe(files);
});

test('the teacher launches the tool from the course and lands in the course scope', async ({ page }, testInfo) => {
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
  await snap(tool, testInfo, 'lti-launched');
  await expect(tool.getByText(/CI Journeys/).first()).toBeVisible({ timeout: 90_000 });
  await expect(tool.getByText(pdfName).first()).toBeVisible();
  await expect(tool.getByText(docxName).first()).toBeVisible();
  await assertNoBlockingViolations(tool, 'lti-course-view', testInfo);
  await tool.close();
});

async function scanRemediateApproveWriteBack(page: Page, testInfo: TestInfo, fileName: string, context: BrowserContext) {
  await page.goto('/integrations/canvas');
  await dismissCookieBanner(page);
  await page.getByRole('button', { name: /CI Journeys/ }).click();
  const row = page.getByRole('row').filter({ hasText: fileName });
  await expect(row).toBeVisible({ timeout: 60_000 });
  await assertNoBlockingViolations(page, `canvas-courses-${fileName}`, testInfo);
  await row.getByRole('button', { name: 'Scan', exact: true }).click();
  await expect(row.getByRole('button', { name: 'View Results' })).toBeVisible({ timeout: 180_000 });
  await row.getByRole('button', { name: 'View Results' }).click();
  await expect(page.getByRole('heading', { name: fileName })).toBeVisible();
  const scanId = scanIdFromUrl(page.url());
  await assertNoBlockingViolations(page, `canvas-scan-${fileName}`, testInfo);

  await page.goto(`/remediate/${scanId}`);
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  const download = page.getByRole('button', { name: 'Download improved working file' });
  await expect(download).toBeVisible({ timeout: 240_000 });
  await assertNoBlockingViolations(page, `canvas-remediated-${fileName}`, testInfo);
  const job = await apiGet<RemediationJob>(page.request, `/education/scans/${scanId}/remediation/latest`);
  expect(job.status).toBe('completed');
  expect(job.download_available).toBe(true);

  await page.getByRole('link', { name: 'Review changes' }).click();
  await page.getByRole('button', { name: /^Approve all \d+ pending fixes/ }).click();
  await page.getByLabel(/I have reviewed this working file/).check();
  await page.getByRole('button', { name: 'Approve this file for publication' }).click();
  await expect(page.getByText(/· Approved for publication/)).toBeVisible();
  await assertNoBlockingViolations(page, `canvas-review-approved-${fileName}`, testInfo);
  await page.getByRole('button', { name: 'Write back approved file' }).click();
  await expect(page.getByText(/Write-back is queued/)).toBeVisible();

  // Canvas now holds a remediated copy beside the original, and its bytes are the approved artifact.
  const written = accessibleCopy(fileName);
  let copy: CanvasFile | undefined;
  await expect
    .poll(async () => (copy = (await courseFiles(context)).find((f) => f.display_name === written)), {
      message: `${written} never appeared in the course`,
      timeout: 180_000,
    })
    .toBeDefined();
  const bytes = await (await context.request.get((copy as CanvasFile).url)).body();
  expect(sha256(bytes), 'Canvas holds the approved bytes').toBe(job.score_measurement?.output_sha256);
  await snap(page, testInfo, `written-${fileName}`);
}

for (const fileName of [pdfName, docxName]) {
  test(`${fileName}: scan, remediate, approve and write back to Canvas`, async ({ page, browser }, testInfo) => {
    await scanRemediateApproveWriteBack(page, testInfo, fileName, await canvasSession(browser));
  });
}

test('the course copy is back to its starting state', async ({ browser }) => {
  const context = await canvasSession(browser);
  for (const file of await courseFiles(context)) {
    if (/_accessible\.[^.]+$/.test(file.display_name)) await deleteCanvasFile(context, file.id);
  }
  expect(describe(await courseFiles(context))).toEqual(snapshot);
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
