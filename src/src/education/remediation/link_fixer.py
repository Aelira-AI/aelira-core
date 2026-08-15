"""LinkFixer specialist module for PDF link annotation accessibility.

Adds accessible names (/Contents) to link annotations that are missing them,
and replaces vague link text (e.g. "click here") with descriptive alternatives.

WCAG 2.4.4 (Link Purpose, In Context): The purpose of each link can be
determined from the link text alone, or from the link text together with its
programmatically determined link context.

PDF/UA requirement: All Link annotation dictionaries must have a /Contents
entry that provides an accessible name for assistive technology.

Coordinate system note:
  PDF uses bottom-left origin; PyMuPDF uses top-left origin.
  When converting, for a page of height H:
    fitz_y0 = H - pdf_y1  (PDF top edge → fitz top)
    fitz_y1 = H - pdf_y0  (PDF bottom edge → fitz bottom)
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from pikepdf import String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

from .base import IssueCategory, RemediationIssue

logger = logging.getLogger(__name__)

# Phrases considered too vague to serve as accessible link names.
VAGUE_PHRASES: set = {
    "click here",
    "here",
    "read more",
    "more",
    "link",
    "learn more",
    "details",
    "info",
    "this link",
    "this page",
    "go",
    "continue",
}


@dataclass
class FixResult:
    """Result of a single link fix operation."""

    success: bool
    links_examined: int = 0
    links_fixed: int = 0
    fix_method: str = "heuristic"
    notes: Optional[str] = None
    error: Optional[str] = None


class LinkFixer:
    """Fix link annotation accessibility issues in PDF documents.

    Takes a pikepdf document and a PyMuPDF document (for text extraction)
    and adds /Contents entries to link annotations that are missing them,
    and replaces vague link text with descriptive alternatives.

    Args:
        pdf: An open pikepdf.Pdf object (will be modified in place).
        fitz_doc: An open fitz.Document for text extraction.
        ai_client: Optional AI client for generating descriptions for vague
            link text. If not provided, falls back to using the URI.
    """

    def __init__(
        self,
        pdf: Any,
        fitz_doc: Any,
        ai_client: Optional[Any] = None,
    ) -> None:
        self._pdf = pdf
        self._fitz_doc = fitz_doc
        self._ai_client = ai_client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fix(self, issues: List[RemediationIssue]) -> List[FixResult]:
        """Fix link accessibility issues.

        Dispatches each issue to the appropriate sub-fixer based on
        ``metadata["issue_type"]``.

        Args:
            issues: List of RemediationIssue objects with
                ``category == IssueCategory.LINK``.

        Returns:
            List of FixResult objects, one per processed issue.
        """
        results: List[FixResult] = []

        for issue in issues:
            if issue.category != IssueCategory.LINK:
                results.append(
                    FixResult(
                        success=False,
                        notes=f"Skipped: not a LINK issue (got {issue.category})",
                    )
                )
                continue

            issue_type = issue.metadata.get("issue_type", "links_missing_alt")
            page_number = issue.metadata.get("page_number")  # 1-indexed or None

            try:
                if issue_type in ("links_missing_alt", "links_missing_contents"):
                    result = self._fix_missing_contents(page_number)
                elif issue_type == "vague_link_text":
                    result = self._fix_vague_text(page_number)
                else:
                    # Default: try to add /Contents to all links on the page
                    result = self._fix_missing_contents(page_number)
            except Exception as exc:
                logger.error("LinkFixer.fix() error for issue %s: %s", issue.id, exc)
                result = FixResult(success=False, error=str(exc))

            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Sub-fixers
    # ------------------------------------------------------------------

    def _fix_missing_contents(self, page_number: Optional[int]) -> FixResult:
        """Add /Contents to link annotations that are missing it.

        Args:
            page_number: 1-indexed page number, or None to process all pages.

        Returns:
            FixResult summarising what was changed.
        """
        page_indices = self._resolve_page_indices(page_number)
        examined = 0
        fixed = 0

        for page_idx in page_indices:
            if page_idx >= len(self._pdf.pages):
                continue

            page_obj = self._pdf.pages[page_idx].obj
            annots = page_obj.get("/Annots")
            if annots is None:
                continue

            fitz_page = (
                self._fitz_doc[page_idx] if page_idx < len(self._fitz_doc) else None
            )

            for annot_ref in annots:
                annot = annot_ref
                # Resolve indirect reference
                if hasattr(annot, "obj"):
                    annot = annot.obj

                subtype = str(annot.get("/Subtype", ""))
                if subtype != "/Link":
                    continue

                examined += 1

                # Already has /Contents — skip
                if "/Contents" in annot:
                    continue

                # Try to get visible text under the link rectangle
                rect = annot.get("/Rect")
                label = ""
                if rect is not None and fitz_page is not None:
                    label = self._extract_text_under_rect(fitz_page, rect)

                # Fall back to URI
                if not label:
                    label = self._get_link_uri(annot) or ""

                if label:
                    annot["/Contents"] = String(label)
                    fixed += 1
                    logger.debug(
                        "Added /Contents=%r to link on page %d", label, page_idx + 1
                    )

        return FixResult(
            success=True,
            links_examined=examined,
            links_fixed=fixed,
            fix_method="heuristic",
            notes=f"Processed {len(page_indices)} page(s)",
        )

    def _fix_vague_text(self, page_number: Optional[int]) -> FixResult:
        """Replace vague /Contents text with a descriptive label.

        Replaces vague phrases (e.g. "click here") with an AI-generated
        description when an AI client is available, otherwise uses the URI.

        Args:
            page_number: 1-indexed page number, or None to process all pages.

        Returns:
            FixResult summarising what was changed.
        """
        page_indices = self._resolve_page_indices(page_number)
        examined = 0
        fixed = 0
        method_used = "rule"

        for page_idx in page_indices:
            if page_idx >= len(self._pdf.pages):
                continue

            page_obj = self._pdf.pages[page_idx].obj
            annots = page_obj.get("/Annots")
            if annots is None:
                continue

            for annot_ref in annots:
                annot = annot_ref
                if hasattr(annot, "obj"):
                    annot = annot.obj

                subtype = str(annot.get("/Subtype", ""))
                if subtype != "/Link":
                    continue

                examined += 1

                current_label = ""
                if "/Contents" in annot:
                    current_label = str(annot["/Contents"]).strip()

                if not self._is_vague(current_label):
                    continue

                uri = self._get_link_uri(annot) or ""
                new_label = ""

                # Try AI first
                if self._ai_client and uri:
                    new_label = self._generate_link_description(uri, current_label)
                    if new_label:
                        method_used = "ai_text"

                # Fall back to URI
                if not new_label:
                    new_label = uri

                if new_label:
                    annot["/Contents"] = String(new_label)
                    fixed += 1
                    logger.debug(
                        "Replaced vague label %r → %r on page %d",
                        current_label,
                        new_label,
                        page_idx + 1,
                    )

        return FixResult(
            success=True,
            links_examined=examined,
            links_fixed=fixed,
            fix_method=method_used,
            notes=f"Replaced vague link text on {len(page_indices)} page(s)",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text_under_rect(self, fitz_page: Any, rect: Any) -> str:
        """Extract visible text from the area covered by a link annotation.

        CRITICAL: PDF uses bottom-left origin; PyMuPDF uses top-left origin.
        We must convert PDF coordinates to PyMuPDF coordinates before calling
        page.get_textbox().

        Args:
            fitz_page: A fitz.Page object.
            rect: A pikepdf Array or object with four numeric items
                [x0, y0, x1, y1] in PDF coordinates (bottom-left origin).

        Returns:
            Stripped text string, or empty string if nothing found.
        """
        try:
            x0 = float(rect[0])
            y0 = float(rect[1])
            x1 = float(rect[2])
            y1 = float(rect[3])

            page_height = fitz_page.rect.height

            # Convert PDF bottom-left origin to PyMuPDF top-left origin
            fitz_y0 = page_height - y1  # PDF top → fitz top
            fitz_y1 = page_height - y0  # PDF bottom → fitz bottom

            fitz_rect = fitz.Rect(x0, fitz_y0, x1, fitz_y1)
            text = fitz_page.get_textbox(fitz_rect).strip()
            return text
        except Exception as exc:
            logger.debug("Text extraction under rect failed: %s", exc)
            return ""

    def _get_link_uri(self, annot: Any) -> Optional[str]:
        """Extract the URI (or destination string) from a link annotation.

        Checks:
        1. ``/A`` action dictionary with ``/S /URI``
        2. ``/A`` action dictionary with ``/S /GoTo`` (returns destination name)
        3. ``/Dest`` entry (returns string representation)

        Args:
            annot: A pikepdf Dictionary for a /Link annotation.

        Returns:
            URI string or destination label, or None if not found.
        """
        try:
            action = annot.get("/A")
            if action is not None:
                subtype = str(action.get("/S", ""))
                if subtype == "/URI":
                    uri_val = action.get("/URI")
                    if uri_val is not None:
                        return str(uri_val)
                elif subtype == "/GoTo":
                    dest = action.get("/D")
                    if dest is not None:
                        return str(dest)

            dest = annot.get("/Dest")
            if dest is not None:
                return str(dest)
        except Exception as exc:
            logger.debug("URI extraction failed: %s", exc)

        return None

    def _generate_link_description(self, uri: str, current_text: str) -> str:
        """Generate a descriptive link label using the AI client.

        Constructs a prompt asking the AI to suggest a concise, descriptive
        link label given the URI and current (vague) text.

        Args:
            uri: The link destination URI.
            current_text: The existing (vague) link text.

        Returns:
            A descriptive label string, or empty string if generation failed.
        """
        if not self._ai_client:
            return ""

        prompt = (
            f"You are helping make a PDF document accessible.\n"
            f"A hyperlink has the following vague text: '{current_text}'\n"
            f"The link destination is: {uri}\n\n"
            f"Write a concise, descriptive accessible name for this link "
            f"(5–10 words maximum) that clearly conveys its purpose. "
            f"Return ONLY the label text with no punctuation at the end."
        )

        try:
            result = self._ai_client.generate_text_sync(
                prompt=prompt,
                max_tokens=50,
            )
            if isinstance(result, dict):
                content = result.get("content", "")
            else:
                content = str(result)
            label = content.strip().strip('"').strip("'")
            return label if label else ""
        except Exception as exc:
            logger.debug("AI link description generation failed: %s", exc)
            return ""

    def _is_vague(self, text: str) -> bool:
        """Return True if *text* is a known vague link phrase.

        Args:
            text: The current link label.

        Returns:
            True if the text (lowercased, stripped) is in VAGUE_PHRASES.
        """
        return text.lower().strip() in VAGUE_PHRASES

    def _resolve_page_indices(self, page_number: Optional[int]) -> List[int]:
        """Convert a 1-indexed page number to a list of 0-indexed page indices.

        Args:
            page_number: 1-indexed page number, or None to process all pages.

        Returns:
            List of 0-indexed page indices to process.
        """
        if page_number is None:
            return list(range(len(self._pdf.pages)))
        page_idx = int(page_number) - 1
        if page_idx < 0:
            page_idx = 0
        return [page_idx]
