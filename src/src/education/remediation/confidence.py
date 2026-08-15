"""Confidence scoring engine for remediation fixes.

Assigns numeric confidence scores (0.0-1.0) to each fix based on the
method used (rule-based, heuristic, AI text, AI vision) and contextual
signals like verification status, signal strength, and context quality.
"""

from enum import Enum


class FixMethod(str, Enum):
    """How a fix was generated."""

    RULE = "rule"
    HEURISTIC = "heuristic"
    AI_TEXT = "ai_text"
    AI_VISION = "ai_vision"


class ConfidenceCalculator:
    """Calculates confidence scores for remediation fixes."""

    BASE_SCORES = {
        FixMethod.RULE: 0.95,
        FixMethod.HEURISTIC: 0.70,
        FixMethod.AI_TEXT: 0.60,
        FixMethod.AI_VISION: 0.55,
    }

    # How much signal_strength and context_quality can adjust the score
    ADJUSTMENT_RANGE = {
        FixMethod.RULE: 0.05,
        FixMethod.HEURISTIC: 0.15,
        FixMethod.AI_TEXT: 0.20,
        FixMethod.AI_VISION: 0.20,
    }

    DEFAULT_REVIEW_THRESHOLD = 0.85

    def calculate(
        self,
        method: FixMethod,
        *,
        verified: bool = False,
        signal_strength: float = 0.5,
        context_quality: float = 0.5,
    ) -> float:
        """Calculate confidence score for a fix.

        Args:
            method: How the fix was generated.
            verified: Whether post-fix verification passed.
            signal_strength: How strong the detection signal was (0.0-1.0).
                For heuristics: e.g. font size delta for heading detection.
            context_quality: Quality of surrounding context (0.0-1.0).
                For AI: availability of document title, headings, metadata.

        Returns:
            Confidence score clamped to [0.0, 1.0].
        """
        base = self.BASE_SCORES[method]
        adjustment_range = self.ADJUSTMENT_RANGE[method]

        if method == FixMethod.RULE:
            score = 1.0 if verified else base
        else:
            # Average of signal_strength and context_quality, centered at 0.5
            quality = (signal_strength + context_quality) / 2.0
            adjustment = (quality - 0.5) * adjustment_range * 2
            score = base + adjustment

        return max(0.0, min(1.0, score))

    def needs_review(
        self,
        confidence: float,
        threshold: float | None = None,
    ) -> bool:
        """Whether a fix with this confidence should be flagged for human review."""
        t = threshold if threshold is not None else self.DEFAULT_REVIEW_THRESHOLD
        return confidence < t
