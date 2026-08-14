"""Tests for reading order AI vision strategy."""

import json
import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock

from src.education.remediation.reading_order import (
    ContentBlock,
    LayoutType,
    VisionStrategy,
    get_reading_order_strategy,
)
from src.education.remediation.confidence import ConfidenceCalculator, FixMethod

pytestmark = pytest.mark.unit


class TestVisionStrategyInit:
    """Test VisionStrategy initialization."""

    def test_creates_confidence_calculator(self):
        strategy = VisionStrategy()
        assert strategy._confidence_calc is not None

    def test_render_dpi_default(self):
        assert VisionStrategy.RENDER_DPI == 300

    def test_iou_threshold_default(self):
        assert VisionStrategy.IOU_THRESHOLD == 0.5


class TestVisionPrompt:
    """Test vision prompt generation."""

    def test_prompt_includes_block_count(self):
        strategy = VisionStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Block 1", 0),
            ContentBlock(1, (72, 100, 540, 120), "Block 2", 0),
            ContentBlock(2, (72, 150, 540, 170), "Block 3", 0),
        ]
        prompt = strategy._build_vision_prompt(blocks)
        assert "3 content blocks" in prompt

    def test_prompt_requests_json(self):
        strategy = VisionStrategy()
        blocks = [ContentBlock(0, (72, 50, 540, 70), "Block 1", 0)]
        prompt = strategy._build_vision_prompt(blocks)
        assert "JSON" in prompt


class TestAIResponseParsing:
    """Test AI response parsing."""

    def test_parse_valid_json(self):
        strategy = VisionStrategy()
        content = '{"order": [2, 1, 3], "headers": []}'
        result = strategy._parse_ai_response(content, 3)
        assert result == [1, 0, 2]  # Converted from 1-indexed to 0-indexed

    def test_parse_json_in_code_block(self):
        strategy = VisionStrategy()
        content = '```json\n{"order": [1, 2, 3], "headers": []}\n```'
        result = strategy._parse_ai_response(content, 3)
        assert result == [0, 1, 2]

    def test_parse_invalid_json(self):
        strategy = VisionStrategy()
        result = strategy._parse_ai_response("not json at all", 3)
        assert result is None

    def test_parse_out_of_range_indices(self):
        strategy = VisionStrategy()
        content = '{"order": [1, 5, 2], "headers": []}'
        result = strategy._parse_ai_response(content, 3)
        # 5 is out of range (only 3 blocks), should be filtered
        assert result == [0, 1]

    def test_parse_empty_order(self):
        strategy = VisionStrategy()
        content = '{"order": [], "headers": []}'
        result = strategy._parse_ai_response(content, 3)
        assert result == []


class TestIoUComputation:
    """Test Intersection over Union computation."""

    def test_identical_boxes(self):
        bbox = (0, 0, 100, 100)
        assert VisionStrategy._compute_iou(bbox, bbox) == pytest.approx(1.0)

    def test_no_overlap(self):
        bbox1 = (0, 0, 50, 50)
        bbox2 = (100, 100, 150, 150)
        assert VisionStrategy._compute_iou(bbox1, bbox2) == 0.0

    def test_partial_overlap(self):
        bbox1 = (0, 0, 100, 100)
        bbox2 = (50, 50, 150, 150)
        iou = VisionStrategy._compute_iou(bbox1, bbox2)
        # Intersection = 50*50 = 2500
        # Union = 10000 + 10000 - 2500 = 17500
        assert iou == pytest.approx(2500 / 17500)

    def test_one_inside_other(self):
        bbox1 = (0, 0, 100, 100)
        bbox2 = (25, 25, 75, 75)
        iou = VisionStrategy._compute_iou(bbox1, bbox2)
        # Intersection = 50*50 = 2500
        # Union = 10000 + 2500 - 2500 = 10000
        assert iou == pytest.approx(2500 / 10000)


class TestBlockMapping:
    """Test AI-to-block mapping."""

    def test_valid_mapping(self):
        strategy = VisionStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Block 0", 0),
            ContentBlock(1, (72, 100, 540, 120), "Block 1", 0),
            ContentBlock(2, (72, 150, 540, 170), "Block 2", 0),
        ]
        ai_order = [2, 0, 1]
        result = strategy._map_ai_to_blocks(ai_order, blocks)
        assert result == [2, 0, 1]

    def test_out_of_range_filtered(self):
        strategy = VisionStrategy()
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Block 0", 0),
            ContentBlock(1, (72, 100, 540, 120), "Block 1", 0),
        ]
        ai_order = [0, 5, 1]
        result = strategy._map_ai_to_blocks(ai_order, blocks)
        assert result == [0, 1]


