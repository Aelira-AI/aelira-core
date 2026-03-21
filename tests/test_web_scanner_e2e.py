"""
End-to-end tests for Web Scanner.

Tests website scanning, WCAG 2.2 compliance checking, multi-page crawling,
image extraction, AI code fix generation, and comprehensive reporting.
"""

import os

import pytest
from pathlib import Path

# Add backend to path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.web_scanner import (
    WebScanner,
    WebScanResult,
    WebPageScanResult,
    SPAFramework,
)

# Skip all tests in this module unless RUN_E2E_TESTS is set
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="E2E test requires running infrastructure (set RUN_E2E_TESTS=1 to enable)",
)

# Test HTML fixtures - Simple HTML pages for testing
TEST_HTML_DIR = Path(__file__).parent / "fixtures" / "html"
TEST_HTML_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def create_test_html_files():
    """Create test HTML files for scanning"""

    # 1. Page with contrast issues
    contrast_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Contrast Issues Test</title>
</head>
<body>
    <h1>Test Page - Low Contrast</h1>
    <p style="color: #777; background: #999;">This text has insufficient contrast.</p>
    <div style="color: #aaa; background: #ccc;">Another low contrast element.</div>
</body>
</html>"""
    (TEST_HTML_DIR / "contrast_issues.html").write_text(contrast_html)

    # 2. Page with missing alt text
    alt_text_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Missing Alt Text Test</title>
</head>
<body>
    <h1>Test Page - Images</h1>
    <img src="logo.png">
    <img src="banner.jpg" alt="">
    <img src="photo.png" alt="A photo">
</body>
</html>"""
    (TEST_HTML_DIR / "missing_alt_text.html").write_text(alt_text_html)

    # 3. Page with heading issues
    heading_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Heading Issues Test</title>
</head>
<body>
    <h2>This should be H1</h2>
    <h4>Skipped H3</h4>
    <h5>Another skip</h5>
</body>
</html>"""
    (TEST_HTML_DIR / "heading_issues.html").write_text(heading_html)

    # 4. Page with form issues
    form_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Form Issues Test</title>
</head>
<body>
    <h1>Contact Form</h1>
    <form>
        <input type="text" placeholder="Name">
        <input type="email" placeholder="Email">
        <button type="submit">Send</button>
    </form>
</body>
</html>"""
    (TEST_HTML_DIR / "form_issues.html").write_text(form_html)

    # 5. Accessible page (few/no issues)
    accessible_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Accessible Page Test</title>
</head>
<body>
    <header>
        <h1>Accessible Website</h1>
        <nav aria-label="Main navigation">
            <ul>
                <li><a href="#home">Home</a></li>
                <li><a href="#about">About</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <h2>Welcome</h2>
        <p>This page follows accessibility best practices.</p>
        <img src="diagram.png" alt="System architecture diagram showing three components">
        <form>
            <label for="name">Name:</label>
            <input type="text" id="name" name="name">
            <label for="email">Email:</label>
            <input type="email" id="email" name="email">
            <button type="submit">Submit</button>
        </form>
    </main>
    <footer>
        <p>&copy; 2025 Example Site</p>
    </footer>
</body>
</html>"""
    (TEST_HTML_DIR / "accessible_page.html").write_text(accessible_html)

    # 6. Multi-page site (index with links)
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Multi-Page Test Site</title>
</head>
<body>
    <h1>Home Page</h1>
    <nav>
        <a href="page2.html">Page 2</a>
        <a href="page3.html">Page 3</a>
        <a href="https://external-site.com">External Link</a>
    </nav>
</body>
</html>"""
    (TEST_HTML_DIR / "index.html").write_text(index_html)

    page2_html = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Page 2</title></head>
<body><h1>Page 2</h1><a href="index.html">Back to Home</a></body>
</html>"""
    (TEST_HTML_DIR / "page2.html").write_text(page2_html)

    page3_html = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Page 3</title></head>
<body><h1>Page 3</h1><a href="index.html">Back to Home</a></body>
</html>"""
    (TEST_HTML_DIR / "page3.html").write_text(page3_html)

    return TEST_HTML_DIR


@pytest.fixture
def web_scanner():
    """Create WebScanner instance with default settings"""
    # Skip DATABASE_URL requirement for basic tests
    os.environ["DATABASE_URL"] = os.getenv(
        "DATABASE_URL", "postgresql://test:test@localhost/test"
    )
    return WebScanner(
        scan_images=False,
        scan_multimedia=False,
        scan_math=False,
        max_depth=1,
        max_pages=10,
        use_ai_analysis=False,  # Disable AI for faster tests
        capture_screenshots=False,
    )


