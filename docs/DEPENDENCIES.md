# Dependencies

What Aelira Core is built from, and what each major dependency does. The
authoritative pinned set is [`requirements.txt`](../requirements.txt) (Python,
direct and transitive, one lockfile), [`dashboard/package.json`](../dashboard/package.json)
(dashboard), and [`cli/package.json`](../cli/package.json) (CLI). This page is
the human-readable map; the lockfiles are the truth.

## Backend (Python)

### Web framework and API

| Dependency | Role |
|---|---|
| `fastapi` | The API server — 335 routes under `src/api/` |
| `uvicorn` | ASGI server |
| `pydantic` / `pydantic-settings` | Request/response models and typed settings |
| `starlette` | ASGI toolkit underneath FastAPI (middleware, responses) |
| `httpx` | Outbound HTTP (AI providers, LMS APIs, webhooks) |

### Data layer

| Dependency | Role |
|---|---|
| `SQLAlchemy` 2.0 | ORM — models in `src/db/models.py` |
| `alembic` | Database migrations (`alembic/versions/`) |
| `psycopg2-binary` / `asyncpg` | PostgreSQL drivers (sync and async) |
| `redis` | Rate limiting, job coordination, caching |

### Authentication and security

| Dependency | Role |
|---|---|
| `PyJWT` / `jwcrypto` | JWT session tokens and LTI 1.3 message signing |
| `bcrypt` | Refresh-token and confirmation-code hashing |
| `cryptography` | Fernet encryption for stored BYOK provider keys |
| `oletools` / `msoffcrypto-tool` | Malicious-document screening on upload |

### Document processing — PDF

| Dependency | Role |
|---|---|
| `pikepdf` | Structure-level PDF editing (tags, metadata) |
| `PyMuPDF` | Rendering, text extraction, page analysis |
| `pdfplumber` | Table and layout extraction |
| `pypdf` | General PDF manipulation |
| `ocrmypdf` | OCR layer for scanned documents (drives Tesseract) |
| `img2pdf` / `reportlab` | Image-to-PDF conversion and PDF report generation |

### Document processing — Office and LaTeX

| Dependency | Role |
|---|---|
| `python-docx` / `python-pptx` / `openpyxl` | Word, PowerPoint, Excel scan + remediation |
| `latex2mathml` | LaTeX equations to accessible MathML |
| `Pillow` | Image analysis and manipulation |
| `matplotlib` | Optional: report charts (degrades gracefully when absent) |

### Web and multimedia

| Dependency | Role |
|---|---|
| `playwright` / `axe-playwright-python` | Real-browser scans with the axe-core WCAG engine |
| `beautifulsoup4` / `lxml` | HTML parsing for web remediation |
| `faster-whisper` / `ctranslate2` | Speech-to-text for captions and transcripts |
| `av` / `scenedetect` | Video decoding and flashing/scene analysis |
| `piper-tts` | Text-to-speech generation (fully local) |

### AI providers

Cloud providers (Gemini, OpenAI and OpenAI-compatible endpoints, Anthropic,
xAI) are implemented directly over `httpx` with bring-your-own keys — no
vendor SDKs to keep the dependency surface small.

| Dependency | Role |
|---|---|
| `ollama` | Client for fully local inference |
| `onnxruntime` | Local model execution for CPU-only helpers |

### Integrations

| Dependency | Role |
|---|---|
| `google-api-python-client` / `google-auth` | Google Workspace (Drive, Docs) |
| Microsoft 365 | OAuth + Graph implemented directly over `httpx` (no SDK) |
| Canvas / Blackboard / Moodle / Brightspace | REST + LTI 1.3, implemented directly over `httpx` (no vendor SDK) |

### Email

| Dependency | Role |
|---|---|
| `smtplib` (stdlib) + `httpx` | Transactional email via any SMTP host or SendGrid API |

### Testing and tooling

| Dependency | Role |
|---|---|
| `pytest` (+ `pytest-asyncio`, `pytest-cov`) | Test suite (`tests/`) |
| `locust` | Load testing (`tests/load/`) |
| `black` / `ruff` | Formatting and linting |
| `mypy` | Static type checking |

## Dashboard (`dashboard/`, TypeScript)

React 19, Vite, Tailwind CSS. Key runtime deps: `@tanstack/react-query`
(server state), `react-router-dom` (routing), `recharts` (analytics charts),
`react-dropzone` (uploads), `lucide-react` (icons), `axios` (API client).

