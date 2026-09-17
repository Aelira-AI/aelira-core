# Aelira CLI examples

Use these scenarios with your own files, URLs, department, and course IDs. See the [command reference](COMMANDS.md) for command definitions and the [troubleshooting guide](TROUBLESHOOTING.md) for prerequisites and failures. Run `aelira --help` or a command's `--help` for the installed version's options.

## Configure a deployment

```bash
aelira config init
aelira config set api-url http://localhost:8000
aelira auth login
aelira config show
aelira config validate
```

`auth login` prompts for a login method and any required email/key. `config validate` tests reachability; it does not verify authorization for every endpoint. Noninteractive environments can provide `AELIRA_API_URL`, `AELIRA_API_KEY`, and `AELIRA_DEPARTMENT` through their secret/configuration settings.

## Scan a website or HTML file locally

These scans need the CLI's Playwright Chromium browser. They run axe-core locally and do not need the Aelira API unless requesting a PDF report.

```bash
aelira scan https://example.com
aelira scan ./dist/index.html --format json --output scan.json
aelira scan https://example.com --timeout 60000 --load-delay 2000
```

To include backend AI analysis, use `analyze`. To request a server-generated PDF of the web scan evidence, use `--pdf`:

```bash
aelira analyze https://example.com --format json --output analysis.json
aelira scan ./dist/index.html --pdf accessibility-report.pdf
```

For backend crawling, provide a starting URL or an explicit sitemap URL:

```bash
aelira scan web https://example.com --batch --max-pages 10
aelira scan web https://example.com/sitemap.xml --sitemap --max-pages 20 --format json --output website.json
```

## Scan course documents

These scanners upload files to the configured backend. Directory inputs process matching documents directly.

```bash
aelira scan pdf syllabus.pdf --format json --output syllabus-scan.json
aelira scan pdf ./course-materials/ --format csv --output pdf-issues.csv
aelira scan pdf searchable.pdf --skip-ocr
aelira scan docx syllabus.docx --format json --output word-scan.json
aelira scan xlsx grades.xlsx --format csv --output spreadsheet-issues.csv
aelira scan ppt lecture.pptx --format json --output slides-scan.json
aelira scan ppt ./slides/
aelira scan latex chemistry.tex --type chemistry --format json --output latex-results.json
```

Review the scan result before remediation. Replace the sample ID below with a backend scan ID from your result. Remediation output availability depends on the backend and file type; inspect and rescan downloaded files.

```bash
aelira remediate scan-123 --download --output remediated.pdf
```

## Scan images, media, and source files

```bash
aelira scan image diagram.png --format json --output image-results.json
aelira scan image ./images/ --batch --format json --output images.json
aelira scan video lecture.mp4 --format json --output transcription.json
aelira scan video recording.mp3 --format json --output audio-results.json
aelira scan code ./src/ --format json --output code-results.json
```

Transcription JSON contains the backend's results and available caption data; the CLI's `--format` selects console or JSON output. It does not select a caption-file format.

## Watch a working directory

The watcher requires a reachable backend at startup. It scans files changed after it starts; run a document directory scan separately when you need to scan existing files.

```bash
aelira scan watch ./course-materials --extensions .pdf,.docx,.pptx
aelira scan watch ./uploads --no-recursive --debounce 1000 --concurrency 2
```

Stop with Ctrl+C. The current implementation watches only the top-level directory on Linux. Start separate watchers for relevant subdirectories.

## Work with Canvas

First complete your deployment's Canvas connection flow. Replace the sample instance URL and identifiers with those for your institution.

```bash
aelira integrations connect canvas --instance-url https://canvas.example.edu
aelira config set department engineering
aelira canvas status
aelira canvas courses --format json
aelira canvas files 101 --search syllabus --format json
aelira canvas scan 101 --wait --format json
aelira canvas remediate 101 555
```

The remediation command above does not replace the original in Canvas. To upload the result back, add `--upload-back` and confirm the replacement at the prompt.

## Export evidence and track issues

```bash
aelira issues list --department engineering --status open --format json --output issues.json
aelira issues stats --department engineering
aelira export --format csv --limit 50 --output scans.csv
aelira export --format json --limit 50 --output scans.json
aelira report evidence engineering --output evidence-report.pdf
```

Export fetches backend scans, keeps scans with reported issues, and retrieves their details; it skips detail requests that fail. It is not a complete backup of every scan. The evidence PDF records scan evidence and limitations, not a conformance certification.

## Gate a build in CI

Run this after building your site and provisioning the CLI and its Chromium browser in the runner:

```bash
aelira ci ./dist --threshold 85 --fail-on serious --format sarif --output accessibility.sarif
```

The command returns a failing exit status when the gate fails. Preserve that status in your CI step and publish the report even on failure. Other supported CI output formats are console, JSON, and JUnit.

For jobs that write configuration, isolate it with `AELIRA_CONFIG_DIR`:

```bash
export AELIRA_CONFIG_DIR="$(mktemp -d)"
aelira config set api-url https://api.example.edu
```

Supply `AELIRA_API_KEY` through the runner's secret environment. This directory override applies to configuration; local history has its own storage under `~/.aelira/history`.
