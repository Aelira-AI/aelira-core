# PDF scanning and remediation

PDF has the deepest end-to-end document-remediation evidence in this repository. That does not turn heuristic structure inference or a successful re-scan into a PDF/UA certificate.

> **v0.9.7 boundary:** The immutable-source OCR, accessible-HTML sanitization, embedded-image validation, and exact-byte managed-publication controls on this page are included in v0.9.7. They were not part of v0.9.5.

## Verified capabilities

The [PDF processor](../../src/education/pdf_processor.py) can extract text, use OCR for image-only material, inspect metadata/language and tag/structure conditions, detect headings/lists/tables, analyze reading order, inspect images and links/forms, calculate a score, and produce an HTML representation. Optional image descriptions and enhanced fix explanations use a configured AI provider.

The [PDF remediator](../../src/education/remediation/pdf_remediator.py) can make bounded changes when the issue and PDF expose a safe target: document language and title, bookmarks, selected structure/heading/list/table tags, and figure alt text. The [structure helper](../../src/education/remediation/pdf_structure.py) performs direct structure-tree work with pikepdf.

The remediator stages a private working copy, so the original PDF remains immutable. It profiles direct text and image presence per page. Zero-text image pages are eligible for mixed-safe English OCR while pages with usable text pass through; OCR-generated searchable text is preserved in the delivered PDF and checked again by direct extraction from the output candidate. A blank page without images does not trigger OCR.

OCR and rewrite decisions fail closed. Signed PDFs, XFA forms, indeterminate signature inspection, declared non-English documents that need OCR, image pages with partial direct text below the safe threshold, missing OCR support, OCR refusal for tagged or prior-OCR input, and OCR output without usable per-page text are refused for manual handling. A failed candidate generation or validation does not replace a prior valid output at the destination.

When remediation produces an accessible-HTML alternative, PDF-derived title and alt text are normalized and escaped for their text or attribute contexts. PyMuPDF page fragments are parsed and canonically rebuilt through a passive allowlist: active elements, event attributes, inline styles, unsafe URLs, comments, blocked content, and malformed structures are removed or normalized rather than copied through.

Embedded images in that HTML accept only fully validated PNG and JPEG data URLs. Raw, encoded, and decoded size limits apply before Pillow structural verification, an independent reopen, and full pixel loading; dimension and pixel bounds apply. PNG and JPEG terminal markers must occur at exact EOF. Trailing data, polyglots, format spoofing, corrupt payloads, decompression risks, and synthetic relative image requests are rejected.

The fixture-backed [integration test](../../tests/test_pdf_remediation_integration.py) scans a PDF, applies rule-based remediation, writes a PDF, re-scans it, and requires fewer findings. Other tests exercise structure, tagged OCR, and table headers.

## What it does not promise

- It does not certify PDF/UA or WCAG conformance, and external Matterhorn checks in the integration test are deliberately non-fatal.
- It cannot safely infer every relationship, reading order, table association, form label, or meaningful image description.
- A missing/incomplete structure tree can prevent targeted changes. Some structure work is available only when pikepdf and required content references are present.
- OCR output can be wrong and must be proofread.
- OCR is English-only when remediation must add a text layer. Declared non-English inputs and indeterminate language inspection fail closed rather than receiving English OCR.
- Remediation can leave manual or failed issues; a suggested fix is not an applied fix.
- The output is another PDF, not the original bytes. Signed PDFs and XFA forms are refused; interactive behavior, complex forms, unusual encodings, and layout still require regression review.

## Dependencies

Install the pinned Python set from [`requirements.txt`](../../requirements.txt). PDF paths use pikepdf, PyMuPDF, pypdf, pdfplumber, OCRmyPDF, pdf2image, Pillow, and pytesseract. OCR and rendering also depend on system tools such as Tesseract, Ghostscript/qpdf, and, for pdf2image in common installations, Poppler. See the [dependency inventory](../DEPENDENCIES.md).

No model is needed for the deterministic scan below. Enable AI options only after configuring an acceptable local or remote provider.

## Quick start

### CLI scan

Start Aelira Core, configure the CLI API key, then run the implemented `scan pdf` command. Pass `--api-url` explicitly for a non-default deployment:

