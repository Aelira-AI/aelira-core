"""Tests for web scanner ScanFix persistence mapping logic.

Verifies the mapping from web issue dicts (as built in
process_web_scan_background) to ScanFix record fields, including
fallback behaviour for missing code fixes, location formatting,
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

wcag_criterion_to_category = _mapper.wcag_criterion_to_category
impact_to_severity = _mapper.impact_to_severity
impact_to_confidence = _mapper.impact_to_confidence


# ---------------------------------------------------------------------------
# Helper: replicate the mapping logic from process_web_scan_background
# ---------------------------------------------------------------------------


def _map_issue_to_scanfix_fields(issue_dict: dict, scan_id: str = "test-scan") -> dict:
    """Mirror the field mapping used in process_web_scan_background().

    Returns a dict of the keyword arguments that would be passed to the
    ScanFix constructor.
    """
    criterion = issue_dict.get("criterion", "")
    impact = issue_dict.get("impact", "moderate")
    code_fix = issue_dict.get("generated_code_fix")
    human_fix = issue_dict.get("fix")
    selector = issue_dict.get("selector", "")
    page_url = issue_dict.get("page_url", "")

    return {
        "scan_id": scan_id,
        "issue_id": f"web-{criterion}-{hash(issue_dict.get('description', '')) % 100000}",
        "category": wcag_criterion_to_category(criterion),
        "severity": impact_to_severity(impact),
        "description": issue_dict.get("description", ""),
        "location": f"{page_url} | {selector}" if selector else page_url,
        "original_content": issue_dict.get("element", ""),
        "fixed_content": code_fix or human_fix or "",
        "fix_method": "ai" if code_fix else "heuristic",
        "model_used": "gemini" if code_fix else None,
        "confidence": impact_to_confidence(impact),
        "needs_review": True,
        "review_status": "pending",
        "wcag_criteria": criterion,
        "page_number": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_issue() -> dict:
    """A fully-populated web issue dict with an AI code fix."""
    return {
        "page_url": "https://example.edu/syllabus",
        "page_title": "Fall 2026 Syllabus",
        "impact": "serious",
        "criterion": "1.1.1",
        "description": "Image missing alt text",
        "help_url": "https://dequeuniversity.com/rules/axe/4.4/image-alt",
        "element": '<img src="chart.png">',
        "fix": "Add alt attribute to image",
        "generated_code_fix": '<img src="chart.png" alt="Enrollment chart">',
        "selector": "#main img.chart",
        "xpath": "/html/body/main/img[1]",
        "screenshot": None,
    }


@pytest.fixture
def heuristic_issue() -> dict:
    """A web issue dict without an AI code fix (heuristic fallback)."""
    return {
        "page_url": "https://example.edu/contact",
        "page_title": "Contact Us",
        "impact": "moderate",
        "criterion": "3.3.2",
        "description": "Form input missing label",
        "help_url": "https://dequeuniversity.com/rules/axe/4.4/label",
        "element": '<input type="text" name="email">',
        "fix": "Add a label element associated with the input",
        "generated_code_fix": None,
        "selector": "input[name=email]",
        "xpath": "/html/body/form/input[2]",
        "screenshot": None,
    }


@pytest.fixture
def minimal_issue() -> dict:
    """A web issue with only required fields, everything else missing."""
    return {
        "page_url": "https://example.edu",
        "impact": "minor",
        "criterion": "2.4.2",
        "description": "Page title is empty",
    }


# ---------------------------------------------------------------------------
# Tests: basic field mapping
# ---------------------------------------------------------------------------


class TestBasicFieldMapping:
    """Verify each ScanFix field is correctly populated from issue dicts."""

    def test_scan_id_passed_through(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue, scan_id="scan-abc")
        assert fields["scan_id"] == "scan-abc"

    def test_issue_id_format(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["issue_id"].startswith("web-1.1.1-")
        # Should be a stable hash
        fields2 = _map_issue_to_scanfix_fields(full_issue)
        assert fields["issue_id"] == fields2["issue_id"]

    def test_category_from_criterion(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["category"] == "alt_text"  # 1.1.1 -> alt_text

    def test_severity_from_impact(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["severity"] == "high"  # serious -> high

    def test_description_copied(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["description"] == "Image missing alt text"

    def test_original_content_from_element(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["original_content"] == '<img src="chart.png">'

    def test_wcag_criteria_copied(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["wcag_criteria"] == "1.1.1"

    def test_page_number_always_none(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["page_number"] is None

    def test_needs_review_always_true(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["needs_review"] is True

    def test_review_status_always_pending(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["review_status"] == "pending"

    def test_confidence_from_impact(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["confidence"] == 0.8  # serious -> 0.8


# ---------------------------------------------------------------------------
# Tests: fix content fallback
# ---------------------------------------------------------------------------


class TestFixContentFallback:
    """Issues without generated_code_fix should fall back to fix text."""

    def test_ai_code_fix_preferred(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["fixed_content"] == '<img src="chart.png" alt="Enrollment chart">'
        assert fields["fix_method"] == "ai"
        assert fields["model_used"] == "gemini"

    def test_heuristic_fallback_when_no_code_fix(self, heuristic_issue):
        fields = _map_issue_to_scanfix_fields(heuristic_issue)
        assert (
            fields["fixed_content"] == "Add a label element associated with the input"
        )
        assert fields["fix_method"] == "heuristic"
        assert fields["model_used"] is None

    def test_empty_string_when_no_fix_at_all(self, minimal_issue):
        fields = _map_issue_to_scanfix_fields(minimal_issue)
        assert fields["fixed_content"] == ""
        assert fields["fix_method"] == "heuristic"
        assert fields["model_used"] is None

    def test_code_fix_empty_string_treated_as_falsy(self):
        """An empty generated_code_fix string should fall back to human fix."""
        issue = {
            "page_url": "https://example.edu",
            "impact": "moderate",
            "criterion": "1.1.1",
            "description": "Missing alt",
            "fix": "Add alt text",
            "generated_code_fix": "",
        }
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["fixed_content"] == "Add alt text"
        assert fields["fix_method"] == "heuristic"


# ---------------------------------------------------------------------------
# Tests: location formatting
# ---------------------------------------------------------------------------


class TestLocationFormatting:
    """Location should combine page_url and selector correctly."""

    def test_url_and_selector_combined(self, full_issue):
        fields = _map_issue_to_scanfix_fields(full_issue)
        assert fields["location"] == "https://example.edu/syllabus | #main img.chart"

    def test_url_only_when_no_selector(self, minimal_issue):
        fields = _map_issue_to_scanfix_fields(minimal_issue)
        assert fields["location"] == "https://example.edu"

    def test_url_only_when_selector_is_empty(self):
        issue = {
            "page_url": "https://example.edu/page",
            "impact": "moderate",
            "criterion": "2.4.2",
            "description": "No title",
            "selector": "",
        }
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["location"] == "https://example.edu/page"

    def test_url_only_when_selector_is_none(self):
        issue = {
            "page_url": "https://example.edu/page",
            "impact": "moderate",
            "criterion": "2.4.2",
            "description": "No title",
            "selector": None,
        }
        fields = _map_issue_to_scanfix_fields(issue)
        # None is falsy, so location should be just the URL
        assert fields["location"] == "https://example.edu/page"

    def test_empty_url_with_selector(self):
        issue = {
            "impact": "moderate",
            "criterion": "1.1.1",
            "description": "Alt text missing",
            "selector": "img.hero",
        }
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["location"] == " | img.hero"


# ---------------------------------------------------------------------------
# Tests: category mapper delegation
# ---------------------------------------------------------------------------


class TestCategoryMapperDelegation:
    """The mapper functions are called with the correct criterion/impact values."""

    def test_criterion_1_1_1_maps_to_alt_text(self):
        issue = {"criterion": "1.1.1", "impact": "critical", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "alt_text"

    def test_criterion_1_4_3_maps_to_contrast(self):
        issue = {"criterion": "1.4.3", "impact": "serious", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "contrast"

    def test_criterion_2_4_4_maps_to_link(self):
        issue = {"criterion": "2.4.4", "impact": "moderate", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "link"

    def test_criterion_3_1_1_maps_to_language(self):
        issue = {"criterion": "3.1.1", "impact": "minor", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "language"

    def test_criterion_4_1_2_maps_to_aria(self):
        issue = {"criterion": "4.1.2", "impact": "moderate", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "aria"

    def test_unknown_criterion_defaults_to_structure(self):
        issue = {"criterion": "99.99.99", "impact": "moderate", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "structure"

    def test_empty_criterion_defaults_to_structure(self):
        issue = {"impact": "moderate", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["category"] == "structure"

    def test_critical_impact_severity_and_confidence(self):
        issue = {"criterion": "1.1.1", "impact": "critical", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "critical"
        assert fields["confidence"] == 0.9

    def test_serious_impact_severity_and_confidence(self):
        issue = {"criterion": "1.1.1", "impact": "serious", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "high"
        assert fields["confidence"] == 0.8

    def test_moderate_impact_severity_and_confidence(self):
        issue = {"criterion": "1.1.1", "impact": "moderate", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "medium"
        assert fields["confidence"] == 0.7

    def test_minor_impact_severity_and_confidence(self):
        issue = {"criterion": "1.1.1", "impact": "minor", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "low"
        assert fields["confidence"] == 0.6

    def test_missing_impact_defaults_to_moderate(self):
        issue = {"criterion": "1.1.1", "description": "x"}
        fields = _map_issue_to_scanfix_fields(issue)
        assert fields["severity"] == "medium"
        assert fields["confidence"] == 0.7


# ---------------------------------------------------------------------------
# Tests: issue_id stability and uniqueness
# ---------------------------------------------------------------------------


class TestIssueIdGeneration:
    """The issue_id should be deterministic for the same input."""

    def test_same_issue_produces_same_id(self):
        issue = {
            "criterion": "1.1.1",
            "impact": "serious",
            "description": "Missing alt text",
        }
        id1 = _map_issue_to_scanfix_fields(issue)["issue_id"]
        id2 = _map_issue_to_scanfix_fields(issue)["issue_id"]
        assert id1 == id2

    def test_different_descriptions_produce_different_ids(self):
        issue_a = {
            "criterion": "1.1.1",
            "impact": "serious",
            "description": "Missing alt text",
        }
        issue_b = {
            "criterion": "1.1.1",
            "impact": "serious",
            "description": "Empty alt text",
        }
        id_a = _map_issue_to_scanfix_fields(issue_a)["issue_id"]
        id_b = _map_issue_to_scanfix_fields(issue_b)["issue_id"]
        assert id_a != id_b

    def test_different_criteria_produce_different_ids(self):
        issue_a = {"criterion": "1.1.1", "impact": "serious", "description": "x"}
        issue_b = {"criterion": "2.4.2", "impact": "serious", "description": "x"}
        id_a = _map_issue_to_scanfix_fields(issue_a)["issue_id"]
        id_b = _map_issue_to_scanfix_fields(issue_b)["issue_id"]
        assert id_a != id_b
