# Self-hosting

> Using AI for LMS remediation is a separate account-wide authorization boundary. Read [LMS AI policy and provider readiness](lms-ai-policy.md) before enabling a cloud or Ollama lane.

Aelira Core is built to run entirely on infrastructure you control:
PostgreSQL, Redis, and either a cloud LLM provider or a local one (Ollama).

The fastest production path is the full-stack compose file at the repo root:
`docker-compose.prod.yml` — published images, healthchecks, persistent
volumes, localhost-bound ports for your reverse proxy to front.
There is no dependency on a hosted Aelira service.

This guide covers a production deployment with Docker Compose. The
repository ships three compose files:

- **`docker-compose.prod.yml`** — the production stack this guide is about:
  published images, healthchecks, persistent volumes, required `.env`.
- `docker-compose.quickstart.yml` — zero-config evaluation, explicitly
  marked **not for production**.
- `docker-compose.dev.yml` — local development, hot-reloading the source
  tree from the host.

What follows explains what the production file runs and verifies every
image, service name, and environment variable against it,
`src/config/settings.py`, and `.env.example`.

## Images

Two Dockerfiles matter here:

- **`Dockerfile`** (repo root) — the production image. Multi-stage build:
  a builder stage installs Python dependencies from `requirements.txt` into a
  venv, then the runtime stage installs OS packages (Tesseract, Poppler,
  ffmpeg, the LaTeXML/TeX Live stack, Pandoc, Playwright's Chromium
  dependencies, Node.js for Pa11y), copies the venv, runs as a non-root
  `aelira` user, and starts via `entrypoint.sh`. `entrypoint.sh` runs
  `alembic upgrade head` and fails closed if migration fails; only after a
  successful migration does it exec
  `uvicorn` with `--workers "${UVICORN_WORKERS:-1}"` — one worker by
  default, deliberately: the job processor and sync Playwright use are not
  yet safe across multiple workers (the entrypoint documents why). Override
  `UVICORN_WORKERS` only if you know those constraints do not apply to you.
- **`dashboard/Dockerfile`** — builds the dashboard with `npm ci && npm run
  build` (build args `VITE_API_URL`, `VITE_WEBSITE_URL`), then serves the
  static output from `nginx:alpine` using `dashboard/nginx.conf`.

Every stable release tag (`vMAJOR.MINOR.PATCH`) publishes both pre-built images
through the coordinated release workflow:

```
ghcr.io/aelira-ai/aelira-core-api:latest      (or a specific X.Y.Z / X.Y tag)
ghcr.io/aelira-ai/aelira-core-dashboard:latest
```

built natively for `linux/amd64` and `linux/arm64`. The release does not expose
any of these tags until all four API/dashboard architecture builds have
succeeded and their digests have been verified. Using the published images
skips building them yourself; building from a Dockerfile directly works the
same way if you want to pin an exact commit or patch something locally.

For reproducible deployments, pin each image by the digest reported by your
registry tooling instead of a mutable tag, for example
`ghcr.io/aelira-ai/aelira-core-api@sha256:<digest>`. Keep the API and dashboard
digests from the same release version when updating a deployment.

## Required environment

