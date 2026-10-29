/* global process */
import { defineConfig, devices } from '@playwright/test';
import { canvasHttpCredentials } from './tests/journeys/support/canvas-gate';

/**
 * Captures the README screenshots from a running deployment of a released
 * version, signed in through its real magic-link flow. Nothing is mocked or
 * routed, so every screen shows what that release renders for the demo files.
 * See docs/assets/readme/screenshots/README.md for how to start the stack.
 */
export default defineConfig({
  testDir: './tests/readme',
  testMatch: /\.capture\.ts$/,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 600_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  outputDir: 'readme-results/artifacts',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.README_SHOTS_URL || 'http://localhost:15373',
    // Only sent to the Canvas origin, and only when its gate asks.
    httpCredentials: canvasHttpCredentials(),
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
    reducedMotion: 'reduce',
    acceptDownloads: true,
    trace: 'retain-on-failure',
  },
});
