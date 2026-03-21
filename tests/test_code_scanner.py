"""
Tests for Code Scanner - Analyzing HTML/CSS/JS files for accessibility issues.
"""

import pytest
import os
import tempfile
import zipfile
from pathlib import Path
from src.education.code_scanner import CodeScanner, CodeScanResult


@pytest.fixture
def code_scanner():
    """Create a CodeScanner instance."""
    return CodeScanner(
        scan_images=False,
        generate_fixes=False,  # Disable AI for tests
        validate_alt_text=False,
        scan_cvd=False,
    )


@pytest.fixture
def temp_project():
    """Create a temporary project with multiple files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        dir_path = Path(temp_dir)

        # 1. HTML file with issues
        html_content = """<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
    <h1>Correct H1</h1>
    <h3>Skipped H2</h3>
    <img src="test.png">
    <input type="text" id="name">
</body>
</html>"""
        (dir_path / "index.html").write_text(html_content)

        # 2. CSS file with issues
        css_content = """
.btn:focus { outline: none; }
.small-text { font-size: 10px; }
"""
        (dir_path / "styles.css").write_text(css_content)

        # 3. JS file with issues
        js_content = """
function handleClick() {
    document.getElementById('popup').style.display = 'block';
}
// onclick used without keyboard equivalent
document.getElementById('btn').onclick = handleClick;
"""
        (dir_path / "script.js").write_text(js_content)

        yield temp_dir


class TestCodeScanner:
    """Test suite for CodeScanner."""

    def test_scanner_initialization(self, code_scanner):
        """Test scanner initializes correctly."""
        assert code_scanner is not None
        assert code_scanner.generate_fixes is False

    def test_scan_single_html_file(self, code_scanner):
        """Test scanning a single HTML file."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write("""<!DOCTYPE html>
<html lang="en">
<head><title>Test Page</title></head>
<body>
    <h1>Title</h1>
    <img src="logo.png" alt="Logo">
</body>
</html>""")
            temp_path = f.name

        try:
            result = code_scanner.scan_uploaded_code(temp_path)
            assert isinstance(result, CodeScanResult)
            assert result.files_analyzed == 1
            assert result.total_lines > 0
            # Should have high score for clean HTML
            assert result.compliance_score >= 90.0
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_detect_html_issues(self, code_scanner):
        """Test detection of common HTML accessibility issues."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
    <h3>Missing H1 and H2</h3>
    <img src="bad.png">
    <input type="text">
</body>
</html>""")
            temp_path = f.name

        try:
            result = code_scanner.scan_uploaded_code(temp_path)
            issues = result.issues

            # Check for specific issues
            rule_ids = [i.rule for i in issues]
            assert "lang-attribute" in rule_ids
            assert "page-title" in rule_ids
            assert "image-alt" in rule_ids
            assert "form-label" in rule_ids

            # Score should be low
            assert result.compliance_score < 70.0
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_detect_css_issues(self, code_scanner):
        """Test detection of CSS accessibility issues."""
        with tempfile.NamedTemporaryFile(suffix=".css", delete=False, mode="w") as f:
            f.write("""
.no-outline:focus { outline: none; }
.too-small { font-size: 9px; }
""")
            temp_path = f.name

        try:
            result = code_scanner.scan_uploaded_code(temp_path)
            issues = result.issues

            rule_ids = [i.rule for i in issues]
            assert "focus-indicator" in rule_ids
            assert "font-size" in rule_ids
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_detect_js_issues(self, code_scanner):
        """Test detection of JavaScript accessibility issues."""
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write("""
element.onclick = function() { doSomething(); };
video.play(); // auto-play
""")
            temp_path = f.name

        try:
            result = code_scanner.scan_uploaded_code(temp_path)
            issues = result.issues

            rule_ids = [i.rule for i in issues]
            assert "keyboard-handler" in rule_ids
            assert "auto-play" in rule_ids
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_scan_directory(self, code_scanner, temp_project):
        """Test scanning a directory with multiple files."""
        result = code_scanner._scan_directory(temp_project, "Test Project")

        assert result.files_analyzed == 3
        assert len(result.issues) > 0
        assert result.project_name == "Test Project"

        # Verify category separation
        categories = {i.category for i in result.issues}
        assert "html" in categories
        assert "css" in categories
        assert "javascript" in categories

    def test_scan_zip_file(self, code_scanner, temp_project):
        """Test scanning a ZIP archive of a project."""
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            zip_path = tf.name

        try:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for root, _, files in os.walk(temp_project):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zf.write(file_path, arcname=file)

            result = code_scanner.scan_uploaded_code(zip_path)
            assert result.files_analyzed == 3
            assert len(result.issues) > 0
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

    def test_compliance_score_calculation(self, code_scanner):
        """Test that compliance score calculation logic works."""
        # 1. Zero issues = 100%
        summary_perfect = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
        score_perfect = code_scanner._calculate_compliance_score(summary_perfect, 1)
        assert score_perfect == 100.0

        # 2. Many issues = lower score
        summary_bad = {"critical": 2, "serious": 3, "moderate": 5, "minor": 10}
        score_bad = code_scanner._calculate_compliance_score(summary_bad, 1)
        assert score_bad < 50.0

    def test_image_detection(self, code_scanner):
        """Test that images are correctly detected and analyzed."""
        html = """
        <img src="logo.png" alt="Logo">
        <img src="decorative.jpg" alt="">
        <img src="bad.png">
        """
        issues, images, _ = code_scanner._scan_html(html, "test.html")

        assert len(images) == 3
        assert images[0].has_alt is True
        assert images[1].is_decorative is True
        assert images[2].has_alt is False

        # Should have one image-alt issue
        assert any(i.rule == "image-alt" for i in issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
