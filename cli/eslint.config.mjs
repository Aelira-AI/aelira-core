// ESLint, eslint-config-oclif, and eslint-config-prettier are pinned to exact
// versions in package.json. An earlier ranged oclif config widened rule
// defaults without a source change, so upgrades are reviewed explicitly.
import {includeIgnoreFile} from '@eslint/compat'
import oclif from 'eslint-config-oclif'
import prettier from 'eslint-config-prettier'
import path from 'node:path'
import {fileURLToPath} from 'node:url'

const gitignorePath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '.gitignore')

// Custom rules for CLI consuming REST API
const customRules = {
  rules: {
    // v7 enables these XO, TypeScript, Node and Unicorn rules for the first time.
    // Existing CLI code was validated under the v6 effective rule set; adopt
    // the new policies separately so the tooling upgrade does not rewrite
    // command behavior or weaken any previously enabled check.
    '@typescript-eslint/consistent-type-definitions': 'off',
    '@typescript-eslint/consistent-type-imports': 'off',
    '@typescript-eslint/no-base-to-string': 'off',
    '@typescript-eslint/no-confusing-void-expression': 'off',
    '@typescript-eslint/no-dynamic-delete': 'off',
    '@typescript-eslint/no-empty-function': 'off',
    '@typescript-eslint/no-restricted-types': 'off',
    '@typescript-eslint/no-unnecessary-template-expression': 'off',
    '@typescript-eslint/no-unnecessary-type-assertion': 'off',
    '@typescript-eslint/no-unsafe-argument': 'off',
    '@typescript-eslint/no-unsafe-assignment': 'off',
    '@typescript-eslint/no-unsafe-call': 'off',
    '@typescript-eslint/no-unsafe-member-access': 'off',
    '@typescript-eslint/no-unsafe-return': 'off',
    '@typescript-eslint/prefer-nullish-coalescing': 'off',
    '@typescript-eslint/prefer-readonly': 'off',
    '@typescript-eslint/prefer-regexp-exec': 'off',
    '@typescript-eslint/promise-function-async': 'off',
    '@typescript-eslint/restrict-plus-operands': 'off',
    '@typescript-eslint/restrict-template-expressions': 'off',
    '@typescript-eslint/return-await': 'off',
    '@typescript-eslint/strict-void-return': 'off',
    '@typescript-eslint/switch-exhaustiveness-check': 'off',
    '@typescript-eslint/only-throw-error': 'off',
    'n/prefer-global/buffer': 'off',
    'n/prefer-promises/fs': 'off',
    'no-useless-assignment': 'off',
    'preserve-caught-error': 'off',
    'require-unicode-regexp': 'off',
    'unicorn/consistent-boolean-name': 'off',
    'unicorn/consistent-class-member-order': 'off',
    'unicorn/isolated-functions': 'off',
    'unicorn/logical-assignment-operators': 'off',
    'unicorn/no-array-sort': 'off',
    'unicorn/no-break-in-nested-loop': 'off',
    'unicorn/no-duplicate-loops': 'off',
    'unicorn/no-for-each': 'off',
    'unicorn/no-computed-property-existence-check': 'off',
    'unicorn/no-error-property-assignment': 'off',
    'unicorn/no-negated-array-predicate': 'off',
    'unicorn/prefer-continue': 'off',
    'unicorn/prefer-hoisting-branch-code': 'off',
    'unicorn/prefer-includes-over-repeated-comparisons': 'off',
    'unicorn/prefer-number-coercion': 'off',
    'unicorn/prefer-simple-condition-first': 'off',
    'unicorn/prefer-split-limit': 'off',
    'unicorn/prefer-unicode-code-point-escapes': 'off',
    'unicorn/prefer-url-href': 'off',
    'import-x/no-cycle': 'off',

    // v7 changed the default class-member spacing policy. Keep the previously
    // enforced field/method distinctions and let the rule remain an error.
    '@stylistic/lines-between-class-members': ['error', {
      enforce: [
        {blankLine: 'always', prev: '*', next: 'method'},
        {blankLine: 'always', prev: 'method', next: 'field'},
        {blankLine: 'never', prev: 'field', next: 'field'},
      ],
    }],
    // Unicorn 72 stopped grouping fractional digits by default. Keep the
    // previous three-digit grouping without changing any numeric literal.
    'unicorn/numeric-separators-style': ['error', {number: {fractionGroupLength: 3}}],
    // Unicorn 72 stopped checking dated TODOs and global NaN by default.
    // Preserve the v6 checks, including its PR-specific date exception.
    'unicorn/expiring-todo-comments': ['error', {checkDates: true, checkDatesOnPullRequests: false}],
    'unicorn/prefer-number-properties': ['error', {checkInfinity: false, checkNaN: true}],
    // TypeScript ESLint's v7 presets turn off several core checks that the
    // previous effective config enforced. Keep those checks active here.
    'constructor-super': 'error',
    'dot-notation': ['error', {allowKeywords: true, allowPattern: ''}],
    'getter-return': ['error', {allowImplicit: false}],
    'no-array-constructor': 'error',
    'no-buffer-constructor': 'error',
    'no-class-assign': 'error',
    'no-const-assign': 'error',
    'no-dupe-args': 'error',
    'no-dupe-keys': 'error',
    'no-func-assign': 'error',
    'no-implicit-globals': ['error', {lexicalBindings: false}],
    'no-import-assign': 'error',
    'no-new-native-nonconstructor': 'error',
    'no-obj-calls': 'error',
    'no-restricted-imports': ['error', 'domain', 'freelist', 'smalloc', 'punycode', 'sys', 'querystring', 'colors'],
    'no-return-await': 'error',
    'no-setter-return': 'error',
    'no-this-before-super': 'error',
    'no-throw-literal': 'error',
    'no-unreachable': 'error',
    'no-unsafe-negation': ['error', {enforceForOrderingRelations: true}],
    'no-unused-private-class-members': 'error',
    'no-with': 'error',
    'prefer-promise-reject-errors': ['error', {allowEmptyReject: true}],
    'unicorn/consistent-function-scoping': 'error',
    'unicorn/no-useless-undefined': 'error',
    // The old import/namespace rule rejected uncheckable computed members.
    'import-x/namespace': ['error', {allowComputed: false}],
    'import-x/default': 'error',
    'import-x/export': 'error',
    // API responses use snake_case (Python convention) - don't enforce camelCase
    'camelcase': 'off',

    // Pragmatic use of 'any' for REST API responses is acceptable in CLI tools
    '@typescript-eslint/no-explicit-any': 'off',

    // fetch is stable in Node 22+ (the CLI's minimum version)
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

// Test fixtures intentionally replace global fetch, serve plain HTTP locally,
// and capture per-case variables in callbacks. Keep the new v7 rules enabled
// for shipped source while retaining those fixture patterns in tests.
const testPolicyExceptions = {
  files: ['test/**/*.ts'],
  rules: {
    '@typescript-eslint/array-type': 'off',
    '@typescript-eslint/no-loop-func': 'off',
    '@typescript-eslint/use-unknown-in-catch-callback-variable': 'off',
    'unicorn/no-global-object-property-assignment': 'off',
    'unicorn/no-unnecessary-global-this': 'off',
    'unicorn/prefer-https': 'off',
    'unicorn/prefer-iterator-to-array': 'off',
    'unicorn/prefer-response-static-json': 'off',
  },
}

// Keep parser compatibility warnings visible during future TypeScript upgrades.
const typescriptVersionWarnings = {
  files: ['**/*.ts', '**/*.tsx'],
  languageOptions: {parserOptions: {warnOnUnsupportedTypeScriptVersion: true}},
}

const config = [includeIgnoreFile(gitignorePath), ...oclif, prettier, customRules, testPolicyExceptions, typescriptVersionWarnings]

export default config
