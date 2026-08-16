# @aelira/cli

AI-powered accessibility testing CLI for WCAG 2.1 AA compliance, optimized for Higher Education.

> **AELIRA** — **A**ccessibility, **E**quity, **L**earning, **I**nclusion, **R**emediation, **A**utomation.

**Version:** 0.9.1

## Installation

```bash
npm install -g @aelira/cli
```

## Quick Start

### Interactive Mode (Recommended)

Launch the interactive CLI with beautiful ASCII art and menu navigation:

```bash
aelira interactive
```

This opens the main menu where you can:

- Scan Website
- Scan Documents (PDF, PowerPoint, LaTeX)
- Scan Media (Images, Videos)
- Scan Source Code
- Generate Compliance Report
- Settings & Configuration
- Help & Documentation

Word and Excel scanning (`aelira scan docx`, `aelira scan xlsx`) are not on the interactive menu yet — run them directly.

**Navigation:**

- Use arrow keys or number keys to select options
- Press Enter to confirm
- Press Ctrl+C to exit

---

## Document Scanning Commands

### `aelira scan pdf` - PDF Accessibility Scanner

Scan PDF files for accessibility issues with OCR and remediation support.

```bash
aelira scan pdf <file or directory>
  --api-url <url>           # Default: http://localhost:8000
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --skip-ocr                # Skip OCR processing
  --timer
```

**Examples:**
```bash
aelira scan pdf document.pdf
aelira scan pdf ./course-materials/
aelira scan pdf document.pdf --skip-ocr
```

---

### `aelira scan docx` - Word Document Scanner

Scan Word documents (.docx) for accessibility issues.

```bash
aelira scan docx <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

**Examples:**
```bash
aelira scan docx syllabus.docx
aelira scan docx ./documents/
aelira scan docx report.docx --format json
```

---

### `aelira scan xlsx` - Excel Spreadsheet Scanner

Scan Excel spreadsheets (.xlsx) for accessibility issues.

```bash
aelira scan xlsx <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

**Examples:**
```bash
aelira scan xlsx grades.xlsx
aelira scan xlsx ./spreadsheets/
aelira scan xlsx data.xlsx --format json
```

---

### `aelira scan ppt` - PowerPoint Accessibility Scanner

Scan PowerPoint presentations for accessibility issues (alt text, contrast, structure).

```bash
aelira scan ppt <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

**Examples:**
```bash
aelira scan ppt lecture.pptx
aelira scan ppt ./presentations/
```

---

### `aelira scan latex` - LaTeX to MathML Converter

Convert LaTeX equations to accessible MathML with ARIA labels.

**Advanced LaTeX support:**
- ChemFig chemical structure support
- mhchem chemistry notation
- Physics package (bra-ket, vectors, tensors)
- TikZ diagram descriptions
- Custom macro expansion

```bash
aelira scan latex <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --type math|chemistry|physics|diagram   # Content type hint
  --expand-macros                          # Expand custom macros (default: true)
  --timer
```

**Examples:**
```bash
aelira scan latex equations.tex
aelira scan latex chemistry.tex --type chemistry
aelira scan latex quantum.tex --type physics
aelira scan latex ./math-dept/
```

---

### `aelira scan watch` - Watch Directory for Changes

Watch a directory for file changes and automatically scan new or modified files.

```bash
aelira scan watch <directory>
  --extensions <list>             # File extensions to watch (default: .pdf,.docx,.pptx,.xlsx,.html,.htm,.tex,.css,.js)
  --debounce <ms>                 # Debounce delay in milliseconds (default: 2000)
  --concurrency <n>               # Max concurrent scans (default: 3)
  --no-recursive                  # Do not watch subdirectories
  --api-url <url>
```

**Examples:**
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

---

## Media Scanning Commands

### `aelira scan image` - AI Alt Text Generator

Generate AI-powered alt text for images using vision models.

```bash
aelira scan image <file or directory>
  --api-url <url>
  --format console|json
  --output <file>
  --batch                   # Use batch API (faster for multiple images)
  --timer
