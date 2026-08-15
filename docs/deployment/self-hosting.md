# Self-hosting

Aelira Core is built to run entirely on infrastructure you control:
PostgreSQL, Redis, and either a cloud LLM provider or a local one (Ollama).

The fastest production path is the full-stack compose file at the repo root:
`docker-compose.prod.yml` — published images, healthchecks, persistent
volumes, localhost-bound ports for your reverse proxy to front.
There is no dependency on a hosted Aelira service.

This guide covers a production-style deployment with Docker Compose. It does
not ship a ready-made `docker-compose.prod.yml` — the repository only
includes `docker-compose.quickstart.yml` (zero-config, explicitly marked
**not for production**) and `docker-compose.dev.yml` (local development,
hot-reloading the source tree from the host). For production you assemble a
compose file from the same pieces those two use, pointed at the production
image instead of the dev one. What follows verifies every image, service
name, and environment variable against those two files, `src/config/settings.py`,
and `.env.example`.

## Images

Two Dockerfiles matter here:

- **`Dockerfile`** (repo root) — the production image. Multi-stage build:
  a builder stage installs Python dependencies from `requirements.txt` into a
  venv, then the runtime stage installs OS packages (Tesseract, Poppler,
  ffmpeg, the LaTeXML/TeX Live stack, Pandoc, Playwright's Chromium
  dependencies, Node.js for Pa11y), copies the venv, runs as a non-root
  `aelira` user, and starts via `entrypoint.sh`. `entrypoint.sh` runs
  `alembic upgrade head` (logging a warning and continuing if it fails,
  rather than refusing to start) and then execs
  `uvicorn` with `--workers "${UVICORN_WORKERS:-1}"` — one worker by
  default, deliberately: the job processor and sync Playwright use are not
  yet safe across multiple workers (the entrypoint documents why). Override
  `UVICORN_WORKERS` only if you know those constraints do not apply to you.
- **`dashboard/Dockerfile`** — builds the dashboard with `npm ci && npm run
  build` (build args `VITE_API_URL`, `VITE_WEBSITE_URL`), then serves the
  static output from `nginx:alpine` using `dashboard/nginx.conf`.

Every tagged release (`git tag v*`) is also published as a pre-built image by
`.github/workflows/publish-docker.yml`:

```
ghcr.io/aelira-ai/aelira-core-api:latest      (or a specific vX.Y.Z / vX.Y tag)
ghcr.io/aelira-ai/aelira-core-dashboard:latest
```

built for `linux/amd64` and `linux/arm64`. Using these skips building the
image yourself; building from `Dockerfile` directly works the same way if
you want to pin an exact commit or patch something locally.

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
| `CORS_ORIGINS` | Origins allowed to call the API | **Comma-separated** (`Settings` does `.split(",")` on this — do not use the JSON-array syntax shown in the `.env.example` comment for this variable; a plain list like `https://dashboard.example.org,https://scans.example.org` is what the code actually parses). With no localhost fallback outside `development`/`test`, so a production deployment that leaves this unset blocks its own dashboard. |
| `JWT_SECRET` | Signs session JWTs (HS256) | If unset, `JWTService` generates a random secret at process start and logs a warning — sessions won't survive a restart. Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`. (RS256 is also supported via `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`, recommended for production per the `validate_jwt_algorithm` warning.) |
| `TOKEN_ENCRYPTION_KEY` | Encrypts stored OAuth tokens | Required if you enable any cloud integration (Google Workspace, Microsoft 365, Canvas OAuth). Generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `ALLOW_MOCK_AUTH` | Dev-only auth bypass | Must **not** be `true` in `production`/`staging` — `Settings` raises at startup if it is (`validate_mock_auth`). Leave unset. |
| SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `FROM_EMAIL`, `FROM_NAME` | Transactional email (magic links, alerts) | Read in `src/mailer/email_service.py`. Defaults assume SendGrid (`smtp.sendgrid.net`); any SMTP provider works. If `SMTP_HOST` is set, the mailer treats it as a trusted network target for its own connection handling. |
| One LLM provider's credentials | AI-generated explanations/remediation | `LLM_PROVIDER` (`gemini`/`ollama`/`openai`/`anthropic`) plus that provider's key (`GEMINI_API_KEY`, etc.), or `OLLAMA_HOST` if running the Ollama profile below. |

