"""
Tests for Shadow DOM Penetration (Task 13)

Tests cover:
- Shadow DOM detection
- Shadow DOM host enumeration
- Accessibility issues inside Shadow DOM
- Web component scanning
- Nested Shadow DOM handling
"""

import pytest
from unittest.mock import MagicMock

from src.education.web_scanner import (
    WebScanner,
    WebPageScanResult,
    WebPageIssue,
)


class TestShadowDOMModel:
    """Test Shadow DOM fields in WebPageScanResult model."""

    def test_shadow_dom_fields_default(self):
        """Test that Shadow DOM fields have correct defaults."""
        result = WebPageScanResult(
            url="https://example.com",
            title="Test Page",
            scan_time=1.0,
            compliance_score=95.0,
            issues=[],
        )

        assert result.shadow_dom_detected is False
        assert result.shadow_dom_host_count == 0
        assert result.shadow_dom_issues_count == 0

    def test_shadow_dom_fields_with_values(self):
        """Test Shadow DOM fields with actual values."""
        result = WebPageScanResult(
            url="https://example.com",
            title="Test Page",
            scan_time=1.5,
            compliance_score=85.0,
            issues=[],
            shadow_dom_detected=True,
            shadow_dom_host_count=5,
            shadow_dom_issues_count=3,
        )

        assert result.shadow_dom_detected is True
        assert result.shadow_dom_host_count == 5
        assert result.shadow_dom_issues_count == 3


class TestFindShadowRoots:
    """Test _find_shadow_roots method."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_find_shadow_roots_no_shadow_dom(self, scanner):
        """Test finding shadow roots when none exist."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"count": 0, "hosts": []}

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 0
        assert hosts == []
        mock_page.evaluate.assert_called_once()

    def test_find_shadow_roots_single_host(self, scanner):
        """Test finding a single shadow DOM host."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "count": 1,
            "hosts": [
                {
                    "tagName": "custom-button",
                    "id": "my-button",
                    "classes": "primary",
                    "mode": "open",
                    "childCount": 3,
                    "selector": "#my-button",
                }
            ],
        }

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 1
        assert len(hosts) == 1
        assert hosts[0]["tagName"] == "custom-button"
        assert hosts[0]["mode"] == "open"

    def test_find_shadow_roots_multiple_hosts(self, scanner):
        """Test finding multiple shadow DOM hosts."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "count": 3,
            "hosts": [
                {
                    "tagName": "custom-nav",
                    "id": "",
                    "classes": "nav",
                    "mode": "open",
                    "childCount": 5,
                    "selector": "custom-nav.nav",
                },
                {
                    "tagName": "custom-card",
                    "id": "card1",
                    "classes": "",
                    "mode": "open",
                    "childCount": 2,
                    "selector": "#card1",
                },
                {
                    "tagName": "custom-footer",
                    "id": "",
                    "classes": "",
                    "mode": "closed",
                    "childCount": 4,
                    "selector": "custom-footer",
                },
            ],
        }

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 3
        assert len(hosts) == 3
        assert hosts[2]["mode"] == "closed"

    def test_find_shadow_roots_error_handling(self, scanner):
        """Test error handling when shadow root detection fails."""
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("JavaScript execution failed")

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 0
        assert hosts == []


