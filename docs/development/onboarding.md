# Developer onboarding

This is the guide requested in issue #3: how the codebase is put together, how
to get it running locally, how to run the tests, and where to start if you
want to contribute. Everything below is checked against the code as it
exists in this repository — file and module names are real, not illustrative.

## Architecture overview

Aelira Core is a FastAPI backend plus a separate React dashboard.

```
src/
  api/            FastAPI app and route modules (src/api/main.py)
  education/      document processors: PDF, Office, LaTeX, web, multimedia
  ai/             LLM provider abstraction, WCAG knowledge base, severity_rules.py
  remediation/    AutoRemediator facade that routes to the per-format remediators
  integrations/   Canvas, Blackboard, Moodle, Brightspace, Google Workspace, Microsoft 365
  jobs/           background job processor (cloud sync, scan/remediation jobs, email alerts)
  auth/           magic link, OAuth, API keys, sessions
  config/         Settings (src/config/settings.py) — every environment variable the app reads
  db/             SQLAlchemy models and database session handling
dashboard/        React 19 + Vite admin/dashboard UI
alembic/          database migrations
tests/            pytest suite (backend)
```

### API app

`src/api/main.py` builds the FastAPI app and mounts route modules — document
scanning and remediation (`src/api/education/`), auth, LMS integrations,
cloud storage, TTS, analytics, and webhooks. The document-scanning and
remediation routes specifically live under `src/api/education/`:
`scan_routes.py` (PDF/PPTX/DOCX/XLSX/LaTeX upload endpoints),
`web_scan_routes.py` (URL and code scanning), `remediation_routes.py`
(auto-remediation), and `scan_history_routes.py` (scan status, progress,
reports).

### Processors (`src/education/`)

Each document type has its own processor: `pdf_processor.py`,
`pptx_processor.py`, `docx_processor.py`, `xlsx_processor.py`,
`latex_processor.py`, `web_scanner.py`, `multimedia_processor.py`,
`image_alt_text.py`. These do the scanning and issue detection. Fixing the
issues is a separate concern, handled by `src/education/remediation/` (a
remediator per format: `docx_remediator.py`, `pptx_remediator.py`,
`pdf_remediator.py`, `xlsx_remediator.py`, `latex_remediator.py`,
`html_remediator.py`, `multimedia_remediator.py`), fronted by
`src/remediation/auto_remediator.py::AutoRemediator`, which picks the right
remediator for the file type.

### AI layer (`src/ai/`)

- **Provider abstraction** — `src/ai/providers/` (`base.py`, `manager.py`,
  `types.py`, plus one module per provider: `gemini_provider.py`,
  `ollama_provider.py`, `openai_provider.py`, `anthropic_provider.py`,
  `xai_provider.py`). `get_provider_manager()` returns the configured
  explicit primary/fallback pair (`LLM_PROVIDER` /
  `LLM_FALLBACK_PROVIDER`). With neither set, AI inference is disabled.
- **Severity is computed, not generated** — `src/ai/severity_rules.py`
  (`resolve_severity()` / `severity_for()`) is a pure function of the rule ID
  and the scanner's impact rating: no I/O, no model call, same input always
  produces the same severity. It's consumed by `docx_processor.py`,
  `xlsx_processor.py`, and `pdf_report_generator.py`. The web/code scanners
  get their severity from axe-core's own `impact` field via the
  `SEVERITY_BY_IMPACT` table in the same module. `tests/test_severity_determinism.py`
  is the test that would fail if this ever regressed to something
  non-deterministic.
