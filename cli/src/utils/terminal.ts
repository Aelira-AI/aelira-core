// Shared terminal-environment checks so commands agree on when to keep
// output plain: CI runners, machine-readable stdout, and user opt-outs.

const FALSE_VALUES = new Set(['', '0', 'false', 'no', 'off'])

/** True when an environment variable is set to anything other than an empty or false-like value. */
export function isTruthyEnv(value: string | undefined): boolean {
  return value !== undefined && !FALSE_VALUES.has(value.trim().toLowerCase())
}

/** True when running under a CI system (generic `CI`, GitHub Actions or GitLab CI). */
export function isCI(env: NodeJS.ProcessEnv = process.env): boolean {
  return isTruthyEnv(env.CI) || env.GITHUB_ACTIONS === 'true' || env.GITLAB_CI === 'true'
}

export interface BannerOptions {
  env?: NodeJS.ProcessEnv
  isTTY?: boolean
  noBanner?: boolean
}

/**
 * Decide whether the decorative block-character logo may be printed.
 *
 * The logo is shown only on an interactive terminal with no opt-out. NO_COLOR
 * follows no-color.org (any non-empty value). AELIRA_NO_BANNER accepts any
 * value except an empty or false-like one.
 */
export function shouldShowBanner(options: BannerOptions = {}): boolean {
  const env = options.env ?? process.env
  const isTTY = options.isTTY ?? Boolean(process.stdout.isTTY as boolean | undefined)
  if (options.noBanner || !isTTY) return false
  if (env.NO_COLOR) return false
  if (env.TERM === 'dumb') return false
  if (isCI(env)) return false
  return !isTruthyEnv(env.AELIRA_NO_BANNER)
}