These come from `src/config/settings.py` (the load-bearing ones — full list
is `.env.example`):

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | Required. `Settings` refuses to start if empty, or if it matches a known dev/placeholder pattern (`change_me`, `password@localhost`, etc. — see `validate_database_url`). |
| `REDIS_URL` | Redis connection string | Defaults to `redis://localhost:6379/0` if unset — set it explicitly for anything other than same-host Redis. |
| `ENV` | `production` | Must be one of `development`, `staging`, `production`, `test` (`validate_env`) — a typo like `prod` is rejected rather than silently falling through to dev behaviour. |
| `PUBLIC_API_URL` | Where the API is publicly reachable | Defaults to `http://localhost:8000`. Used to build absolute URLs (OAuth callbacks, links in emails) rather than hardcoding a vendor domain. |
| `PUBLIC_DASHBOARD_URL` | Where the dashboard is publicly reachable | Defaults to `http://localhost:5173`. |
| `CORS_ORIGINS` | Origins allowed to call the API | **Comma-separated** (`Settings` does `.split(",")` on this — a plain comma-separated list like `https://dashboard.example.org,https://scans.example.org`, not a JSON array). With no localhost fallback outside `development`/`test`, so a production deployment that leaves this unset blocks its own dashboard. |
| `SESSION_COOKIE_DOMAIN` | Shared parent cookie domain for split dashboard/API subdomains | When the dashboard and API use sibling hosts, set a common parent such as `.example.org`. The session cookies and readable double-submit CSRF cookie use this domain. Unrelated cross-site dashboard/API cookie authentication is not supported. |
| `JWT_SECRET` | Signs session JWTs (HS256) | If unset, `JWTService` generates a random secret at process start and logs a warning — sessions won't survive a restart. Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`. (RS256 is also supported via `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`, recommended for production per the `validate_jwt_algorithm` warning.) |
| `SESSION_REPLAY_ENCRYPTION_KEY` | Encrypts the short-lived cached token pair used to tolerate one concurrent refresh replay | Required in staging and production. Generate a Fernet key with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Give every API worker the same value. |
| `SESSION_REFRESH_GRACE_SECONDS` | Window for returning the exact cached replacement pair once | Defaults to `10`. Keep this short; increase it only to cover measured concurrent refresh latency. |
| `TOKEN_ENCRYPTION_KEY` | Encrypts stored OAuth tokens | Required if you enable any cloud integration (Google Workspace, Microsoft 365, Canvas OAuth). Generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `BLACKBOARD_OAUTH_ALLOWED_ORIGINS` | Exact Blackboard OAuth and bearer trust boundary | Required in staging/production when both Blackboard OAuth client credentials are configured. Use comma-separated canonical HTTPS root origins only, such as `https://blackboard.university.edu`; paths, userinfo, query strings, fragments, wildcards, private DNS, and foreign response origins are rejected. Removing an origin revokes persisted credentials on their next use. Development/test may omit the list only for the explicit localhost/test convention. |
| `REMEDIATION_ARTIFACT_DIR` | Durable root for managed remediation outputs | Defaults to `/app/uploads/remediation-artifacts`. Mount this path on persistent storage. Every API and worker replica must see the same bytes at the same path. |
| `REMEDIATION_ARTIFACT_RETENTION_DAYS`, `REMEDIATION_ARTIFACT_APPROVED_RETENTION_DAYS`, `REMEDIATION_ARTIFACT_WRITTEN_RETENTION_DAYS` | Artifact retention windows | Defaults to 30 days while pending, a 30-day writeback deadline after approval, and 7 days after writeback. Approval sets the artifact expiry to the approved-retention deadline; an idempotent approval retry does not extend it. See `.env.example` for all bounded cleanup and size settings. |
| `REMEDIATION_ARTIFACT_CLEANUP_BATCH_SIZE`, `REMEDIATION_ARTIFACT_STAGING_GRACE_SECONDS`, `DURABLE_MAINTENANCE_INTERVAL_SECONDS` | Bounded artifact recovery | Defaults to 100 artifacts per batch, a 3,600-second staging grace period, and a 300-second maintenance interval. At least one `python -m src.jobs.worker` process must remain running; its singleton maintenance loop claims and cleans eligible stale staging rows after the grace period. |
| `ALLOW_MOCK_AUTH` | Dev-only auth bypass | Must **not** be `true` in `production`/`staging` — `Settings` raises at startup if it is (`validate_mock_auth`). Leave unset. |
| SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `FROM_EMAIL`, `FROM_NAME` | Transactional email (magic links, alerts) | Read in `src/mailer/email_service.py`. Defaults assume SendGrid (`smtp.sendgrid.net`); any SMTP provider works. If `SMTP_HOST` is set, the mailer treats it as a trusted network target for its own connection handling. |
| One LLM provider's credentials | AI-generated explanations/remediation | `LLM_PROVIDER` (`gemini`/`ollama`/`openai`/`anthropic`) plus that provider's key (`GEMINI_API_KEY`, etc.), or `OLLAMA_HOST` if running the Ollama profile below. |

Branding/contact fields (`BRAND_NAME`, `PUBLIC_WEBSITE_URL`, `SUPPORT_EMAIL`)
are also read from environment variables in `settings.py` — worth setting so
outbound email doesn't point users at somebody else's support address. See
`BRANDING.md` for what you can and can't rename.

