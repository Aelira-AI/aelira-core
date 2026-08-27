# Aelira CLI Usage Examples

**Version:** v0.4.0
**Last Updated:** March 17, 2026

Real-world usage examples for common accessibility workflows.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Authentication](#authentication)
- [Faculty Workflows](#faculty-workflows)
- [IT Administrator Workflows](#it-administrator-workflows)
- [Developer Workflows](#developer-workflows)
- [Batch Processing](#batch-processing)
- [Export](#export)
- [File Watcher](#file-watcher)
- [CSV Output](#csv-output)
- [Integration Examples](#integration-examples)

---

## Getting Started

### Install and Setup

```bash
# Install globally
npm install -g @aelira/cli

# Verify installation
aelira --version
# Output: v0.4.0

# Check help
aelira --help
```

### First Scan

```bash
# Scan a website
aelira scan https://example.com

# See detailed output
aelira scan https://example.com --verbose

# Generate PDF report
aelira scan https://example.com --pdf my-first-report.pdf
```

---

## Authentication

### Login with Email Magic Link

```bash
# Login with email magic link
aelira auth login
# → Select "Login with email"
# → Enter your .edu email
# → Paste the token from the email
```

### Login with an API Key

```bash
# Login by pasting an API key
aelira auth login
# → Select "Paste an API key"
# → Paste your key
```

### Logout

```bash
# Logout
aelira auth logout
```

---

## Faculty Workflows

### Scenario 1: Prepare Course Materials for Semester

**Professor Sarah teaches Biology 101 with 500+ files:**

```bash
# Step 1: Scan all PDFs in course directory
aelira scan pdf ./BIO101/lectures/ --batch --generate-alt-text

# Step 2: Process PowerPoint slides
aelira scan ppt ./BIO101/slides/ --batch --fix-contrast

# Step 3: Convert LaTeX equations
aelira scan latex ./BIO101/equations.tex --format html --output accessible-equations.html

# Step 4: Download the accessibility evidence report
aelira report evidence dept-biology --output BIO101-evidence.pdf
```

**Output:**
```
📄 Batch Processing: 47 PDFs found
⏳ Processing (AI alt text enabled)...
  [1/47] lecture-01.pdf ✓ (Score: 89/100, 12 images processed)
  [2/47] lecture-02.pdf ✓ (Score: 92/100, 8 images processed)
  ...
  [47/47] lab-manual.pdf ✓ (Score: 85/100, 34 images processed)

✅ Batch Complete
  Total Time: 18 minutes
  Average Score: 88/100
  Images Processed: 523
  Issues Found: 247
  Auto-Fixes Applied: 189
```

---

### Scenario 2: Fix Single Problematic Document

**Professor Mike needs to fix a PDF with many images:**

```bash
# Scan with detailed output
aelira scan pdf syllabus.pdf --generate-alt-text --verbose

# Review issues, then export accessible version
aelira scan pdf syllabus.pdf --export-html syllabus-accessible.html

# Generate scan report for accessibility review
aelira scan pdf syllabus.pdf --pdf syllabus-scan-report.pdf
```

**Workflow:**
1. Scan identifies 18 images without alt text
2. AI generates descriptions for all images
3. Export HTML version with alt text embedded
4. Submit PDF scan report to the accessibility review team

---

### Scenario 3: Video Lecture Captions

**Professor Chen records 15 lecture videos:**

```bash
# Process video with auto-generated captions
aelira scan video lecture-01.mp4 --captions youtube-auto.vtt --output corrected-01.vtt

# Batch process all lectures
for video in ./lectures/*.mp4; do
  caption="${video%.mp4}.vtt"
  output="${video%.mp4}-corrected.vtt"
  aelira scan video "$video" --captions "$caption" --output "$output"
done

# Verify all captions are WCAG compliant
echo "All captions processed!"
```

**Output:**
```
🎥 Processing: lecture-01.mp4 (1:24:30)

⏳ AI Caption Correction...
  Technical Terms Fixed: 147
    "Michaelis-Menten" (line 45)
    "photosynthesis" (line 89)
    "ATP synthase" (line 123)
  Timing Adjustments: 23
  Formatting Fixes: 12

✅ Complete
  Output: corrected-01.vtt
  WCAG 1.2.2 Compliant: ✓
  Processing Time: 42 seconds
```

---

## IT Administrator Workflows

### Scenario 4: Department-Wide Audit

**IT Director audits entire Computer Science department:**

```bash
# Download the bounded accessibility evidence report
aelira report evidence dept-cs --output CS-Evidence-Nov-2025.pdf

# Export scan evidence statistics through the deprecated compatibility view
aelira report compliance dept-cs --format json --output cs-scan-evidence.json
```

**Output:**
```
Department Scan Evidence Statistics

Department: Computer Science (dept-cs)
Faculty: 47 professors
Report Period: October 1 - November 1, 2025

Scan Evidence Summary:
  Files Scanned: 2,847
  Total Findings: 8,456
  Findings Marked Resolved: 6,789
  Average Scan Score: 82/100

Critical Findings (28 remaining):
  - 18 PDFs with missing headings
  - 10 videos without captions

Review Notes:
  1. Validate the 10 caption findings manually
  2. Review heading structure in the 18 flagged PDFs
  3. Use the evidence report's methods and limitations when interpreting results
```

---

### Scenario 5: Bulk Website Scanning

**IT team scans all university department websites:**

```bash
# Create list of URLs
cat > university-sites.txt << EOF
https://cs.university.edu
https://biology.university.edu
https://engineering.university.edu
https://arts.university.edu
EOF

# Scan all websites
while read url; do
  dept=$(echo "$url" | cut -d'/' -f3 | cut -d'.' -f1)
  aelira analyze "$url" --pdf "reports/${dept}-report.pdf"
  echo "✓ Scanned: $dept"
done < university-sites.txt

# Generate summary report
echo "✅ All department websites scanned"
ls -lh reports/
```

**Output:**
```
✓ Scanned: cs (Score: 92/100, 14 issues)
✓ Scanned: biology (Score: 78/100, 42 issues)
✓ Scanned: engineering (Score: 85/100, 28 issues)
✓ Scanned: arts (Score: 95/100, 8 issues)

✅ All department websites scanned

reports/
  cs-report.pdf          (1.2 MB)
  biology-report.pdf     (2.4 MB)
  engineering-report.pdf (1.8 MB)
  arts-report.pdf        (890 KB)
```

---

## Developer Workflows

### Scenario 6: CI/CD Integration

**Add accessibility testing to GitHub Actions:**

```yaml
# .github/workflows/accessibility.yml
name: Accessibility Testing

on: [push, pull_request]

jobs:
  accessibility-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Install Aelira CLI
        run: npm install -g @aelira/cli

      - name: Build website
        run: npm run build

      - name: Scan for accessibility issues
        run: |
          aelira scan ./dist/index.html --format json --output accessibility-report.json
          score=$(jq '.compliance_score' accessibility-report.json)
          echo "Automated scan score: $score/100"

          if [ "$score" -lt 80 ]; then
            echo "❌ Accessibility score below threshold (80)"
            exit 1
          fi

      - name: Upload report
        uses: actions/upload-artifact@v2
        with:
          name: accessibility-report
          path: accessibility-report.json
```

---

### Scenario 7: Pre-Commit Hook

**Prevent commits with accessibility issues:**

```bash
# .git/hooks/pre-commit
#!/bin/bash

echo "🔍 Running accessibility scan..."

# Find HTML files in staging area
html_files=$(git diff --cached --name-only --diff-filter=ACM | grep '\.html$')

if [ -z "$html_files" ]; then
  echo "No HTML files to scan"
  exit 0
fi

# Scan each file
for file in $html_files; do
  echo "Scanning: $file"
  aelira scan "$file" --threshold 80 --quiet

  if [ $? -ne 0 ]; then
    echo "❌ Accessibility issues found in $file"
    echo "Run: aelira scan $file"
    exit 1
  fi
done

echo "✅ All files passed accessibility check"
exit 0
```

---

### Scenario 8: Local Development Workflow

**Developer builds accessible React app:**

```bash
# Terminal 1: Run development server
npm start

# Terminal 2: Watch for accessibility issues
watch -n 5 'aelira scan http://localhost:3000 --threshold 85'

# When ready to commit
aelira scan http://localhost:3000 --pdf accessibility-report.pdf
git add .
git commit -m "feat: add accessible navigation"
```

---

## Batch Processing

### Process Entire Course Directory

```bash
#!/bin/bash
# process-course.sh - Batch process all course materials

COURSE_DIR="./MATH201"
OUTPUT_DIR="./MATH201-reports"

mkdir -p "$OUTPUT_DIR"

echo "📚 Processing Course: MATH201"
echo "================================"

# Process PDFs
echo "📄 Scanning PDFs..."
aelira scan pdf "$COURSE_DIR/pdfs/" --batch --generate-alt-text > "$OUTPUT_DIR/pdf-results.txt"

# Process PowerPoints
echo "📊 Scanning PowerPoints..."
aelira scan ppt "$COURSE_DIR/slides/" --batch --fix-contrast > "$OUTPUT_DIR/ppt-results.txt"

# Convert LaTeX
echo "🔬 Converting LaTeX..."
find "$COURSE_DIR" -name "*.tex" -exec aelira scan latex {} --output {}.mathml \;

# Download the accessibility evidence report
echo "📊 Generating Accessibility Evidence Report..."
aelira report evidence dept-math --output "$OUTPUT_DIR/evidence-report.pdf"

echo "✅ Course processing complete!"
echo "Reports saved to: $OUTPUT_DIR"
```

---

### Weekly Accessibility Evidence Check

```bash
#!/bin/bash
# weekly-evidence.sh - Run every Monday at 8 AM

# Departments to check
DEPARTMENTS=("cs" "biology" "engineering" "math" "physics")

DATE=$(date +%Y-%m-%d)
REPORT_DIR="./evidence-reports/$DATE"

mkdir -p "$REPORT_DIR"

for dept in "${DEPARTMENTS[@]}"; do
  echo "📊 Checking department: $dept"

  aelira report evidence "dept-$dept" \
    --output "$REPORT_DIR/$dept-evidence.pdf"

  echo "✓ Evidence report saved: $dept-evidence.pdf"
done

# Share reports with the accessibility review team
echo "📧 Sharing reports with accessibility-review@university.edu"
# (Add email command here)

echo "✅ Weekly accessibility evidence check complete!"
```

---

## Export

### Export Scan History

```bash
# Export scan history to CSV
aelira export --output scans.csv

# Export as JSON to stdout
aelira export --format json

# Export last 100 scans
aelira export --limit 100 --output full-report.csv
```

---

## File Watcher

### Watch a Directory for Changes

```bash
# Watch course materials directory
aelira scan watch ./course-materials

# Watch only PDF and DOCX files
aelira scan watch ./docs --extensions .pdf,.docx

# Non-recursive, faster debounce
aelira scan watch ./src --no-recursive --debounce 1000
```

---

## CSV Output

### Scan and Output as CSV

```bash
# Scan PDF and output as CSV
aelira scan pdf document.pdf --format csv --output issues.csv

# Scan website and pipe CSV to another tool
aelira scan web https://example.com --format csv | grep critical
```

---

## Integration Examples

### Integrate with LMS (Canvas)

```bash
# Export course materials from Canvas
canvas-cli export --course-id 12345 --output ./canvas-export/

# Scan all materials
aelira scan pdf ./canvas-export/files/ --batch --generate-alt-text

# Generate evidence report for the course
aelira report evidence dept-cs --output canvas-course-12345-evidence.pdf

# Upload report back to Canvas
canvas-cli upload --course-id 12345 --file canvas-course-12345-evidence.pdf
```

---

### Integrate with Blackboard

```bash
# Download course content
bb-cli download --course-id CS101 --output ./blackboard-export/

# Scan and fix
aelira scan pdf ./blackboard-export/ --batch --generate-alt-text --export-html

# Re-upload fixed content
bb-cli upload --course-id CS101 --directory ./blackboard-export-fixed/
```

---

### Slack Notifications

```bash
#!/bin/bash
# notify-evidence.sh - Send a Slack notification when evidence is refreshed

WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

# Download the latest bounded evidence report
aelira report evidence dept-cs --output department-evidence.pdf

# Send to Slack
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"The latest accessibility scan evidence report is ready for review. It records automated findings and limitations, not a conformance or legal determination."}' \
  "$WEBHOOK_URL"
```

---

## Troubleshooting Examples

### Debug Slow Scans

```bash
# Enable verbose logging
aelira scan https://example.com --verbose

# Check if backend is running
curl http://localhost:8000/api/scan/health

# Test with local file (faster)
aelira scan ./test.html --local
```

### Fix API Connection Issues

```bash
# Check backend status
curl http://localhost:8000/health

# Use custom API URL
aelira scan https://example.com --api-url http://localhost:8000

# Skip AI analysis if backend unavailable
aelira scan https://example.com --local
```

### Handle Large Batch Jobs

```bash
# Process in smaller chunks
find ./pdfs/ -name "*.pdf" | head -50 | xargs -I {} aelira scan pdf {}

# Use parallel processing
find ./pdfs/ -name "*.pdf" | parallel -j 4 aelira scan pdf {}

# Monitor progress
tail -f ~/.aelira/logs/batch-processing.log
```

---

## Best Practices

### 1. Always Use Configuration File

Create `.aelirarc.json`:
```json
{
  "apiUrl": "http://localhost:8000",
  "departmentId": "dept-cs",
  "defaultFormat": "console",
  "theme": "dark"
}
```

### 2. Version Control Reports

```bash
# Add reports to .gitignore if sensitive
echo "*.pdf" >> .gitignore
echo "accessibility-reports/" >> .gitignore

# Or commit for team visibility
git add reports/
git commit -m "docs: add accessibility evidence reports"
```

### 3. Schedule Regular Scans

```bash
# Add to crontab (every Monday at 8 AM)
0 8 * * 1 /usr/local/bin/aelira report evidence dept-cs --output /reports/weekly-evidence-$(date +\%Y-\%m-\%d).pdf
```

### 4. Combine Multiple Commands

```bash
# Process everything at once
aelira scan pdf ./course/ --batch --generate-alt-text && \
aelira scan ppt ./course/ --batch --fix-contrast && \
aelira report evidence dept-cs --output final-evidence-report.pdf
```

---

## Getting Help

- **CLI Help:** `aelira --help`
- **Command Help:** `aelira scan --help`
- **Examples:** `aelira examples` (shows common usage)
- **Documentation:** https://github.com/Aelira-AI/aelira-core/tree/main/cli/docs
- **Support:** https://github.com/Aelira-AI/aelira-core/issues

---

**Last Updated:** March 17, 2026
**CLI Version:** v0.4.0

**Made with 💜 by the Aelira team**
