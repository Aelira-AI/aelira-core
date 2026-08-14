"""Tests for reading order auto-fix heuristic strategy."""

import os
import tempfile

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.remediation.reading_order import (
    ContentBlock,
    HeuristicStrategy,
    LayoutType,
    ReadingOrderFixResult,
    get_reading_order_strategy,
)
from src.education.remediation.confidence import ConfidenceCalculator, FixMethod

pytestmark = pytest.mark.unit


# ===================================================================
# Tests
# ===================================================================


class TestContentBlockExtraction:
    """Test content block extraction from PDFs."""

    def test_extract_blocks_empty_pdf(self):
        """Empty PDF should return no blocks."""
        fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf = pikepdf.new()
            page = pikepdf.Page(
                pdf.make_indirect(
                    Dictionary(
                        {"/Type": Name.Page, "/MediaBox": Array([0, 0, 612, 792])}
                    )
                )
            )
            pdf.pages.append(page)
            pdf.save(f.name)

            try:
                doc = fitz.open(f.name)
                strategy = HeuristicStrategy()
                blocks = strategy._extract_blocks(doc)
                doc.close()
                assert len(blocks) == 0
            finally:
                os.unlink(f.name)


class TestLayoutDetection:
    """Test column layout detection."""

    def test_single_column_detection(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Title", 0),
            ContentBlock(1, (72, 100, 540, 120), "Paragraph 1", 0),
            ContentBlock(2, (72, 150, 540, 170), "Paragraph 2", 0),
        ]
        assert strategy._detect_layout(blocks) == LayoutType.SINGLE_COLUMN

    def test_two_column_detection(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 280, 70), "Left 1", 0),
            ContentBlock(1, (72, 80, 280, 100), "Left 2", 0),
            ContentBlock(2, (320, 50, 540, 70), "Right 1", 0),
            ContentBlock(3, (320, 80, 540, 100), "Right 2", 0),
        ]
        assert strategy._detect_layout(blocks) == LayoutType.TWO_COLUMN

    def test_single_block_is_single_column(self):
        strategy = HeuristicStrategy()
        blocks = [ContentBlock(0, (72, 50, 540, 70), "Only block", 0)]
        assert strategy._detect_layout(blocks) == LayoutType.SINGLE_COLUMN

    def test_blocks_with_small_x_gap_are_single_column(self):
        """Blocks with slight X offset should still be single column."""
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Line 1", 0),
            ContentBlock(1, (75, 100, 540, 120), "Line 2 (slight indent)", 0),
            ContentBlock(2, (72, 150, 540, 170), "Line 3", 0),
        ]
        assert strategy._detect_layout(blocks) == LayoutType.SINGLE_COLUMN


class TestReadingOrderComputation:
    """Test reading order computation for different layouts."""

    def test_single_column_top_to_bottom(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 200, 540, 220), "Second", 0),
            ContentBlock(1, (72, 50, 540, 70), "First", 0),
            ContentBlock(2, (72, 350, 540, 370), "Third", 0),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.SINGLE_COLUMN)
        assert order == [1, 0, 2]

    def test_two_column_left_then_right(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (320, 50, 540, 70), "Right 1", 0),
            ContentBlock(1, (72, 50, 280, 70), "Left 1", 0),
            ContentBlock(2, (320, 100, 540, 120), "Right 2", 0),
            ContentBlock(3, (72, 100, 280, 120), "Left 2", 0),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.TWO_COLUMN)
        # Expected: left top-to-bottom (1, 3) then right top-to-bottom (0, 2)
        assert order == [1, 3, 0, 2]

    def test_headers_before_content(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 200, 540, 220), "Content", 0),
            ContentBlock(1, (72, 20, 540, 35), "Header", 0, is_header=True),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.SINGLE_COLUMN)
        assert order[0] == 1  # Header comes first

    def test_footers_after_content(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 750, 540, 770), "Footer", 0, is_footer=True),
            ContentBlock(1, (72, 200, 540, 220), "Content", 0),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.SINGLE_COLUMN)
        assert order[-1] == 0  # Footer comes last

    def test_multi_page_single_column(self):
        """Blocks across pages should be ordered by page then Y."""
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 300, 540, 320), "Page 0 bottom", 0),
            ContentBlock(1, (72, 50, 540, 70), "Page 1 top", 1),
            ContentBlock(2, (72, 50, 540, 70), "Page 0 top", 0),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.SINGLE_COLUMN)
        # Page 0 top, Page 0 bottom, Page 1 top
        assert order == [2, 0, 1]

    def test_two_column_multi_page(self):
        """Two-column ordering should work across pages."""
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (320, 50, 540, 70), "P0 Right", 0),
            ContentBlock(1, (72, 50, 280, 70), "P0 Left", 0),
            ContentBlock(2, (72, 50, 280, 70), "P1 Left", 1),
            ContentBlock(3, (320, 50, 540, 70), "P1 Right", 1),
        ]
        order = strategy._compute_reading_order(blocks, LayoutType.TWO_COLUMN)
        # P0 left, P0 right, P1 left, P1 right
        assert order == [1, 0, 2, 3]