Treat `SESSION_REPLAY_ENCRYPTION_KEY` as persistent deployment state, not as a
value to regenerate at container startup. Store it in your secret manager and
use the same key across every API worker and after every restart. Include the
secret (securely and separately from the database dump) in your backup and
disaster-recovery procedure so a restored deployment has the same operational
configuration.

Rotate this key cautiously: replacing it while workers or recently written
refresh-replay ciphertext still use the old key can make an otherwise valid
concurrent refresh fail. Stop or drain all API workers, wait longer than
`SESSION_REFRESH_GRACE_SECONDS` so cached replay windows expire, update the key
atomically for every worker, and then restart them. Keep the prior key in your
protected secret backup until the rollout and restore verification are complete;
never run workers with mixed old and new values.

## Postgres, Redis, and the Ollama profile

Both existing compose files use the same two service images for storage:

- `pgvector/pgvector:0.8.1-pg16` for Postgres (Postgres 16 with the pgvector
  extension available — the WCAG knowledge base currently does its
  similarity search in Python rather than via pgvector, but the extension
  being present costs nothing and keeps the option open).
- `redis:7.4-alpine` for Redis.

Ollama is optional and gated behind a Compose profile in both existing
files (`profiles: ["ollama"]`), so it only starts when you ask for it:

```bash
docker compose -f docker-compose.prod.yml --profile ollama up -d
docker exec <ollama-container> ollama pull gemma3:4b   # or a larger model per docker-compose.dev.yml's guidance
```

Model selection, hardware tiers, and what each AI lane does are documented
in [local-ai-models.md](local-ai-models.md).

If you enable Canvas LTI, follow the [Canvas LTI administrator
configuration](canvas-lti.md) checklist. It covers the staff-visible placement
settings and required numeric Canvas course-ID custom field.

Run Ollama when you want documents to never leave your own infrastructure —
no cloud API call for remediation text at all. Without it, set `LLM_PROVIDER=gemini`
(or `openai`/`anthropic`) and supply that provider's API key.

## Reverse proxy / TLS

The repo ships two nginx configs you can use as a starting point (adapt the
`server_name` and certificate paths — both currently use `.example.com`
placeholders, not real hosts):

- **`nginx.conf`** (repo root) — reverse proxy in front of the API:
  HTTP→HTTPS redirect, an ACME challenge location for Let's Encrypt,
  rate-limiting zones (`api_limit` at 10r/s, a stricter `upload_limit` at
  2r/s on the scan endpoints), and response caching for `/docs` and
  `/openapi.json`.
- **`dashboard/nginx.conf`** — serves the built dashboard as a static SPA
  (falls back to `index.html` for client-side routes), with cache headers
  for hashed assets and a `/health` endpoint.

Any TLS-terminating reverse proxy works the same way (Caddy and Traefik are
common choices) — the requirement is just that it forwards to the API
container on port 8000 and the dashboard container on port 80, and that
`PUBLIC_API_URL` / `PUBLIC_DASHBOARD_URL` / `CORS_ORIGINS` match whatever
public hostnames you put in front of them.


## Backups

Postgres holds everything that matters — scans, users, departments, the
WCAG knowledge base. A straightforward logical backup:

```bash
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U ${POSTGRES_USER:-aelira} ${POSTGRES_DB:-aelira} > backup.sql
```

Restore with `psql -U <user> -d <db> < backup.sql` against a fresh database.
Uploaded and remediated files live outside Postgres. Include `/app/uploads`
and `REMEDIATION_ARTIFACT_DIR` in your backup and restore verification. Managed
remediation output is claimed in Postgres as `staging` before bytes are
published, so an interrupted worker leaves a database-known row that bounded
cleanup can recover; do not delete unknown files by scanning this directory.
Consumers must use the service's descriptor-bound `open_verified(...)` context
rather than retaining a resolved path. Managed PDF producers likewise publish
the exact claimed stream, not a later reopen of `output_file`: the service
recomputes size, SHA-256, MIME type, scan type, and filename before the DB-first
`staging` row becomes `available`. A multi-replica deployment must mount
durable shared storage at the artifact path for all API and worker replicas;
container-local filesystems will split the database authority from the bytes.
The normal retention window is reset to
`REMEDIATION_ARTIFACT_WRITTEN_RETENTION_DAYS` when writeback is durably marked
complete.

