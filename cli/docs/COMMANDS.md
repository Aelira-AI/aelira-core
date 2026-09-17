# Aelira CLI command reference

Use `aelira --help` to list installed commands and, for example, `aelira scan pdf --help` for a command's arguments, flags, defaults, and accepted values. Help is generated from the [command definitions](../src/commands/). Flags are command-specific; an option accepted by one scanner may be rejected by another.

This guide explains command selection and links to those definitions instead of maintaining a second complete flag list. See [examples](EXAMPLES.md) for workflows and [troubleshooting](TROUBLESHOOTING.md) for failures.

## Setup and configuration

```bash
aelira config init
aelira config show
aelira config set api-url http://localhost:8000
aelira config set api-key "$AELIRA_API_KEY"
aelira config set department engineering
aelira config validate
```

`init`, `show`, `set`, `validate`, and `profile` are positional actions. The keys accepted by `set` are `api-url`, `api-key`, and `department`. Set the environment variable to your issued key before using the key example, or use the interactive `aelira auth login` prompt.

Configuration is stored in `~/.aelira/config.json`. `AELIRA_CONFIG_DIR` changes the directory containing `config.json`. API URL precedence is explicit `--api-url`, then `AELIRA_API_URL`, then the active profile, then `http://localhost:8000`. `AELIRA_API_KEY` and `AELIRA_DEPARTMENT` override the corresponding profile settings where used. `config show` masks the key; `config validate` checks backend health, not key validity.

```bash
aelira config profile create staging --api-url https://api.example.edu
aelira config profile list
aelira config profile use staging
aelira auth login
aelira auth logout
```

Authentication methods are selected interactively. Logout removes the locally stored key; it does not revoke a server key or clear an environment variable.

Definitions: [config](../src/commands/config.ts), [auth](../src/commands/auth.ts), [configuration resolution](../src/utils/config.ts).

## Choose a scanner

| Command | Input and behavior | Definition |
| --- | --- | --- |
| `aelira scan` | URL or local HTML; local axe-core browser scan. JSON can be saved with `--format json --output`. | [scan](../src/commands/scan.ts) |
| `aelira analyze` | URL or local HTML; browser scan plus backend AI analysis. | [analyze](../src/commands/analyze.ts) |
| `aelira scan web` | Backend web scan; `--batch` crawls linked pages, `--sitemap` takes a sitemap URL. | [web](../src/commands/scan/web.ts) |
| `aelira scan pdf` | PDF file or directory; `--skip-ocr` disables OCR. | [pdf](../src/commands/scan/pdf.ts) |
| `aelira scan docx` | Word file or directory. | [docx](../src/commands/scan/docx.ts) |
| `aelira scan xlsx` | Excel file or directory. | [xlsx](../src/commands/scan/xlsx.ts) |
| `aelira scan ppt` | PowerPoint file or directory. | [ppt](../src/commands/scan/ppt.ts) |
| `aelira scan latex` | LaTeX file or directory; `--type` provides a content hint. | [latex](../src/commands/scan/latex.ts) |
| `aelira scan code` | Source file or directory sent to the backend. | [code](../src/commands/scan/code.ts) |
| `aelira scan image` | Image file or directory; backend alt-text generation. | [image](../src/commands/scan/image.ts) |
| `aelira scan video` | Video/audio file or directory; backend transcription results. | [video](../src/commands/scan/video.ts) |
| `aelira scan watch` | Directory; scans changed files by extension after a backend health check. | [watch](../src/commands/scan/watch.ts) |

The document scanners accept directories directly. PDF, DOCX, XLSX, PPT, LaTeX, and code scanners support `console`, `json`, and `csv`; image and video scanners support `console` and `json`. Use `--output` with JSON/CSV to save structured results. Video `--output` saves JSON results, not a VTT/SRT file. LaTeX `--output` similarly saves scan results rather than a standalone HTML conversion.