class TestHeaderFooterDetection:
    """Test header/footer pattern detection across pages."""

    def test_repeated_text_detected_as_header(self):
        strategy = HeuristicStrategy()
        blocks = []
        for page in range(4):
            blocks.append(
                ContentBlock(len(blocks), (72, 20, 540, 35), "university name", page)
            )
            blocks.append(
                ContentBlock(
                    len(blocks),
                    (72, 200, 540, 400),
                    f"Content page {page}",
                    page,
                )
            )
        strategy._detect_headers_footers(blocks)
        assert len([b for b in blocks if b.is_header]) == 4

    def test_non_repeated_text_not_marked(self):
        strategy = HeuristicStrategy()
        blocks = []
        for page in range(4):
            blocks.append(
                ContentBlock(
                    len(blocks),
                    (72, 20, 540, 35),
                    f"unique title {page}",
                    page,
                )
            )
            blocks.append(
                ContentBlock(
                    len(blocks),
                    (72, 200, 540, 400),
                    f"Content {page}",
                    page,
                )
            )
        strategy._detect_headers_footers(blocks)
        assert len([b for b in blocks if b.is_header]) == 0

    def test_page_number_detection_sequential(self):
        """Sequential page numbers across 3+ pages should be detected."""
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (290, 760, 320, 775), "1", 0),
            ContentBlock(1, (72, 200, 540, 400), "Content", 0),
            ContentBlock(2, (290, 760, 320, 775), "2", 1),
            ContentBlock(3, (72, 200, 540, 400), "Content 2", 1),
            ContentBlock(4, (290, 760, 320, 775), "3", 2),
            ContentBlock(5, (72, 200, 540, 400), "Content 3", 2),
        ]
        strategy._detect_headers_footers(blocks)
        assert len([b for b in blocks if b.is_page_number]) == 3

    def test_non_sequential_numbers_not_page_numbers(self):
        """Non-sequential numbers in margins should NOT be marked as page numbers."""
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (290, 760, 320, 775), "5", 0),
            ContentBlock(1, (72, 200, 540, 400), "Content", 0),
            ContentBlock(2, (290, 760, 320, 775), "12", 1),
            ContentBlock(3, (72, 200, 540, 400), "Content 2", 1),
            ContentBlock(4, (290, 760, 320, 775), "3", 2),
            ContentBlock(5, (72, 200, 540, 400), "Content 3", 2),
        ]
        strategy._detect_headers_footers(blocks)
        assert len([b for b in blocks if b.is_page_number]) == 0

    def test_few_pages_skips_detection(self):
        strategy = HeuristicStrategy()
        blocks = [
            ContentBlock(0, (72, 20, 540, 35), "university name", 0),
            ContentBlock(1, (72, 200, 540, 400), "Content", 0),
            ContentBlock(2, (72, 20, 540, 35), "university name", 1),
            ContentBlock(3, (72, 200, 540, 400), "Content 2", 1),
        ]
        strategy._detect_headers_footers(blocks)
        # Only 2 pages, below MIN_PAGES_FOR_HEADER_DETECTION (3)
        assert len([b for b in blocks if b.is_header]) == 0

    def test_footer_detection_repeated(self):
        """Repeated text in the footer zone should be detected."""
        strategy = HeuristicStrategy()
        blocks = []
        for page in range(4):
            blocks.append(
                ContentBlock(
                    len(blocks),
                    (72, 200, 540, 400),
                    f"Content page {page}",
                    page,
                )
            )
            blocks.append(
                ContentBlock(
                    len(blocks),
                    (72, 760, 540, 780),
                    "confidential",
                    page,
                )
            )
        strategy._detect_headers_footers(blocks)
        assert len([b for b in blocks if b.is_footer]) == 4