```console
npm install -g @aelira/cli
aelira config set api-key "$AELIRA_API_KEY"
aelira scan pdf document.pdf --api-url http://localhost:8000 --format json --output pdf-scan.json
```

The command posts to `POST /education/pdf/scan`, receives a `scan_id`, polls progress, and writes the completed stored scan result as JSON. It does not remediate the file.

### HTTP scan

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $AELIRA_API_KEY" \
  -F "file=@document.pdf" \
  http://localhost:8000/education/pdf/scan
```

The immediate response has `status: "PROCESSING"` and a `scan_id`. Poll `GET /education/scans/{scan_id}/progress`, then retrieve `GET /education/scans/{scan_id}`. Do not expect findings in the initial upload response.

### Direct scan

A maintained no-server example is available at [`examples/scan_pdf_direct.py`](../../examples/scan_pdf_direct.py):

```console
DATABASE_URL=postgresql://localhost/unused JWT_SECRET=dev-only \
  python examples/scan_pdf_direct.py document.pdf
```

It disables AI and prints the score and issues. The environment variables satisfy settings imported by the processor; the example itself does not use the database.

## Output and review

A remediation request uses `POST /education/remediate/{scan_id}` and may return fixed/manual/failed counts plus managed artifact metadata. With an artifact ID, inspect `GET /education/scans/{scan_id}/artifacts/{artifact_id}`, download from `GET /education/scans/{scan_id}/artifacts/{artifact_id}/download`, and approve or reject only after review.

For a managed PDF, the authoritative object is a private, unlinked output claim over the exact validated bytes, not `output_file`. Direct, queued, and Brightspace flows publish its exact claimed stream through descriptor-bound, DB-first artifact staging. The service recomputes size, SHA-256, MIME type, scan type, and filename. Cleanup or cancellation that cannot remove the exact staging attempt remains an explicit, recoverable failure with an artifact ID, publication token held internally, and operator-visible cleanup state; it is not converted into success.

The descriptor implementation depends on Unix-style APIs including directory-relative `open`, `rename`, `link`, `unlink`, `O_NOFOLLOW`, and `fcntl`. The managed PDF path is therefore specific to supported Unix/macOS/Linux environments; it is not a portable Windows filesystem contract. Path-oriented compatibility remains for direct library consumers and non-PDF formats.

Compare pages visually, inspect the tag tree and reading order, proofread OCR and alt text, exercise links/forms/bookmarks, and use the external PDF validator(s) and assistive technology required by your workflow. Keep the source PDF.

## Tests

- [`tests/test_pdf_processor_e2e.py`](../../tests/test_pdf_processor_e2e.py) — fixture-backed extraction, structure, scoring, HTML, and batch behavior.
- [`tests/test_pdf_remediation_integration.py`](../../tests/test_pdf_remediation_integration.py) — scan → rule-based remediation → re-scan improvement.
- [`tests/test_pdf_ocr_remediation.py`](../../tests/test_pdf_ocr_remediation.py) — immutable originals, per-page OCR suitability, output text preservation, fail-closed refusal, candidate publication, and cleanup.
- [`tests/test_pdf_accessible_html_escaping.py`](../../tests/test_pdf_accessible_html_escaping.py) — HTML-context escaping, canonical fragment sanitization, and bounded PNG/JPEG data URLs.
- [`tests/test_remediation_output_claim.py`](../../tests/test_remediation_output_claim.py) — private exact-byte claim ownership and aliasing rejection.
- [`tests/test_direct_pdf_claim_publication.py`](../../tests/test_direct_pdf_claim_publication.py), [`tests/test_queued_pdf_output_claim.py`](../../tests/test_queued_pdf_output_claim.py), and [`tests/test_brightspace_pdf_output_claim.py`](../../tests/test_brightspace_pdf_output_claim.py) — stream-authoritative managed publication.
- [`tests/test_pdf_structure.py`](../../tests/test_pdf_structure.py) — structure conditions.
- [`tests/test_pdf_table_headers.py`](../../tests/test_pdf_table_headers.py) — table header structures.
- [`tests/test_ocr_tagged_pdf.py`](../../tests/test_ocr_tagged_pdf.py) — OCR/tag behavior.
- [`tests/test_verapdf.py`](../../tests/test_verapdf.py) — veraPDF integration behavior.
