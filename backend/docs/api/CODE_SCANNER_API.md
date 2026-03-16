# Code Scanner API Documentation

**Endpoint:** `POST /api/education/code/scan`

**Version:** v0.13.0

**Description:** Perform static analysis on HTML, CSS, and JavaScript files for WCAG 2.2 Level AA compliance without requiring deployment or browser rendering.

---

## Authentication

All requests require an API key in the header:

```bash
X-API-Key: your-api-key-here
```

---

## Request

### Endpoint

```
POST https://aelira.ai/api/education/code/scan
```

### Content Type

```
multipart/form-data
```

### Form Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | file | ✅ Yes | - | Code file or ZIP archive to scan |
| `scan_images` | boolean | No | false | Analyze images found in HTML with AI |
| `generate_fixes` | boolean | No | true | Generate AI-powered code fixes for critical/serious issues |

### Accepted File Types

- `.html`, `.htm` - HTML files
- `.css` - Stylesheet files
- `.js` - JavaScript files
- `.zip` - ZIP archive containing HTML/CSS/JS files

### Examples

#### Single HTML File

```bash
curl -X POST "https://aelira.ai/api/education/code/scan" \
  -H "X-API-Key: your-api-key" \
  -F "file=@index.html" \
  -F "scan_images=false" \
  -F "generate_fixes=true"
```

#### ZIP Project

```bash
curl -X POST "https://aelira.ai/api/education/code/scan" \
  -H "X-API-Key: your-api-key" \
  -F "file=@website-project.zip" \
  -F "scan_images=true" \
  -F "generate_fixes=true"
```

#### Python Example

```python
import requests

url = "https://aelira.ai/api/education/code/scan"
headers = {"X-API-Key": "your-api-key"}

# Scan single file
with open("index.html", "rb") as f:
    files = {"file": f}
    data = {
        "scan_images": "true",
        "generate_fixes": "true"
    }
    response = requests.post(url, headers=headers, files=files, data=data)

result = response.json()

print(f"Project: {result['project_name']}")
print(f"Files Analyzed: {result['files_analyzed']}")
print(f"Compliance Score: {result['compliance_score']}%")
print(f"Critical Issues: {result['summary']['critical']}")

# Display AI-generated fixes
for issue in result['issues']:
    if issue['ai_generated_fix']:
        print(f"\n{issue['description']}")
        print(f"Fix: {issue['ai_generated_fix']}")
```

#### JavaScript (Node.js) Example

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

const scanCode = async (filePath) => {
  const form = new FormData();
  form.append('file', fs.createReadStream(filePath));
  form.append('scan_images', 'true');
  form.append('generate_fixes', 'true');

  const response = await axios.post(
    'https://aelira.ai/api/education/code/scan',
    form,
    {
      headers: {
        'X-API-Key': 'your-api-key',
        ...form.getHeaders()
      }
    }
  );

  const result = response.data;

  console.log(`Project: ${result.project_name}`);
  console.log(`Compliance Score: ${result.compliance_score}%`);
  console.log(`Total Issues: ${Object.values(result.summary).reduce((a, b) => a + b, 0)}`);

  return result;
};

// Usage
scanCode('./website-project.zip');
```

#### JavaScript (Browser) Example

```javascript
const scanCodeFile = async (fileInput) => {
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('scan_images', 'true');
  formData.append('generate_fixes', 'true');

  const response = await fetch('https://aelira.ai/api/education/code/scan', {
    method: 'POST',
    headers: {
      'X-API-Key': 'your-api-key'
    },
    body: formData
  });

  const result = await response.json();

  console.log(`Compliance Score: ${result.compliance_score}%`);
  console.log(`Issues Found: ${result.issues.length}`);

  // Display recommendations
  result.recommendations.forEach(rec => console.log(`- ${rec}`));

  return result;
};

