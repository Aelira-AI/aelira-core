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

## Configure the environment

Start from the repository's canonical template and replace every placeholder
in its **REQUIRED FOR PRODUCTION COMPOSE** section:

```bash
cp .env.example .env
```

The production file supplies `DATABASE_URL` and `REDIS_URL` with the bundled
`postgres` and `redis` service names, and supplies `ENV=production`. Leave the
direct host-development examples commented so they do not override that
container topology. Operators using an external database, Redis instance, or
another environment may explicitly set those variables in `.env`.

Also set `PUBLIC_API_URL`, `PUBLIC_DASHBOARD_URL`, and `CORS_ORIGINS` to the
public HTTPS origins served by your reverse proxy before starting the stack.

## Images

Two Dockerfiles matter here:

- **`Dockerfile`** (repo root) — the production image. Multi-stage build:
  a builder stage installs Python dependencies from `requirements.txt` into a
  venv, then the runtime stage installs OS packages (Tesseract, Poppler,
  ffmpeg, the LaTeXML/TeX Live stack, Pandoc, Playwright's Chromium
  dependencies, Node.js for Pa11y), copies the venv, runs as a non-root
  `aelira` user, and starts via `entrypoint.sh`. `entrypoint.sh` runs
  `alembic upgrade head` and fails closed if migration fails; only after a
  successful migration does it exec `uvicorn` with
  `--workers "${UVICORN_WORKERS:-2}"`. Set `UVICORN_WORKERS` to match the
  host's API capacity; long-running scans execute in the separate durable
  worker service and use the shared upload volume.
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

## API and worker topology

The API validates tenant scope and writes durable queue rows. It never consumes
those rows. CPU-bound scans, Playwright sessions, multimedia transcription, and
document/content remediation run only in the separate `worker` service through
the explicit `python -m src.jobs.worker` entrypoint. API and worker replicas
must use the same release image, PostgreSQL, Redis, provider configuration, and
`/app/uploads` volume; decrypted provider credentials never belong in queue
payloads.

The container probe runs
`python -m src.jobs.healthcheck --mode readiness` and verifies the worker's
database heartbeat, queue progress, leases, and running-job age. A super
administrator can inspect the same bounded global state at
`/api/jobs/worker-status`. The response contains no tenant, document, worker,
job, or provider credential data.

### Resource limits

`JOB_WORKER_MAX_CONCURRENCY` bounds simultaneous jobs in each worker process
(Compose default `1`, valid range `1`–`64`). The shipped worker quota is `0.75`
CPU so a one- or two-core host retains scheduler headroom for API health and
authentication. Increase `JOB_WORKER_CPUS` or concurrency only after measuring
peak memory, CPU, and API latency. The worker service intentionally has no fixed
container name or fixed worker ID: Compose replicas receive independently
generated claim identities. Scale with `docker compose -f
docker-compose.prod.yml up -d --scale worker=N`, keeping the sum of worker CPU
quotas below host capacity.

`JOB_WORKER_MAX_EXECUTION_SECONDS` defaults to 3,600 seconds. The shipped
`JOB_WORKER_STOP_GRACE_PERIOD` is 65 minutes, deliberately longer than that
execution ceiling so a normal stop can drain or kill and reap child process
groups before Docker sends SIGKILL. If you raise the execution ceiling, raise
the stop grace period above it as well.

### Worker recovery

First stop new intake, then inspect `/api/jobs/worker-status` and the worker
logs. Restart only the worker with
`docker compose -f docker-compose.prod.yml restart worker`. Active claims keep
their lease while work runs; after an unclean stop, expired claims are fenced
and recovered by a healthy worker according to their bounded retry policy.
PostgreSQL advisory authority distinguishes a live or frozen child from a dead
one: cancellation remains a nonterminal request while the child connection is
alive, and is acknowledged only after the child has stopped or its dead
connection has released that authority. Do not force terminal queue state for
a frozen worker; stop the owning container so recovery can prove child death.
Confirm a fresh heartbeat and movement in the aggregate claimed/completed/failed
counters before resuming intake. Never edit claim tokens or queue rows by hand.

### Worker rollback

Drain work before changing images. `docker compose -f docker-compose.prod.yml stop worker`
sends the worker its drain signal; allow enough stop time for the
largest admitted job. Pin `AELIRA_VERSION` to the previous verified release for
both API and worker, restore the matching database backup if that release is not
schema-compatible, then start the full matched stack. Do not run an older
worker against a newer API/schema or roll back only one process. Recheck API
health, `src.jobs.healthcheck`, and `/api/jobs/worker-status` before reopening
intake.

