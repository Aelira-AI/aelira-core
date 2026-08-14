# Aelira Core

**Accessibility remediation for course content. It returns fixed files, not a list of problems.**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/Python-3.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)

Most accessibility tools tell you a PDF has no tags, an image has no alt text, and a table has no headers. Someone still has to open the file and fix it. Aelira does the fixing: you give it a document, it gives you back a remediated one, with a report of what changed and why.

It is built for institutions working toward WCAG 2.1 AA, including US public entities under the DOJ ADA Title II rule (**26 April 2027** for jurisdictions of 50,000+, **26 April 2028** for smaller entities).

---

## Try it in one command

```bash
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core
docker compose -f docker-compose.quickstart.yml up -d
```

No `.env` file, no configuration. When it comes up:

- API and interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

That gets you scanning immediately. AI-generated fixes need a model, which is the one thing you have to choose: either set `GEMINI_API_KEY` for cloud inference, or run models locally with `--profile ollama` and keep every document on your own hardware. Nothing is sent anywhere you did not configure.

## What it handles

| Content | What it does |
|---|---|
| **PDF** | Tags structure, fixes reading order, adds alt text, OCRs scans, repairs tables |
| **Word, PowerPoint, Excel** | Heading structure, alt text, contrast, table headers, slide reading order |
| **LaTeX** | Converts equations to MathML so screen readers can read the maths |
| **Web pages** | axe-core and Pa11y detection, with generated code fixes |
| **Video and audio** | Transcription and WebVTT captions |
| **Images** | Context-aware alt text, not filename echoes |

It reads course content directly from **Canvas, Blackboard, Moodle and Brightspace** over LTI 1.3 and their APIs, and from **Google Drive** and **Microsoft 365**, so faculty do not have to download and re-upload anything.

## Severity is computed, not generated

Aelira uses AI to write explanations. It does **not** use AI to decide how serious a violation is. Severity comes from [`src/ai/severity_rules.py`](src/ai/severity_rules.py), a plain function of the rule that fired and the scanner's impact rating. It performs no I/O, holds no state, and calls no model.

That means the same file produces the same severities on every run, including when your AI provider is rate-limiting or down. Language models sample, so anything that asks one to rate severity will disagree with itself eventually; setting `temperature=0` does not fix that.

If your compliance reports have to be reproducible, that distinction matters more than any feature list. There is a test that fails if a single severity varies across repeated runs:

```bash
pytest tests/test_severity_determinism.py
```

Explanations are grounded too. A knowledge base of 112 WCAG guidelines is retrieved against, so a cited criterion is one that was looked up rather than recalled:

```bash
python scripts/seed_wcag_guidelines.py
python scripts/generate_wcag_embeddings.py
```

## Self-hosting

Aelira Core is designed to run entirely on your own infrastructure. It needs PostgreSQL, Redis, and optionally Ollama for local inference.

Two settings point the system at your deployment, and everything user-facing derives from them:

```bash
PUBLIC_API_URL=https://accessibility-api.your-university.edu
PUBLIC_DASHBOARD_URL=https://accessibility.your-university.edu
CORS_ORIGINS=https://accessibility.your-university.edu
```

Full configuration is documented in [`.env.example`](.env.example), and the deployment guide is in [`docs/`](docs/).

**A note on data.** With Ollama, documents never leave your servers: no cloud API, no third-party processing, nothing to put through a vendor review — the right deployment for anything covered by FERPA.

## Architecture

```
src/
  education/     document processors: PDF, Office, LaTeX, web, multimedia
  ai/            provider abstraction, WCAG knowledge base, severity rules
  integrations/  Canvas, Blackboard, Moodle, Brightspace, Google, Microsoft
  api/           FastAPI routes (335 endpoints)
  auth/          magic link, OAuth, API keys, sessions
dashboard/       React 19 + Vite admin interface
alembic/         database migrations
tests/           pytest suite
```

| Layer | Stack |
|---|---|
| API | FastAPI, Python 3.14, SQLAlchemy 2.0 |
| Storage | PostgreSQL 16, Redis |
| Dashboard | React 19, Vite, TypeScript, Tailwind |
| AI | Gemini or Ollama, your choice |
| PDF | pikepdf, PyMuPDF, pdfplumber, OCRmyPDF |
| Office | python-docx, python-pptx, openpyxl |
| Web | Playwright, axe-core, Pa11y |
| Media | faster-whisper, PySceneDetect |

## Development

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose exec api alembic upgrade head
docker compose exec api pytest
```

The dashboard runs separately with `cd dashboard && npm install && npm run dev`.

## What is not here

Not in this repository:

- **Billing, CRM, campaign and helpdesk integrations.** They run the commercial service and have nothing to do with remediation.
- **Hosted infrastructure and support** are the commercial offering. The engine is here and complete; what you buy is somebody else running it.

If you self-host and never pay us anything, the tool still works. That is the point of the licence.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for expectations. Security reports go to the process in [SECURITY.md](SECURITY.md), not to the public issue tracker.

## Licence and branding

[AGPL-3.0](LICENSE). You can run it, modify it, and self-host it, including inside an institution. If you offer it to others as a network service, your modifications have to be published under the same licence.

The code is AGPL. The **name and logos are not** — see [BRANDING.md](BRANDING.md). You can also replace the branding entirely with environment variables rather than forking, which is the supported path for an institution that wants this under its own name.