// Usage with file input
document.getElementById('fileInput').addEventListener('change', (e) => {
  scanCodeFile(e.target);
});
```

---

## Response

### Success Response (200 OK)

```json
{
  "success": true,
  "scan_id": "660e8400-e29b-41d4-a716-446655440000",
  "project_name": "course-portal.zip",
  "files_analyzed": 15,
  "total_lines": 3542,
  "compliance_score": 68.5,
  "scan_time": 4.2,
  "summary": {
    "critical": 8,
    "serious": 15,
    "moderate": 22,
    "minor": 10
  },
  "recommendations": [
    "Fix 8 critical issues immediately (especially image alt text)",
    "Address 15 serious issues (form labels, ARIA, keyboard access)",
    "Add alt text to 12 images",
    "Review HTML semantic structure and form labels",
    "Validate ARIA usage and keyboard accessibility"
  ],
  "issues": [
    {
      "severity": "critical",
      "category": "html",
      "rule": "image-alt",
      "description": "Image missing alt text: logo.png",
      "file_path": "index.html",
      "line_number": 42,
      "code_snippet": "<img src=\"logo.png\">",
      "fix_suggestion": "Add descriptive alt text: alt=\"description of image\"",
      "ai_generated_fix": "<img src=\"logo.png\" alt=\"University logo with shield and laurel wreath\">",
      "wcag_criterion": "1.1.1"
    },
    {
      "severity": "serious",
      "category": "html",
      "rule": "form-label",
      "description": "Form input missing label: email",
      "file_path": "contact.html",
      "line_number": 87,
      "code_snippet": "<input type=\"email\" name=\"email\" placeholder=\"Your Email\">",
      "fix_suggestion": "Add <label for=\"input-id\"> or aria-label attribute",
      "ai_generated_fix": "<label for=\"email-input\">Email Address</label>\n<input type=\"email\" id=\"email-input\" name=\"email\" placeholder=\"Your Email\">",
      "wcag_criterion": "3.3.2"
    },
    {
      "severity": "serious",
      "category": "css",
      "rule": "focus-indicator",
      "description": "Focus indicator removed (outline: none)",
      "file_path": "styles.css",
      "line_number": 125,
      "code_snippet": "button:focus {\n  outline: none;\n}",
      "fix_suggestion": "Provide alternative visible focus indicator",
      "ai_generated_fix": "button:focus {\n  outline: 2px solid #0066cc;\n  outline-offset: 2px;\n}",
      "wcag_criterion": "2.4.7"
    },
    {
      "severity": "moderate",
      "category": "javascript",
      "rule": "keyboard-handler",
      "description": "Click handlers detected without keyboard equivalents",
      "file_path": "script.js",
      "line_number": 45,
      "code_snippet": null,
      "fix_suggestion": "Add keydown/keypress event listeners for keyboard users",
      "ai_generated_fix": "// Add keyboard support\nelement.addEventListener('click', handleClick);\nelement.addEventListener('keydown', (e) => {\n  if (e.key === 'Enter' || e.key === ' ') {\n    e.preventDefault();\n    handleClick(e);\n  }\n});",
      "wcag_criterion": "2.1.1"
    },
    {
      "severity": "serious",
      "category": "html",
      "rule": "heading-hierarchy",
      "description": "Heading hierarchy skipped level (from h1 to h3)",
      "file_path": "about.html",
      "line_number": 64,
      "code_snippet": "<h3>Our Mission</h3>",
      "fix_suggestion": "Use sequential heading levels (h1, h2, h3, etc.)",
      "ai_generated_fix": "<h2>Our Mission</h2>",
      "wcag_criterion": "2.4.6"
    },
    {
      "severity": "moderate",
      "category": "html",
      "rule": "landmark-main",
      "description": "Page should have a main landmark",
      "file_path": "services.html",
      "line_number": null,
      "code_snippet": null,
      "fix_suggestion": "Add <main> element or role=\"main\"",
      "ai_generated_fix": "<main>\n  <!-- Main content goes here -->\n</main>",
      "wcag_criterion": "1.3.1"
    },
    {
      "severity": "serious",
      "category": "aria",
      "rule": "button-keyboard",
      "description": "Element with role=\"button\" must be keyboard accessible",
      "file_path": "dashboard.html",
      "line_number": 102,
      "code_snippet": "<div role=\"button\" onclick=\"handleClick()\">Submit</div>",
      "fix_suggestion": "Add tabindex=\"0\" to make element keyboard accessible",
      "ai_generated_fix": "<div role=\"button\" tabindex=\"0\" onclick=\"handleClick()\" onkeydown=\"if(event.key==='Enter' || event.key===' '){handleClick()}\">Submit</div>",
      "wcag_criterion": "2.1.1"
    },
    {
      "severity": "moderate",
      "category": "css",
      "rule": "font-size",
      "description": "Font size too small: 10px",
      "file_path": "mobile.css",
      "line_number": 78,
      "code_snippet": ".small-text {\n  font-size: 10px;\n}",
      "fix_suggestion": "Use font-size >= 12px or relative units (em, rem)",
      "ai_generated_fix": ".small-text {\n  font-size: 0.875rem; /* 14px */\n}",
      "wcag_criterion": "1.4.4"
    }
  ],
  "images": [
    {
      "src": "logo.png",
      "alt": null,
      "has_alt": false,
      "is_decorative": false,
      "file_path": "index.html",
      "suggested_alt": "University logo with shield and laurel wreath"
    },
    {
      "src": "banner.jpg",
      "alt": "",
      "has_alt": true,
      "is_decorative": true,
      "file_path": "index.html",
      "suggested_alt": null
    },
    {
      "src": "chart.png",
      "alt": "Statistics",
      "has_alt": true,
      "is_decorative": false,
      "file_path": "research.html",
      "suggested_alt": "Bar chart showing 40% increase in enrollment from 2020 to 2024"
    }
  ]
}
```

### Response Fields

#### Root Level

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the scan completed successfully |
| `scan_id` | string (UUID) | Unique identifier for this scan |
| `project_name` | string | Name of the uploaded file |
| `files_analyzed` | integer | Number of HTML/CSS/JS files analyzed |
| `total_lines` | integer | Total lines of code analyzed |
| `compliance_score` | float | Overall compliance score (0-100) |
| `scan_time` | float | Total scan time in seconds |
| `summary` | object | Issue counts by severity |
| `recommendations` | array[string] | Top 5 actionable recommendations |
| `issues` | array | Detailed list of all issues found |
| `images` | array | Image analysis results |

#### Issue Object

| Field | Type | Description |
|-------|------|-------------|
| `severity` | string | critical, serious, moderate, minor |
| `category` | string | html, css, javascript, aria |
| `rule` | string | Rule identifier (e.g., "image-alt", "form-label") |
| `description` | string | Human-readable description |
| `file_path` | string | Relative path to file with issue |
| `line_number` | integer\|null | Line number (if applicable) |
| `code_snippet` | string\|null | Code excerpt showing the issue |
| `fix_suggestion` | string | Manual fix instructions |
| `ai_generated_fix` | string\|null | AI-generated code fix (if `generate_fixes=true`) |
| `wcag_criterion` | string | WCAG criterion number (e.g., "1.1.1") |

#### Image Object

| Field | Type | Description |
|-------|------|-------------|
| `src` | string | Image source attribute |
| `alt` | string\|null | Current alt text (if any) |
| `has_alt` | boolean | Whether alt attribute exists |
| `is_decorative` | boolean | Whether image is decorative (alt="") |
| `file_path` | string | File containing the image |
| `suggested_alt` | string\|null | AI-generated alt text (if `scan_images=true` and missing) |

---

## Compliance Scoring

The compliance score is calculated using a weighted system:

### Severity Weights

- **Critical:** 4 points per issue
- **Serious:** 3 points per issue
- **Moderate:** 2 points per issue
- **Minor:** 1 point per issue

### Score Formula

```
weighted_issues = (critical × 4) + (serious × 3) + (moderate × 2) + (minor × 1)
max_possible = files_analyzed × 20
score = max(0, 100 - (weighted_issues / max_possible × 100))
```

### Score Interpretation

| Score | Rating | Description |
|-------|--------|-------------|
| 90-100 | Excellent | Minimal issues, WCAG 2.2 Level AA compliant |
| 75-89 | Good | Some improvements needed |
| 60-74 | Fair | Multiple issues requiring attention |
| 40-59 | Poor | Significant accessibility problems |
| 0-39 | Critical | Major compliance failures |

---

## Error Responses

### 400 Bad Request - Invalid File Type

```json
{
  "detail": "Unsupported file type: .pdf. Allowed: .html, .htm, .css, .js, .zip"
}
```

### 400 Bad Request - File Too Large

```json
{
  "detail": "File size exceeds maximum limit of 50MB"
}
```

### 401 Unauthorized

```json
{
  "detail": "X-API-Key header required"
}
```

### 403 Forbidden

```json
{
  "detail": "Invalid API key"
}
```

### 413 Payload Too Large

```json
{
  "detail": "ZIP archive contains too many files. Maximum 100 files."
}
```

### 500 Internal Server Error

```json
{
  "detail": "Failed to scan code: Invalid HTML syntax in index.html"
}
```

---

## ZIP Archive Requirements

### Structure

ZIP archives should contain HTML, CSS, and/or JavaScript files:

```
website-project.zip
├── index.html
├── about.html
├── contact.html
├── css/
│   ├── styles.css
│   └── mobile.css
└── js/
    ├── main.js
    └── utils.js
