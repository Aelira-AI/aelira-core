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
import hashlib
import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, List, Optional

from src.education.equation_region_contract import PageRasterRegionLocator
from src.education.handwritten_math_suitability import (
    classify_handwritten_math_suitability,
)
from src.education.math_contracts import (
    IMAGE_EQUATION_ISSUE_TYPE,
    MATH_ISSUE_TYPES,
    SCANNED_EQUATION_REGION_ISSUE_TYPE,
)
from src.education.math_speech import (
    equation_alt_text_is_ambiguous,
    generate_equation_alt_text,
    strip_math_delimiters,
)
from src.education.remediation.equation_verifier import (
    RENDERER_DIMENSION_LIMIT,
    EquationVerificationRejected,
)
from src.education.remediation.handwritten_equation_verifier import (
    ALT_TEXT_AMBIGUOUS as HANDWRITTEN_ALT_TEXT_AMBIGUOUS,
    HandwrittenEquationVerificationEvidence,
    HandwrittenEquationVerificationRejected,
)
from src.education.remediation.printed_equation_verifier import (
    SEMANTIC_DISAGREEMENT,
    VERIFIER_DECLINED,
    PrintedEquationConsensusEvidence,
    PrintedEquationVerificationRejected,
    printed_equation_agreement,
)

if TYPE_CHECKING:
    from .equation_image_source import WorkingEquationRegionOccurrence

try:
    from latex2mathml.converter import convert as latex_to_mathml

    HAS_LATEX2MATHML = True
except ImportError:
    HAS_LATEX2MATHML = False
    latex_to_mathml = None  # type: ignore[assignment]

try:
    import pymupdf as fitz  # noqa: F401  # PyMuPDF availability canary (module attrs not used directly)

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf  # noqa: F401

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

logger = logging.getLogger(__name__)

# The target draw already sits inside marked content: a tagged element such as
# the Figure of a tagged source, or an Artifact scope. A Formula cannot be
# written over it.
IMAGE_EQUATION_EXISTING_OWNER_ERROR = "image_equation_existing_owner_unsupported"
# The target draw cannot be matched to exactly one drawing operation in the page
# content (an image inside a Form XObject, a draw count that disagrees with the
# displayed images, or marked content that does not balance). No Formula can be
# written for it and verified afterwards.
IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR = "image_equation_draw_unresolved"
# The plain-language description built from the equation's LaTeX could not be
# trusted (see ``equation_alt_text_is_ambiguous``), so no Formula is staged.
IMAGE_EQUATION_ALT_TEXT_AMBIGUOUS_ERROR = "image_equation_alt_text_ambiguous"
# Manual reasons for the blind second reading of a printed equation.
PRINTED_EQUATION_VERIFIER_UNAVAILABLE_ERROR = "printed_equation_verifier_unavailable"
PRINTED_EQUATION_VERIFIER_FAILED_ERROR = "printed_equation_verifier_failed"
PRINTED_EQUATION_VERIFIER_DECLINED_ERROR = "printed_equation_verifier_declined"
PRINTED_EQUATION_SEMANTIC_DISAGREEMENT_ERROR = "printed_equation_semantic_disagreement"


@dataclass(frozen=True)
class MathVerificationEvidence:
    passed: bool
    source_sha256: str
    rendered_sha256: str
    mathml_sha256: str
    renderer_version: str
    comparator_version: str
    font_sha256: str
    threshold_version: str
    ink_iou: float
    pixel_similarity: float
    required_ink_iou: float
    required_pixel_similarity: float


@dataclass(frozen=True)
class PendingEquationAssociation:
    page_number: int
    image_xref: int
    image_index: int
    occurrence_ordinal: int
    bbox: tuple[float, float, float, float]
    occurrence_id: str
    image_stream_sha256: str
    alt_text: str
    mathml_string: str
    provider_used: Optional[str]
    model_used: Optional[str]
    verification_evidence: (
        MathVerificationEvidence | HandwrittenEquationVerificationEvidence
    )
    # Present for every printed equation: the exact two-reading agreement the
    # saved-file contract must carry. Handwritten equations keep their own
    # consensus inside ``verification_evidence``.
    consensus_evidence: Optional[PrintedEquationConsensusEvidence] = None