class TestVisionFixMissingDeps:
    """Test VisionStrategy.fix() error handling."""

    def test_fix_missing_pymupdf(self):
        strategy = VisionStrategy()
        import src.education.remediation.reading_order as ro_mod

        original = ro_mod.HAS_PYMUPDF
        ro_mod.HAS_PYMUPDF = False
        try:
            result = strategy.fix("/tmp/test.pdf")
            assert not result.success
            assert "PyMuPDF" in result.error
        finally:
            ro_mod.HAS_PYMUPDF = original

    def test_fix_missing_pikepdf(self):
        strategy = VisionStrategy()
        import src.education.remediation.reading_order as ro_mod

        original = ro_mod.HAS_PIKEPDF
        ro_mod.HAS_PIKEPDF = False
        try:
            result = strategy.fix("/tmp/test.pdf")
            assert not result.success
            assert "pikepdf" in result.error
        finally:
            ro_mod.HAS_PIKEPDF = original


class TestVisionFixWithMockedAI:
    """Test VisionStrategy.fix() with mocked AI provider."""

    def test_fix_success_mocked(self):
        """Full pipeline with mocked AI returning correct order."""
        strategy = VisionStrategy()

        # Create a test PDF with content
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            import fitz

            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            # Insert some text blocks
            page.insert_text((72, 100), "First paragraph", fontsize=12)
            page.insert_text((72, 200), "Second paragraph", fontsize=12)
            page.insert_text((72, 300), "Third paragraph", fontsize=12)
            doc.save(f.name)
            doc.close()

            try:
                mock_provider = MagicMock()
                mock_provider.analyze_image_sync.return_value = {
                    "success": True,
                    "content": json.dumps({"order": [1, 2, 3], "headers": []}),
                }

                # Patch the import source (fix method does:
                # from src.ai.providers import get_provider_manager)
                with patch(
                    "src.ai.providers.get_provider_manager",
                    return_value=mock_provider,
                ):
                    result = strategy.fix(f.name, page_num=0)

                assert result.success
                assert result.layout_type == LayoutType.COMPLEX
            finally:
                os.unlink(f.name)

    def test_fix_ai_failure(self):
        """AI failure should return unsuccessful result."""
        strategy = VisionStrategy()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            import fitz

            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 100), "Some text", fontsize=12)
            doc.save(f.name)
            doc.close()

            try:
                mock_provider = MagicMock()
                mock_provider.analyze_image_sync.return_value = {
                    "success": False,
                    "error": "API quota exceeded",
                }

                with patch(
                    "src.ai.providers.get_provider_manager",
                    return_value=mock_provider,
                ):
                    result = strategy.fix(f.name, page_num=0)

                assert not result.success
                assert "AI analysis failed" in result.error
            finally:
                os.unlink(f.name)


class TestStrategySelectionUpdated:
    """Test updated strategy selection with floating element detection."""

    def test_single_column_heuristic(self):
        blocks = [
            ContentBlock(0, (72, 50, 540, 70), "Line 1", 0),
            ContentBlock(1, (72, 100, 540, 120), "Line 2", 0),
            ContentBlock(2, (72, 150, 540, 170), "Line 3", 0),
        ]
        assert get_reading_order_strategy(blocks) == "heuristic"

    def test_two_column_no_floating_heuristic(self):
        blocks = [
            ContentBlock(0, (72, 50, 280, 70), "Left 1", 0),
            ContentBlock(1, (72, 80, 280, 100), "Left 2", 0),
            ContentBlock(2, (340, 50, 540, 70), "Right 1", 0),
            ContentBlock(3, (340, 80, 540, 100), "Right 2", 0),
        ]
        assert get_reading_order_strategy(blocks) == "heuristic"

    def test_empty_heuristic(self):
        assert get_reading_order_strategy([]) == "heuristic"


class TestVisionConfidence:
    """Test confidence scoring for vision strategy."""

    def test_vision_base_confidence(self):
        """AI_VISION base is 0.55, range is 0.55-0.75."""
        calc = ConfidenceCalculator()
        # Perfect match
        confidence = calc.calculate(
            FixMethod.AI_VISION, signal_strength=1.0, context_quality=1.0
        )
        assert 0.55 <= confidence <= 0.80

    def test_vision_low_match_confidence(self):
        """Low match ratio should produce lower confidence."""
        calc = ConfidenceCalculator()
        confidence = calc.calculate(
            FixMethod.AI_VISION, signal_strength=0.3, context_quality=0.3
        )
        assert confidence < 0.60