## Monitoring and sustained alerts

The API separates process liveness from dependency readiness. `GET /live`
returns `200` when the API process can answer without touching PostgreSQL or
Redis. `GET /ready` checks both dependencies and returns `503` with only the
bounded check names when the service should stop receiving traffic. The legacy
`/health` and `/api/health` liveness routes remain available for existing
integrations.

The worker exposes the same distinction through its container command:

```bash
docker compose -f docker-compose.prod.yml exec worker \
  python -m src.jobs.healthcheck --mode liveness --json
docker compose -f docker-compose.prod.yml exec worker \
  python -m src.jobs.healthcheck --mode readiness --json
```

Neither response includes a worker, tenant, job, scan, file, document,
credential, or provider identifier. Inspect the default Docker health states
with:

```bash
docker compose -f docker-compose.prod.yml ps
docker inspect --format '{{json .State.Health}}' \
  "$(docker compose -f docker-compose.prod.yml ps -q worker)"
```

### Prometheus and Alertmanager

Prometheus can scrape the API's existing `/metrics` endpoint from the private
deployment network. The worker collector reads aggregate queue state from
PostgreSQL and exports only fixed, low-cardinality gauges. A minimal scrape job
is:

```yaml
scrape_configs:
  - job_name: aelira-api
    metrics_path: /metrics
    static_configs:
      - targets: [api:8000]

rule_files:
  - /etc/prometheus/rules/aelira-alerts.yml
```

Mount the repository's `ops/prometheus/aelira-alerts.yml` at that rule path.
The rules wait two to five minutes before firing for an unavailable API,
missing worker heartbeat, expired lease, or stalled job. A single failed probe
does not page an operator.

Alertmanager sends recovery notifications when the receiver enables resolved
delivery. For example:

```yaml
route:
  receiver: operations
receivers:
  - name: operations
    webhook_configs:
      - url: https://monitoring.example.edu/aelira-alerts
        send_resolved: true
```

Keep the receiver URL and credentials in your monitoring secret store, not in
the Aelira repository or Compose environment. Prometheus automatically marks
the matching alert resolved after its expression becomes false; the receiver
then delivers the recovery event.

### Gatus

Gatus can distinguish a dead API from a dependency-blocked API with two
endpoints. Use consecutive-failure conditions in the Gatus alert configuration
so a single transient does not notify:

```yaml
endpoints:
  - name: aelira-api-liveness
    url: http://api:8000/live
    interval: 30s
    conditions: ["[STATUS] == 200", "[BODY].status == alive"]
    alerts:
      - type: email
        failure-threshold: 4
        success-threshold: 2
  - name: aelira-api-readiness
    url: http://api:8000/ready
    interval: 30s
    conditions: ["[STATUS] == 200", "[BODY].status == ready"]
    alerts:
      - type: email
        failure-threshold: 4
        success-threshold: 2
```

The four-failure threshold is two sustained minutes; the two-success recovery
threshold prevents a single recovered probe from prematurely clearing an
incident. Configure the referenced Gatus provider separately for your chosen
notification channel.

## Required environment

These come from [`src/config/settings.py`](../../src/config/settings.py) (the
load-bearing ones — the full list is [`.env.example`](../../.env.example)):

