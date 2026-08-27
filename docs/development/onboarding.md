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
`upload_job.py`, `email_alert_job.py`, `account_deletion_job.py`. This is
what drives asynchronous cloud-integration work; document uploads via the
scan endpoints below use FastAPI `BackgroundTasks` directly rather than this
queue.

### Dashboard (`dashboard/`)

React 19 + Vite + TypeScript + Tailwind, source under `dashboard/src/`
(`api/`, `pages/`, `components/`, `context/`, `hooks/`). Talks to the backend
over the URL in `VITE_API_URL` (see `dashboard/.env.example`).

## Life of a scan

Tracing the actual code path for a single PDF, since that's the clearest
example (`src/api/education/scan_routes.py`):

1. **Upload** — `POST /api/education/pdf/scan` (`scan_pdf()` in
   `scan_routes.py`) validates the file, checks quota
   (`check_scan_quota()`), creates a `Scan` row with `status=PROCESSING`,
   saves the file (`save_uploaded_file()`), and returns `scan_id`
   immediately — processing happens in a background task
   (`process_pdf_background()`), not inline.
2. **Scan** — the background task instantiates `PDFProcessor` (from
   `src/education/pdf_processor.py`) and calls `process_pdf()`, which
   detects accessibility issues (missing tags, reading order, alt text,
   contrast, tables) and reports progress back through a callback that
   updates `Scan.progress` in the database, so the client can poll
   `GET /api/education/scans/{scan_id}/progress`.
3. **Issues with computed severity** — each detected issue carries a
   `severity` field. For PDF/DOCX/XLSX this comes from
   `src/ai/severity_rules.py` as described above; for axe-core-based web/code
   scans it comes from axe's own impact rating through the same module's
   `SEVERITY_BY_IMPACT` mapping. The result is written to a `ScanResult` row
   (`compliance_score`, `critical_issues`/`high_issues`/`medium_issues`/`low_issues`,
   the raw `issues` list, `structure`, `html_output`).
4. **Remediation** — `POST /api/education/remediate/{scan_id}`
   (`remediate_scan()` in `remediation_routes.py`) loads the stored issues
   and file, and routes to the matching remediator
   (`PdfRemediator`/`DocxRemediator`/`PptxRemediator`/`XlsxRemediator`/etc.
   via `AutoRemediator`) to fix as many issues automatically as possible,
   producing a remediated file plus a list of what still needs a human.
5. **Report** — `GET /api/education/scans/{scan_id}/report` and
   `GET /api/education/scans/{scan_id}/html` return the scan's findings;
   `GET /api/education/compliance/{department_id}/report/pdf`
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

```bash
cp .env.example .env
# edit .env — select any supported LLM_PROVIDER and supply its key/endpoint,
# or choose ollama and use --profile ollama for local inference
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec api alembic upgrade head
docker compose -f docker-compose.dev.yml exec api pytest
```

This starts Postgres (`pgvector/pgvector:0.8.1-pg16`, with the pgvector
extension available even though the WCAG knowledge base doesn't currently
use it — see above), Redis (`redis:7.4-alpine`), the API
(built from `Dockerfile.dev`, hot-reloading `./src`, `./tests`, and
`./alembic` into the container), and optionally Ollama and veraPDF via
`--profile ollama` / `--profile verapdf`. The dashboard is **not** part of
this compose file — run it separately:

```bash
cd dashboard && npm install && npm run dev
```

### 3. Bare-metal Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --port 8000
```

You'll still need Postgres and Redis reachable at whatever `DATABASE_URL`
and `REDIS_URL` you set — either point at the Dockerized ones from option 2
or run them yourself.

The runtime is **Python 3.14** (`.python-version`, both Dockerfiles, and the
CI workflow all pin 3.14).

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
- `TOKEN_ENCRYPTION_KEY` — needed if you're touching any OAuth cloud
  integration (Google/Microsoft/Canvas); generate with
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
(`--cov=src`), and the gate fails under 25% coverage
(`--cov-fail-under=25`). Markers worth knowing about (`pytest.ini`,
enforced in `tests/conftest.py`): `browser`, `e2e`, and `integration` tests
are automatically skipped when `CI=true` or `GITHUB_ACTIONS=true`, because
they need a running dashboard, external services, or the full environment —
run those manually against a real stack when you're touching that code.

To run one file:

```bash
pytest tests/test_severity_determinism.py -v
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
  `black --check src/ tests/ scripts/` (black is pinned to `26.3.1` in
  `requirements.txt`; ruff's lint scope is deliberately pinned to the rule
  families `E4`, `E7`, `E9`, `F` in `ruff.toml` rather than ruff's newer,
  much wider defaults — see the comment at the top of that file for why).
- Dashboard gates: `npm run lint`, `npx tsc --noEmit`, `npm run build`.
