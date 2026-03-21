"""
Integration tests for reporting new accessibility issue types (Tasks 1-14)

Tests cover:
- Issue normalization for new types
- Seizure-risk issues always marked critical
- PDF and HTML report generation with new types
"""

import pytest

from src.db.scan_service import (
    normalize_issue,
    normalize_issues,
    ISSUE_TYPE_CATEGORY_MAP,
    CRITICAL_SEVERITY_TYPES,
)


class TestIssueNormalization:
    """Test issue normalization for new issue types from Tasks 1-14."""

    def test_new_issue_types_have_category_mapping(self):
        """Test that all new issue types from Tasks 1-14 have category mappings."""
        new_issue_types = [
            # PDF (Tasks 4, 14)
            "reading_order",
            "table_header",
            "table_accessibility",
            # Web - Shadow DOM (Task 13)
            "shadow_dom",
            "image-alt",
            "button-name",
            "link-name",
            "form-label",
            # XLSX (Tasks 10, 11)
            "conditional_format",
            "pivot_table",
            "color_only",
            # Multimedia (Tasks 2, 5)
            "red_flash",
            "flashing_content",
            "speaker_diarization",
            # PPTX (Tasks 8, 9)
            "animation",
            "animation_flash",
            "animation_auto",
            "embedded_media",
            # DOCX (Tasks 6, 7)
            "smartart",
            "embedded_object",
            "ole_object",
        ]

        for issue_type in new_issue_types:
            assert (
                issue_type in ISSUE_TYPE_CATEGORY_MAP
            ), f"Issue type '{issue_type}' missing from ISSUE_TYPE_CATEGORY_MAP"

    def test_normalize_reading_order_issue(self):
        """Test normalizing a reading order issue (Task 4)."""
        issue = {
            "type": "reading_order",
            "severity": "high",
            "description": "Reading order does not match visual layout",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "reading_order"
        assert normalized["severity"] in [
            "critical",
            "high",
            "serious",
            "medium",
            "low",
        ]

    def test_normalize_shadow_dom_issue(self):
        """Test normalizing a Shadow DOM issue (Task 13)."""
        issue = {
            "type": "image-alt",
            "impact": "critical",
            "description": "Image inside Shadow DOM missing alt text",
            "metadata": {"shadow_dom": True},
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "alt_text"
        assert normalized.get("metadata", {}).get("shadow_dom") is True

    def test_normalize_conditional_format_issue(self):
        """Test normalizing a conditional formatting issue (Task 10)."""
        issue = {
            "type": "conditional_format",
            "severity": "medium",
            "description": "Conditional formatting uses color alone",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "conditional_format"

    def test_normalize_pivot_table_issue(self):
        """Test normalizing a pivot table issue (Task 11)."""
        issue = {
            "type": "pivot_table",
            "severity": "medium",
            "description": "Pivot table has complex structure",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "pivot_table"

    def test_normalize_animation_issue(self):
        """Test normalizing an animation issue (Task 8)."""
        issue = {
            "type": "animation",
            "severity": "medium",
            "description": "Animation may cause motion sensitivity issues",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "animation"

    def test_normalize_smartart_issue(self):
        """Test normalizing a SmartArt issue (Task 6)."""
        issue = {
            "type": "smartart",
            "severity": "high",
            "description": "SmartArt diagram missing alt text",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "smartart"

    def test_normalize_embedded_object_issue(self):
        """Test normalizing an embedded object issue (Task 7)."""
        issue = {
            "type": "embedded_object",
            "severity": "medium",
            "description": "Embedded Excel spreadsheet needs description",
        }

        normalized = normalize_issue(issue)

        assert normalized["category"] == "embedded_object"

    def test_normalize_multiple_issues(self):
        """Test normalizing a list of issues."""
        issues = [
            {"type": "reading_order", "severity": "high"},
            {"type": "shadow_dom", "impact": "critical"},
            {"type": "pivot_table", "severity": "medium"},
        ]

        normalized = normalize_issues(issues)

        assert len(normalized) == 3
        assert all("category" in issue for issue in normalized)


class TestSeizureRiskSeverity:
    """Test that seizure-risk issues are always marked critical."""

    def test_red_flash_always_critical(self):
        """Test that red flash issues are always marked critical (WCAG 2.3.1)."""
        issue = {
            "type": "red_flash",
            "severity": "medium",  # Should be upgraded to critical
            "description": "Saturated red flashing detected",
        }

        normalized = normalize_issue(issue)

        assert normalized["severity"] == "critical"

    def test_flashing_content_always_critical(self):
        """Test that flashing content issues are always marked critical."""
        issue = {
            "type": "flashing_content",
            "severity": "low",  # Should be upgraded to critical
            "description": "Content flashes more than 3 times per second",
        }

        normalized = normalize_issue(issue)

        assert normalized["severity"] == "critical"

    def test_animation_flash_always_critical(self):
        """Test that animation flash issues are always marked critical."""
        issue = {
            "type": "animation_flash",
            "severity": "medium",  # Should be upgraded to critical
            "description": "Animation creates rapid flashing",
        }

        normalized = normalize_issue(issue)

        assert normalized["severity"] == "critical"

    def test_seizure_risk_types_constant(self):
        """Test that CRITICAL_SEVERITY_TYPES contains all seizure risk types."""
        expected_types = {"red_flash", "flashing_content", "animation_flash"}

        assert CRITICAL_SEVERITY_TYPES == expected_types


class TestPDFReportGeneration:
    """Test PDF report generation with new issue types."""

    def test_pdf_report_generator_imports(self):
        """Test that PDF report generator constants are properly defined."""
        from src.education.pdf_report_generator import (
            ISSUE_TYPE_DISPLAY_NAMES,
            SEIZURE_RISK_ISSUE_TYPES,
        )

        # Check seizure risk types
        assert "red_flash" in SEIZURE_RISK_ISSUE_TYPES
        assert "flashing_content" in SEIZURE_RISK_ISSUE_TYPES
        assert "animation_flash" in SEIZURE_RISK_ISSUE_TYPES

        # Check display names for new types
        assert "reading_order" in ISSUE_TYPE_DISPLAY_NAMES
        assert "shadow_dom" in ISSUE_TYPE_DISPLAY_NAMES
        assert "red_flash" in ISSUE_TYPE_DISPLAY_NAMES
        assert "conditional_format" in ISSUE_TYPE_DISPLAY_NAMES
        assert "pivot_table" in ISSUE_TYPE_DISPLAY_NAMES
        assert "animation" in ISSUE_TYPE_DISPLAY_NAMES
        assert "smartart" in ISSUE_TYPE_DISPLAY_NAMES

    def test_pdf_report_includes_display_names(self):
        """Test that PDF reports use human-readable display names."""
        from src.education.pdf_report_generator import ISSUE_TYPE_DISPLAY_NAMES

        # Verify display names are descriptive
        assert "Seizure" in ISSUE_TYPE_DISPLAY_NAMES["red_flash"]
        assert "Shadow DOM" in ISSUE_TYPE_DISPLAY_NAMES["shadow_dom"]
        assert "Reading Order" in ISSUE_TYPE_DISPLAY_NAMES["reading_order"]


class TestHTMLReportGeneration:
    """Test HTML report generation with new issue types."""

    def test_html_report_renders_seizure_warning(self):
        """Test that HTML reports include seizure warning for flashing issues."""
        from src.education.report_generator import AccessibilityReportGenerator

        scan_data = {
            "url": "https://example.com",
            "compliance_score": 50,
            "issues": [
                {
                    "type": "red_flash",
                    "impact": "critical",
                    "description": "Red flashing detected",
                    "element": "<div>",
                    "fix": "Remove flashing",
                }
            ],
        }

        html = AccessibilityReportGenerator.generate_website_report(scan_data)

        assert "SEIZURE RISK WARNING" in html
        assert "issue-seizure-risk" in html
        assert "badge-seizure" in html

    def test_html_report_renders_shadow_dom_info(self):
        """Test that HTML reports include Shadow DOM info for web component issues."""
        from src.education.report_generator import AccessibilityReportGenerator

        scan_data = {
            "url": "https://example.com",
            "compliance_score": 75,
            "issues": [
                {
                    "type": "image-alt",
                    "impact": "critical",
                    "description": "Image in Shadow DOM missing alt",
                    "element": '<img src="test.png">',
                    "fix": "Add alt attribute",
                    "metadata": {"shadow_dom": True},
                }
            ],
        }

        html = AccessibilityReportGenerator.generate_website_report(scan_data)

        assert "Shadow DOM Component" in html
        assert "issue-shadow-dom" in html
        assert "badge-shadow-dom" in html

    def test_html_report_renders_animation_badge(self):
        """Test that HTML reports include animation badge for animation issues."""
        from src.education.report_generator import AccessibilityReportGenerator

        scan_data = {
            "url": "https://example.com",
            "compliance_score": 80,
            "issues": [
                {
                    "type": "animation",
                    "impact": "medium",
                    "description": "Auto-starting animation detected",
                    "element": '<div class="animated">',
                    "fix": "Add user controls",
                }
            ],
        }

        html = AccessibilityReportGenerator.generate_website_report(scan_data)

        assert "Animation" in html
        assert "badge-animation" in html

    def test_html_report_css_contains_new_classes(self):
        """Test that HTML report CSS includes new issue type classes."""
        from src.education.report_generator import AccessibilityReportGenerator

        scan_data = {
            "url": "https://example.com",
            "compliance_score": 100,
            "issues": [],
        }

        html = AccessibilityReportGenerator.generate_website_report(scan_data)

        # Check CSS classes are defined
        assert ".issue-seizure-risk" in html
        assert ".issue-shadow-dom" in html
        assert ".issue-animation" in html
        assert ".badge-seizure" in html
        assert ".badge-shadow-dom" in html
        assert ".badge-pivot" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
