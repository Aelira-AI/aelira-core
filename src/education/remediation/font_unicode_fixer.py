"""Compatibility adapter for missing-map findings; never mutates PDF fonts.

Missing ToUnicode alone does not establish unreadable text. A supported base
encoding can already supply semantics. A repair needs complete used-code
coverage and source-bound save/reopen verification, which a Differences-only
in-place edit cannot provide. Reviewed recovery happens during staging.
"""

from dataclasses import dataclass

import pikepdf

from .base import RemediationIssue


@dataclass(frozen=True)
class FontFixResult:
    success: bool
    font_name: str = ""
    mappings_added: int = 0
    confidence: float = 0.0
    needs_review: bool = True
    error: str | None = "source_bound_font_review_required"


class FontUnicodeFixer:
    """Retained caller interface; findings remain unresolved without staging."""

    def __init__(self, pdf: pikepdf.Pdf, fitz_doc=None) -> None:
        self.pdf = pdf

    def fix(self, issues: list[RemediationIssue]) -> list[FontFixResult]:
        if any(
            issue.metadata.get("issue_type") == "missing_tounicode" for issue in issues
        ):
            return [FontFixResult(success=False)]
        return []