Cancellation, ownership-fence loss, and completion-commit failure abort only
the exact staging publication identified by its artifact ID and private
publication token. If abort or filesystem cleanup fails, the API or durable job
keeps the failure explicit: inspect operator logs and, for durable jobs, the
returned `publication_cleanup_pending`/artifact ID state. Keep at least one
`python -m src.jobs.worker` process healthy. Its singleton maintenance loop runs
every `DURABLE_MAINTENANCE_INTERVAL_SECONDS`, waits at least
`REMEDIATION_ARTIFACT_STAGING_GRACE_SECONDS` before treating a staging row as
stale, and removes at most `REMEDIATION_ARTIFACT_CLEANUP_BATCH_SIZE` eligible
artifacts per batch. Monitor worker logs and the artifact row after the grace
period plus a maintenance interval. Do not delete unknown files or rows by hand;
if the same artifact remains pending, preserve its artifact ID, lifecycle state,
and sanitized worker logs for investigation. PDF candidate and working-copy
cleanup warnings include a retained path when manual removal is required. Do not
report or treat these warnings as a successful publication, and never copy
publication tokens into support tickets or public logs.

The descriptor-bound managed PDF implementation uses Unix directory-descriptor
and `fcntl` semantics and is supported on macOS/Linux/other compatible Unix
hosts. It is not a Windows filesystem portability promise. Path-oriented
compatibility remains for non-PDF formats and direct library callers, but the
managed PDF output claim is authoritative.

This PDF publication hardening is present on `main` after v0.9.5 and is not part
of v0.9.5. It may ship in a future release after release verification; merging
it created no release or deployment.

Approval is a bounded writeback authorization, not indefinite retention. An
approved but unwritten artifact is held only until its
`REMEDIATION_ARTIFACT_APPROVED_RETENTION_DAYS` deadline. Writeback is rejected
at or after that instant, and cleanup deletes the expired row and managed bytes,
which releases the row's parent `RESTRICT` references. The approval audit
metadata and checksum remain unchanged for the lifetime of the row; retries do
not silently move the deadline. After expiry, generate a new remediation
artifact and obtain a new approval before attempting writeback.

Managed artifact rows deliberately restrict deletion of their owning
department, scan, cloud file, or remediation job. Parent deletion first acquires
a database cleanup fence, then re-locks and revalidates the parent and its
artifacts before descriptor-confined byte deletion and row removal. The
publication and cleanup cannot both own the same artifact. If the fence or byte cleanup
cannot complete, parent deletion remains blocked rather than orphaning bytes.
Use the service-managed maintenance path above; do not scan the artifact directory or
manually delete artifact rows to force progress.

## Upgrade procedure

1. Take and verify a PostgreSQL backup before changing the application or
   schema.
2. `git pull` (or pull the new image tag).
3. Rebuild if building locally: `docker compose -f docker-compose.prod.yml build api dashboard`, or
   just re-pull if using the published `ghcr.io/aelira-ai/...` images.
4. Run the migration explicitly and check its exit code before starting the
   application:
   ```bash
   docker compose -f docker-compose.prod.yml run --rm --entrypoint alembic api upgrade head
   ```
   The normal API entrypoint also runs this command and fails closed rather
   than serving against an incompatible schema.
5. `docker compose -f docker-compose.prod.yml up -d`, then confirm `GET /health`
   on the API and check `docker compose -f docker-compose.prod.yml logs -f api`
   for migration or startup errors.

### v0.9.4 upgrade

The v0.9.4 security migration intentionally disables all legacy API keys that
used the static `aelira_live_` prefix. This is a breaking security change, not
an authentication outage: reissue keys after the migration and update every
CLI, integration, or automation client before treating resulting HTTP `401`
responses as an application failure.

Existing LTI-provisioned users must relaunch from an authorized staff Canvas
placement to complete reauthorization. Canvas OAuth credentials whose stored
origin is no longer in `CANVAS_OAUTH_ALLOWED_ORIGINS` must reconnect.
Blackboard OAuth credentials whose stored origin is no longer in
`BLACKBOARD_OAUTH_ALLOWED_ORIGINS` must likewise reconnect; validation runs
before token refresh or bearer-client construction.

There is no supported in-place downgrade to v0.9.3. Keep the pre-upgrade database backup:
returning to v0.9.3 requires restoring that backup together
with the matching v0.9.3 images. Do not run newer images against the restored
older schema, or older images against the migrated schema.
