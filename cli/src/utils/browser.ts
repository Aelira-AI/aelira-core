import { existsSync } from 'node:fs'

export const CHROMIUM_EXECUTABLE_ENV = 'AELIRA_CHROMIUM_EXECUTABLE'

/**
 * Launch options for every Playwright Chromium the CLI starts.
 *
 * Playwright's bundled Chromium can trail Chrome stable by weeks. Setting
 * AELIRA_CHROMIUM_EXECUTABLE runs a browser you have patched yourself, such as
 * the pinned Chrome for Testing build the Aelira API image ships. A path that
 * does not exist is an error, so the setting never silently falls back to the
 * browser it was meant to replace.
 */
export function chromiumLaunchOptions(
  env: NodeJS.ProcessEnv = process.env,
): { executablePath?: string; headless: true } {
  const executablePath = env[CHROMIUM_EXECUTABLE_ENV]?.trim()
  if (!executablePath) return { headless: true }
  if (!existsSync(executablePath)) {
    throw new Error(`${CHROMIUM_EXECUTABLE_ENV} does not point to a file: ${executablePath}`)
  }

  return { executablePath, headless: true }
}