- **WCAG knowledge base** — `src/ai/wcag_knowledge_base.py` does retrieval
  (cosine similarity over embeddings stored as JSONB in Postgres, not
  pgvector — the corpus is small enough that an index isn't worth the extra
  requirement for self-hosters) so the AI-written explanation for a violation
  cites a WCAG guideline that was actually looked up, not recalled. The
  API automatically seeds an empty corpus at startup. Known scanner rule IDs
  use exact lookup without vectors. Optional semantic search is independent:
  `EMBEDDING_PROVIDER=ollama` embeds missing rows with the configured model. The
  `scripts/seed_wcag_guidelines.py` and
  `scripts/generate_wcag_embeddings.py` entry points remain available for
  explicit operator repair.

### Integrations (`src/integrations/`)

Per-LMS/cloud-provider packages: `canvas/`, `blackboard/`, `blackboard_lti/`,
`moodle/`, `brightspace_lti.py`, `google_workspace/`, `microsoft_365/`, plus
shared pieces (`cloud_base.py`, `oauth_token_manager.py`). These let content
be pulled from an LMS or cloud drive and written back after remediation,
instead of requiring a manual download/upload round-trip.

### Jobs (`src/jobs/`)

`job_processor.py` is a background worker that polls a job queue table
(`cloud_job_queue`) and dispatches to registered handlers —
`cloud_scan_job.py`, `cloud_sync_job.py`, `remediation_job.py`,
`upload_job.py`, `email_alert_job.py`, `account_deletion_job.py`, and the local
scan dispatcher. This drives asynchronous cloud integration, uploaded
document, code, multimedia, and website scanning work. API routes persist the
input and enqueue a tenant-fenced job; the dedicated worker executes it.

### Dashboard (`dashboard/`)

React 19 + Vite + TypeScript + Tailwind, source under `dashboard/src/`
(`api/`, `pages/`, `components/`, `context/`, `hooks/`). Talks to the backend
over the URL in `VITE_API_URL` (see `dashboard/.env.example`).

## Life of a scan

Tracing a PDF scan through `src/api/education/scan_routes.py`. Paths here are
relative to the direct backend (`http://localhost:8000`). With the dashboard's
nginx proxy, prefix them with `/api`: nginx strips that prefix before forwarding.
The Vite dev server has no equivalent proxy; use the direct backend base URL.

1. **Upload** — `POST /education/pdf/scan` (`scan_pdf()` in
   `scan_routes.py`) validates the file, checks quota
   (`check_scan_quota()`), creates a `Scan` row with `status=PROCESSING`,
   saves the file (`save_uploaded_file()`), and returns `scan_id`
   after `enqueue_local_scan_job()` persists a `local_pdf` job in
   `cloud_job_queue` and the transaction commits.
2. **Scan** — the separate `python -m src.jobs.worker` process claims the
   durable job. `handle_local_scan_job()` verifies the stored input and launches
   a scan subprocess; its dispatcher calls `process_pdf_background()`. Despite
   that function's name, the API does not schedule a FastAPI background task.
   The processor calls `PDFProcessor.process_pdf()`, which
   detects accessibility issues (missing tags, reading order, alt text,
   contrast, tables) and reports progress back through a callback that
   updates `Scan.progress` in the database, so the client can poll
   `GET /education/scans/{scan_id}/progress`.
3. **Issues with computed severity** — each detected issue carries a
   `severity` field. For PDF/DOCX/XLSX this comes from
   `src/ai/severity_rules.py` as described above; for axe-core-based web/code
   scans it comes from axe's own impact rating through the same module's
   `SEVERITY_BY_IMPACT` mapping. The result is written to a `ScanResult` row
   (`compliance_score`, `critical_issues`/`high_issues`/`medium_issues`/`low_issues`,
   the raw `issues` list, `structure`, `html_output`).
4. **Remediation** — `POST /education/remediate/{scan_id}`
   (`enqueue_remediate_scan()` in `remediation_routes.py`) authorizes access and
   enqueues another durable job. The worker runs the matching format remediator,
   producing a managed artifact and recording issues that still need a human.
   `Prefer: respond-async` returns the job contract immediately; otherwise the
   route may wait briefly for a result before returning HTTP 202.
