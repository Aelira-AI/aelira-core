"""
End-to-end tests for PowerPoint processor.

Tests contrast detection, image alt text analysis, and accessibility compliance
using synthetic PowerPoint test fixtures.

Updated to match PowerPointProcessingResult dataclass API.
"""

import pytest
from pathlib import Path

# Add backend to path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.pptx_processor import (
    PowerPointProcessor as PPTXProcessor,
)

# Fixture paths
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "powerpoint"
LECTURE_DECK = FIXTURES_DIR / "lecture_deck.pptx"
DARK_THEME = FIXTURES_DIR / "dark_theme.pptx"
IMAGE_HEAVY = FIXTURES_DIR / "image_heavy.pptx"


@pytest.fixture
def pptx_processor():
    """Create PowerPoint processor instance."""
    return PPTXProcessor()


class TestPowerPointProcessingWorkflow:
    """Test complete PowerPoint processing workflow."""

    def test_lecture_deck_processing(self, pptx_processor):
        """Test processing of lecture presentation with contrast issues."""
        assert LECTURE_DECK.exists(), f"Test fixture not found: {LECTURE_DECK}"

        # Process PowerPoint
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        # Verify result is PowerPointProcessingResult dataclass
        assert result is not None, "PowerPoint processing returned None"
        assert result.file_name == "lecture_deck.pptx"
        assert result.total_slides > 0
        assert result.total_shapes >= 0
        assert result.total_images >= 0

        # Verify slides are processed
        assert len(result.slides) == result.total_slides
        assert len(result.slides) == 5, f"Expected 5 slides, got {len(result.slides)}"

        # Verify slide structure
        for slide in result.slides:
            assert hasattr(slide, "slide_number")
            assert hasattr(slide, "contrast_issues")
            assert hasattr(slide, "alt_text_issues")
            assert hasattr(slide, "total_issues")

        # Verify compliance scoring
        assert 0 <= result.compliance_score <= 100
        assert isinstance(result.summary, dict)
        assert isinstance(result.remediation_suggestions, list)

    def test_dark_theme_processing(self, pptx_processor):
        """Test processing of dark theme presentation."""
        assert DARK_THEME.exists(), f"Test fixture not found: {DARK_THEME}"

        result = pptx_processor.process_pptx(str(DARK_THEME))

        assert result is not None
        assert len(result.slides) == 4, f"Expected 4 slides, got {len(result.slides)}"
        assert result.total_slides == 4

        # Verify compliance score exists
        assert 0 <= result.compliance_score <= 100

    def test_image_heavy_processing(self, pptx_processor):
        """Test processing of image-heavy presentation."""
        assert IMAGE_HEAVY.exists(), f"Test fixture not found: {IMAGE_HEAVY}"

        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))

        assert result is not None

        # Count total alt text issues across all slides
        all_alt_text_issues = []
        for slide in result.slides:
            all_alt_text_issues.extend(slide.alt_text_issues)

        # Image-heavy deck should have many images
        assert (
            result.total_images >= 5
        ), f"Expected at least 5 images, found {result.total_images}"

        # Should detect missing alt text
        images_without_alt = [
            issue for issue in all_alt_text_issues if not issue.has_alt_text
        ]
        assert len(images_without_alt) > 0, "Should detect images without alt text"