```

### Limitations

- **Maximum file size:** 50MB
- **Maximum files:** 100 files per ZIP
- **Supported files:** Only .html, .htm, .css, .js are analyzed
- **Other files:** Images, fonts, etc. are ignored (unless `scan_images=true`)

### Best Practices

1. **Clean builds:** Remove node_modules, .git, build artifacts
2. **Flat structure:** Keep directory nesting to reasonable levels
3. **Production code:** Upload minified/compiled code if that's what's deployed

---

## Use Cases

### 1. Pre-Deployment Validation

Scan code before deploying to production:

```bash
#!/bin/bash
# pre-deploy.sh

# Build production code
npm run build

# Create ZIP of build folder
zip -r build.zip dist/

# Scan for accessibility issues
RESULT=$(curl -X POST "https://aelira.ai/api/education/code/scan" \
  -H "X-API-Key: $AELIRA_API_KEY" \
  -F "file=@build.zip" \
  -F "generate_fixes=true")

SCORE=$(echo $RESULT | jq '.compliance_score')

if (( $(echo "$SCORE < 75" | bc -l) )); then
  echo "❌ Compliance score ($SCORE) below threshold"
  echo $RESULT | jq '.recommendations[]'
  exit 1
else
  echo "✅ Compliance score: $SCORE"
  # Proceed with deployment