```

**Examples:**
```bash
aelira scan image photo.jpg
aelira scan image ./images/ --batch
```

---

### `aelira scan video` - Video/Audio Transcription

Transcribe video/audio files to accessible WebVTT and SRT captions using Whisper AI.

```bash
aelira scan video <file or directory>
  --api-url <url>
  --format console|json
  --output <file>
  --timer
```

**Examples:**
```bash
aelira scan video lecture.mp4
aelira scan video recording.mp3
aelira scan video ./videos/
```

---

## Web Scanning Commands

### `aelira scan` - Basic Website Scan

Fast, local-only website scanning using axe-core.

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

---

### `aelira scan web` - Advanced Web Scanner

Advanced web scanning with batch and sitemap support.

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

**Examples:**
```bash
# Single page scan
aelira scan web https://example.com

# Batch scan (crawl linked pages)
aelira scan web https://example.com --batch --max-pages 50

# Sitemap scan
aelira scan web https://example.com/sitemap.xml --sitemap
```

---

### `aelira analyze` - AI-Enhanced Website Scan

Scans with axe-core + sends violations to Aelira AI for classification and code fix generation.

```bash
aelira analyze <URL or HTML file>
  --api-url <backend_url>
  --generate-fixes
  --pdf <file>              # Temporarily unavailable (exits with an error)
  --format console|json
  --output <file>
  --timer
```

---

### `aelira scan code` - Code Accessibility Scanner

Scan source code for accessibility issues (ARIA attributes, semantic HTML, keyboard navigation).

```bash
aelira scan code <file or directory>
  --api-url <url>
  --format console|json|csv
  --output <file>           # Output file (for JSON or CSV)
  --timer
```

---

### CSV Output Format

All issue-based scan commands support `--format csv` for easy spreadsheet import:

```bash
aelira scan pdf document.pdf --format csv --output results.csv
aelira scan docx syllabus.docx --format csv --output results.csv
aelira scan web https://example.com --format csv --output results.csv
```

**Supported commands:** `scan pdf`, `scan docx`, `scan ppt`, `scan xlsx`, `scan latex`, `scan code`, `scan web`.

**Not supported:** `scan image` and `scan video` (these produce alt text and transcriptions, not issue lists).

---

## Canvas LMS Commands

Scan and remediate Canvas course content without leaving the terminal (requires a Canvas connection set up via `aelira integrations connect canvas`).

Every command below accepts `--department <id>` and falls back to the department set with `aelira config set department <id>`.

### `aelira canvas status` - Canvas Connection Status

Check whether a department's Canvas connection is set up and working.

```bash
aelira canvas status
  --department <id>         # Defaults to the configured department
  --api-url <url>
  --api-key <key>
  --format console|json
```

**Examples:**
```bash
aelira canvas status
aelira canvas status --department 42 --format json
```

---

### `aelira canvas courses` - List Canvas Courses

List the Canvas courses available to the connected account.

```bash
aelira canvas courses
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

**Examples:**
```bash
aelira canvas courses
aelira canvas courses --department 42 --format json
```

---

### `aelira canvas files` - List a Course's Files

List the files in a Canvas course. Use this to find the file id needed for `aelira canvas remediate`.

```bash
aelira canvas files <course_id>
  --search <text>            # Filter files by name
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

**Examples:**
```bash
aelira canvas files 101
aelira canvas files 101 --search syllabus --format json
```

---

### `aelira canvas scan` - Bulk-Scan a Course

Scan every file in a Canvas course for accessibility issues in one pass.

```bash
aelira canvas scan <course_id>
  --wait                      # Poll status until every file in the course finishes
  --department <id>
  --api-url <url>
  --api-key <key>
  --format console|json
```

**Examples:**
```bash
aelira canvas scan 101
aelira canvas scan 101 --wait --format json
```

---

### `aelira canvas remediate` - Remediate One File

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

**Examples:**
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

---

## Remediation Commands

### `aelira remediate` - Auto-Remediation Engine

Auto-remediate accessibility issues from a previous scan.

```bash
aelira remediate <scan_id>
  --api-url <url>
  --download                # Download remediated file
  --output <file>           # Output path for remediated file
  --format console|json
  --timer
