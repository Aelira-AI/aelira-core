# Web Scanner API Documentation

**Endpoint:** `POST /api/education/web/scan`

**Version:** v0.13.0

**Description:** Scan live websites for WCAG 2.2 Level AA accessibility compliance using Playwright browser automation and axe-core testing.

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
POST https://aelira.ai/api/education/web/scan
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | ✅ Yes | - | Website URL to scan (must include protocol: http:// or https://) |
| `scan_images` | boolean | No | false | Enable AI image analysis with alt text generation |
| `scan_multimedia` | boolean | No | false | Check multimedia elements for captions |
| `scan_math` | boolean | No | false | Detect and analyze LaTeX/MathML content |
| `max_depth` | integer | No | 1 | Crawl depth (1-3 levels) |
| `max_pages` | integer | No | 10 | Maximum pages to scan (5, 10, 20, or 50) |
| `generate_code_fixes` | boolean | No | true | Generate AI-powered code fixes using Qwen Coder |

### Examples

#### Basic Scan (Single Page)

```bash
curl -X POST "https://aelira.ai/api/education/web/scan?url=https://example.com" \
  -H "X-API-Key: your-api-key"
```

#### Comprehensive Scan (Multi-page with All Features)

```bash
curl -X POST "https://aelira.ai/api/education/web/scan?url=https://university.edu&scan_images=true&scan_multimedia=true&scan_math=true&max_depth=2&max_pages=20&generate_code_fixes=true" \
  -H "X-API-Key: your-api-key"
```

#### Python Example

```python
import requests

url = "https://aelira.ai/api/education/web/scan"
headers = {"X-API-Key": "your-api-key"}
params = {
    "url": "https://university.edu",
    "scan_images": True,
    "scan_multimedia": True,
    "scan_math": True,
    "max_depth": 2,
    "max_pages": 20,
    "generate_code_fixes": True
}

response = requests.post(url, headers=headers, params=params)
result = response.json()

print(f"Scan ID: {result['scan_id']}")
print(f"Compliance Score: {result['overall_compliance_score']}%")
print(f"Pages Scanned: {result['pages_scanned']}")
print(f"Issues Found: {sum(result['summary'].values())}")
```

#### JavaScript Example

```javascript
const scanWebsite = async (targetUrl) => {
  const apiUrl = 'https://aelira.ai/api/education/web/scan';
  const params = new URLSearchParams({
    url: targetUrl,
    scan_images: 'true',
    scan_multimedia: 'true',
    scan_math: 'true',
    max_depth: '2',
    max_pages: '20',
    generate_code_fixes: 'true'
  });

  const response = await fetch(`${apiUrl}?${params}`, {
    method: 'POST',
    headers: {
      'X-API-Key': 'your-api-key'
    }
  });

  const result = await response.json();

  console.log(`Scan ID: ${result.scan_id}`);
  console.log(`Compliance Score: ${result.overall_compliance_score}%`);
  console.log(`Pages Scanned: ${result.pages_scanned}`);

  return result;
};

// Usage
scanWebsite('https://university.edu');
```

---

## Response

### Success Response (200 OK)

```json
{
  "success": true,
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "root_url": "https://university.edu",
  "pages_scanned": 18,
  "total_scan_time": 12.4,
  "overall_compliance_score": 72.5,
  "summary": {
    "critical": 8,
    "serious": 15,
    "moderate": 32,
    "minor": 12
  },
  "pages": [
    {
      "url": "https://university.edu/",
      "title": "University Home Page",
      "scan_time": 1.2,
      "compliance_score": 75.0,
      "page_structure": {
        "has_h1": true,
        "h1_count": 1,
        "has_main": true,
        "has_nav": true,
        "has_footer": true,
        "landmark_count": 5
      },
      "issues": [
        {
          "id": "image-alt",
          "impact": "critical",
          "criterion": "1.1.1",
          "description": "Images must have alternate text",
          "help": "Ensures <img> elements have alternate text or a role of none or presentation",
          "help_url": "https://dequeuniversity.com/rules/axe/4.4/image-alt",
          "element": "<img src=\"/images/hero.jpg\" class=\"hero-image\">",
          "fix": "Add alt attribute to describe the image content",
          "generated_code_fix": "<img src=\"/images/hero.jpg\" class=\"hero-image\" alt=\"Students studying together in modern library\">"
        },
        {
          "id": "label",
          "impact": "serious",
          "criterion": "3.3.2",
          "description": "Form elements must have labels",
          "help": "Ensures every form element has a label",
          "help_url": "https://dequeuniversity.com/rules/axe/4.4/label",
          "element": "<input type=\"email\" name=\"email\" placeholder=\"Email\">",
          "fix": "Add a <label> element or aria-label attribute",
          "generated_code_fix": "<label for=\"email-input\">Email Address</label>\n<input type=\"email\" id=\"email-input\" name=\"email\" placeholder=\"Email\">"
        },
        {
          "id": "color-contrast",
          "impact": "serious",
          "criterion": "1.4.3",
          "description": "Elements must have sufficient color contrast",
          "help": "Ensures the contrast between foreground and background colors meets WCAG 2 AA contrast ratio thresholds",
          "help_url": "https://dequeuniversity.com/rules/axe/4.4/color-contrast",
          "element": "<p style=\"color: #777; background: #fff;\">Footer text</p>",
          "fix": "Increase contrast ratio to at least 4.5:1",
          "generated_code_fix": "<p style=\"color: #555; background: #fff;\">Footer text</p>"
        }
      ],
      "images": [
        {
          "src": "/images/hero.jpg",
          "alt": null,
          "has_alt": false,
          "ai_generated_alt": "Students studying together in modern library",
          "long_description": "A group of diverse students collaborating around a table in a bright, modern university library with floor-to-ceiling windows and contemporary furniture",
          "image_type": "Photo",
          "educational_value": "Essential"
        }
      ],
      "multimedia": [
        {
          "type": "video",
          "src": "/videos/campus-tour.mp4",
          "has_captions": false,
          "has_transcript": false,
          "wcag_issues": [
            "Missing captions (WCAG 1.2.2)",
            "Missing transcript (WCAG 1.2.8)"
          ]
        }
      ],
      "math_content": [
        {
          "format": "latex",
          "content": "E = mc^2",
          "has_alt_text": false,
          "suggested_alt_text": "Einstein's mass-energy equivalence equation: Energy equals mass times the speed of light squared",
          "accessible_mathml": "<math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math>"
        }
      ],
      "content_analysis": {
        "readability_score": 72,
        "reading_level": "College",
        "clarity_score": 8,
        "suggestions": [
          "Consider simplifying complex sentences in the introduction",
          "Add more headings to break up long sections"
        ]
      }
    }
  ],
  "download_url": "/api/education/scans/550e8400-e29b-41d4-a716-446655440000/report"
}
```

### Response Fields

#### Root Level

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the scan completed successfully |
| `scan_id` | string (UUID) | Unique identifier for this scan (use to retrieve results later) |
| `root_url` | string | The starting URL that was scanned |
| `pages_scanned` | integer | Total number of pages analyzed |
| `total_scan_time` | float | Total time in seconds for the entire scan |
| `overall_compliance_score` | float | Weighted compliance score across all pages (0-100) |
| `summary` | object | Aggregate issue counts by severity |
| `pages` | array | Detailed results for each scanned page |
| `download_url` | string | Endpoint to download full report |

#### Summary Object

| Field | Type | Description |
|-------|------|-------------|
| `critical` | integer | Count of critical issues (WCAG Level A failures) |
| `serious` | integer | Count of serious issues (WCAG Level AA failures) |
| `moderate` | integer | Count of moderate issues (best practice violations) |
| `minor` | integer | Count of minor issues (minor improvements) |

#### Page Object

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | URL of the scanned page |
| `title` | string | Page title from `<title>` element |
| `scan_time` | float | Time in seconds to scan this page |
| `compliance_score` | float | Compliance score for this page (0-100) |
| `page_structure` | object | Semantic HTML structure analysis |
| `issues` | array | Accessibility issues found on this page |
| `images` | array | Image analysis results (if `scan_images=true`) |
| `multimedia` | array | Multimedia analysis results (if `scan_multimedia=true`) |
| `math_content` | array | Math content analysis (if `scan_math=true`) |
| `content_analysis` | object | AI content quality analysis |

#### Issue Object

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | axe-core rule ID |
| `impact` | string | Severity: critical, serious, moderate, minor |
| `criterion` | string | WCAG criterion number (e.g., "1.1.1") |
| `description` | string | Human-readable description of the issue |
| `help` | string | Detailed explanation |
| `help_url` | string | Link to axe-core documentation |
| `element` | string | HTML element with the issue |
| `fix` | string | Manual fix suggestion |
| `generated_code_fix` | string | AI-generated code fix (if `generate_code_fixes=true`) |

---

## Error Responses

### 400 Bad Request - Invalid URL

```json
{
  "detail": "Invalid URL format. URL must include protocol (http:// or https://)"
}
```

### 400 Bad Request - Invalid Parameters

```json
{
  "detail": "max_depth must be between 1 and 3"
}
```

### 401 Unauthorized - Missing API Key

```json
{
  "detail": "X-API-Key header required"
}
```

### 403 Forbidden - Invalid API Key

```json
{
  "detail": "Invalid API key"
}
```

### 429 Too Many Requests

```json
{
  "detail": "Rate limit exceeded. Maximum 10 scans per minute."
}
```

### 500 Internal Server Error - Scan Failed

```json
{
  "detail": "Failed to scan website: Connection timeout"
}
```

---

## Rate Limits

- **Free tier:** 10 scans per hour, 100 scans per month
- **Professional tier:** 100 scans per hour, 5,000 scans per month
- **Enterprise tier:** Unlimited scans

Rate limit headers included in response:
```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1698765432
```

---

## Best Practices

### 1. Start Small

Begin with single-page scans to understand the data structure:

```bash
curl -X POST "https://aelira.ai/api/education/web/scan?url=https://example.com&max_pages=1" \
  -H "X-API-Key: your-api-key"
```

### 2. Incremental Scanning

For large sites, scan incrementally:

```python
# Week 1: Homepage + main sections
scan_pages(url="https://university.edu", max_depth=1, max_pages=10)

# Week 2: Course catalog
scan_pages(url="https://university.edu/courses", max_depth=2, max_pages=20)

# Week 3: Student resources
scan_pages(url="https://university.edu/students", max_depth=2, max_pages=20)
```

### 3. Schedule Regular Scans

Set up cron jobs or scheduled tasks for ongoing monitoring:

```bash
# Crontab entry - scan every Monday at 2am
0 2 * * 1 /usr/bin/python /path/to/scan_script.py
```

### 4. Focus on Critical Issues First

Filter results by severity:

```python
critical_issues = [
    issue for page in result['pages']
    for issue in page['issues']
    if issue['impact'] == 'critical'
]

print(f"Found {len(critical_issues)} critical issues to fix immediately")
```

### 5. Use AI Features Strategically

Enable `scan_images` and `scan_multimedia` only when needed (they increase scan time):

- **Development:** Use all features to catch everything
- **CI/CD:** Use basic scan for speed
- **Weekly audits:** Use comprehensive scan with all features

---

## Integration Examples

### CI/CD Pipeline (GitHub Actions)

```yaml
name: Accessibility Scan

on:
  pull_request:
    branches: [main]

jobs:
  accessibility:
    runs-on: ubuntu-latest
    steps:
      - name: Scan staging site
        run: |
          curl -X POST "https://aelira.ai/api/education/web/scan?url=${{ secrets.STAGING_URL }}&max_pages=10" \
            -H "X-API-Key: ${{ secrets.AELIRA_API_KEY }}" \
            -o scan-results.json

      - name: Check compliance score
        run: |
          SCORE=$(jq '.overall_compliance_score' scan-results.json)
          if (( $(echo "$SCORE < 80" | bc -l) )); then
            echo "Compliance score ($SCORE) below threshold (80)"
            exit 1
          fi
```

### Slack Notifications

```python
import requests

# Scan website
scan_result = requests.post(
    "https://aelira.ai/api/education/web/scan",
    headers={"X-API-Key": "your-api-key"},
    params={"url": "https://university.edu", "max_pages": 10}
).json()

# Send to Slack
critical_count = scan_result['summary']['critical']
score = scan_result['overall_compliance_score']

slack_webhook = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
slack_message = {
    "text": f"🔍 Accessibility Scan Complete",
    "blocks": [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Compliance Score:* {score}%\n*Critical Issues:* {critical_count}"
            }
        }
    ]
}

requests.post(slack_webhook, json=slack_message)
```

### Automated Reporting

```python
import csv
from datetime import datetime

# Scan website
result = requests.post(
    "https://aelira.ai/api/education/web/scan",
    headers={"X-API-Key": "your-api-key"},
    params={"url": "https://university.edu", "max_depth": 2, "max_pages": 20}
).json()

# Generate CSV report
with open(f'scan-report-{datetime.now().date()}.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['URL', 'Issue', 'Severity', 'WCAG', 'Fix'])

    for page in result['pages']:
        for issue in page['issues']:
            writer.writerow([
                page['url'],
                issue['description'],
                issue['impact'],
                issue['criterion'],
                issue['generated_code_fix'] or issue['fix']
            ])

print(f"Report saved with {result['pages_scanned']} pages analyzed")
```

---

## Related Endpoints

- `GET /api/education/scans` - List all scans
- `GET /api/education/scans/{scan_id}` - Get specific scan results
- `GET /api/education/scans/{scan_id}/report` - Download PDF report
- `POST /api/education/code/scan` - Scan uploaded code files

---

## Support

- **Documentation:** https://docs.aelira.ai
- **API Status:** https://status.aelira.ai
- **Support Email:** support@aelira.ai
- **Slack Community:** https://aelira.slack.com

---

**Last Updated:** October 30, 2025
**Version:** v0.13.0