fi
```

### 2. Code Review Integration

Add to pull request checks:

```python
# pr-checker.py
import requests
import sys

def check_accessibility(pr_files):
    """Scan changed HTML/CSS/JS files for accessibility"""

    url = "https://aelira.ai/api/education/code/scan"
    headers = {"X-API-Key": os.getenv("AELIRA_API_KEY")}

    results = []
    for file_path in pr_files:
        if file_path.endswith(('.html', '.css', '.js')):
            with open(file_path, 'rb') as f:
                files = {"file": f}
                data = {"generate_fixes": "true"}

                response = requests.post(url, headers=headers, files=files, data=data)
                result = response.json()

                # Only flag critical/serious issues
                critical = result['summary']['critical']
                serious = result['summary']['serious']

                if critical > 0 or serious > 0:
                    results.append({
                        'file': file_path,
                        'critical': critical,
                        'serious': serious,
                        'issues': result['issues']
                    })

    if results:
        print("⚠️  Accessibility issues found in PR:")
        for r in results:
            print(f"\n{r['file']}: {r['critical']} critical, {r['serious']} serious")

            # Show AI fixes
            for issue in r['issues'][:3]:  # Top 3 issues
                if issue['ai_generated_fix']:
                    print(f"  - {issue['description']}")
                    print(f"    Fix: {issue['ai_generated_fix'][:100]}...")

        sys.exit(1)
    else:
        print("✅ No accessibility issues found")
        sys.exit(0)

# Usage
changed_files = sys.argv[1:]  # Get from git diff
check_accessibility(changed_files)
```

### 3. Learning Tool for Developers

Generate educational reports:

```python
import requests
from datetime import datetime

def generate_learning_report(code_file):
    """Create accessibility learning report"""

    url = "https://aelira.ai/api/education/code/scan"
    headers = {"X-API-Key": "your-api-key"}

    with open(code_file, 'rb') as f:
        files = {"file": f}
        data = {"generate_fixes": "true"}
        response = requests.post(url, headers=headers, files=files, data=data)

    result = response.json()

    # Generate markdown report
    report = f"""# Accessibility Report - {code_file}

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Compliance Score:** {result['compliance_score']}%

## Summary

- Critical Issues: {result['summary']['critical']}
- Serious Issues: {result['summary']['serious']}
- Moderate Issues: {result['summary']['moderate']}
- Minor Issues: {result['summary']['minor']}

## Recommendations

"""

    for rec in result['recommendations']:
        report += f"- {rec}\n"

    report += "\n## Issues & Fixes\n\n"

    for issue in result['issues']:
        report += f"### {issue['description']}\n\n"
        report += f"**Severity:** {issue['severity']} | "
        report += f"**WCAG:** {issue['wcag_criterion']} | "
        report += f"**File:** {issue['file_path']}\n\n"

        if issue['code_snippet']:
            report += f"**Current Code:**\n```html\n{issue['code_snippet']}\n```\n\n"

        if issue['ai_generated_fix']:
            report += f"**Suggested Fix:**\n```html\n{issue['ai_generated_fix']}\n```\n\n"

        report += f"**Explanation:** {issue['fix_suggestion']}\n\n"
        report += "---\n\n"

    # Save report
    report_file = f"accessibility-report-{datetime.now().strftime('%Y%m%d')}.md"
    with open(report_file, 'w') as f:
        f.write(report)

    print(f"📄 Report saved: {report_file}")
    return report_file

# Usage
generate_learning_report("index.html")
```

### 4. Batch Scanning

Scan multiple projects:

```python
import os
import requests
import pandas as pd

