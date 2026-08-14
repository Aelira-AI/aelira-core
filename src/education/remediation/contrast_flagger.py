"""
ContrastFlagger — Report-only contrast issue guidance.

Does NOT auto-fix. Produces structured guidance for each contrast issue
so users know exactly what to change.
"""

import logging
from dataclasses import dataclass
from typing import List

from .base import RemediationIssue

logger = logging.getLogger(__name__)


@dataclass
class ContrastGuidance:
    """Structured guidance for a contrast issue."""

    issue_id: str
    page_number: int
    current_ratio: float
    required_ratio: float
    foreground_color: str
    background_color: str
    guidance: str
    manual_review_required: bool = True


class ContrastFlagger:
    """Produce structured contrast guidance (no auto-fix)."""

    def flag(self, issues: List[RemediationIssue]) -> List[ContrastGuidance]:
        results = []
        for issue in issues:
            ratio = issue.metadata.get("contrast_ratio", 0)
            fg = issue.metadata.get("foreground_color", "unknown")
            bg = issue.metadata.get("background_color", "unknown")
            page = issue.metadata.get("page_number", 0)

            is_large = issue.metadata.get("is_large_text", False)
            required = 3.0 if is_large else 4.5

            guidance_parts = [
                f"Contrast ratio {ratio:.1f}:1 is below the required {required:.1f}:1.",
                f"Foreground: {fg}, Background: {bg}.",
            ]

            if ratio > 0 and required > 0:
                deficit = required - ratio
                guidance_parts.append(
                    f"Increase contrast by at least {deficit:.1f} ratio points."
                )
                guidance_parts.append(
                    "Options: darken the text color, lighten the background, "
                    "or increase font size to 18pt+ (which lowers the requirement to 3:1)."
                )

            results.append(
                ContrastGuidance(
                    issue_id=issue.id,
                    page_number=page,
                    current_ratio=ratio,
                    required_ratio=required,
                    foreground_color=fg,
                    background_color=bg,
                    guidance=" ".join(guidance_parts),
                    manual_review_required=True,
                )
            )

        return results