5. **Report** — `GET /education/scans/{scan_id}/report` and
   `GET /education/scans/{scan_id}/html` return the scan's findings;
   `GET /education/compliance/{department_id}/report/pdf`
   (`compliance_routes.py`) produces a PDF compliance report.

Other file types (PPTX, DOCX, XLSX, LaTeX, web pages, code) go through the
same shape — upload endpoint under `src/api/education/`, a processor under
`src/education/`, a remediator under `src/education/remediation/` — just with
a different processor/remediator pair.

## Dev environment

There are three ways to run this locally, described in the README and in
`CONTRIBUTING.md`:

### 1. Zero-config quickstart

```bash
docker compose -f docker-compose.quickstart.yml up -d
```

No `.env` file needed — Postgres, Redis, and the API come up with insecure
defaults baked into `docker-compose.quickstart.yml` itself (see the warning
at the top of that file: **not for production**). AI is disabled until chosen.
For local AI, run
`LLM_PROVIDER=ollama EMBEDDING_PROVIDER=ollama docker compose -f docker-compose.quickstart.yml --profile ollama up -d`.
API and docs land on `http://localhost:8000/docs`.

### 2. Full dev stack (`docker-compose.dev.yml`)

Install Docker with the Compose plugin (`docker compose`, including JSON config
and `up --wait --wait-timeout` support) and `jq`. No host Python or Bun is needed
for setup. With Docker running:

```bash
./setup-dev.sh
```

The script builds `Dockerfile.dev` from current sources, waits for Postgres and
Redis health, stops any existing API/worker, runs `alembic upgrade head`, then
starts and waits for both application services. API health uses `/ready`
(Postgres and Redis); worker health uses `python -m src.jobs.healthcheck --mode
readiness`. Each startup phase has a 180-second deadline; use
`--wait-timeout 300` for a slower machine. Any failed build, model pull,
migration or readiness check exits nonzero without a completion banner.
Reruns briefly interrupt API/worker service for migration; a failed migration
leaves them stopped so you can investigate before retrying. Model downloads and
image builds can take longer than the readiness deadline.

No `.env` is required for basic **local development**. Compose supplies local-only
Postgres credentials and an insecure development JWT secret. The script never
creates, sources, rewrites or prints `.env`; Compose resolves the repository's
`.env` plus exported environment values (shell values take precedence). Running
the script by absolute path from another directory uses the same repository
configuration, not that directory's `.env`. Review any `COMPOSE_ENV_FILES`
override you have explicitly exported, since Compose honors it too.

If you choose to create `.env`, copy `.env.example` only when there is no existing
file and edit its placeholders first. Local document scanning can omit
`TOKEN_ENCRYPTION_KEY`. For cloud OAuth/BYOK credential storage, export a valid
Fernet key or provide it in `.env` for both API and worker. Generate it using the
command in `.env.example`, store it privately and retain it across runs. Do not
replace an existing key: stored credentials depend on it. Infrastructure readiness
does not prove a document-processing or cloud integration workflow succeeds. Set your own `JWT_SECRET` for
anything reachable beyond your machine; these defaults are not production setup.

AI remains disabled unless you choose a provider. To opt into bundled Ollama:

```bash
LLM_PROVIDER=ollama ./setup-dev.sh
# Optional semantic WCAG retrieval, selected independently:
LLM_PROVIDER=ollama EMBEDDING_PROVIDER=ollama ./setup-dev.sh
```