After adding, removing, or renaming runtime configuration, run
`python scripts/verify_environment_example.py`. CI runs the same deterministic
parity contract and requires every runtime name to be documented there or
explicitly classified as internal, derived, legacy, or Compose-only.

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | Production Compose derives a service-local URL from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. Set it explicitly only for an external database or direct host development. `Settings` refuses empty and known unsafe values (`change_me`, `password@localhost`, etc. — see `validate_database_url`). |
| `REDIS_URL` | Redis connection string | Production Compose defaults to `redis://redis:6379/0`. Direct host development defaults to `redis://localhost:6379/0`; set it explicitly for any other topology. |
| `ENV` | Runtime mode | Production Compose defaults to `production`; direct host development defaults to `development`. Must be one of `development`, `staging`, `production`, `test` (`validate_env`). |
| `PUBLIC_API_URL` | Where the API is publicly reachable | Defaults to `http://localhost:8000`. Used to build absolute URLs (OAuth callbacks, links in emails) rather than hardcoding a vendor domain. |
| `PUBLIC_DASHBOARD_URL` | Where the dashboard is publicly reachable | Defaults to `http://localhost:5173`. |
| `CORS_ORIGINS` | Origins allowed to call the API | **Comma-separated** (`Settings` does `.split(",")` on this — a plain comma-separated list like `https://dashboard.example.org,https://scans.example.org`, not a JSON array). With no localhost fallback outside `development`/`test`, so a production deployment that leaves this unset blocks its own dashboard. |
| `SESSION_COOKIE_DOMAIN` | Optional parent domain for authenticated session cookies only | Leave unset for the secure host-only default. A dashboard on `dashboard.example.org` calling `api.example.org` does **not** require a parent-domain session cookie: the browser sends the API's host-only cookie to the API. Set this only when the authenticated session itself must be sent to multiple sibling hosts. |
| `CSRF_COOKIE_DOMAIN` | Optional parent domain for the readable double-submit CSRF token only | For a sibling-host dashboard/API deployment, set `.example.org` so dashboard JavaScript can read the token and echo it to the API in `X-CSRF-Token`, while `SESSION_COOKIE_DOMAIN` remains unset. Leave unset when dashboard and API share a host. Values must be bare DNS domains without a scheme, port, path, wildcard, or trailing dot; invalid values fail startup. Unrelated cross-site cookie authentication is not supported. |
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
| One LLM provider's credentials | AI-generated explanations/remediation | AI defaults to disabled. Set `LLM_PROVIDER` to `gemini`, `ollama`, `openai`, `anthropic`, or `xai`, plus that provider's key or `OLLAMA_HOST`. `LLM_FALLBACK_PROVIDER` is independently opt-in. |
| `EMBEDDING_PROVIDER` | Optional semantic WCAG retrieval | Defaults to `none`; exact rule-ID grounding still works. Set `ollama` only when you want semantic search and have the embedding model installed. |

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

## Account and department provisioning

Provisioning is closed by default. On an empty database, the first person to
complete a verified magic-link login becomes the initial administrator and gets
the deployment's first department. After that bootstrap, unknown email
addresses cannot create accounts unless an administrator invites them, an LMS
launch or domain-matched SSO provisions them, or the operator deliberately sets
`OPEN_SIGNUP=true`.

Creating another department is a separate operation. `POST /auth/departments`
accepts a normal `ADMIN` or `SUPER_ADMIN` session, or an API key owned by one of
those users. LTI launch sessions remain scoped to their existing department and
cannot use this endpoint. Cookie-authenticated requests must include the
double-submit `X-CSRF-Token`; API-key requests use `Authorization: Bearer` and
do not need a CSRF token.

The request's `contact_email` is also the first administrator by default. Set
`first_admin_email` when the operational contact and the person receiving
administrator access are different. The server always assigns the handoff to
the newly created department with the `ADMIN` role; neither the caller nor an
email domain can select another tenant or elevate the recipient to
`SUPER_ADMIN`.

Provisioning stores the department, its single first-administrator handoff, and
the required audit records in one transaction. The emailed link opens the
dashboard's `/accept-invitation` page. The recipient confirms the invited email
and their name there; acceptance creates the target department's administrator
account but does not create a login session. They then use the normal magic-link
login and can manage users from `/admin` within that department.

Treat outbound email as part of the provisioning run: configure SMTP and set
`PUBLIC_DASHBOARD_URL` before creating departments. The raw handoff token is
only sent in the email and is never returned by the API or stored in plaintext.
An authenticated exact repeat from the administrator who created the department
reuses the existing department and handoff rather than creating duplicates. It
preserves the active link and suppresses duplicate email for 15 minutes; after
that delivery window, or after expiry, it rotates the token and queues a fresh
link. Anonymous provisioning cannot recover an existing handoff, and a repeat
from another operator, a different administrator, or materially different
department details is rejected. Expired links cannot create accounts; replaying
an already accepted link reports the completed result without creating another
user. Failed background delivery is recorded in server logs, and the original
operator can retry after the delivery window.

Set `ALLOW_PUBLIC_DEPARTMENT_CREATION=true` only when anonymous department
creation is intentional, such as an isolated public demo. Anonymous browser
clients still need a valid double-submit CSRF token and receive the same
tenant-bound administrator handoff; the anonymous source is recorded explicitly
in the audit trail. Keep the default `false` for institutional deployments.

## Blackboard LTI signing keys

Blackboard LTI uses one deployment-global RSA signing identity. Generate a
matching RSA key pair of at least 2048 bits, store the private PEM in your
secret manager, and mount both files read-only into every API replica. Set
`BLACKBOARD_LTI_PRIVATE_KEY_PATH` and `BLACKBOARD_LTI_PUBLIC_KEY_PATH` to those
mounted files. Register the canonical Tool JWKS URL with Anthology Blackboard:
`https://<your-public-api>/lti/blackboard/jwks`.

