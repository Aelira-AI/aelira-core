# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

These entries describe changes merged into `main` since v0.9.11, not a new versioned release. Published v0.9.11 artifacts and stable Compose image references remain unchanged.

### Security

- Outbound HTTP connections are pinned to validated public addresses. Browser scan requests use the guarded transport; unsupported frames, popups, workers and alternate network channels leave required checks incomplete rather than producing a verified score ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- New upload writes are confined through directory descriptors. OAuth callback destinations are constrained, and affected API errors and persisted diagnostics omit unfiltered exception details ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Image descriptions require verified source pixels and the workspace vision provider. Text-only fallbacks are removed; missing images remain unresolved ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Dashboard API keys are held in memory instead of persistent browser storage. Cookie-session recovery remains available; API-key users re-enter their key after a full reload ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Cache deletion accepts canonical provider filters, and HTTPX request-detail logs that could expose caller URL paths or queries are suppressed ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Dependency updates include security fixes for JWT handling, PDF parsing, dashboard HTML sanitisation, CLI brace expansion and KaTeX development tooling. Existing dependency audit and image gates remain required ([#518](https://github.com/Aelira-AI/aelira-core/pull/518), [#540](https://github.com/Aelira-AI/aelira-core/pull/540)).

### Added

- The dashboard can prepare bounded PDF heading-level, sibling-order and simple first-row column-header edits from verified structure targets. Saves create immutable, pending-review candidates with source checksums and durable concurrency guards; they do not approve output or write it back to an LMS. Unsupported structures remain refused ([reading-order and editing guide](docs/reading-order-review.md)).
- The PPTX direct-library remediator can save an explicitly accepted, source-bound shape order for supported slides, verifying unchanged package parts and the reopened output. This does not add an API/queue reviewer-input workflow or establish PowerPoint/assistive-technology acceptance ([#501](https://github.com/Aelira-AI/aelira-core/pull/501)).
- A bounded LaTeX project ZIP workflow preserves original member bytes, inventories literal dependencies and exposes checksum-bound original retrieval and accepted HTML candidates. Unsupported or incomplete projects retain explicit diagnostics; multi-file source remediation and project PDF output remain outside this workflow ([project workflow](docs/testing/latex-source-projects.md)).
- Workspace administrators can inspect and edit saved Ollama text, code and vision model identifiers, or disable workspace AI while retaining configuration. Model selection does not download models, and the connection test exercises only the saved text model ([local AI settings](docs/deployment/local-ai-models.md#workspace-settings)).

### Fixed

- Short direct-text PDFs no longer inherit the OCR output minimum. Incomplete required PDF checks expose bounded manual-review reasons and withhold unverified scores ([#384](https://github.com/Aelira-AI/aelira-core/pull/384)).
- Missing remediation confidence stays unknown across persistence, review, aggregation and exports; genuine zero scores remain zero. Unknown values cannot pass numeric batch approval. Historical generic AI defaults are conservatively corrected with audit records and invalidated approval bindings ([confidence and migration guidance](docs/document-remediation/confidence.md)).
- LaTeX handling preserves complete supported math input, authored metadata and verified figure/table relationships instead of fabricating defaults or treating conversion as accessibility evidence. Compilation failures and incomplete exports retain diagnostics and cannot publish a partial PDF ([PDF validation boundaries](docs/testing/latex-pdf-validation.md), [authored relationships](docs/testing/latex-authored-relationships.md)).
- LaTeX converter selection records the compatibility decision, preprocessing and source/equation provenance without silently trying another HTML converter after failure. Bounded saved HTML exports use native non-table equation layout and verified language fixes, preserve durable conversion receipts, and keep refused findings unresolved with zero fixed credit. HTML has no verified accessibility score; companion stylesheets remain unpackaged ([#479](https://github.com/Aelira-AI/aelira-core/pull/479), [#532](https://github.com/Aelira-AI/aelira-core/pull/532)).
- Google and Microsoft routes return actual binary downloads/exports and persisted scan/remediation job identities, refresh credentials and clean temporary storage. Legacy server-local upload requests return unsupported status; Google exports reject the obsolete `output_path` field. Interrupted Microsoft subscription mutations retain a pending reconciliation marker ([#437](https://github.com/Aelira-AI/aelira-core/pull/437), [#438](https://github.com/Aelira-AI/aelira-core/pull/438)).
- Account deletion confirmations and administrator invitations await required delivery. Account lifecycle changes commit account, session and audit updates together; failed invitation delivery remains visible for retry. Notification preferences and alert recipient contracts preserve stored state ([#434](https://github.com/Aelira-AI/aelira-core/pull/434), [#433](https://github.com/Aelira-AI/aelira-core/pull/433), [#431](https://github.com/Aelira-AI/aelira-core/pull/431), [#432](https://github.com/Aelira-AI/aelira-core/pull/432)).
- Dashboard review selection survives refresh and narrow-screen controls remain usable. LMS policy denials provide actionable recovery without changing authorization ([#488](https://github.com/Aelira-AI/aelira-core/pull/488), [#495](https://github.com/Aelira-AI/aelira-core/pull/495)).
- Cancelled CLI prompts stop before dispatching operations. SARIF validation uses AJV 8 with explicit format checks ([#472](https://github.com/Aelira-AI/aelira-core/pull/472), [#344](https://github.com/Aelira-AI/aelira-core/pull/344)).
- Development setup, non-root upload storage, nginx route forwarding, encoded database URLs and checksum-verified Piper voice installation match the current runtime configuration ([#398](https://github.com/Aelira-AI/aelira-core/pull/398), [#386](https://github.com/Aelira-AI/aelira-core/pull/386), [#421](https://github.com/Aelira-AI/aelira-core/pull/421)).

### Changed

- Dashboard presentation uses shared theme tokens, clearer typography, restrained card depth, responsive summary/table layouts, keyboard account disclosure, working guide/help actions and an unclipped score ring. Theme transitions preserve navigation contrast ([#541](https://github.com/Aelira-AI/aelira-core/pull/541)).
- Transactional emails use a table/inline-style shell and a current tagline-free PNG for default Aelira branding. Institution branding covers subjects, body text, sender-name fallback, legal identity and operator links; custom logos retain their aspect ratio, and a custom brand without a logo uses a text header ([branding configuration](BRANDING.md#replacing-the-branding)).
- The guarded secondary web engine identifies new findings as HTML_CodeSniffer; stored Pa11y findings retain their legacy label ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- CI requires explicit coverage/skip accounting, PostgreSQL worker/race checks, saved Office preservation, and real LaTeX compilation/TEX/HTML download evidence. Both native image architectures exercise functional LaTeX runtime controls. The aggregate `CI complete` check requires every job to succeed; these checks do not certify accessibility conformance ([coverage policy](docs/testing/coverage-and-skips.md), [release journey matrix](docs/testing/release-journey-matrix.md)).
- Opt-in alpha release tooling isolates prereleases from stable Docker, npm and GitHub release defaults while retaining the release safety gates ([alpha release guide](docs/deployment/alpha-releases.md)).
- Governance, CODEOWNERS and contribution/support routes identify the current maintainer and administration account, with Discussions, company social links and live CI/release badges ([GOVERNANCE.md](GOVERNANCE.md), [SUPPORT.md](SUPPORT.md)).

### Operator action required

- Before deploying these `main` changes, pause intake and review/remediation writers, drain work and take a coordinated backup of the database, uploads, artifacts and private configuration. Run `alembic upgrade head` and verify the head is `20260928_pdf_edit_pub`; deploy API, worker and dashboard from the same revision ([upgrade procedure](docs/deployment/self-hosting.md#upgrade-procedure)).
- Review the `20260917_unknown_confidence` historical correction before migrating: affected non-rejected fixes and approved artifacts not yet written back can return to pending review. Already published external output is not undone. Older images may not support nullable confidence; restoring only an old application image is not a safe rollback ([correction and recovery](docs/document-remediation/confidence.md#historical-correction)).
- Align backend `BRAND_NAME` and dashboard `VITE_BRAND_NAME`. Optional `EMAIL_LOGO_URL`, `EMAIL_LEGAL_NAME`, `EMAIL_PRIVACY_URL` and `VITE_PRIVACY_POLICY_URL` support institution-owned identity and policies. Use a public HTTPS PNG/JPEG for email logos, rebuild the dashboard after changing Vite settings, and verify delivery in the mail clients your institution uses ([BRANDING.md](BRANDING.md)).
- Expect API-key re-entry after a dashboard reload. Dynamic pages requiring unsupported browser network features can remain incomplete and unscored; image remediation needs retrievable source pixels. Validate representative files/pages and review saved outputs before publication.
- Preserve earlier release backup, deployment, provider, LMS and review requirements. LaTeX PDF machine validation additionally requires the configured veraPDF service; conversion success, synthetic controls and accepted candidates do not replace human review or assistive-technology testing.

## [0.9.11] - 2026-09-12

### Fixed

- Upload and scan-detail remediation status follows the authoritative job; download actions remain inside the artifact-aware review workflow.
- Withheld output retains all original findings as unresolved, with explicit withheld and unreported outcomes instead of disappearing counts.
- Corrected upload labels, nested interactive controls, review-list keyboard scrolling and affected label contrast.
- PDF verification compares source and output checkpoint results to distinguish persistent failures from newly introduced regressions.
- Reading-order checks resolve supported MCID/ParentTree text and stable page ownership; generated headings and lists retain source content bindings.
- Supported table pages compare table placement and surrounding text; ambiguous coverage stays explicitly incomplete instead of silently skipping the page. Internal table semantics remain a separate check.
- Issues uses document queue eligibility, reload-safe status references and a clear-tracking control. The Issues Found total explicitly describes original findings.
- Review controls wrap on narrow screens and document changes cannot display a stale review beside another file's preview.
- Review document operations and aggregate queries preserve authenticated department and course scope; explicit filters cannot widen access.
- Shared scan/remediation access and scan history reject unsupported LMS course bindings instead of matching course IDs across platforms.

### Changed

- Added a blocking real API/queue/worker/download acceptance gate for synthetic PDF and Office files, alongside the existing browser testbed.
- Scanner measurements and safety refusal are documented separately from accessibility conformance.
- Added a read-only original/saved PDF reading-order viewer with checksum-bound evidence, course-scoped authorization and conservative rendering limits. Unsupported evidence remains unavailable; no edited or inferred order is presented as saved data.

### Operator action required

- No database migration or new production environment variable is required. Deploy API, worker and dashboard from the same 0.9.11 release.
- Re-run remediation from the retained original for new per-finding outcome records; historical estimates are not retroactively verified.
- Preserve the v0.9.8 operator action requirements. Review saved files before publishing; partial-output safety restrictions remain in place.
- See [the corrective release notes](docs/releases/v0.9.11.md). Published v0.9.10 artifacts remain unchanged.

## [0.9.10] - 2026-09-12

### Fixed

- Remediation scores use paired scans of the original and saved output instead of estimates from fix counts. Genuine score decreases remain visible.
- Office uploads preserve canonical findings and target metadata. Legacy Office findings are recovered only when uniquely matched against the retained original, without rewriting scan history.
- Incomplete PDF checks, missing source evidence, and unsupported comparisons no longer produce verified scores. Canvas content uses paired browser checks rather than static source scores.
- The dashboard hides historical estimates, distinguishes applied changes from verified fixes, and explains unavailable comparisons with bounded diagnostic codes.

### Changed

- Verified comparisons include the scoring method version and SHA-256 identities of both measured artifacts.
- Regression coverage includes pre-hotfix records, persistence-to-worker Office comparisons, failed scans, changed artifacts, and score decreases.

### Operator action required

- No database migration or new environment variable is required. Deploy API, worker, and dashboard from the same 0.9.10 release.
- Old job estimates are not retrospectively verified. Re-run remediation from a retained original, or upload and scan the original again if it is unavailable. Original scan history is preserved.
- Existing partial-output publication restrictions remain in force. A valid automated score does not establish accessibility conformance.
- Preserve the v0.9.8 operator actions and backup requirements. See [the upgrade guide](docs/releases/v0.9.10.md) for comparison diagnostics and rescan guidance.

## [0.9.9] - 2026-09-09

### Security

- The CLI lockfile resolves `js-yaml` 4.3.2, closing GHSA-2883-xcg3-v3hh in the shipped dependency tree.

### Fixed

- Strict PDF remediation recognizes every built-in scanner issue type, including versioned PDF/UA rule labels, without requiring an intermediate enrichment step.
- Every input finding is accounted for as fixed, manual, failed, or skipped; unsupported future categories remain explicit manual work instead of disappearing from the result.
- The dashboard reconciles persisted fix records to scan findings one-to-one and reports fixed, proposed, approved, rejected, and failed outcomes without inferring unsupported detail.
- Zero-issue remediation jobs now include an authoritative `total_issues` value.

### Changed

- The scan-to-remediation integration suite now passes raw scanner output directly into strict remediation and permanently exercises the reported H1, title-metadata, and PDF/UA three-finding regression.

### Operator action required

- No database migration or new environment variable is required.
- Deploy API, worker, and dashboard from the same 0.9.9 release. Re-run any remediation that failed under v0.9.8, then confirm that fixed, manual, failed, and skipped counts sum to the total before relying on the artifact.
- Preserve every v0.9.8 operator action below, including backup, deployment configuration, worker-health, and review requirements.

## [0.9.8] - 2026-09-08

### Security

- Production images exclude development dependency trees, upgrade vulnerable runtime packages, and verify the final installed package state before publication.
- Review evidence exports and versioned evidence packages remain tenant-scoped, validate source and output identity, and fail closed on stale, missing, altered, or cross-tenant artifacts.
- Visual-analysis proposals are bound to exact source bytes and durable attempts; unsupported or unverifiable outputs remain review-required instead of being presented as completed remediation.

### Added

- Reviewers can create owned, expiring deferrals, download review evidence, and verify portable evidence packages offline.
- Durable image and chart analysis records expose bounded lifecycle, retry, provenance, and human-review states.
- Administrators can inspect worker and queue health from the dashboard, while the CLI can emit SARIF 2.1 for CI systems.
- A fixture-backed PDF acceptance corpus exercises representative scan and remediation behavior.

### Fixed

- Local uploaded documents are persisted before durable enqueue, preventing `local_scan_input_unavailable` failures after the request ends.
- Pa11y runs inside production API and worker images with the packaged Chromium launcher and checked-in launch policy.
- The dashboard shows each PDF issue location once, preserves scan actions at narrow widths, and reports aggregate remediation outcomes without inventing unavailable per-issue attribution.
- Production Compose accepts the documented environment format, serves dashboard API traffic through a same-origin proxy, and derives public links and email branding from deployment settings.
- PDF rollback comparisons are serialization-stable, invalid PDF role-map self-mappings are removed, and ScanFix JSON preserves genuine null outcomes.

### Changed

- Runtime and development Python dependencies are separated, with release checks covering both fully pinned sets.
- CLI network commands consistently prefer explicit flags, then `AELIRA_API_URL`, then the active profile, then localhost.
- Release actions use Node 24-compatible revisions and artifact-producing jobs receive only the required metadata permission.
- CLI, dashboard, Python, GitHub Actions, OCRmyPDF, and transitive dependencies are refreshed to their reviewed v0.9.8 set.

### Operator action required

- Back up PostgreSQL and verify the restore path. Drain active work, then run `alembic upgrade head` and confirm the single head is `20260905_visual_analysis`.
- Reconcile `.env` with the new production Compose template. Replace every required secret placeholder and set `PUBLIC_API_URL`, `PUBLIC_DASHBOARD_URL`, and `CORS_ORIGINS` for the public deployment.
- Deploy API, worker, and dashboard from the same 0.9.8 release. Confirm API readiness, worker health, dashboard health, same-origin `/api/live`, queue age, and failed or quarantined jobs before resuming intake.
- Review active deferrals for an owner and expiry. Visual-analysis and remediation outputs that remain manual or review-required must not be promoted as fixed.
- Preserve every v0.9.7 operator action below, including database backup, provider-key, proxy, cookie, LMS, and human-approval requirements.

## [0.9.7] - 2026-08-31

### Security

- Dashboard images upgrade Alpine OpenSSL runtime packages to the fixed versions before application artifacts are copied into the final image.
- Authentication bootstrap, magic-link consumption, and open-signup provisioning are serialized; CSRF and session-cookie scope are separated; routine authentication logs exclude sensitive identifiers; and provisioning audit attribution trusts only configured proxy networks.
- Analytics, evidence reports, current-compliance projections, provider configuration, report artifacts, and institution rollups are tenant-scoped. Workspace AI credentials are encrypted and resolved only for the authenticated workspace and requested purpose.
- General STEM remediation is source-bound and fail closed. Typed provenance, specialist verification, saved-file reverse verification, approval invalidation, and exact mixed-region composition prevent unsupported or ambiguous semantics from being published.

### Added

- General STEM semantics now covers scanned equations, ordered multi-equation screenshots, vector equations, handwritten mathematics, chemical formulas, molecular structures, commutative diagrams, and mixed visuals through bounded specialist contracts and independent verifiers.
- Mixed STEM composition accepts only a fully resolved typed region graph, preserves source rendering and metadata, orders specialist outputs deterministically, reopens and verifies the serialized candidate, and binds human approval to that exact candidate.
- Institution administrators can manage canonical regulatory profiles and workspace AI providers. Compliance evidence reports, current-state projections, institution coverage rollups, CVD metrics, durable weekly summaries, Brightspace course discovery, Blackboard signing keys, and verified CLI report retrieval extend operator and integration coverage.
- Separate API and worker liveness/readiness probes, privacy-bounded health metrics, and sustained Prometheus alerts cover unavailable APIs, missing worker heartbeats, expired leases, and stalled jobs with recovery notifications.

### Fixed

- PDF image XObject traversal rolls back safely, API keys remain usable at narrow widths, AI-provider controls stay inside their cards on phones, Microsoft OAuth tests exercise the real HTTP seam, and release image security checks inspect final installed Python packages rather than stale lower-layer attribution.
- Compliance deadlines come from canonical institution profiles, dashboard remediation outcomes come from durable jobs, and each document contributes only its latest verified state.

### Changed

- Release workflows use current Node 24-compatible dependency review, Docker Buildx, artifact, and Trivy actions pinned to reviewed commits.
- Open-core makes no implicit AI-vendor choice. Text generation, fallback, and semantic embedding are independently opt-in; executable tenant inference resolves the workspace's durable provider selection.
- CPU- and browser-intensive scans and remediations run only in the dedicated `python -m src.jobs.worker` service. API processes enqueue bounded jobs and remain independently responsive; Compose ships a single-job worker default, a 0.75-CPU quota, killable child-process execution, durable leases, and worker-specific health reporting.
- Breaking API change: `POST /education/multimedia/transcribe` now returns an
  HTTP `200` asynchronous scan handle instead of a terminal transcript/captions
  payload. Poll its authenticated `/education/scans/{scan_id}/progress` URL,
  then retrieve the result from `/education/scans/{scan_id}`.
- Breaking API change: Brightspace single-content and batch remediation now
  return HTTP `202` job descriptors. Clients must poll each authenticated
  `status_url` for the bounded terminal outcome and artifact reference.
- Breaking security and reliability change: the unauthenticated server endpoints
  `POST /education/focus-order/analyze` and
  `POST /education/focus-order/analyze-html` have been removed so API requests
  cannot launch Chromium. The independent CLI `focus` command and the
  worker/scanner FocusOrder capability remain available.

### Operator action required

- Drain active work and pause intake. Back up PostgreSQL and verify the restore path, then run `alembic upgrade head` explicitly and confirm the single head is `20260831_institution_scope`.
- Set and retain `BYOK_ENCRYPTION_KEY` before storing workspace provider credentials. Select `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, and `EMBEDDING_PROVIDER` deliberately; all three default to `none` where applicable.
- If Blackboard LTI is enabled, configure a matching RSA 2048+ signing-key pair. Review `TRUSTED_PROXY_CIDRS`, cookie domains, Brightspace OAuth origins, shared report storage, and the new worker resource and health limits before restarting services.
- Deploy API and worker from the same 0.9.7 release. Confirm API readiness, a fresh worker readiness result, queue age, failed or quarantined rows, and alert recovery before resuming intake.
- Update clients for the asynchronous multimedia and Brightspace responses and the removed unauthenticated focus-order HTTP endpoints. Unsupported or ambiguous STEM content remains open for human review; recognition alone never approves an artifact, and human acceptance stays bound to the exact verified candidate.
- Preserve every v0.9.6 operator action below, including the remediation timeout settings and deliberate handling of pre-v0.9.5 quarantined work.

## [0.9.6] - 2026-08-26

### Security

- Image-equation remediation is purpose-bound, source-validated, deterministically verified, confidence-capped, and always held for explicit human acceptance before approval or download.
- PDF table remediation binds visible cells to real marked content and verifies the saved structure before publication; ambiguous, ragged, merged, unbound, or excessive structures fail closed without mutation.
- Remediation publication revalidates tenant, approval, checksum, and artifact authority under the final lock, while Canvas and Brightspace enqueue failures return stable bounded error codes.

### Added

- Printed standalone equation images addressable by PDF occurrence identity can be recognized as LaTeX, converted to MathML, round-trip verified, and associated with `/Formula` content. Unsupported STEM visuals remain manual-review cases.
- Verified Table/TR/TH/TD remediation includes MCID/MCR and ParentTree associations, saved-output semantic verification, atomic rollback, and inclusive limits of 64 columns, 10,000 cells, and 200 tables.
- Canvas stored-content remediation now uses durable jobs, immutable bounded source snapshots, complete target fingerprints, persisted review evidence, restart recovery, and fenced writeback.

### Fixed

- OCR-generated searchable text survives into delivered PDFs; PDF-derived HTML is rebuilt through a passive allowlist and rejects active content, unsafe URLs, malformed images, and trailing-data polyglots.
- Direct, queued, and Brightspace PDF publication consumes the exact descriptor-bound output claim instead of reopening a mutable pathname.
- OneDrive receives tenant scope through `department_id`; invalid credential constructor wiring, reserved scan metadata use, asynchronous remediation CORS preflight, and LMS enqueue-error leakage are corrected.

### Changed

- Document remediation runs through the durable queue with active-job deduplication, bounded legacy waiting, tenant-fenced status/latest endpoints, artifact-gated downloads, and a killable subprocess with a terminal no-retry hard timeout.
- Release reruns isolate publication receipts by attempt while preserving the exact seven-file SBOM gate. Public PDF, Office, and LaTeX remediation guides now document supported boundaries and human-review outcomes.

### Operator action required

- Drain active work and pause remediation intake before upgrading. Back up PostgreSQL and verify the restore path, then run `alembic upgrade head` explicitly and confirm the single head is `20260825_canvas_queue`.
- Set `REMEDIATION_EXECUTION_TIMEOUT_SECONDS` and `REMEDIATION_TERMINATION_GRACE_SECONDS` for the deployment. Defaults are 1,800 seconds and 10 seconds; a hard timeout is terminal and is not retried automatically.
- Start API and worker from the same 0.9.6 release, confirm API health and a fresh worker heartbeat, then inspect queue age and failed/quarantined rows before accepting new work.
- Image-derived equations remain `ai_vision`, confidence `0.55`, and `needs_review=true`. Recognition alone never approves an artifact: a human acceptance must be persisted and revalidated before download or writeback.
- Preserve every v0.9.5 operator action below, including deliberate review and resubmission of any pre-v0.9.5 quarantined work.

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
