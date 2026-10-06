# Aelira Core

**Accessibility scanning and supported remediation for course content, with saved-file evidence and human review.**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![CI](https://github.com/Aelira-AI/aelira-core/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Aelira-AI/aelira-core/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Aelira-AI/aelira-core?display_name=tag)](https://github.com/Aelira-AI/aelira-core/releases/latest)

[Quickstart](#try-it-in-one-command) · [Documentation](docs/) · [Discussions](https://github.com/Aelira-AI/aelira-core/discussions) · [Contribute](CONTRIBUTING.md) · [Get help](SUPPORT.md)

> **Status: 0.9.11 beta.** Scanning and remediation remain under active validation. Automated scores describe scanner findings, not accessibility conformance. Some documents require manual work and cannot produce a downloadable remediated artifact. LMS integration maturity varies by platform (see the [integration status table](#lms-integration-status) below). Cloud and uploaded scans, remediation, upload, synchronization, and reconciliation jobs use a bounded, multi-worker durable queue. See the [0.9.11 corrective release notes](docs/releases/v0.9.11.md) for workflow fixes and verification limits.

Aelira can apply eligible fixes and return a saved candidate with a report of recorded outcomes. Supported changes differ by format and source structure. Unsupported or ambiguous cases remain unresolved; some cannot produce a downloadable remediated artifact. Review the actual saved file before publishing it.

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

For document work, scan an original file, apply supported changes, and review any saved candidate and unresolved findings. The public core treats **documents**, **LMS**, **web**, and **media** as four equal product pillars, each with its own implementation and evidence boundaries.

| Pillar | Scope | Start here |
|---|---|---|
| **Documents** | PDF, DOCX, PPTX, XLSX, and LaTeX scanning, bounded remediation, review artifacts | [Document remediation hub](docs/document-remediation/README.md) |
| **LMS** | Course discovery, scanning, remediation policy, and provider-specific write-back | [LMS integration status](#lms-integration-status) |
| **Web** | Browser-based accessibility detection and code remediation | [`src/education/web_scanner.py`](src/education/web_scanner.py) |
| **Media** | Audio/video transcription, captions, and related review outputs | [`src/education/multimedia_processor.py`](src/education/multimedia_processor.py) |

## What it handles

| Content | What it does |
|---|---|
| **PDF** | Scans text/OCR and structure; applies bounded metadata, tag, bookmark, table, and alt-text fixes where the file exposes a safe target. Eligible image-only pages are OCR'd in a private working copy and the searchable text is preserved in the delivered PDF; ambiguous or unsupported cases fail closed. |
| **Word, PowerPoint, Excel** | Format-specific structure, alternative-text, contrast, table, slide, and workbook checks with partial original-format remediation |
| **LaTeX** | Remediates and returns `.tex` source directly, converts supported equations to MathML/ARIA descriptions, and can optionally produce PDF/HTML |
| **General STEM visuals** | Uses source-bound, specialist-specific contracts for printed, multi-equation, vector, and handwritten mathematics; chemical formulas and molecular structures; commutative diagrams; and mixed visuals. Saved-file verification and exact-candidate human approval remain mandatory. |
| **Web pages** | axe-core and Pa11y detection, with generated code fixes |
| **Video and audio** | Transcription and WebVTT captions |
| **Images** | Context-aware alt text, not filename echoes |

MathML is one stage of the LaTeX pipeline; the source remains first-class. Source-level remediation can improve accessibility metadata and language, figures, tables, equations, and links, depending on the issues found. With AI configured, figure descriptions use the issue, location, and original LaTeX context rather than the filename alone, with a filename-based fallback when richer context is unavailable. Capabilities, dependencies, evidence level, and review limits for every document format are in the [document remediation hub](docs/document-remediation/README.md); the [General STEM guide](docs/document-remediation/general-stem.md) documents the visual trust pipeline and its limits.

Connectors can read course content from supported LMS deployments, **Google Drive** and **Microsoft 365**. Permissions, supported operations and verification maturity differ; review and authorized writeback are separate steps.

### LMS integration status

Connectors are at different stages of verification. We label them honestly rather than imply parity — check your platform before you depend on it:

| LMS | Connection | Status |
|---|---|---|
| **Canvas** | LTI 1.3 + REST API | **Integration verified** — maintainer production integration testing; institution-specific user-journey acceptance remains necessary |
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

Export `DATABASE_URL` before running these repair commands. The embedding
repair uses `OLLAMA_HOST` and `OLLAMA_EMBEDDING_MODEL`; it preserves existing
vectors and returns nonzero if rows fail or are busy. Downloading a missing
model requires an explicit `--pull` option.

## Self-hosting

Aelira Core is designed to run entirely on your own infrastructure. It needs PostgreSQL, Redis, and optionally Ollama for local inference.

Three settings point the system at your deployment, and everything user-facing derives from them:

```bash
PUBLIC_API_URL=https://accessibility-api.your-university.edu
PUBLIC_DASHBOARD_URL=https://accessibility.your-university.edu
CORS_ORIGINS=https://accessibility.your-university.edu
```

The production compose file runs the full stack (API, dashboard, PostgreSQL, Redis, optional Ollama) from the published images:

```bash
cp .env.example .env   # replace the production secret placeholders
docker compose -f docker-compose.prod.yml up -d
```

Production Compose supplies service-local PostgreSQL and Redis URLs and
`ENV=production` unless you explicitly override them. The commented localhost
examples in `.env.example` are for running the API directly on the host.

Full configuration is documented in [`.env.example`](.env.example) — reconciled against every variable the code reads — and the deployment guide is in [`docs/`](docs/).

**A note on data.** Local Ollama inference can keep AI processing on institution-controlled infrastructure when local providers are deliberately configured. Review fallback providers, storage, logs, backups, network egress and access controls for the actual deployment. Local inference alone does not establish FERPA compliance or replace an institutional privacy review.

**A note on analytics.** The dashboard ships with an optional, off-by-default [Umami](https://umami.is/) integration (Umami is open-source, self-hostable web analytics). It activates when you configure `VITE_UMAMI_WEBSITE_ID` and `VITE_UMAMI_URL`, and loads after analytics consent. Review the configured destination and deployment's network behavior before making a telemetry or data-residency claim.

## Architecture

```
src/
  education/     document processors: PDF, Office, LaTeX, web, multimedia
  ai/            provider abstraction, WCAG knowledge base, severity rules
  integrations/  Canvas, Blackboard, Moodle, Brightspace, Google, Microsoft
  api/           FastAPI routes (~330 endpoints)
  auth/          magic link, OAuth, API keys, sessions
dashboard/       React 19 + Vite admin interface
cli/             oclif command-line client (TypeScript, Node 22+)
alembic/         database migrations
tests/           pytest suite
```

| Layer | Stack |
|---|---|
| API | FastAPI, Python 3.12+, SQLAlchemy 2.1 |
| Storage | PostgreSQL 16, Redis |
| Dashboard | React 19, Vite, TypeScript, Tailwind |
| CLI | oclif, TypeScript, Node 22+ |
| AI | Bring your own: Gemini, OpenAI, Anthropic, xAI, any OpenAI-compatible endpoint, or fully local via Ollama |
| PDF | pikepdf, PyMuPDF, pdfplumber, OCRmyPDF |
| Office | python-docx, python-pptx, openpyxl |
| Web | Playwright, axe-core, Pa11y |
| Media | faster-whisper, PySceneDetect, FFmpeg |
| OCR & print | Tesseract (via OCRmyPDF), Ghostscript, qpdf |
| LaTeX & conversion | TeX Live (pdflatex/LuaTeX), LaTeXML, Pandoc |

The full annotated dependency inventory — every major dependency and what it does — is in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md). Production dependencies are pinned in [requirements.txt](requirements.txt); contributors install the complete runtime and tooling set through [requirements-dev.txt](requirements-dev.txt). Local AI model recommendations and hardware tiers are in [docs/deployment/local-ai-models.md](docs/deployment/local-ai-models.md). Administrators should also read the [LMS AI policy, readiness, egress, and revocation guide](docs/deployment/lms-ai-policy.md).

## Development

```bash
./setup-dev.sh  # Docker Compose plugin + jq; builds, migrates, and waits for readiness
docker compose -f docker-compose.dev.yml exec api pytest
```

AI is disabled by default. To choose local inference, run
`LLM_PROVIDER=ollama ./setup-dev.sh`; add `EMBEDDING_PROVIDER=ollama` only
when semantic retrieval is wanted. Existing `.env` values are preserved.
Read the [development setup guide](docs/development/onboarding.md#2-full-dev-stack-docker-composedevyml)
for credentials, custom models, Compose overrides, and authenticated smoke tests.
The dashboard runs separately with `cd dashboard && npm install && npm run dev`.

## Command line

`aelira` scans and remediates content from the terminal against any Aelira Core API — the quickstart above, a self-hosted deployment, or your own.

```bash
npm install -g @aelira/cli
aelira --help
```

It lives in [`cli/`](cli/) if you prefer to run it from source (`npm ci && npm run build && ./bin/run.js`).

Network commands resolve the API endpoint consistently. The precedence order is:

1. An explicit `--api-url`
2. `AELIRA_API_URL`
3. The active profile's `apiUrl`, set with `aelira config set api-url <url>`
4. `http://localhost:8000`

Configure a self-hosted deployment once, or override it for a single command:

```bash
aelira config set api-url https://api.example.edu
aelira report analytics
aelira report analytics --api-url http://localhost:8000
```

## What is not here

Not in this repository:

- **Billing, CRM, campaign and helpdesk integrations.** They run the commercial service and have nothing to do with remediation.
- **Hosted infrastructure and support** are the commercial offering. The self-hosted core has no paid feature tier; its current beta scope and validation limits apply whether you self-host or use managed infrastructure.

If you self-host and never pay us anything, the tool still works. That is the point of the licence.

## Built on

Aelira Core stands on excellent open-source tools, and it is worth naming the ones doing the heavy lifting: [axe-core](https://github.com/dequelabs/axe-core) and [Pa11y](https://pa11y.org/) for web accessibility rules, [Tesseract](https://github.com/tesseract-ocr/tesseract) and [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) for OCR, [pikepdf](https://github.com/pikepdf/pikepdf)/[PyMuPDF](https://github.com/pymupdf/PyMuPDF)/qpdf/Ghostscript for PDF surgery, [LaTeXML](https://math.nist.gov/~BMiller/LaTeXML/) and TeX Live for maths accessibility, [Pandoc](https://pandoc.org/) for format conversion, [FFmpeg](https://ffmpeg.org/) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for captioning, and [Playwright](https://playwright.dev/) for browser automation. Their licences ship with their packages; this project would not exist without them.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for expectations. Questions, setup help, and feature ideas belong in [Discussions](https://github.com/Aelira-AI/aelira-core/discussions); reproducible bugs go to the issue tracker. Security reports go to the process in [SECURITY.md](SECURITY.md), not to the public issue tracker.

Technical maintenance is led by [RD (Reg) Crampton](https://github.com/rdcrampton), with the [Aelira-AI-Dev](https://github.com/Aelira-AI-Dev) project administration account. [Governance](GOVERNANCE.md) describes maintainer and contributor responsibilities, review and access. Browse [good first issues](https://github.com/Aelira-AI/aelira-core/labels/good%20first%20issue) or [help wanted](https://github.com/Aelira-AI/aelira-core/labels/help%20wanted) to find work to discuss.

Follow Aelira: [Website](https://aelira.ai) · [LinkedIn](https://www.linkedin.com/company/aelira-ai) · [X](https://x.com/Aelira_dot_AI) · [Release announcements](https://github.com/Aelira-AI/aelira-core/discussions/categories/announcements)

## Licence and branding

[AGPL-3.0](LICENSE). You can run it, modify it, and self-host it, including inside an institution. If you offer it to others as a network service, your modifications have to be published under the same licence.

One deliberate exception: the command-line client in [`cli/`](cli/) is [MIT-licensed](cli/LICENSE), so institutions and vendors can embed or script against it without AGPL obligations. The engine the CLI talks to remains AGPL.

The code is AGPL. The **name and logos are not** — see [BRANDING.md](BRANDING.md). You can also replace the branding entirely with environment variables rather than forking, which is the supported path for an institution that wants this under its own name.
