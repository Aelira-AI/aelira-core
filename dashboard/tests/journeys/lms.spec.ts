/* global process */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet, scanIdFromUrl } from './support/scans';
import { apiUrl, dismissCookieBanner } from './support/stack';

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

test.describe.configure({ mode: 'serial' });

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
});

test('the teacher launches the tool from the course and lands in the course scope', async ({ page }, testInfo) => {
  await page.goto(`${canvas}/courses/${courseId}`);
  if (/\/login/.test(new URL(page.url()).pathname)) await signInToCanvas(page);
  await page.goto(`${canvas}/courses/${courseId}`);
  await page.getByRole('link', { name: 'Accessibility Scanner (CI)' }).click();
  const tool = page.frameLocator('iframe#tool_content');
  await expect(tool.getByText(/CI Journeys/).first()).toBeVisible({ timeout: 90_000 });
  await snap(page, testInfo, 'lti-launched');
  await expect(tool.getByText(pdfName).first()).toBeVisible();
  await expect(tool.getByText(docxName).first()).toBeVisible();
  expect(scanIdFromUrl).toBeDefined();
});