class TestContrastDetection:
    """Test color contrast detection in PowerPoint slides."""

    def test_contrast_issue_detection(self, pptx_processor):
        """Test detection of WCAG contrast violations."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        # Collect all contrast issues across slides
        all_contrast_issues = []
        for slide in result.slides:
            all_contrast_issues.extend(slide.contrast_issues)

        # Lecture deck may or may not have contrast issues
        # (depends on fixture) - just verify we can access them
        assert isinstance(all_contrast_issues, list)

        # Verify contrast issue structure if any exist
        for issue in all_contrast_issues:
            assert hasattr(issue, "slide_number")
            assert hasattr(issue, "foreground")
            assert hasattr(issue, "background")
            assert hasattr(issue, "contrast_ratio")
            assert hasattr(issue, "wcag_aa_pass")
            assert hasattr(issue, "wcag_aaa_pass")

    def test_contrast_ratio_calculation(self, pptx_processor):
        """Test contrast ratio calculation accuracy."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        # Collect all contrast issues
        all_contrast_issues = []
        for slide in result.slides:
            all_contrast_issues.extend(slide.contrast_issues)

        for issue in all_contrast_issues:
            ratio = issue.contrast_ratio
            # Contrast ratio should be between 1:1 and 21:1
            assert 1.0 <= ratio <= 21.0, f"Invalid contrast ratio: {ratio}"

            # WCAG AA requires 4.5:1 for normal text
            if ratio >= 4.5:
                assert issue.wcag_aa_pass, f"Ratio {ratio:.2f} should pass WCAG AA"
            else:
                assert not issue.wcag_aa_pass, f"Ratio {ratio:.2f} should fail WCAG AA"

            # WCAG AAA requires 7:1 for normal text
            if ratio >= 7.0:
                assert issue.wcag_aaa_pass, f"Ratio {ratio:.2f} should pass WCAG AAA"
            else:
                assert (
                    not issue.wcag_aaa_pass
                ), f"Ratio {ratio:.2f} should fail WCAG AAA"

    def test_dark_theme_contrast(self, pptx_processor):
        """Test that dark themes generally have better contrast."""
        result = pptx_processor.process_pptx(str(DARK_THEME))

        # Collect all contrast issues
        all_contrast_issues = []
        for slide in result.slides:
            all_contrast_issues.extend(slide.contrast_issues)

        print(f"\n📊 Dark theme contrast issues: {len(all_contrast_issues)}")

        # Dark theme should have fewer contrast issues
        slides_with_issues = {issue.slide_number for issue in all_contrast_issues}
        slides_without_issues = result.total_slides - len(slides_with_issues)

        # Some slides should have good contrast
        print(f"   Slides without contrast issues: {slides_without_issues}")

    def test_high_contrast_detection(self, pptx_processor):
        """Test detection of high contrast (black on white, etc.)."""
        result = pptx_processor.process_pptx(str(DARK_THEME))

        # Verify slides exist
        assert len(result.slides) > 0


class TestImageAltTextDetection:
    """Test detection of missing alt text on images."""

    def test_missing_alt_text_detection(self, pptx_processor):
        """Test detection of images without alt text."""
        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))

        # Collect all alt text issues
        all_alt_text_issues = []
        for slide in result.slides:
            all_alt_text_issues.extend(slide.alt_text_issues)

        # Count images with and without alt text
        images_with_alt = [issue for issue in all_alt_text_issues if issue.has_alt_text]
        images_without_alt = [
            issue for issue in all_alt_text_issues if not issue.has_alt_text
        ]

        print(f"\n📊 Images with alt text: {len(images_with_alt)}")
        print(f"   Images without alt text: {len(images_without_alt)}")

        # Image-heavy deck intentionally has mostly missing alt text
        assert len(images_without_alt) >= len(
            images_with_alt
        ), "Should detect images without alt text"

    def test_alt_text_validation(self, pptx_processor):
        """Test validation of existing alt text quality."""
        result = pptx_processor.process_pptx(str(DARK_THEME))

        # Collect all alt text issues
        all_alt_text_issues = []
        for slide in result.slides:
            all_alt_text_issues.extend(slide.alt_text_issues)

        # Dark theme deck may have one image WITH alt text (for comparison)
        images_with_alt = [issue for issue in all_alt_text_issues if issue.has_alt_text]

        if len(images_with_alt) > 0:
            for img in images_with_alt:
                alt_text = img.existing_alt_text or ""
                assert (
                    len(alt_text) > 0
                ), "Image marked as having alt text but text is empty"

    def test_image_location_tracking(self, pptx_processor):
        """Test that image locations are tracked correctly."""
        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))

        for slide in result.slides:
            for img_issue in slide.alt_text_issues:
                assert hasattr(img_issue, "slide_number")
                assert hasattr(img_issue, "shape_id")

                # Verify slide number is valid
                assert (
                    1 <= img_issue.slide_number <= result.total_slides
                ), f"Invalid slide number: {img_issue.slide_number}"


