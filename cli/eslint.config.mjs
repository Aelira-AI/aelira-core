// eslint, eslint-config-oclif, eslint-config-prettier, and @eslint/compat are
// pinned to exact versions in package.json (no ^ range). eslint-config-oclif
// pulls in eslint-plugin-perfectionist/unicorn/@stylistic transitively, and
// package.json previously ranged it on ^6.0.150 while 6.0.179 was actually
// installed — a minor bump had already widened rule defaults with no code
// change on our side. Mirrors the ruff.toml explicit-select pin on the
// Python side, for the same reason (measured 2026-08-15).
import {includeIgnoreFile} from '@eslint/compat'
import oclif from 'eslint-config-oclif'
import prettier from 'eslint-config-prettier'
import path from 'node:path'
import {fileURLToPath} from 'node:url'

const gitignorePath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '.gitignore')

// Custom rules for CLI consuming REST API
const customRules = {
  rules: {
    // API responses use snake_case (Python convention) - don't enforce camelCase
    'camelcase': 'off',

    // Pragmatic use of 'any' for REST API responses is acceptable in CLI tools
    '@typescript-eslint/no-explicit-any': 'off',

    // fetch is stable in Node 18+ (our minimum version)
    'n/no-unsupported-features/node-builtins': 'off',

    // Sequential await in loops is intentional for batch processing with rate limiting
    'no-await-in-loop': 'off',

    // Allow console.log in CLI - it's how we communicate with users
    'no-console': 'off',

    // RequestInit is a global type in Node 18+ fetch API
    'no-undef': 'off',

    // forEach is fine for simple iterations - readability over micro-optimization
    'unicorn/no-array-for-each': 'off',

    // Import style is a matter of preference
    'unicorn/import-style': 'off',

    // Ternary expressions aren't always more readable than if statements
    'unicorn/prefer-ternary': 'off',

    // process.exit() is appropriate in CLI applications
    'n/no-process-exit': 'off',
    'unicorn/no-process-exit': 'off',

    // Unused function parameters are common for consistent signatures
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],

    // Complexity warnings are informational, not errors
    'complexity': 'warn',

    // max-params warning is fine
    'max-params': 'warn',

    // Sorting objects alphabetically is pedantic
    'perfectionist/sort-objects': 'off',

    // perfectionist/sort-classes forces alphabetical method order, which
    // conflicts with this codebase's convention of grouping class methods
    // under `// --- Section ---` banner comments. Its autofix physically
    // moves methods without moving the banner comments that describe them,
    // so a `--fix` run silently detaches every banner from the methods it
    // was written for (found while triaging lint churn, 2026-08-15 — see
    // auth.ts / scan/watch.ts in git history for the pre-fix layout this
    // protects). Off rather than warn: a warning would still invite a blind
    // `--fix` that breaks the comments again.
    'perfectionist/sort-classes': 'off',

    // no-void's default forbids `void expr` everywhere, but this codebase
    // uses `void somePromise()` as the standard, TS-ESLint-recommended way
    // to mark a floating promise as intentionally not awaited (e.g. inside
    // a setTimeout/event callback that can't be async). allowAsStatement
    // keeps the rule for accidental `void 0`-style expressions while
    // permitting the statement form. Configured 2026-08-15 while triaging
    // lint churn; see src/commands/scan/watch.ts for the pattern.
    'no-void': ['error', { allowAsStatement: true }],
  }
}

export default [includeIgnoreFile(gitignorePath), ...oclif, prettier, customRules]