```

**Examples:**
```bash
# Remediate a scan
aelira remediate abc123

# Remediate and download the fixed file
aelira remediate abc123 --download

# Remediate and save to specific path
aelira remediate abc123 --download --output fixed_document.pdf
```

---

## Compliance Reporting Commands

### `aelira report compliance` - Department Compliance Report

Generate department-wide compliance reports with priority issue ranking.

```bash
aelira report compliance [department_id]
  --api-url <url>
  --format console|json
  --output <file>
  --pdf <file>
  --timer
```

**Examples:**
```bash
aelira report compliance
aelira report compliance dept-123
aelira report compliance dept-123 --pdf report.pdf
```

---

### `aelira report certificate` - Compliance Certificate

Generate a professional compliance certificate (Bronze/Silver/Gold/Platinum).

Certificate levels based on compliance score:
- **Platinum** (95-100%): Exceptional Compliance Achievement
- **Gold** (90-94%): Excellent Compliance Achievement
- **Silver** (80-89%): Good Compliance Achievement
- **Bronze** (70-79%): Basic Compliance Achievement

```bash
aelira report certificate [department_id]
  --api-url <url>
  --check-eligibility       # Check eligibility without generating
  --output <file>
  --timer
```

**Examples:**
```bash
# Check if eligible for certificate
aelira report certificate --check-eligibility

# Generate certificate
aelira report certificate dept-123

# Save to specific path
aelira report certificate dept-123 --output certificate.pdf
```

---

### `aelira report analytics` - Historical Trends

View historical compliance trends and deadline projections.

```bash
aelira report analytics [department_id]
  --api-url <url>
  --days <n>                # Days to look back (default: 30)
  --projection              # Show April 2027 ADA Title II deadline projection
  --format console|json
  --output <file>
  --timer
```

**Examples:**
```bash
# View 30-day trend
aelira report analytics

# View 90-day trend with projection
aelira report analytics dept-123 --days 90 --projection

# Export to JSON
aelira report analytics --format json --output analytics.json
```

---

## Issue Management Commands

### `aelira issues` - Issue Tracker

Manage and track accessibility issues for team collaboration.

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

**Actions:**
- `list` - List issues (with optional filters)
- `stats` - Show issue statistics
- `update` - Update issue status
- `assign` - Assign issue to team member
- `note` - Add note to issue

**Examples:**
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

---

## Cloud Integration Commands

### `aelira integrations` - Cloud Integration Status

View connection status for Google Workspace, Microsoft 365, Canvas LMS, Blackboard Learn, Moodle LMS, and D2L Brightspace integrations.

```bash
aelira integrations
  --api-url <url>           # Default: http://localhost:8000
  --api-key <key>           # API key for authentication
  --format console|json
```

**Examples:**
```bash
# View integration status (interactive display)
aelira integrations

# Export to JSON
aelira integrations --format json

# Use with specific API endpoint
aelira integrations --api-url http://localhost:8000 --api-key your-api-key
```

**Output includes:**

- Connection status for each provider
- Connected account email/name
- Last sync timestamp
- Selected folder count

---

### `aelira integrations connect` - Connect Cloud Providers

Connect Google Workspace, Microsoft 365, Canvas LMS, or Blackboard Learn accounts via OAuth 2.0.

**PRIVACY-FIRST:** After connecting, you must explicitly select which folders to sync. By default, NO files are synced until you choose folders with `aelira integrations folders`.

```bash
aelira integrations connect [provider]
  --api-url <url>
  --api-key <key>
  --instance-url <url>      # LMS instance URL (required for Canvas, Blackboard, Moodle, Brightspace)
