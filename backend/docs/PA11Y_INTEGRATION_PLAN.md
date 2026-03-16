# Pa11y Integration Plan

**Status:** Planning
**Target:** v0.21.0 (Post Phase 2 Complete)
**Estimated Effort:** 2-3 days

---

## Overview

Add Pa11y as a complementary accessibility testing engine alongside axe-core, providing multi-engine coverage for higher confidence in WCAG compliance detection.

## Business Value

**For Universities (April 2026 Deadline):**
- **Risk Reduction:** Multiple engines = fewer false negatives = lower ADA lawsuit risk
- **Competitive Advantage:** No competitor (YuJa, Blackboard Ally) runs multiple engines
- **Marketing Claim:** "Most comprehensive open source accessibility platform"

**Scan Tiers:**
1. **Quick Scan:** axe-core only (fast, ~90% coverage)
2. **Comprehensive Scan:** axe-core + Pa11y (slower, ~95%+ coverage, catches edge cases)
3. **Deep Scan:** Both engines + AI vision analysis + manual review suggestions

---

## Technical Implementation

### 1. Dockerfile Changes

**Add Node.js and Pa11y to backend container:**

```dockerfile
# Stage 2: Runtime (add after line 28)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    ffmpeg \
    libpq5 \
    curl \
    # Node.js for Pa11y
    nodejs \
    npm \
    # ... rest of dependencies
```

**Install Pa11y globally:**

```dockerfile
# After Playwright installation (around line 91)
RUN npm install -g pa11y pa11y-reporter-json
```

**Estimated Image Size Impact:** +150MB (Node.js + Pa11y dependencies)

---

### 2. Backend Code Changes

#### A. Create Pa11y Scanner Wrapper

**File:** `backend/src/scanners/pa11y_scanner.py`

```python
"""
Pa11y accessibility scanner wrapper.

Calls Pa11y CLI via subprocess and parses JSON output.
"""

import asyncio
import json
import logging
from typing import Dict, List, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Pa11yResult:
    """Pa11y scan result"""
    url: str
    total_issues: int
    issues_by_severity: Dict[str, int]
    issues: List[Dict[str, Any]]
    engine: str = "pa11y"

class Pa11yScanner:
    """
    Wrapper for Pa11y CLI accessibility testing.

    Runs Pa11y as subprocess, parses JSON output.
    """

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    async def scan(self, url: str, runner: str = "axe") -> Pa11yResult:
        """
        Scan URL with Pa11y.

        Args:
            url: URL to scan
            runner: Pa11y runner ('axe', 'htmlcs', or both)

        Returns:
            Pa11yResult with issues found
        """
        cmd = [
            "pa11y",
            "--reporter", "json",
            "--runner", runner,
            "--timeout", str(self.timeout * 1000),  # Pa11y uses ms
            url
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout
            )

            if process.returncode not in (0, 2):  # 2 = issues found
                logger.error(f"Pa11y failed: {stderr.decode()}")
                raise Exception(f"Pa11y scan failed: {stderr.decode()}")

            # Parse JSON output
            results = json.loads(stdout.decode())

            # Count by severity
            severity_counts = {"error": 0, "warning": 0, "notice": 0}
            for issue in results:
                severity = issue.get("type", "error").lower()
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

            return Pa11yResult(
                url=url,
                total_issues=len(results),
                issues_by_severity=severity_counts,
                issues=results,
                engine=f"pa11y-{runner}"
            )

        except asyncio.TimeoutError:
            logger.error(f"Pa11y scan timed out after {self.timeout}s")
            raise Exception(f"Scan timed out after {self.timeout}s")
        except Exception as e:
            logger.error(f"Pa11y scan error: {e}")
            raise
```

#### B. Update Scan Endpoint

**File:** `backend/src/api/scan.py`

Add scan mode parameter:

```python
from enum import Enum

class ScanMode(str, Enum):
    QUICK = "quick"          # axe-core only
    COMPREHENSIVE = "comprehensive"  # axe-core + pa11y
    DEEP = "deep"            # both + AI vision

@router.post("/scan/website")
async def scan_website(
    url: str,
    mode: ScanMode = ScanMode.QUICK,
    use_ai_analysis: bool = False
):
    """
    Scan website for accessibility issues.

    Args:
        url: URL to scan
        mode: Scan mode (quick/comprehensive/deep)
        use_ai_analysis: Enable AI-powered fix generation
    """
    results = {}

    # Always run axe-core
    axe_scanner = AxeCoreScanner()
    results["axe"] = await axe_scanner.scan(url)

    # Run Pa11y for comprehensive/deep scans
    if mode in (ScanMode.COMPREHENSIVE, ScanMode.DEEP):
        pa11y_scanner = Pa11yScanner()
        results["pa11y"] = await pa11y_scanner.scan(url, runner="axe")

    # Merge results
    total_issues = results["axe"].total_issues
    if "pa11y" in results:
        # Deduplicate issues by selector+code
        total_issues = merge_and_deduplicate(results)

    return {
        "url": url,
        "mode": mode,
        "engines_used": list(results.keys()),
        "total_issues": total_issues,
        "results": results
    }
```

#### C. Result Merging & Deduplication

**File:** `backend/src/scanners/merge_results.py`

