<h1 align="center">@aelira/cli</h1>

<p align="center"><strong>AI-assisted accessibility testing CLI for WCAG 2.1 AA compliance, built for higher education.</strong></p>

<p align="center">
  <a href="https://www.npmjs.com/package/@aelira/cli"><img alt="npm package @aelira/cli" src="https://img.shields.io/npm/v/%40aelira%2Fcli?label=npm&color=%23cb3837"></a>
  <a href="https://github.com/Aelira-AI/aelira-core/blob/main/cli/LICENSE"><img alt="Licence: MIT" src="https://img.shields.io/badge/licence-MIT-informational?color=%230f766e"></a>
  <a href="https://github.com/Aelira-AI/aelira-core/blob/main/cli/package.json"><img alt="Node.js 22 or newer" src="https://img.shields.io/badge/node-%3E%3D22-informational?color=%232e2963"></a>
</p>

<p align="center">
  <a href="#installation">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#command-reference">Commands</a> ·
  <a href="#cicd-integration">CI/CD</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="https://github.com/Aelira-AI/aelira-core">Aelira Core on GitHub</a>
</p>

> **AELIRA**: **A**ccessibility, **E**quity, **L**earning, **I**nclusion, **R**emediation, **A**utomation.

**Version:** 0.9.13

## What it does

| Area | What you get |
|---|---|
| <img src="https://raw.githubusercontent.com/Aelira-AI/aelira-core/main/docs/assets/readme/icons/document-magnifying-glass.svg" width="24" height="24" alt=""> **Scanning** | PDF (with OCR), Word, PowerPoint, Excel, LaTeX to MathML, images (AI alt text), video and audio (transcription), source code, and websites (single, batch, or from a sitemap). Watch a directory to auto-scan on change. |
| <img src="https://raw.githubusercontent.com/Aelira-AI/aelira-core/main/docs/assets/readme/icons/wrench-screwdriver.svg" width="24" height="24" alt=""> **Remediation** | `aelira remediate` fixes issues and downloads the corrected file; `aelira diff` tracks regressions between scans. |
| <img src="https://raw.githubusercontent.com/Aelira-AI/aelira-core/main/docs/assets/readme/icons/academic-cap.svg" width="24" height="24" alt=""> **LMS and cloud** | Canvas course scanning and per-file remediation; cloud integration status, OAuth connect, folder selection, and background sync for Google, Microsoft, Blackboard. |
| <img src="https://raw.githubusercontent.com/Aelira-AI/aelira-core/main/docs/assets/readme/icons/clipboard-document-check.svg" width="24" height="24" alt=""> **Reporting, team and CI** | Accessibility evidence reports for scanned content and historical trend analytics with deadline projection; an issue tracker with assignments and filters; `aelira ci` emits exit codes, JUnit XML, and badges for pipelines; CSV/JSON export of scan history. Evidence reports describe Aelira's checks and limitations; they are not accessibility or legal determinations. |

Authentication supports magic-link, browser, or API-key login with multi-profile config, and the CLI retries transient API errors automatically with backoff.

## Installation

```bash
npm install -g @aelira/cli
```

## Quick start

### Interactive mode (recommended)

Launch the interactive CLI with ASCII art and menu navigation:

```bash
aelira interactive
```

This opens the main menu where you can:

- Scan Website
- Scan Documents (PDF, PowerPoint, LaTeX)
- Scan Media (Images, Videos)
- Scan Source Code
- Download Accessibility Evidence Report
- Settings & Configuration
- Help & Documentation

Word and Excel scanning (`aelira scan docx`, `aelira scan xlsx`) are not on the interactive menu yet; run them directly.

Navigation:

- Use arrow keys or number keys to select options
- Press Enter to confirm
- Press Ctrl+C to exit

### API configuration

All network commands resolve the API endpoint in the same order: an explicit `--api-url`, `AELIRA_API_URL`, the active profile's `apiUrl`, then `http://localhost:8000`. Configure a deployment once with:

```bash
aelira config set api-url https://api.example.edu
```

## Command reference