```

**Supported Providers:**

- `google` - Google Workspace (Drive, Docs, Slides, Sheets)
- `microsoft` - Microsoft 365 (OneDrive, Word, PowerPoint, Excel)
- `canvas` - Canvas LMS — **production-verified**
- `blackboard` - Blackboard Learn — **experimental** (implemented, not yet tested end to end)
- `moodle` - Moodle LMS — **experimental** (implemented, not yet tested end to end)
- `brightspace` - D2L Brightspace — **beta** (tested against a D2L developer instance, not recently re-verified)

> LMS connector maturity varies. See the [integration status table](../README.md#lms-integration-status) in the main README before relying on a connector in production.

**Examples:**
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

**OAuth Flow:**

1. CLI generates authorization URL via backend
2. Opens browser automatically (with confirmation)
3. User logs in and grants permissions
4. Backend receives OAuth callback and stores encrypted tokens
5. User returns to CLI to select folders (next step)

---

### `aelira integrations folders` - Manage Folder Selection

**PRIVACY-CRITICAL:** Select which folders to sync from Google Drive or OneDrive. This prevents Aelira from scanning your entire cloud storage.

```bash
aelira integrations folders
  --api-url <url>
  --api-key <key>
  --provider google|microsoft
  --format console|json
```

Run with no flags for an interactive menu (view selected folders, select folders for a provider, or remove folders). Pass `--provider` to jump straight into selecting folders for that provider; there is no separate `list`/`select`/`remove` subcommand — those are menu choices, not CLI arguments.

**Examples:**
```bash
# Interactive menu: view, select, or remove synced folders
aelira integrations folders

# Select folders from Google Drive (interactive)
aelira integrations folders --provider google

# Select folders from OneDrive (interactive)
aelira integrations folders --provider microsoft
```

**Interactive Selection:**

- Browse folder hierarchy from your cloud storage
- Multi-select folders with checkboxes
- Subfolder syncing enabled by default
- Changes take effect on next sync

**Privacy Guarantee:**

- Only selected folders are scanned
- No files are accessed until folders are explicitly chosen
- You can add/remove folders anytime
- All file access is logged

---

### `aelira integrations sync` - Trigger File Sync

Manually trigger background sync jobs for connected cloud providers. Sync jobs discover new files, detect changes, and queue accessibility scans.

```bash
aelira integrations sync
  --api-url <url>
  --api-key <key>
```

**Examples:**
```bash
# Trigger sync for all connected providers
aelira integrations sync

# Sync with a specific API endpoint
aelira integrations sync --api-url http://localhost:8000 --api-key your-api-key
```

**What Happens During Sync:**

1. Fetches file metadata from selected folders only
2. Detects new files and modified files (version changes)
3. Creates background scan jobs for changed files
4. Scans run automatically in the backend job queue
5. Results appear in dashboard and reports

**Automatic Sync:**

- Webhooks detect file changes in real-time (Google Drive push notifications, Microsoft Graph subscriptions)
- Manual sync ensures nothing is missed
- Typical sync time: 1-5 minutes for 1,000 files

**Background Processing:**

- Sync jobs run asynchronously (non-blocking)
- Progress tracked in backend job queue
- Use `aelira report analytics` to view scan results

---

## Accessibility Analysis Commands

### `aelira focus` - Focus Order Analysis

Analyze keyboard focus order for WCAG 2.4.3 compliance. Detects focus traps, invisible elements, and illogical tab order.

```bash
aelira focus <URL or file>
  --max-tabs <n>            # Max TAB keys to simulate (default: 100)
  --format console|json
  --output <file>
  --timer
```

**Examples:**

```bash
aelira focus https://example.com
aelira focus ./index.html --format json --output focus-report.json
```

---

### `aelira cvd` - Color Vision Deficiency Analysis

Simulate how your site appears to color-blind users (8% of males affected).

**CVD Types:**

- Protanopia (red-blind, 1% males)
- Deuteranopia (green-blind, 1% males)
- Tritanopia (blue-blind, rare)
- Protanomaly (red-weak, 1% males)
- Deuteranomaly (green-weak, 5% males - most common)
- Tritanomaly (blue-weak, rare)
- Achromatopsia (complete color blindness)

```bash
aelira cvd <URL or file>
  --type <cvd_type>         # Specific CVD type to test
  --all-types               # Test all 7 CVD types
  --min-contrast <ratio>    # Minimum contrast threshold (default: 4.5)
  --format console|json
  --output <file>
