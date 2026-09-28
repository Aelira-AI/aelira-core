import { test, expect, type Page } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  await request.get('/fixture-api/reset');
});

async function assertDenied(page: Page, policy: boolean) {
  await expect(page.getByRole('heading', { name: policy ? 'LMS AI remediation is blocked by policy' : 'Remediation permission denied' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Back to Scan', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: policy ? 'Try again after policy review' : 'Try again after access review' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Start Remediation|Run Again|Check Status/ })).toHaveCount(0);
  await expect(page.getByText('Server progress', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/PRIVATE_DIAGNOSTIC/)).toHaveCount(0);
}

for (const scenario of ['policy403', 'alt403', 'permission403']) {
  test(`${scenario} gives bounded guidance without a retry loop`, async ({ page, request }) => {
    await page.goto(`/remediate/${scenario}`);
    await page.getByRole('button', { name: 'Start Remediation' }).click();
    await assertDenied(page, scenario !== 'permission403');
    const metrics = await (await request.get('/fixture-api/metrics')).json();
    expect(metrics.starts[scenario]).toBe(1);
    expect(metrics.polls[scenario]).toBeUndefined();
    if (scenario === 'permission403') await expect(page.getByText(/institution’s LMS AI policy/)).toHaveCount(0);
    await page.getByRole('button', { name: 'Back to Scan', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/scan/${scenario}$`));
  });
}

test('worker policy denial stops polling and survives reload', async ({ page, request }) => {
  await page.goto('/remediate/worker');
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByRole('heading', { name: 'Remediation in progress' })).toBeVisible();
  await assertDenied(page, true);
  const before = await (await request.get('/fixture-api/metrics')).json();
  expect(before.starts.worker).toBe(1);
  expect(before.polls.worker).toBe(2);
  await page.reload();
  await assertDenied(page, true);
  expect(await (await request.get('/fixture-api/metrics')).json()).toEqual(before);
});

test('persisted worker denial restores actionable guidance without starting', async ({ page, request }) => {
  await page.goto('/remediate/persisted');
  await assertDenied(page, true);
  expect(await (await request.get('/fixture-api/metrics')).json()).toEqual({ starts: {}, polls: {} });
});

test('policy guidance stays above its actions at the tablet breakpoint', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 900 });
  await page.goto('/remediate/persisted');
  await assertDenied(page, true);
  const guidance = await page.getByText('Your institution’s LMS AI policy', { exact: false }).boundingBox();
  const retry = await page.getByRole('button', { name: 'Try again after policy review' }).boundingBox();
  expect(guidance).not.toBeNull();
  expect(retry).not.toBeNull();
  expect(guidance!.width).toBeGreaterThan(400);
  expect(retry!.y).toBeGreaterThanOrEqual(guidance!.y + guidance!.height);
});

test('policy correction permits an explicit retry of a previously denied scan', async ({ page, request }) => {
  await page.goto('/remediate/persisted');
  await assertDenied(page, true);
  await request.get('/fixture-api/review-policy?scan=persisted');
  await page.getByRole('button', { name: 'Try again after policy review' }).click();
  await expect(page.getByRole('button', { name: 'Try again after policy review' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Run Again' })).toBeVisible();
  expect((await (await request.get('/fixture-api/metrics')).json()).starts.persisted).toBe(1);
});

test('an accepted normal request completes without duplicate submission', async ({ page, request }) => {
  await page.goto('/remediate/success');
  const submitted = page.waitForRequest(request => request.method() === 'POST' && request.url().includes('/education/remediate/'));
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  expect(new URL((await submitted).url()).searchParams.get('use_ai')).toBe('true');
  await expect(page.getByRole('button', { name: 'Run Again' })).toBeVisible();
  expect((await (await request.get('/fixture-api/metrics')).json()).starts.success).toBe(1);
});

test('an unknown server failure hides diagnostics and retains status reconciliation', async ({ page }) => {
  await page.goto('/remediate/unknown');
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByRole('heading', { name: 'Remediation could not be started' })).toBeVisible();
  await expect(page.getByText(/PRIVATE_DIAGNOSTIC/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Check Status' }).click();
  await expect(page.getByRole('button', { name: 'Start Remediation' })).toBeVisible();
});

test('a lost response reconciles the accepted job without resubmission', async ({ page, request }) => {
  await page.goto('/remediate/uncertain');
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByRole('button', { name: 'Start Remediation' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Check Status' }).click();
  await expect(page.getByRole('button', { name: 'Run Again' })).toBeVisible();
  const metrics = await (await request.get('/fixture-api/metrics')).json();
  expect(metrics.starts.uncertain).toBe(1);
  expect(metrics.polls.uncertain).toBe(2);
});