| Group | Commands |
|---|---|
| [Document scanning](#document-scanning-commands) | `scan pdf`, `scan docx`, `scan xlsx`, `scan ppt`, `scan latex`, `scan watch` |
| [Media scanning](#media-scanning-commands) | `scan image`, `scan video` |
| [Web scanning](#web-scanning-commands) | `scan`, `scan web`, `analyze`, `scan code` |
| [Canvas LMS](#canvas-lms-commands) | `canvas status`, `canvas courses`, `canvas files`, `canvas scan`, `canvas remediate` |
| [Remediation](#remediation-commands) | `remediate` |
| [Evidence reporting](#accessibility-evidence-reporting-commands) | `report evidence`, `report compliance`, `report analytics` |
| [Issue management](#issue-management-commands) | `issues` |
| [Cloud integrations](#cloud-integration-commands) | `integrations`, `integrations connect`, `integrations folders`, `integrations sync` |
| [Accessibility analysis](#accessibility-analysis-commands) | `focus`, `cvd` |
| [Bulk operations](#bulk-operations) | `bulk` |
| [Comparison and history](#comparison-and-history) | `diff`, `history` |
| [Configuration](#configuration) | `config`, `auth login`, `auth logout`, `export` |
| [CI/CD](#cicd-integration) | `ci` |

## Document scanning commands

### `aelira scan pdf`

PDF accessibility scanner. Scan PDF files for accessibility issues with OCR and remediation support.

```bash
aelira scan pdf <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --skip-ocr                # Skip OCR processing
  --timer
```

Examples:

```bash
aelira scan pdf document.pdf
aelira scan pdf ./course-materials/
aelira scan pdf document.pdf --skip-ocr
```

### `aelira scan docx`

Word document scanner. Scan Word documents (.docx) for accessibility issues.

```bash
aelira scan docx <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

Examples:

```bash
aelira scan docx syllabus.docx
aelira scan docx ./documents/
aelira scan docx report.docx --format json
```

### `aelira scan xlsx`

Excel spreadsheet scanner. Scan Excel spreadsheets (.xlsx) for accessibility issues.

```bash
aelira scan xlsx <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

Examples:

```bash
aelira scan xlsx grades.xlsx
aelira scan xlsx ./spreadsheets/
aelira scan xlsx data.xlsx --format json
```

### `aelira scan ppt`

PowerPoint accessibility scanner. Scan PowerPoint presentations for accessibility issues (alt text, contrast, structure).

```bash
aelira scan ppt <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

Examples:

```bash
aelira scan ppt lecture.pptx
aelira scan ppt ./presentations/
```

### `aelira scan latex`

LaTeX to MathML converter. Convert LaTeX equations to MathML. Model-drafted equation descriptions are kept as review drafts and never written as ARIA labels.

The command uploads each `.tex` or `.latex` file to the API's LaTeX convert endpoint, then waits for the scan to finish. Given a directory, it uploads every `.tex` and `.latex` file it finds, including in subdirectories.

The server's LaTeX processor recognizes:

- ChemFig chemical structures
- mhchem chemistry notation
- Physics package notation (bra-ket, vectors, tensors)
- TikZ diagrams

```bash
aelira scan latex <file or directory>
  --api-url <url>
  --format console|csv|json
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

The `--type` and `--expand-macros` flags are still accepted so existing scripts keep running, but they have no effect: the convert endpoint takes no content type hint or macro option, so the CLI does not send them and prints a deprecation warning when they are used.

Examples:

```bash
aelira scan latex equations.tex
aelira scan latex equations.tex --format json --output equations.json
aelira scan latex ./math-dept/
```

### `aelira scan watch`

Watch a directory for file changes and automatically scan new or modified files.

```bash
aelira scan watch <directory>
  --extensions <list>             # File extensions to watch (default: .pdf,.docx,.pptx,.xlsx,.html,.htm,.tex,.css,.js)
  --debounce <ms>                 # Debounce delay in milliseconds (default: 2000)
  --concurrency <n>               # Max concurrent scans (default: 3)
  --no-recursive                  # Do not watch subdirectories
  --api-url <url>
```

Examples:

```bash
# Watch current directory
aelira scan watch .

# Watch course materials with custom extensions
aelira scan watch ./course-materials --extensions .pdf,.docx,.pptx

# Watch without subdirectories, higher concurrency
aelira scan watch ./uploads --no-recursive --concurrency 5

# Longer debounce for slow file writes
aelira scan watch ./shared-drive --debounce 5000
```

## Media scanning commands

### `aelira scan image`

AI alt text generator. Generate AI-powered alt text for images using vision models.

```bash
aelira scan image <file or directory>
  --api-url <url>
  --format console|json
  --output <file>
  --batch                   # Use batch API (faster for multiple images)
  --timer
```

Examples:

```bash
aelira scan image photo.jpg
aelira scan image ./images/ --batch
```

### `aelira scan video`

Video and audio transcription. Transcribe video/audio files to accessible WebVTT and SRT captions using Whisper AI.

```bash
aelira scan video <file or directory>
  --api-url <url>
  --format console|json
  --output <file>
  --timer
```

Examples:

```bash
aelira scan video lecture.mp4
aelira scan video recording.mp3
aelira scan video ./videos/
```

## Web scanning commands

### `aelira scan`

Basic website scan. Fast, local-only website scanning using axe-core.

```bash
aelira scan <URL or HTML file>
  --format console|json|html
  --output <file>
  --pdf <file>              # Temporarily unavailable (exits with an error)
  --threshold <score>
  --timer
  --local                   # Skip AI analysis
  --timeout <ms>
  --load-delay <ms>
```

### `aelira scan web`

Advanced web scanner with batch and sitemap support.

```bash
aelira scan web <URL>
  --api-url <url>
  --batch                   # Crawl linked pages
  --sitemap                 # Treat URL as sitemap.xml
  --max-pages <n>           # Max pages to scan (default: 20)
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

Examples:

```bash
# Single page scan
aelira scan web https://example.com

# Batch scan (crawl linked pages)
aelira scan web https://example.com --batch --max-pages 50

# Sitemap scan
aelira scan web https://example.com/sitemap.xml --sitemap
```

### `aelira analyze`

AI-enhanced website scan. Scans with axe-core and sends violations to Aelira AI for classification and code fix generation.

```bash
aelira analyze <URL or HTML file>
  --api-url <backend_url>
  --generate-fixes
  --pdf <file>              # Temporarily unavailable (exits with an error)
  --format console|json
  --output <file>
  --timer
```

### `aelira scan code`

Code accessibility scanner. Scan source code for accessibility issues (ARIA attributes, semantic HTML, keyboard navigation).

```bash
aelira scan code <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

### CSV output format

All issue-based scan commands support `--format csv` for easy spreadsheet import:

```bash
aelira scan pdf document.pdf --format csv --output results.csv
aelira scan docx syllabus.docx --format csv --output results.csv
aelira scan web https://example.com --format csv --output results.csv
```

| | Commands |
|---|---|
| Supported | `scan pdf`, `scan docx`, `scan ppt`, `scan xlsx`, `scan latex`, `scan code`, `scan web` |
| Not supported | `scan image` and `scan video` (these produce alt text and transcriptions, not issue lists) |

## Canvas LMS commands

Scan and remediate Canvas course content without leaving the terminal. These commands require a Canvas connection set up via `aelira integrations connect canvas`.

Every command below accepts `--department <id>` and falls back to the department set with `aelira config set department <id>`.

### `aelira canvas status`

Check whether a department's Canvas connection is set up and working.

```bash
aelira canvas status
  --department <id>         # Defaults to the configured department
  --api-url <url>
  --api-key <key>
  --format console|json
```

Examples:

```bash
aelira canvas status
aelira canvas status --department 42 --format json
```

### `aelira canvas courses`

List the Canvas courses available to the connected account.

```bash
aelira canvas courses
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

Examples:

```bash
aelira canvas courses
aelira canvas courses --department 42 --format json
```

### `aelira canvas files`

List the files in a Canvas course. Use this to find the file id needed for `aelira canvas remediate`.

```bash
aelira canvas files <course_id>
  --search <text>            # Filter files by name
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

Examples:

```bash
aelira canvas files 101
aelira canvas files 101 --search syllabus --format json
```

### `aelira canvas scan`

Bulk-scan a course: scan every file in a Canvas course for accessibility issues in one pass.

```bash
aelira canvas scan <course_id>
  --wait                      # Poll status until every file in the course finishes
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

Examples:

```bash
aelira canvas scan 101
aelira canvas scan 101 --wait --format json
```

### `aelira canvas remediate`

Fix accessibility issues in a single Canvas file.

> **`--upload-back` replaces the original file inside the live Canvas course with the remediated version.** It defaults to off, and it prompts for confirmation before overwriting unless you also pass `--yes`. Without a TTY, `--upload-back` requires `--yes`.

The command queues a remediation job and prints its job id; the write-back happens asynchronously once that job completes.

```bash
aelira canvas remediate <course_id> <file_id>
  --upload-back               # Replace the file in Canvas (prompts unless --yes)
  --yes, -y                   # Skip the --upload-back confirmation prompt
  --no-ai                     # Structural fixes only, no AI-generated fixes
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

Examples:

```bash
# Remediate and leave the original file in Canvas untouched
aelira canvas remediate 101 555

# Remediate and replace the file in Canvas (prompts for confirmation)
aelira canvas remediate 101 555 --upload-back

# Replace the file in Canvas without a confirmation prompt
aelira canvas remediate 101 555 --upload-back --yes

# Structural fixes only, no AI
aelira canvas remediate 101 555 --no-ai
```

## Remediation commands

### `aelira remediate`

Auto-remediation engine. Auto-remediate accessibility issues from a previous scan.

```bash
aelira remediate <scan_id>
  --api-url <url>
  --download                # Download remediated file
  --output <file>           # Output path for remediated file
  --format console|json
  --timer
```

Examples:

```bash
# Remediate a scan
aelira remediate abc123

# Remediate and download the fixed file
aelira remediate abc123 --download

# Remediate and save to specific path
aelira remediate abc123 --download --output fixed_document.pdf
```

## Accessibility evidence reporting commands

### `aelira report evidence`

Download a PDF record of Aelira's scanned-content findings, methods, and limitations. The report does not determine conformance with an accessibility standard or legal requirement.

```bash
aelira report evidence [department_id]
  --api-url <url>
  --output <file>
  --timer
```

Examples:

```bash
aelira report evidence dept-123
aelira report evidence dept-123 --output evidence-report.pdf
```

### `aelira report compliance`

Deprecated scan-evidence statistics view. This compatibility command exposes console or JSON scan statistics and findings. Its `--pdf` option downloads the same bounded accessibility evidence report as `aelira report evidence`; it does not make a conformance or legal determination.

```bash
aelira report compliance [department_id]
  --api-url <url>
  --format console|json
  --output <file>                # JSON output path
  --pdf <file>                   # Evidence report PDF path
  --timer
```

Examples:

```bash
# Export scan statistics and findings
aelira report compliance dept-123 --format json --output scan-evidence.json

# Download the canonical evidence report through the compatibility command
aelira report compliance dept-123 --pdf evidence-report.pdf
```

`aelira report certificate` remains as a deprecated alias. It downloads the same accessibility evidence report and does not perform an eligibility or score-threshold check.

### `aelira report analytics`

Historical trends. View historical compliance trends and deadline projections.

```bash
aelira report analytics [department_id]
  --api-url <url>
  --days <n>                # Days to look back (default: 30)
  --projection              # Show April 2027 ADA Title II deadline projection
  --format console|json
  --output <file>
  --timer
```

Examples:

```bash
# View 30-day trend
aelira report analytics

# View 90-day trend with projection
aelira report analytics dept-123 --days 90 --projection

# Export to JSON
aelira report analytics --format json --output analytics.json
```

## Issue management commands

### `aelira issues`

Issue tracker. Manage and track accessibility issues for team collaboration.

```bash
aelira issues <action> [issue_id]
  --api-url <url>
  --department <id>         # Department ID (default: default)
  --status <status>         # Filter/update status
  --severity <level>        # Filter by severity
  --to <user_id>           # User to assign to
  --message <text>         # Note message
  --limit <n>              # Max results (default: 50)
  --format console|json
  --output <file>
  --timer
```

| Action | What it does |
|---|---|
| `list` | List issues (with optional filters) |
| `stats` | Show issue statistics |
| `update` | Update issue status |
| `assign` | Assign issue to team member |
| `note` | Add note to issue |

Examples:

```bash
# List all open issues
aelira issues list --status open

# View issue statistics
aelira issues stats

# Update issue status
aelira issues update abc123 --status resolved

# Assign issue to team member
aelira issues assign abc123 --to user@example.com

# Add note to issue
aelira issues note abc123 --message "Fixed manually by adding alt text"

# Filter by severity
aelira issues list --severity critical --status open
```

## Cloud integration commands

### `aelira integrations`

Cloud integration status. View connection status for Google Workspace, Microsoft 365, Canvas LMS, Blackboard Learn, Moodle LMS, and D2L Brightspace integrations.

```bash
aelira integrations
  --api-url <url>
  --api-key <key>           # API key for authentication
  --format console|json
```

Examples:

```bash
# View integration status (interactive display)
aelira integrations

# Export to JSON
aelira integrations --format json

# Use with specific API endpoint
aelira integrations --api-url http://localhost:8000 --api-key your-api-key
```

The output includes:

- Connection status for each provider
- Connected account email/name
- Last sync timestamp
- Selected folder count

### `aelira integrations connect`

Connect Google Workspace, Microsoft 365, Canvas LMS, or Blackboard Learn accounts via OAuth 2.0.

**Privacy first:** after connecting, you must explicitly select which folders to sync. By default, NO files are synced until you choose folders with `aelira integrations folders`.

```bash
aelira integrations connect [provider]
  --api-url <url>
  --api-key <key>
  --instance-url <url>      # LMS instance URL (required for Canvas, Blackboard, Moodle, Brightspace)
```

| Provider | What it connects | Status |
|---|---|---|
| `google` | Google Workspace (Drive, Docs, Slides, Sheets) | |
| `microsoft` | Microsoft 365 (OneDrive, Word, PowerPoint, Excel) | |
| `canvas` | Canvas LMS | Integration verified; institution user-journey acceptance still needed |
| `blackboard` | Blackboard Learn | **Experimental** (implemented, not yet tested end to end) |
| `moodle` | Moodle LMS | **Experimental** (implemented, not yet tested end to end) |
| `brightspace` | D2L Brightspace | **Beta** (tested against a D2L developer instance, not recently re-verified) |

> LMS connector maturity varies. See the [integration status table](https://github.com/Aelira-AI/aelira-core#lms-integration-status) in the main README before relying on a connector in production.

Examples:

```bash
# Interactive provider selection
aelira integrations connect

# Connect Google Workspace
aelira integrations connect google

# Connect Microsoft 365
aelira integrations connect microsoft

# Connect Canvas LMS
aelira integrations connect canvas --instance-url https://canvas.university.edu

# Connect Blackboard
aelira integrations connect blackboard --instance-url https://blackboard.university.edu

# Connect Moodle LMS
aelira integrations connect moodle --instance-url https://moodle.university.edu

# Connect D2L Brightspace
aelira integrations connect brightspace --instance-url https://university.brightspace.com
```

<details>
<summary><strong>OAuth flow</strong></summary>

<br>

1. CLI generates authorization URL via backend
2. Opens browser automatically (with confirmation)
3. User logs in and grants permissions
4. Backend receives OAuth callback and stores encrypted tokens
5. User returns to CLI to select folders (next step)

</details>

### `aelira integrations folders`

Manage folder selection. **Privacy critical:** select which folders to sync from Google Drive or OneDrive. This prevents Aelira from scanning your entire cloud storage.

```bash
aelira integrations folders
  --api-url <url>
  --api-key <key>
  --provider google|microsoft
  --format console|json
```

Run with no flags for an interactive menu (view selected folders, select folders for a provider, or remove folders). Pass `--provider` to jump straight into selecting folders for that provider. There is no separate `list`/`select`/`remove` subcommand; those are menu choices, not CLI arguments.

Examples:

```bash
# Interactive menu: view, select, or remove synced folders
aelira integrations folders

# Select folders from Google Drive (interactive)
aelira integrations folders --provider google

# Select folders from OneDrive (interactive)
aelira integrations folders --provider microsoft
```

<details>
<summary><strong>Interactive selection and privacy guarantee</strong></summary>

<br>

Interactive selection:

- Browse folder hierarchy from your cloud storage
- Multi-select folders with checkboxes
- Subfolder syncing enabled by default
- Changes take effect on next sync

Folder access:

- Only selected folders are scanned
- No files are accessed until folders are explicitly chosen
- You can add/remove folders anytime

</details>

### `aelira integrations sync`

Trigger file sync. Manually trigger background sync jobs for connected cloud providers. Sync jobs discover new files, detect changes, and queue accessibility scans.

```bash
aelira integrations sync
  --api-url <url>
  --api-key <key>
```

Examples:

```bash
# Trigger sync for all connected providers
aelira integrations sync

# Sync with a specific API endpoint
aelira integrations sync --api-url http://localhost:8000 --api-key your-api-key
```

<details>
<summary><strong>What happens during sync</strong></summary>

<br>

1. Fetches file metadata from selected folders only
2. Detects new files and modified files (version changes)
3. Creates background scan jobs for changed files
4. Scans run automatically in the backend job queue
5. Results appear in dashboard and reports

Automatic sync:

- Webhooks detect file changes in real-time (Google Drive push notifications, Microsoft Graph subscriptions)
- Manual sync picks up changes that webhooks miss

Background processing:

- Sync jobs run asynchronously (non-blocking)
- Progress tracked in backend job queue
- Use `aelira report analytics` to view scan results

</details>

## Accessibility analysis commands

### `aelira focus`

Focus order analysis. Analyze keyboard focus order for WCAG 2.4.3 compliance. Detects focus traps, invisible elements, and illogical tab order.

```bash
aelira focus <URL or file>
  --max-tabs <n>            # Max TAB keys to simulate (default: 100)
  --format console|json
  --output <file>
  --timer
```

Examples:

```bash
aelira focus https://example.com
aelira focus ./index.html --format json --output focus-report.json
```

### `aelira cvd`

Color vision deficiency analysis. Simulate how your site appears to color-blind users (8% of males affected).

| CVD type | Description |
|---|---|
| Protanopia | Red-blind, 1% males |
| Deuteranopia | Green-blind, 1% males |
| Tritanopia | Blue-blind, rare |
| Protanomaly | Red-weak, 1% males |
| Deuteranomaly | Green-weak, 5% males (most common) |
| Tritanomaly | Blue-weak, rare |
| Achromatopsia | Complete color blindness |

```bash
aelira cvd <URL or file>
  --type <cvd_type>         # Specific CVD type to test
  --all-types               # Test all 7 CVD types
  --min-contrast <ratio>    # Minimum contrast threshold (default: 4.5)
  --format console|json
  --output <file>
```

Examples:

```bash
aelira cvd https://example.com
aelira cvd https://example.com --type deuteranopia
aelira cvd ./index.html --all-types --format json
```

## Bulk operations

### `aelira bulk`

Bulk directory operations. Scan entire directories with parallel processing, progress tracking, and resume support.

| Action | What it does |
|---|---|
| `scan` | Scan files for accessibility issues |
| `remediate` | Batch remediate files |
| `export` | Export results to CSV/JSON/HTML |
| `report` | Generate comprehensive HTML report |

```bash
aelira bulk <action> <path>
  --recursive               # Scan subdirectories
  --pattern <glob>          # File pattern (e.g., "*.pdf")
  --concurrency <n>         # Parallel scans (default: 3)
  --threshold <score>       # Minimum pass score (default: 80)
  --dry-run                 # Show what would be processed
  --resume <file>           # Resume from state file
  --format json|csv|html
  --output <file>
```

Examples:

```bash
# Scan all HTML files recursively
aelira bulk scan ./course-materials --recursive --pattern "*.html"

# Generate HTML report
aelira bulk report ./documents --output report.html

# Export to CSV
aelira bulk export ./scan-results.json --format csv --output results.csv
```

## Comparison and history

### `aelira diff`

Compare two scan results to track accessibility changes over time.

```bash
aelira diff <baseline.json> <current.json>
  --show-fixed              # Show issues that were fixed
  --show-unchanged          # Show unchanged issues
  --format console|json|html|markdown
  --output <file>
```

Examples:

```bash
aelira diff scan-v1.json scan-v2.json
aelira diff baseline.json current.json --format html --output diff-report.html
aelira diff old.json new.json --show-fixed
```

### `aelira history`

View and manage your scan history.

```bash
aelira history
  --target <filter>         # Filter by target URL/path
  --type <scan_type>        # Filter by type (website, pdf, ppt, etc.)
  --limit <n>               # Number of entries (default: 10)
  --export <file>           # Export history
  --clear                   # Clear all history
  --format table|json|csv
```

Examples:

```bash
aelira history
aelira history --target example.com --limit 20
aelira history --type pdf --format json
aelira history --export history.json
```

## Configuration

### `aelira config`

Manage CLI configuration with support for multiple profiles.

| Subcommand | What it does |
|---|---|
| `init` | Interactive setup wizard |
| `show` | Display current configuration |
| `set <key> <value>` | Set a configuration value |
| `validate` | Test backend connection |
| `profile list\|create\|use\|delete` | Manage profiles |

```bash
aelira config init
aelira config show
aelira config set api-url http://localhost:8000
aelira config validate
aelira config profile list
aelira config profile create staging
aelira config profile use staging
```

Valid `config set` keys: `api-url`, `api-key`, `department`.

Configuration file: `~/.aelira/config.json`

### `aelira auth login`

Authenticate the CLI with the Aelira backend. Three methods are supported:

1. **Email magic link:** enter your email, receive a magic link, click to authenticate
2. **Browser:** opens the dashboard in your browser to authenticate
3. **API key:** paste an existing API key directly

The authenticated API key is stored in your local config file. There is no flag to pick the method up front; `aelira auth login` always prompts you to choose.

```bash
aelira auth login
  --api-url <url>
```

Example:

```bash
# Prompts for a method (email magic link, browser, or paste an API key), then authenticates
aelira auth login
```

### `aelira auth logout`

Remove the stored API key from local config.

```bash
aelira auth logout
```

### `aelira export`

Export scan history to CSV or JSON.

```bash
aelira export
  --format csv|json               # Output format (default: csv)
  --output <file>                 # Output file path (required for CSV)
  --limit <n>                     # Max records to export (default: 50)
  --api-url <url>
```

Examples:

```bash
# Export to CSV
aelira export --output scans.csv

# Export to JSON
aelira export --format json --output scans.json

# Export last 100 scans
aelira export --limit 100 --output scans.csv
```

## Environment variables

Override configuration values via environment variables. This is useful for CI/CD pipelines and scripting.

| Variable | Description |
|----------|-------------|
| `AELIRA_API_URL` | Override API URL (e.g., `http://localhost:8000`) |
| `AELIRA_API_KEY` | Override API key for authentication |
| `AELIRA_DEPARTMENT` | Override department ID |
| `AELIRA_CONFIG_DIR` | Override config directory (default: `~/.aelira`). Useful for CI/testing to isolate config. |

Environment variables take precedence over values in the config file.

## Progress tracking

Document scan commands (`scan pdf`, `scan docx`, `scan ppt`, `scan xlsx`, `scan latex`, `scan code`, `scan web`, `scan image`, `scan video`) now show real-time progress during backend processing:

```
Scanning document.pdf... 45% - Analyzing structure
```

Progress includes a percentage and current operation description, updated as the backend processes the file.

## Automatic retry

The CLI automatically retries failed API requests with exponential backoff, which helps it ride out transient network issues and backend restarts.

| Setting | Value |
|---|---|
| Retries | 3 attempts |
| Retried status codes | 429 (rate limited), 502, 503, 504 (server errors) |
| Retried errors | Network connection failures, timeouts |
| Backoff | Exponential (increasing delay between retries) |

## CI/CD integration

### `aelira ci`

Dedicated command for CI/CD pipelines with proper exit codes, JSON, JUnit XML, SARIF 2.1.0, and badge generation.

```bash
aelira ci <URL or file>
  --threshold <score>       # Minimum score to pass (default: 80)
  --fail-on <severity>      # Fail on severity level (default: serious)
  --format console|json|junit|sarif
  --output <file>           # Output file for reports
  --badge <file>            # Generate SVG badge
  --timeout <ms>
```

| Exit code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | Accessibility issues found (threshold not met) |
| `2` | Error during scan |

Examples:

```bash
# Basic CI check
aelira ci https://example.com --threshold 85

# Generate JUnit XML for test frameworks
aelira ci ./dist --format junit --output results.xml

# Generate SARIF 2.1.0 for code-scanning tools
aelira ci ./dist --format sarif --output results.sarif

# Generate accessibility badge
aelira ci https://example.com --badge badge.svg

# Fail only on critical issues
aelira ci https://example.com --fail-on critical
```

SARIF source annotations are deliberately narrow. When the target is an HTML file inside the current repository, Aelira adds a location only when the affected node's exact HTML occurs once in that file. Remote pages, files outside the repository, and ambiguous or unmatched nodes remain in the report without a fabricated path or region.

### GitHub Actions example

```yaml
name: Accessibility Scan

on:
  pull_request:
    paths:
      - 'course-materials/**'

jobs:
  scan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v6

      - name: Install Aelira CLI
        run: npm install -g @aelira/cli

      - name: Run accessibility checks
        id: aelira
        continue-on-error: true
        run: |
          aelira ci ./dist \
            --threshold 85 \
            --format sarif \
            --output results.sarif \
            --badge accessibility-badge.svg

      - name: Upload SARIF to GitHub code scanning
        uses: github/codeql-action/upload-sarif@v4
        with:
          sarif_file: results.sarif

      - name: Upload badge
        uses: actions/upload-artifact@v4
        with:
          name: accessibility-badge
          path: accessibility-badge.svg

      - name: Enforce Aelira result
        if: steps.aelira.outcome == 'failure'
        run: exit 1
```

## Development

```bash
# Install dependencies
npm install

# Install Playwright (for website scanning)
npx playwright install chromium

# Build TypeScript
npm run build

# Test commands
./bin/dev.js scan test-sample.html
./bin/dev.js scan pdf test-files/sample.pdf
./bin/dev.js scan docx test-files/document.docx
./bin/dev.js report evidence
./bin/dev.js issues stats
```

## Backend requirements

The CLI commands require the Aelira backend API running. From the repository root (one level up from `cli/`):

```bash
cd ..
docker compose -f docker-compose.quickstart.yml up -d   # Start API, PostgreSQL, Redis
```

The API will be available at `http://localhost:8000`. See the root `README.md` for full setup options, including `docker-compose.dev.yml` for local development.

## Compliance context

Institutions covered by the DOJ ADA Title II rule need WCAG 2.1 AA by April 26, 2027 (public entities with population ≥50,000) or April 26, 2028 (smaller entities). That is extended one year from the original April 24, 2026 date under the Interim Final Rule RIN 1190-AA82 (April 20, 2026). `aelira report analytics --projection` estimates whether current progress is on track for that deadline.

## License

MIT © Aelira AI Pty Ltd. This licence covers the code under `cli/`; the root of this repository is licensed separately. See the [root README](https://github.com/Aelira-AI/aelira-core#licence-and-branding).

## Links

- **Backend:** [Repository root](https://github.com/Aelira-AI/aelira-core)
- **API Docs:** http://localhost:8000/docs (when the backend is running)
