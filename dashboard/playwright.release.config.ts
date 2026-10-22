/* global process */
import { defineConfig, devices } from '@playwright/test';

// CI runs the hash-pinned Chrome for Testing build named in
// scripts/chrome-for-testing.json; elsewhere Playwright's own Chromium is used.
const chromiumLaunchOptions = {
  executablePath: process.env.AELIRA_CHROMIUM_EXECUTABLE || undefined,
};

const dashboardUrl = 'http://127.0.0.1:5173';
const canvasTestbedUrl = 'http://127.0.0.1:4174';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  grep: /@release/,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: dashboardUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'release-chromium',
      use: { ...devices['Desktop Chrome'], launchOptions: chromiumLaunchOptions },
    },
  ],
  webServer: [
    {
      command: 'node --disable-warning=ExperimentalWarning tests/e2e/canvas-testbed.mjs',
      url: `${canvasTestbedUrl}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1',
      url: dashboardUrl,
      env: {
        VITE_API_URL: canvasTestbedUrl,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
