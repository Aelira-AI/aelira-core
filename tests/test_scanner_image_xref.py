"""Test that scanner passes image_xref in issue metadata."""

import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "pdfs"

# simple_syllabus.pdf has no images; academic_paper.pdf has one image without alt text.
_IMAGE_FIXTURE = FIXTURES / "academic_paper.pdf"


@pytest.mark.skipif(
    not _IMAGE_FIXTURE.exists(),
    reason="Test fixture not available",
)
def test_image_issues_include_xref():
    """ImageAccessibilityChecker scan-only mode should emit PDFImageIssue with image_xref.

    We call check() with no image_generator to force scan_only=True.
    This path is used by the remediator to discover image xrefs for
    later vision AI extraction.
    """
    from src.education.pdf_checks.image_checker import ImageAccessibilityChecker

    # Create checker without AI — image_generator will be None (scan-only mode)
    checker = ImageAccessibilityChecker(
        generate_alt_text=False, validate_alt_text=False
    )
    assert checker.image_generator is None, "Expected no image generator"

    # Call check (scan-only mode)
    issues = checker.check(str(_IMAGE_FIXTURE), {})

    assert len(issues) > 0, "Test PDF should have at least one image without alt text"
    for issue in issues:
        assert (
            issue.image_xref is not None
        ), f"image_xref should not be None for scan-only issue: {issue}"
