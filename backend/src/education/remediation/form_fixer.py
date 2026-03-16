"""FormFixer specialist module for PDF form field accessibility remediation.

Fixes WCAG 1.3.1 / PDF/UA form-related issues:
- Missing tooltip (/TU) on form fields (screen readers read /TU as the field label)
- Missing tab order (/Tabs /S) on pages that contain widgets

WCAG 1.3.1 (Info and Relationships): Information, structure, and relationships
conveyed through presentation can be programmatically determined.
WCAG 4.1.2 (Name, Role, Value): For all UI components, the name and role can be
programmatically determined.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .base import IssueCategory, RemediationIssue

logger = logging.getLogger(__name__)

# Issue type constants matched against metadata["issue_type"]
_ISSUE_UNLABELED = "unlabeled_form_fields"
_ISSUE_TAB_ORDER = "missing_tab_order"


@dataclass
class FixResult:
    """Result of a single form-fix operation."""

    issue_id: str
    success: bool
    confidence: float
    description: str


class FormFixer:
    """Specialist module that remediates PDF form field accessibility issues.

    Accepts a pikepdf.Pdf (open for editing) and a fitz.Document (open for
    reading).  Callers are responsible for saving the pikepdf.Pdf after all
    specialists have run.

    Interface contract:
        fixer = FormFixer(pdf, fitz_doc)
        results: list[FixResult] = fixer.fix(issues)
    """

    def __init__(self, pdf: "pikepdf.Pdf", fitz_doc: "fitz.Document") -> None:
        self._pdf = pdf
        self._fitz_doc = fitz_doc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self, issues: List[RemediationIssue]) -> List[FixResult]:
        """Attempt to fix every FORM issue in *issues*.

        Unknown issue types produce a failed FixResult rather than raising.

        Args:
            issues: List of RemediationIssue objects (category == FORM expected).

        Returns:
            One FixResult per issue.
        """
        results: List[FixResult] = []
        for issue in issues:
            if issue.category != IssueCategory.FORM:
                results.append(
                    FixResult(
                        issue_id=issue.id,
                        success=False,
                        confidence=0.0,
                        description=f"FormFixer does not handle category: {issue.category}",
                    )
                )
                continue

            issue_type = issue.metadata.get("issue_type", "")
            page_number = issue.metadata.get("page_number")  # 1-indexed or None

            try:
                if issue_type == _ISSUE_UNLABELED:
                    result = self._fix_unlabeled_fields(issue, page_number)
                elif issue_type == _ISSUE_TAB_ORDER:
                    result = self._fix_tab_order(issue, page_number)
                else:
                    result = FixResult(
                        issue_id=issue.id,
                        success=False,
                        confidence=0.0,
                        description=f"Unknown form issue type: {issue_type!r}",
                    )
            except Exception as exc:
                logger.error("FormFixer.fix error for issue %s: %s", issue.id, exc, exc_info=True)
                result = FixResult(
                    issue_id=issue.id,
                    success=False,
                    confidence=0.0,
                    description=f"Exception during fix: {exc}",
                )

            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Fix: unlabeled form fields
    # ------------------------------------------------------------------

    def _fix_unlabeled_fields(
        self, issue: RemediationIssue, page_number: Optional[int]
    ) -> FixResult:
        """Add /TU tooltip to every AcroForm field that lacks one.

        If *page_number* is provided (1-indexed), only fields whose /P
        reference resolves to that page are processed.  Otherwise all fields
        in the AcroForm are processed.

        The /TU value is inferred in priority order:
        1. Positional inference from nearby text on the page (PyMuPDF)
        2. Humanised /T field name (underscores/hyphens → spaces, title case)

        Returns a FixResult with success=True if at least one field was labelled.
        """
        if not HAS_PIKEPDF:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="pikepdf not available",
            )

        acroform = self._pdf.Root.get("/AcroForm")
        if acroform is None:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="No AcroForm found in document",
            )

        fields_array = acroform.get("/Fields")
        if fields_array is None:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="AcroForm has no /Fields array",
            )

        # Resolve page filter (0-indexed)
        target_page_index: Optional[int] = None
        if page_number is not None:
            target_page_index = int(page_number) - 1  # convert 1-indexed to 0-indexed

        fixed_count = 0
        confidence_sum = 0.0

        for field_ref in fields_array:
            try:
                field = field_ref
                # Skip non-widget annotations (e.g. container fields without /FT)
                subtype = field.get("/Subtype")
                if subtype is not None and str(subtype) != "/Widget":
                    continue

                # Apply page filter if requested
                if target_page_index is not None:
                    field_page_index = self._get_field_page_index(field)
                    if field_page_index is not None and field_page_index != target_page_index:
                        continue

                # Skip fields that already have a tooltip
                if "/TU" in field:
                    continue

                # Infer label
                label, confidence = self._infer_label(field, target_page_index)
                if label:
                    field["/TU"] = String(label)
                    fixed_count += 1
                    confidence_sum += confidence
                    logger.debug(
                        "Set /TU=%r on field %r (confidence=%.2f)",
                        label,
                        str(field.get("/T", "")),
                        confidence,
                    )

            except Exception as exc:
                logger.warning("Could not process field: %s", exc)

        if fixed_count == 0:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="No unlabeled fields found or all already have /TU",
            )

        avg_confidence = confidence_sum / fixed_count
        return FixResult(
            issue_id=issue.id,
            success=True,
            confidence=avg_confidence,
            description=f"Added /TU tooltip to {fixed_count} form field(s)",
        )

    # ------------------------------------------------------------------
    # Fix: missing tab order
    # ------------------------------------------------------------------

    def _fix_tab_order(
        self, issue: RemediationIssue, page_number: Optional[int]
    ) -> FixResult:
        """Set /Tabs /S (Structure order) on the specified page(s).

        PDF/UA requires that pages containing widget annotations declare an
        explicit tab order.  /Tabs /S means "follow the structure order",
        which is the most accessible choice.

        Args:
            issue: The RemediationIssue being fixed.
            page_number: 1-indexed page number, or None to fix all pages.

        Returns:
            FixResult indicating success.
        """
        if not HAS_PIKEPDF:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="pikepdf not available",
            )

        pages_to_fix: List[int] = []
        if page_number is not None:
            idx = int(page_number) - 1  # 1-indexed → 0-indexed
            if 0 <= idx < len(self._pdf.pages):
                pages_to_fix = [idx]
        else:
            pages_to_fix = list(range(len(self._pdf.pages)))

        fixed_count = 0
        for idx in pages_to_fix:
            try:
                page_obj = self._pdf.pages[idx].obj
                # Only set /Tabs on pages that have widget annotations
                annots = page_obj.get("/Annots")
                has_widgets = False
                if annots is not None:
                    for ann in annots:
                        try:
                            subtype = ann.get("/Subtype")
                            if subtype is not None and str(subtype) == "/Widget":
                                has_widgets = True
                                break
                        except Exception:
                            pass

                if has_widgets:
                    page_obj["/Tabs"] = Name("/S")
                    fixed_count += 1
                    logger.debug("Set /Tabs /S on page %d", idx)
            except Exception as exc:
                logger.warning("Could not set /Tabs on page %d: %s", idx, exc)

        if fixed_count == 0:
            return FixResult(
                issue_id=issue.id,
                success=False,
                confidence=0.0,
                description="No pages updated (already have /Tabs or no widgets found)",
            )

        return FixResult(
            issue_id=issue.id,
            success=True,
            confidence=0.95,  # /Tabs /S is a deterministic structural fix
            description=f"Set /Tabs /S on {fixed_count} page(s)",
        )

    # ------------------------------------------------------------------
    # Label inference helpers
    # ------------------------------------------------------------------

    def _infer_label(
        self, field, page_index: Optional[int]
    ) -> Tuple[str, float]:
        """Infer a human-readable label for a form field.

        Tries positional inference first (higher confidence), then falls
        back to humanising the /T field name.

        Returns:
            (label, confidence) — label is empty string if inference failed.
        """
        # Attempt positional inference via PyMuPDF
        if HAS_PYMUPDF and page_index is not None:
            rect = self._get_field_rect(field)
            if rect is not None:
                positional = self._infer_label_from_position(page_index, rect)
                if positional:
                    return positional, 0.85

        # Fall back: humanise /T field name
        t_value = field.get("/T")
        if t_value is not None:
            raw = str(t_value)
            humanised = self._humanise_field_name(raw)
            if humanised:
                return humanised, 0.60

        return "", 0.0

    def _infer_label_from_position(
        self, page_index: int, rect: Tuple[float, float, float, float]
    ) -> str:
        """Use PyMuPDF text blocks to find text to the left or above the field.

        Searches a region extending 200 pts to the left and 30 pts above the
        field bounding box for the closest text fragment.

        Args:
            page_index: 0-indexed page number in the fitz document.
            rect: Field bounding box (x0, y0, x1, y1) in PDF coordinates.

        Returns:
            Nearest label text, or empty string if nothing found.
        """
        if page_index >= len(self._fitz_doc):
            return ""

        try:
            page = self._fitz_doc[page_index]
            x0, y0, x1, y1 = rect
            field_height = max(y1 - y0, 1.0)
            field_mid_y = (y0 + y1) / 2.0

            # Search region: left of field and slightly above
            search_rect = fitz.Rect(
                x0 - 200,          # extend left
                y0 - field_height,  # a little above
                x0 + 10,           # just past left edge
                y1 + field_height,  # a little below
            )

            blocks = page.get_text("blocks", clip=search_rect)

            if not blocks:
                # Try above the field as fallback
                above_rect = fitz.Rect(x0 - 50, y0 - 40, x1 + 50, y0)
                blocks = page.get_text("blocks", clip=above_rect)

            if not blocks:
                return ""

            # Pick the block whose vertical centre is closest to the field centre
            def _distance(blk):
                bx0, by0, bx1, by1 = blk[0], blk[1], blk[2], blk[3]
                blk_mid_y = (by0 + by1) / 2.0
                return abs(blk_mid_y - field_mid_y)

            closest = min(blocks, key=_distance)
            text = closest[4].strip() if len(closest) > 4 else ""
            # Strip trailing colon (common in form labels like "Name:")
            if text.endswith(":"):
                text = text[:-1].strip()
            return text

        except Exception as exc:
            logger.debug("Positional label inference failed: %s", exc)
            return ""

    @staticmethod
    def _humanise_field_name(raw: str) -> str:
        """Convert a machine-style field name to a human-readable label.

        Examples:
            "name_field"    → "Name Field"
            "first-name"    → "First Name"
            "emailAddress"  → "Email Address"  (camelCase split)
            "field1"        → "Field 1"

        Returns empty string if *raw* is blank.
        """
        if not raw:
            return ""

        # Replace underscores and hyphens with spaces
        label = raw.replace("_", " ").replace("-", " ")

        # Split camelCase: insert space before each uppercase letter that
        # follows a lowercase letter (e.g. "firstName" → "first Name")
        result = []
        for i, ch in enumerate(label):
            if ch.isupper() and i > 0 and label[i - 1].islower():
                result.append(" ")
            result.append(ch)
        label = "".join(result)

        # Collapse multiple spaces and title-case
        label = " ".join(label.split()).title()
        return label

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _get_field_rect(
        self, field
    ) -> Optional[Tuple[float, float, float, float]]:
        """Extract the /Rect of a field as a float 4-tuple, or None."""
        try:
            rect = field.get("/Rect")
            if rect is None:
                return None
            return (
                float(rect[0]),
                float(rect[1]),
                float(rect[2]),
                float(rect[3]),
            )
        except Exception:
            return None

    def _get_field_page_index(self, field) -> Optional[int]:
        """Resolve the /P page reference to a 0-indexed page number.

        Iterates through pdf.pages comparing object identity to find which
        page contains this field.  Returns None if /P is absent or the page
        cannot be found.
        """
        try:
            page_ref = field.get("/P")
            if page_ref is None:
                return None
            for idx, page in enumerate(self._pdf.pages):
                if page.obj.objgen == page_ref.objgen:
                    return idx
        except Exception as exc:
            logger.debug("Could not resolve field /P reference: %s", exc)
        return None
