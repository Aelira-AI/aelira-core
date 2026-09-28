import { defineConfig } from '@playwright/test';
import process from 'node:process';

export default defineConfig({
  testDir: './tests',
  testMatch: 'remediation-policy.spec.ts',
  workers: 1,
  forbidOnly: true,
  use: { baseURL: 'http://127.0.0.1:4326' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: './node_modules/.bin/vite --config tests/fixtures/remediation-policy-preview.config.ts',
    url: 'http://127.0.0.1:4326',
    reuseExistingServer: !process.env.CI,
  },
});
