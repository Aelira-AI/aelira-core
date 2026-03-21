"""Tests for the confidence scoring engine."""
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the confidence module directly to avoid triggering the heavy
# remediation __init__.py imports (docx, pikepdf, etc.) which are not
# needed for this standalone module.
_mod_path = Path(__file__).resolve().parent.parent / "src" / "education" / "remediation" / "confidence.py"
_spec = importlib.util.spec_from_file_location("confidence", _mod_path)
_confidence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_confidence)

FixMethod = _confidence.FixMethod
ConfidenceCalculator = _confidence.ConfidenceCalculator


class TestFixMethod:
    def test_enum_values(self):
        assert FixMethod.RULE == "rule"
        assert FixMethod.HEURISTIC == "heuristic"
        assert FixMethod.AI_TEXT == "ai_text"
        assert FixMethod.AI_VISION == "ai_vision"


class TestConfidenceCalculator:
    @pytest.fixture
    def calc(self):
        return ConfidenceCalculator()

    def test_rule_based_default(self, calc):
        score = calc.calculate(FixMethod.RULE)
        assert score == 0.95

    def test_rule_based_verified(self, calc):
        score = calc.calculate(FixMethod.RULE, verified=True)
        assert score == 1.0

    def test_heuristic_default(self, calc):
        score = calc.calculate(FixMethod.HEURISTIC)
        assert score == 0.70

    def test_heuristic_high_signal(self, calc):
        score = calc.calculate(FixMethod.HEURISTIC, signal_strength=0.9)
        assert 0.73 <= score <= 0.80

    def test_heuristic_low_signal(self, calc):
        score = calc.calculate(FixMethod.HEURISTIC, signal_strength=0.3)
        assert 0.55 <= score <= 0.70

    def test_ai_text_default(self, calc):
        score = calc.calculate(FixMethod.AI_TEXT)
        assert score == 0.60

    def test_ai_text_with_good_context(self, calc):
        score = calc.calculate(FixMethod.AI_TEXT, context_quality=0.9)
        assert 0.65 <= score <= 0.80

    def test_ai_vision_default(self, calc):
        score = calc.calculate(FixMethod.AI_VISION)
        assert score == 0.55

    def test_ai_vision_clear_image(self, calc):
        score = calc.calculate(FixMethod.AI_VISION, context_quality=0.9)
        assert 0.60 <= score <= 0.75

    def test_score_clamped_to_0_1(self, calc):
        score = calc.calculate(FixMethod.RULE, verified=True)
        assert 0.0 <= score <= 1.0
        score2 = calc.calculate(FixMethod.AI_VISION, context_quality=0.0, signal_strength=0.0)
        assert 0.0 <= score2 <= 1.0

    def test_needs_review_above_threshold(self, calc):
        assert calc.needs_review(0.90) is False

    def test_needs_review_below_threshold(self, calc):
        assert calc.needs_review(0.70) is True

    def test_needs_review_at_threshold(self, calc):
        assert calc.needs_review(0.85) is False

    def test_needs_review_custom_threshold(self, calc):
        assert calc.needs_review(0.80, threshold=0.90) is True
        assert calc.needs_review(0.95, threshold=0.90) is False