class TestStrategySelection:
    """Test strategy selection logic."""

    def test_single_column_selects_heuristic(self):
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Line 1", 0),
            ContentBlock(1, (72, 100, 540, 120), "Line 2", 0),
            ContentBlock(2, (72, 150, 540, 170), "Line 3", 0),
        ]
        assert get_reading_order_strategy(blocks) == "heuristic"

    def test_empty_blocks_selects_heuristic(self):
        assert get_reading_order_strategy([]) == "heuristic"


class TestReadingOrderFixResult:
    """Test the fix result dataclass."""

    def test_result_defaults(self):
        result = ReadingOrderFixResult(success=True)
        assert result.reordered_count == 0
        assert result.artifacts_marked == 0
        assert result.layout_type == LayoutType.SINGLE_COLUMN
        assert result.confidence == 0.0
        assert result.needs_review is True
        assert result.error is None

    def test_failed_result(self):
        result = ReadingOrderFixResult(success=False, error="Test error")
        assert not result.success
        assert result.error == "Test error"

    def test_successful_result_with_data(self):
        result = ReadingOrderFixResult(
            success=True,
            reordered_count=5,
            artifacts_marked=2,
            layout_type=LayoutType.TWO_COLUMN,
            confidence=0.78,
            needs_review=True,
            original_order=[0, 1, 2, 3, 4],
            new_order=[1, 3, 0, 2, 4],
        )
        assert result.success
        assert result.reordered_count == 5
        assert result.artifacts_marked == 2
        assert result.layout_type == LayoutType.TWO_COLUMN
        assert result.confidence == 0.78


class TestConfidenceScoring:
    """Test confidence scoring for reading order fixes."""

    def test_single_column_high_confidence(self):
        """Single column should produce confidence in 0.80-0.90 range."""
        calc = ConfidenceCalculator()
        # Uses signal=0.9, context=0.9 for single column
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.9, context_quality=0.9
        )
        assert 0.80 <= confidence <= 0.90

    def test_two_column_moderate_confidence(self):
        """Two column should produce confidence in 0.70-0.80 range."""
        calc = ConfidenceCalculator()
        # Uses signal=0.6, context=0.7 for two column
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.6, context_quality=0.7
        )
        assert 0.70 <= confidence <= 0.85

    def test_complex_layout_lower_confidence(self):
        calc = ConfidenceCalculator()
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.3, context_quality=0.7
        )
        assert 0.55 <= confidence <= 0.80

    def test_heuristic_needs_review_below_threshold(self):
        calc = ConfidenceCalculator()
        # Low signals should produce confidence below review threshold
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.3, context_quality=0.3
        )
        assert calc.needs_review(confidence)


class TestHeuristicStrategyIntegration:
    """Integration tests for the full HeuristicStrategy.fix() pipeline."""

    def test_fix_empty_pdf(self):
        """Fix on empty PDF should succeed with no changes."""
        pytest.importorskip("fitz", reason="PyMuPDF not installed")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf = pikepdf.new()
            page = pikepdf.Page(
                pdf.make_indirect(
                    Dictionary(
                        {"/Type": Name.Page, "/MediaBox": Array([0, 0, 612, 792])}
                    )
                )
            )
            pdf.pages.append(page)
            pdf.save(f.name)

            try:
                strategy = HeuristicStrategy()
                result = strategy.fix(f.name)
                assert result.success
                assert result.reordered_count == 0
            finally:
                os.unlink(f.name)