Branding/contact fields (`BRAND_NAME`, `PUBLIC_WEBSITE_URL`, `SUPPORT_EMAIL`)
are also read from environment variables in `settings.py` — worth setting so
outbound email doesn't point users at somebody else's support address. See
`BRANDING.md` for what you can and can't rename.

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
docker compose --profile ollama up -d
docker exec <ollama-container> ollama pull gemma3:4b   # or a larger model per docker-compose.dev.yml's guidance
```

Model selection, hardware tiers, and what each AI lane does are documented
in [local-ai-models.md](local-ai-models.md).

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

## Assembling a production compose file

A production `docker-compose.yml` built from the pieces above — same
service names and images as `docker-compose.dev.yml`, but using the
production `Dockerfile` (or the published `ghcr.io/aelira-ai/aelira-core-api`
image) instead of `Dockerfile.dev`, and no source-tree bind mounts:

```yaml
services:
  postgres:
    image: pgvector/pgvector:0.8.1-pg16
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-aelira}
      POSTGRES_USER: ${POSTGRES_USER:-aelira}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set a real password}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-aelira}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7.4-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  ollama:
    image: ollama/ollama:latest
    profiles: ["ollama"]
    volumes:
      - ollama_data:/root/.ollama

  api:
    image: ghcr.io/aelira-ai/aelira-core-api:latest   # or build: { context: ., dockerfile: Dockerfile }
    environment:
      ENV: production
      DATABASE_URL: postgresql://${POSTGRES_USER:-aelira}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-aelira}
      REDIS_URL: redis://redis:6379/0
      JWT_SECRET: ${JWT_SECRET:?generate with secrets.token_urlsafe(64)}
      TOKEN_ENCRYPTION_KEY: ${TOKEN_ENCRYPTION_KEY}
      PUBLIC_API_URL: ${PUBLIC_API_URL}
      PUBLIC_DASHBOARD_URL: ${PUBLIC_DASHBOARD_URL}
      CORS_ORIGINS: ${CORS_ORIGINS}
      LLM_PROVIDER: ${LLM_PROVIDER:-gemini}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      OLLAMA_HOST: http://ollama:11434
      SMTP_HOST: ${SMTP_HOST}
      SMTP_PORT: ${SMTP_PORT:-587}
      SMTP_USER: ${SMTP_USER}
      SMTP_PASSWORD: ${SMTP_PASSWORD}
      FROM_EMAIL: ${FROM_EMAIL}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  dashboard:
    image: ghcr.io/aelira-ai/aelira-core-dashboard:latest  # or build: { context: ./dashboard }
    depends_on:
      - api

volumes:
  postgres_data:
  redis_data:
  ollama_data:
```

Put your reverse proxy (nginx/Caddy/Traefik) in front of `api` (port 8000)
and `dashboard` (port 80), terminating TLS there. Everything referenced
above — image names, the `--profile ollama` gate, the healthcheck shape, the
`POSTGRES_*`/`DATABASE_URL` pattern — matches `docker-compose.dev.yml` and
`docker-compose.quickstart.yml`; only the image source (production
`Dockerfile` / published image, no bind-mounted source, `ENV=production`)
differs.

## Backups

Postgres holds everything that matters — scans, users, departments, the
WCAG knowledge base. A straightforward logical backup:

```bash
docker compose exec postgres pg_dump -U ${POSTGRES_USER:-aelira} ${POSTGRES_DB:-aelira} > backup.sql
```

Restore with `psql -U <user> -d <db> < backup.sql` against a fresh database.
Uploaded/remediated files live outside Postgres on whatever storage path
`save_uploaded_file()` writes to — include that path in your backup scope
too if you're relying on local disk rather than object storage.

## Upgrade procedure

1. `git pull` (or pull the new image tag).
2. Rebuild if building locally: `docker compose build api dashboard`, or
   just re-pull if using the published `ghcr.io/aelira-ai/...` images.
3. `docker compose up -d` — the `api` container's `entrypoint.sh` runs
   `alembic upgrade head` automatically on start. If it fails, it logs a
   warning and starts anyway rather than blocking, so also run it
   explicitly and check the exit code:
   ```bash
   docker compose exec api alembic upgrade head
   ```
4. Confirm health: `GET /health` on the API, and check
   `docker compose logs -f api` for migration or startup errors.

Take a Postgres backup (above) before step 3 on anything you can't afford to
lose — migrations in `alembic/versions/` are the same ones applied in
development and CI, but a schema change against production data is still
the point where a backup is worth having.
