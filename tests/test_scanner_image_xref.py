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
    """_check_images scan-only mode should emit PDFImageIssue with image_xref.

    We call _check_images directly with image_generator=None to force
    scan_only=True.  This path is used by the remediator to discover
    image xrefs for later vision AI extraction.
    """
    from src.education.pdf_processor import PDFProcessor

    # Create processor without AI — image_generator will be None
    processor = PDFProcessor(generate_alt_text=False, validate_alt_text=False)
    assert processor.image_generator is None, "Expected no image generator"

    # Call _check_images directly (scan-only mode)
    issues = processor._check_images(str(_IMAGE_FIXTURE), {})

    assert len(issues) > 0, "Test PDF should have at least one image without alt text"
    for issue in issues:
        assert issue.image_xref is not None, (
            f"image_xref should not be None for scan-only issue: {issue}"
        )
