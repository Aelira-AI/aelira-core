# LaTeX scanning and remediation

LaTeX source is first-class: remediation writes `.tex` by default. MathML/ARIA conversion and optional HTML/PDF generation are related outputs, not replacements for the source.

## Verified capabilities

The [LaTeX processor](../../src/education/latex_processor.py) detects inline/display math and common math environments, converts supported expressions with latex2mathml, creates ARIA labels, and checks source for metadata/language, figure/caption, table, equation-label, color-only/possible contrast, bare-link, and list-structure issues. The API stores source findings alongside equation conversion results and generated HTML.

The [LaTeX remediator](../../src/education/remediation/latex_remediator.py) can make bounded source edits for supported language, figure description, heading, table, title, equation/ARIA, color, and link cases. Figure descriptions can use the issue location and nearby source context when AI is configured; filename-based text is only a fallback and still needs review.

The [converter](../../src/education/remediation/latex_converter.py) can request additional HTML and PDF files. HTML prefers LaTeXML/latexmlpost for MathML. PDF prefers LuaLaTeX with document metadata/tagging, then can fall back to LaTeXML → HTML → Playwright, then pdflatex with more limited accessibility.

## What it does not promise

- A successful equation conversion does not prove the document is accessible or mathematically spoken as the author intends.
- Custom macros, unsupported packages, TikZ/chemistry/physics notation, multi-file projects, and malformed source can require manual work even when detection code recognizes the construct.
- `.tex`, HTML, and PDF are not equivalent outputs. Fallback selection changes PDF semantics, and the pdflatex fallback is explicitly limited.
- The processor's comprehensive E2E module is guarded by `RUN_E2E_TESTS`; default test runs do not execute that whole module.
- Optional conversion can return no file when required executables are unavailable or conversion fails. The `.tex` source remains the primary output.
- A PDF uploaded to the LaTeX route is scanned/remediated as a PDF; Aelira does not reconstruct TeX source from it.

## Dependencies

The in-process equation path uses latex2mathml from [`requirements.txt`](../../requirements.txt). Optional conversions probe runtime executables rather than assuming they exist:

- LuaLaTeX (preferred PDF path) or pdflatex fallback from TeX Live;
- LaTeXML and `latexmlpost` for HTML/MathML conversion;
- Playwright plus an installed Chromium browser for the HTML-to-PDF fallback;
- Pandoc where a converter branch uses it;
- pikepdf for PDF metadata post-processing.

See the [dependency inventory](../DEPENDENCIES.md). The converter also restricts input/output paths to configured allowed directories; a direct library caller cannot convert an arbitrary path without matching that policy.

## Quick start

Use the current multipart route for a safe source scan:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $AELIRA_API_KEY" \
  -F "file=@document.tex" \
  "http://localhost:8000/education/latex/scan?use_ollama=false"
```

`POST /education/latex/scan` delegates to `POST /education/latex/convert`. The immediate response contains a `scan_id`; poll `GET /education/scans/{scan_id}/progress`, then fetch `GET /education/scans/{scan_id}`.

The CLI command is named:

```console
aelira scan latex document.tex --format json
```

However, the current [`scan latex` command source](../../cli/src/commands/scan/latex.ts) sends a JSON body while the current FastAPI route expects multipart `UploadFile`. Use the curl form above rather than relying on this command until the mismatch is fixed.

To request source remediation plus optional conversions through the API:

```bash
curl --fail --silent --show-error \
  -X POST \
  -H "Authorization: Bearer $AELIRA_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"use_ai":false,"latex_formats":["tex","html"]}' \
  "http://localhost:8000/education/remediate/$SCAN_ID"
```

The route can accept `tex`, `html`, and `pdf`. Request only formats whose dependencies are installed; absence/failure is reflected by unavailable output rather than silently upgrading a fallback's quality.

## Output and review

The default direct remediator configuration requests TeX only. The API remediation options currently default to `tex`, `pdf`, and `html`, but each converted file exists only if its path succeeds. Available legacy downloads can be listed at `GET /education/scans/{scan_id}/remediated/formats` and selected from `GET /education/scans/{scan_id}/remediated?format=tex|html|pdf`.

When remediation returns a managed artifact ID, inspect `GET /education/scans/{scan_id}/artifacts/{artifact_id}`, download from `GET /education/scans/{scan_id}/artifacts/{artifact_id}/download`, and use `POST /education/scans/{scan_id}/artifacts/{artifact_id}/approve` or `POST /education/scans/{scan_id}/artifacts/{artifact_id}/reject` only after review.

Diff the TeX source. Compile it in a restricted environment, inspect logs, verify figures/tables/links and the spoken mathematics, and separately test each HTML/PDF output with the validators and assistive technology appropriate to that format. Keep project dependencies and included files with the source; a single-file upload is not a complete multi-file build environment.

## Tests

- [`tests/test_latex_processor_e2e.py`](../../tests/test_latex_processor_e2e.py) — equation detection, MathML, ARIA, compliance data, and HTML export; module is opt-in via `RUN_E2E_TESTS`.
- [`tests/test_latex_pdf_ua.py`](../../tests/test_latex_pdf_ua.py) — source metadata injection and PDF conversion paths.
- [`tests/test_latex_siunitx.py`](../../tests/test_latex_siunitx.py) — SI-unit notation handling.
- [`tests/test_remediation_downloads.py`](../../tests/test_remediation_downloads.py) — default/custom output format configuration and converter behavior.
- [`tests/test_alt_text_fail_closed.py`](../../tests/test_alt_text_fail_closed.py) — generated semantic content fails closed instead of counting placeholders as fixes.
