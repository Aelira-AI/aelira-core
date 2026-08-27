# Examples

Working examples for the three ways to drive Aelira Core: the engine as a
library, the HTTP API, and the CLI. Each example is runnable as-is and is
verified against the code in this repository.

For format-by-format scan/remediation boundaries, dependencies, current API and
CLI entry points, output expectations, and human-review guidance, start with the
[document remediation hub](../docs/document-remediation/README.md).

The PDF safety architecture documented there is present on `main` after v0.9.5, is not part of v0.9.5, and can enter a future release only through a later release process. Its merge created no release or deployment. The direct example below scans only; managed PDF remediation additionally uses the exact-byte output-claim lifecycle described in the hub.

| Example | What it shows | Needs a running server? |
|---------|---------------|-------------------------|
| [`scan_pdf_direct.py`](scan_pdf_direct.py) | Scan a PDF by importing the engine directly — embed the processors in your own batch job or CI check | No |
| [`api_scan.sh`](api_scan.sh) | Submit a scan over HTTP, poll progress, fetch results | Yes |
| [`custom_processor.py`](custom_processor.py) | Add support for a new file format: issue models, the scan interface, registration | No (reference code) |

## 1. Scan a document with the engine directly

The processors are plain Python classes with no web framework attached.
`scan_pdf_direct.py` scans a bundled test fixture (or any PDF you pass) and
prints the compliance score and each issue:

```console
$ DATABASE_URL=postgresql://localhost/unused JWT_SECRET=dev-only \
      python examples/scan_pdf_direct.py
File:             tests/fixtures/pdfs/academic_paper.pdf
Compliance score: 41.0
Issues found:     5
  [critical] missing_content_marking (page 1): Structure tree exists but content streams have no marked content (BDC/EMC)
  [critical] empty_parent_tree (page 1): Structure tree has no ParentTree mapping (empty /Nums)
  ...
```

(The settings module requires `DATABASE_URL` and `JWT_SECRET` at import time
even though this example never touches the database.)

The same pattern works for the other processors — `docx_processor`,
`pptx_processor`, `xlsx_processor`, `latex_processor` — which share the
scan-and-return-issues shape shown in `custom_processor.py`.

## 2. Scan through the API

`api_scan.sh` drives the asynchronous scan flow end to end: upload returns a
`scan_id` immediately, progress is polled at
`/education/scans/{scan_id}/progress`, and the full result comes from
`/education/scans/{scan_id}`.

```console
$ AELIRA_API_KEY=aelira_live_... sh examples/api_scan.sh syllabus.pdf
```

Create an API key in the dashboard under Settings → API Keys. Other formats
use the same pattern: `/education/word/scan`, `/education/powerpoint/scan`,
`/education/excel/scan`, `/education/latex/scan`.

## 3. Scan from the command line

The CLI wraps the API flow above with progress display and formatted output:

```console
$ npx @aelira/cli scan pdf syllabus.pdf
```

See [`cli/README.md`](../cli/README.md) for authentication and the full
command set.

## Adding a New Document Processor

See [custom_processor.py](custom_processor.py) for a complete example of how
to add support for a new file format. It shows how to:

- Define issue models using Pydantic
- Create a processor class with the standard scan interface
- Return structured results compatible with the dashboard
- Register the processor with the API

## Architecture Overview

```
User uploads file
    |
    v
API route receives file → identifies type → dispatches to processor
    |
    v
Processor scans for WCAG violations → returns structured results
    |
    v
Results stored in database → displayed in dashboard
    |
    v
(Optional) Remediator generates a candidate, validates it, and for managed PDFs publishes the exact claimed bytes
```

Each processor follows the same pattern:

1. Accept a file path or bytes
2. Parse the document format
3. Check for WCAG 2.1 AA violations
4. Return a list of issues with locations, severity, and suggested fixes
