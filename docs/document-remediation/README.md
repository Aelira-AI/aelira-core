# Document remediation

Aelira Core scans and can remediate PDF, DOCX, PPTX, XLSX, and LaTeX content. This page is the canonical public guide to what the current open-source implementation does—and where a person still has to decide whether a change is correct.

> **Beta, not a compliance certificate.** A completed scan or remediated file is evidence from Aelira's checks, not proof that a document satisfies every applicable WCAG or format-specific requirement. Review and test the output with assistive technology and any validator required by your organization.

> **Release boundary (24 August 2026):** The PDF hardening described below is present on `main`, after the immutable v0.9.5 release. It is not part of v0.9.5. It can become part of a future release only after that release's gates pass; merging it created no release or deployment.

Choose a format guide: [PDF](pdf.md), [Office: DOCX, PPTX, XLSX](office.md), or [LaTeX](latex.md).

## Format support and maturity

“Maturity” below describes evidence in this repository, not feature parity or a support guarantee.

| Input | Primary scan output | Remediation output | Evidence-based maturity |
|---|---|---|---|
| `.pdf` | Stored issue list, score, detected structure, and an HTML representation | `.pdf` | Broad processor tests plus a fixture-backed scan → remediate → re-scan integration test. This is the strongest document-format evidence, but complex semantics remain review work. |
| `.docx` | Stored categorized issues, score, structure counts, and HTML representation | `.docx` | Processor and edge-case tests cover Word structures; remediator and route wiring are tested. There is no claim of PDF-level end-to-end parity. |
| `.pptx` | Stored slide issues, score, and slide structure summary | `.pptx` | Scanner has fixture-backed end-to-end tests plus animation/media tests. Remediation is intentionally partial; actual slide reading-order repair is not implemented. |
| `.xlsx` | Stored worksheet issues, score, and workbook structure summary | `.xlsx` | Focused integration tests cover pivot tables and conditional formatting; remediator and route wiring are tested. There is no full-workbook end-to-end parity claim. |
| `.tex` | Source issues plus equation conversion results, MathML/ARIA data, score, and HTML | `.tex` by default; optional `.html` and `.pdf` | Source remediation and conversion branches are tested. The large processor E2E module is opt-in, and PDF quality depends on the converter available at runtime. |

The API's LaTeX conversion route also accepts `.txt`, `.md`, and `.pdf`; those are inputs to that route, not additional source-remediation formats. A PDF submitted there is dispatched to the PDF processor/remediator rather than converted back to TeX.

## Scan and remediation boundaries

Scanning reports evidence; remediation changes only categories that a format remediator considers eligible and for which required location/context data exists.

| Format | Scanning detects or analyzes | Remediation may change |
|---|---|---|
| PDF | Text/OCR state, metadata and language, tag/structure conditions, headings/lists/tables, reading order, images/alt text, links, forms, bookmarks, and generated HTML | Language/title metadata, bookmarks, selected structure/heading/list/table tags, and alt text where the structure tree and issue metadata permit a safe target |
| DOCX | Headings, images/alt text, tables, fake lists, link text, language, title, font size, SmartArt, and embedded objects | Image alt text, heading styles, fake-list formatting, first-row table headers, link text, run language, and title |
| PPTX | Image alt text, text contrast, slide titles, optional image-of-text OCR, animations, and embedded media | Eligible image alt text, text color, and missing slide titles. A reading-order request currently adds guidance to notes and returns “not fixed”; it does not reorder slide XML. |
| XLSX | Sheet names, table/header structure, charts/images, merged cells, color-only cues, navigation/named ranges, pivot tables, and conditional formatting | Sheet names, table/header formatting, a missing chart title, text indicators for recognized colors, and frozen panes |
| LaTeX | Metadata/language, figures and captions, tables, equation labels/MathML/ARIA, color-only emphasis and possible contrast, links, and list structure | TeX source for supported language, figure, heading, table, title, equation/ARIA, color, and link cases; requested conversions are a separate post-remediation step |

A finding can remain manual because its category is unsupported, its target is ambiguous, required metadata is absent, AI was unavailable or disallowed, or verification did not pass. Counts for fixed, manual, failed, and skipped work are part of the remediation result; “suggested fix” text is not evidence that a mutation occurred.

