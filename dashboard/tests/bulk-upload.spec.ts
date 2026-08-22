import { expect, test, type Page, type Route } from '@playwright/test';
import { Buffer } from 'node:buffer';

const department = { id: 'dept-1', name: 'Accessibility', tier: 'department' };
const user = {
  id: 'user-1',
  email: 'staff@example.com',
  name: 'Staff User',
  role: 'admin',
  department_id: 'dept-1',
  is_active: true,
};

async function installAuth(page: Page): Promise<void> {
  await page.route('**/auth/session/validate', (route) => route.fulfill({
    json: { valid: true, auth_method: 'session', user, department, expires_at: 1999999999 },
  }));
}

function uploadFiles(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    name: `document-${index}.pdf`,
    mimeType: 'application/pdf',
    buffer: Buffer.from(`pdf-${index}`),
  }));
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function fulfillScan(route: Route, index: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 15));
  await route.fulfill({
    json: {
      scan_id: `scan-${index}`,
      status: 'COMPLETED',
      compliance_score: 100,
      issues: [],
    },
  });
}

test.describe('standalone bounded bulk upload pool', () => {
  test.beforeEach(async ({ page }) => {
    await installAuth(page);
  });

  test('@release drains more files than concurrency without exceeding the selected bound', async ({ page }) => {
    let requests = 0;
    let active = 0;
    let maximum = 0;
    await page.route('**/education/pdf/scan*', async (route) => {
      const index = requests;
      requests += 1;
      active += 1;
      maximum = Math.max(maximum, active);
      await fulfillScan(route, index);
      active -= 1;
    });

    await page.goto('/bulk-upload');
    await expect(page.getByRole('heading', { name: 'Bulk Upload' })).toBeVisible();
    await page.locator('input[type="file"]').setInputFiles(uploadFiles(10));
    await page.getByRole('combobox').selectOption('3');
    await page.getByRole('button', { name: 'Start Processing (10 files)' }).click();

    await expect.poll(() => requests).toBe(10);
    await expect(page.getByText('Complete', { exact: true }).locator('..').getByText('10')).toBeVisible();
    expect(maximum).toBe(3);
  });

  test('synchronous repeated starts cannot create competing pools', async ({ page }) => {
    let requests = 0;
    let active = 0;
    let maximum = 0;
    await page.route('**/education/pdf/scan*', async (route) => {
      const index = requests;
      requests += 1;
      active += 1;
      maximum = Math.max(maximum, active);
      await fulfillScan(route, index);
      active -= 1;
    });

    await page.goto('/bulk-upload');
    await page.locator('input[type="file"]').setInputFiles(uploadFiles(4));
    await page.getByRole('combobox').selectOption('3');
    await page.getByRole('button', { name: 'Start Processing (4 files)' }).evaluate((button) => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });

    await expect.poll(() => requests).toBe(4);
    await page.waitForTimeout(100);
    expect(requests).toBe(4);
    expect(maximum).toBe(3);
  });

  test('pause blocks new uploads until resume', async ({ page }) => {
    const firstUpload = deferred();
    let requests = 0;
    await page.route('**/education/pdf/scan*', async (route) => {
      const index = requests;
      requests += 1;
      if (index === 0) await firstUpload.promise;
      await route.fulfill({
        json: { scan_id: `scan-${index}`, status: 'COMPLETED', compliance_score: 100, issues: [] },
      });
    });

    await page.goto('/bulk-upload');
    await page.locator('input[type="file"]').setInputFiles(uploadFiles(4));
    await page.getByRole('combobox').selectOption('1');
    await page.getByRole('button', { name: 'Start Processing (4 files)' }).click();
    await expect.poll(() => requests).toBe(1);

    await page.getByRole('button', { name: 'Pause' }).click();
    firstUpload.resolve();
    await page.waitForTimeout(100);
    expect(requests).toBe(1);

    await page.getByRole('button', { name: 'Resume' }).click();
    await expect.poll(() => requests).toBe(4);
    await expect(page.getByText('Complete', { exact: true }).locator('..').getByText('4')).toBeVisible();
  });

  test('stop starts no new uploads and reports untouched files as pending', async ({ page }) => {
    const activeUploads = deferred();
    let requests = 0;
    await page.route('**/education/pdf/scan*', async (route) => {
      const index = requests;
      requests += 1;
      await activeUploads.promise;
      await route.fulfill({
        json: { scan_id: `scan-${index}`, status: 'COMPLETED', compliance_score: 100, issues: [] },
      });
    });

    await page.goto('/bulk-upload');
    await page.locator('input[type="file"]').setInputFiles(uploadFiles(5));
    await page.getByRole('combobox').selectOption('3');
    await page.getByRole('button', { name: 'Start Processing (5 files)' }).click();
    await expect.poll(() => requests).toBe(3);

    await page.getByRole('button', { name: 'Stop All' }).click();
    await expect(page.getByRole('button', { name: 'Stopping active files' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Pause' })).toHaveCount(0);
    activeUploads.resolve();
    await expect(page.getByText('Batch Stopped')).toBeVisible();
    expect(requests).toBe(3);
    await expect(page.getByText('Pending', { exact: true }).locator('..').getByText('2')).toBeVisible();
    await expect(page.getByText('Complete', { exact: true }).locator('..').getByText('3')).toBeVisible();
  });

  test('Retry Failed reruns only rejected uploads and settles accurate counts', async ({ page }) => {
    let requests = 0;
    await page.route('**/education/pdf/scan*', async (route) => {
      const index = requests;
      requests += 1;
      if (index === 1 || index === 3) {
        await route.fulfill({ status: 500, json: { detail: 'scan failed' } });
        return;
      }
      await route.fulfill({
        json: { scan_id: `scan-${index}`, status: 'COMPLETED', compliance_score: 100, issues: [] },
      });
    });

    await page.goto('/bulk-upload');
    await page.locator('input[type="file"]').setInputFiles(uploadFiles(5));
    await page.getByRole('button', { name: 'Start Processing (5 files)' }).click();
    await expect(page.getByRole('button', { name: 'Retry Failed (2)' })).toBeVisible();
    expect(requests).toBe(5);

    await page.getByRole('button', { name: 'Retry Failed (2)' }).click();
    await expect.poll(() => requests).toBe(7);
    await expect(page.getByText('Complete', { exact: true }).locator('..').getByText('5')).toBeVisible();
    await expect(page.getByText('Failed', { exact: true }).locator('..').getByText('0')).toBeVisible();
  });
});
