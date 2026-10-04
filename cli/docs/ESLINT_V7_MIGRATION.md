# ESLint oclif 7 migration

The CLI pins `eslint-config-oclif` to 7.1.8 with ESLint 10.10.0. Its locked
plugin set includes `eslint-plugin-import-x` 4.17.1, `eslint-plugin-mocha`
11.3.0, `eslint-plugin-unicorn` 72.0.0, `eslint-plugin-perfectionist` 5.12.1,
`@stylistic/eslint-plugin` 5.10.0 and `typescript-eslint` 8.67.0. The exact
resolved tree and integrity hashes are in `package-lock.json`. The lock was
regenerated through npm's Arborist library in lockfile-only mode, without
running lifecycle scripts; CI's clean-install check is the reproducibility
gate. The lock changes only development packages; production dependency
entries retain their previous versions. The compiler and parser use root
TypeScript 5.9.3; the lock also has an optional, nested TypeScript 7.0.2 peer
under the oclif config for XO. The config explicitly keeps unsupported
TypeScript version warnings visible.
The CLI's TypeScript 7 migration remains separate work under #134.

The old ESLint 10 `fixupPluginRules` wrappers for import and Mocha are gone.
`@eslint/compat` stays because `includeIgnoreFile` still imports `.gitignore`.
The import rules now use the maintained `import-x` namespace: `no-unresolved`,
`namespace`, `default`, `export`, `no-named-as-default`,
`no-named-as-default-member` and `no-duplicates`. The last rule is now an
error and prefers inline type imports; seven Playwright import groups were
merged without moving executable code. `import-x/namespace` explicitly keeps
the prior rejection of computed namespace members. Mocha's
`no-empty-description` and `no-skipped-tests` are covered by `no-empty-title`
and `no-pending-tests`.

The v7 presets disabled several previously active core rules. The explicit
overrides in `eslint.config.mjs` retain them, including `no-return-await`,
`no-buffer-constructor`, constructor and assignment checks, unreachable-code
checks, restricted imports and unsafe negation. The new typed `return-await`
policy is deferred because adopting it would change JSON rejection handling
inside the CLI polling loop. Class member spacing and fractional numeric
grouping retain the previous settings. Date-based TODO expiry, including its
pull-request exception, and the global `NaN` check also retain their prior
settings after Unicorn changed its defaults.

Unicorn 72 removed `no-instanceof-array`, `prefer-dom-node-dataset`,
`no-array-push-push`, `no-length-as-slice-end` and `no-hex-escape`.
`no-instanceof-builtins` covers the first pattern; the other four upstream
rules have no direct replacement in this plugin release. The v6 lint baseline
reported no violations of them. Retaining a second, older Unicorn plugin
solely for those style checks would add duplicate tooling and maintenance,
so their retirement is intentional.

V7 also adds policy checks that the current CLI has never enforced. The
configuration lists 49 individual new-rule deferrals where the initial v7
lint reported existing violations; this keeps the dependency migration from
mixing with large semantic rewrites, especially around untyped REST responses.
Eight other new checks remain enabled for shipped source and are scoped off
only for test fixtures that replace global `fetch`, use local HTTP, capture
loop variables or construct synthetic responses. Future lint-policy work can
adopt the deferred rules in focused batches with behavior tests. That work is
independent of the TypeScript 7 upgrade in #134.