def batch_scan_projects(project_dirs):
    """Scan multiple projects and compare results"""

    url = "https://aelira.ai/api/education/code/scan"
    headers = {"X-API-Key": "your-api-key"}

    results = []

    for project_dir in project_dirs:
        # Create ZIP
        zip_path = f"{project_dir}.zip"
        os.system(f"zip -r {zip_path} {project_dir}")

        # Scan
        with open(zip_path, 'rb') as f:
            files = {"file": f}
            response = requests.post(url, headers=headers, files=files)

        result = response.json()

        results.append({
            'Project': os.path.basename(project_dir),
            'Score': result['compliance_score'],
            'Files': result['files_analyzed'],
            'Critical': result['summary']['critical'],
            'Serious': result['summary']['serious'],
            'Moderate': result['summary']['moderate'],
            'Minor': result['summary']['minor']
        })

        # Cleanup
        os.remove(zip_path)

    # Create comparison table
    df = pd.DataFrame(results)
    df = df.sort_values('Score', ascending=False)

    print("\n📊 Project Comparison:")
    print(df.to_string(index=False))

    # Export to CSV
    df.to_csv('accessibility-comparison.csv', index=False)
    print("\n💾 Results saved to accessibility-comparison.csv")

# Usage
projects = ['project-a', 'project-b', 'project-c']
batch_scan_projects(projects)
```

---

## Integration Examples

### VS Code Extension

```javascript
// accessibility-checker.js
const vscode = require('vscode');
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function scanCurrentFile() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;

  const document = editor.document;
  const filePath = document.fileName;

  // Check if HTML/CSS/JS
  if (!/\.(html|css|js)$/i.test(filePath)) {
    vscode.window.showWarningMessage('Only HTML, CSS, and JS files can be scanned');
    return;
  }

  // Show progress
  vscode.window.withProgress({
    location: vscode.ProgressLocation.Notification,
    title: "Scanning for accessibility issues...",
    cancellable: false
  }, async (progress) => {
    // Scan file
    const form = new FormData();
    form.append('file', fs.createReadStream(filePath));
    form.append('generate_fixes', 'true');

    const response = await axios.post(
      'https://aelira.ai/api/education/code/scan',
      form,
      {
        headers: {
          'X-API-Key': vscode.workspace.getConfiguration().get('aelira.apiKey'),
          ...form.getHeaders()
        }
      }
    );

    const result = response.data;

    // Show results
    const criticalCount = result.summary.critical;
    const seriousCount = result.summary.serious;

    if (criticalCount + seriousCount > 0) {
      vscode.window.showWarningMessage(
        `Found ${criticalCount} critical and ${seriousCount} serious issues`,
        'View Details'
      ).then(selection => {
        if (selection === 'View Details') {
          showIssuesPanel(result);
        }
      });
    } else {
      vscode.window.showInformationMessage(
        `✅ No critical issues found! Score: ${result.compliance_score}%`
      );
    }
  });
}

module.exports = { scanCurrentFile };
```

### Git Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

echo "🔍 Scanning staged files for accessibility issues..."

# Get staged HTML/CSS/JS files
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(html|css|js)$')

if [ -z "$STAGED_FILES" ]; then
  echo "✅ No HTML/CSS/JS files to scan"
  exit 0
fi

# Scan each file
ISSUES_FOUND=0

for FILE in $STAGED_FILES; do
  RESULT=$(curl -s -X POST "https://aelira.ai/api/education/code/scan" \
    -H "X-API-Key: $AELIRA_API_KEY" \
    -F "file=@$FILE" \
    -F "generate_fixes=false")

  CRITICAL=$(echo $RESULT | jq '.summary.critical')

  if [ "$CRITICAL" -gt 0 ]; then
    echo "❌ $FILE: $CRITICAL critical issues"
    ISSUES_FOUND=1
  fi
done

if [ $ISSUES_FOUND -eq 1 ]; then
  echo ""
  echo "⚠️  Critical accessibility issues found!"
  echo "Fix issues or use 'git commit --no-verify' to skip check"
  exit 1
fi

echo "✅ All files passed accessibility check"
exit 0
```

---

## Rate Limits

Same as Web Scanner API:

- **Free tier:** 10 scans per hour
- **Professional tier:** 100 scans per hour
- **Enterprise tier:** Unlimited scans

---

## Related Endpoints

- `POST /api/education/web/scan` - Scan live websites
- `GET /api/education/scans` - List all scans
- `GET /api/education/scans/{scan_id}` - Get scan details

---

## Support

- **Documentation:** https://docs.aelira.ai
- **API Status:** https://status.aelira.ai
- **Support Email:** support@aelira.ai

---

**Last Updated:** October 30, 2025
**Version:** v0.13.0