```

**Examples:**

```bash
aelira cvd https://example.com
aelira cvd https://example.com --type deuteranopia
aelira cvd ./index.html --all-types --format json
```

---

## Bulk Operations

### `aelira bulk` - Bulk Directory Operations

Scan entire directories with parallel processing, progress tracking, and resume support.

**Actions:**

- `scan` - Scan files for accessibility issues
- `remediate` - Batch remediate files
- `export` - Export results to CSV/JSON/HTML
- `report` - Generate comprehensive HTML report

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

**Examples:**

```bash
# Scan all HTML files recursively
aelira bulk scan ./course-materials --recursive --pattern "*.html"

# Generate HTML report
aelira bulk report ./documents --output report.html

# Export to CSV
aelira bulk export ./scan-results.json --format csv --output results.csv
```

---

## Comparison & History

### `aelira diff` - Compare Scan Results

Compare two scan results to track accessibility changes over time.

```bash
aelira diff <baseline.json> <current.json>
  --show-fixed              # Show issues that were fixed
  --show-unchanged          # Show unchanged issues
  --format console|json|html|markdown
  --output <file>
```

**Examples:**

```bash
aelira diff scan-v1.json scan-v2.json
aelira diff baseline.json current.json --format html --output diff-report.html
aelira diff old.json new.json --show-fixed
```

---

### `aelira history` - Scan History

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

**Examples:**

```bash
aelira history
aelira history --target example.com --limit 20
aelira history --type pdf --format json
aelira history --export history.json
```

---

## Configuration

### `aelira config` - Configuration Management

Manage CLI configuration with support for multiple profiles.

**Subcommands:**

- `init` - Interactive setup wizard
- `show` - Display current configuration
- `set <key> <value>` - Set a configuration value
- `validate` - Test backend connection
- `profile list|create|use|delete` - Manage profiles

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

**Configuration File:** `~/.aelira/config.json`

---

### `aelira auth login` - Authenticate CLI

Authenticate the CLI with the Aelira backend. Three methods are supported:

1. **Email magic link** - Enter your email, receive a magic link, click to authenticate
2. **Browser** - Opens the dashboard in your browser to authenticate
3. **API key** - Paste an existing API key directly

The authenticated API key is stored in your local config file. There is no flag to pick the method up front — `aelira auth login` always prompts you to choose.

```bash
aelira auth login
  --api-url <url>
```

**Example:**
```bash
# Prompts for a method (email magic link, browser, or paste an API key), then authenticates
aelira auth login
```

---

### `aelira auth logout` - Remove Authentication

Remove the stored API key from local config.

```bash
aelira auth logout
```

---

### `aelira export` - Export Scan History

Export scan history to CSV or JSON.

```bash
aelira export
  --format csv|json               # Output format (default: csv)
  --output <file>                 # Output file path (required for CSV)
  --limit <n>                     # Max records to export (default: 50)
  --api-url <url>
```

**Examples:**
```bash
# Export to CSV
aelira export --output scans.csv

# Export to JSON
aelira export --format json --output scans.json

# Export last 100 scans
aelira export --limit 100 --output scans.csv
```

---

## Environment Variables

Override configuration values via environment variables. Useful for CI/CD pipelines and scripting.

| Variable | Description |
|----------|-------------|
| `AELIRA_API_URL` | Override API URL (e.g., `http://localhost:8000`) |
| `AELIRA_API_KEY` | Override API key for authentication |
| `AELIRA_DEPARTMENT` | Override department ID |
| `AELIRA_CONFIG_DIR` | Override config directory (default: `~/.aelira`). Useful for CI/testing to isolate config. |

Environment variables take precedence over values in the config file.

---

## Progress Tracking

Document scan commands (`scan pdf`, `scan docx`, `scan ppt`, `scan xlsx`, `scan latex`, `scan code`, `scan web`, `scan image`, `scan video`) now show real-time progress during backend processing:

```
Scanning document.pdf... 45% - Analyzing structure
```

Progress includes a percentage and current operation description, updated as the backend processes the file.

