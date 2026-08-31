import { test, expect } from '@playwright/test';

test('profile and email preferences persist after save and reload', async ({ page }) => {
  let profile = {
    email: 'teacher@example.edu', name: 'Original Name', timezone: 'UTC',
    email_verified: true, auth_provider: 'api_key', created_at: '2026-01-01T00:00:00Z',
  };
  let preferences = {
    email_scan_complete: true,
    email_remediation_complete: true,
    email_critical_alerts: true,
    email_weekly_summary: true,
    weekly_summary_day: 0,
    weekly_summary_hour: 9,
  };

  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/auth/session/validate') {
      return route.fulfill({ status: 401, json: { detail: 'no session' } });
    }
    if (path === '/auth/validate') {
      return route.fulfill({ json: {
        auth_method: 'api_key',
        department: { id: 'dept-1', name: 'Accessibility', institution: 'Example', tier: 'department' },
        user: { id: 'user-1', email: profile.email, name: profile.name, role: 'admin', department_id: 'dept-1' },
      } });
    }
    if (path === '/auth/profile') {
      if (request.method() === 'PATCH') profile = { ...profile, ...request.postDataJSON() };
      return route.fulfill({ json: profile });
    }
    if (path === '/auth/profile/email-preferences') {
      if (request.method() === 'PATCH') preferences = { ...preferences, ...request.postDataJSON() };
      return route.fulfill({ json: preferences });
    }
    if (path === '/auth/sessions') return route.fulfill({ json: { sessions: [] } });
    if (path === '/auth/keys') return route.fulfill({ json: [] });
    if (path === '/account/deletion-status') return route.fulfill({ status: 404, json: {} });
    if (path === '/llm/providers') return route.fulfill({ json: {
      primary: null,
      fallback: null,
      providers: Object.fromEntries(['ollama', 'gemini', 'openai', 'anthropic', 'xai'].map(name => [name, {
        name,
        display_name: name === 'xai' ? 'xAI' : name[0].toUpperCase() + name.slice(1),
        is_available: false,
        is_local: name === 'ollama',
        status: 'not_configured',
        text_model: null,
        code_model: null,
        vision_model: null,
      }])),
    } });
    return route.fulfill({ json: {} });
  });

  await page.addInitScript(() => localStorage.setItem('apiKey', 'characterization-key'));
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Your Profile' })).toBeVisible();

  await page.getByRole('button', { name: 'Edit' }).click();
  await page.locator('label:text-is("Name")').locator('..').getByRole('textbox').fill('Persisted Name');
  await page.locator('label:text-is("Timezone")').locator('..').getByRole('combobox').selectOption('Asia/Tokyo');
  await page.getByRole('button', { name: 'Save Changes' }).first().click();

  for (const label of ['Scan Complete', 'Remediation Complete', 'Critical Issue Alerts']) {
    await page.getByText(label, { exact: true }).locator('xpath=ancestor::div[contains(@class,"p-4")][1]').getByRole('switch').click();
  }
  const weeklyCard = page.getByText('Weekly Summary', { exact: true }).locator('xpath=ancestor::div[contains(@class,"p-4")][1]');
  await page.getByRole('combobox').nth(0).selectOption('3');
  await page.getByRole('combobox').nth(1).selectOption('14');
  await weeklyCard.getByRole('switch').click();
  await page.getByRole('button', { name: 'Save Changes' }).last().click();

  await page.reload();
  await expect(page.getByText('Persisted Name', { exact: true })).toBeVisible();
  await expect(page.getByText('Asia/Tokyo', { exact: true })).toBeVisible();
  const cards = ['Scan Complete', 'Remediation Complete', 'Critical Issue Alerts'];
  for (const label of cards) {
    await expect(page.getByText(label, { exact: true }).locator('xpath=ancestor::div[contains(@class,"p-4")][1]').getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  }
  await expect(weeklyCard.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await weeklyCard.getByRole('switch').click();
  await expect(page.getByRole('combobox').nth(0)).toHaveValue('3');
  await expect(page.getByRole('combobox').nth(1)).toHaveValue('14');
});