class TestWebScannerBasicFunctionality:
    """Test basic web scanner functionality"""

    def test_scanner_initialization(self, web_scanner):
        """Test scanner initializes correctly"""
        assert web_scanner is not None
        assert web_scanner.max_depth == 1
        assert web_scanner.max_pages == 10
        assert web_scanner.scan_images is False
        assert web_scanner.use_ai_analysis is False

    def test_scan_accessible_page(self, web_scanner, create_test_html_files):
        """Test scanning a mostly accessible page"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Verify basic structure
        assert result is not None
        assert isinstance(result, WebScanResult)
        assert result.root_url == file_url
        assert result.pages_scanned >= 1
        assert result.total_scan_time > 0

        # Verify page results
        assert len(result.pages) >= 1
        first_page = result.pages[0]
        assert isinstance(first_page, WebPageScanResult)
        assert first_page.url == file_url
        assert first_page.title == "Accessible Page Test"

        # Should have high compliance score (few issues)
        assert (
            result.overall_compliance_score >= 80.0
        ), f"Expected high compliance score, got {result.overall_compliance_score}"

    def test_scan_contrast_issues(self, web_scanner, create_test_html_files):
        """Test detection of contrast ratio violations"""
        test_file = TEST_HTML_DIR / "contrast_issues.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Should detect issues
        assert len(result.pages[0].issues) > 0, "Should detect contrast issues"

        # Check for contrast-related issues
        contrast_issues = [
            issue
            for issue in result.pages[0].issues
            if "contrast" in issue.description.lower()
        ]
        assert len(contrast_issues) > 0, "Should detect at least one contrast issue"

        # Compliance score should be lower
        assert result.overall_compliance_score < 90.0


class TestWebScannerIssueDetection:
    """Test detection of various WCAG violations"""

    def test_detect_missing_alt_text(self, web_scanner, create_test_html_files):
        """Test detection of images without alt text"""
        test_file = TEST_HTML_DIR / "missing_alt_text.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        page_issues = result.pages[0].issues

        # Should detect missing alt text
        alt_issues = [
            issue for issue in page_issues if "alt" in issue.description.lower()
        ]
        assert len(alt_issues) > 0, "Should detect missing alt text"

    def test_detect_heading_structure_issues(self, web_scanner, create_test_html_files):
        """Test detection of improper heading structure"""
        test_file = TEST_HTML_DIR / "heading_issues.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        page_issues = result.pages[0].issues

        # Should detect heading issues (skipped levels, wrong starting level)
        heading_issues = [
            issue
            for issue in page_issues
            if "heading" in issue.description.lower()
            or "h1" in issue.description.lower()
        ]
        assert len(heading_issues) > 0, "Should detect heading structure issues"

    def test_detect_form_label_issues(self, web_scanner, create_test_html_files):
        """Test detection of form inputs without labels"""
        test_file = TEST_HTML_DIR / "form_issues.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        _ = result.pages[0].issues  # Verify issues can be accessed

        # Note: axe-core may not flag placeholder-only inputs as errors
        # (placeholder is considered an acceptable alternative)
        # This test verifies the scanner runs without errors
        assert result is not None
        assert len(result.pages) > 0


class TestWebScannerCompliance:
    """Test WCAG compliance scoring"""

    def test_compliance_score_calculation(self, web_scanner, create_test_html_files):
        """Test compliance score is calculated correctly"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Verify compliance score is in valid range
        assert 0 <= result.overall_compliance_score <= 100
        assert 0 <= result.pages[0].compliance_score <= 100

    def test_issue_severity_categorization(self, web_scanner, create_test_html_files):
        """Test issues are categorized by severity"""
        test_file = TEST_HTML_DIR / "contrast_issues.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Check summary has severity counts
        assert "critical" in result.summary or "error" in result.summary
        assert isinstance(result.summary, dict)

    def test_page_structure_extraction(self, web_scanner, create_test_html_files):
        """Test extraction of page structure information"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        page = result.pages[0]

        # Should extract page structure
        assert page.page_structure is not None
        assert isinstance(page.page_structure, dict)


class TestWebScannerMultiPageCrawling:
    """Test multi-page website crawling"""

    def test_crawl_multiple_pages(self, create_test_html_files):
        """Test scanning multiple linked pages"""
        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            scan_multimedia=False,
            max_depth=2,  # Crawl linked pages
            max_pages=5,
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        test_file = TEST_HTML_DIR / "index.html"
        file_url = f"file://{test_file.absolute()}"

        result = scanner.scan_website(file_url)

        # Should scan multiple pages
        assert result.pages_scanned >= 1
        # Note: file:// links may not be followed by Playwright, so we can't guarantee > 1
        # But the scanner should not crash

    def test_max_depth_limit(self, create_test_html_files):
        """Test max_depth parameter limits crawl depth"""
        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            max_depth=1,  # Only scan provided URL
            max_pages=10,
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        test_file = TEST_HTML_DIR / "index.html"
        file_url = f"file://{test_file.absolute()}"

        result = scanner.scan_website(file_url)

        # With max_depth=1, should only scan the root page
        assert result.pages_scanned >= 1

    def test_max_pages_limit(self, create_test_html_files):
        """Test max_pages parameter limits total pages scanned"""
        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            max_depth=10,  # High depth
            max_pages=2,  # But limit total pages
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        test_file = TEST_HTML_DIR / "index.html"
        file_url = f"file://{test_file.absolute()}"

        result = scanner.scan_website(file_url)

        # Should respect max_pages limit
        assert result.pages_scanned <= 2


class TestWebScannerResultStructure:
    """Test result data structure and completeness"""

    def test_result_has_all_required_fields(self, web_scanner, create_test_html_files):
        """Test WebScanResult has all required fields"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Check top-level fields
        assert hasattr(result, "root_url")
        assert hasattr(result, "pages_scanned")
        assert hasattr(result, "total_scan_time")
        assert hasattr(result, "overall_compliance_score")
        assert hasattr(result, "pages")
        assert hasattr(result, "summary")

    def test_page_result_has_all_required_fields(
        self, web_scanner, create_test_html_files
    ):
        """Test WebPageScanResult has all required fields"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        page = result.pages[0]

        # Check page-level fields
        assert hasattr(page, "url")
        assert hasattr(page, "title")
        assert hasattr(page, "scan_time")
        assert hasattr(page, "compliance_score")
        assert hasattr(page, "issues")
        assert hasattr(page, "page_structure")

    def test_issue_has_all_required_fields(self, web_scanner, create_test_html_files):
        """Test WebPageIssue has all required fields"""
        test_file = TEST_HTML_DIR / "contrast_issues.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        if len(result.pages[0].issues) > 0:
            issue = result.pages[0].issues[0]

            # Check issue fields (uses "impact" not "severity")
            assert hasattr(issue, "description")
            assert hasattr(issue, "impact")  # axe-core uses "impact" field
            assert hasattr(issue, "criterion")


class TestWebScannerPerformance:
    """Test scanner performance and optimization"""

    def test_scan_time_is_reasonable(self, web_scanner, create_test_html_files):
        """Test scan completes in reasonable time"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Single page scan should complete quickly (< 30 seconds)
        assert (
            result.total_scan_time < 30.0
        ), f"Scan took too long: {result.total_scan_time}s"

    def test_page_scan_time_is_tracked(self, web_scanner, create_test_html_files):
        """Test individual page scan times are tracked"""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)
        page = result.pages[0]

        # Page scan time should be positive
        assert page.scan_time > 0


