# Dependencies

What Aelira Core is built from, and what each major dependency does. The
authoritative pinned sets are [`requirements.txt`](../requirements.txt) (Python
runtime), [`requirements-dev.txt`](../requirements-dev.txt) (Python development
and test tooling, including the runtime set),
[`dashboard/package.json`](../dashboard/package.json) (dashboard), and
[`cli/package.json`](../cli/package.json) (CLI). This page is the human-readable
map; the pinned files and JavaScript lockfiles are the truth.

## Backend (Python)

Runtime dependencies live in `requirements.txt`; the sections below explain
the production roles that keep each one in the shipped environment.

### Coupled Python pins

Exact transitive pins must satisfy the parent package's published requirements.
`pydantic==2.14.1` requires `pydantic_core==2.50.1`; `sympy==1.14.0`
requires `mpmath>=1.1.0,<1.4`, for which the pinned version is `1.3.0`.
Independent minor updates of these transitive packages can make the entire
runtime and development installation unsatisfiable.

Dependabot ignores standalone `pydantic-core` updates and `mpmath>=1.4`.
When updating Pydantic, review its package metadata and update its exact core
pin in the same change. When updating SymPy, review its mpmath constraint and
adjust both the pin and the Dependabot exclusion if the supported range changes.
These exclusions do not establish security status: the shipped dependency audit
still applies, and any advisory affecting an excluded dependency needs a manual
compatible-pair update.

Resolve both requirements files in a fresh environment, run the affected
regressions, and require the security, full-test, AMD64 and ARM64 CI jobs to pass
before merging. Installing only the changed package or passing lint does not
verify compatibility of the pinned sets.

### Hugging Face Hub 2.x

`huggingface_hub` 2.x downloads the faster-whisper speech model and replaces
`httpx` with `httpx2` for its own traffic. That adds `httpx2` and `httpcore2`
(both pinned to the same release) and `truststore`, which `httpx2` uses for
system certificate verification. The application's own outbound HTTP still uses
`httpx`. `tokenizers` 0.23.3 is the first release that accepts Hub 3, and
`tokenizers` 0.23.2 required `huggingface-hub<2.0`; keep the two moving
together. `scripts/smoke_whisper_transcription.py` downloads the small `tiny.en`
model through the installed Hub and transcribes synthesized speech, then repeats
the load with `HF_HUB_OFFLINE=1` and no network; CI runs it in the built API
image on both architectures.

### Web framework and API

| Dependency | Role |
|---|---|
| `fastapi` | The API server (~330 routes under `src/api/`) |
| `uvicorn` | ASGI server |
| `pydantic` / `pydantic-settings` | Request/response models and typed settings |
| `starlette` | ASGI toolkit underneath FastAPI (middleware, responses) |
| `httpx` | Outbound HTTP (AI providers, LMS APIs, webhooks) |

### Data layer

| Dependency | Role |
|---|---|
| `SQLAlchemy` 2.1 | ORM — models in `src/db/models.py`; unqualified PostgreSQL URLs retain psycopg2 at engine creation |
| `alembic` | Database migrations (`alembic/versions/`) |
| `psycopg2-binary` / `asyncpg` | PostgreSQL drivers (sync and async) |
| `redis` | Rate limiting, job coordination, caching |

### Authentication and security

| Dependency | Role |
|---|---|
| `PyJWT` / `jwcrypto` | JWT session tokens and LTI 1.3 message signing |
| `bcrypt` | Hashing for API keys, refresh tokens, and confirmation codes |
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
| `Pillow` | Structural verification, bounded decoding, and full pixel loading for PNG/JPEG data URLs embedded in PDF-derived accessible HTML |

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
| `defusedxml` | XXE-safe XML parsing (sitemaps) |
| `faster-whisper` / `ctranslate2` | Speech-to-text for captions and transcripts |
| `av` / `scenedetect` | Video decoding and scene extraction |
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

### Development and test dependencies

These packages live in `requirements-dev.txt`. Installing that file also
installs every runtime dependency through `-r requirements.txt`.