## Deterministic and AI-assisted work

- **Deterministic/rule-based:** file parsing; pattern and structural checks; score calculations; issue routing; metadata/language changes; known heading/list/table/navigation changes; format serialization; artifact hashing and authorization.
- **Optional AI capability:** descriptions and alt text, validation of existing descriptions, inferred titles/headings, chart/figure descriptions, and natural-language equation labels or explanations can use a configured provider. “Optional” means these capabilities can be disabled and deterministic scanning does not require a model; it does not mean every current route defaults its AI flags off.
- **Fail-closed boundary:** generated semantic content is not replaced with a success-shaped placeholder when the provider fails. The issue remains manual. Provider use and purpose are tracked by the remediation route.

The actual API defaults and explicit controls are route-specific:

- Direct remediation's `RemediationOptions.use_ai` defaults to `true`. Send `{"use_ai": false}` to `POST /education/remediate/{scan_id}` (that is, set `use_ai=false` in remediation options) when only rule-based fixes are acceptable. LMS-backed remediation has a separate explicit-intent policy and does not inherit that direct-upload default.
- `POST /education/pdf/scan` has `enhance_descriptions` defaults to `true` and `generate_alt_text` defaults to `false`; set `enhance_descriptions=false` with `?enhance_descriptions=false` to disable description enhancement, and leave `generate_alt_text=false` to keep alt-text generation off.
- The DOCX and PPTX scan routes default both `generate_alt_text` and `validate_alt_text` to `false`. The XLSX route defaults `generate_chart_descriptions` and `generate_alt_text` to `false`.
- `POST /education/latex/scan` has `use_ollama` defaults to `true`; set `use_ollama=false` with `?use_ollama=false` to disable it.

The same source can therefore have deterministic findings but different drafted language when a model is enabled. Disabling AI does not make every issue auto-fixable.

## Format preservation

PDF, DOCX, PPTX, and XLSX remediators save the same primary format as their input. LaTeX preserves remediated `.tex` source as the default and can additionally request HTML or PDF. PDF remediation works from a private copy and refuses an output path that resolves to the input, so the original PDF remains immutable.

“Same format” does not mean byte-for-byte preservation. The format libraries rewrite packages/objects, and unsupported or uncommon constructs may be normalized or lost. Compare the original and output visually and structurally, retain the original, and pay particular attention to signatures, forms, embedded objects/media, macros, formulas, pivots, animations, and complex layout.

## Managed artifacts and review

The direct remediation API publishes a scan-bound managed artifact only when remediation succeeds, applies at least one fix, has zero manual issues and zero failed issues, produces an output file, and verification passes. If that publication gate is not met, do not expect an artifact ID. A published artifact includes filename, MIME type, size, SHA-256, expiry, review status, lifecycle status, and approval blockers. Artifact access is tenant- and scan-scoped; download verifies the stored object before streaming it.

A typical review is:

1. Complete a scan and call `POST /education/remediate/{scan_id}`.
2. Confirm the publication gate in the response: success, at least one fixed issue, zero manual and zero failed issues, and passed verification with an artifact ID.
3. Fetch `GET /api/reviews/{scan_id}`. For every `ScanFix` that still needs a decision, call `POST /api/reviews/{scan_id}/fixes/{fix_id}` to approve, reject, or edit it. All fix decisions must be terminal (`auto_approved`, `approved`, or `rejected`), with at least one accepted fix (`auto_approved` or `approved`), before artifact approval.
4. Read `GET /education/scans/{scan_id}/artifacts/{artifact_id}` and inspect `approval_blockers` and `can_approve`. Do not attempt approval while blockers remain.
5. Download the verified review copy from `GET /education/scans/{scan_id}/artifacts/{artifact_id}/download` and review the document itself.
6. Call `POST /education/scans/{scan_id}/artifacts/{artifact_id}/approve` only when the fix review is terminal, at least one fix is accepted, and `can_approve` is true; otherwise call `POST /education/scans/{scan_id}/artifacts/{artifact_id}/reject`.