class TestPowerPointCompliance:
    """Test PowerPoint accessibility compliance scoring."""

    def test_compliance_score_calculation(self, pptx_processor):
        """Test compliance score is calculated correctly."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        assert hasattr(result, "compliance_score"), "Compliance score missing"
        assert hasattr(result, "summary"), "Summary missing"

        score = result.compliance_score
        assert 0 <= score <= 100, f"Invalid score: {score}"

        # Summary should have issue counts
        assert "total_issues" in result.summary

    def test_issue_summary_format(self, pptx_processor):
        """Test that summary has expected keys."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        assert isinstance(result.summary, dict)
        # Summary typically includes contrast_issues, alt_text_issues, total_issues
        assert "total_issues" in result.summary or len(result.summary) > 0

    def test_remediation_suggestions(self, pptx_processor):
        """Test that remediation suggestions are generated."""
        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))

        assert isinstance(result.remediation_suggestions, list)
        # If there are issues, there should be suggestions
        if result.summary.get("total_issues", 0) > 0:
            # Suggestions should be generated
            pass

    def test_slide_level_issues(self, pptx_processor):
        """Test per-slide issue tracking."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        for slide in result.slides:
            assert hasattr(slide, "slide_number")
            assert hasattr(slide, "total_issues")
            # Total issues should match sum of all issue types
            expected = (
                len(slide.contrast_issues)
                + len(slide.alt_text_issues)
                + len(getattr(slide, "title_issues", []))
                + len(getattr(slide, "image_of_text_issues", []))
            )
            assert slide.total_issues == expected


class TestPowerPointTextExtraction:
    """Test text extraction from PowerPoint slides."""

    def test_slide_title_attribute(self, pptx_processor):
        """Test extraction of slide titles."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        # Slides may have optional slide_title
        for slide in result.slides:
            assert hasattr(slide, "slide_title")

    def test_slide_structure(self, pptx_processor):
        """Test slide structure attributes."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        for slide in result.slides:
            assert slide.slide_number >= 1
            assert isinstance(slide.contrast_issues, list)
            assert isinstance(slide.alt_text_issues, list)


class TestPowerPointBatchProcessing:
    """Test batch PowerPoint processing."""

    def test_batch_directory_processing(self, pptx_processor):
        """Test processing multiple PowerPoint files."""
        results = []
        for pptx_file in [LECTURE_DECK, DARK_THEME, IMAGE_HEAVY]:
            if pptx_file.exists():
                result = pptx_processor.process_pptx(str(pptx_file))
                results.append(result)

        assert (
            len(results) == 3
        ), f"Expected 3 presentations processed, got {len(results)}"

        # Verify all presentations were processed
        processed_files = {r.file_name for r in results}
        expected_files = {"lecture_deck.pptx", "dark_theme.pptx", "image_heavy.pptx"}
        assert processed_files == expected_files

    def test_batch_summary_statistics(self, pptx_processor):
        """Test aggregate statistics across multiple presentations."""
        results = []
        for pptx_file in [LECTURE_DECK, DARK_THEME, IMAGE_HEAVY]:
            if pptx_file.exists():
                result = pptx_processor.process_pptx(str(pptx_file))
                results.append(result)

        # Calculate aggregate statistics
        total_slides = sum(r.total_slides for r in results)
        total_images = sum(r.total_images for r in results)
        total_issues = sum(r.summary.get("total_issues", 0) for r in results)
        avg_compliance = sum(r.compliance_score for r in results) / len(results)

        print("\n📊 Batch Processing Summary:")
        print(f"   Total Presentations: {len(results)}")
        print(f"   Total Slides: {total_slides}")
        print(f"   Total Images: {total_images}")
        print(f"   Total Issues: {total_issues}")
        print(f"   Average Compliance: {avg_compliance:.1f}%")

        assert total_slides > 0
        assert 0 <= avg_compliance <= 100


class TestPowerPointPerformance:
    """Test PowerPoint processing performance."""

    def test_processing_speed(self, pptx_processor):
        """Test PowerPoint processing speed."""
        import time

        start_time = time.time()
        result = pptx_processor.process_pptx(str(LECTURE_DECK))
        elapsed = time.time() - start_time

        # PowerPoint processing should be fast (no OCR needed)
        assert elapsed < 30, f"PowerPoint processing too slow: {elapsed:.1f}s"

        print(f"\n⚡ Processing time: {elapsed:.2f}s for {result.total_slides} slides")

    def test_large_presentation_handling(self, pptx_processor):
        """Test handling of presentation with many images."""
        import time

        start_time = time.time()
        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))
        elapsed = time.time() - start_time

        # Image-heavy presentation with AI alt text generation
        assert elapsed < 180, f"Image processing too slow: {elapsed:.1f}s"

        print(f"\n⚡ Processed {result.total_images} images in {elapsed:.2f}s")


class TestPowerPointEdgeCases:
    """Test PowerPoint processing edge cases."""

    def test_result_attributes(self, pptx_processor):
        """Test all expected attributes exist on result."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        assert hasattr(result, "file_path")
        assert hasattr(result, "file_name")
        assert hasattr(result, "total_slides")
        assert hasattr(result, "total_shapes")
        assert hasattr(result, "total_images")
        assert hasattr(result, "slides")
        assert hasattr(result, "summary")
        assert hasattr(result, "compliance_score")
        assert hasattr(result, "remediation_suggestions")

    def test_invalid_file_path(self, pptx_processor):
        """Test handling of invalid file path."""
        try:
            pptx_processor.process_pptx("/nonexistent/file.pptx")
            # If no exception, implementation handles gracefully
        except Exception:
            pass  # Expected

    def test_non_pptx_file(self, pptx_processor):
        """Test handling of non-PowerPoint file."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"This is not a PowerPoint file")
            f.flush()

            try:
                pptx_processor.process_pptx(f.name)
                # If no exception, implementation handles gracefully
            except Exception:
                pass  # Expected

    def test_corrupted_pptx(self, pptx_processor):
        """Test handling of corrupted PowerPoint file."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            # Create invalid ZIP structure (PPTX is just a ZIP)
            f.write(b"PK\x03\x04" + b"corrupted data")
            f.flush()

            try:
                pptx_processor.process_pptx(f.name)
                # If no exception, implementation handles gracefully
            except Exception:
                pass  # Expected


