# PDF scanning and remediation

PDF has the deepest end-to-end document-remediation evidence in this repository. That does not turn heuristic structure inference or a successful re-scan into a PDF/UA certificate.

## Verified capabilities

The [PDF processor](../../src/education/pdf_processor.py) can extract text, use OCR for image-only material, inspect metadata/language and tag/structure conditions, detect headings/lists/tables, analyze reading order, inspect images and links/forms, calculate a score, and produce an HTML representation. Optional image descriptions and enhanced fix explanations use a configured AI provider.

The [PDF remediator](../../src/education/remediation/pdf_remediator.py) can make bounded changes when the issue and PDF expose a safe target: document language and title, bookmarks, selected structure/heading/list/table tags, and figure alt text. The [structure helper](../../src/education/remediation/pdf_structure.py) performs direct structure-tree work with pikepdf.

The fixture-backed [integration test](../../tests/test_pdf_remediation_integration.py) scans a PDF, applies rule-based remediation, writes a PDF, re-scans it, and requires fewer findings. Other tests exercise structure, tagged OCR, and table headers.

## What it does not promise

- It does not certify PDF/UA or WCAG conformance, and external Matterhorn checks in the integration test are deliberately non-fatal.
- It cannot safely infer every relationship, reading order, table association, form label, or meaningful image description.
- A missing/incomplete structure tree can prevent targeted changes. Some structure work is available only when pikepdf and required content references are present.
- OCR output can be wrong and must be proofread.
- Remediation can leave manual or failed issues; a suggested fix is not an applied fix.
- The output is another PDF, not the original bytes. Signatures, interactive behavior, complex forms, unusual encodings, and layout require regression review.

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

Compare pages visually, inspect the tag tree and reading order, proofread OCR and alt text, exercise links/forms/bookmarks, and use the external PDF validator(s) and assistive technology required by your workflow. Keep the source PDF.

## Tests

- [`tests/test_pdf_processor_e2e.py`](../../tests/test_pdf_processor_e2e.py) — fixture-backed extraction, structure, scoring, HTML, and batch behavior.
- [`tests/test_pdf_remediation_integration.py`](../../tests/test_pdf_remediation_integration.py) — scan → rule-based remediation → re-scan improvement.
- [`tests/test_pdf_structure.py`](../../tests/test_pdf_structure.py) — structure conditions.
- [`tests/test_pdf_table_headers.py`](../../tests/test_pdf_table_headers.py) — table header structures.
- [`tests/test_ocr_tagged_pdf.py`](../../tests/test_ocr_tagged_pdf.py) — OCR/tag behavior.
- [`tests/test_verapdf.py`](../../tests/test_verapdf.py) — veraPDF integration behavior.
