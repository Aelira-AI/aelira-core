"""
Example: Adding a Custom Document Processor

This example shows how to create a processor for a new file format.
We'll build a Markdown (.md) accessibility scanner as an example.

It mirrors the real processors in src/education/ (docx_processor.py,
pdf_processor.py, pptx_processor.py, xlsx_processor.py):
- A `process_<format>(file_path) -> <Format>ProcessingResult` entrypoint
- A Pydantic result model with file_path/file_name/issues/compliance_score
- Issue severity/category matching the shared vocabulary in
  src.education.remediation.base (IssueSeverity/IssueCategory/
  RemediationIssue), so a remediator can consume the output without a
  translation layer

To integrate this with Aelira:
1. Save your processor in src/education/
2. Create a scan route in src/api/ (or add to an existing one)
3. Register the route in src/api/main.py

Run this file directly to see it work end-to-end against a generated
sample document:

    python examples/custom_processor.py
"""

from typing import Dict, List, Optional
from pydantic import BaseModel
from enum import Enum
import re
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


# ============================================================================
# Step 1: Define your issue models
# ============================================================================
# Use Pydantic models for structured, validated results. The enums and
# field names below mirror src.education.remediation.base.IssueSeverity /
# IssueCategory / RemediationIssue — the shared vocabulary every remediator
# normalizes scan output into — so a Markdown remediator could consume
# these issues with no translation layer. They're redeclared here rather
# than imported so this example stays runnable with only `pydantic`
# installed: importing src.education.remediation pulls in every format's
# remediator (python-docx, python-pptx, pikepdf, ...) as a side effect of
# its package __init__.


class IssueSeverity(str, Enum):
    """Mirrors src.education.remediation.base.IssueSeverity."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueCategory(str, Enum):
    """Subset of src.education.remediation.base.IssueCategory used here."""

    ALT_TEXT = "alt_text"
    HEADING = "heading"
    LINK = "link"


class AccessibilityIssue(BaseModel):
    """A single accessibility issue found in a document."""

    category: IssueCategory
    severity: IssueSeverity
    description: str  # Human-readable description
    location: Optional[str] = None  # Where in the document (page, line, element)
    wcag_criteria: Optional[str] = None  # e.g., "1.1.1"
    fix_suggestion: Optional[str] = None  # How to fix it


class MarkdownProcessingResult(BaseModel):
    """Result of Markdown processing operation.

    Shaped like the real *ProcessingResult classes (see
    src/education/pdf_checks/models.py:PDFProcessingResult and
    src/education/docx_processor.py:DocxProcessingResult) so a Markdown
    processor plugs into the same dashboard/API contract as the built-in
    formats.
    """

    file_path: str
    file_name: str
    issues: List[AccessibilityIssue]
    summary: Dict[str, int]
    compliance_score: float  # 0-100, where 100 = fully accessible

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def has_critical_issues(self) -> bool:
        return any(i.severity == IssueSeverity.CRITICAL for i in self.issues)


# ============================================================================
# Step 2: Build your processor
# ============================================================================
# The processor parses the file format and checks for WCAG violations.
# Keep each check in its own method for testability — this mirrors how
# DocxProcessor and PDFProcessor split checks into one _check_* method per
# WCAG concern.


class MarkdownProcessor:
    """
    Example accessibility scanner for Markdown files.

    Checks for:
    - Images missing alt text
    - Heading hierarchy issues (skipped levels)
    - Links with non-descriptive text ("click here")
    """

    def process_markdown(self, file_path: str) -> MarkdownProcessingResult:
        """Scan a Markdown file for accessibility issues.

        Named to match the real processors' entrypoint convention:
        process_docx(), process_pdf(), process_pptx(), process_xlsx().
        """
        logger.info(f"Scanning Markdown file: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")

        issues: List[AccessibilityIssue] = []
        issues.extend(self._check_image_alt_text(lines))
        issues.extend(self._check_heading_hierarchy(lines))
        issues.extend(self._check_link_text(lines))

        summary = {
            severity.value: sum(1 for i in issues if i.severity == severity)
            for severity in IssueSeverity
        }

        return MarkdownProcessingResult(
            file_path=file_path,
            file_name=Path(file_path).name,
            issues=issues,
            summary=summary,
            compliance_score=self._calculate_score(issues),
        )

    def _check_image_alt_text(self, lines: List[str]) -> List[AccessibilityIssue]:
        """WCAG 1.1.1: All images must have alt text."""
        issues = []
        for i, line in enumerate(lines, 1):
            # Match ![](url) — empty alt text
            if re.search(r"!\[\]\(", line):
                issues.append(
                    AccessibilityIssue(
                        category=IssueCategory.ALT_TEXT,
                        severity=IssueSeverity.CRITICAL,
                        description="Image is missing alt text",
                        location=f"Line {i}",
                        wcag_criteria="1.1.1",
                        fix_suggestion="Add descriptive alt text: ![description of image](url)",
                    )
                )
        return issues

    def _check_heading_hierarchy(self, lines: List[str]) -> List[AccessibilityIssue]:
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
                            category=IssueCategory.HEADING,
                            severity=IssueSeverity.HIGH,
                            description=f"Heading level skipped from H{last_level} to H{level}",
                            location=f"Line {i}",
                            wcag_criteria="1.3.1",
                            fix_suggestion=f"Use H{last_level + 1} instead of H{level}",
                        )
                    )
                last_level = level

        return issues

    def _check_link_text(self, lines: List[str]) -> List[AccessibilityIssue]:
        """WCAG 2.4.4: Links should have descriptive text."""
        issues = []
        bad_link_texts = {"click here", "here", "link", "read more", "more"}

        for i, line in enumerate(lines, 1):
            for match in re.finditer(r"\[([^\]]+)\]\(", line):
                link_text = match.group(1).strip().lower()
                if link_text in bad_link_texts:
                    issues.append(
                        AccessibilityIssue(
                            category=IssueCategory.LINK,
                            severity=IssueSeverity.MEDIUM,
                            description=f'Link text "{match.group(1)}" is not descriptive',
                            location=f"Line {i}",
                            wcag_criteria="2.4.4",
                            fix_suggestion="Use text that describes the link destination",
                        )
                    )

        return issues

    def _calculate_score(self, issues: List[AccessibilityIssue]) -> float:
        """Calculate accessibility score from 0-100."""
        if not issues:
            return 100.0

        deductions = {
            IssueSeverity.CRITICAL: 20,
            IssueSeverity.HIGH: 10,
            IssueSeverity.MEDIUM: 5,
            IssueSeverity.LOW: 2,
        }

        total_deduction = sum(deductions.get(i.severity, 5) for i in issues)
        return max(0.0, 100.0 - total_deduction)


# ============================================================================
# Step 3: Usage
# ============================================================================
# Generates a tiny sample document on the fly so this file runs standalone
# with no fixture dependencies: `python examples/custom_processor.py`

_SAMPLE_MARKDOWN = """# Getting Started

![](diagram.png)

### Skipped Straight to H3

See the docs [here](https://example.com/docs) for more.
"""


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(_SAMPLE_MARKDOWN)
        sample_path = f.name

    try:
        processor = MarkdownProcessor()
        result = processor.process_markdown(sample_path)

        print(f"File: {result.file_name}")
        print(f"Score: {result.compliance_score}/100")
        print(f"Issues: {result.issue_count} ({result.summary})")

        for issue in result.issues:
            print(f"  [{issue.severity.value}] {issue.description} ({issue.location})")
            if issue.fix_suggestion:
                print(f"    Fix: {issue.fix_suggestion}")
    finally:
        Path(sample_path).unlink(missing_ok=True)
