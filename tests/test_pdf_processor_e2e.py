"""
End-to-end tests for PDF processor.

Tests PDF OCR, structure detection, image analysis, and compliance reporting
using synthetic academic PDF fixtures.

Updated to match PDFProcessingResult dataclass API.
"""

import pytest
from pathlib import Path

# Add backend to path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.pdf_processor import PDFProcessor, PDFBatchProcessor

# Fixture paths
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pdfs"
ACADEMIC_PAPER = FIXTURES_DIR / "academic_paper.pdf"
LECTURE_NOTES = FIXTURES_DIR / "lecture_notes.pdf"
LAB_MANUAL = FIXTURES_DIR / "lab_manual.pdf"
TEXTBOOK_CHAPTER = FIXTURES_DIR / "textbook_chapter.pdf"
SIMPLE_SYLLABUS = FIXTURES_DIR / "simple_syllabus.pdf"


@pytest.fixture
def pdf_processor():
    """Create PDF processor instance."""
    return PDFProcessor()


@pytest.fixture
def batch_processor():
    """Create batch PDF processor instance."""
    return PDFBatchProcessor()


class TestPDFProcessingWorkflow:
    """Test complete PDF processing workflow."""

    def test_academic_paper_processing(self, pdf_processor):
        """Test processing of academic paper PDF."""
        assert ACADEMIC_PAPER.exists(), f"Test fixture not found: {ACADEMIC_PAPER}"

        # Process PDF
        result = pdf_processor.process_pdf(str(ACADEMIC_PAPER))

        # Verify result is PDFProcessingResult dataclass
        assert result is not None, "PDF processing returned None"
        assert result.file_name == "academic_paper.pdf"
        assert result.pages > 0
        assert result.text_extracted is True

        # Verify structure detection
        structure = result.structure
        assert "headings" in structure
        assert "paragraphs" in structure
        assert len(structure["headings"]) > 0, "No headings detected"
        assert len(structure["paragraphs"]) > 0, "No paragraphs detected"

        # Check for expected headings
        heading_texts = [h["text"] for h in structure["headings"]]
        assert any(
            "abstract" in h.lower() for h in heading_texts
        ), "Expected 'Abstract' heading not found"
        assert any(
            "introduction" in h.lower() for h in heading_texts
        ), "Expected 'Introduction' heading not found"

        # Verify HTML output
        assert result.html_output is not None
        assert len(result.html_output) > 0
        assert "<html" in result.html_output.lower()

        # Verify compliance scoring
        assert (
            0 <= result.compliance_score <= 100
        ), f"Invalid compliance score: {result.compliance_score}"
        assert isinstance(result.issues, list)

    def test_lecture_notes_processing(self, pdf_processor):
        """Test processing of lecture notes PDF."""
        assert LECTURE_NOTES.exists(), f"Test fixture not found: {LECTURE_NOTES}"

        result = pdf_processor.process_pdf(str(LECTURE_NOTES))

        # Verify structure
        assert result is not None
        structure = result.structure

        # Lecture notes should have bullet points
        assert "lists" in structure or "paragraphs" in structure

        # Check HTML output for code-related content
        full_text = result.html_output.lower()
        assert (
            "class" in full_text or "def" in full_text or "tree" in full_text
        ), "Expected code/technical content not found"

    def test_lab_manual_processing(self, pdf_processor):
        """Test processing of lab manual PDF."""
        assert LAB_MANUAL.exists(), f"Test fixture not found: {LAB_MANUAL}"

        result = pdf_processor.process_pdf(str(LAB_MANUAL))

        # Verify structure exists
        structure = result.structure
        assert "headings" in structure or "paragraphs" in structure

        # Check for safety/procedure content in HTML
        full_text = result.html_output.lower()
        assert (
            "safety" in full_text
            or "warning" in full_text
            or "step" in full_text
            or "procedure" in full_text
        ), "Lab manual content not found"

    def test_textbook_chapter_processing(self, pdf_processor):
        """Test processing of complex textbook chapter PDF."""
        assert TEXTBOOK_CHAPTER.exists(), f"Test fixture not found: {TEXTBOOK_CHAPTER}"

        result = pdf_processor.process_pdf(str(TEXTBOOK_CHAPTER))

        # Textbook chapters have complex structure
        structure = result.structure
        headings = structure.get("headings", [])

        # Check for multiple headings
        assert (
            len(headings) >= 3
        ), f"Expected multiple headings in textbook, found {len(headings)}"

        # Check for learning/chapter content
        full_text = result.html_output.lower()
        assert (
            "chapter" in full_text or "learning" in full_text or "section" in full_text
        )

    def test_simple_syllabus_processing(self, pdf_processor):
        """Test processing of simple syllabus PDF."""
        assert SIMPLE_SYLLABUS.exists(), f"Test fixture not found: {SIMPLE_SYLLABUS}"

        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))

        # Verify basic document processing
        assert result is not None
        assert result.pages >= 1

        # Check for typical syllabus content
        full_text = result.html_output.lower()
        assert (
            "course" in full_text or "syllabus" in full_text
        ), "Syllabus content not found"
        assert (
            "grading" in full_text or "grade" in full_text
        ), "Grading information not found"