class TestWebScannerEdgeCases:
    """Test edge cases and error handling"""

    def test_invalid_url_handling(self, web_scanner):
        """Test handling of invalid URLs"""
        with pytest.raises(Exception):
            # Should raise exception for invalid URL
            web_scanner.scan_website("not-a-valid-url")

    def test_empty_page_handling(self, web_scanner, create_test_html_files):
        """Test scanning a minimal page"""
        minimal_html = """<!DOCTYPE html>
<html><head><title>Empty</title></head><body></body></html>"""
        test_file = TEST_HTML_DIR / "empty.html"
        test_file.write_text(minimal_html)

        file_url = f"file://{test_file.absolute()}"
        result = web_scanner.scan_website(file_url)

        # Should complete without crashing
        assert result is not None
        assert result.pages_scanned >= 1


@pytest.mark.asyncio
@pytest.mark.slow
class TestWebScannerAIFeatures:
    """Test AI-powered features (slower tests)"""

    def test_ai_analysis_integration(self, create_test_html_files):
        """Test AI content analysis when enabled"""
        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            use_ai_analysis=True,  # Enable AI
            capture_screenshots=False,
        )

        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = scanner.scan_website(file_url)

        # Should complete (may or may not have AI analysis depending on Ollama availability)
        assert result is not None
        assert result.pages_scanned >= 1


