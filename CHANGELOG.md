# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Equation canonicalisation no longer treats a LaTeX brace group around a single item as part of the meaning, so `x^{2}` and `x^2` canonicalise identically and handwritten equation agreement no longer sends them to manual review because of braces alone. Different readings still differ, and a group around a lone operator or around several factors is kept. The canonicaliser also accepts binomial coefficients (`\binom`, `\dbinom`, `\tbinom`): `linethickness` is allowed on `mfrac` only, and `mstyle` is allowed with only `displaystyle` and `scriptlevel`, which also admits `\dfrac`, `\tfrac`, `\cfrac` and the `\displaystyle` family; an `mstyle` that carries colour or size (`\color`, `\large`) is still rejected. Compatibility: the canonical MathML form is now version 2 and the handwritten consensus policy is `handwritten-equation-consensus-v2`. Handwritten equation evidence stored by earlier versions, and printed-equation contracts whose stored MathML contained a collapsible group, now read as invalid contracts and block approval. Operator action: run remediation again on the affected documents to regenerate those findings. No database migration is involved ([#691](https://github.com/Aelira-AI/aelira-core/issues/691), [#690](https://github.com/Aelira-AI/aelira-core/issues/690)).

## [0.9.13] - 2026-10-20

See the [release notes](docs/releases/v0.9.13.md) for operator actions and
evidence limits.

### Added

