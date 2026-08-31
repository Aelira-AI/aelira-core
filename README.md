# Aelira Core

**Accessibility remediation for course content. It returns fixed files, not a list of problems.**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/Python-3.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/Dashboard-React_19-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6.svg)](https://www.typescriptlang.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Deploy-Docker_Compose-2496ED.svg)](docker-compose.quickstart.yml)

> **Status: 0.9.7 beta.** The engine — scanning, deterministic scoring, remediation — is complete and tested (5,000+ backend tests). LMS integration maturity varies by platform: Canvas is production-verified, the others range from beta to untested (see the [integration status table](#lms-integration-status) below). Pre-1.0 means we're still hardening operational edges. Cloud and uploaded scans, remediation, upload, synchronization, and reconciliation jobs use a bounded, multi-worker durable queue. Known work is tracked openly in the issues.

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

That gets you scanning immediately, with AI disabled. AI-generated fixes need a
provider, and open-core does not choose one for you: set `LLM_PROVIDER` to
`gemini`, `openai`, `anthropic`, `xai`, or `ollama`, then supply that provider's
key or local endpoint. Point `openai` at any OpenAI-compatible endpoint, or run
fully local AI with:

```bash
LLM_PROVIDER=ollama EMBEDDING_PROVIDER=ollama docker compose -f docker-compose.quickstart.yml --profile ollama up -d
```

Fallback is opt-in through `LLM_FALLBACK_PROVIDER`. Nothing is sent to an AI
service you did not select.

## Four equal product pillars

For document work, upload a file and get back a remediated file rather than only a problem list. The public core treats **documents**, **LMS**, **web**, and **media** as four equal product pillars, each with its own implementation and evidence boundaries.

| Pillar | Scope | Start here |
|---|---|---|
| **Documents** | PDF, DOCX, PPTX, XLSX, and LaTeX scanning, bounded remediation, review artifacts | [Document remediation hub](docs/document-remediation/README.md) |
| **LMS** | Course discovery, scanning, remediation policy, and provider-specific write-back | [LMS integration status](#lms-integration-status) |
| **Web** | Browser-based accessibility detection and code remediation | [`src/education/web_scanner.py`](src/education/web_scanner.py) |
| **Media** | Audio/video transcription, captions, and related review outputs | [`src/education/multimedia_processor.py`](src/education/multimedia_processor.py) |

## What it handles

| Content | What it does |
|---|---|
| **PDF** | Scans text/OCR and structure; applies bounded metadata, tag, bookmark, table, and alt-text fixes where the file exposes a safe target. On `main`, eligible image-only pages are OCR'd in a private working copy and the searchable text is preserved in the delivered PDF; ambiguous or unsupported cases fail closed. |
| **Word, PowerPoint, Excel** | Format-specific structure, alternative-text, contrast, table, slide, and workbook checks with partial original-format remediation |
| **LaTeX** | Remediates and returns `.tex` source directly, converts supported equations to MathML/ARIA descriptions, and can optionally produce PDF/HTML |
| **Web pages** | axe-core and Pa11y detection, with generated code fixes |
| **Video and audio** | Transcription and WebVTT captions |
| **Images** | Context-aware alt text, not filename echoes |

MathML is one stage of the LaTeX pipeline; the source remains first-class. Source-level remediation can improve accessibility metadata and language, figures, tables, equations, and links, depending on the issues found. With AI configured, figure descriptions use the issue, location, and original LaTeX context rather than the filename alone, with a filename-based fallback when richer context is unavailable. Capabilities, dependencies, evidence level, and review limits for every document format are in the [document remediation hub](docs/document-remediation/README.md).

It reads course content directly from your LMS, plus **Google Drive** and **Microsoft 365**, so faculty do not have to download and re-upload anything.

### LMS integration status

Connectors are at different stages of verification. We label them honestly rather than imply parity — check your platform before you depend on it:

| LMS | Connection | Status |
|---|---|---|
| **Canvas** | LTI 1.3 + REST API | **Production-verified** — tested end to end |
| **Brightspace (D2L)** | LTI 1.3 + API | **Beta** — built and tested against a D2L developer instance, not recently re-verified |
| **Blackboard** | LTI 1.3 + API | **Experimental** — implemented, not yet tested end to end |
| **Moodle** | REST API | **Experimental** — implemented, not yet tested end to end |

If you run one of the experimental integrations, we would value the feedback — open an issue with what you find.

## Severity is computed, not generated

Aelira uses AI to write explanations. It does **not** use AI to decide how serious a violation is. Severity comes from [`src/ai/severity_rules.py`](src/ai/severity_rules.py), a plain function of the rule that fired and the scanner's impact rating. It performs no I/O, holds no state, and calls no model.

That means the same file produces the same severities on every run, including when your AI provider is rate-limiting or down. Language models sample, so anything that asks one to rate severity will disagree with itself eventually; setting `temperature=0` does not fix that.

If your compliance reports have to be reproducible, that distinction matters more than any feature list. There is a test that fails if a single severity varies across repeated runs:

```bash
pytest tests/test_severity_determinism.py
```

Explanations are grounded too. On first startup, Aelira seeds its bundled WCAG
corpus. Known scanner rule IDs use exact corpus lookup, so this grounding works
with every generation provider and needs no embedding service. Optional
free-text semantic search is enabled separately with
`EMBEDDING_PROVIDER=ollama`; only then does startup generate missing Ollama
embeddings. The explicit scripts remain available for operator repair:

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

The production compose file runs the full stack (API, dashboard, PostgreSQL, Redis, optional Ollama) from the published images:

```bash
cp .env.example .env   # set the REQUIRED section
docker compose -f docker-compose.prod.yml up -d
```

Full configuration is documented in [`.env.example`](.env.example) — reconciled against every variable the code reads — and the deployment guide is in [`docs/`](docs/).

**A note on data.** With Ollama, documents never leave your servers: no cloud API, no third-party processing, nothing to put through a vendor review — the right deployment for anything covered by FERPA.

**A note on analytics.** The dashboard ships with an optional, off-by-default [Umami](https://umami.is/) integration (Umami is open-source, self-hostable web analytics). It only activates if you set `VITE_UMAMI_WEBSITE_ID` and `VITE_UMAMI_URL` to point at **your own** Umami instance, and even then it loads only after the user accepts the analytics cookie consent. Nothing is hardcoded, and no usage data is ever sent to the Aelira project — there is no telemetry or phone-home anywhere in this codebase.

## Architecture

```
src/
  education/     document processors: PDF, Office, LaTeX, web, multimedia
  ai/            provider abstraction, WCAG knowledge base, severity rules
  integrations/  Canvas, Blackboard, Moodle, Brightspace, Google, Microsoft
  api/           FastAPI routes (~330 endpoints)
  auth/          magic link, OAuth, API keys, sessions
dashboard/       React 19 + Vite admin interface
cli/             oclif command-line client (TypeScript, Node 20+)
alembic/         database migrations
tests/           pytest suite
```

| Layer | Stack |
|---|---|
| API | FastAPI, Python 3.14, SQLAlchemy 2.0 |
| Storage | PostgreSQL 16, Redis |
| Dashboard | React 19, Vite, TypeScript, Tailwind |
| CLI | oclif, TypeScript, Node 20+ |
| AI | Bring your own: Gemini, OpenAI, Anthropic, xAI, any OpenAI-compatible endpoint, or fully local via Ollama |
| PDF | pikepdf, PyMuPDF, pdfplumber, OCRmyPDF |
| Office | python-docx, python-pptx, openpyxl |
| Web | Playwright, axe-core, Pa11y |
| Media | faster-whisper, PySceneDetect, FFmpeg |
| OCR & print | Tesseract (via OCRmyPDF), Ghostscript, qpdf |
| LaTeX & conversion | TeX Live (pdflatex/LuaTeX), LaTeXML, Pandoc |

The full annotated dependency inventory — every major dependency and what it does — is in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md); the pinned set is [requirements.txt](requirements.txt). Local AI model recommendations and hardware tiers are in [docs/deployment/local-ai-models.md](docs/deployment/local-ai-models.md). Administrators should also read the [LMS AI policy, readiness, egress, and revocation guide](docs/deployment/lms-ai-policy.md).

## Development

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec api alembic upgrade head
docker compose -f docker-compose.dev.yml exec api pytest
```

The dashboard runs separately with `cd dashboard && npm install && npm run dev`.

## Command line

`aelira` scans and remediates content from the terminal against any Aelira Core API — the quickstart above, a self-hosted deployment, or your own.

```bash
npm install -g @aelira/cli
aelira --help
```

It lives in [`cli/`](cli/) if you prefer to run it from source (`npm ci && npm run build && ./bin/run.js`).

The current document scan and remediation commands take `--api-url` with a command-local default of `http://localhost:8000`, matching the quickstart. Although `aelira config set api-url <url>` stores a profile value, these command sources currently use their own flag default rather than that stored value. Pass `--api-url` on each invocation for another deployment:

```bash
./bin/run.js report analytics --api-url http://localhost:8000
```

## What is not here

Not in this repository:

- **Billing, CRM, campaign and helpdesk integrations.** They run the commercial service and have nothing to do with remediation.
- **Hosted infrastructure and support** are the commercial offering. The engine is here and complete; what you buy is somebody else running it.

If you self-host and never pay us anything, the tool still works. That is the point of the licence.

## Built on

Aelira Core stands on excellent open-source tools, and it is worth naming the ones doing the heavy lifting: [axe-core](https://github.com/dequelabs/axe-core) and [Pa11y](https://pa11y.org/) for web accessibility rules, [Tesseract](https://github.com/tesseract-ocr/tesseract) and [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) for OCR, [pikepdf](https://github.com/pikepdf/pikepdf)/[PyMuPDF](https://github.com/pymupdf/PyMuPDF)/qpdf/Ghostscript for PDF surgery, [LaTeXML](https://math.nist.gov/~BMiller/LaTeXML/) and TeX Live for maths accessibility, [Pandoc](https://pandoc.org/) for format conversion, [FFmpeg](https://ffmpeg.org/) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for captioning, and [Playwright](https://playwright.dev/) for browser automation. Their licences ship with their packages; this project would not exist without them.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for expectations. Questions, setup help, and feature ideas belong in [Discussions](https://github.com/Aelira-AI/aelira-core/discussions); reproducible bugs go to the issue tracker. Security reports go to the process in [SECURITY.md](SECURITY.md), not to the public issue tracker.

## Licence and branding

[AGPL-3.0](LICENSE). You can run it, modify it, and self-host it, including inside an institution. If you offer it to others as a network service, your modifications have to be published under the same licence.

One deliberate exception: the command-line client in [`cli/`](cli/) is [MIT-licensed](cli/LICENSE), so institutions and vendors can embed or script against it without AGPL obligations. The engine the CLI talks to remains AGPL.

The code is AGPL. The **name and logos are not** — see [BRANDING.md](BRANDING.md). You can also replace the branding entirely with environment variables rather than forking, which is the supported path for an institution that wants this under its own name.