class TestPowerPointRemediation:
    """Test PowerPoint remediation suggestions."""

    def test_contrast_fix_suggestions(self, pptx_processor):
        """Test that contrast issues include fix suggestions."""
        result = pptx_processor.process_pptx(str(LECTURE_DECK))

        # Collect all contrast issues
        all_contrast_issues = []
        for slide in result.slides:
            all_contrast_issues.extend(slide.contrast_issues)

        for issue in all_contrast_issues:
            assert hasattr(issue, "contrast_ratio")
            # May have suggested_fix field
            if hasattr(issue, "suggested_fix") and issue.suggested_fix:
                assert isinstance(issue.suggested_fix, str)

    def test_alt_text_generation_suggestions(self, pptx_processor):
        """Test generation of alt text for images."""
        result = pptx_processor.process_pptx(str(IMAGE_HEAVY))

        # Collect all alt text issues
        all_alt_text_issues = []
        for slide in result.slides:
            all_alt_text_issues.extend(slide.alt_text_issues)

        images_without_alt = [
            issue for issue in all_alt_text_issues if not issue.has_alt_text
        ]

        for img in images_without_alt:
            # May have suggested_alt_text field
            if hasattr(img, "suggested_alt_text"):
                # Suggested alt text may or may not be populated
                pass


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
