# Upgrade and recovery rehearsal

Use `scripts/verify_upgrade_recovery.ts` to compare persisted application state
before an upgrade, after it, and after restoring the previous release into fresh
storage. It calls the real API and durable worker with synthetic documents. It
does not migrate, stop, back up, restore or delete infrastructure itself.

This is an integration gate. It does not replace browser journeys, published
image checks, packed CLI checks or verification of prerelease distribution.

## Prepare an isolated stack

Run the previous release with PostgreSQL, Redis, an API, a worker, shared durable
uploads and local Mailpit. Use fresh synthetic credentials and an empty database.
Set `ENV=production`, `OPEN_SIGNUP=false`, and all three of `LLM_PROVIDER`,
`LLM_FALLBACK_PROVIDER` and `EMBEDDING_PROVIDER` to `none`. Route all email to
Mailpit. Keep the environment isolated from external services.

Use separate volumes, network, container names and host ports. The production
Compose file has explicit container names: changing only the Compose project
name does **not** isolate it. A separate host is another option. Do not point
this probe at an existing institutional installation.

Expose the test API and Mailpit only on loopback, or use SSH forwarding from
local ports to their private container addresses. Docker internal networks can
disable published host ports; check the actual bindings. Loopback HTTP requires
`SESSION_COOKIE_SECURE=false` and `CSRF_COOKIE_SECURE=false` in this isolated
test. Keep secure cookies enabled on normal HTTPS deployments.

Record the release SHA, platform, image digests, Alembic revision, configuration
identity and volume mappings. Preserve encryption keys with the private backup;
do not put configuration secrets, cookies or magic links in test output.

## Seed the previous release

From the repository root, with Bun installed:

```bash
export STACK_API_URL=http://127.0.0.1:18300
export STACK_MAIL_URL=http://127.0.0.1:18325
export STACK_EVIDENCE_DIR="$PWD/test-results/upgrade-recovery"
export STACK_TEST_EMAIL=upgrade.rehearsal@example.org

STACK_PHASE=baseline STACK_ALLOW_LEGACY_CSRF_MISMATCH=true \
  bun scripts/verify_upgrade_recovery.ts seed
```

The legacy exception applies specifically to v0.9.11's known first-response
CSRF mismatch: the harness uses the issued cookie and records the mismatch.
Leave the exception unset when checking a release containing its fix.

v0.9.11 also reports `email_notifications=true` even when the saved
`email_scan_complete` value is false. The seed deliberately requests false.
Before accepting this expected API correction during an upgrade, independently
check that the synthetic user's value is false in both the backup restored into
fresh storage and the upgraded database. Then use
`STACK_EXPECT_LEGACY_NOTIFICATION_CORRECTION=true` for the upgraded `verify`
invocation only. This requires exactly the legacy true-to-false response change;
all other profile fields, saved scan results and artifact hashes still must match.
The evidence records the exception. Leave it unset for baseline restoration.

Seeding creates the first administrator, changes notification and timezone
preferences, and scans/remediates a spreadsheet and Word document. The
spreadsheet must produce a digest-verified download. The Word document must
retain its unresolved findings and refuse a download. The resulting `seed.json`
contains the synthetic profile, complete scan results, remediation outcomes and
download hashes. Keep this snapshot unchanged; seeding refuses to overwrite it.
Each invocation requests a fresh login. The application permits five magic-link
requests per email per hour; repeated diagnostics can reach that limit. Preserve
the rate limit and resume when its window permits another login.

## Capture a consistent backup and upgrade

Pause test intake, allow jobs to finish, then stop both the API and worker.
Confirm they have stopped before capturing PostgreSQL with `pg_dump -Fc` and
archiving the upload volume. Include separately configured remediation and
report artifact directories when they are outside that volume. Capture the
private runtime configuration and encryption keys in a protected backup file.
Record file hashes, inspect the dump catalog and archive listing, and verify
backup permissions. A database dump alone cannot restore downloadable artifacts.

Upgrade both API and worker together, following the
[self-hosting upgrade instructions](self-hosting.md#upgrade-procedure). Record the new
image identities and migration revision. Require API readiness and worker
readiness before checking saved data:

```bash
STACK_PHASE=upgraded bun scripts/verify_upgrade_recovery.ts verify
STACK_PHASE=candidate-new bun scripts/verify_upgrade_recovery.ts exercise
```

`verify` compares saved profile fields, complete scan results, job dispositions,
artifact identity and exact downloaded bytes. `exercise` creates a new verified
spreadsheet outcome, proving that the upgraded worker can still do new work.

## Restore into fresh storage

Use a second isolated stack with the previous pinned images, an empty database
and fresh upload/artifact volumes. Keep its API and worker stopped while restoring
the dump with `pg_restore --exit-on-error` and extracting the file archives.
Restore the matching private configuration and encryption keys, adapting only
the isolation-specific service names and ports. Preserve file ownership so the
non-root API and worker can read and write the restored storage.

Start the matched API and worker and check readiness. Point the local test URLs
at this restored stack, retain the original evidence directory, then run:

```bash
export STACK_API_URL=http://127.0.0.1:18310
export STACK_MAIL_URL=http://127.0.0.1:18335

STACK_PHASE=restored STACK_ALLOW_LEGACY_CSRF_MISMATCH=true \
  STACK_ABSENT_SNAPSHOT="$STACK_EVIDENCE_DIR/candidate-new.json" \
  bun scripts/verify_upgrade_recovery.ts verify
STACK_PHASE=restored-new STACK_ALLOW_LEGACY_CSRF_MISMATCH=true \
  bun scripts/verify_upgrade_recovery.ts exercise
```

The absence check requires the post-backup scan to return 404. This distinguishes
restoration of the earlier state from merely pointing the test at the upgraded
database. The final new job proves that recovery produces a usable installation.

Retain the backup and evidence securely, stop temporary services, and compare
the original installation's inventory with its pre-rehearsal state. Do not delete
its volumes or replace its configuration as part of this test.

## Interpret the evidence

A passing rehearsal proves only the tested release pair, platform, configuration
and seeded records. If their migration revisions are identical, it exercises no
schema transition and proves no migration downgrade. When versions are schema
incompatible, recovery requires the matching database **and** file backup with
the previous application images; swapping an image tag is insufficient.

A locally built candidate must be identified separately from a published
artifact. Repeat the gate against the actual release images before claiming
published-artifact upgrade support.
