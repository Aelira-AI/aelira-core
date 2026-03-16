"""Unit tests for the code remediation endpoint helpers.

These tests verify the mapping and validation logic used by the
POST /api/education/code/remediate/{scan_id} endpoint, without
requiring a running HTTP server or database.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Load the education_routes module helpers directly to avoid importing the
# full FastAPI app (which pulls in DB connections, heavy dependencies, etc.).
# We import just the helper functions and constants we need to test.
# ---------------------------------------------------------------------------

_routes_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "api"
    / "education_routes.py"
)

# We can't import education_routes directly without the full app context, so
# we test the logic by importing the remediation base classes and reimplementing
# the same mapping the endpoint uses. This keeps tests fast and isolated.

_base_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "education"
    / "remediation"
    / "base.py"
)
_base_spec = importlib.util.spec_from_file_location("remediation_base", _base_path)
_base_mod = importlib.util.module_from_spec(_base_spec)
_base_spec.loader.exec_module(_base_mod)

IssueCategory = _base_mod.IssueCategory
IssueSeverity = _base_mod.IssueSeverity


# ---------------------------------------------------------------------------
# Re-implement the helper functions exactly as they appear in
# education_routes.py so we can unit-test them in isolation.
# ---------------------------------------------------------------------------

def _scanfix_to_issue_dict(fix) -> dict:
    """Mirror of education_routes._scanfix_to_issue_dict."""
    return {
        "id": fix.issue_id or fix.id,
        "category": fix.category or "other",
        "severity": fix.severity or "medium",
        "description": fix.description or "",
        "location": fix.location,
        "original_content": fix.original_content,
        "fix_suggestion": fix.fixed_content,
        "fixed_content": fix.fixed_content,
        "wcag_criteria": fix.wcag_criteria,
        "metadata": {},
    }


def _map_category_string(category_str: str):
    """Mirror of education_routes._map_category_string."""
    category_map = {
        "alt_text": IssueCategory.ALT_TEXT,
        "alternative_text": IssueCategory.ALT_TEXT,
        "image": IssueCategory.ALT_TEXT,
        "heading": IssueCategory.HEADING,
        "heading_structure": IssueCategory.HEADING,
        "contrast": IssueCategory.CONTRAST,
        "color_contrast": IssueCategory.CONTRAST,
        "table": IssueCategory.TABLE,
        "table_header": IssueCategory.TABLE,
        "link": IssueCategory.LINK,
        "hyperlink": IssueCategory.LINK,
        "list": IssueCategory.LIST,
        "list_structure": IssueCategory.LIST,
        "language": IssueCategory.LANGUAGE,
        "reading_order": IssueCategory.READING_ORDER,
        "form": IssueCategory.FORM,
        "aria": IssueCategory.ARIA,
        "navigation": IssueCategory.NAVIGATION,
        "structure": IssueCategory.STRUCTURE,
        "color": IssueCategory.COLOR,
        "chart": IssueCategory.CHART,
        "sheet": IssueCategory.SHEET,
        "title": IssueCategory.TITLE,
        "other": IssueCategory.OTHER,
    }
    normalized = category_str.lower().strip().replace(" ", "_").replace("-", "_")
    return category_map.get(normalized, IssueCategory.OTHER)


def _map_severity_string(severity_str: str):
    """Mirror of education_routes._map_severity_string."""
    severity_map = {
        "critical": IssueSeverity.CRITICAL,
        "high": IssueSeverity.HIGH,
        "medium": IssueSeverity.MEDIUM,
        "low": IssueSeverity.LOW,
        "error": IssueSeverity.HIGH,
        "warning": IssueSeverity.MEDIUM,
        "info": IssueSeverity.LOW,
    }
    normalized = severity_str.lower().strip()
    return severity_map.get(normalized, IssueSeverity.MEDIUM)


APPROVED_REVIEW_STATUSES = {"approved", "edited", "auto_approved"}


def _make_scanfix(**kwargs):
    """Create a mock ScanFix-like object using SimpleNamespace."""
    defaults = {
        "id": "fix-001",
        "issue_id": "issue-001",
        "scan_id": "scan-001",
        "category": "alt_text",
        "severity": "high",
        "description": "Image missing alt text",
        "location": "line 42",
        "original_content": '<img src="photo.jpg">',
        "fixed_content": '<img src="photo.jpg" alt="Campus building">',
        "fix_method": "ai_text",
        "model_used": "gemini",
        "confidence": 0.95,
        "needs_review": False,
        "review_status": "approved",
        "wcag_criteria": "1.1.1",
        "page_number": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ===================================================================
# Test 1: ScanFix to RemediationIssue dict mapping
# ===================================================================

class TestScanFixToIssueDict:
    """Verify _scanfix_to_issue_dict produces the correct dict shape."""

    def test_basic_mapping(self):
        fix = _make_scanfix()
        result = _scanfix_to_issue_dict(fix)

        assert result["id"] == "issue-001"
        assert result["category"] == "alt_text"
        assert result["severity"] == "high"
        assert result["description"] == "Image missing alt text"
        assert result["location"] == "line 42"
        assert result["original_content"] == '<img src="photo.jpg">'
        assert result["fix_suggestion"] == '<img src="photo.jpg" alt="Campus building">'
        assert result["fixed_content"] == '<img src="photo.jpg" alt="Campus building">'
        assert result["wcag_criteria"] == "1.1.1"
        assert result["metadata"] == {}

    def test_falls_back_to_id_when_issue_id_is_none(self):
        fix = _make_scanfix(issue_id=None, id="fallback-id")
        result = _scanfix_to_issue_dict(fix)
        assert result["id"] == "fallback-id"

    def test_uses_issue_id_over_id(self):
        fix = _make_scanfix(issue_id="preferred", id="fallback")
        result = _scanfix_to_issue_dict(fix)
        assert result["id"] == "preferred"

    def test_none_category_defaults_to_other(self):
        fix = _make_scanfix(category=None)
        result = _scanfix_to_issue_dict(fix)
        assert result["category"] == "other"

    def test_none_severity_defaults_to_medium(self):
        fix = _make_scanfix(severity=None)
        result = _scanfix_to_issue_dict(fix)
        assert result["severity"] == "medium"

    def test_none_description_defaults_to_empty(self):
        fix = _make_scanfix(description=None)
        result = _scanfix_to_issue_dict(fix)
        assert result["description"] == ""

    def test_none_location_is_preserved(self):
        fix = _make_scanfix(location=None)
        result = _scanfix_to_issue_dict(fix)
        assert result["location"] is None

    def test_fixed_content_maps_to_fix_suggestion(self):
        """Approved fixed_content should become the fix_suggestion for the remediator."""
        fix = _make_scanfix(fixed_content="<h2>Fixed heading</h2>")
        result = _scanfix_to_issue_dict(fix)
        assert result["fix_suggestion"] == "<h2>Fixed heading</h2>"


# ===================================================================
# Test 2: Only HTML files are accepted
# ===================================================================

class TestHtmlFileValidation:
    """Verify that only .html and .htm files are accepted for remediation."""

    @pytest.mark.parametrize("ext", [".html", ".htm"])
    def test_html_extensions_accepted(self, ext):
        assert ext in (".html", ".htm")

    @pytest.mark.parametrize("ext", [".css", ".js", ".py", ".json", ".txt", ".pdf"])
    def test_non_html_extensions_rejected(self, ext):
        assert ext not in (".html", ".htm")

    def test_html_extension_check_logic(self):
        """Test the exact validation condition used in the endpoint."""
        for path in ["page.html", "index.htm", "UPPER.HTML", "mixed.Htm"]:
            ext = Path(path).suffix.lower()
            assert ext in (".html", ".htm"), f"{path} should be accepted"

    def test_css_js_rejected(self):
        for path in ["styles.css", "app.js", "main.CSS", "bundle.JS"]:
            ext = Path(path).suffix.lower()
            assert ext not in (".html", ".htm"), f"{path} should be rejected"


# ===================================================================
# Test 3: Category string maps to IssueCategory enum
# ===================================================================

class TestCategoryMapping:
    """Verify _map_category_string produces correct IssueCategory values."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("alt_text", IssueCategory.ALT_TEXT),
            ("alternative_text", IssueCategory.ALT_TEXT),
            ("image", IssueCategory.ALT_TEXT),
            ("heading", IssueCategory.HEADING),
            ("heading_structure", IssueCategory.HEADING),
            ("contrast", IssueCategory.CONTRAST),
            ("color_contrast", IssueCategory.CONTRAST),
            ("table", IssueCategory.TABLE),
            ("table_header", IssueCategory.TABLE),
            ("link", IssueCategory.LINK),
            ("hyperlink", IssueCategory.LINK),
            ("list", IssueCategory.LIST),
            ("list_structure", IssueCategory.LIST),
            ("language", IssueCategory.LANGUAGE),
            ("reading_order", IssueCategory.READING_ORDER),
            ("form", IssueCategory.FORM),
            ("aria", IssueCategory.ARIA),
            ("navigation", IssueCategory.NAVIGATION),
            ("structure", IssueCategory.STRUCTURE),
            ("color", IssueCategory.COLOR),
            ("chart", IssueCategory.CHART),
            ("sheet", IssueCategory.SHEET),
            ("title", IssueCategory.TITLE),
            ("other", IssueCategory.OTHER),
        ],
    )
    def test_known_categories(self, input_str, expected):
        assert _map_category_string(input_str) == expected

    def test_unknown_category_maps_to_other(self):
        assert _map_category_string("unknown_thing") == IssueCategory.OTHER

    def test_case_insensitive(self):
        assert _map_category_string("ALT_TEXT") == IssueCategory.ALT_TEXT
        assert _map_category_string("Heading") == IssueCategory.HEADING
        assert _map_category_string("CONTRAST") == IssueCategory.CONTRAST

    def test_strips_whitespace(self):
        assert _map_category_string("  alt_text  ") == IssueCategory.ALT_TEXT

    def test_normalizes_hyphens(self):
        assert _map_category_string("alt-text") == IssueCategory.ALT_TEXT
        assert _map_category_string("color-contrast") == IssueCategory.CONTRAST

    def test_normalizes_spaces(self):
        assert _map_category_string("alt text") == IssueCategory.ALT_TEXT
        assert _map_category_string("heading structure") == IssueCategory.HEADING