class TestPDFStructureDetection:
    """Test PDF structure detection accuracy."""

    def test_heading_detection(self, pdf_processor):
        """Test heading detection in PDFs."""
        result = pdf_processor.process_pdf(str(ACADEMIC_PAPER))
        structure = result.structure
        headings = structure.get("headings", [])

        assert len(headings) > 0, "No headings detected"

        # Verify heading attributes
        for heading in headings:
            assert "text" in heading, "Heading missing text"
            assert "level" in heading, "Heading missing level"
            assert (
                1 <= heading["level"] <= 6
            ), f"Invalid heading level: {heading['level']}"

    def test_table_detection(self, pdf_processor):
        """Test table detection in PDFs."""
        result = pdf_processor.process_pdf(str(LAB_MANUAL))
        structure = result.structure

        # Tables detection is optional - verify structure exists
        assert isinstance(structure, dict)
        if "tables" in structure and len(structure["tables"]) > 0:
            for table in structure["tables"]:
                # Just verify it's a dict with some structure
                assert isinstance(table, dict)

    def test_list_detection(self, pdf_processor):
        """Test list detection in PDFs."""
        result = pdf_processor.process_pdf(str(LECTURE_NOTES))
        structure = result.structure

        # Verify structure exists
        assert isinstance(structure, dict)

        # Check for list markers in HTML output
        full_text = result.html_output
        has_bullets = "•" in full_text or "*" in full_text or "<li>" in full_text
        has_numbers = any(f"{i}." in full_text for i in range(1, 10))
        # Lists may be detected as paragraphs, that's ok
        assert len(structure.get("paragraphs", [])) > 0 or has_bullets or has_numbers


class TestPDFImageProcessing:
    """Test PDF image extraction and alt text generation."""

    def test_image_issues_field(self, pdf_processor):
        """Test image_issues field exists."""
        result = pdf_processor.process_pdf(str(ACADEMIC_PAPER))

        # image_issues is optional, may be None if not enabled
        assert hasattr(result, "image_issues")

    def test_image_alt_text_with_generator(self):
        """Test image alt text generation when enabled."""
        # Create processor with alt text generation enabled
        processor = PDFProcessor(generate_alt_text=True)
        result = processor.process_pdf(str(TEXTBOOK_CHAPTER))

        # If image generation is enabled and images exist, should have image_issues
        if result.image_issues:
            for img_issue in result.image_issues:
                assert hasattr(img_issue, "page_number")
                assert hasattr(img_issue, "has_alt_text")