| Dependency | Role |
|---|---|
| `pytest` (+ `pytest-asyncio`, `pytest-cov`) | Test suite (`tests/`) |
| `locust` | Load testing (`tests/load/`) |
| `black` / `ruff` | Formatting and linting |

## Dashboard (`dashboard/`, TypeScript)

React 19, Vite, Tailwind CSS. Key runtime deps: `@tanstack/react-query`
(server state), `react-router-dom` (routing), `recharts` (analytics charts),
`react-dropzone` (uploads), `lucide-react` (icons), `axios` (API client).

Optional integration (no package dependency): [Umami](https://umami.is/)
web analytics — the dashboard can load the script from an operator's own
self-hosted Umami instance, gated on env config and cookie consent, and
disabled by default. See the README's "note on analytics".

## CLI (`cli/`, TypeScript)

oclif on Node 22+. Key runtime deps: `@oclif/core` (command framework),
`playwright` + `axe-core` (local web scans), `@clack/prompts` (interactive
prompts), `picocolors` (terminal output).

The SARIF tests use AJV 8 with `ajv-formats` in full validation mode for the
official OASIS schema's URI, URI-reference and date-time constraints. The schema
contains a language pattern with an unescaped closing bracket, so this test
validator retains non-Unicode pattern matching (`unicodeRegExp: false`) as in
AJV 6. The schema fixture remains unchanged. Regression tests accept valid
generated logs and reject malformed formats and values that fail that pattern.

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
| FFmpeg 9.0.2 (`ffmpeg`, `ffprobe`) | Audio/video probing, decoding and muxing for captioning; the API image builds it from source (below) |

### CPython Expat in API images

Production and quickstart images keep the pinned official CPython 3.14.8
interpreter, ABI, headers, configuration, and unrelated `/usr/local` files.
Their fresh Debian Trixie runtime installs `libexpat1=2.9.0-1~deb13u1`.
Only `pyexpat` and its matching `_elementtree` C-API consumer are rebuilt
from the [official CPython 3.14.8 source
archive](https://www.python.org/ftp/python/3.14.8/Python-3.14.8.tar.xz)
(SHA-256 `c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73`)
with CPython's supported `--with-system-expat` configure option. The source
archive's `LICENSE` covers CPython; Debian ships Expat's copyright and
license record at `/usr/share/doc/libexpat1/copyright`.

The old bundled modules are removed before the official tree is copied into
the Debian-rooted runtime. `scripts/verify_final_python_packages.py` checks
the imported pair, normal XML consumers, Debian package version, loader path,
and libpython cache; `scripts/verify_python_expat_layers.ts` checks every
exported image layer. Both gates run for final images in CI. The exact Debian
package pin fails the build or final check if the mutable archive supersedes
this version; a saved Debian package or snapshot is needed for long-term
replay of all apt inputs.

## The rest of the dependency inventories

Every package resolved in the supported runtime and contributor environments is
explicitly pinned in one of the two requirements files, so nothing is left to
the resolver's latest-version choice.

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

### Libraries built from source

The published `lxml`, `Pillow`, `psycopg2-binary`, `av`, and `opencv-python`
wheels bundle native libraries that Debian and the vulnerability scanner do
not see, and that lag upstream security releases. Both the production API and
quickstart images build the five exact distributions from their pinned source
releases, then install the resulting wheels before the locked requirements,
which are then already satisfied. The quickstart keeps pip for developer use;
its source builds and temporary build tools finish in the same image layer.

| Package | Build script | What it links |
|---|---|---|
| `lxml` 6.1.3 | `docker/build-lxml-wheel.sh` | libxml2 2.15.4, libxslt 1.1.45, zlib and libiconv, statically, from hash-verified archives |
| `Pillow` 13.0.0 | `docker/build-pillow-wheel.sh` | libtiff 4.7.2 (JPEG, Deflate, LZMA and Zstandard codecs) statically, from a hash-verified archive; JPEG, JPEG 2000, WebP, AVIF, FreeType, Raqm with FriBidi and HarfBuzz, LittleCMS, zlib and XCB from Debian packages |
| `psycopg2-binary` 2.9.13 | `docker/build-psycopg2-wheel.sh` | Debian `libpq` and its system TLS libraries, with no wheel-private `psycopg2_binary.libs` |
| `av` 18.1.0 | `docker/build-pyav-wheel.sh` | FFmpeg 9.0.2 shared libraries under `/opt/ffmpeg9/lib`, built from a hash-verified official release archive; no wheel-private `av.libs` or TLS libraries |
| `opencv-python` 5.0.0.93 | `docker/build-opencv-wheel.sh` | The same FFmpeg 9.0.2 libraries and Debian `libdav1d7` 1.5.1-1 for CPU AV1 decoding; no `opencv_python.libs` or optional OpenCV image-codec backends |

The [PyAV source archive](https://pypi.org/project/av/18.1.0/#files) has SHA-256
`47bfc286e1bc9de7ab4681fc2b575cd2460a66919d31ffe1bd5aa54fae531a28`.
The [official FFmpeg archive](https://ffmpeg.org/download.html) has SHA-256
`8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`;
its official release signature was verified against FFmpeg release signer
`FCF986EA15E6E293A5644F10B4322F04D67658D8`. The PyAV build pins Cython
3.2.9 and the existing setuptools 84.0.0, disables build isolation, and checks
both archive hashes before unpacking. The final image carries the FFmpeg and
PyAV license texts with the seven required shared libraries and the `ffmpeg`
and `ffprobe` programs. PyAV's extension
modules and the FFmpeg siblings use an embedded RPATH to that prefix.
The [OpenCV 5.0.0.93 source archive](https://pypi.org/project/opencv-python/5.0.0.93/#files)
has SHA-256 `66aac3e5b5faa48d4025816592f3af19e4bfc2c68dec067bae2dbb4ca10aa9e2`.
Its exact [upstream merged FFmpeg 9 compatibility diff](https://github.com/opencv/opencv/compare/49c8d21f16e205647fb77535b3d182f14b2debfe...c0166c617d765ac707dde6b9acd8cfcd49751049.diff)
has SHA-256 `ee7c145e51c7988325296ea2cc1dc70b42ecd6e27b49386370b926c2211ff6f4`.
Both hashes are checked before the OpenCV build. Its temporary build environment
pins NumPy 2.3.2, setuptools 69.5.1 and wheel 0.45.1 as required by the
sdist's legacy build backend on Python
3.14; the final application keeps NumPy 2.5.4 and setuptools 84.0.0. FFmpeg's
development headers and linker symlinks are removed after both wheels exist.
The final-image verifier checks the installed PyAV version, native FFmpeg
version tuples, every resolved FFmpeg SONAME and path, and the absence of
`av.libs` and linked TLS libraries.

This FFmpeg build disables autodetected optional components, including TLS/HTTPS.
It explicitly enables Debian's `libdav1d` software AV1 decoder and disables
FFmpeg's hardware-only native AV1 decoder so OpenCV selects the CPU decoder by
codec ID. PyAV still decodes the local PCM WAV that `multimedia_processor.py`
generates for `faster-whisper==1.2.1`.

The same build provides the `ffmpeg` and `ffprobe` programs the application
runs, installed under `/opt/ffmpeg9/bin` and linked from `/usr/local/bin`.
Debian's `ffmpeg` package is not installed in the API image, which also drops
its libraries and their dependencies, among them `librist4`, `libcjson1`,
`libsndfile1`, SDL and PulseAudio. The build compiles in only the `file` and
`pipe` protocols (`--disable-network --disable-protocols`), so no demuxer can
open a network reference even without the application's input whitelist. It
links Debian's `libmp3lame0` 3.100-6+b3 and `libvorbis0a`/`libvorbisenc2`
1.3.7-3 (with `libogg0` 1.3.5-3+b2), pinned in both stages, so spoken audio is
still written as MP3 and Ogg Vorbis; without `libvorbis`, FFmpeg's Ogg muxer
falls back to FLAC. The final-image verifier pins both programs' version,
configure line, library paths, protocol list and encoders, and
`scripts/smoke_media_cli.py` runs every application call site against the
shipped programs in CI. The development image keeps Debian's `ffmpeg` on PATH
as its fixture tool; its `/opt/ffmpeg9` build carries the same programs but
they are not on PATH.

The OpenCV wheel retains the exact `opencv-python` distribution identity that
PySceneDetect 0.7.2 requires. Its compiled scope is scene video capture and
processing; OpenCV's optional JPEG, PNG, TIFF, WebP, JPEG 2000, GIF, AVIF and
other optional image-codec backends are disabled even when the production and
quickstart builders have `libavif-dev` for Pillow. OpenCV's built-in BMP codec remains
registered. The unused Protobuf and Flatbuffers backends are disabled.
With the production builder's image-development packages present, OpenCV
links Debian `libz.so.1` (zlib 1.3.1) rather than its in-tree zlib. Its
remaining internal ittnotify, libclapack, and HAL components are retained
support code, not additional linked media, image-codec, or TLS shared
libraries. Pillow handles application image decoding and keeps its own verified
format support. Native acceptance checks normal H.264, HEVC, VP8, VP9,
MPEG-4, MJPEG and AV1 input, 90 decoded frames and scene starts at
0, 3 and 6 seconds, plus a true single-scene input. These checks cover the
listed CPU inputs and do not establish every media profile or clear broader
dependency findings tracked by #583, #578 and #377.

Raising any source-built pin means changing the requirement, the build script's
version and digests, and the policy test together. `pi_heif` is intentionally absent:
`ocrmypdf` does not depend on it and nothing in the application imports it. The
final image check (`scripts/verify_final_python_packages.py venv`) requires
Pillow's libtiff to be at least 4.7.2, the required codec and text-shaping
features to be present, no
`pillow.libs` directory and no `pi_heif`, `pybind11` or `Cython`
distribution.
The quickstart runs the same native binding and image-feature checks through
`scripts/verify_final_python_packages.py quickstart`, with its retained pinned
pip checked separately. Its CI image smoke also exercises PyAV WAV resampling,
Pillow's formats and text shaping, and real OCR.
The published Pillow wheel reports `zlib_ng` as its zlib implementation; this
build uses Debian's maintained `zlib1g` while retaining PNG and Deflate support.

The developer image keeps pip 26.2.2 as an installer. Its published wheel
(SHA-256 `fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70`)
vendors CacheControl 0.14.4, distlib 0.4.2, distro 1.9.0, msgpack 1.1.2,
packaging 26.2, platformdirs 4.10.0, pyproject-hooks 1.2.0, requests 2.34.2,
certifi 2026.10.19, idna 3.18, urllib3 2.7.0, rich 14.2.0, pygments 2.20.0,
resolvelib 1.2.1, setuptools 70.3.0, tomli 2.4.1, tomli-w 1.2.0 and
truststore 0.10.4. Only certifi differs from pip 26.2.1, whose vendored
certifi 2026.6.17 still trusts `emSign ECC Root CA - G3`
([GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2),
[#725](https://github.com/Aelira-AI/aelira-core/issues/725)). The quickstart
verifier requires this exact vendor manifest and urllib3 code version;
production still requires pip and its vendor tree to be absent. The separately
installed application urllib3 remains pinned to 2.8.0. This native-library
change does not resolve findings in pip's bundled pure-Python dependencies;
broader dependency work remains open under #578.

Build-only pip pins stay at 26.2.1 and are not shipped. The native evidence
pipelines under `runtime-security/` (`lxml`, `lxml-native-ci`, `psycopg2` and
`media/pillow`) install pip 26.2.1 offline from hash-pinned inputs into
disposable build containers, and `docker/build-opencv-wheel.sh` installs it into
a temporary build virtual environment that the script deletes on exit. No
shipped image keeps any of them. The evidence pipelines record those exact pip
inputs, so a new pip version there means a new evidence run, not a pin edit.

### System trust store

Debian trixie `ca-certificates` 20250419 ships `emSign ECC Root CA - G3`
(SHA-256
`86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b`). The
security update 20250419+deb13u1 removes it, but an image built from an older
package index, such as a cached apt layer, still installs 20250419. Clients
that read the system store trust the root: `curl`, Python's default SSL context
and the Hugging Face Hub client through `truststore`. The runtime and developer
Dockerfiles deselect it with a `!` line in `/etc/ca-certificates.conf` and
rebuild `/etc/ssl/certs`, after the last apt operation of the stage and leaving
the other emSign roots (G1, C1 and C3) trusted. The step does nothing once the
installed package no longer lists the root, which is the case with
20250419+deb13u1. `scripts/verify_final_os_packages.py` fails the build if the
bundle or any certificate in `/etc/ssl/certs`, including its hashed lookup
link, has that fingerprint, or if one of the three retained emSign roots is
missing from either. CI and publication run it against the API image, and the
developer build runs `verify_final_os_packages.py trust-store`. Node.js carries
its own root store, so pa11y's Node still trusts the root
([#726](https://github.com/Aelira-AI/aelira-core/issues/726)).

### Transitive dependencies, grouped by what pulls them in

| Pulled in by | Pins |
|---|---|
| fastapi / uvicorn / httpx | `anyio`, `h11`, `httpcore`, `httptools`, `uvloop`, `watchfiles`, `websockets`, `click`, `idna`, `certifi`, `annotated-types`, `annotated-doc`, `pydantic_core`, `typing_extensions`, `typing-inspection`, `python-dotenv`, `PyYAML` |
| SQLAlchemy / alembic | `greenlet`, `Mako`, `MarkupSafe` |
| requests | `charset-normalizer`, `urllib3`, `brotli` |
| Google API client | `google-api-core`, `google-auth`, `google-auth-httplib2`, `googleapis-common-protos`, `proto-plus`, `protobuf`, `httplib2`, `uritemplate`, `pyasn1`, `pyasn1_modules`, `rsa`, `cachetools`, `pyparsing` |
| cryptography / bcrypt | `cffi`, `pycparser` |
| oletools (malware screening) | `olefile`, `pcodedmp`, `colorclass`, `easygui`, `chardet`, `encutils` |
| ocrmypdf | `deprecation`, `packaging`, `pathvalidate`, `pypdfium2`, `rich`, `typer`, `typer-slim`, `markdown-it-py`, `mdurl`, `Pygments`, `img2pdf` |
| ocrmypdf / pikepdf PDF/A support | `fpdf2`, `uharfbuzz`, `fonttools`, `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`, `attrs` |
| reportlab (PDF reports) | `freetype-py`, `pycairo`, `rlPyCairo`, `tinycss2`, `webencodings` |
| faster-whisper / onnxruntime | `huggingface_hub`, `tokenizers`, `hf-xet`, `httpx2`, `httpcore2`, `truststore`, `fsspec`, `filelock`, `tqdm`, `flatbuffers`, `sympy`, `mpmath` |
| locust (load tests) | `Flask`, `flask-cors`, `Flask-Login`, `Werkzeug`, `Jinja2`, `itsdangerous`, `blinker`, `gevent`, `geventhttpclient`, `zope.event`, `zope.interface`, `msgpack`, `pyzmq`, `ConfigArgParse`, `bidict`, `python-engineio`, `python-socketio`, `simple-websocket`, `websocket-client`, `wsproto` |
| pytest / black | `iniconfig`, `pluggy`, `coverage`, `pytokens`, `pathspec`, `mypy_extensions` |
| beautifulsoup4 / openpyxl / python-pptx / playwright | `soupsieve`, `et_xmlfile`, `XlsxWriter`, `pyee` |
| scenedetect | `opencv-python`, `platformdirs` |
| email-validator / PyLTI1p3 / misc | `dnspython`, `Deprecated`, `wrapt`, `six`, `python-dateutil`, `more-itertools`, `setuptools`, `shellingham`, `pdfminer.six` |

Pins are audited against this test and removed when nothing requires them
any more: no reverse dependencies among the installed packages, and no
imports anywhere in the source tree.

## Optional extras

Commented out in `requirements.txt` with reasons inline — e.g. `paddleocr`
(multi-language OCR beyond Tesseract, ~1&nbsp;GB of extra weight).
