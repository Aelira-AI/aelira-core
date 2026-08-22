/* global process */
import { defineConfig, devices } from '@playwright/test';

const dashboardUrl = 'http://127.0.0.1:5173';
const canvasTestbedUrl = 'http://127.0.0.1:4174';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  grep: /@release/,
  fullyParallel: false,
  forbidOnly: true,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: dashboardUrl,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
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
