# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Releases now run as one bounded CI → preflight → Docker → npm → GitHub Release DAG. API and dashboard images are built on native amd64/arm64 runners, verified by immutable digest, and promoted together before npm or a GitHub Release can publish.
- Published container tags use the non-`v` forms `X.Y.Z`, `X.Y`, and `latest`; deployment documentation now recommends digest pinning for reproducibility.

## [0.9.3] - 2026-08-18

### Fixed

- Canvas content review works end to end. LTI-launch tokens are admitted on both authentication paths, launches land on the course they came from, and a deep link opened while logged out returns to the page it asked for instead of the default landing page
- Course files are treated as course content: they are scanned, listed and counted alongside pages and assignments, and they contribute to course and institution compliance scores. Previously a course could report a clean score while its files were the worst thing in it
- Remediated files reach the course. The remediated copy is uploaded alongside the original, so nothing an author wrote is overwritten
- The remediate endpoint runs the work it reports. It previously wrote job rows, returned success, and did nothing, because nothing polls the queue
- Content items are remediated in place rather than being sent to the file endpoint, which tried to download a document that does not exist and reported the resulting 404 as a failed remediation
- A refused OAuth authorisation returns to the dashboard with the reason the LMS gave, instead of a validation error about a missing query parameter
- Migrations match the models. A database built the way a deployment builds one was missing the entire content surface: two tables, thirteen columns, and two enum values. Installing from a clean database produced an application that failed the moment it touched course content
- Uploads default to a directory under the working directory rather than an absolute container path, so running from source no longer fails with a permission error

### Changed

- Remediation is verified by rescanning the result, and the measured score is the one reported. Where no rescan was recorded the fixed/remaining split is reported as unknown rather than assumed. Issues the remediation introduced are counted separately from issues that remain
- Content is scanned in the document context an LMS renders, so findings describe the author's content rather than the wrapper. Three of five findings on a real course page were artefacts of a bare wrapper, worth 12.5 points of score
- Alt text is generated from the image itself, fetched with the integration's own credential. Empty alt, placeholder strings, and descriptions of images that could not be retrieved are refused: an unfixed image is visible to an audit, a falsely fixed one is not
- Vision requests retry on transient refusals. A 503 was previously treated as a final answer, leaving the image with no description at all
- Controls that did nothing are gone: fake pause and resume, a scan button with no handler, and a per-issue auto-fix that silently remediated the whole document, which is now labelled for what it does
- Batch results report what was skipped instead of showing success for a run that changed nothing
- Integration tests run in CI. They were skipped whenever CI was detected, so 284 tests including every API route test were verified only on a developer's machine

## [0.9.2] - 2026-08-17

### Security

- Browser-level SSRF protection for web scanning: every request Chromium makes during a scan — navigations, redirects, and subresources — is now validated against private/loopback/link-local targets, with redirect chains walked and validated hop by hop. Previously only the initial scan URL was checked.

### Fixed

- PDF remediation results now count the fixes performed by the content tagging pass (content marking, ParentTree, document root, PDF/UA identifier) instead of reporting them as manual work (#48)

### Changed

- GitHub releases, Docker images, and npm publishes now require all five CI checks to be green on the exact tagged commit before anything ships
- Quickstart documents the optional Ollama models and WCAG knowledge-base seeding steps

## [0.9.1] - 2026-08-16

### Security

- Bearer token hardening: access tokens are type-gated and checked against live sessions, so refresh tokens can no longer be replayed as access credentials and revoked tokens stop working at logout
- CSRF enforcement on cookie-authenticated dashboard mutations via a double-submit `X-CSRF-Token`; the dashboard's blanket exemptions were removed
- OAuth callback CSRF protection: Google/Microsoft connect and callback now use server-side one-time state, and the workspace binding comes only from verified state metadata
- `/google/connect` now requires authentication (development fallback removed)
- OAuth login domain matching uses exact domain equality (a substring match could be bypassed by look-alike domains)
- Sitemap XML parsing switched to `defusedxml` (XXE)

### Changed

- Faculty gamification and leaderboards are now opt-in and off by default
- LMS integration maturity labeled honestly: Canvas is production-verified; others range from beta to untested
- Documentation claims accuracy pass; SECURITY.md supported-versions table aligned; dashboard licence declared
- Upload paths genericized and dashboard debug logging removed

### Dependencies

- bcrypt 5.0.0, redis 8.1.0, websockets 17.0.1, python-pptx 1.0.2, av 18.1.0, packaging 26.3, setuptools 84.0.0, click 8.3.3
- Dashboard and CLI npm minor/patch groups; docker build actions updated

## [0.9.0] - 2026-08-15

### Added

- Initial public release
- FastAPI backend with document scanning and AI-powered remediation
- React + Vite admin dashboard
- Document processors: PDF, DOCX, PPTX, XLSX, LaTeX, HTML, images, video
- AI remediation with bring-your-own-model support: Gemini, OpenAI, Anthropic, xAI, any OpenAI-compatible endpoint, or fully local via Ollama
- WCAG 2.1 AA compliance scanning and reporting
- Authentication: magic links, Google OAuth, Microsoft OAuth
- Multi-tenant department system with tier-based quotas
- LMS integrations: Canvas LTI 1.3, Blackboard, Moodle, Brightspace
- Cloud storage integrations: Google Workspace, Microsoft 365
- PDF/UA compliance tagging and structure tree manipulation
- Reading order analysis and correction
- Table accessibility detection and remediation
- Color contrast analysis
- OCR for scanned documents
- Video captioning and audio description
- GDPR-compliant account management and data deletion
- Database schema management with Alembic migrations
- Docker development environment