The script respects configured cloud providers and their credentials. These
variables configure deployment defaults; they do not rewrite saved workspace AI
provider choices. Selecting Ollama as primary, fallback (`LLM_FALLBACK_PROVIDER=ollama`), or embedding provider
automatically enables its Compose profile. It pulls exactly the resolved text,
code and vision models for inference, and the embedding model only when
`EMBEDDING_PROVIDER=ollama`; repeated identifiers are downloaded once. Defaults
are `gemma3:4b`, `qwen2.5-coder:7b`, `qwen2.5vl:3b` and
`nomic-embed-text:latest`. Configure `OLLAMA_TEXT_MODEL`, `OLLAMA_CODE_MODEL`,
`OLLAMA_VISION_MODEL` and `OLLAMA_EMBEDDING_MODEL` in `.env` or your shell to
change these. See [local AI models](../deployment/local-ai-models.md) for hardware
requirements. This helper provisions the bundled `http://ollama:11434` endpoint;
for external Ollama, provision its models and start Compose manually.

For GPU resources or isolated ports, pass repeatable override files:

```bash
COMPOSE_PROJECT_NAME=aelira-test ./setup-dev.sh \
  --compose-override ./dev-isolated.yml --compose-override ./dev-gpu.yml
```

Override paths are relative to the caller's directory, applied in order after
the repository's `docker-compose.dev.yml`. Compose service paths remain relative
to the repository. `COMPOSE_PROJECT_NAME` is preserved. The base file has fixed
container names and published ports: to run alongside another dev stack, override
both (the project name alone is insufficient). For example, with a recent Compose
plugin supporting `!reset` and `!override`:

```yaml
# dev-isolated.yml
services:
  postgres:
    container_name: !reset null
    ports: !override ["15432:5432"]
  redis:
    container_name: !reset null
    ports: !override ["16379:6379"]
  api:
    container_name: !reset null
    ports: !override ["18000:8000"]
  ollama:
    container_name: !reset null
    ports: !override ["21434:11434"]
```

A separate `dev-gpu.yml` can set `services.ollama.gpus: all` on a host configured
for Docker GPU access. Preserve service health checks in overrides: setup relies
on them. Use `--no-build` only when you have deliberately prepared current dev
images. This skips building; migrations and readiness checks still run.

The stack hot-reloads `./src`, `./tests` and `./alembic`. Basic verification and
an authenticated AI smoke test (with a bearer API key or access token from your
dev account) are separate from infrastructure readiness:

```bash
curl --fail http://localhost:8000/ready
curl --fail http://localhost:8000/api/ai/health
docker compose -f docker-compose.dev.yml exec -T api pytest
curl --fail -H "Authorization: Bearer $AELIRA_API_TOKEN" http://localhost:8000/api/test-ai
```

The dev Compose file enables mock auth for local convenience. To exercise real
authentication, override `ALLOW_MOCK_AUTH` to `"false"` for API and worker and use
a valid account token; do not rely on a bare unauthenticated test request. AI
health reports configured providers; successful container readiness does not
prove an inference call succeeded. Use your overridden host port when applicable.

For logs and shutdown, reuse the same project name, `-f` override files and
`--profile ollama` selection that setup used:

```bash
docker compose -f docker-compose.dev.yml logs -f api worker
docker compose -f docker-compose.dev.yml --profile ollama down
```

`down` preserves named data volumes; avoid `--volumes` unless you intend to delete
them. Optional veraPDF starts separately with the `verapdf` profile. The dashboard
is **not** included in this stack — run it separately:

```bash
cd dashboard && npm install && npm run dev
```

### 3. Bare-metal Python

Run these commands from the repository root using **Python 3.14** (the version
in `.python-version` and both Dockerfiles). This path runs the API and worker on
host Python; PostgreSQL 16 and Redis 7.4 must be running separately. For local
services with the dev Compose credentials and host ports 5432/6379:

```bash
docker compose -f docker-compose.dev.yml up -d --wait postgres redis
python3.14 -m venv venv
source venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Alternatively provision your own local database/user and Redis, then adjust the
URLs below. Stop any Compose API/worker using that database before applying
migrations; do not run this alongside the full dev stack on the same ports.

Python packages do not install all document-processing tools. Match the native
packages in [`Dockerfile.dev`](../../Dockerfile.dev) for the features you use:

| Feature | Host requirements beyond pip |
|---|---|
| Python builds/rendering | C/C++ build tools, pkg-config, Cairo development libraries |
| PDF/OCR | Tesseract with English language data, Poppler utilities |
| Multimedia | FFmpeg (including ffprobe) |
| Web/code scanning | Node 24, Pa11y 9.0.1 on PATH, Playwright Chromium and its system libraries |
| LaTeX/document conversion | LaTeXML, libxml2/libxslt, ImageMagick, Ghostscript, TeX Live packages and Pandoc listed in the Dockerfile |
| Piper TTS | The `.onnx` voice and matching `.onnx.json` under `data/piper-voices/`, as downloaded in the Dockerfile |

For web/code scanning, install Chromium with `python -m playwright install chromium`
(on supported Linux hosts, `python -m playwright install --with-deps chromium`
also installs OS dependencies). Install Pa11y 9.0.1 using your Node package
manager. Set `PA11Y_CONFIG_PATH` to a local JSON configuration whose
`chromeLaunchConfig.executablePath` points at that installed Chromium. The
repository's `config/pa11y.json` targets a container-only path; installing the
Python requirements alone does not make Pa11y usable. Native package names and
browser paths vary by OS; the Docker path provides the maintained complete
runtime when matching these dependencies is impractical.

Export the following in **every** API, worker, migration and probe shell, with
that shell in the same repository root and the venv activated. These database
credentials are local dev Compose defaults. A production deployment needs its
own credentials. For a new installation, generate the secrets **once** in your
first configured shell (reuse existing values instead if already configured):

```bash
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
# Optional for local scans; required for cloud OAuth/BYOK credential storage.
export TOKEN_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Retain these values privately and export the **same values** in subsequent
shells; do not rerun the generation commands on restart. You can omit the Fernet
export for local document scanning. Cloud jobs initialize the token manager when
they access credentials and need the same valid key as the API.
Then export the rest of the shared configuration:

```bash
export ENV=development
export DATABASE_URL=postgresql://aelira:localdev123@localhost:5432/aelira_dev
export REDIS_URL=redis://localhost:6379/0
export ALLOW_MOCK_AUTH=true
export LLM_PROVIDER=none
export LLM_FALLBACK_PROVIDER=none
export EMBEDDING_PROVIDER=none
export UPLOAD_DIR="$PWD/uploads"
export REMEDIATION_ARTIFACT_DIR="$UPLOAD_DIR/remediation-artifacts"
export REPORT_ARTIFACT_DIR="$UPLOAD_DIR/report-artifacts"
mkdir -p "$UPLOAD_DIR" "$REMEDIATION_ARTIFACT_DIR" "$REPORT_ARTIFACT_DIR"
```

Keep these exports in your own untracked shell configuration if needed. Inspect
any existing `.env` without overwriting it; replace template placeholders before
use. `Settings` reads `.env`, but database, storage and other modules also read
`os.environ` directly: exporting the values ensures all entry points agree.
API and worker need the **same absolute writable storage roots** and database;
their default artifact directories are under `/app/uploads`, which is intended
for containers. Keep any configured encryption key stable for OAuth/BYOK
credentials. Mock auth is for isolated local development.

Apply migrations **before** starting either application process:

```bash
python -m alembic upgrade head
```

Start the API in one terminal and the worker in another, both with the environment
above (including the same JWT secret and, if configured, Fernet key):

```bash
# Terminal 1
python -m uvicorn src.api.main:app --host 127.0.0.1 --reload --port 8000 --no-proxy-headers

# Terminal 2
python -m src.jobs.worker
```

From a third configured terminal, check both services:

```bash
curl --fail http://localhost:8000/ready
python -m src.jobs.healthcheck --mode readiness --json
```