Optional integration (no package dependency): [Umami](https://umami.is/)
web analytics — the dashboard can load the script from an operator's own
self-hosted Umami instance, gated on env config and cookie consent, and
disabled by default. See the README's "note on analytics".

## CLI (`cli/`, TypeScript)

oclif on Node 20+. Key runtime deps: `@oclif/core` (command framework),
`playwright` + `axe-core` (local web scans), `@clack/prompts` (interactive
prompts), `picocolors` (terminal output).

## System-level (not in any lockfile)

Installed by the Docker images / quickstart, or by you when running bare:

| Tool | Role |
|---|---|
| PostgreSQL 16 | Primary database |
| Redis | Cache and rate limiting |
| Tesseract | OCR engine (via OCRmyPDF) |
| Ghostscript / qpdf | PDF preprocessing and repair |
| TeX Live (pdflatex/LuaTeX) + LaTeXML | LaTeX compilation and MathML conversion |
| Pandoc | Document format conversion |
| FFmpeg | Audio/video decoding for captioning |

## The rest of the lockfile

Every remaining pin in `requirements.txt`, so nothing is unaccounted for.

### More direct dependencies

| Dependency | Role |
|---|---|
| `PyLTI1p3` | LTI 1.3 launch/deep-linking protocol (Canvas, Blackboard, Brightspace routes) |
| `pytesseract` / `pdf2image` | OCR calls and PDF page rasterization inside the document processors |
| `numpy` | Color-blindness simulation and image math |
| `requests` | Legacy sync HTTP in the web scanner (being migrated to httpx) |
| `prometheus_client` | The `/metrics` endpoint (`src/monitoring/metrics.py`) |
| `sentry-sdk` | Optional error tracking — only initializes when `SENTRY_DSN` is set |
| `cssutils` / `cssselect2` | CSS parsing in the code/web accessibility scanner |
| `python-multipart` | File uploads (required by FastAPI at runtime, never imported directly) |
| `email-validator` | Backs pydantic's `EmailStr` validation |
| `psutil` | Process/resource introspection (also required by locust) |

### Transitive dependencies, grouped by what pulls them in

| Pulled in by | Pins |
|---|---|
| fastapi / uvicorn / httpx | `anyio`, `h11`, `httpcore`, `httptools`, `uvloop`, `watchfiles`, `websockets`, `click`, `idna`, `certifi`, `annotated-types`, `annotated-doc`, `pydantic_core`, `typing_extensions`, `typing-inspection`, `python-dotenv`, `PyYAML` |
| SQLAlchemy / alembic | `greenlet`, `Mako`, `MarkupSafe` |
| requests | `charset-normalizer`, `urllib3`, `brotli` |
| Google API client | `google-api-core`, `google-auth`, `google-auth-httplib2`, `googleapis-common-protos`, `proto-plus`, `protobuf`, `httplib2`, `uritemplate`, `pyasn1`, `pyasn1_modules`, `rsa`, `cachetools`, `pyparsing` |
| cryptography / bcrypt | `cffi`, `pycparser` |
| oletools (malware screening) | `olefile`, `pcodedmp`, `colorclass`, `easygui` |
| ocrmypdf | `pi_heif`, `deprecation`, `packaging`, `rich`, `typer-slim`, `markdown-it-py`, `mdurl`, `Pygments`, `img2pdf` |
| reportlab (PDF reports) | `freetype-py`, `pycairo`, `rlPyCairo`, `tinycss2`, `webencodings` |
| faster-whisper / onnxruntime | `huggingface_hub`, `tokenizers`, `hf-xet`, `fsspec`, `filelock`, `tqdm`, `flatbuffers`, `sympy`, `mpmath` |
| locust (load tests) | `Flask`, `flask-cors`, `Flask-Login`, `Werkzeug`, `Jinja2`, `itsdangerous`, `blinker`, `gevent`, `geventhttpclient`, `zope.event`, `zope.interface`, `msgpack`, `pyzmq`, `ConfigArgParse` |
| pytest / black / mypy | `iniconfig`, `pluggy`, `coverage`, `pytokens`, `pathspec`, `platformdirs`, `mypy_extensions` |
| beautifulsoup4 / openpyxl / python-pptx / playwright | `soupsieve`, `et_xmlfile`, `XlsxWriter`, `pyee` |
| email-validator / PyLTI1p3 / misc | `dnspython`, `Deprecated`, `wrapt`, `six`, `python-dateutil`, `more-itertools`, `defusedxml`, `setuptools`, `shellingham`, `pdfminer.six` (version managed by pdfplumber) |

Pins are audited against this test and removed when nothing requires them
any more: no reverse dependencies among the installed packages, and no
imports anywhere in the source tree.

## Optional extras

Commented out in `requirements.txt` with reasons inline — e.g. `paddleocr`
(multi-language OCR beyond Tesseract, ~1&nbsp;GB of extra weight).