- An isolated upgrade and restoration rehearsal automates the documented backup procedure, verifies persisted scan outcomes and artifact hashes, and records exact image manifests and platform identities in a validated receipt. It uses fresh local test resources and preserves the remaining publication, platform and prerelease checks as separate release gates ([#663](https://github.com/Aelira-AI/aelira-core/issues/663)).
- Local PPTX uploads accept a complete, source-bound reviewer shape order through the API, queue the repair and verify the saved package before offering a working file. Unsupported shapes and cloud-backed requests remain refused ([#662](https://github.com/Aelira-AI/aelira-core/pull/662)).
- Dashboard journeys exercise document, web, media and synthetic Canvas paths. Canvas cloud-file identities now retain course and content type without weakening other providers' uniqueness ([#646](https://github.com/Aelira-AI/aelira-core/pull/646)).

### Changed

- Playwright moves from 1.63.0 to 1.64.0, with its newer Chromium, and the API runtime stage uses a `debian:trixie-slim` base. Chromium-rendered equation images and LaTeX HTML-to-PDF output can differ byte for byte from v0.9.12; compare renders within one release.
- Four merged dependency groups update five CLI packages and its installation lockfile, eight dashboard packages, Pydantic and its required core version, and selected native-fixture/font-corpus pins with SoupSieve selector consumers. Each passed CI at its reviewed head before merging ([#652](https://github.com/Aelira-AI/aelira-core/pull/652), [#653](https://github.com/Aelira-AI/aelira-core/pull/653), [#654](https://github.com/Aelira-AI/aelira-core/pull/654), [#655](https://github.com/Aelira-AI/aelira-core/pull/655)); coordination and release-note inclusion are tracked in [#665](https://github.com/Aelira-AI/aelira-core/issues/665). Embedded-library assessment has separate gates.
- Hugging Face Hub is updated to 2.2.0 and Tokenizers to 0.23.3, which accepts Hub 2 and later. Hub 2 adds `httpx2`, `httpcore2` and `truststore` to the pinned runtime set. A real faster-whisper `tiny.en` download, transcription and `HF_HUB_OFFLINE=1` cached load now run in the built API image on both architectures ([#560](https://github.com/Aelira-AI/aelira-core/issues/560)).

### Security

- The dashboard image installs Alpine `libx11` 1.8.14-r0 for CVE-2026-88806 ([#686](https://github.com/Aelira-AI/aelira-core/pull/686)).
- Merged [#640](https://github.com/Aelira-AI/aelira-core/pull/640) rebuilds Debian libxml2 with the maintained CVE-2026-6653 patch and verifies the installed package, loaded native consumers and shipped image layers. Seven other libxml2 advisories remain affected and disclosed; broader advisory acceptance is still open.
- Three MEDIUM scanner findings without a Debian fix are reviewed and disclosed in the [advisory record](docs/security/known-unfixed-advisories-0.9.12.md#medium-severity-advisories-reviewed-in-the-0913-candidate): CVE-2026-107161 (Cyrus SASL DIGEST-MD5, plugin not installed), CVE-2026-85091 (zlib, vulnerable code not present in Debian's 1.3.1) and CVE-2026-27171 (zlib `crc32_combine`, no caller in the image). The record also lists the zlib copies bundled in lxml, PyMuPDF and Node.js, including an older Chromium zlib build inside Node.js.
- Source-built PyAV and OpenCV use hash-verified FFmpeg 9.0.2 instead of their published wheels' private stacks. Native checks cover library binding, audio decode, seven video codec families and scene boundaries; wider media and embedded-library assessment remains open ([#669](https://github.com/Aelira-AI/aelira-core/pull/669), [#675](https://github.com/Aelira-AI/aelira-core/pull/675)).
- Python's matched `pyexpat` and `_elementtree` modules are rebuilt against Debian Expat 2.9.0 and checked in both native images. This does not close all scanner and disposition gates ([#671](https://github.com/Aelira-AI/aelira-core/pull/671)).
- The API image builds Pillow 12.3.0 from its source release with libtiff 4.7.2 built from a hash-verified source archive and linked statically, replacing the libtiff 4.7.1 copy bundled in the published wheels, which carries CVE-2026-36849. JPEG, JPEG 2000, PNG, WebP, AVIF, TIFF, FreeType, LittleCMS and Raqm text shaping remain available, with their shared libraries supplied by Debian packages. The build and the final image check the libtiff version and the absence of the wheel-private library directory ([#644](https://github.com/Aelira-AI/aelira-core/issues/644), part of [#583](https://github.com/Aelira-AI/aelira-core/issues/583)).
- `pi_heif` is no longer installed. ocrmypdf 17 does not depend on it, and its optional HEIF support loads a different package that is not installed, so the bundled libheif 1.23.0 and libde265 1.1.0 copies were never used. OCR of a PDF is exercised in the built image on both architectures ([#644](https://github.com/Aelira-AI/aelira-core/issues/644), part of [#583](https://github.com/Aelira-AI/aelira-core/issues/583)).
- The API image no longer installs `libxml2-utils` (`xmllint`, `xmlcatalog`) and removes `xdg-utils` (`xdg-email`, `xdg-open`), which nothing in the application, the TeX and LaTeXML conversion paths or ImageMagick runs. `xdg-utils` is a hard dependency of `texlive-base`, so it is purged with dpkg in the same layer; a build-time and CI check asserts both packages and their executables stay absent. The CVE-2022-4055 disposition is removed. libX11, libXrender, cJSON and GnuTLS stay because Chromium, FFmpeg, Ghostscript, ImageMagick and poppler link them directly.
- The API image builds psycopg2-binary 2.9.13 from its hash-verified source archive against Debian's libpq instead of installing the published wheel, which bundles its own libpq, OpenSSL and Kerberos copies in `psycopg2_binary.libs`. The package name and version are unchanged. The final-image package check now fails if a bundled library directory exists or if the extension module resolves libpq, libssl or libcrypto outside `/usr/lib`, and CI runs a PostgreSQL 16 smoke test against the built image on amd64 and arm64 ([#588](https://github.com/Aelira-AI/aelira-core/issues/588)).
- Runtime Dockerfile images pin Debian's fixed `libexpat1` 2.9.0-1~deb13u1 (DSA-6566-1), so the five dispositions for `libexpat1` 2.8.3 are removed from the current image policy. The Docker CI job records the installed package list and a HIGH and CRITICAL inventory of the built API image, fails when `libexpat1` is older than 2.9.0, and applies the same disposition gate that publication uses.
- The v0.9.12 notes and the [known unfixed advisories](docs/security/known-unfixed-advisories-0.9.12.md) said the Expat bundled in Python 3.14.8 includes the fixes. It lacks the fix for CVE-2026-77214, which first ships in Expat 2.9.0. The historical advisory table remains in the disclosure with a correction and a bounded `pyexpat.c` reachability assessment. Merged [#671](https://github.com/Aelira-AI/aelira-core/pull/671) rebuilds the Python Expat modules against Debian's fixed library for subsequent images; final candidate acceptance remains open in [#589](https://github.com/Aelira-AI/aelira-core/issues/589).
- The same document records the libheif 1.23.0 and libde265 1.1.0 copies bundled in the v0.9.12 `pi_heif` wheel as a historical gap, removed from images built for the next release ([#583](https://github.com/Aelira-AI/aelira-core/issues/583)). It also records that the OpenSSL copies bundled in the `psycopg2-binary` wheel shipped in v0.9.12 and are replaced from the next release by a source build ([#645](https://github.com/Aelira-AI/aelira-core/issues/645), [#588](https://github.com/Aelira-AI/aelira-core/issues/588)).

### Fixed

- Media remediation now delivers its generated WebVTT captions as a downloadable file for review. Previously the captions were generated and then discarded, because no saved-file check existed for them and remediation published only verified fixes. The saved file must be well-formed WebVTT whose cues match the generated transcription and fit the media; that checks structure, not accuracy, so the captions are always flagged for human review. Only the missing-captions finding counts as fixed; caption-quality findings about the source transcription stay manual, and speech recognition runs once per job. Cue text is escaped for WebVTT. Transcripts and audio descriptions are not included, audio descriptions still need an AI provider, and flashing safety remains unassessed. A new migration, `20261020_media_captions`, lets media scans hold caption artifacts. Caption files are download-only: write-back to cloud storage or a Canvas course is refused, and approving one does not mark the media for write-back. LMS-bound media jobs without an AI provider now also attempt captioning, which the alt-text switch had blocked.
- Open [#681](https://github.com/Aelira-AI/aelira-core/pull/681) supplies the media assessment change: unmeasured video remains unassessed without an overall score or unverified credit, and mixed LMS averages are withheld when a scanned item lacks a measured score. Linked null-score Canvas scans show “Unassessed”; Canvas content without a linked scan shows “Not scanned”. Numeric zero stays measured. Draft [#682](https://github.com/Aelira-AI/aelira-core/pull/682) adds the completed null-score Brightspace row label “Unassessed”. Final-image and provider acceptance remain pending.
- The PDF, DOCX and PPTX remediators no longer fall back to text-only image descriptions. When an image cannot be analysed from its own pixels the finding stays unresolved for human review. The v0.9.12 notes stated that text-only fallbacks were removed, which overstated the change for these three remediators ([#636](https://github.com/Aelira-AI/aelira-core/issues/636)).
- Canvas course scans can retain a file and another content item with the same native ID. The original cloud-file constraint rejected that valid pair despite the newer course/content identity index, causing the durable scan to fail. Uniqueness for other cloud providers remains enforced ([#650](https://github.com/Aelira-AI/aelira-core/issues/650)).

### Operator action required

- Before deploying an approved candidate, pause intake and writers, drain jobs, stop API and worker, and verify a coordinated backup of PostgreSQL, uploads, managed artifacts, private configuration and encryption keys. Use matching API, worker and dashboard images, run `alembic upgrade head` explicitly, and verify the combined tree's single head `20261020_media_captions` after `20261018_media_truth`, `20261017_canvas_scope` and `20260928_pdf_edit_pub`. Keep valid Canvas identities; the Canvas downgrade can refuse duplicate native IDs across courses or content types. Roll back with the matched pre-upgrade database and files, not by forcing deletion. Reconcile external writebacks separately and require human approval of the exact working file before publication.

## [0.9.12] - 2026-10-15

See the [release notes](docs/releases/v0.9.12.md) for operator actions, limits and publication requirements.

### Security

- LaTeX conversion runs every LaTeX tool and pandoc with a minimal environment and confined to its own working files and the submitted source, fixing [GHSA-53gp-pqcg-h8q4](https://github.com/Aelira-AI/aelira-core/security/advisories/GHSA-53gp-pqcg-h8q4).
- Alert email is sent only to the department's configured recipients using the fixed alert templates. An unreleased endpoint that sent custom subjects to arbitrary recipients has been removed.
- Outbound HTTP connections are pinned to validated public addresses. Browser scan requests use the guarded transport; unsupported frames, popups, workers and alternate network channels leave required checks incomplete rather than producing a verified score ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- New upload writes are confined through directory descriptors. OAuth callback destinations are constrained, and affected API errors and persisted diagnostics omit unfiltered exception details ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Image descriptions require verified source pixels and the workspace vision provider. Text-only fallbacks are removed; missing images remain unresolved ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Dashboard API keys are held in memory instead of persistent browser storage. Cookie-session recovery remains available; API-key users re-enter their key after a full reload ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Cache deletion accepts canonical provider filters, and HTTPX request-detail logs that could expose caller URL paths or queries are suppressed ([#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Dependency updates include security fixes for JWT handling, PDF parsing, dashboard HTML sanitisation, CLI brace expansion and KaTeX development tooling. Existing dependency audit and image gates remain required ([#518](https://github.com/Aelira-AI/aelira-core/pull/518), [#540](https://github.com/Aelira-AI/aelira-core/pull/540)).
- Production API images omit pip and its vendored libraries after dependency installation. Pa11y's locked runtime excludes the vulnerable FTP and ZIP downloader chain while retaining Pa11y 9.0.1, HTML_CodeSniffer 2.5.1 and the native Chromium checks ([#575](https://github.com/Aelira-AI/aelira-core/issues/575)).
- Dashboard images install the available pcre2 and tiff security updates on the pinned nginx base ([#574](https://github.com/Aelira-AI/aelira-core/issues/574)).
- Media decoding accepts only local files in the supported container formats (MP4/MOV/M4A, WebM/MKV, AVI, MP3, WAV, Ogg, FLAC) and mainstream codecs. FFmpeg, ffprobe and OpenCV capture refuse other formats and codecs before an unrelated decoder runs, and audio is converted to WAV before transcription. Media using an unsupported codec is reported as a processing failure.
- Publication rejects every reported HIGH or CRITICAL image vulnerability regardless of fix availability, retaining the unsuppressed native-image inventory before rejection. The only exception is a distribution-package finding with no vendor fix and a reviewed, expiring, disclosed disposition: HIGH findings as affected, or HIGH and CRITICAL findings as not affected with a stated justification. Known affected embedded libraries remain separate release blockers ([#580](https://github.com/Aelira-AI/aelira-core/issues/580)).
- The 64 HIGH and one CRITICAL Debian advisories without a distribution fix are reviewed and disclosed in [known unfixed advisories](docs/security/known-unfixed-advisories-0.9.12.md): 55 not affected in this image and 10 affected (libxml2, ImageMagick and libtiff through LaTeX conversion).
- The API image uses Python 3.14.8, whose bundled Expat 2.8.5 fixes the Expat advisories affecting the copy Python's XML parsers use. lxml is built from its pinned source release against hash-verified libxml2 2.15.4 and libxslt 1.1.45, fixing libxml2 advisories that affect the libxml2 bundled in the published lxml wheels.

### Added

- The dashboard can prepare bounded PDF heading-level, sibling-order and simple first-row column-header edits from verified structure targets. Saves create immutable, pending-review candidates with source checksums and durable concurrency guards; they do not approve output or write it back to an LMS. Unsupported structures remain refused ([reading-order and editing guide](docs/reading-order-review.md)).
- Supported multiline PDF title groups can become a complete source-bound H1–H6 candidate. The editor retains actual sparse structure paths and creates a new pending-review artifact for each accepted operation ([#564](https://github.com/Aelira-AI/aelira-core/pull/564)).
- The PPTX direct-library remediator can save an explicitly accepted, source-bound shape order for supported slides, verifying unchanged package parts and the reopened output. This does not add an API/queue reviewer-input workflow or establish PowerPoint/assistive-technology acceptance ([#501](https://github.com/Aelira-AI/aelira-core/pull/501)).
- A bounded LaTeX project ZIP workflow preserves original member bytes, inventories literal dependencies and exposes checksum-bound original retrieval and accepted HTML candidates. Unsupported or incomplete projects retain explicit diagnostics; multi-file source remediation and project PDF output remain outside this workflow ([project workflow](docs/testing/latex-source-projects.md)).
- Workspace administrators can inspect and edit saved Ollama text, code and vision model identifiers, or disable workspace AI while retaining configuration. Model selection does not download models, and the connection test exercises only the saved text model ([local AI settings](docs/deployment/local-ai-models.md#workspace-settings)).

### Fixed

- PDF scans keep rule-derived scores separate from optional AI review of existing descriptions. Fresh source/output comparisons bind the measurements to exact checksums; changed scoring baselines are labelled explicitly.
- PDF repair records source-bound image occurrences, bounded reading-order and link repairs, and saved-file evidence. Unreadable font mappings stop unsafe processing; reviewed recovery checks preserved text, paint and geometry.
- Heading checks require complete nonempty source ownership; an empty tag, a later H1 or ambiguous replacement semantics cannot clear the first-heading rule. Scanning and repair share bounded regional reading-order rules and actual page width, with reopened occurrence and graph checks. Mixed content arrays remain immutable and ambiguous layouts remain explicit review requirements ([#564](https://github.com/Aelira-AI/aelira-core/pull/564)).
- Completed remediation results group applied changes, pending human review and remaining manual work without a misleading active progress bar. Recorded refusal reasons, attempts and next steps remain visible; absent historical evidence is labelled unavailable.
- Verified improved PDFs remain downloadable while manual work remains. Reviewed rebuilding applies accepted changes and excludes rejected changes; publication and queued writeback recheck current file identity, approval and scope ([reviewed working files](docs/document-remediation/reviewed-pdf.md)).
- Saved manual PDF edits inherit prior repair receipts only when reopened bytes establish preservation. Conflicting heading edits, unproved order/table scopes and historical copied receipts block approval while candidates remain downloadable. Earlier job scores remain tied to the earlier output ([#572](https://github.com/Aelira-AI/aelira-core/issues/572)).
- Short direct-text PDFs no longer inherit the OCR output minimum. Incomplete required PDF checks expose bounded manual-review reasons and withhold unverified scores ([#384](https://github.com/Aelira-AI/aelira-core/pull/384)).
- Missing remediation confidence stays unknown across persistence, review, aggregation and exports; genuine zero scores remain zero. Unknown values cannot pass numeric batch approval. Historical generic AI defaults are conservatively corrected with audit records and invalidated approval bindings ([confidence and migration guidance](docs/document-remediation/confidence.md)).
- Analytics exports preserve measured zero and label absent scores `Not assessed`; alt-text quality aggregates use recorded finite values and leave unavailable averages and the legacy conformance-rate field null. CSV lengths count UTF-8 bytes, and route filters/pagination reject invalid bounds ([#439](https://github.com/Aelira-AI/aelira-core/pull/439)).
- Scan records retain observed Ollama usage rather than estimating it from options or object counts; absent measurements stay unknown ([#392](https://github.com/Aelira-AI/aelira-core/pull/392)).
- LaTeX handling preserves complete supported math input, authored metadata and verified figure/table relationships instead of fabricating defaults or treating conversion as accessibility evidence. Compilation failures and incomplete exports retain diagnostics and cannot publish a partial PDF ([PDF validation boundaries](docs/testing/latex-pdf-validation.md), [authored relationships](docs/testing/latex-authored-relationships.md)).
- LaTeX converter selection records the compatibility decision, preprocessing and source/equation provenance without silently trying another HTML converter after failure. Bounded saved HTML exports use native non-table equation layout and verified language fixes, preserve durable conversion receipts, and keep refused findings unresolved with zero fixed credit. HTML has no verified accessibility score; companion stylesheets remain unpackaged ([#479](https://github.com/Aelira-AI/aelira-core/pull/479), [#532](https://github.com/Aelira-AI/aelira-core/pull/532)).
- Google and Microsoft routes return actual binary downloads/exports and persisted scan/remediation job identities, refresh credentials and clean temporary storage. Legacy server-local upload requests return unsupported status; Google exports reject the obsolete `output_path` field. Interrupted Microsoft subscription mutations retain a pending reconciliation marker ([#437](https://github.com/Aelira-AI/aelira-core/pull/437), [#438](https://github.com/Aelira-AI/aelira-core/pull/438)).
- Integration status reads persisted provider profiles, and provider queue metrics exclude other providers' jobs. Webhook enqueue and local subscription deactivation preserve scoped stored state; local deactivation does not revoke remote provider access ([#436](https://github.com/Aelira-AI/aelira-core/pull/436)).
- Account deletion confirmations and administrator invitations await required delivery. Account lifecycle changes commit account, session and audit updates together; failed invitation delivery remains visible for retry. Notification preferences and alert recipient contracts preserve stored state ([#434](https://github.com/Aelira-AI/aelira-core/pull/434), [#433](https://github.com/Aelira-AI/aelira-core/pull/433), [#431](https://github.com/Aelira-AI/aelira-core/pull/431), [#432](https://github.com/Aelira-AI/aelira-core/pull/432)).
- Dashboard review selection survives refresh and narrow-screen controls remain usable. LMS policy denials provide actionable recovery without changing authorization ([#488](https://github.com/Aelira-AI/aelira-core/pull/488), [#495](https://github.com/Aelira-AI/aelira-core/pull/495)).
- Cancelled CLI prompts stop before dispatching operations. SARIF validation uses AJV 8 with explicit format checks ([#472](https://github.com/Aelira-AI/aelira-core/pull/472), [#344](https://github.com/Aelira-AI/aelira-core/pull/344)).
- Development setup, non-root upload storage, nginx route forwarding, encoded database URLs and checksum-verified Piper voice installation match the current runtime configuration ([#398](https://github.com/Aelira-AI/aelira-core/pull/398), [#386](https://github.com/Aelira-AI/aelira-core/pull/386), [#421](https://github.com/Aelira-AI/aelira-core/pull/421)).

### Changed

- Compatible runtime, CLI, dashboard and development dependencies are reconciled into complete lockfiles. Four Actions retain verified full commit pins, and the font-recovery corpus's strict runtime expectation matches the reviewed requirements. Incompatible PyAV and Hugging Face Hub major upgrades remain tracked separately ([#563](https://github.com/Aelira-AI/aelira-core/pull/563), [#560](https://github.com/Aelira-AI/aelira-core/issues/560), [#562](https://github.com/Aelira-AI/aelira-core/issues/562)).
- Result pages link directly to individual change review and the improved working file. Publication approval remains separate from download and writeback. Unsupported Brightspace binary writeback is reported as skipped with a manual-download path.
- Default dashboard logos use bundled current tagline-free assets. Institution logo and brand-name overrides remain supported.
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
- Production API images no longer include pip. Add or change Python dependencies by rebuilding from reviewed requirements; the development image retains its installer. See the [Pa11y runtime contract](pa11y-runtime/README.md) before changing its dependency override.
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