`/health` only proves API liveness. `/ready` checks database/Redis connectivity;
the worker probe checks heartbeat and queue readiness. A successful upload can
remain `PROCESSING` if the worker is absent. Readiness does not prove document
processing or AI inference: upload a small local fixture through the dashboard
or authenticated API and poll its returned scan ID to a terminal state to verify
that workflow. Run the dashboard separately with `VITE_API_URL=http://localhost:8000`
(see its [README](../../dashboard/README.md)). Stop API and worker with Ctrl-C;
keep them stopped while applying subsequent migrations.

### Environment variables that matter

`.env.example` at the repo root is the template for the backend;
`dashboard/.env.example` is the template for the dashboard. The ones you'll
hit immediately:

- `DATABASE_URL`, `REDIS_URL` — required; `Settings` raises if `DATABASE_URL`
  is empty or matches a known-unsafe placeholder (`src/config/settings.py`).
- `LLM_PROVIDER` / `LLM_FALLBACK_PROVIDER` — which AI backend to use
  (`gemini`, `ollama`, `openai`, `anthropic`, `xai`, or `none`) and what to
  fall back to. Both default to `none`.
- `EMBEDDING_PROVIDER` — optional semantic WCAG retrieval (`none` or
  `ollama`); exact rule grounding works when it is `none`.
- The selected provider's key/endpoint: `GEMINI_API_KEY`, `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `XAI_API_KEY`, or `OLLAMA_HOST`.
- `JWT_SECRET` — needed for auth; generate one with
  `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` (from the
  comment in `.env.example`).
- `TOKEN_ENCRYPTION_KEY` — required for cloud OAuth/BYOK credential storage;
  local document scans can omit it. Generate once, retain privately, and share
  the same value across API and worker; generate with
  `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `ALLOW_MOCK_AUTH=true` — dev-only convenience so you don't need a full auth
  flow locally. `Settings` refuses to start with this set in `staging` or
  `production` (`validate_mock_auth` in `settings.py`).

## Running tests

### Backend

```bash
pytest
```

(inside the `api` container, or in your venv with the services reachable).
Config lives in `pytest.ini`: coverage is measured against `src`
(`--cov=src`), and the full-suite gate fails under 68% coverage
(`--cov-fail-under=68`). The `browser`, `e2e`, and `integration` markers
do not themselves skip tests in CI. Individual modules and fixtures can still
require services, optional tools or explicit opt-in. CI checks every observed
skip against the exact-node policy and requires critical tests to pass.
See [coverage and skip evidence](../testing/coverage-and-skips.md) for the
measured baseline, dispositions and reproducible commands. Do not enable all
legacy integration suites indiscriminately: some target obsolete contracts.

To run one file:

```bash
pytest tests/test_severity_determinism.py -v --no-cov
```

### Dashboard

From `dashboard/`:

```bash
npm run lint          # eslint .
npx tsc --noEmit       # type check
npm run build          # vite build
npm run test:unit      # node --test tests/unit/*.test.js
npm run test           # playwright test (e2e)
```

This is the same sequence CI runs (`.github/workflows/ci.yml`), aside from
`npm run test` (Playwright) which isn't part of the `dashboard` CI job.

## Where to start contributing

- Read `CONTRIBUTING.md` for the workflow (branching, commit message format,
  PR process) and code style expectations.
- Browse open issues on GitHub — issues labeled `good first issue` are
  meant as a starting point (linked from `CONTRIBUTING.md`).
- Backend lint/format gates you'll need to pass before a PR is mergeable
  (`.github/workflows/ci.yml`): `ruff check .` and
  `black --check src/ tests/ scripts/` (black is pinned to `26.5.1` in
  `requirements-dev.txt`; ruff's lint scope is deliberately pinned to the rule
  families `E4`, `E7`, `E9`, `F` in `ruff.toml` rather than ruff's newer,
  much wider defaults — see the comment at the top of that file for why).
- Dashboard gates: `npm run lint`, `npx tsc --noEmit`, `npm run build`.