The legacy scan download routes remain implemented: `GET /education/scans/{scan_id}/remediated` and `GET /education/scans/{scan_id}/remediated/formats`. Prefer managed artifact metadata when the remediation response supplies an artifact ID because it carries integrity and review state.

Managed PDF publication has a stricter byte-identity boundary on `main`. The remediator creates PDF and optional HTML candidates in a private directory opened through retained directory descriptors, validates those candidates, and snapshots the exact validated PDF into a private, unlinked output claim before exposing the final pathname. The claim owns one read-only, non-inheritable descriptor; it has a single owner, is not serialized, and rejects copy, deep-copy, and pickle operations while live. Internal PDF verification and direct, queued, and Brightspace managed publication consume the exact claimed stream instead of reopening `output_file`.

`RemediationArtifactService` recomputes and checks size, SHA-256, MIME type, scan type, and filename while publishing that stream. Publication remains DB-first: a `staging` row and private publication token identify the exact attempt before bytes become `available`. Cancellation, ownership-fence loss, and completion-commit failure abort only that staging publication by artifact ID plus publication token. These controls narrow pathname races; they do not promise one-and-only-one external effects. Path-oriented compatibility remains for non-PDF formats and library consumers, but a managed PDF treats the output claim as authoritative.

Cleanup is part of the outcome. Candidate, serialization, working-copy, descriptor-close, or staging-abort failures produce an explicit cleanup warning or failed result, with a retained path or artifact ID when manual recovery is needed; no such failure is reported as success. See the self-hosting guide for recovery and platform limits.

See the [artifact service](../../src/services/remediation_artifact_service.py), [artifact route implementation](../../src/api/education/remediation_routes.py), and [artifact service tests](../../tests/test_remediation_artifact_service.py).

## Installation and dependencies

The supported repository installation is the full pinned set in [`requirements.txt`](../../requirements.txt); the annotated inventory is [`docs/DEPENDENCIES.md`](../DEPENDENCIES.md). Python is declared as 3.12 or newer in [`pyproject.toml`](../../pyproject.toml). The API-backed CLI requires Node 20 or newer and is packaged separately under the MIT license.

Major document dependencies are:

- PDF: pikepdf, PyMuPDF, pypdf, pdfplumber, OCRmyPDF, pdf2image, and pytesseract. OCR/conversion paths also need the corresponding system tools (Tesseract, Ghostscript/qpdf, and in some environments Poppler).
- Office: python-docx, python-pptx, openpyxl, Pillow, lxml, and optional Tesseract for PPTX image-of-text detection.
- LaTeX: latex2mathml for in-process equation conversion. Optional file conversion probes for LuaLaTeX/pdflatex, LaTeXML/latexmlpost, Pandoc, and Playwright/Chromium; unavailable tools select a weaker fallback or no converted file.

A model is not required for deterministic scanning. AI-assisted fields require a configured provider and may send document-derived content to that provider; use a local provider or keep AI options off when that egress is not acceptable.

## Entry points

### HTTP API

The scan uploads are asynchronous and return a `scan_id`:

- `POST /education/pdf/scan`
- `POST /education/word/scan`
- `POST /education/powerpoint/scan`
- `POST /education/excel/scan`
- `POST /education/latex/scan`

Poll `GET /education/scans/{scan_id}/progress`, then fetch `GET /education/scans/{scan_id}`. Authentication uses an `Authorization` header with a Bearer API key outside explicitly configured development mock auth. Routes are registered under `/education`, not `/api/education`.

### CLI

The API-backed command sources verify these command names:

```console
aelira scan pdf document.pdf --format json
aelira scan docx document.docx --format json
aelira scan ppt slides.pptx --format json
aelira scan xlsx workbook.xlsx --format json
aelira scan latex document.tex --format json
aelira remediate <scan_id> --download
```

The first four scan commands and remediation command use the current API routes. The current `aelira scan latex` source sends JSON while the server's current route accepts an uploaded file; use the multipart HTTP example in [the LaTeX guide](latex.md) until that client/server mismatch is resolved.

### Direct library

