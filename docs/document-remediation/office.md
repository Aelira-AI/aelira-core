# Office documents: DOCX, PPTX, and XLSX

The three OOXML formats have separate processors, remediators, routes, and evidence. “Office support” does not mean identical checks or remediation depth.

## DOCX

### DOCX verified capabilities

The [DOCX processor](../../src/education/docx_processor.py) checks heading hierarchy, images and alt text, tables, fake lists, non-descriptive links, language, title, font size, SmartArt, and embedded objects. It returns categorized issues, a score, document counts, suggestions, and an HTML representation.

The [DOCX remediator](../../src/education/remediation/docx_remediator.py) may set image alt text, apply/create heading styles, convert detected fake bullets to list formatting, mark a first table row as a header, replace targeted link text, set run language, and set the document title.

### DOCX non-capabilities

It does not rewrite inaccessible embedded objects or guarantee that SmartArt becomes accessible. First-row header inference, generated alt text, heading choices, and link labels require review. Legacy `.doc` and macro-enabled `.docm` are not accepted by `POST /education/word/scan`.

## PPTX

### PPTX verified capabilities

The [PPTX processor](../../src/education/pptx_processor.py) checks image alt text, text/background contrast, missing/empty/duplicate slide titles, optional images-of-text via OCR, animation timing patterns, and embedded media/caption or transcript signals. Fixture-backed end-to-end tests cover slide processing, contrast, and missing alt text.

The [PPTX remediator](../../src/education/remediation/pptx_remediator.py) may set alt text on a located shape, adjust eligible text color when foreground/background metadata is available, and add a missing title.

### PPTX non-capabilities

The current reading-order path determines a suggested position order, writes guidance into speaker notes, and returns `False`; it does **not** structurally reorder shapes. It does not add captions to embedded video/audio or replace images of text. Legacy `.ppt` is not accepted by `POST /education/powerpoint/scan`, even though the CLI's directory finder currently discovers it; use `.pptx`.

## XLSX

### XLSX verified capabilities

The [XLSX processor](../../src/education/xlsx_processor.py) checks meaningful sheet names, table/header structure, chart and image alternatives, merged cells, color-only information, frozen panes/navigation, named ranges, pivot-table structures, and conditional formatting. Optional chart/image descriptions can use AI.

The [XLSX remediator](../../src/education/remediation/xlsx_remediator.py) may rename a generic sheet, format/create a table over a detected range, add a missing chart title, append a text indicator for recognized cell colors, and freeze the header row.

### XLSX non-capabilities

The chart path sets a chart title only when one is absent; do not treat it as guaranteed chart alt text. Color indicators cover recognized RGB-like fills, not every theme, gradient, icon set, or conditional rule. Table-range and header inference can be wrong. Legacy `.xls` and macro-enabled `.xlsm` are not accepted by `POST /education/excel/scan`.

## Dependencies

Install [`requirements.txt`](../../requirements.txt). Core libraries are python-docx, python-pptx, openpyxl, Pillow, and lxml. PPTX image-of-text detection additionally requires the Tesseract executable and is opt-in. See [`docs/DEPENDENCIES.md`](../DEPENDENCIES.md).

All three formats can be scanned without a model. AI is optional for generated or validated semantic text; remote providers may receive extracted document context or images.

## Quick start

Start Aelira Core and pass the API URL to the current API-backed commands when it is not the default `http://localhost:8000`:

```console
npm install -g @aelira/cli
aelira config set api-key "$AELIRA_API_KEY"

aelira scan docx handout.docx --api-url http://localhost:8000 --format json --output docx-scan.json
aelira scan ppt lecture.pptx --api-url http://localhost:8000 --format json --output pptx-scan.json
aelira scan xlsx grades.xlsx --api-url http://localhost:8000 --format json --output xlsx-scan.json
```

These commands use `POST /education/word/scan`, `POST /education/powerpoint/scan`, and `POST /education/excel/scan`. Each upload returns a `scan_id`; the CLI polls the shared progress/result routes. To call the API directly, replace the route in this multipart request:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $AELIRA_API_KEY" \
  -F "file=@handout.docx" \
  http://localhost:8000/education/word/scan
```

Do not send a legacy Office format under a renamed extension; upload validation and the format parser inspect the actual content.

## Output and review

A scan produces stored JSON findings; it does not modify the upload. `POST /education/remediate/{scan_id}` creates an output in the same OOXML format for supported fixes. A managed artifact is published only after successful remediation with at least one fix, zero manual issues, zero failed issues, an output file, and passed verification. A response that reports manual or failed work does not publish that managed artifact.

Open the output in the target Office application. Check layout and styles, navigation panes/reading order, tables and formulas, links, charts, speaker notes, animations/media, embedded objects, pivots/conditional formatting, and generated language. Test with assistive technology before approval. OOXML serialization is not byte-preserving and can normalize unsupported package parts.

## Tests

### DOCX evidence

- [`tests/test_docx_no_default_style.py`](../../tests/test_docx_no_default_style.py) — processing exported documents without a default paragraph style.
- [`tests/test_docx_smartart.py`](../../tests/test_docx_smartart.py) — SmartArt detection.
- [`tests/test_docx_embedded_objects.py`](../../tests/test_docx_embedded_objects.py) — embedded-object detection and processor integration.

### PPTX evidence

- [`tests/test_pptx_processor_e2e.py`](../../tests/test_pptx_processor_e2e.py) — fixture-backed scan workflow.
- [`tests/test_pptx_animations.py`](../../tests/test_pptx_animations.py) — animation checks.
- [`tests/test_pptx_embedded_media.py`](../../tests/test_pptx_embedded_media.py) — media checks.

### XLSX evidence

- [`tests/test_xlsx_pivot_tables.py`](../../tests/test_xlsx_pivot_tables.py) — pivot and nested-header checks.
- [`tests/test_xlsx_conditional_formatting.py`](../../tests/test_xlsx_conditional_formatting.py) — conditional-format/color checks.
- [`tests/test_small_doc_scoring.py`](../../tests/test_small_doc_scoring.py) — score behavior on small documents.

### Shared remediation boundaries

- [`tests/test_remediation_purpose_clients.py`](../../tests/test_remediation_purpose_clients.py) — provider/client boundaries across document remediators.
- [`tests/test_alt_text_fail_closed.py`](../../tests/test_alt_text_fail_closed.py) — PPTX/XLSX generated descriptions do not become placeholder successes.
- [`tests/test_remediation_verification.py`](../../tests/test_remediation_verification.py) — verification result handling.