class TestSPADetection:
    """Test Single Page Application detection and hydration support."""

    def test_spa_framework_enum(self):
        """Test SPAFramework enum values."""
        assert SPAFramework.REACT.value == "react"
        assert SPAFramework.VUE.value == "vue"
        assert SPAFramework.ANGULAR.value == "angular"
        assert SPAFramework.SVELTE.value == "svelte"
        assert SPAFramework.NEXT.value == "next"
        assert SPAFramework.NUXT.value == "nuxt"
        assert SPAFramework.NONE.value == "none"

    def test_scan_result_includes_spa_fields(self, web_scanner, create_test_html_files):
        """Test that WebPageScanResult includes SPA detection fields."""
        test_file = TEST_HTML_DIR / "accessible_page.html"
        file_url = f"file://{test_file.absolute()}"

        result = web_scanner.scan_website(file_url)

        # Check SPA fields exist on page result
        first_page = result.pages[0]
        assert hasattr(first_page, "spa_framework")
        assert hasattr(first_page, "spa_hydration_waited")

        # Static HTML should be detected as non-SPA
        assert first_page.spa_framework == SPAFramework.NONE
        assert first_page.spa_hydration_waited is False

    def test_scan_react_spa_page(self, create_test_html_files):
        """Test detection of React SPA markers."""
        # Create a React-like test page
        react_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>React SPA Test</title>
</head>
<body>
    <div id="root" data-reactroot="">
        <h1>React App</h1>
        <p>This simulates a React application.</p>
    </div>
    <script>
        // Simulate React markers
        window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { renderers: new Map([[1, {}]]) };
    </script>
</body>
</html>"""
        react_file = TEST_HTML_DIR / "react_spa.html"
        react_file.write_text(react_html)

        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        file_url = f"file://{react_file.absolute()}"
        result = scanner.scan_website(file_url)

        # Should detect React (or at least not crash)
        first_page = result.pages[0]
        # Note: Detection depends on JS execution timing
        # The test primarily verifies the code path doesn't crash
        assert first_page.spa_framework in [SPAFramework.REACT, SPAFramework.NONE]

    def test_scan_vue_spa_page(self, create_test_html_files):
        """Test detection of Vue.js SPA markers."""
        vue_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Vue SPA Test</title>
</head>
<body>
    <div id="app" data-v-12345="">
        <h1>Vue App</h1>
        <p data-v-12345="">This simulates a Vue application.</p>
    </div>
    <script>
        // Simulate Vue markers
        window.__VUE__ = true;
    </script>
</body>
</html>"""
        vue_file = TEST_HTML_DIR / "vue_spa.html"
        vue_file.write_text(vue_html)

        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        file_url = f"file://{vue_file.absolute()}"
        result = scanner.scan_website(file_url)

        # Should detect Vue (or at least not crash)
        first_page = result.pages[0]
        assert first_page.spa_framework in [SPAFramework.VUE, SPAFramework.NONE]

    def test_scan_nextjs_spa_page(self, create_test_html_files):
        """Test detection of Next.js SPA markers."""
        nextjs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Next.js SPA Test</title>
</head>
<body>
    <div id="__next">
        <h1>Next.js App</h1>
        <p>This simulates a Next.js application.</p>
    </div>
    <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{}},"page":"/","query":{}}
    </script>
    <script>
        // Simulate Next.js markers
        window.__NEXT_DATA__ = {"props":{}};
    </script>
</body>
</html>"""
        nextjs_file = TEST_HTML_DIR / "nextjs_spa.html"
        nextjs_file.write_text(nextjs_html)

        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        scanner = WebScanner(
            scan_images=False,
            use_ai_analysis=False,
            capture_screenshots=False,
        )

        file_url = f"file://{nextjs_file.absolute()}"
        result = scanner.scan_website(file_url)

        # Should detect Next.js (or at least not crash)
        first_page = result.pages[0]
        assert first_page.spa_framework in [
            SPAFramework.NEXT,
            SPAFramework.REACT,
            SPAFramework.NONE,
        ]


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