For example, generate a 3072-bit pair outside the application container:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out blackboard-lti-private.pem
openssl pkey -in blackboard-lti-private.pem -pubout -out blackboard-lti-public.pem
chmod 600 blackboard-lti-private.pem
```

The JWKS endpoint publishes only RFC 7517 public parameters. Its `kid` is the
stable RFC 7638 thumbprint of that public key. A missing, malformed, undersized,
or mismatched active pair makes the endpoint return `503` until the operator
repairs the configuration.

Rotate in three cache-safe phases using the public-only, comma-separated
`BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS` list:

1. Prepublish the future public key in the overlap list on every replica,
   restart or roll all replicas, and wait at least the JWKS cache TTL of 300
   seconds. The old key remains active during this phase.
2. Switch the active pair to the new matching private/public files, retain the
   old public key in the overlap list, and restart or roll every replica. Both
   public keys remain continuously available while new tokens use the new key.
3. Remove the old public key only after the maximum old-token lifetime, allowed
   clock skew, rolling-rollout duration, and an additional 300-second JWKS cache
   TTL have all elapsed; then restart or roll every replica again.

The active key appears first in JWKS; duplicate public keys are collapsed by
their immutable thumbprint. A running process never rereads signing files:
signing and JWKS publication use one construction-time snapshot, so rotations
take effect only after that replica restarts.

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
LLM_PROVIDER=ollama EMBEDDING_PROVIDER=ollama docker compose -f docker-compose.prod.yml --profile ollama up -d
docker exec <ollama-container> ollama pull gemma3:4b   # or a larger model per docker-compose.dev.yml's guidance
```

Model selection, hardware tiers, and what each AI lane does are documented
in [local-ai-models.md](local-ai-models.md).

If you enable Canvas LTI, follow the [Canvas LTI administrator
configuration](canvas-lti.md) checklist. It covers the staff-visible placement
settings and required numeric Canvas course-ID custom field.

Run Ollama when you want documents to never leave your own infrastructure —
no cloud API call for remediation text at all. Otherwise select Gemini,
OpenAI, Anthropic, or xAI explicitly and supply that provider's API key.

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

The shipped API commands disable Uvicorn's automatic proxy-header rewriting so
the application has one client-address trust boundary. `TRUSTED_PROXY_CIDRS`
is empty by default, which means `X-Forwarded-For` and `X-Real-IP` are ignored
and the direct transport peer is authoritative. If your proxy connects from
loopback, set `TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128`; for a container
network, configure only that network's exact CIDR. The resolver walks a
forwarded chain from the trusted edge toward the client and uses the first
untrusted address, so a caller-prepended value does not win. Invalid chain
values fall back to the direct peer. Configure every trusted proxy to append
its observed peer to `X-Forwarded-For`, or to overwrite the header at the
public edge rather than passing a caller-supplied value through unchanged.
Custom ASGI servers or middleware must also leave `scope["client"]` unchanged
and must not pre-process proxy headers.


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

The immutable-source OCR and exact-byte PDF publication controls in this section
are included in v0.9.7. They were not part of v0.9.5.

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
5. `docker compose -f docker-compose.prod.yml up -d`, then confirm `GET /live`
   and `GET /ready` on the API, run the worker readiness command documented
   above, and check `docker compose -f docker-compose.prod.yml logs -f api`
   for migration or startup errors.

### v0.9.7 upgrade

Before replacing v0.9.6, drain active work and pause intake. Back up PostgreSQL,
uploads, and managed artifacts, and verify the restore path. Run `alembic upgrade
head` explicitly and confirm that the single Alembic head is
`20260831_institution_scope` before resuming traffic.

Set and retain `BYOK_ENCRYPTION_KEY` before storing workspace AI credentials.
Choose `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, and `EMBEDDING_PROVIDER`
deliberately; do not rely on an implicit vendor. Review `TRUSTED_PROXY_CIDRS`,
cookie domains, Brightspace OAuth origins, Blackboard RSA signing keys, and the
shared upload and artifact mounts on every API and worker replica.

Deploy the API and dedicated `python -m src.jobs.worker` service from the same
v0.9.7 image set. Confirm API readiness, a fresh worker readiness result, queue
age, failed or quarantined rows, and alert recovery before reopening intake.

Client integrations must account for three breaking changes: multimedia
transcription now returns an asynchronous scan handle, Brightspace remediation
returns HTTP `202` job descriptors, and the unauthenticated focus-order HTTP
endpoints have been removed. Preserve every v0.9.6 operator action, including
remediation timeout settings and deliberate handling of quarantined work.

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
