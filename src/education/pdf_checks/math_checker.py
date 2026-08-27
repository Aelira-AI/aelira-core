"""Math/equation accessibility checking for PDFs."""

import logging
import re
from typing import Dict, List, Optional

import fitz

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.equation_region_detector import (
    MAX_CANDIDATES_PER_DOCUMENT,
    MAX_CANDIDATES_PER_PAGE,
    MAX_REGION_PAGES_PER_DOCUMENT,
    RasterEquationRegionDetector,
    is_full_page_raster_occurrence,
)
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.models import PDFImageIssue

try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

logger = logging.getLogger(__name__)


class MathEquationChecker:
    """Check math/equation accessibility in PDFs.

    Provides enhanced checks for LaTeX-compiled and math-heavy documents:
    - Math equations rendered as images without alt text
    - Untagged equation content (common in LaTeX-compiled PDFs)
    - Missing MathML representations
    - LaTeX-compiled PDF signatures (pdfTeX, XeTeX, LuaTeX)
    """

    # Patterns indicating math/equation content
    MATH_INDICATORS = [
        r"\b(?:equation|formula|theorem|lemma|proof|corollary)\b",
        r"[\u222b\u2211\u220f\u221a\u221e]",  # Integral, sum, product, sqrt, infinity
        r"[\u03b1-\u03c9\u0393-\u03a9]",  # Greek letters
        r"\b(?:sin|cos|tan|log|ln|exp|lim)\b",  # Math functions
        r"[=<>≤≥≠±×÷].*[=<>≤≥≠±×÷]",  # Multiple math operators
        r"\bx\s*[²³⁴⁵⁶⁷⁸⁹ⁿ]\b",  # Superscripts
        r"∑|∏|∫|∂|∇|∆|√",  # Math symbols
        r"\bfrac\b|\bsqrt\b|\bint\b",  # LaTeX remnants in text
    ]

    # Known LaTeX producer/creator strings
    LATEX_PRODUCERS = [
        "pdftex",
        "pdflatex",
        "xetex",
        "xelatex",
        "luatex",
        "lualatex",
        "latex",
        "tex",
        "dvips",
        "dvipdfm",
        "dvipdfmx",
        "ps2pdf",
        "miktex",
        "texlive",
        "context",
    ]

    # LaTeX remnant patterns indicating unconverted equations
    LATEX_REMNANTS = [
        (r"\\frac\{", "Unconverted LaTeX fraction command found"),
        (r"\\sqrt\{", "Unconverted LaTeX square root command found"),
        (r"\\sum_", "Unconverted LaTeX summation command found"),
        (r"\\int_", "Unconverted LaTeX integral command found"),
        (
            r"\\begin\{(?:equation|align|gather)",
            "Unconverted LaTeX equation environment found",
        ),
        (r"\$[^$]+\$", "Inline LaTeX math delimiters found"),
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    _LOCAL_EQUATION_CUE = re.compile(r"\b(?:equation|formula)\b", re.IGNORECASE)

    def __init__(
        self, *, region_detector: Optional[RasterEquationRegionDetector] = None
    ) -> None:
        self.region_detector = region_detector or RasterEquationRegionDetector()

    def find_image_equation_candidates(
        self, file_path: str, image_issues: List[PDFImageIssue]
    ) -> List[Dict]:
        """Create conservative candidates from exact, locally cued occurrences."""
        candidates: List[Dict] = []
        region_candidates: List[Dict] = []
        region_limit_exceeded = False
        with fitz.open(file_path) as doc:
            region_sources = []
            for page_index, page in enumerate(doc):
                try:
                    occurrences = _displayed_image_occurrences(page, page_index + 1)
                except Exception:
                    continue
                if len(occurrences) != 1:
                    continue
                occurrence = occurrences[0]
                if not is_full_page_raster_occurrence(page, occurrence):
                    continue
                region_sources.append((page_index, occurrence))
                if len(region_sources) > MAX_REGION_PAGES_PER_DOCUMENT:
                    region_limit_exceeded = True
                    region_sources = []
                    break

            for page_index, occurrence in region_sources:
                page = doc[page_index]
                try:
                    page_regions = self.region_detector.find_regions(
                        doc, page, occurrence
                    )
                except Exception:
                    continue
                if len(page_regions) > MAX_CANDIDATES_PER_PAGE:
                    continue
                region_candidates.extend(page_regions)
                if len(region_candidates) > MAX_CANDIDATES_PER_DOCUMENT:
                    region_limit_exceeded = True
                    region_candidates = []
                    break

            for issue in image_issues:
                page_index = issue.page_number - 1
                if page_index < 0 or page_index >= len(doc):
                    continue
                page = doc[page_index]
                bbox = fitz.Rect(issue.bbox)
                if bbox.is_empty or bbox.is_infinite:
                    continue
                # A page-sized scan cannot safely use the whole-image equation
                # path.  Region findings have a separate manual-only contract.
                if is_full_page_raster_occurrence(
                    page,
                    {
                        "bbox": issue.bbox,
                    },
                ):
                    continue
                nearby = fitz.Rect(
                    max(page.rect.x0, bbox.x0 - 24),
                    max(page.rect.y0, bbox.y0 - 48),
                    min(page.rect.x1, bbox.x1 + 24),
                    min(page.rect.y1, bbox.y1 + 48),
                )
                context = page.get_text("text", clip=nearby)
                if not self._LOCAL_EQUATION_CUE.search(context):
                    continue
                identity = {
                    "page_number": issue.page_number,
                    "image_xref": issue.image_xref,
                    "image_index": issue.image_index,
                    "occurrence_ordinal": issue.occurrence_ordinal,
                    "bbox": list(issue.bbox),
                    "occurrence_id": issue.occurrence_id,
                }
                candidates.append(
                    {
                        "category": "structure",
                        "severity": "high",
                        "rule": "WCAG 1.1.1",
                        "message": "Possible equation image requires accessible math",
                        "impact": "Screen readers cannot interpret equation pixels as mathematical content",
                        "location": (
                            f"Page {issue.page_number}, Image {issue.image_index + 1}, "
                            f"Occurrence {issue.occurrence_ordinal + 1}"
                        ),
                        "element": "Image equation candidate",
                        "suggested_fix": "Verify the equation and associate accessible MathML with this exact occurrence",
                        "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
                        **identity,
                        "metadata": {
                            "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
                            "rule": "WCAG 1.1.1",
                            **identity,
                        },
                    }
                )
        if region_limit_exceeded:
            region_candidates = []
        return candidates + region_candidates

    def check(self, file_path: str, text: str, structure: Dict) -> List[Dict]:
        """Check for math/equation accessibility issues in a PDF.

        This is intended for LaTeX-aware mode and adds enhanced checks for
        math-heavy documents.

        Args:
            file_path: Path to PDF file
            text: Extracted text content
            structure: Parsed document structure dict

        Returns:
            List of math/equation accessibility issue dicts
        """
        issues: List[Dict] = []
        is_latex_pdf = False
        equation_count = 0

        # Check PDF producer/creator for LaTeX tools
        if HAS_PIKEPDF:
            try:
                with pikepdf.open(file_path) as pdf:
                    # Check metadata for LaTeX producers
                    info = pdf.docinfo
                    producer = str(info.get("/Producer", "")).lower() if info else ""
                    creator = str(info.get("/Creator", "")).lower() if info else ""

                    for latex_tool in self.LATEX_PRODUCERS:
                        if latex_tool in producer or latex_tool in creator:
                            is_latex_pdf = True
                            logger.info(
                                f"[MathEquationChecker] Detected LaTeX-compiled PDF: {latex_tool}"
                            )
                            break

                    # Check for math structure elements
                    if Name.StructTreeRoot in pdf.Root:
                        struct_root = pdf.Root[Name.StructTreeRoot]
                        # Look for Formula, Math tags (UA-2) or Figure tags with math
                        # This is a simplified check - full traversal would be expensive
                        if Name.K in struct_root:
                            # Check role map for custom math tags
                            if Name.RoleMap in struct_root:
                                role_map = struct_root[Name.RoleMap]
                                role_map_str = str(role_map)
                                if (
                                    "formula" in role_map_str.lower()
                                    or "math" in role_map_str.lower()
                                ):
                                    equation_count += 1

            except Exception as e:
                logger.warning(
                    f"[MathEquationChecker] Error checking PDF math structure: {e}"
                )

        # Check text content for math patterns
        math_pattern_matches = 0
        for pattern in self.MATH_INDICATORS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            math_pattern_matches += len(matches)

        has_math_content = (
            math_pattern_matches >= 3
        )  # Threshold for "math-heavy" document

        # Issue: LaTeX PDF without proper tagging
        if is_latex_pdf:
            issues.append(
                {
                    "severity": "high",
                    "rule": "WCAG 1.1.1",
                    "message": "LaTeX-compiled PDF detected - equations may not be accessible",
                    "impact": "Screen readers cannot read mathematical formulas rendered as images or untagged content",
                    "page_number": 1,
                    "location": "Document-wide",
                    "element": "Math equations",
                    "suggested_fix": "Use accessible LaTeX packages (accessibility, axessibility) or convert equations to MathML",
                    "issue_type": "latex_equations_inaccessible",
                    "metadata": {
                        "is_latex_pdf": True,
                        "math_pattern_matches": math_pattern_matches,
                    },
                }
            )

        # Issue: Math content without MathML
        if has_math_content and not is_latex_pdf:
            issues.append(
                {
                    "severity": "medium",
                    "rule": "WCAG 1.1.1",
                    "message": "Document contains mathematical content that may not be accessible",
                    "impact": "Users with screen readers may not be able to understand mathematical formulas",
                    "page_number": 1,
                    "location": "Document-wide",
                    "element": "Mathematical content",
                    "suggested_fix": "Ensure all equations have proper alt text or are represented as MathML",
                    "issue_type": "math_content_accessibility",
                    "metadata": {
                        "math_pattern_matches": math_pattern_matches,
                    },
                }
            )

        # Check for common LaTeX accessibility issues in the text
        # (equations that weren't properly converted)
        for pattern, message in self.LATEX_REMNANTS:
            if re.search(pattern, text):
                issues.append(
                    {
                        "severity": "critical",
                        "rule": "WCAG 1.1.1",
                        "message": message,
                        "impact": "Raw LaTeX code is displayed instead of rendered math - completely inaccessible",
                        "page_number": 1,
                        "location": "Document content",
                        "element": "LaTeX code",
                        "suggested_fix": "Properly compile LaTeX to render equations, then ensure accessibility",
                        "issue_type": "raw_latex_code",
                    }
                )
                break  # Only report once

        # Issue: Check for equation images without alt text
        # This would be caught by regular image checking, but we add context
        if is_latex_pdf or has_math_content:
            # Add recommendation for MathML conversion
            issues.append(
                {
                    "severity": "low",
                    "rule": "Best Practice",
                    "message": "Consider converting equations to MathML for optimal accessibility",
                    "impact": "MathML allows screen readers to read equations mathematically rather than as descriptions",
                    "page_number": 1,
                    "location": "Document-wide",
                    "element": "All equations",
                    "suggested_fix": "Use Aelira's LaTeX scanner to convert equations to accessible MathML format",
                    "issue_type": "mathml_recommendation",
                }
            )

        return issues
