"""
MathFixer specialist module for PDF math content accessibility.

Converts raw LaTeX/math content found in PDF documents into accessible
Formula structure elements with embedded MathML and human-readable
ARIA labels, meeting WCAG 1.1.1 and PDF/UA-1 Section 7.11 requirements.

WCAG 1.1.1 (Non-text Content): All non-text content that is presented
to the user has a text alternative that serves the equivalent purpose.

Usage:
    with pikepdf.open('paper.pdf') as pdf:
        fitz_doc = fitz.open('paper.pdf')
        struct_tree = PDFStructureTree(pdf)
        fixer = MathFixer(pdf, fitz_doc, struct_tree=struct_tree)
        results = fixer.fix(issues)
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE, MATH_ISSUE_TYPES

try:
    from latex2mathml.converter import convert as latex_to_mathml

    HAS_LATEX2MATHML = True
except ImportError:
    HAS_LATEX2MATHML = False
    latex_to_mathml = None  # type: ignore[assignment]

try:
    import fitz  # noqa: F401  # PyMuPDF availability canary (module attrs not used directly)

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf  # noqa: F401

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for detecting LaTeX math in plain text extracted from PDFs
# ---------------------------------------------------------------------------

# Display math: $$...$$ or \[...\]
_DISPLAY_MATH_PATTERNS = [
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
]

# Inline math: $...$ or \(...\)
_INLINE_MATH_PATTERNS = [
    re.compile(r"\$([^$]+?)\$"),
    re.compile(r"\\\((.+?)\\\)", re.DOTALL),
]

# ---------------------------------------------------------------------------
# ARIA label substitution table (LaTeX → plain English)
# ---------------------------------------------------------------------------

_ARIA_SUBSTITUTIONS: List[tuple] = [
    # Fractions
    (re.compile(r"\\frac\{([^}]+)\}\{([^}]+)\}"), r"\1 over \2"),
    # Square root
    (re.compile(r"\\sqrt\{([^}]+)\}"), r"square root of \1"),
    (re.compile(r"\\sqrt\[([^\]]+)\]\{([^}]+)\}"), r"\1th root of \2"),
    # Powers (must come before simple ^ handler)
    (re.compile(r"\^2\b"), " squared"),
    (re.compile(r"\^3\b"), " cubed"),
    (re.compile(r"\^\{2\}"), " squared"),
    (re.compile(r"\^\{3\}"), " cubed"),
    (re.compile(r"\^\{([^}]+)\}"), r" to the power \1"),
    (re.compile(r"\^(\w)"), r" to the power \1"),
    # Subscripts
    (re.compile(r"_\{([^}]+)\}"), r" sub \1"),
    (re.compile(r"_(\w)"), r" sub \1"),
    # Greek letters (lowercase)
    (re.compile(r"\\alpha\b"), "alpha"),
    (re.compile(r"\\beta\b"), "beta"),
    (re.compile(r"\\gamma\b"), "gamma"),
    (re.compile(r"\\delta\b"), "delta"),
    (re.compile(r"\\epsilon\b"), "epsilon"),
    (re.compile(r"\\zeta\b"), "zeta"),
    (re.compile(r"\\eta\b"), "eta"),
    (re.compile(r"\\theta\b"), "theta"),
    (re.compile(r"\\iota\b"), "iota"),
    (re.compile(r"\\kappa\b"), "kappa"),
    (re.compile(r"\\lambda\b"), "lambda"),
    (re.compile(r"\\mu\b"), "mu"),
    (re.compile(r"\\nu\b"), "nu"),
    (re.compile(r"\\xi\b"), "xi"),
    (re.compile(r"\\pi\b"), "pi"),
    (re.compile(r"\\rho\b"), "rho"),
    (re.compile(r"\\sigma\b"), "sigma"),
    (re.compile(r"\\tau\b"), "tau"),
    (re.compile(r"\\upsilon\b"), "upsilon"),
    (re.compile(r"\\phi\b"), "phi"),
    (re.compile(r"\\chi\b"), "chi"),
    (re.compile(r"\\psi\b"), "psi"),
    (re.compile(r"\\omega\b"), "omega"),
    # Greek letters (uppercase)
    (re.compile(r"\\Gamma\b"), "Gamma"),
    (re.compile(r"\\Delta\b"), "Delta"),
    (re.compile(r"\\Theta\b"), "Theta"),
    (re.compile(r"\\Lambda\b"), "Lambda"),
    (re.compile(r"\\Xi\b"), "Xi"),
    (re.compile(r"\\Pi\b"), "Pi"),
    (re.compile(r"\\Sigma\b"), "Sigma"),
    (re.compile(r"\\Upsilon\b"), "Upsilon"),
    (re.compile(r"\\Phi\b"), "Phi"),
    (re.compile(r"\\Psi\b"), "Psi"),
    (re.compile(r"\\Omega\b"), "Omega"),
    # Operators
    (re.compile(r"\\times\b"), "times"),
    (re.compile(r"\\div\b"), "divided by"),
    (re.compile(r"\\pm\b"), "plus or minus"),
    (re.compile(r"\\mp\b"), "minus or plus"),
    (re.compile(r"\\leq\b"), "less than or equal to"),
    (re.compile(r"\\geq\b"), "greater than or equal to"),
    (re.compile(r"\\neq\b"), "not equal to"),
    (re.compile(r"\\approx\b"), "approximately equal to"),
    (re.compile(r"\\equiv\b"), "equivalent to"),
    (re.compile(r"\\cdot\b"), "dot"),
    (re.compile(r"\\ldots\b"), "ellipsis"),
    (re.compile(r"\\cdots\b"), "ellipsis"),
    (re.compile(r"\\infty\b"), "infinity"),
    (re.compile(r"\\partial\b"), "partial derivative"),
    (re.compile(r"\\nabla\b"), "nabla"),
    (re.compile(r"\\sum\b"), "sum"),
    (re.compile(r"\\prod\b"), "product"),
    (re.compile(r"\\int\b"), "integral"),
    (re.compile(r"\\oint\b"), "contour integral"),
    # Remove remaining LaTeX commands (leave their arguments visible)
    (re.compile(r"\\[a-zA-Z]+\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\[a-zA-Z]+"), ""),
    # Clean up braces, backticks, etc.
    (re.compile(r"[{}]"), ""),
    # Collapse whitespace
    (re.compile(r"\s{2,}"), " "),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MathFixResult:
    """Result of a single math-fix operation."""

    success: bool
    equation_text: str = ""
    aria_label: str = ""
    page_number: int = 0
    error: Optional[str] = None
    has_mathml: bool = False


# ---------------------------------------------------------------------------
# MathFixer
# ---------------------------------------------------------------------------


class MathFixer:
    """Fix math accessibility issues in PDF documents.

    Creates Formula structure elements with:
    - /Alt entry containing a human-readable ARIA label
    - /AF associated-file entry containing MathML markup

    This specialist module is designed to be called from PdfRemediator or
    standalone.  It deliberately accepts ``struct_tree`` and ``ai_client``
    as keyword arguments because those objects are constructed externally
    and are not part of the standard BaseRemediator ``__init__`` signature.

    Args:
        pdf: An *open* pikepdf.Pdf object (will be modified in place).
        fitz_doc: An *open* fitz.Document object (read-only, for extraction).
        struct_tree: PDFStructureTree instance wrapping ``pdf``. If None a new
            one is created automatically.
        ai_client: Optional AI provider client. Reserved for future use —
            currently ARIA labels are generated purely by regex substitution.
    """

    # Issue types this fixer handles
    HANDLED_ISSUE_TYPES = MATH_ISSUE_TYPES

    def __init__(
        self,
        pdf: Any,
        fitz_doc: Any,
        *,
        struct_tree: Optional[Any] = None,
        ai_client: Optional[Any] = None,
    ) -> None:
        self.pdf = pdf
        self.fitz_doc = fitz_doc
        self.ai_client = ai_client

        if struct_tree is not None:
            self.struct_tree = struct_tree
        else:
            if not HAS_PIKEPDF:
                raise ImportError(
                    "pikepdf is required. Install with: pip install pikepdf"
                )
            from .pdf_structure import PDFStructureTree

            self.struct_tree = PDFStructureTree(pdf)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self, issues: List[Any]) -> List[MathFixResult]:
        """Process a list of math-related RemediationIssue objects.

        Args:
            issues: RemediationIssue instances whose metadata contains
                ``issue_type`` (one of HANDLED_ISSUE_TYPES) and optionally
                ``page_number`` and ``equation_text``.

        Returns:
            A list of MathFixResult objects — one per issue processed.
        """
        if not HAS_LATEX2MATHML:
            logger.warning(
                "latex2mathml is not installed — MathFixer cannot convert equations. "
                "Install with: pip install latex2mathml"
            )
            return [
                MathFixResult(
                    success=False,
                    error="latex2mathml not available",
                    equation_text=(
                        getattr(issue.metadata, "get", lambda k, d=None: d)(
                            "equation_text", ""
                        )
                        if hasattr(issue, "metadata")
                        else ""
                    ),
                )
                for issue in issues
            ]

        if self.struct_tree is None:
            logger.error("MathFixer: struct_tree is required but not set")
            return [
                MathFixResult(success=False, error="struct_tree not available")
                for _ in issues
            ]

        results: List[MathFixResult] = []
        for issue in issues:
            try:
                result = self._fix_math_issue(issue)
            except Exception as exc:
                logger.error(f"MathFixer: unexpected error on issue {issue}: {exc}")
                result = MathFixResult(success=False, error=str(exc))
            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fix_math_issue(self, issue: Any) -> MathFixResult:
        """Handle a single math RemediationIssue."""
        metadata = getattr(issue, "metadata", {}) or {}

        issue_type = metadata.get("issue_type", "math_content_accessibility")
        if issue_type not in self.HANDLED_ISSUE_TYPES:
            return MathFixResult(
                success=False,
                error=f"Unrecognised issue_type: {issue_type!r}",
            )
        if issue_type == IMAGE_EQUATION_ISSUE_TYPE:
            return MathFixResult(
                success=False,
                error="image_equation_pipeline_unavailable",
                page_number=int(metadata.get("page_number", 1) or 1),
            )

        # Determine page number (1-indexed)
        page_number: int = int(metadata.get("page_number", 1) or 1)
        # Clamp to valid range
        page_count = len(self.pdf.pages)
        if page_number < 1:
            page_number = 1
        elif page_number > page_count:
            page_number = page_count

        # Get equation text
        equation_text: str = metadata.get("equation_text", "") or ""
        if not equation_text:
            equation_text = self._extract_equation_from_page(page_number)

        if not equation_text:
            return MathFixResult(
                success=False,
                error="Could not determine equation text",
                page_number=page_number,
            )

        # Convert LaTeX → MathML
        mathml_string = self._convert_to_mathml(equation_text)
        has_mathml = bool(mathml_string)
        if not mathml_string:
            # Fallback: produce minimal MathML with escaped text
            escaped = (
                equation_text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            mathml_string = (
                '<math xmlns="http://www.w3.org/1998/Math/MathML">'
                f"<mtext>{escaped}</mtext>"
                "</math>"
            )

        # Generate human-readable ARIA label
        aria_label = self._generate_aria_label(equation_text)

        # Add Formula structure element to the PDF
        success = self.struct_tree.add_formula(
            page_num=page_number,
            alt_text=aria_label,
            mathml_string=mathml_string,
        )

        return MathFixResult(
            success=success,
            equation_text=equation_text,
            aria_label=aria_label,
            page_number=page_number,
            has_mathml=has_mathml,
        )

    def _convert_to_mathml(self, latex: str) -> str:
        """Convert a LaTeX equation string to MathML.

        Strips display-mode delimiters before passing to latex2mathml.

        Returns empty string on failure (caller applies fallback).
        """
        if not HAS_LATEX2MATHML or latex_to_mathml is None:
            return ""

        # Strip common delimiters
        stripped = latex.strip()
        for delimiter_pair in [("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)")]:
            start, end = delimiter_pair
            if stripped.startswith(start) and stripped.endswith(end):
                stripped = stripped[len(start) : len(stripped) - len(end)].strip()
                break
        if stripped.startswith("$") and stripped.endswith("$") and len(stripped) > 1:
            stripped = stripped[1:-1].strip()

        try:
            return latex_to_mathml(stripped)
        except Exception as exc:
            logger.debug(f"latex2mathml failed for {latex!r}: {exc}")
            return ""

    def _extract_equation_from_page(self, page_num: int) -> str:
        """Attempt to extract a LaTeX equation from the fitz page text.

        Searches for common LaTeX math delimiters in page text extracted
        by PyMuPDF.

        Args:
            page_num: 1-indexed page number.

        Returns:
            The matched equation string (with delimiters), or empty string.
        """
        if not HAS_PYMUPDF or self.fitz_doc is None:
            return ""

        try:
            page = self.fitz_doc[page_num - 1]
            text = page.get_text("text")
        except Exception as exc:
            logger.debug(
                f"MathFixer: could not extract text from page {page_num}: {exc}"
            )
            return ""

        # Try display math first (more specific)
        for pattern in _DISPLAY_MATH_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)

        # Then inline math
        for pattern in _INLINE_MATH_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)

        return ""

    def _generate_aria_label(self, latex: str) -> str:
        """Generate a human-readable ARIA label from a LaTeX equation.

        Applies a series of regex substitutions to translate common LaTeX
        notation into plain English suitable for screen-reader announcement.

        Args:
            latex: LaTeX equation string (may include delimiters).

        Returns:
            Plain-English label string.
        """
        # Strip delimiters
        label = latex.strip()
        for delimiter_pair in [("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)")]:
            start, end = delimiter_pair
            if label.startswith(start) and label.endswith(end):
                label = label[len(start) : len(label) - len(end)].strip()
                break
        if label.startswith("$") and label.endswith("$") and len(label) > 1:
            label = label[1:-1].strip()

        # Apply substitution table
        for pattern, replacement in _ARIA_SUBSTITUTIONS:
            label = pattern.sub(replacement, label)

        label = label.strip()

        # If the result is empty or too short, fall back to "mathematical equation"
        if len(label) < 2:
            label = "mathematical equation"

        return label