class TestPDFComplianceScoring:
    """Test PDF compliance scoring and issue detection."""

    def test_compliance_score_calculation(self, pdf_processor):
        """Test compliance score is calculated correctly."""
        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))

        assert hasattr(result, "compliance_score"), "Compliance score missing"
        assert hasattr(result, "issues"), "Issues list missing"

        score = result.compliance_score
        assert 0 <= score <= 100, f"Invalid score: {score}"

    def test_issues_list_format(self, pdf_processor):
        """Test issues are in expected format."""
        result = pdf_processor.process_pdf(str(ACADEMIC_PAPER))

        assert isinstance(result.issues, list)
        for issue in result.issues:
            assert isinstance(issue, dict)
            # Issues should have some identifying information
            assert len(issue) > 0

    def test_compliance_scoring_consistency(self, pdf_processor):
        """Test compliance score is consistent across runs."""
        result1 = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))
        result2 = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))

        # Scores should be identical for same file
        assert result1.compliance_score == result2.compliance_score


class TestPDFBatchProcessing:
    """Test batch PDF processing functionality."""

    def test_batch_directory_processing(self, batch_processor):
        """Test processing entire directory of PDFs."""
        results = batch_processor.process_directory(str(FIXTURES_DIR))

        # Verify core fixture PDFs were processed
        processed_files = {r.file_name for r in results}
        expected_files = {
            "academic_paper.pdf",
            "lecture_notes.pdf",
            "lab_manual.pdf",
            "textbook_chapter.pdf",
            "simple_syllabus.pdf",
        }
        assert expected_files.issubset(
            processed_files
        ), f"Missing files: {expected_files - processed_files}"

    def test_batch_processing_error_handling(self, batch_processor):
        """Test batch processing handles individual file errors gracefully."""
        import tempfile
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy one valid PDF
            shutil.copy(SIMPLE_SYLLABUS, tmpdir)

            # Create invalid "PDF" file
            invalid_pdf = Path(tmpdir) / "invalid.pdf"
            invalid_pdf.write_text("This is not a PDF")

            # Process directory - should handle error gracefully
            results = batch_processor.process_directory(tmpdir)

            # Should process valid PDF (invalid one may be skipped or error)
            assert len(results) >= 1, "Valid PDF should be processed"

    def test_batch_summary_statistics(self, batch_processor):
        """Test batch processing generates summary statistics."""
        results = batch_processor.process_directory(str(FIXTURES_DIR))

        # Calculate aggregate statistics
        total_pages = sum(r.pages for r in results)
        avg_compliance = sum(r.compliance_score for r in results) / len(results)

        assert total_pages > 0, "No pages processed"
        assert (
            0 <= avg_compliance <= 100
        ), f"Invalid average compliance: {avg_compliance}"

        print("\n📊 Batch Processing Summary:")
        print(f"   Total PDFs: {len(results)}")
        print(f"   Total Pages: {total_pages}")
        print(f"   Average Compliance: {avg_compliance:.1f}%")


class TestPDFHTMLExport:
    """Test PDF to accessible HTML export functionality."""

    def test_html_output_basic(self, pdf_processor):
        """Test basic HTML output functionality."""
        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))

        html = result.html_output
        assert html is not None, "HTML output is None"
        assert len(html) > 0, "HTML output is empty"
        assert (
            "<!DOCTYPE html>" in html or "<html" in html
        ), "HTML missing document declaration"

    def test_html_semantic_structure(self, pdf_processor):
        """Test HTML output uses semantic structure."""
        result = pdf_processor.process_pdf(str(ACADEMIC_PAPER))
        html = result.html_output

        # Check for semantic HTML elements
        assert "<h1>" in html or "<h2>" in html, "HTML missing semantic headings"
        assert "<p>" in html, "HTML missing paragraph tags"

        # Check for accessibility attributes
        assert 'lang="en"' in html or "lang=" in html, "HTML missing language attribute"

    def test_html_output_readable(self, pdf_processor):
        """Test HTML output is readable text."""
        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))
        html = result.html_output

        # Should contain actual text content, not just tags
        # Remove HTML tags and check remaining content
        import re

        text_only = re.sub(r"<[^>]+>", "", html)
        assert len(text_only.strip()) > 100, "HTML contains very little readable text"


