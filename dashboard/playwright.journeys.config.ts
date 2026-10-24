/* global process */
import { defineConfig, devices } from '@playwright/test';
import { canvasHttpCredentials } from './tests/journeys/support/canvas-gate';

// CI runs the hash-pinned Chrome for Testing build named in
// scripts/chrome-for-testing.json; elsewhere Playwright's own Chromium is used.
const chromiumLaunchOptions = {
  executablePath: process.env.AELIRA_CHROMIUM_EXECUTABLE || undefined,
};

/**
 * Browser user journeys against the real stack: API, durable worker,
 * PostgreSQL, Redis and the built dashboard. Nothing is mocked or routed.
 * The stack is started by the caller (CI job or tests/journeys/stack.compose.yml).
 */
const dashboardPort = process.env.JOURNEY_DASHBOARD_PORT || '15373';
const apiUrl = process.env.JOURNEY_API_URL || 'http://localhost:18300';

export default defineConfig({
  testDir: './tests/journeys',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 20_000 },
  reporter: [['list'], ['json', { outputFile: 'journey-results/results.json' }]],
  outputDir: 'journey-results/artifacts',
  use: {
    baseURL: `http://localhost:${dashboardPort}`,
    acceptDownloads: true,
    // Traces and screenshots are only kept for failures.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
      use: { ...devices['Desktop Chrome'], launchOptions: chromiumLaunchOptions },
    },
    {
      name: 'journeys',
      testMatch: /\.spec\.ts$/,
      testIgnore: /lms\.spec\.ts$/,
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: chromiumLaunchOptions,
        storageState: 'journey-results/.auth/admin.json',
      },
    },
    {
      // Needs a Canvas instance and credentials, so it is selected on its own.
      name: 'lms',
      testMatch: /lms\.spec\.ts$/,
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: chromiumLaunchOptions,
        storageState: 'journey-results/.auth/admin.json',
        httpCredentials: canvasHttpCredentials(),
      },
    },
  ],
  webServer: {
    command: `npm run build && npx vite preview --host localhost --port ${dashboardPort} --strictPort`,
    url: `http://localhost:${dashboardPort}/login`,
    env: { VITE_API_URL: apiUrl },
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
