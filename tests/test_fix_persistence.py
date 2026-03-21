"""Tests for persisting remediation fixes to database."""
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the base module directly to avoid triggering the heavy
# remediation __init__.py imports (docx, pikepdf, etc.) which are not
# installed in the test environment.
_mod_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "education"
    / "remediation"
    / "base.py"
)
_spec = importlib.util.spec_from_file_location("remediation_base", _mod_path)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

FixedIssue = _base.FixedIssue
IssueCategory = _base.IssueCategory
IssueSeverity = _base.IssueSeverity


def test_fixed_issue_to_dict():
    """FixedIssue can be serialized to a dict suitable for ScanFix insertion."""
    fix = FixedIssue(
        issue_id="issue-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Missing alt text on figure",
        fixed_content="Graph showing enrollment trends",
        fix_method="ai_vision",
        confidence=0.65,
        needs_review=True,
        model_used="gemini",
        wcag_criteria="1.1.1",
        page_number=2,
    )
    d = fix.model_dump()
    assert d["category"] == "alt_text"
    assert d["confidence"] == 0.65
    assert d["needs_review"] is True
    assert d["model_used"] == "gemini"
    assert d["page_number"] == 2


def test_fixed_issue_enum_serialization():
    """Enum values serialize correctly for database storage."""
    fix = FixedIssue(
        issue_id="issue-2",
        category=IssueCategory.HEADING,
        severity=IssueSeverity.CRITICAL,
        description="Heading level skip",
        fixed_content="<h2>Section</h2>",
        fix_method="rule",
        confidence=1.0,
        needs_review=False,
    )
    d = fix.model_dump()
    assert d["category"] == "heading"
    assert d["severity"] == "critical"
    assert isinstance(d["category"], str)
    assert isinstance(d["severity"], str)


def test_fixed_issue_review_status_logic():
    """Auto-approved vs pending logic matches what remediation_job uses."""
    high_confidence = FixedIssue(
        issue_id="fix-high",
        category=IssueCategory.LANGUAGE,
        severity=IssueSeverity.LOW,
        description="Missing lang attribute",
        fixed_content='lang="en"',
        fix_method="rule",
        confidence=0.95,
        needs_review=False,
    )
    low_confidence = FixedIssue(
        issue_id="fix-low",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Missing alt text",
        fixed_content="A chart showing data",
        fix_method="ai_vision",
        confidence=0.45,
        needs_review=True,
    )

    # Mirrors the logic in remediation_job.py
    assert (
        "auto_approved" if not high_confidence.needs_review else "pending"
    ) == "auto_approved"
    assert (
        "auto_approved" if not low_confidence.needs_review else "pending"
    ) == "pending"


def test_fixed_issue_optional_fields_default_none():
    """Optional fields default to None for clean database insertion."""
    fix = FixedIssue(
        issue_id="issue-minimal",
        category=IssueCategory.TABLE,
        severity=IssueSeverity.MEDIUM,
        description="Table missing headers",
        fixed_content="<th>Header</th>",
        fix_method="rule",
    )
    d = fix.model_dump()
    assert d["location"] is None
    assert d["original_content"] is None
    assert d["model_used"] is None
    assert d["wcag_criteria"] is None
    assert d["page_number"] is None
    assert d["confidence"] == 1.0
    assert d["needs_review"] is False
