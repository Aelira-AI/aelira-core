# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.5] - 2026-08-22

### Security

- Canvas staff authorization remains bound to the authoritative tenant, account, course, content type, and object throughout launch, navigation, scan, remediation, approval, upload, and write-back paths.
- API-key recovery and management remain tenant-scoped, LMS AI use is policy-bound and purpose-isolated, and release publication remains fail-closed behind signed-tag, protected-denylist, CI, four-receipt, dependency-audit, SBOM, and reproducibility gates.

### Fixed

- Canvas content identity now includes course and content type; inline image remediation preserves image type; LTI navigation exchanges launch codes once; dashboard review actions and labels reflect persisted state.
- Standalone batches drain all requested work, including the verified 1,000-item boundary, instead of stopping after the first page.

### Changed

- Cloud scans, remediations, uploads, syncs, and Canvas reconciliation run through a bounded, fenced, multi-worker durable queue with heartbeats, retries, restart recovery, deduplication, and explicit managed artifacts.
- The deterministic release browser gate covers the staff Canvas course, image remediation, review, write-back, rescan, restart, and artifact journey across the release Chromium set and compatibility matrix.

### Operator action required

- **Durable-job quarantine:** durable-worker activation quarantines every pre-v0.9.5 pending or processing job rather than executing it. Each row becomes terminal `failed` with exact reason `pre_v0_9_5_job_quarantined`; its original payload, result, and external-effect evidence remain available for review. Identify all affected rows with: `SELECT id, job_type, status, last_error_code, created_at FROM cloud_job_queue WHERE status = 'failed' AND last_error_code = 'pre_v0_9_5_job_quarantined' ORDER BY created_at, id;`
- Review each quarantined row and its linked course, file, credential, managed artifact, and external-effect evidence. Do not edit the old row back to `pending`. After confirming current authorization and intent, deliberately resubmit scans, remediations, uploads, and syncs through the same authenticated dashboard action or API endpoint that initiates new work; retain the failed row as the audit record.
- Preserve every v0.9.4 operator action below, back up PostgreSQL, run `alembic upgrade head` explicitly, and confirm API and worker health before accepting new work.

## [0.9.4] - 2026-08-19

### Security

- LTI is staff-only and fails closed. Canvas `Administrator` launches receive account-wide scope; `Instructor`, `TeachingAssistant`, and `ContentDeveloper` launches are course-scoped. Learner-only, missing, malformed, and unknown roles are denied before provisioning, token creation, statistics, grade-service state, deep-link work, or data access. Route authorization independently enforces tenant, course, and account scope.
- Existing legacy API keys with the static `aelira_live_` prefix are intentionally disabled. New keys use indexed random-bearing prefixes, active tenant-consistent owners, and bounded bcrypt work.
- Existing legacy LTI users are deactivated and marked for reauthorization. An authorized staff relaunch can reactivate only the matching migration-marked identity; deletion-pending and administratively deactivated users are never revived.
- Canvas OAuth state is opaque, one-time, expiring, and server-side. Canvas origins require exact current operator authorization; outbound DNS is bound to the connection, redirects and pagination are validated and bounded, and OAuth tokens are never placed in download URLs or forwarded to cross-origin download/upload targets.
- Session access requires a live database session unless it is a canonical LTI v2 token. Refresh rotation uses a stable session ID and one short encrypted replay window for legitimate concurrent requests; concurrent dashboard 401s share one refresh and terminate/logout once when recovery fails.
- Public release scanning now requires a protected disclosure policy, scans exact Git index blobs and paths, redacts protected findings, and fails closed before the coordinated Docker, npm, and GitHub Release pipeline can publish.

### Fixed

- Blackboard remediation and generic cloud-provider synchronization routes that have no durable executor now return HTTP `501` and create zero job rows instead of claiming work was queued.
- Weekly alert schedules are repaired and constrained: null or invalid legacy values become Monday at 09:00 UTC, with database and API bounds of day `0–6` and hour `0–23`.
- Expired dashboard sessions no longer enter a refresh/validation flash loop; recovery retries each request at most once and redirects once to a clear sign-in state.
- Canvas account management, content scans, uploads, remediation, re-downloads, write-back, and token refresh all revalidate the currently allowed persisted Canvas origin before using credentials.

### Changed

- Releases now run as one bounded CI → preflight → Docker → npm → GitHub Release DAG. API and dashboard images are built on native amd64/arm64 runners, verified by immutable digest, and promoted together before npm or a GitHub Release can publish.
- Published container tags use the non-`v` forms `X.Y.Z`, `X.Y`, and `latest`; deployment documentation now recommends digest pinning for reproducibility.

### Operator action required

- **No downgrade:** Back up PostgreSQL before upgrading. The published Canvas-content schema migration refuses to move its revision marker backward because deeper rollback can destroy adopted production data. Credential and LTI invalidation is also intentionally irreversible. Returning to v0.9.3 requires restoring the pre-upgrade database backup together with the matching v0.9.3 images.
- Set a stable `SESSION_REPLAY_ENCRYPTION_KEY` in staging and production before rollout. Preserve it across restarts and use the same value on every worker.
- When Canvas OAuth is enabled, set `CANVAS_OAUTH_ALLOWED_ORIGINS` to the exact canonical institutional Canvas HTTPS root origins. Staging and production also require Redis for one-time Canvas OAuth state. Connections whose persisted origin is absent or no longer allowed must reconnect.
- Keep `UVICORN_WORKERS=1`. Back up the database and run `alembic upgrade head` as an explicit preflight; container startup also runs migrations and fails closed if they fail.
- Reissue replacement keys and update every client that used a legacy API key. Existing LTI users must relaunch through an authorized staff Canvas placement to complete reauthorization.

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