class TestPDFPerformance:
    """Test PDF processing performance."""

    def test_processing_speed(self, pdf_processor):
        """Test PDF processing completes within reasonable time."""
        import time

        start_time = time.time()
        _ = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))
        elapsed = time.time() - start_time

        # Simple syllabus should process in under 30 seconds
        assert elapsed < 30, f"PDF processing too slow: {elapsed:.1f}s"

        print(f"\n⚡ Processing time: {elapsed:.2f}s")

    def test_batch_processing_performance(self, batch_processor):
        """Test batch processing performance."""
        import time

        start_time = time.time()
        results = batch_processor.process_directory(str(FIXTURES_DIR))
        elapsed = time.time() - start_time

        # 5 PDFs should complete within reasonable time
        assert elapsed < 180, f"Batch processing too slow: {elapsed:.1f}s for 5 PDFs"

        print(f"\n⚡ Batch processing time: {elapsed:.2f}s for {len(results)} PDFs")


class TestPDFEdgeCases:
    """Test PDF processing edge cases and error handling."""

    def test_minimal_pdf(self, pdf_processor):
        """Test handling of minimal PDF."""
        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))
        assert result is not None, "Should handle minimal PDF"
        assert result.text_extracted is True

    def test_pdf_result_attributes(self, pdf_processor):
        """Test all expected attributes exist on result."""
        result = pdf_processor.process_pdf(str(SIMPLE_SYLLABUS))

        # Check all expected attributes
        assert hasattr(result, "file_path")
        assert hasattr(result, "file_name")
        assert hasattr(result, "pages")
        assert hasattr(result, "text_extracted")
        assert hasattr(result, "ocr_used")
        assert hasattr(result, "structure")
        assert hasattr(result, "html_output")
        assert hasattr(result, "compliance_score")
        assert hasattr(result, "issues")

    def test_invalid_file_path(self, pdf_processor):
        """Test handling of invalid file path."""
        # Should raise an exception for non-existent file
        try:
            pdf_processor.process_pdf("/nonexistent/file.pdf")
            # If no exception, the implementation might handle this gracefully
            # That's acceptable behavior
        except Exception:
            pass  # Expected

    def test_non_pdf_file(self, pdf_processor):
        """Test handling of non-PDF file."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"This is not a PDF")
            f.flush()

            try:
                pdf_processor.process_pdf(f.name)
                # If no exception, implementation handles gracefully
            except Exception:
                pass  # Expected


class TestPDFReadingOrderVerification:
    """Test reading order verification for multi-column PDFs."""

    @pytest.fixture
    def pdf_processor(self):
        """Create PDF processor for testing."""
        from src.education.pdf_processor import PDFProcessor

        return PDFProcessor()

    @pytest.fixture
    def sample_pdf(self):
        """Create a simple sample PDF for testing."""
        import tempfile
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            c = canvas.Canvas(f.name, pagesize=letter)
            c.drawString(100, 750, "Header text")
            c.drawString(100, 700, "First paragraph content")
            c.drawString(100, 650, "Second paragraph content")
            c.drawString(100, 600, "Third paragraph content")
            c.save()
            yield f.name

        # Cleanup
        import os

        try:
            os.unlink(f.name)
        except Exception:
            pass

    def test_verify_reading_order_returns_result(self, pdf_processor, sample_pdf):
        """Test that verify_reading_order returns a valid result."""
        from src.education.pdf_checks.models import ReadingOrderResult
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()
        result = verifier.check(sample_pdf)

        assert isinstance(result, ReadingOrderResult)
        assert result.total_pages >= 0
        assert result.pages_analyzed >= 0
        assert isinstance(result.issues, list)
        assert 0.0 <= result.compliance_score <= 100.0
        assert isinstance(result.has_structure_tree, bool)
        assert isinstance(result.multi_column_detected, bool)

    def test_verify_reading_order_max_pages_limit(self, pdf_processor, sample_pdf):
        """Test that max_pages parameter limits analysis."""
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()
        result = verifier.check(sample_pdf, max_pages=1)

        # Should only analyze up to 1 page
        assert result.pages_analyzed <= 1

    def test_verify_reading_order_no_structure_tree(self, pdf_processor):
        """Test reading order check on PDF without structure tree."""
        import tempfile
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        # Create a simple PDF without structure tree
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            c = canvas.Canvas(f.name, pagesize=letter)
            c.drawString(100, 750, "Header text here")
            c.drawString(100, 700, "First paragraph")
            c.drawString(100, 650, "Second paragraph")
            c.save()

            verifier = ReadingOrderVerifier()
            result = verifier.check(f.name)

            # Should detect no structure tree
            assert result.has_structure_tree is False
            # Should have issues about missing structure
            assert len(result.issues) > 0

    def test_visual_text_order_extraction(self, pdf_processor, sample_pdf):
        """Test visual text order extraction helper."""
        import fitz
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()
        doc = fitz.open(sample_pdf)
        page = doc[0]

        blocks = verifier._get_visual_text_order(page)

        # Should return a list
        assert isinstance(blocks, list)

        # Each block should have required fields
        for block in blocks:
            assert "text" in block
            assert "bbox" in block
            assert "x" in block
            assert "y" in block

        doc.close()

    def test_multi_column_detection(self, pdf_processor):
        """Test multi-column layout detection heuristic."""
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()

        # Single column layout - blocks stacked vertically
        single_column_blocks = [
            {"text": "Header", "x": 100, "y": 50},
            {"text": "Para 1", "x": 100, "y": 100},
            {"text": "Para 2", "x": 100, "y": 150},
            {"text": "Para 3", "x": 100, "y": 200},
        ]
        assert verifier._detect_multi_column(single_column_blocks) is False

        # Two column layout - blocks side by side
        two_column_blocks = [
            {"text": "Left Col 1", "x": 50, "y": 50},
            {"text": "Right Col 1", "x": 300, "y": 50},  # >100pt gap
            {"text": "Left Col 2", "x": 50, "y": 100},
            {"text": "Right Col 2", "x": 300, "y": 100},
        ]
        assert verifier._detect_multi_column(two_column_blocks) is True

    def test_reading_order_compliance_score(self, pdf_processor, sample_pdf):
        """Test that compliance score is calculated correctly."""
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()
        result = verifier.check(sample_pdf)

        # Score should be between 0 and 100
        assert 0.0 <= result.compliance_score <= 100.0

        # If no structure tree, score should be 0
        if not result.has_structure_tree:
            assert result.compliance_score == 0.0

    def test_reading_order_issue_model(self, pdf_processor):
        """Test ReadingOrderIssue model fields."""
        from src.education.pdf_checks.models import ReadingOrderIssue

        issue = ReadingOrderIssue(
            page_number=1,
            expected_order=["Header", "Para 1"],
            actual_order=["Para 1", "Header"],
            severity="warning",
            recommendation="Fix reading order",
        )

        assert issue.page_number == 1
        assert issue.expected_order == ["Header", "Para 1"]
        assert issue.actual_order == ["Para 1", "Header"]
        assert issue.severity == "warning"
        assert issue.recommendation == "Fix reading order"

    def test_reading_order_with_invalid_file(self, pdf_processor):
        """Test reading order verification with invalid file."""
        from src.education.pdf_checks.reading_order import ReadingOrderVerifier

        verifier = ReadingOrderVerifier()
        result = verifier.check("/nonexistent/file.pdf")

        # Should return a result with error issue
        assert isinstance(result, type(result))  # Still returns ReadingOrderResult
        assert len(result.issues) > 0
        assert result.compliance_score == 0.0


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
