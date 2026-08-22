/* global process */
import { expect, test } from '@playwright/test';

const TESTBED_URL = process.env.CANVAS_TESTBED_URL || 'http://127.0.0.1:4174';

test.describe('deterministic Canvas release journey', () => {
  test.beforeEach(async ({ request }) => {
    const reset = await request.post(`${TESTBED_URL}/testbed/reset`);
    expect(reset.ok()).toBeTruthy();
  });

  test('@release staff LTI launch scans, remediates, approves, writes back, and rescans', async ({ page, request }) => {
    await page.goto(`${TESTBED_URL}/testbed/lti/launch?role=staff`);

    await expect(page).toHaveURL(/\/lti\/course\/101/);
    await expect(page.getByRole('heading', { name: 'Accessible Design' })).toBeVisible();
    await expect(page.getByText('No content found')).toBeVisible();

    await page.getByRole('button', { name: 'Scan Content', exact: true }).first().click();
    const row = page.getByRole('row').filter({ hasText: 'Week 1 Data Chart' });
    await expect(row).toContainText('72%');
    await expect(row).toContainText('Scanned · needs remediation');

    await page.getByRole('button', { name: 'Remediate All' }).click();
    await expect(row).toContainText('Auto-remediated · pending review');

    await page.getByRole('button', { name: 'Approve All' }).click();
    await expect(row).toContainText('Approved');

    await page.getByRole('button', { name: 'Write Back All' }).click();
    await expect(row).toContainText('Written back');

    await page.getByRole('button', { name: 'Scan Content', exact: true }).first().click();
    await expect(row).toContainText('100%');

    const state = await request.get(`${TESTBED_URL}/testbed/state`);
    expect(state.ok()).toBeTruthy();
    expect(await state.json()).toMatchObject({
      users: [{ role: 'faculty' }],
      content: [{
        title: 'Week 1 Data Chart',
        writeback_status: 'written_back',
        compliance_score: 100,
        issue_count: 0,
        scan_count: 2,
        canvas_body: '<p>Results</p><img src="chart.png" alt="Bar chart of assessment results">',
      }],
      audit: [{ action: 'written_back', approved_by: 'staff-1' }],
    });
  });

  test('@release learner launch is denied without provisioning', async ({ page, request }) => {
    const response = await page.goto(`${TESTBED_URL}/testbed/lti/launch?role=learner`);

    expect(response?.status()).toBe(403);
    await expect(page.getByRole('heading', { name: 'Staff access required' })).toBeVisible();

    const state = await request.get(`${TESTBED_URL}/testbed/state`);
    expect(await state.json()).toMatchObject({ users: [] });
  });
});