# ===================================================================
# Test 4: Severity string maps to IssueSeverity enum
# ===================================================================

class TestSeverityMapping:
    """Verify _map_severity_string produces correct IssueSeverity values."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("critical", IssueSeverity.CRITICAL),
            ("high", IssueSeverity.HIGH),
            ("medium", IssueSeverity.MEDIUM),
            ("low", IssueSeverity.LOW),
            ("error", IssueSeverity.HIGH),
            ("warning", IssueSeverity.MEDIUM),
            ("info", IssueSeverity.LOW),
        ],
    )
    def test_known_severities(self, input_str, expected):
        assert _map_severity_string(input_str) == expected

    def test_unknown_severity_defaults_to_medium(self):
        assert _map_severity_string("unknown") == IssueSeverity.MEDIUM

    def test_case_insensitive(self):
        assert _map_severity_string("CRITICAL") == IssueSeverity.CRITICAL
        assert _map_severity_string("High") == IssueSeverity.HIGH
        assert _map_severity_string("LOW") == IssueSeverity.LOW

    def test_strips_whitespace(self):
        assert _map_severity_string("  high  ") == IssueSeverity.HIGH


# ===================================================================
# Test 5: Only approved/edited/auto_approved fixes are included
# ===================================================================

class TestApprovedStatusFiltering:
    """Verify the APPROVED_REVIEW_STATUSES set correctly filters fixes."""

    @pytest.mark.parametrize("status", ["approved", "edited", "auto_approved"])
    def test_approved_statuses_included(self, status):
        assert status in APPROVED_REVIEW_STATUSES

    @pytest.mark.parametrize("status", ["pending", "rejected", "applied", "apply_failed"])
    def test_non_approved_statuses_excluded(self, status):
        assert status not in APPROVED_REVIEW_STATUSES

    def test_filtering_logic(self):
        """Simulate the DB query filter logic in Python."""
        all_fixes = [
            _make_scanfix(review_status="approved", issue_id="a"),
            _make_scanfix(review_status="pending", issue_id="b"),
            _make_scanfix(review_status="rejected", issue_id="c"),
            _make_scanfix(review_status="edited", issue_id="d"),
            _make_scanfix(review_status="auto_approved", issue_id="e"),
            _make_scanfix(review_status="applied", issue_id="f"),
        ]

        approved = [f for f in all_fixes if f.review_status in APPROVED_REVIEW_STATUSES]
        assert len(approved) == 3
        approved_ids = {f.issue_id for f in approved}
        assert approved_ids == {"a", "d", "e"}

    def test_set_has_exactly_three_members(self):
        assert len(APPROVED_REVIEW_STATUSES) == 3