Only the PDF direct-library path has a maintained runnable example: [`examples/scan_pdf_direct.py`](../../examples/scan_pdf_direct.py). Processors and remediators are importable classes, but their return shapes and configuration are internal Python interfaces rather than a separately versioned SDK contract.

## Limitations and human review

- Aelira does not decide whether generated alt text, a heading hierarchy, a table header, a chart description, or a reading order conveys the author's intended meaning.
- It does not claim all-format parity. Scanner findings can outnumber safe mutations, and focused test depth differs by format.
- PPTX reading order is not structurally auto-repaired. XLSX chart remediation may add a chart title; it should not be described as guaranteed chart alt text.
- Office serialization can affect macros and uncommon package parts; only `.docx`, `.pptx`, and `.xlsx` are accepted by their document scan routes, not legacy `.doc`, `.ppt`, or `.xls` files.
- OCR can misread scans. PDF tag repair cannot infer every semantic relationship in a complex layout.
- LaTeX HTML and PDF are separate conversion paths. LuaLaTeX/tagpdf, LaTeXML/Playwright, and pdflatex fallbacks have different accessibility properties.
- Validation in this repository is not a legal opinion, accessibility audit, or substitute for testing with people and assistive technology.

## Source and test evidence

| Area | Source | Tests |
|---|---|---|
| PDF | [processor](../../src/education/pdf_processor.py), [remediator](../../src/education/remediation/pdf_remediator.py), [structure writer](../../src/education/remediation/pdf_structure.py), [output claim](../../src/education/remediation/output_claim.py) | [processor E2E](../../tests/test_pdf_processor_e2e.py), [scan/remediate/re-scan](../../tests/test_pdf_remediation_integration.py), [OCR delivery](../../tests/test_pdf_ocr_remediation.py), [accessible HTML](../../tests/test_pdf_accessible_html_escaping.py), [output claim](../../tests/test_remediation_output_claim.py), [structure](../../tests/test_pdf_structure.py), [table headers](../../tests/test_pdf_table_headers.py) |
| DOCX | [processor](../../src/education/docx_processor.py), [remediator](../../src/education/remediation/docx_remediator.py) | [no-default-style](../../tests/test_docx_no_default_style.py), [SmartArt](../../tests/test_docx_smartart.py), [embedded objects](../../tests/test_docx_embedded_objects.py) |
| PPTX | [processor](../../src/education/pptx_processor.py), [remediator](../../src/education/remediation/pptx_remediator.py) | [processor E2E](../../tests/test_pptx_processor_e2e.py), [animations](../../tests/test_pptx_animations.py), [embedded media](../../tests/test_pptx_embedded_media.py) |
| XLSX | [processor](../../src/education/xlsx_processor.py), [remediator](../../src/education/remediation/xlsx_remediator.py) | [pivot tables](../../tests/test_xlsx_pivot_tables.py), [conditional formatting](../../tests/test_xlsx_conditional_formatting.py), [small-document scoring](../../tests/test_small_doc_scoring.py) |
| LaTeX | [processor](../../src/education/latex_processor.py), [remediator](../../src/education/remediation/latex_remediator.py), [converter](../../src/education/remediation/latex_converter.py) | [processor E2E (opt-in)](../../tests/test_latex_processor_e2e.py), [PDF/UA pipeline](../../tests/test_latex_pdf_ua.py), [download formats](../../tests/test_remediation_downloads.py), [siunitx](../../tests/test_latex_siunitx.py) |
| API/CLI | [scan routes](../../src/api/education/scan_routes.py), [remediation routes](../../src/api/education/remediation_routes.py), [artifact service](../../src/services/remediation_artifact_service.py), [CLI command sources](../../cli/src/commands) | [artifact service](../../tests/test_remediation_artifact_service.py), [direct PDF publication](../../tests/test_direct_pdf_claim_publication.py), [queued PDF publication](../../tests/test_queued_pdf_output_claim.py), [Brightspace PDF publication](../../tests/test_brightspace_pdf_output_claim.py), [outcome atomicity](../../tests/test_remediation_outcome_atomicity.py), [purpose-bound clients](../../tests/test_remediation_purpose_clients.py), [fail-closed alt text](../../tests/test_alt_text_fail_closed.py) |