@dataclass(frozen=True)
class PendingScannedRegionAssociation:
    """Verified crop awaiting exact clipped marked-content association."""

    locator: PageRasterRegionLocator
    working_occurrence: "WorkingEquationRegionOccurrence"
    normalized_crop_sha256: str
    alt_text: str
    mathml_string: str
    provider_used: Optional[str]
    model_used: Optional[str]
    verification_evidence: (
        MathVerificationEvidence | HandwrittenEquationVerificationEvidence
    )
    consensus_evidence: Optional[PrintedEquationConsensusEvidence] = None

    @property
    def page_number(self) -> int:
        return int(self.working_occurrence.page_number)

    @property
    def image_xref(self) -> int:
        return int(self.working_occurrence.image_xref)

    @property
    def image_index(self) -> int:
        return int(self.working_occurrence.image_index)

    @property
    def occurrence_ordinal(self) -> int:
        return int(self.working_occurrence.occurrence_ordinal)

    @property
    def occurrence_id(self) -> str:
        return str(self.working_occurrence.occurrence_id)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Exact Formula region bbox; never the whole-page source bbox."""
        return self.locator.pdf_bbox

    @property
    def region_bbox(self) -> tuple[float, float, float, float]:
        return self.locator.pdf_bbox

    @property
    def parent_bbox(self) -> tuple[float, float, float, float]:
        return self.locator.parent_bbox

    @property
    def working_parent_bbox(self) -> tuple[float, float, float, float]:
        return tuple(self.working_occurrence.bbox)  # type: ignore[return-value]


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
    source_kind: Optional[str] = None
    fix_method: Optional[str] = None
    confidence: float = 0.0
    needs_review: bool = False
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    verification_evidence: Optional[
        MathVerificationEvidence | HandwrittenEquationVerificationEvidence
    ] = None
    pending_association: Optional[
        PendingEquationAssociation | PendingScannedRegionAssociation
    ] = None
    # True once the specialist itself classified the image as an equation,
    # whether or not the reading was later verified. A generic image
    # description must not stand in for an equation that could not be proven.
    equation_recognized: bool = False
    # The printed-equation second-reading agreement, when one was established.
    consensus_evidence: Optional[PrintedEquationConsensusEvidence] = None


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
        alt_text_client: Optional[Any] = None,
        image_source: Optional[Any] = None,
        region_source: Optional[Any] = None,
        equation_recognizer: Optional[Any] = None,
        equation_verifier: Optional[Any] = None,
        handwritten_recognizer: Optional[Any] = None,
        handwritten_verifier: Optional[Any] = None,
        printed_equation_second_reader: Optional[Any] = None,
    ) -> None:
        self.pdf = pdf
        self.fitz_doc = fitz_doc
        self.ai_client = ai_client
        self.alt_text_client = alt_text_client
        if image_source is None:
            from .equation_image_source import EquationImageSource

            image_source = EquationImageSource()
        if region_source is None:
            from .equation_image_source import EquationRegionSource

            region_source = EquationRegionSource()
        if equation_recognizer is None and alt_text_client is not None:
            from .equation_recognizer import EquationRecognizer

            equation_recognizer = EquationRecognizer(alt_text_client)
        if equation_verifier is None:
            from .equation_verifier import EquationVerifier

            equation_verifier = EquationVerifier()
        if handwritten_recognizer is None and alt_text_client is not None:
            from .handwritten_equation_recognizer import HandwrittenEquationRecognizer

            handwritten_recognizer = HandwrittenEquationRecognizer(alt_text_client)
        if handwritten_verifier is None and alt_text_client is not None:
            from .handwritten_equation_verifier import HandwrittenEquationVerifier

            handwritten_verifier = HandwrittenEquationVerifier(
                alt_text_client, reading_refusal=equation_alt_text_is_ambiguous
            )
        if printed_equation_second_reader is None and alt_text_client is not None:
            from .printed_equation_verifier import PrintedEquationSecondReader

            printed_equation_second_reader = PrintedEquationSecondReader(
                alt_text_client
            )
        self.printed_equation_second_reader = printed_equation_second_reader
        self.image_source = image_source
        self.region_source = region_source
        self.equation_recognizer = equation_recognizer
        self.equation_verifier = equation_verifier
        self.handwritten_recognizer = handwritten_recognizer
        self.handwritten_verifier = handwritten_verifier

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
                logger.error(
                    "MathFixer: unexpected %s while processing an issue",
                    type(exc).__name__,
                )
                result = MathFixResult(success=False, error="math_fix_failed")
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
            return self._prepare_image_equation(metadata)
        if issue_type == SCANNED_EQUATION_REGION_ISSUE_TYPE:
            return self._prepare_scanned_equation_region(metadata)

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

    def _prepare_image_equation(self, metadata: dict[str, Any]) -> MathFixResult:
        page_number = int(metadata.get("page_number", 1) or 1)
        unavailable = self._visual_equation_dependency_error(page_number)
        if unavailable is not None:
            return unavailable
        # Decide ownership and draw resolution before any provider call can
        # spend on an image that the Formula writer is going to refuse. Neither
        # refusal classifies the image, so neither claims it is an equation.
        refusal = self._image_draw_refusal(metadata)
        if refusal is not None:
            return MathFixResult(
                success=False,
                error=refusal,
                page_number=page_number,
            )
        try:
            validated = self.image_source.extract(self.fitz_doc, metadata)
        except Exception:
            return MathFixResult(
                success=False,
                error="equation_image_source_rejected",
                page_number=page_number,
            )
        return self._prepare_validated_equation(
            validated, page_number=page_number, scanned_region=False
        )

    def _image_draw_refusal(self, metadata: dict[str, Any]) -> Optional[str]:
        """The manual reason for a draw the Formula writer cannot take, if any.

        The target draw is refused when any marked content already encloses it,
        and when it cannot be resolved to exactly one drawing
        operation in balanced marked content: the saved-file verification would
        refuse either one after the provider had been paid. A finding with no
        usable occurrence identity is not probed here; source extraction rejects
        it, and the writer and verification stay authoritative.
        """
        from .content_tagger_v2 import image_draw_has_semantic_owner
        from .pdf_image_finding_identity import occurrence_identity

        identity = occurrence_identity(metadata)
        if identity is None:
            return None
        page_number, image_xref, image_index, ordinal, bbox, occurrence_id = identity
        owned = image_draw_has_semantic_owner(
            self.pdf,
            self.fitz_doc,
            page_number=page_number,
            image_xref=image_xref,
            image_index=image_index,
            occurrence_ordinal=ordinal,
            bbox=bbox,
            occurrence_id=occurrence_id,
        )
        if owned is None:
            return IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR
        return IMAGE_EQUATION_EXISTING_OWNER_ERROR if owned else None

    def _prepare_scanned_equation_region(
        self, metadata: dict[str, Any]
    ) -> MathFixResult:
        page_number = int(metadata.get("page_number", 1) or 1)
        unavailable = self._visual_equation_dependency_error(page_number)
        if unavailable is not None:
            return unavailable
        try:
            validated = self.region_source.extract(self.fitz_doc, metadata)
        except Exception:
            return MathFixResult(
                success=False,
                error="equation_region_source_rejected",
                page_number=page_number,
            )
        return self._prepare_validated_equation(
            validated, page_number=page_number, scanned_region=True
        )

    def _visual_equation_dependency_error(
        self, page_number: int
    ) -> Optional[MathFixResult]:
        if self.equation_recognizer is None:
            return MathFixResult(
                success=False,
                error="alt_text_client_unavailable",
                page_number=page_number,
            )
        if self.equation_verifier is None:
            return MathFixResult(
                success=False,
                error="equation_verifier_unavailable",
                page_number=page_number,
            )
        return None

    def _prepare_validated_equation(
        self, validated: Any, *, page_number: int, scanned_region: bool
    ) -> MathFixResult:
        """Route one source-bound raster without disturbing printed success."""
        if self.equation_recognizer is None:
            return MathFixResult(
                success=False,
                error="alt_text_client_unavailable",
                page_number=page_number,
            )
        if self.equation_verifier is None:
            return MathFixResult(
                success=False,
                error="equation_verifier_unavailable",
                page_number=page_number,
            )
        try:
            recognition = self.equation_recognizer.recognize(validated)
        except Exception:
            return MathFixResult(
                success=False,
                error="equation_recognition_rejected",
                page_number=page_number,
            )
        if recognition.classification == "printed_equation" and recognition.latex:
            return replace(
                self._prepare_printed_equation(
                    validated,
                    recognition,
                    page_number=page_number,
                    scanned_region=scanned_region,
                ),
                equation_recognized=True,
            )
        if recognition.classification != "not_equation":
            return MathFixResult(
                success=False,
                error="not_printed_equation",
                page_number=page_number,
                equation_recognized=recognition.classification == "printed_equation",
            )
        return self._prepare_handwritten_equation(
            validated,
            page_number=page_number,
            scanned_region=scanned_region,
        )

    def _prepare_printed_equation(
        self,
        validated: Any,
        recognition: Any,
        *,
        page_number: int,
        scanned_region: bool,
    ) -> MathFixResult:
        """Preserve the existing printed-equation verification path."""
        try:
            verification = self.equation_verifier.verify(validated, recognition.latex)
        except EquationVerificationRejected as exc:
            return MathFixResult(
                success=False,
                error=(
                    "equation_too_large_for_verification"
                    if str(exc) == RENDERER_DIMENSION_LIMIT
                    else "equation_verification_failed"
                ),
                page_number=page_number,
            )
        except Exception:
            return MathFixResult(
                success=False,
                error="equation_verification_failed",
                page_number=page_number,
            )
        if getattr(verification, "passed", False) is not True:
            return MathFixResult(
                success=False,
                error="equation_verification_failed",
                page_number=page_number,
            )
        mathml_string = self._convert_to_mathml(recognition.latex)
        if not mathml_string:
            return MathFixResult(
                success=False,
                error="image_equation_conversion_failed",
                page_number=page_number,
            )
        canonicalize = getattr(self.equation_verifier, "canonicalize_mathml", None)
        if callable(canonicalize):
            try:
                canonical_mathml = canonicalize(mathml_string)
            except Exception:
                return MathFixResult(
                    success=False,
                    error="image_equation_conversion_failed",
                    page_number=page_number,
                )
            if not isinstance(canonical_mathml, str) or not canonical_mathml:
                return MathFixResult(
                    success=False,
                    error="image_equation_conversion_failed",
                    page_number=page_number,
                )
            mathml_string = canonical_mathml
        expected_mathml_sha256 = getattr(verification, "mathml_sha256", None)
        actual_mathml_sha256 = hashlib.sha256(mathml_string.encode("utf-8")).hexdigest()
        if expected_mathml_sha256 != actual_mathml_sha256:
            return MathFixResult(
                success=False,
                error="equation_verification_mismatch",
                page_number=page_number,
            )
        evidence = self._bounded_verification_evidence(verification)
        if evidence is None or evidence.source_sha256 != validated.normalized_sha256:
            return MathFixResult(
                success=False,
                error="equation_verification_mismatch",
                page_number=page_number,
            )
        refusal, consensus = self._printed_equation_second_reading(
            validated,
            recognition,
            staged_mathml_sha256=actual_mathml_sha256,
            page_number=page_number,
        )
        if refusal is not None:
            return refusal
        aria_label = self._generate_aria_label(recognition.latex)
        identity = validated.identity
        if scanned_region:
            if not isinstance(identity, PageRasterRegionLocator):
                return MathFixResult(
                    success=False,
                    error="equation_region_source_rejected",
                    page_number=page_number,
                )
            pending: PendingEquationAssociation | PendingScannedRegionAssociation = (
                PendingScannedRegionAssociation(
                    locator=identity,
                    working_occurrence=validated.working_occurrence,
                    normalized_crop_sha256=validated.normalized_sha256,
                    alt_text=aria_label,
                    mathml_string=mathml_string,
                    provider_used=getattr(recognition, "provider", None),
                    model_used=getattr(recognition, "model", None),
                    verification_evidence=evidence,
                    consensus_evidence=consensus,
                )
            )
            pending_error = "scanned_equation_region_association_pending"
        else:
            pending = PendingEquationAssociation(
                page_number=identity.page_number,
                image_xref=identity.image_xref,
                image_index=identity.image_index,
                occurrence_ordinal=identity.occurrence_ordinal,
                bbox=identity.bbox,
                occurrence_id=identity.occurrence_id,
                image_stream_sha256=validated.source_sha256,
                alt_text=aria_label,
                mathml_string=mathml_string,
                provider_used=getattr(recognition, "provider", None),
                model_used=getattr(recognition, "model", None),
                verification_evidence=evidence,
                consensus_evidence=consensus,
            )
            pending_error = "image_equation_association_pending"
        return MathFixResult(
            success=False,
            error=pending_error,
            equation_text=recognition.latex,
            aria_label=aria_label,
            page_number=page_number,
            has_mathml=True,
            source_kind="image_equation",
            fix_method="ai_vision",
            confidence=0.55,
            needs_review=True,
            provider_used=getattr(recognition, "provider", None),
            model_used=getattr(recognition, "model", None),
            verification_evidence=evidence,
            pending_association=pending,
            consensus_evidence=consensus,
        )

    def _printed_equation_second_reading(
        self,
        validated: Any,
        recognition: Any,
        *,
        staged_mathml_sha256: str,
        page_number: int,
    ) -> tuple[Optional[MathFixResult], Optional[PrintedEquationConsensusEvidence]]:
        """Require a blind second reading that agrees exactly with the first.

        Runs only after the visual comparison passed, so a reading the image
        already refuted costs no second provider call. The description is
        checked first for the same reason, and again for the second reading's
        LaTeX once the two are known to agree.
        """

        def refuse(error: str) -> tuple[MathFixResult, None]:
            return (
                MathFixResult(success=False, error=error, page_number=page_number),
                None,
            )

        if equation_alt_text_is_ambiguous(recognition.latex):
            return refuse(IMAGE_EQUATION_ALT_TEXT_AMBIGUOUS_ERROR)
        reader = self.printed_equation_second_reader
        if reader is None:
            return refuse(PRINTED_EQUATION_VERIFIER_UNAVAILABLE_ERROR)
        try:
            second = reader.read(validated)
            consensus = printed_equation_agreement(recognition, second)
        except PrintedEquationVerificationRejected as exc:
            if str(exc) == VERIFIER_DECLINED:
                return refuse(PRINTED_EQUATION_VERIFIER_DECLINED_ERROR)
            if str(exc) == SEMANTIC_DISAGREEMENT:
                return refuse(PRINTED_EQUATION_SEMANTIC_DISAGREEMENT_ERROR)
            return refuse(PRINTED_EQUATION_VERIFIER_FAILED_ERROR)
        except Exception:
            return refuse(PRINTED_EQUATION_VERIFIER_FAILED_ERROR)
        if (
            not isinstance(consensus, PrintedEquationConsensusEvidence)
            or consensus.passed is not True
            or consensus.source_sha256 != validated.normalized_sha256
            or consensus.mathml_sha256 != staged_mathml_sha256
        ):
            return refuse("equation_verification_mismatch")
        if equation_alt_text_is_ambiguous(second.latex):
            return refuse(IMAGE_EQUATION_ALT_TEXT_AMBIGUOUS_ERROR)
        return None, consensus

    def _prepare_handwritten_equation(
        self, validated: Any, *, page_number: int, scanned_region: bool
    ) -> MathFixResult:
        """Recognize eligible handwriting and require exact semantic agreement."""
        if self.handwritten_recognizer is None or self.handwritten_verifier is None:
            return MathFixResult(
                success=False,
                error="hmer_unavailable",
                page_number=page_number,
            )
        try:
            suitability = classify_handwritten_math_suitability(validated.jpeg_bytes)
        except Exception:
            return MathFixResult(
                success=False,
                error="handwritten_math_suitability_rejected",
                page_number=page_number,
            )
        if suitability.disposition != "eligible":
            return MathFixResult(
                success=False,
                error="handwritten_math_not_eligible",
                page_number=page_number,
            )
        try:
            recognition = self.handwritten_recognizer.recognize(validated, suitability)
        except Exception:
            return MathFixResult(
                success=False,
                error="handwritten_equation_recognition_rejected",
                page_number=page_number,
            )
        if recognition.classification == "unsupported_notation":
            return MathFixResult(
                success=False,
                error="handwritten_notation_unsupported",
                page_number=page_number,
                equation_recognized=True,
            )
        if (
            recognition.classification != "handwritten_equation"
            or not recognition.latex
        ):
            return MathFixResult(
                success=False,
                error="not_handwritten_equation",
                page_number=page_number,
                equation_recognized=recognition.classification
                == "handwritten_equation",
            )
        return replace(
            self._verify_handwritten_equation(
                validated,
                suitability,
                recognition,
                page_number=page_number,
                scanned_region=scanned_region,
            ),
            equation_recognized=True,
        )

    def _verify_handwritten_equation(
        self,
        validated: Any,
        suitability: Any,
        recognition: Any,
        *,
        page_number: int,
        scanned_region: bool,
    ) -> MathFixResult:
        """Verify one recognized handwritten equation by exact agreement."""
        try:
            verification = self.handwritten_verifier.verify(
                validated, suitability, recognition
            )
        except HandwrittenEquationVerificationRejected as exc:
            return MathFixResult(
                success=False,
                error=(
                    IMAGE_EQUATION_ALT_TEXT_AMBIGUOUS_ERROR
                    if str(exc) == HANDWRITTEN_ALT_TEXT_AMBIGUOUS
                    else "handwritten_equation_verification_failed"
                ),
                page_number=page_number,
            )
        except Exception:
            return MathFixResult(
                success=False,
                error="handwritten_equation_verification_failed",
                page_number=page_number,
            )
        if (
            not isinstance(verification, HandwrittenEquationVerificationEvidence)
            or verification.passed is not True
            or verification.source_sha256 != validated.normalized_sha256
            or verification.suitability_evidence != suitability
        ):
            return MathFixResult(
                success=False,
                error="handwritten_equation_verification_mismatch",
                page_number=page_number,
            )
        mathml_string = self._convert_to_mathml(recognition.latex)
        if not mathml_string:
            return MathFixResult(
                success=False,
                error="image_equation_conversion_failed",
                page_number=page_number,
            )
        canonicalize = getattr(self.equation_verifier, "canonicalize_mathml", None)
        if not callable(canonicalize):
            return MathFixResult(
                success=False,
                error="image_equation_conversion_failed",
                page_number=page_number,
            )
        try:
            mathml_string = canonicalize(mathml_string)
        except Exception:
            return MathFixResult(
                success=False,
                error="image_equation_conversion_failed",
                page_number=page_number,
            )
        if (
            hashlib.sha256(mathml_string.encode("utf-8")).hexdigest()
            != verification.mathml_sha256
        ):
            return MathFixResult(
                success=False,
                error="handwritten_equation_verification_mismatch",
                page_number=page_number,
            )
        # The two readings agree, so the equation itself is established. Its
        # description is still built by regular expressions, and ``\frac12``
        # would read "12", so an agreed reading whose notation the label cannot
        # translate is refused here, before anything is staged. The verifier
        # has already applied the same check to its own LaTeX.
        if equation_alt_text_is_ambiguous(recognition.latex):
            return MathFixResult(
                success=False,
                error=IMAGE_EQUATION_ALT_TEXT_AMBIGUOUS_ERROR,
                page_number=page_number,
            )
        aria_label = self._generate_aria_label(recognition.latex)
        pending = self._pending_visual_equation_association(
            validated,
            scanned_region=scanned_region,
            aria_label=aria_label,
            mathml_string=mathml_string,
            provider_used=recognition.provider,
            model_used=recognition.model,
            verification_evidence=verification,
            page_number=page_number,
        )
        if pending is None:
            return MathFixResult(
                success=False,
                error=(
                    "equation_region_source_rejected"
                    if scanned_region
                    else "equation_image_source_rejected"
                ),
                page_number=page_number,
            )
        pending_error = (
            "scanned_equation_region_association_pending"
            if scanned_region
            else "image_equation_association_pending"
        )
        return MathFixResult(
            success=False,
            error=pending_error,
            equation_text=recognition.latex,
            aria_label=aria_label,
            page_number=page_number,
            has_mathml=True,
            source_kind="image_equation",
            fix_method="ai_vision",
            confidence=0.55,
            needs_review=True,
            provider_used=recognition.provider,
            model_used=recognition.model,
            verification_evidence=verification,
            pending_association=pending,
        )

    @staticmethod
    def _pending_visual_equation_association(
        validated: Any,
        *,
        scanned_region: bool,
        aria_label: str,
        mathml_string: str,
        provider_used: str | None,
        model_used: str | None,
        verification_evidence: (
            MathVerificationEvidence | HandwrittenEquationVerificationEvidence
        ),
        page_number: int,
    ) -> PendingEquationAssociation | PendingScannedRegionAssociation | None:
        identity = validated.identity
        if scanned_region:
            if not isinstance(identity, PageRasterRegionLocator):
                return None
            return PendingScannedRegionAssociation(
                locator=identity,
                working_occurrence=validated.working_occurrence,
                normalized_crop_sha256=validated.normalized_sha256,
                alt_text=aria_label,
                mathml_string=mathml_string,
                provider_used=provider_used,
                model_used=model_used,
                verification_evidence=verification_evidence,
            )
        return PendingEquationAssociation(
            page_number=identity.page_number,
            image_xref=identity.image_xref,
            image_index=identity.image_index,
            occurrence_ordinal=identity.occurrence_ordinal,
            bbox=identity.bbox,
            occurrence_id=identity.occurrence_id,
            image_stream_sha256=validated.source_sha256,
            alt_text=aria_label,
            mathml_string=mathml_string,
            provider_used=provider_used,
            model_used=model_used,
            verification_evidence=verification_evidence,
        )

    @staticmethod
    def _bounded_verification_evidence(
        verification: Any,
    ) -> Optional[MathVerificationEvidence]:
        hash_names = (
            "source_sha256",
            "rendered_sha256",
            "mathml_sha256",
            "font_sha256",
        )
        version_names = (
            "renderer_version",
            "comparator_version",
            "threshold_version",
        )
        metric_names = (
            "ink_iou",
            "pixel_similarity",
            "required_ink_iou",
            "required_pixel_similarity",
        )
        hashes = {name: getattr(verification, name, None) for name in hash_names}
        versions = {name: getattr(verification, name, None) for name in version_names}
        metrics = {name: getattr(verification, name, None) for name in metric_names}
        if getattr(verification, "passed", None) is not True:
            return None
        if any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in hashes.values()
        ):
            return None
        if any(
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or not value.isprintable()
            for value in versions.values()
        ):
            return None
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
            or float(value) > 1.0
            for value in metrics.values()
        ):
            return None
        return MathVerificationEvidence(
            passed=True,
            source_sha256=str(hashes["source_sha256"]),
            rendered_sha256=str(hashes["rendered_sha256"]),
            mathml_sha256=str(hashes["mathml_sha256"]),
            font_sha256=str(hashes["font_sha256"]),
            renderer_version=str(versions["renderer_version"]),
            comparator_version=str(versions["comparator_version"]),
            threshold_version=str(versions["threshold_version"]),
            ink_iou=float(metrics["ink_iou"]),
            pixel_similarity=float(metrics["pixel_similarity"]),
            required_ink_iou=float(metrics["required_ink_iou"]),
            required_pixel_similarity=float(metrics["required_pixel_similarity"]),
        )

    def _convert_to_mathml(self, latex: str) -> str:
        """Convert a LaTeX equation string to MathML.

        Strips display-mode delimiters before passing to latex2mathml.

        Returns empty string on failure (caller applies fallback).
        """
        if not HAS_LATEX2MATHML or latex_to_mathml is None:
            return ""

        stripped = strip_math_delimiters(latex)

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
        return generate_equation_alt_text(latex)
