"""Tests for code scanner ScanFix persistence mapping logic.

Verifies the mapping from CodeIssue dicts (as built in
process_code_background) to ScanFix record fields, including
fallback behaviour for missing AI fixes, location formatting,
and correct delegation to category_mapper functions.
"""

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load category_mapper directly (avoids heavy remediation __init__.py imports)
# ---------------------------------------------------------------------------
_mapper_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "education"
    / "remediation"
    / "category_mapper.py"
)
_spec = importlib.util.spec_from_file_location("category_mapper", _mapper_path)
_mapper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mapper)

code_rule_to_category = _mapper.code_rule_to_category
impact_to_severity = _mapper.impact_to_severity
impact_to_confidence = _mapper.impact_to_confidence


# ---------------------------------------------------------------------------
# Helper: replicate the mapping logic from process_code_background
# ---------------------------------------------------------------------------


def _map_code_issue_to_scanfix_fields(
    issue_dict: dict, scan_id: str = "test-scan"
) -> dict:
    """Mirror the field mapping used in process_code_background().

    Returns a dict of the keyword arguments that would be passed to the
    ScanFix constructor.
    """
    scanner_cat = issue_dict.get("category", "html")
    rule = issue_dict.get("rule", "")
    severity_raw = issue_dict.get("severity", "moderate")
    ai_fix = issue_dict.get("ai_generated_fix")
    human_fix = issue_dict.get("fix_suggestion", "")
    file_path = issue_dict.get("file_path", "")
    line_num = issue_dict.get("line_number")

    return {
        "scan_id": scan_id,
        "issue_id": f"code-{rule}-{hash(issue_dict.get('description', '')) % 100000}",
        "category": code_rule_to_category(scanner_cat, rule),
        "severity": impact_to_severity(severity_raw),
        "description": issue_dict.get("description", ""),
        "location": f"{file_path}:{line_num}" if line_num else file_path,
        "original_content": issue_dict.get("code_snippet", ""),
        "fixed_content": ai_fix or human_fix or "",
        "fix_method": "ai" if ai_fix else "heuristic",
        "model_used": "gemini" if ai_fix else None,
        "confidence": impact_to_confidence(severity_raw),
        "needs_review": True,
        "review_status": "pending",
        "wcag_criteria": issue_dict.get("wcag_criterion", ""),
        "page_number": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_code_issue() -> dict:
    """A fully-populated code issue dict with an AI-generated fix."""
    return {
        "severity": "serious",
        "category": "html",
        "rule": "image-alt",
        "description": "Image missing alt attribute",
        "file_path": "templates/index.html",
        "line_number": 42,
        "code_snippet": '<img src="banner.png">',
        "fix_suggestion": "Add an alt attribute to the image",
        "ai_generated_fix": '<img src="banner.png" alt="Campus welcome banner">',
        "wcag_criterion": "1.1.1",
    }


@pytest.fixture
def heuristic_code_issue() -> dict:
    """A code issue dict without an AI fix (heuristic fallback)."""
    return {
        "severity": "moderate",
        "category": "html",
        "rule": "form-label",
        "description": "Form input missing associated label",
        "file_path": "templates/contact.html",
        "line_number": 18,
        "code_snippet": '<input type="text" name="email">',
        "fix_suggestion": "Add a <label> element with a for attribute matching the input id",
        "ai_generated_fix": None,
        "wcag_criterion": "3.3.2",
    }


@pytest.fixture
def minimal_code_issue() -> dict:
    """A code issue with only required fields, everything else missing."""
    return {
        "severity": "minor",
        "category": "css",
        "rule": "focus-indicator",
        "description": "No visible focus indicator on interactive element",
        "file_path": "static/styles.css",
        "fix_suggestion": "",
        "wcag_criterion": "2.4.7",
    }


# ---------------------------------------------------------------------------
# Tests: basic field mapping
# ---------------------------------------------------------------------------


class TestBasicFieldMapping:
    """Verify each ScanFix field is correctly populated from code issue dicts."""

    def test_scan_id_passed_through(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue, scan_id="scan-xyz")
        assert fields["scan_id"] == "scan-xyz"

    def test_issue_id_format(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["issue_id"].startswith("code-image-alt-")
        # Should be a stable hash
        fields2 = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["issue_id"] == fields2["issue_id"]

    def test_category_from_scanner_category_and_rule(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["category"] == "alt_text"  # (html, image-alt) -> alt_text

    def test_severity_from_severity_field(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["severity"] == "high"  # serious -> high

    def test_description_copied(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["description"] == "Image missing alt attribute"

    def test_original_content_from_code_snippet(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["original_content"] == '<img src="banner.png">'

    def test_wcag_criteria_copied(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["wcag_criteria"] == "1.1.1"

    def test_page_number_always_none(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["page_number"] is None

    def test_needs_review_always_true(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["needs_review"] is True

    def test_review_status_always_pending(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["review_status"] == "pending"

    def test_confidence_from_severity(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["confidence"] == 0.8  # serious -> 0.8


# ---------------------------------------------------------------------------
# Tests: fix content fallback
# ---------------------------------------------------------------------------


class TestFixContentFallback:
    """Issues without ai_generated_fix should fall back to fix_suggestion."""

    def test_ai_fix_preferred(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert (
            fields["fixed_content"]
            == '<img src="banner.png" alt="Campus welcome banner">'
        )
        assert fields["fix_method"] == "ai"
        assert fields["model_used"] == "gemini"

    def test_heuristic_fallback_when_no_ai_fix(self, heuristic_code_issue):
        fields = _map_code_issue_to_scanfix_fields(heuristic_code_issue)
        assert (
            fields["fixed_content"]
            == "Add a <label> element with a for attribute matching the input id"
        )
        assert fields["fix_method"] == "heuristic"
        assert fields["model_used"] is None

    def test_empty_string_when_no_fix_at_all(self, minimal_code_issue):
        fields = _map_code_issue_to_scanfix_fields(minimal_code_issue)
        assert fields["fixed_content"] == ""
        assert fields["fix_method"] == "heuristic"
        assert fields["model_used"] is None

    def test_ai_fix_empty_string_treated_as_falsy(self):
        """An empty ai_generated_fix string should fall back to fix_suggestion."""
        issue = {
            "severity": "moderate",
            "category": "html",
            "rule": "image-alt",
            "description": "Missing alt",
            "file_path": "index.html",
            "fix_suggestion": "Add alt text",
            "ai_generated_fix": "",
            "wcag_criterion": "1.1.1",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["fixed_content"] == "Add alt text"
        assert fields["fix_method"] == "heuristic"

    def test_both_fixes_missing(self):
        """When both ai_generated_fix is None and fix_suggestion is empty."""
        issue = {
            "severity": "minor",
            "category": "html",
            "rule": "lang-attribute",
            "description": "Missing lang attribute",
            "file_path": "page.html",
            "fix_suggestion": "",
            "ai_generated_fix": None,
            "wcag_criterion": "3.1.1",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["fixed_content"] == ""
        assert fields["fix_method"] == "heuristic"
        assert fields["model_used"] is None


# ---------------------------------------------------------------------------
# Tests: location formatting
# ---------------------------------------------------------------------------


class TestLocationFormatting:
    """Location should combine file_path and line_number correctly."""

    def test_file_path_and_line_number_combined(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["location"] == "templates/index.html:42"

    def test_file_path_only_when_no_line_number(self, minimal_code_issue):
        fields = _map_code_issue_to_scanfix_fields(minimal_code_issue)
        assert fields["location"] == "static/styles.css"

    def test_file_path_only_when_line_number_is_none(self):
        issue = {
            "severity": "moderate",
            "category": "html",
            "rule": "page-title",
            "description": "Page title missing",
            "file_path": "templates/base.html",
            "line_number": None,
            "fix_suggestion": "Add a title element",
            "wcag_criterion": "2.4.2",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["location"] == "templates/base.html"

    def test_file_path_only_when_line_number_is_zero(self):
        """Line number 0 is falsy, so location should be just the file path."""
        issue = {
            "severity": "moderate",
            "category": "html",
            "rule": "page-title",
            "description": "No title",
            "file_path": "index.html",
            "line_number": 0,
            "fix_suggestion": "Add title",
            "wcag_criterion": "2.4.2",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        # 0 is falsy, so location is just file_path
        assert fields["location"] == "index.html"

    def test_line_number_one(self):
        """Line number 1 should produce file_path:1."""
        issue = {
            "severity": "minor",
            "category": "html",
            "rule": "lang-attribute",
            "description": "Missing lang",
            "file_path": "index.html",
            "line_number": 1,
            "fix_suggestion": "Add lang",
            "wcag_criterion": "3.1.1",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["location"] == "index.html:1"

    def test_empty_file_path_with_line_number(self):
        issue = {
            "severity": "moderate",
            "category": "html",
            "rule": "image-alt",
            "description": "Missing alt",
            "line_number": 10,
            "fix_suggestion": "Add alt",
            "wcag_criterion": "1.1.1",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["location"] == ":10"


# ---------------------------------------------------------------------------
# Tests: category mapper delegation for code rules
# ---------------------------------------------------------------------------


class TestCategoryMapperDelegation:
    """The mapper functions are called with the correct category/rule values."""

    def test_html_image_alt_maps_to_alt_text(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "critical",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "alt_text"

    def test_html_heading_hierarchy_maps_to_heading(self):
        issue = {
            "category": "html",
            "rule": "heading-hierarchy",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "heading"

    def test_html_form_label_maps_to_form(self):
        issue = {
            "category": "html",
            "rule": "form-label",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "form"

    def test_html_lang_attribute_maps_to_language(self):
        issue = {
            "category": "html",
            "rule": "lang-attribute",
            "severity": "minor",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "language"

    def test_html_page_title_maps_to_title(self):
        issue = {
            "category": "html",
            "rule": "page-title",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "title"

    def test_css_focus_indicator_maps_to_navigation(self):
        issue = {
            "category": "css",
            "rule": "focus-indicator",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "navigation"

    def test_css_color_contrast_maps_to_contrast(self):
        issue = {
            "category": "css",
            "rule": "color-contrast",
            "severity": "serious",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "contrast"

    def test_aria_wildcard_maps_to_aria(self):
        issue = {
            "category": "aria",
            "rule": "any-rule-name",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "aria"

    def test_unknown_category_rule_defaults_to_structure(self):
        issue = {
            "category": "unknown",
            "rule": "unknown-rule",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "structure"

    def test_missing_category_defaults_to_html(self):
        """When category is missing, defaults to 'html' which with unknown rule -> structure."""
        issue = {"rule": "unknown-rule", "severity": "moderate", "description": "x"}
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "structure"

    def test_missing_rule_defaults_to_empty(self):
        """When rule is missing, defaults to empty string."""
        issue = {"category": "html", "severity": "moderate", "description": "x"}
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["category"] == "structure"  # (html, "") -> structure

    def test_critical_severity_maps_correctly(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "critical",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "critical"
        assert fields["confidence"] == 0.9

    def test_serious_severity_maps_correctly(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "serious",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "high"
        assert fields["confidence"] == 0.8

    def test_moderate_severity_maps_correctly(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "moderate",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "medium"
        assert fields["confidence"] == 0.7

    def test_minor_severity_maps_correctly(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "minor",
            "description": "x",
        }
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "low"
        assert fields["confidence"] == 0.6

    def test_missing_severity_defaults_to_moderate(self):
        issue = {"category": "html", "rule": "image-alt", "description": "x"}
        fields = _map_code_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "medium"
        assert fields["confidence"] == 0.7


# ---------------------------------------------------------------------------
# Tests: issue_id stability and uniqueness
# ---------------------------------------------------------------------------


class TestIssueIdGeneration:
    """The issue_id should be deterministic for the same input."""

    def test_same_issue_produces_same_id(self):
        issue = {
            "category": "html",
            "rule": "image-alt",
            "severity": "serious",
            "description": "Missing alt text",
        }
        id1 = _map_code_issue_to_scanfix_fields(issue)["issue_id"]
        id2 = _map_code_issue_to_scanfix_fields(issue)["issue_id"]
        assert id1 == id2

    def test_different_descriptions_produce_different_ids(self):
        issue_a = {
            "category": "html",
            "rule": "image-alt",
            "severity": "serious",
            "description": "Missing alt text",
        }
        issue_b = {
            "category": "html",
            "rule": "image-alt",
            "severity": "serious",
            "description": "Empty alt text",
        }
        id_a = _map_code_issue_to_scanfix_fields(issue_a)["issue_id"]
        id_b = _map_code_issue_to_scanfix_fields(issue_b)["issue_id"]
        assert id_a != id_b

    def test_different_rules_produce_different_ids(self):
        issue_a = {
            "category": "html",
            "rule": "image-alt",
            "severity": "serious",
            "description": "x",
        }
        issue_b = {
            "category": "html",
            "rule": "form-label",
            "severity": "serious",
            "description": "x",
        }
        id_a = _map_code_issue_to_scanfix_fields(issue_a)["issue_id"]
        id_b = _map_code_issue_to_scanfix_fields(issue_b)["issue_id"]
        assert id_a != id_b

    def test_issue_id_starts_with_code_prefix(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert fields["issue_id"].startswith("code-")

    def test_issue_id_contains_rule_name(self, full_code_issue):
        fields = _map_code_issue_to_scanfix_fields(full_code_issue)
        assert "image-alt" in fields["issue_id"]