```python
"""
Merge and deduplicate results from multiple accessibility engines.
"""

def merge_and_deduplicate(results: Dict[str, Any]) -> int:
    """
    Merge results from multiple engines, removing duplicates.

    Deduplication strategy:
    - Same selector + same WCAG criterion = duplicate
    - Keep the most detailed issue (longest message)
    """
    seen = set()
    unique_issues = []

    for engine, result in results.items():
        for issue in result.issues:
            key = f"{issue.get('selector')}:{issue.get('code')}"
            if key not in seen:
                seen.add(key)
                unique_issues.append({**issue, "detected_by": engine})

    return len(unique_issues)
```

---

### 3. Dashboard Changes

#### A. Add Scan Mode Selector

**File:** `backend/dashboard/src/components/ScanForm.tsx`

```tsx
<select name="mode" className="...">
  <option value="quick">Quick Scan (axe-core only)</option>
  <option value="comprehensive">Comprehensive Scan (axe-core + Pa11y)</option>
  <option value="deep">Deep Scan (all engines + AI vision)</option>
</select>
```

#### B. Display Multi-Engine Results

**File:** `backend/dashboard/src/components/ScanResults.tsx`

```tsx
{scan.engines_used.map(engine => (
  <div key={engine} className="engine-results">
    <h3>{engine.toUpperCase()} Results</h3>
    <IssueList issues={scan.results[engine].issues} />
  </div>
))}
```

---

### 4. CLI Changes

**File:** `cli/src/commands/scan.ts`

```typescript
export default class Scan extends Command {
  static flags = {
    url: Flags.string({required: true}),
    engine: Flags.string({
      options: ['axe', 'pa11y', 'all'],
      default: 'axe',
      description: 'Accessibility engine to use'
    }),
    mode: Flags.string({
      options: ['quick', 'comprehensive', 'deep'],
      default: 'quick'
    })
  }
}
```

---

## Testing Strategy

### Unit Tests

```python
# tests/scanners/test_pa11y_scanner.py

async def test_pa11y_scan():
    scanner = Pa11yScanner()
    result = await scanner.scan("https://example.com")
    assert result.total_issues >= 0
    assert result.engine == "pa11y-axe"

async def test_pa11y_timeout():
    scanner = Pa11yScanner(timeout=1)
    with pytest.raises(Exception, match="timed out"):
        await scanner.scan("https://slow-site.com")
```

### Integration Tests

```python
# tests/api/test_scan_modes.py

async def test_quick_scan_uses_axe_only(client):
    response = await client.post("/scan/website", json={
        "url": "https://example.com",
        "mode": "quick"
    })
    assert "axe" in response.json()["engines_used"]
    assert "pa11y" not in response.json()["engines_used"]

async def test_comprehensive_scan_uses_both(client):
    response = await client.post("/scan/website", json={
        "url": "https://example.com",
        "mode": "comprehensive"
    })
    assert "axe" in response.json()["engines_used"]
    assert "pa11y" in response.json()["engines_used"]
```

---

## Performance Considerations

**Scan Times (estimated):**
- Quick (axe-core only): 5-10 seconds
- Comprehensive (axe + Pa11y): 15-25 seconds (run in parallel)
- Deep (both + AI vision): 30-60 seconds

**Optimization:**
- Run axe-core and Pa11y in parallel using `asyncio.gather()`
- Cache Pa11y binary location
- Use connection pooling for Playwright

---

## Documentation Updates

1. **README.md** — ✅ Already updated
2. **docs/guides/web-scanning.md** — Add scan mode comparison table
3. **docs/api/README.md** — Document `mode` parameter
4. **docs/getting-started/configuration.md** — Add Pa11y config options

---

## Rollout Plan

### Phase 1: Backend Integration (Day 1)
- [ ] Update Dockerfile (add Node.js + Pa11y)
- [ ] Create `Pa11yScanner` class
- [ ] Add `ScanMode` enum to API
- [ ] Implement result merging

### Phase 2: Frontend Integration (Day 2)
- [ ] Add scan mode selector to dashboard
- [ ] Update results display for multi-engine
- [ ] Add CLI `--engine` and `--mode` flags

### Phase 3: Testing & Documentation (Day 3)
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Update user documentation
- [ ] Performance testing

### Phase 4: PR & Review
- [ ] Create PR with all changes
- [ ] Code review
- [ ] Merge to main

---

## Success Metrics

**Objective:** Increase issue detection accuracy without significant performance degradation

**Metrics:**
- Unique issues found with Pa11y that axe-core missed: Target >5%
- Scan time increase for comprehensive mode: Target <3x quick mode
- User adoption of comprehensive mode: Target >30% of scans

---

## Future Enhancements

1. **Pa11y-CI Integration** — Batch scanning of multiple URLs
2. **Custom Pa11y Runners** — Add custom accessibility rules
3. **Historical Comparison** — Track which engine finds what over time
4. **Engine Benchmarking** — A/B test engine accuracy with known test cases

---

## License Compliance

**Pa11y License:** LGPL-3.0
**Compatibility:** ✅ Compatible with our MIT (CLI) + AGPL (backend) dual license
**Attribution:** Added to README.md tech stack

**LGPL-3.0 Requirements:**
- ✅ Can use as dependency (no code modification needed)
- ✅ Can integrate into AGPL backend
- ✅ Must provide link to Pa11y source (already in README)
- ✅ No license change required for our code

---

**Author:** Claude Code
**Last Updated:** November 9, 2025
**Status:** Ready for implementation
