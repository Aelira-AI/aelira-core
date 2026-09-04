# Aelira CLI Command Reference

**Version:** v0.9.7
**Last Updated:** August 31, 2026

Complete reference for all Aelira CLI commands.

---

## Table of Contents

- [Authentication](#authentication)
- [Interactive Mode](#interactive-mode)
- [Website Scanning](#website-scanning)
- [Document Scanning](#document-scanning)
- [Media Scanning](#media-scanning)
- [Code Scanning](#code-scanning)
- [Directory Watching](#directory-watching)
- [Accessibility Analysis](#accessibility-analysis)
- [Auto-Remediation](#auto-remediation)
- [Cloud Integrations](#cloud-integrations)
- [Canvas](#canvas)
- [Issue Management](#issue-management)
- [Accessibility Evidence Reports](#accessibility-evidence-reports)
- [Export](#export)
- [Utilities](#utilities)
- [CI/CD Integration](#cicd-integration)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Global Flags](#global-flags)

---

## Authentication

### `aelira auth` - Authenticate with Aelira

Log in or log out of the Aelira platform. Authentication stores an API key in local config for subsequent commands.

**Usage:**
```bash
aelira auth [action] [options]
```

**Arguments:**
- `action` - `login` or `logout` (default: `login`)

**Flags:**
- `--api-url <url>` - Backend URL (default: http://localhost:8000)

**Login Methods (interactive prompt):**
- **Email magic link:** Enter your email address, receive a magic link token, paste the token back into the CLI. An API key is created on the backend and stored locally.
- **Browser:** Opens the dashboard API keys page in your default browser so you can create and copy a key.
- **Paste API key:** Enter an existing API key directly.

**Logout:** Removes the API key from local config. Does not revoke the key on the backend.

**Examples:**
```bash
# Log in (interactive)
aelira auth login

# Log out
aelira auth logout
```

---

## Interactive Mode

### `aelira interactive`

Launch the interactive CLI with ASCII art and menu navigation.

**Usage:**
```bash
aelira interactive
```

**Features:**
- Beautiful ASCII art header
- Menu navigation (arrow keys or numbers)
- Color-coded output
- Contextual help
- Progress indicators

**Menu Options:**
1. Scan Website
2. Scan Documents (PDF, DOCX, XLSX, PPT, LaTeX)
3. Scan Media (Images, Videos)
4. Scan Source Code
5. Cloud Integrations
6. Download Accessibility Evidence Report
7. Help & Documentation
8. Exit

---

## Website Scanning

### `aelira scan` - Basic Website Scan

Fast, local-only website scanning using axe-core. No API connection required.

**Usage:**
```bash
aelira scan <URL or HTML file>
```

**Flags:**
- `--format <console|json|html|csv>` - Output format (default: console)
- `--output <file>` - Save output to file
- `--pdf <file>` - Generate PDF report (**temporarily unavailable** - the command exits with an error; use `--format json` or the console output)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--threshold <score>` - Minimum compliance score (0-100)
- `--timer` - Show execution time
- `--local` - Skip AI analysis (axe-core only)
- `--timeout <ms>` - Page load timeout (default: 30000)
- `--load-delay <ms>` - Wait after page load for SPAs

**Examples:**
```bash
# Basic scan
aelira scan https://example.com

# Save results as JSON
aelira scan https://example.com --format json --output report.json

# Scan local HTML file
aelira scan ./dist/index.html

# JSON output with threshold
aelira scan https://example.com --format json --threshold 80
```

---

### `aelira scan web` - Advanced Web Scanner

Advanced web scanner with batch processing and sitemap support.

**Usage:**
```bash
aelira scan web <URL> [options]
```

**Flags:**
- `--format <console|json|html|csv>` - Output format (default: console)
- `--sitemap` - Crawl sitemap.xml automatically
- `--batch` - Process multiple URLs
- `--max-pages <n>` - Maximum pages to scan
- `--parallel <n>` - Number of parallel scans

**Examples:**
```bash
# Scan with sitemap crawling
aelira scan web https://example.com --sitemap

# Batch scan with parallelization
aelira scan web urls.txt --batch --parallel 4
```

---

### `aelira analyze` - AI-Enhanced Analysis

Deep analysis with AI-generated code fixes. Requires backend API running.

**Usage:**
```bash
aelira analyze <URL or HTML file>
```

**Features:**
- RAG classification of issues
- AI-generated working code fixes
- WCAG context from knowledge base

**Examples:**
```bash
# AI-enhanced scan with code fixes
aelira analyze https://example.com

# Save the AI-enhanced results as JSON
aelira analyze https://example.com --format json --output ai-report.json
```

---

## Document Scanning

### `aelira scan pdf` - PDF Accessibility Scan

Scan PDF files for accessibility issues with OCR and structure tagging.

**Usage:**
```bash
aelira scan pdf <file|directory> [options]
```

**Flags:**
- `--format <console|json|csv>` - Output format (default: console)
- `--generate-alt-text` - Enable AI image alt text generation
- `--export-html <file>` - Export accessible HTML version
- `--batch` - Process all PDFs in directory
- `--ocr` - Force OCR processing
- `--pdf <file>` - Generate a PDF scan report

**Examples:**
```bash
# Scan single PDF
aelira scan pdf document.pdf

# With AI image analysis
aelira scan pdf document.pdf --generate-alt-text

# Batch process directory
aelira scan pdf ./documents/ --batch

# Export accessible HTML
aelira scan pdf document.pdf --export-html accessible.html
```

---

### `aelira scan docx` - Word Document Scan

Scan Microsoft Word documents for accessibility issues.

**Usage:**
```bash
aelira scan docx <file|directory> [options]
```

**Flags:**
- `--format <console|json|csv>` - Output format (default: console)
- `--generate-alt-text` - Generate alt text for images
- `--batch` - Process all DOCX files in directory
- `--export-html <file>` - Export accessible HTML

**Examples:**
```bash
# Scan Word document
aelira scan docx report.docx

# Batch process with alt text generation
aelira scan docx ./docs/ --batch --generate-alt-text
```

---

### `aelira scan xlsx` - Excel Spreadsheet Scan

Scan Excel spreadsheets for accessibility issues.

**Usage:**
```bash
aelira scan xlsx <file|directory> [options]
```

**Flags:**
- `--format <console|json|csv>` - Output format (default: console)
- `--batch` - Process all XLSX files in directory
- `--check-charts` - Analyze chart accessibility

**Examples:**
```bash
# Scan Excel file
aelira scan xlsx data.xlsx

# Batch process
aelira scan xlsx ./spreadsheets/ --batch
```

---

### `aelira scan ppt` - PowerPoint Accessibility Scan

Scan PowerPoint files for contrast violations and missing alt text.

**Usage:**
```bash
aelira scan ppt <file|directory> [options]
```

**Flags:**
- `--format <console|json|csv>` - Output format (default: console)
- `--batch` - Process all PPTX files in directory
- `--fix-contrast` - Auto-fix contrast issues
- `--generate-alt-text` - Generate AI alt text for images
- `--cvd-simulation` - Check with color vision deficiency simulation

**Examples:**
```bash
# Scan single PowerPoint
aelira scan ppt presentation.pptx

# With auto-fixes
aelira scan ppt presentation.pptx --fix-contrast --generate-alt-text

# Batch process with CVD check
aelira scan ppt ./slides/ --batch --cvd-simulation
```

---

### `aelira scan latex` - LaTeX to MathML Conversion

Convert LaTeX equations to accessible MathML with ARIA labels.

**Supported Packages:**
- amsmath, amssymb (standard math)
- ChemFig, mhchem (chemistry)
- physics, braket (physics notation)
- TikZ (diagrams - basic support)

**Usage:**
```bash
aelira scan latex <file|equation> [options]
```

**Flags:**
- `--output <file>` - Save MathML output
- `--format <mathml|html|csv>` - Output format
- `--aria-labels` - Include ARIA labels (default: true)
- `--chemistry` - Enable chemistry package support

**Examples:**
```bash
# Convert LaTeX file
aelira scan latex equations.tex

# Single equation
aelira scan latex "\frac{-b \pm \sqrt{b^2-4ac}}{2a}"

# Chemistry notation
aelira scan latex "H2O" --chemistry

# Export as HTML
aelira scan latex equations.tex --format html --output accessible.html
```

---

## Media Scanning

### `aelira scan image` - Image Alt Text Generation

Generate AI-powered alt text for images using Moondream2.

**Usage:**
```bash
aelira scan image <file|directory> [options]
```

**Flags:**
- `--batch` - Process all images in directory
- `--context <text>` - Provide context for better descriptions
- `--educational` - Use educational context mode
- `--format <text|json>` - Output format

**Examples:**
```bash
# Single image
aelira scan image photo.jpg

# Batch process
aelira scan image ./images/ --batch

# With educational context
aelira scan image diagram.png --context "Biology cell structure" --educational
```

---

### `aelira scan video` - Video/Audio Transcription

Generate or enhance video captions using faster-whisper.

**Usage:**
```bash
aelira scan video <file> [options]
```

**Flags:**
- `--captions <file>` - Upload existing captions (VTT/SRT)
- `--output <file>` - Save transcription output
- `--format <vtt|srt>` - Caption format (default: vtt)
- `--language <code>` - Caption language (default: en)
- `--audio-only` - Process audio files (mp3, wav)

**Examples:**
```bash
# Transcribe video
aelira scan video lecture.mp4

# Correct existing captions
aelira scan video lecture.mp4 --captions auto-captions.vtt --output corrected.vtt

# Audio transcription
aelira scan video podcast.mp3 --audio-only --format srt
```

---

## Code Scanning

### `aelira scan code` - Source Code Analysis

Analyze HTML, CSS, and JavaScript source code for accessibility issues.

**Usage:**
```bash
aelira scan code <file|directory> [options]
```

**Flags:**
- `--format <console|json|csv>` - Output format (default: console)
- `--batch` - Process all source files in directory
- `--include <pattern>` - File patterns to include
- `--exclude <pattern>` - File patterns to exclude
- `--fix` - Generate fix suggestions

**Examples:**
```bash
# Scan source file
aelira scan code src/components/Button.tsx

# Scan directory
aelira scan code ./src/ --batch --include "*.tsx,*.jsx"

# With fix suggestions
aelira scan code ./src/ --batch --fix
```

---

## Directory Watching

### `aelira scan watch` - Watch Directory for Changes

Watch a directory for file changes and automatically scan new or modified files for accessibility issues. File types are matched by extension and dispatched to the appropriate scanner.

**Usage:**
```bash
aelira scan watch <directory> [options]
```

**Arguments:**
- `directory` - Directory to watch (required)

**Flags:**
- `--extensions <list>` - Comma-separated file extensions to watch (default: `.pdf,.docx,.pptx,.xlsx,.html,.htm,.tex,.css,.js`)
- `--debounce <ms>` - Debounce delay in milliseconds before scanning after a change (default: 2000)
- `--concurrency <n>` - Maximum number of concurrent scans (default: 3)
- `--no-recursive` - Only watch the top-level directory (do not recurse into subdirectories)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)

**Behavior:**
- Detects file creation and modification events, debounces rapid changes, and queues scans based on file extension.
- Maintains a persistent Playwright browser instance for HTML/web scans to avoid repeated startup overhead.
- On Linux, falls back to non-recursive watching if recursive inotify is unavailable.

**Examples:**
```bash
# Watch course materials directory
aelira scan watch ./course-materials

# Watch with specific extensions
aelira scan watch ./docs --extensions .pdf,.docx

# Watch without recursion
aelira scan watch ./src --no-recursive
```

---

## Accessibility Analysis

### `aelira focus` - Focus Order Analysis

Analyze keyboard focus order for WCAG 2.4.3 compliance.

**Usage:**
```bash
aelira focus <URL> [options]
```

**Flags:**
- `--visualize` - Generate visual focus order diagram
- `--output <file>` - Save analysis report
- `--interactive` - Step through focus order manually

**Examples:**
```bash
# Analyze focus order
aelira focus https://example.com

# With visualization
aelira focus https://example.com --visualize --output focus-diagram.html
```

---

### `aelira cvd` - Color Vision Deficiency Simulation

Simulate how pages appear to users with color vision deficiencies.

**Supported Types:**
- Protanopia (red-blind)
- Deuteranopia (green-blind)
- Tritanopia (blue-blind)
- Protanomaly (red-weak)
- Deuteranomaly (green-weak)
- Tritanomaly (blue-weak)
- Achromatopsia (complete color blindness)

**Usage:**
```bash
aelira cvd <URL> [options]
```

**Flags:**
- `--type <type>` - CVD type to simulate (default: all)
- `--output <dir>` - Save simulation screenshots
- `--contrast-check` - Check contrast ratios under simulation

**Examples:**
```bash
# Simulate all CVD types
aelira cvd https://example.com

# Specific type with screenshots
aelira cvd https://example.com --type deuteranopia --output ./cvd-screenshots/

# With contrast verification
aelira cvd https://example.com --contrast-check
```

---

## Auto-Remediation

### `aelira remediate` - Auto-Fix Accessibility Issues

Automatically fix accessibility issues where possible.

**Usage:**
```bash
aelira remediate <file> [options]
```

**Flags:**
- `--dry-run` - Show what would be fixed without making changes
- `--backup` - Create backup before modifications
- `--issues <types>` - Comma-separated issue types to fix

**Examples:**
```bash
# Auto-remediate PDF
aelira remediate document.pdf

# Dry run to preview fixes
aelira remediate presentation.pptx --dry-run

# Fix specific issue types only
aelira remediate document.pdf --issues "alt-text,contrast" --backup
```

---

### `aelira bulk` - Bulk Operations

Process entire directories with parallel execution.

**Usage:**
```bash
aelira bulk <action> <path> [options]
```

**Actions:**
- `scan` - Scan all files
- `remediate` - Auto-fix all files
- `report` - Generate combined report

**Flags:**
- `--parallel <n>` - Number of parallel workers (default: 4)
- `--recursive` - Include subdirectories
- `--filter <pattern>` - File pattern filter

**Examples:**
```bash
# Bulk scan directory
aelira bulk scan ./course-materials/ --parallel 8

# Bulk remediate with filter
aelira bulk remediate ./docs/ --filter "*.pdf,*.pptx" --recursive
```

---

## Cloud Integrations

### `aelira integrations` - Integration Status

View status of all cloud integrations.

**Usage:**
```bash
aelira integrations
```

**Output shows:**
- Connected platforms (Google, Microsoft, Canvas, Blackboard, Moodle, Brightspace)
- Sync status
- Last sync time
- Folder configuration

---

### `aelira integrations connect` - Connect Cloud Platform

Set up OAuth 2.0 connection to cloud platforms.

**Usage:**
```bash
aelira integrations connect <platform>
```

**Platforms:**
- `google` - Google Workspace (Drive, Docs, Slides, Sheets)
- `microsoft` - Microsoft 365 (OneDrive, SharePoint)
- `canvas` - Canvas LMS
- `blackboard` - Blackboard LMS
- `moodle` - Moodle LMS
- `brightspace` - D2L Brightspace

**Examples:**
```bash
# Connect Google Workspace
aelira integrations connect google

# Connect Canvas LMS
aelira integrations connect canvas
```

---

### `aelira integrations folders` - Configure Sync Folders

Select which folders to sync for privacy-first scanning.

**Usage:**
```bash
aelira integrations folders <platform>
```

**Examples:**
```bash
# Configure Google Drive folders
aelira integrations folders google

# Configure SharePoint folders
aelira integrations folders microsoft
```

---

### `aelira integrations sync` - Manual Sync

Trigger manual sync with cloud platform.

**Usage:**
```bash
aelira integrations sync <platform> [options]
```

**Flags:**
- `--force` - Force full resync
- `--folder <id>` - Sync specific folder only

**Examples:**
```bash
# Sync Google Drive
aelira integrations sync google

# Force full resync
aelira integrations sync microsoft --force
```

---

## Canvas

Scan and remediate Canvas LMS course content without leaving the terminal. Useful once a department has already connected Canvas via `aelira integrations connect canvas`.

Every command below accepts `--department` and falls back to the department set with `aelira config set department <id>`, plus the standard `--api-url`, `--api-key`, and `--format` flags.

### `aelira canvas status` - Canvas Connection Status

Check whether a department's Canvas connection is set up and working.

**Usage:**
```bash
aelira canvas status [options]
```

**Flags:**
- `--department <id>` - Department id (defaults to the configured department)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--api-key <key>` - API key for authentication (optional in development)
- `--format <console|json>` - Output format (default: console)

**Examples:**
```bash
# Is Canvas connected?
aelira canvas status

# Check a specific department, machine-readable output
aelira canvas status --department 42 --format json
```

---

### `aelira canvas courses` - List Canvas Courses

List the Canvas courses available to the connected account.

**Usage:**
```bash
aelira canvas courses [options]
```

**Flags:**
- `--department <id>` - Department id (defaults to the configured department)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--api-key <key>` - API key for authentication (optional in development)
- `--format <console|json>` - Output format (default: console)

**Examples:**
```bash
aelira canvas courses

aelira canvas courses --department 42 --format json
```

---

### `aelira canvas files` - List a Course's Files

List the files in a Canvas course. Use this to find the file id needed for `aelira canvas remediate`.

**Usage:**
```bash
aelira canvas files <course-id> [options]
```

**Arguments:**
- `course-id` - Canvas course id (required)

**Flags:**
- `--search <text>` - Filter files by name
- `--department <id>` - Department id (defaults to the configured department)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--api-key <key>` - API key for authentication (optional in development)
- `--format <console|json>` - Output format (default: console)

**Examples:**
```bash
aelira canvas files 101

# Find the syllabus file id
aelira canvas files 101 --search syllabus --format json
```

---

### `aelira canvas scan` - Bulk-Scan a Course

Scan every file in a Canvas course for accessibility issues in one pass.

**Usage:**
```bash
aelira canvas scan <course-id> [options]
```

**Arguments:**
- `course-id` - Canvas course id (required)

**Flags:**
- `--wait` - Poll scan status until every file in the course finishes scanning, instead of returning immediately with the scan queued
- `--department <id>` - Department id (defaults to the configured department)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--api-key <key>` - API key for authentication (optional in development)
- `--format <console|json>` - Output format (default: console)

**Examples:**
```bash
# Queue a scan and return immediately
aelira canvas scan 101

# Queue a scan and wait for every file to finish
aelira canvas scan 101 --wait --format json
```

---

### `aelira canvas remediate` - Remediate One File

Fix accessibility issues in a single Canvas file, identified by course id and file id (get both from `aelira canvas courses` and `aelira canvas files`).

**`--upload-back` replaces the original file inside the live Canvas course with the remediated version. It defaults to off, and when used it prompts for confirmation before overwriting the file, unless `--yes` is also passed.**

The command *queues* a remediation job and returns a job id; the download, remediation and any write-back happen asynchronously after that. A successful submission is not the same as a finished write-back. Without a TTY (CI, piped input), `--upload-back` requires `--yes` and exits with an error otherwise.

**Usage:**
```bash
aelira canvas remediate <course-id> <file-id> [options]
```

**Arguments:**
- `course-id` - Canvas course id (required)
- `file-id` - Canvas file id (required)

**Flags:**
- `--upload-back` - Replace the file in Canvas with the remediated version (default: off; prompts for confirmation unless `--yes` is also set)
- `--yes`, `-y` - Skip the confirmation prompt for `--upload-back`
- `--no-ai` - Skip AI-generated fixes and apply structural fixes only
- `--department <id>` - Department id (defaults to the configured department)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)
- `--api-key <key>` - API key for authentication (optional in development)
- `--format <console|json>` - Output format (default: console)

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

## Issue Management

### `aelira issues` - Team Collaboration

Manage accessibility issues with team collaboration features.

**Usage:**
```bash
aelira issues <action> [options]
```

**Actions:**
- `list` - List issues
- `assign` - Assign issue to team member
- `update` - Update issue status
- `note` - Add note to issue
- `export` - Export issues to CSV/JSON

**Examples:**
```bash
# List all open issues
aelira issues list --status open

# Assign issue
aelira issues assign ISS-123 --to "jane@university.edu"

# Add note
aelira issues note ISS-123 "Fixed contrast on slide 5"

# Update status
aelira issues update ISS-123 --status resolved

# Export for reporting
aelira issues export --format csv --output issues.csv
```

---

## Accessibility Evidence Reports

### `aelira report evidence` - Accessibility Evidence Report

Download a PDF record of Aelira's scanned-content findings, methods, and
limitations. It does not determine conformance with an accessibility standard
or legal requirement.

**Usage:**
```bash
aelira report evidence [department-id] [options]
```

**Flags:**
- `--api-url <url>` - Aelira API URL
- `--output <file>` - Evidence report PDF path
- `--timer` - Show performance timing information

**Examples:**
```bash
# Download an evidence report
aelira report evidence dept-123 --output evidence-report.pdf
```

---

### `aelira report compliance` - Deprecated Scan-Evidence Statistics View

This compatibility command exposes console or JSON scan statistics and
findings. Its PDF option downloads the canonical accessibility evidence report.

**Usage:**
```bash
aelira report compliance [department-id] [options]
```

**Flags:**
- `--api-url <url>` - Aelira API URL
- `--format <console|json>` - Statistics output format
- `--output <file>` - JSON output path
- `--pdf <file>` - Accessibility evidence report PDF path
- `--timer` - Show performance timing information

**Examples:**
```bash
# Export scan evidence statistics
aelira report compliance dept-123 --format json --output scan-evidence.json

# Download the evidence PDF through the compatibility command
aelira report compliance dept-123 --pdf evidence-report.pdf
```

`aelira report certificate` is a deprecated alias for `aelira report evidence`.
It downloads the same evidence PDF without eligibility, threshold, or award
logic.

---

### `aelira report analytics` - Historical Trends

View historical compliance trends and April 2027 ADA Title II deadline projections.

**Usage:**
```bash
aelira report analytics [options]
```

**Flags:**
- `--period <days>` - Analysis period (default: 90)
- `--projection` - Include deadline projection
- `--export <file>` - Export data

**Examples:**
```bash
# View analytics
aelira report analytics

# With deadline projection
aelira report analytics --projection --period 180
```

---

## Export

### `aelira export` - Export Scan History

Export scan history for your department. The department is determined by the authenticated API key.

**Usage:**
```bash
aelira export [options]
```

**Flags:**
- `--format <csv|json>` - Export format (default: csv)
- `--output <file>` - Output file path (required for CSV format)
- `--limit <n>` - Maximum number of scan records to export (default: 50)
- `--api-url <url>` - Backend URL (default: http://localhost:8000)

**CSV Columns:** `scan_id`, `date`, `file`, `issue`, `severity`, `rule`, `element`

**Examples:**
```bash
# Export as CSV
aelira export --output scans.csv

# Export as JSON (prints to stdout)
aelira export --format json

# Export with limit
aelira export --limit 100 --output report.csv
```

---

## Utilities

### `aelira diff` - Compare Scan Results

Compare two scan results to track progress.

**Usage:**
```bash
aelira diff <old-scan> <new-scan> [options]
```

**Examples:**
```bash
# Compare scans
aelira diff scan-2025-12-01.json scan-2026-01-25.json

# Output diff report
aelira diff old.json new.json --output diff-report.html
```

---

### `aelira history` - Scan History

View and manage scan history.

**Usage:**
```bash
aelira history [options]
```

**Flags:**
- `--limit <n>` - Number of entries to show
- `--filter <term>` - Filter by filename or URL
- `--clear` - Clear history

**Examples:**
```bash
# View recent scans
aelira history --limit 20

# Filter by term
aelira history --filter "syllabus"
```

---

## CI/CD Integration

### `aelira ci` - CI/CD Mode

Run scans in CI/CD pipelines with proper exit codes and console, JSON, JUnit, or SARIF 2.1.0 output.

**Usage:**
```bash
aelira ci <target> [options]
```

**Flags:**
- `--threshold <score>` - Minimum passing score (default: 80)
- `--format <console|json|junit|sarif>` - Report format (default: console)
- `--output <file>` - Output path for JSON, JUnit, or SARIF reports
- `--badge <file>` - Generate status badge
- `--fail-on <level>` - Fail on issue severity (critical, serious, moderate, minor)

**Examples:**
```bash
# CI scan with threshold
aelira ci https://example.com --threshold 90

# With JUnit output for CI
aelira ci ./build/ --format junit --output results.xml --fail-on serious

# With SARIF 2.1.0 output for code scanning
aelira ci ./build/ --format sarif --output results.sarif

# Generate badge
aelira ci https://example.com --badge badge.svg
```

SARIF locations are emitted only for targets inside the current repository when an axe node's exact HTML appears once in the scanned source file. Remote pages and ambiguous or unmatched nodes stay in the results without a guessed file path or region.

**Exit Codes:**
- `0` - Pass (score >= threshold)
- `1` - Fail (score < threshold)
- `2` - Error

**GitHub Actions Example:**
```yaml
permissions:
  contents: read
  security-events: write

steps:
  - uses: actions/checkout@v6
  - name: Install Aelira CLI
    run: npm install -g @aelira/cli
  - name: Accessibility check
    id: aelira
    continue-on-error: true
    run: aelira ci ./dist/ --threshold 85 --format sarif --output aelira.sarif
  - name: Upload SARIF
    uses: github/codeql-action/upload-sarif@v4
    with:
      sarif_file: aelira.sarif
  - name: Enforce Aelira result
    if: steps.aelira.outcome == 'failure'
    run: exit 1
```

The upload step is separate by design: Aelira writes the local report and never sends it to a vendor. See [GitHub's SARIF upload documentation](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file) for repository eligibility and workflow details.

---

## Configuration

### `aelira config` - Configuration Management

Manage CLI configuration and profiles.

**Usage:**
```bash
aelira config <action> [options]
```

**Actions:**
- `show` - Show current configuration
- `set <key> <value>` - Set configuration value
- `profile <name>` - Switch profile
- `init` - Initialize configuration

**Examples:**
```bash
# Show config
aelira config show

# Set API URL
aelira config set apiUrl http://localhost:8000

# Set API key
aelira config set apiKey your-api-key

# Switch profile
aelira config profile production

# Initialize
aelira config init
```

**Configuration File Location:** `~/.aelira/config.json`

**Supported Options:**
- `apiUrl` - Backend API URL
- `apiKey` - API authentication key
- `departmentId` - Department ID for compliance tracking
- `defaultFormat` - Default output format
- `timeout` - Request timeout (ms)

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AELIRA_CONFIG_DIR` | Override the default configuration directory | `~/.aelira` |
| `AELIRA_API_URL` | Default backend API URL | `http://localhost:8000` |

---

## Global Flags

These flags work with all commands:

- `--help, -h` - Show help for command
- `--version, -v` - Show CLI version
- `--verbose` - Enable verbose logging
- `--quiet, -q` - Suppress non-error output
- `--no-color` - Disable colored output
- `--profile <name>` - Use specific config profile

---

## Exit Codes

- `0` - Success
- `1` - General error / CI threshold not met
- `2` - Invalid arguments
- `3` - File not found
- `4` - API connection error
- `5` - Scan failed
- `130` - User interrupted (Ctrl+C)

---

## Getting Help

- **CLI Help:** `aelira --help`
- **Command Help:** `aelira <command> --help`
- **Documentation:** https://github.com/Aelira-AI/aelira-core/tree/main/cli/docs
- **GitHub Issues:** https://github.com/Aelira-AI/aelira-core/issues
- **Support:** https://github.com/Aelira-AI/aelira-core/issues

---

**Last Updated:** August 31, 2026
**CLI Version:** v0.9.7
**Status:** Beta

**Made with by the Aelira team**