---

## Automatic Retry

The CLI automatically retries failed API requests with exponential backoff:

- **Retries:** 3 attempts
- **Retried status codes:** 429 (rate limited), 502, 503, 504 (server errors)
- **Retried errors:** Network connection failures, timeouts
- **Backoff:** Exponential (increasing delay between retries)

This makes the CLI resilient to transient network issues and backend restarts.

---

## CI/CD Integration

### `aelira ci` - CI/CD Command

Dedicated command for CI/CD pipelines with proper exit codes, JUnit XML output, and badge generation.

```bash
aelira ci <URL or file>
  --threshold <score>       # Minimum score to pass (default: 80)
  --fail-on <severity>      # Fail on severity level (default: serious)
  --format console|json|junit
  --output <file>           # Output file for reports
  --badge <file>            # Generate SVG badge
  --timeout <ms>
```

**Exit Codes:**

- `0` - All checks passed
- `1` - Accessibility issues found (threshold not met)
- `2` - Error during scan

**Examples:**

```bash
# Basic CI check
aelira ci https://example.com --threshold 85

# Generate JUnit XML for test frameworks
aelira ci ./dist --format junit --output results.xml

# Generate accessibility badge
aelira ci https://example.com --badge badge.svg

# Fail only on critical issues
aelira ci https://example.com --fail-on critical
```

### GitHub Actions Example

```yaml
name: Accessibility Scan

on:
  pull_request:
    paths:
      - 'course-materials/**'

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install Aelira CLI
        run: npm install -g @aelira/cli

      - name: Run accessibility checks
        run: |
          aelira ci ./dist \
            --threshold 85 \
            --format junit \
            --output results.xml \
            --badge accessibility-badge.svg

      - name: Upload test results
        uses: actions/upload-artifact@v3
        with:
          name: accessibility-results
          path: results.xml

      - name: Upload badge
        uses: actions/upload-artifact@v3
        with:
          name: accessibility-badge
          path: accessibility-badge.svg
```

---

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
./bin/dev.js report compliance
./bin/dev.js issues stats
```

---

## Backend Requirements

The CLI commands require the Aelira backend API running. From the repository root (one level up from `cli/`):

```bash
cd ..
docker compose -f docker-compose.quickstart.yml up -d   # Start API, PostgreSQL, Redis
```

The API will be available at `http://localhost:8000` (see the root `README.md` for full setup options, including `docker-compose.dev.yml` for local development).

---

## Feature highlights

**Scanning** — PDF (with OCR), Word, PowerPoint, Excel, LaTeX→MathML, images (AI alt text), video/audio (transcription), source code, and websites (single, batch, or from a sitemap). Watch a directory to auto-scan on change.

**Remediation** — `aelira remediate` fixes issues and downloads the corrected file; `aelira diff` tracks regressions between scans.

**LMS & cloud** — Canvas course scanning and per-file remediation; cloud integration status, OAuth connect, folder selection, and background sync for Google, Microsoft, Blackboard.

**Reporting** — department compliance reports, historical trend analytics with deadline projection, and Bronze/Silver/Gold/Platinum compliance certificates.

**Team & CI** — issue tracker with assignments and filters; `aelira ci` emits exit codes, JUnit XML, and badges for pipelines; CSV/JSON export of scan history.

**Authentication** — magic-link, browser, or API-key login with multi-profile config, and automatic retry with backoff on transient API errors.

## Compliance context

Institutions covered by the DOJ ADA Title II rule need WCAG 2.1 AA by April 26, 2027 (public entities with population ≥50,000) or April 26, 2028 (smaller entities) — extended one year from the original April 24, 2026 date under the Interim Final Rule RIN 1190-AA82 (April 20, 2026). `aelira report analytics --projection` estimates whether current progress is on track for that deadline.

---

## License

MIT © Aelira. This licence covers the code under `cli/`; the root of this repository is licensed separately — see the [root README](../README.md#licence-and-branding).

---

## Links

- **Backend:** [Repository root](..)
- **API Docs:** http://localhost:8000/docs (when the backend is running)
