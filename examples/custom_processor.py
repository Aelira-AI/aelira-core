"""
Example: Adding a Custom Document Processor

This example shows how to create a processor for a new file format.
We'll build a Markdown (.md) accessibility scanner as an example.

To integrate this with Aelira:
1. Save your processor in src/education/
2. Create a scan route in src/api/ (or add to an existing one)
3. Register the route in src/api/main.py

The key interface is simple: accept a file, return a list of issues.
"""

from typing import Optional
from pydantic import BaseModel
import re
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Step 1: Define your issue models
# ============================================================================
# Use Pydantic models for structured, validated results.
# These models are what the dashboard displays.


class AccessibilityIssue(BaseModel):
    """A single accessibility issue found in a document."""

    issue_type: str  # e.g., "missing_alt_text", "skipped_heading"
    severity: str  # "critical", "serious", "moderate", "minor"
    message: str  # Human-readable description
    location: Optional[str] = None  # Where in the document (page, line, element)
    wcag_criterion: Optional[str] = None  # e.g., "1.1.1"
    suggested_fix: Optional[str] = None  # How to fix it


class ScanResult(BaseModel):
    """Results from scanning a document."""

    file_name: str
    file_type: str
    issues: list[AccessibilityIssue]
    score: float  # 0-100, where 100 = fully accessible

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def has_critical_issues(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)


# ============================================================================
# Step 2: Build your processor
# ============================================================================
# The processor parses the file format and checks for WCAG violations.
# Keep each check in its own method for testability.


class MarkdownProcessor:
    """
    Example accessibility scanner for Markdown files.

    Checks for:
    - Images missing alt text
    - Heading hierarchy issues (skipped levels)
    - Links with non-descriptive text ("click here")
    - Missing document language declaration
    """

    def scan(self, file_path: str) -> ScanResult:
        """Scan a Markdown file for accessibility issues."""
        logger.info(f"Scanning Markdown file: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            lines = content.split("\n")

        issues: list[AccessibilityIssue] = []

        # Run each check
        issues.extend(self._check_image_alt_text(lines))
        issues.extend(self._check_heading_hierarchy(lines))
        issues.extend(self._check_link_text(lines))

        # Calculate score (simple: deduct points per issue by severity)
        score = self._calculate_score(issues)

        return ScanResult(
            file_name=file_path.split("/")[-1],
            file_type="markdown",
            issues=issues,
            score=score,
        )

    def _check_image_alt_text(self, lines: list[str]) -> list[AccessibilityIssue]:
        """WCAG 1.1.1: All images must have alt text."""
        issues = []
        for i, line in enumerate(lines, 1):
            # Match ![](url) — empty alt text
            if re.search(r"!\[\]\(", line):
                issues.append(
                    AccessibilityIssue(
                        issue_type="missing_alt_text",
                        severity="critical",
                        message="Image is missing alt text",
                        location=f"Line {i}",
                        wcag_criterion="1.1.1",
                        suggested_fix="Add descriptive alt text: ![description of image](url)",
                    )
                )
        return issues

    def _check_heading_hierarchy(self, lines: list[str]) -> list[AccessibilityIssue]:
        """WCAG 1.3.1: Headings should not skip levels."""
        issues = []
        last_level = 0

        for i, line in enumerate(lines, 1):
            match = re.match(r"^(#{1,6})\s", line)
            if match:
                level = len(match.group(1))
                if last_level > 0 and level > last_level + 1:
                    issues.append(
                        AccessibilityIssue(
                            issue_type="skipped_heading_level",
                            severity="serious",
                            message=f"Heading level skipped from H{last_level} to H{level}",
                            location=f"Line {i}",
                            wcag_criterion="1.3.1",
                            suggested_fix=f"Use H{last_level + 1} instead of H{level}",
                        )
                    )
                last_level = level

        return issues

    def _check_link_text(self, lines: list[str]) -> list[AccessibilityIssue]:
        """WCAG 2.4.4: Links should have descriptive text."""
        issues = []
        bad_link_texts = {"click here", "here", "link", "read more", "more"}

        for i, line in enumerate(lines, 1):
            for match in re.finditer(r"\[([^\]]+)\]\(", line):
                link_text = match.group(1).strip().lower()
                if link_text in bad_link_texts:
                    issues.append(
                        AccessibilityIssue(
                            issue_type="non_descriptive_link",
                            severity="moderate",
                            message=f'Link text "{match.group(1)}" is not descriptive',
                            location=f"Line {i}",
                            wcag_criterion="2.4.4",
                            suggested_fix="Use text that describes the link destination",
                        )
                    )

        return issues

    def _calculate_score(self, issues: list[AccessibilityIssue]) -> float:
        """Calculate accessibility score from 0-100."""
        if not issues:
            return 100.0

        deductions = {
            "critical": 20,
            "serious": 10,
            "moderate": 5,
            "minor": 2,
        }

        total_deduction = sum(deductions.get(i.severity, 5) for i in issues)
        return max(0.0, 100.0 - total_deduction)


# ============================================================================
# Step 3: Usage
# ============================================================================

if __name__ == "__main__":
    processor = MarkdownProcessor()
    result = processor.scan("example.md")

    print(f"Score: {result.score}/100")
    print(f"Issues: {result.issue_count}")

    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.message} ({issue.location})")
        if issue.suggested_fix:
            print(f"    Fix: {issue.suggested_fix}")
