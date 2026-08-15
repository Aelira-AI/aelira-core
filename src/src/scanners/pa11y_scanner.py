"""
Pa11y accessibility scanner wrapper.

Calls Pa11y CLI via subprocess and parses JSON output for multi-engine
accessibility testing (axe-core + HTML_CodeSniffer).
"""

import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Pa11yIssue:
    """Single Pa11y accessibility issue"""

    code: str
    type: str  # error, warning, notice
    selector: str
    context: str
    message: str
    type_code: int
    runner: str  # axe, htmlcs


@dataclass
class Pa11yResult:
    """Pa11y scan result"""

    url: str
    total_issues: int
    issues_by_severity: Dict[str, int]
    issues: List[Pa11yIssue]
    engine: str = "pa11y"
    runner: str = "axe"  # Which Pa11y runner was used
    scan_duration_ms: Optional[int] = None
    page_title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "url": self.url,
            "total_issues": self.total_issues,
            "issues_by_severity": self.issues_by_severity,
            "issues": [
                {
                    "code": issue.code,
                    "type": issue.type,
                    "selector": issue.selector,
                    "context": issue.context,
                    "message": issue.message,
                    "type_code": issue.type_code,
                    "runner": issue.runner,
                }
                for issue in self.issues
            ],
            "engine": self.engine,
            "runner": self.runner,
            "scan_duration_ms": self.scan_duration_ms,
            "page_title": self.page_title,
        }


class Pa11yScanner:
    """
    Wrapper for Pa11y CLI accessibility testing.

    Pa11y can run multiple accessibility test runners:
    - axe: Deque's axe-core (WCAG 2.2 AA)
    - htmlcs: Squiz HTML_CodeSniffer (WCAG 2.1 AAA)

    Runs Pa11y as subprocess, parses JSON output.
    """

    def __init__(self, timeout: int = 60, pa11y_bin: str = "pa11y"):
        """
        Initialize Pa11y scanner.

        Args:
            timeout: Maximum scan time in seconds
            pa11y_bin: Path to pa11y binary (default: "pa11y" in PATH)
        """
        self.timeout = timeout
        self.pa11y_bin = pa11y_bin

    async def scan(
        self, url: str, runner: str = "axe", standard: str = "WCAG2AA"
    ) -> Pa11yResult:
        """
        Scan URL with Pa11y.

        Args:
            url: URL to scan
            runner: Pa11y runner ('axe' or 'htmlcs')
            standard: Accessibility standard (WCAG2A, WCAG2AA, WCAG2AAA)

        Returns:
            Pa11yResult with issues found

        Raises:
            Exception: If Pa11y scan fails or times out
        """
        import time

        start_time = time.time()

        cmd = [
            self.pa11y_bin,
            "--reporter",
            "json",
            "--runner",
            runner,
            "--standard",
            standard,
            "--timeout",
            str(self.timeout * 1000),  # Pa11y uses milliseconds
            url,
        ]

        logger.info(
            f"Starting Pa11y scan: {url} (runner={runner}, standard={standard})"
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Pa11y returns:
            # - 0: No errors found
            # - 2: Errors found (this is normal!)
            # - Other: Actual failure
            if process.returncode not in (0, 2):
                error_msg = stderr.decode().strip()
                logger.error(
                    f"Pa11y scan failed (exit code {process.returncode}): {error_msg}"
                )
                raise Exception(f"Pa11y scan failed: {error_msg}")

            # Parse JSON output
            output = stdout.decode().strip()
            if not output:
                logger.warning(f"Pa11y returned empty output for {url}")
                return Pa11yResult(
                    url=url,
                    total_issues=0,
                    issues_by_severity={"error": 0, "warning": 0, "notice": 0},
                    issues=[],
                    runner=runner,
                    scan_duration_ms=duration_ms,
                )

            raw_results = json.loads(output)

            # Convert to Pa11yIssue objects
            issues = []
            severity_counts = {"error": 0, "warning": 0, "notice": 0}

            for raw_issue in raw_results:
                issue_type = raw_issue.get("type", "error").lower()

                issue = Pa11yIssue(
                    code=raw_issue.get("code", ""),
                    type=issue_type,
                    selector=raw_issue.get("selector", ""),
                    context=raw_issue.get("context", ""),
                    message=raw_issue.get("message", ""),
                    type_code=raw_issue.get("typeCode", 1),
                    runner=raw_issue.get("runner", runner),
                )
                issues.append(issue)

                # Count by severity
                if issue_type in severity_counts:
                    severity_counts[issue_type] += 1

            logger.info(
                f"Pa11y scan complete: {len(issues)} issues found "
                f"(errors={severity_counts['error']}, "
                f"warnings={severity_counts['warning']}, "
                f"notices={severity_counts['notice']}) "
                f"in {duration_ms}ms"
            )

            return Pa11yResult(
                url=url,
                total_issues=len(issues),
                issues_by_severity=severity_counts,
                issues=issues,
                engine="pa11y",
                runner=runner,
                scan_duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            logger.error(f"Pa11y scan timed out after {self.timeout}s for {url}")
            raise Exception(f"Pa11y scan timed out after {self.timeout}s")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Pa11y JSON output: {e}")
            logger.error(f"Raw output: {output}")
            raise Exception(f"Failed to parse Pa11y output: {e}")
        except Exception as e:
            logger.error(f"Pa11y scan error for {url}: {e}")
            raise

    async def verify_installation(self) -> bool:
        """
        Verify Pa11y is installed and accessible.

        Returns:
            True if Pa11y is installed, False otherwise
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self.pa11y_bin,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)

            if process.returncode == 0:
                version = stdout.decode().strip()
                logger.info(f"Pa11y is installed: {version}")
                return True
            else:
                logger.warning(
                    f"Pa11y verification failed (exit code {process.returncode})"
                )
                return False

        except FileNotFoundError:
            logger.error(f"Pa11y binary not found at: {self.pa11y_bin}")
            return False
        except Exception as e:
            logger.error(f"Pa11y verification error: {e}")
            return False