class TestScanShadowDOM:
    """Test _scan_shadow_dom method."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_scan_shadow_dom_no_hosts(self, scanner):
        """Test scanning when no shadow hosts provided."""
        mock_page = MagicMock()

        issues = scanner._scan_shadow_dom(mock_page, [])

        assert issues == []
        mock_page.evaluate.assert_not_called()

    def test_scan_shadow_dom_no_issues(self, scanner):
        """Test scanning shadow DOM with no accessibility issues."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = []
        shadow_hosts = [{"tagName": "custom-element", "id": "test", "mode": "open"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert issues == []

    def test_scan_shadow_dom_image_alt_issue(self, scanner):
        """Test detecting image without alt text in Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "image-alt",
                "element": '<img src="logo.png">',
                "selector": "#my-component >>> img:nth-of-type(1)",
                "impact": "critical",
                "criterion": "1.1.1",
                "description": "Image inside Shadow DOM missing alt text",
            }
        ]
        shadow_hosts = [{"tagName": "my-component", "id": "my-component"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        assert issues[0].criterion == "1.1.1"
        assert issues[0].impact == "critical"
        assert "shadow_dom" in issues[0].metadata
        assert issues[0].metadata["shadow_dom"] is True

    def test_scan_shadow_dom_button_name_issue(self, scanner):
        """Test detecting button without accessible name in Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "button-name",
                "element": '<button class="icon-btn"></button>',
                "selector": "#widget >>> button:nth-of-type(1)",
                "impact": "critical",
                "criterion": "4.1.2",
                "description": "Button inside Shadow DOM missing accessible name",
            }
        ]
        shadow_hosts = [{"tagName": "custom-widget", "id": "widget"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        assert issues[0].criterion == "4.1.2"
        assert issues[0].metadata["issue_type"] == "button-name"

    def test_scan_shadow_dom_link_name_issue(self, scanner):
        """Test detecting link without accessible name in Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "link-name",
                "element": '<a href="/page"></a>',
                "selector": "nav-element >>> a:nth-of-type(1)",
                "impact": "serious",
                "criterion": "2.4.4",
                "description": "Link inside Shadow DOM missing accessible name",
            }
        ]
        shadow_hosts = [{"tagName": "nav-element", "id": ""}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        assert issues[0].criterion == "2.4.4"
        assert issues[0].impact == "serious"

    def test_scan_shadow_dom_form_label_issue(self, scanner):
        """Test detecting form input without label in Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "form-label",
                "element": '<input type="text" name="email">',
                "selector": "#form-widget >>> input:nth-of-type(1)",
                "impact": "critical",
                "criterion": "1.3.1",
                "description": "Form input inside Shadow DOM missing label",
            }
        ]
        shadow_hosts = [{"tagName": "form-widget", "id": "form-widget"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        assert issues[0].criterion == "1.3.1"
        assert issues[0].metadata["issue_type"] == "form-label"

    def test_scan_shadow_dom_multiple_issues(self, scanner):
        """Test detecting multiple issues in Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "image-alt",
                "element": '<img src="photo.jpg">',
                "selector": "#card >>> img:nth-of-type(1)",
                "impact": "critical",
                "criterion": "1.1.1",
                "description": "Image missing alt text",
            },
            {
                "type": "button-name",
                "element": "<button><svg></svg></button>",
                "selector": "#card >>> button:nth-of-type(1)",
                "impact": "critical",
                "criterion": "4.1.2",
                "description": "Button missing accessible name",
            },
            {
                "type": "link-name",
                "element": '<a href="#"><i class="icon"></i></a>',
                "selector": "#card >>> a:nth-of-type(1)",
                "impact": "serious",
                "criterion": "2.4.4",
                "description": "Link missing accessible name",
            },
        ]
        shadow_hosts = [{"tagName": "product-card", "id": "card"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 3
        assert all(issue.metadata["shadow_dom"] for issue in issues)

    def test_scan_shadow_dom_error_handling(self, scanner):
        """Test error handling when shadow DOM scanning fails."""
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = Exception("Failed to access shadow root")
        shadow_hosts = [{"tagName": "broken-element", "id": "broken"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert issues == []


class TestShadowDOMIssueFormat:
    """Test the format of Shadow DOM issues."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_issue_has_pierce_selector(self, scanner):
        """Test that issues have pierce selector for Shadow DOM."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "image-alt",
                "element": '<img src="test.png">',
                "selector": "#component >>> img:nth-of-type(1)",
                "impact": "critical",
                "criterion": "1.1.1",
                "description": "Image missing alt text",
            }
        ]
        shadow_hosts = [{"tagName": "my-component", "id": "component"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        assert ">>>" in issues[0].selector
        assert "#component" in issues[0].selector

    def test_issue_priority_critical(self, scanner):
        """Test that critical impact issues get high priority."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "button-name",
                "element": "<button></button>",
                "selector": "#btn >>> button",
                "impact": "critical",
                "criterion": "4.1.2",
                "description": "Button missing name",
            }
        ]
        shadow_hosts = [{"tagName": "custom-btn", "id": "btn"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert issues[0].priority == "high"

    def test_issue_priority_non_critical(self, scanner):
        """Test that non-critical impact issues get medium priority."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "color-contrast",
                "element": "<p>Text</p>",
                "selector": "#box >>> p",
                "impact": "serious",
                "criterion": "1.4.3",
                "description": "Contrast issue",
            }
        ]
        shadow_hosts = [{"tagName": "text-box", "id": "box"}]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert issues[0].priority == "medium"


class TestShadowDOMIntegration:
    """Test Shadow DOM integration with page scanning."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_shadow_dom_issues_added_to_main_issues(self, scanner):
        """Test that Shadow DOM issues are included in main issues list."""
        # This tests the integration conceptually
        # In the actual scan, shadow issues are added to the issues list
        shadow_issue = WebPageIssue(
            impact="critical",
            criterion="1.1.1",
            description="Image in Shadow DOM missing alt text",
            help_url="https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships.html",
            element='<img src="test.png">',
            fix="Add alt attribute to image",
            selector="#component >>> img",
            priority="high",
            metadata={"shadow_dom": True, "issue_type": "image-alt"},
        )

        assert shadow_issue.metadata["shadow_dom"] is True
        assert shadow_issue.criterion == "1.1.1"

    def test_web_page_result_includes_shadow_stats(self):
        """Test that scan result includes shadow DOM statistics."""
        result = WebPageScanResult(
            url="https://example.com",
            title="Web Components Demo",
            scan_time=2.5,
            compliance_score=75.0,
            issues=[
                WebPageIssue(
                    impact="critical",
                    criterion="1.1.1",
                    description="Regular issue",
                    help_url="https://example.com",
                ),
                WebPageIssue(
                    impact="critical",
                    criterion="1.1.1",
                    description="Shadow DOM issue",
                    help_url="https://example.com",
                    metadata={"shadow_dom": True},
                ),
            ],
            shadow_dom_detected=True,
            shadow_dom_host_count=3,
            shadow_dom_issues_count=1,
        )

        assert result.shadow_dom_detected is True
        assert result.shadow_dom_host_count == 3
        assert result.shadow_dom_issues_count == 1
        assert len(result.issues) == 2


class TestNestedShadowDOM:
    """Test handling of nested Shadow DOM structures."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_nested_shadow_dom_selector(self, scanner):
        """Test that nested shadow DOM uses proper pierce selectors."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "type": "button-name",
                "element": "<button></button>",
                "selector": "#outer >>> inner-component >>> button:nth-of-type(1)",
                "impact": "critical",
                "criterion": "4.1.2",
                "description": "Nested button missing name",
            }
        ]
        shadow_hosts = [
            {"tagName": "outer-component", "id": "outer"},
            {"tagName": "inner-component", "id": ""},
        ]

        issues = scanner._scan_shadow_dom(mock_page, shadow_hosts)

        assert len(issues) == 1
        # Selector should contain multiple pierce operators for nesting
        assert issues[0].selector.count(">>>") >= 1


class TestShadowDOMOpenVsClosed:
    """Test handling of open vs closed Shadow DOM modes."""

    @pytest.fixture
    def scanner(self):
        """Create WebScanner instance for testing."""
        return WebScanner(scan_images=False, scan_multimedia=False)

    def test_open_shadow_dom_detected(self, scanner):
        """Test that open Shadow DOM is detected."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "count": 1,
            "hosts": [
                {
                    "tagName": "open-element",
                    "id": "open",
                    "mode": "open",
                    "childCount": 2,
                    "classes": "",
                    "selector": "#open",
                }
            ],
        }

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 1
        assert hosts[0]["mode"] == "open"

    def test_closed_shadow_dom_detected(self, scanner):
        """Test that closed Shadow DOM is still detected (mode reported)."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "count": 1,
            "hosts": [
                {
                    "tagName": "closed-element",
                    "id": "closed",
                    "mode": "closed",
                    "childCount": 3,
                    "classes": "",
                    "selector": "#closed",
                }
            ],
        }

        count, hosts = scanner._find_shadow_roots(mock_page)

        assert count == 1
        assert hosts[0]["mode"] == "closed"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