`scan` and `analyze` support `--pdf` for a verified server-generated PDF report; this requires the API even when the underlying scan is local. `scan pdf` instead scans an input PDF. Automated reports describe scanned-content evidence and limitations; they do not establish conformance with accessibility standards or legal requirements.

## Analysis, remediation, and reports

| Command | Purpose | Definition |
| --- | --- | --- |
| `aelira focus` | Browser-based keyboard focus analysis. | [focus](../src/commands/focus.ts) |
| `aelira cvd` | Browser-based color vision deficiency analysis. | [cvd](../src/commands/cvd.ts) |
| `aelira remediate` | Remediate a previous backend **scan ID**, optionally download the resulting file. | [remediate](../src/commands/remediate.ts) |
| `aelira bulk` | Directory operations; inspect action behavior and available flags before use. | [bulk](../src/commands/bulk.ts) |
| `aelira issues` | List, summarize, update, assign, or annotate backend issues. | [issues](../src/commands/issues.ts) |
| `aelira report evidence` | Download a department's accessibility evidence PDF. | [evidence](../src/commands/report/evidence.ts) |
| `aelira report analytics` | Backend historical trend data. | [analytics](../src/commands/report/analytics.ts) |
| `aelira report compliance` | Deprecated scan-evidence statistics view. | [compliance](../src/commands/report/compliance.ts) |
| `aelira report certificate` | Deprecated alias for `report evidence`. | [certificate](../src/commands/report/certificate.ts) |
| `aelira export` | Export backend scan history with issue details to CSV/JSON. CSV requires `--output`. | [export](../src/commands/export.ts) |
| `aelira history` | Inspect locally recorded scan history; not a backend job-status query. | [history](../src/commands/history.ts) |
| `aelira diff` | Compare two saved scan-result JSON files. | [diff](../src/commands/diff.ts) |
| `aelira ci` | Browser checks with thresholds, severity gates, and JSON/JUnit/SARIF reports. | [ci](../src/commands/ci.ts) |

## Integrations and Canvas

These commands require a configured backend and provider connection. Provider availability also depends on the server's OAuth and integration configuration.

| Command | Purpose | Definition |
| --- | --- | --- |
| `aelira integrations` | Show connected provider status. | [integrations](../src/commands/integrations.ts) |
| `aelira integrations connect` | Connect a provider; LMS connections take `--instance-url` or prompt for it. | [connect](../src/commands/integrations/connect.ts) |
| `aelira integrations folders` | Interactive folder selection; Google/Microsoft selected with `--provider`. | [folders](../src/commands/integrations/folders.ts) |
| `aelira integrations sync` | Interactive sync of a connected provider. | [sync](../src/commands/integrations/sync.ts) |
| `aelira canvas status` | Show Canvas connection status for a department. | [status](../src/commands/canvas/status.ts) |
| `aelira canvas courses` | List available courses. | [courses](../src/commands/canvas/courses.ts) |
| `aelira canvas files` | List a course's files. | [files](../src/commands/canvas/files.ts) |
| `aelira canvas scan` | Scan a course, optionally wait for completion. | [scan](../src/commands/canvas/scan.ts) |
| `aelira canvas remediate` | Remediate a course file, optionally upload it back to Canvas. | [remediate](../src/commands/canvas/remediate.ts) |

Canvas commands accept `--department` and otherwise use the configured department. `--upload-back` replaces the original Canvas file and prompts for confirmation unless `--yes` is passed.

## Interactive mode

`aelira interactive` opens the [interactive menu](../src/commands/interactive.ts). Use direct commands for automation and for commands not offered by the menu.

## Keeping this guide current

The [guide regression test](../test/docs/operator-guides.test.ts) parses every fenced `aelira` example in these three guides against the current command definitions without running commands, launching browsers, or making API calls. It checks syntax and accepted options, not server availability or successful processing. Keep executable examples on separate lines in `bash` fences; put placeholder usage syntax in prose and link to command help for exhaustive flags.
