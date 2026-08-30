"""
Base Remediation Framework for Aelira Auto-Remediation Engine.

This module provides the foundation for automatically fixing accessibility issues
in various document types (PDF, Word, PowerPoint, Excel, HTML).

The remediation system follows a consistent pattern:
1. Analyze scan results to identify fixable issues
2. Generate appropriate fixes (often using AI)
3. Apply fixes to the document
4. Verify fixes were applied correctly
5. Return results with fixed and manual issues separated
"""

import os
import uuid
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from src.education.equation_region_contract import PageRasterRegionLocator
from src.education.visual_semantic_contract import (
    CommutativeDiagramPdfContract,
    CommutativeDiagramRecognitionEvidenceV1,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    HandwrittenEquationConsensusEvidenceV1,
    HandwrittenEquationContract,
    PrintedEquationRoundtripEvidenceV1,
    PrintedEquationContract,
    VisualSemanticContract,
)
from src.education.handwritten_math_suitability import (
    POLICY_SHA256 as HANDWRITTEN_SUITABILITY_POLICY_SHA256,
    HandwrittenMathSuitabilityEvidence,
)
from src.education.handwritten_equation_policy import (
    HANDWRITTEN_VERIFIER_POLICY_SHA256,
    HANDWRITTEN_VERIFIER_POLICY_VERSION,
)
from src.education.math_contracts import (
    MATH_ISSUE_TYPES,
    SCANNED_EQUATION_REGION_ISSUE_TYPE,
)

try:
    from .output_claim import DescriptorBoundOutputClaim
except ImportError:
    if __package__:
        raise
    from src.education.remediation.output_claim import DescriptorBoundOutputClaim

ALT_TEXT_CATEGORY_ALIASES = frozenset(
    {
        "alt_text",
        "alternative_text",
        "image",
        "image_alt",
        "image_alt_text",
        "image_description",
        "image_of_text",
        "missing_alt_text",
        "missing_figure_caption",
        "missing_image_description",
        "area_alt",
        "figure_alt",
        "input_image_alt",
        "object_alt",
        "role_img_alt",
        "svg_img_alt",
    }
)

CHART_CATEGORY_ALIASES = frozenset(
    {
        "chart",
        "chart_alt_text",
        "chart_description",
        "missing_chart_description",
    }
)

VISUAL_DESCRIPTION_CATEGORY_ALIASES = ALT_TEXT_CATEGORY_ALIASES | CHART_CATEGORY_ALIASES


def normalize_category_alias(value: object) -> str:
    """Normalize scanner category spelling before exact alias lookup."""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


logger = logging.getLogger(__name__)


class IssueSeverity(str, Enum):
    """Severity levels for accessibility issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueCategory(str, Enum):
    """Categories of accessibility issues."""

    ALT_TEXT = "alt_text"
    HEADING = "heading"
    CONTRAST = "contrast"
    TABLE = "table"
    LINK = "link"
    LIST = "list"
    LANGUAGE = "language"
    READING_ORDER = "reading_order"
    FORM = "form"
    ARIA = "aria"
    NAVIGATION = "navigation"
    STRUCTURE = "structure"
    COLOR = "color"
    CHART = "chart"
    SHEET = "sheet"
    TITLE = "title"  # Document title issues
    OTHER = "other"


_CATEGORY_FIELD_NAMES = (
    "category",
    "type",
    "issue_type",
    "rule",
    "rule_id",
    "id",
    "axe_id",
    "axe_rule_id",
    "wcag_criteria",
    "wcag_criterion",
    "wcag",
)

_CATEGORY_ALIASES = {
    **{alias: IssueCategory.ALT_TEXT for alias in ALT_TEXT_CATEGORY_ALIASES},
    **{alias: IssueCategory.CHART for alias in CHART_CATEGORY_ALIASES},
    "heading": IssueCategory.HEADING,
    "heading_structure": IssueCategory.HEADING,
    "contrast": IssueCategory.CONTRAST,
    "color_contrast": IssueCategory.CONTRAST,
    "table": IssueCategory.TABLE,
    "table_header": IssueCategory.TABLE,
    "link": IssueCategory.LINK,
    "hyperlink": IssueCategory.LINK,
    "list": IssueCategory.LIST,
    "list_structure": IssueCategory.LIST,
    "language": IssueCategory.LANGUAGE,
    "missing_language": IssueCategory.LANGUAGE,
    "missing_lang": IssueCategory.LANGUAGE,
    "reading_order": IssueCategory.READING_ORDER,
    "form": IssueCategory.FORM,
    "aria": IssueCategory.ARIA,
    "navigation": IssueCategory.NAVIGATION,
    "bookmark": IssueCategory.NAVIGATION,
    "bookmarks": IssueCategory.NAVIGATION,
    "outline": IssueCategory.NAVIGATION,
    "structure": IssueCategory.STRUCTURE,
    "structure_tree": IssueCategory.STRUCTURE,
    "tagged": IssueCategory.STRUCTURE,
    "color": IssueCategory.COLOR,
    "sheet": IssueCategory.SHEET,
    "sheet_name": IssueCategory.SHEET,
    "title": IssueCategory.TITLE,
    "document_title": IssueCategory.TITLE,
    "font_size": IssueCategory.STRUCTURE,
    SCANNED_EQUATION_REGION_ISSUE_TYPE: IssueCategory.STRUCTURE,
    "1.1.1": IssueCategory.ALT_TEXT,
    "1.3.1": IssueCategory.STRUCTURE,
    "1.4.3": IssueCategory.CONTRAST,
    **{issue_type: IssueCategory.STRUCTURE for issue_type in MATH_ISSUE_TYPES},
}


@dataclass(frozen=True)
class IssueCategoryClassification:
    """Pure classification result shared by every remediation entry point."""

    category: IssueCategory
    manual_reason: Optional[str] = None


def classify_issue_category(
    issue: Dict[str, Any], *, authoritative: bool
) -> IssueCategoryClassification:
    """Classify all category-bearing fields without precedence bypasses.

    Visual-description aliases always win because they select the least-
    privileged client. Authoritative callers fail closed when non-visual fields
    disagree or semantic category fields contain unknown values.
    """
    values: List[tuple[str, str]] = []
    metadata = issue.get("metadata")
    sources: tuple[Dict[str, Any], ...] = (
        issue,
        metadata if isinstance(metadata, dict) else {},
    )
    if any(
        source.get("issue_type") == SCANNED_EQUATION_REGION_ISSUE_TYPE
        for source in sources
    ):
        return IssueCategoryClassification(category=IssueCategory.STRUCTURE)
    for source in sources:
        for field in _CATEGORY_FIELD_NAMES:
            value = source.get(field)
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            normalized = normalize_category_alias(value)
            if normalized:
                values.append((field, normalized))

    known = {
        _CATEGORY_ALIASES[value] for _, value in values if value in _CATEGORY_ALIASES
    }
    visual = known & {IssueCategory.ALT_TEXT, IssueCategory.CHART}
    if visual:
        category = (
            IssueCategory.CHART
            if IssueCategory.CHART in visual
            else IssueCategory.ALT_TEXT
        )
        return IssueCategoryClassification(category=category)

    nonvisual = known - {IssueCategory.OTHER}
    semantic_unknown = {
        value
        for field, value in values
        if field in {"category", "type", "issue_type", "rule", "rule_id"}
        and value not in _CATEGORY_ALIASES
        and not value.startswith("wcag_")
    }
    if authoritative and len(nonvisual) > 1:
        return IssueCategoryClassification(
            category=IssueCategory.OTHER,
            manual_reason="conflicting_issue_categories",
        )
    if authoritative and (
        len(semantic_unknown) > 1 or (semantic_unknown and nonvisual)
    ):
        return IssueCategoryClassification(
            category=IssueCategory.OTHER,
            manual_reason="ambiguous_issue_category",
        )
    if nonvisual:
        # Legacy callers preserve the historical first-recognized precedence.
        for _, value in values:
            category = _CATEGORY_ALIASES.get(value)
            if category in nonvisual:
                assert category is not None
                return IssueCategoryClassification(category=category)
    return IssueCategoryClassification(category=IssueCategory.OTHER)


class FixStatus(str, Enum):
    """Status of a fix attempt."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class OutputFormat(str, Enum):
    """Output format options for remediated files."""

    # LaTeX options
    TEX = "tex"  # Original LaTeX source
    PDF = "pdf"  # Compiled PDF
    HTML = "html"  # Converted to HTML

    # Multimedia options
    INDIVIDUAL = "individual"  # Separate companion files
    ZIP = "zip"  # All files in ZIP archive

    # Common
    ORIGINAL = "original"  # Same format as input


class RemediationIssue(BaseModel):
    """Represents an accessibility issue to be remediated."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: IssueCategory
    severity: IssueSeverity
    description: str
    location: Optional[str] = None  # Page, slide, sheet, element path
    element_type: Optional[str] = None  # img, heading, table, etc.
    original_content: Optional[str] = None
    wcag_criteria: Optional[str] = None
    can_auto_fix: bool = False
    fix_suggestion: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationEvidence(BaseModel):
    """Allowlisted, bounded evidence safe for durable review records."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    source_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    rendered_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    mathml_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    renderer_version: str = Field(min_length=1, max_length=128)
    comparator_version: str = Field(min_length=1, max_length=128)
    font_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    threshold_version: str = Field(min_length=1, max_length=128)
    ink_iou: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    pixel_similarity: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    required_ink_iou: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    required_pixel_similarity: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @field_validator("renderer_version", "comparator_version", "threshold_version")
    @classmethod
    def _printable_version(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("evidence version must be bounded printable text")
        return value


class HandwrittenVerificationEvidence(BaseModel):
    """Allowlisted HMER consensus evidence safe for durable review records."""

    model_config = ConfigDict(extra="forbid")

    passed: Literal[True]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    suitability_evidence: HandwrittenMathSuitabilityEvidence
    suitability_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    suitability_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_policy_version: Literal["handwritten-equation-consensus-v1"]
    verifier_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agreement_count: Literal[2]
    required_agreement_count: Literal[2]
    mathml_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_mathml_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_mathml_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_latex_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_latex_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_provider: str = Field(min_length=1, max_length=200)
    primary_model: str = Field(min_length=1, max_length=200)
    verifier_provider: str = Field(min_length=1, max_length=200)
    verifier_model: str = Field(min_length=1, max_length=200)

    @field_validator(
        "primary_provider", "primary_model", "verifier_provider", "verifier_model"
    )
    @classmethod
    def _bounded_identities(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("HMER identities must be bounded printable text")
        return value

    @model_validator(mode="after")
    def _exact_hmer_identity(self) -> "HandwrittenVerificationEvidence":
        if (
            self.suitability_policy_sha256 != HANDWRITTEN_SUITABILITY_POLICY_SHA256
            or self.suitability_evidence.policy_sha256
            != HANDWRITTEN_SUITABILITY_POLICY_SHA256
            or self.suitability_evidence.disposition != "eligible"
            or self.suitability_evidence_sha256
            != self.suitability_evidence.evidence_sha256
            or self.source_sha256 != self.suitability_evidence.source_sha256
        ):
            raise ValueError("HMER suitability identity is invalid")
        if (
            self.verifier_policy_version != HANDWRITTEN_VERIFIER_POLICY_VERSION
            or self.verifier_policy_sha256 != HANDWRITTEN_VERIFIER_POLICY_SHA256
        ):
            raise ValueError("HMER verifier policy identity is invalid")
        if (
            self.mathml_sha256 != self.primary_mathml_sha256
            or self.mathml_sha256 != self.verifier_mathml_sha256
        ):
            raise ValueError("HMER semantic readings do not agree")
        return self


class FixedIssue(BaseModel):
    """Represents a successfully fixed issue."""

    issue_id: str
    category: IssueCategory
    severity: IssueSeverity
    description: str
    location: Optional[str] = None
    original_content: Optional[str] = None
    fixed_content: str
    fix_method: str  # "rule", "heuristic", "ai_text", "ai_vision"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    needs_review: bool = False
    provider_used: Optional[str] = Field(default=None, min_length=1, max_length=64)
    model_used: Optional[str] = Field(default=None, min_length=1, max_length=50)
    source_kind: Optional[str] = Field(default=None, min_length=1, max_length=32)
    source_locator: Optional[PageRasterRegionLocator] = None
    verification_evidence: Optional[
        VerificationEvidence
        | CommutativeDiagramRecognitionEvidenceV1
        | HandwrittenVerificationEvidence
    ] = None
    visual_semantic_contract: Optional[VisualSemanticContract] = None
    verification_passed: bool = True
    notes: Optional[str] = None
    wcag_criteria: Optional[str] = None
    page_number: Optional[int] = None

    @model_validator(mode="after")
    def _region_locator_requires_image_equation_source(self) -> "FixedIssue":
        if self.source_locator is not None and self.source_kind not in {
            "image_equation",
            "commutative_diagram",
        }:
            raise ValueError(
                "page raster region locator requires image_equation or "
                "commutative_diagram source"
            )
        contract = self.visual_semantic_contract
        if contract is None:
            return self
        expected_fixed_content = (
            contract.semantic_output.description.summary
            if isinstance(contract, CommutativeDiagramPdfContract)
            else contract.semantic_output.alt_text
        )
        expected_source_kind = (
            "commutative_diagram"
            if isinstance(contract, CommutativeDiagramPdfContract)
            else "image_equation"
        )
        if (
            self.source_kind != expected_source_kind
            or self.provider_used is None
            or self.model_used is None
            or self.verification_evidence is None
            or self.page_number is None
            or self.verification_passed is not True
            or self.fixed_content != expected_fixed_content
            or self.page_number != contract.locator.page_number
        ):
            raise ValueError(
                "visual semantic contract requires a complete visual-fix envelope"
            )

        if isinstance(contract, CommutativeDiagramPdfContract):
            contract_evidence = next(
                (
                    evidence
                    for evidence in contract.verification_evidence
                    if isinstance(evidence, CommutativeDiagramRecognitionEvidenceV1)
                ),
                None,
            )
            if (
                contract_evidence is None
                or not isinstance(
                    self.verification_evidence,
                    CommutativeDiagramRecognitionEvidenceV1,
                )
                or self.verification_evidence != contract_evidence
            ):
                raise ValueError(
                    "visual semantic contract evidence does not match recognition"
                )
        elif isinstance(contract, PrintedEquationContract):
            contract_evidence = next(
                (
                    evidence
                    for evidence in contract.verification_evidence
                    if isinstance(evidence, PrintedEquationRoundtripEvidenceV1)
                ),
                None,
            )
            expected_type = VerificationEvidence
        elif isinstance(contract, HandwrittenEquationContract):
            contract_evidence = next(
                (
                    evidence
                    for evidence in contract.verification_evidence
                    if isinstance(evidence, HandwrittenEquationConsensusEvidenceV1)
                ),
                None,
            )
            expected_type = HandwrittenVerificationEvidence
        else:
            raise ValueError("unsupported visual semantic contract")
        if not isinstance(contract, CommutativeDiagramPdfContract) and (
            contract_evidence is None
            or not isinstance(self.verification_evidence, expected_type)
            or self.verification_evidence.model_dump(mode="json")
            != contract_evidence.model_dump(mode="json", exclude={"evidence_kind"})
        ):
            raise ValueError(
                "visual semantic contract evidence does not match legacy evidence"
            )

        if isinstance(contract.locator, FrozenPageRasterRegionLocator):
            if self.source_locator is None or self.source_locator.model_dump(
                mode="json"
            ) != contract.locator.model_dump(mode="json"):
                raise ValueError(
                    "visual semantic contract locator does not match legacy locator"
                )
        elif isinstance(contract.locator, EmbeddedImageOccurrenceLocator):
            if self.source_locator is not None:
                raise ValueError(
                    "embedded visual semantic contracts cannot use a region locator"
                )
        else:
            raise ValueError("unsupported visual semantic contract locator")
        return self


class ManualIssue(BaseModel):
    """Represents an issue that requires manual intervention."""

    issue_id: str
    category: IssueCategory
    severity: IssueSeverity
    description: str
    location: Optional[str] = None
    reason: str  # Why it couldn't be auto-fixed
    recommendation: str  # What the user should do
    wcag_criteria: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def materialize_manual_issues(
    issues: List[Dict[str, Any]],
    *,
    reason: str,
    purpose: str,
) -> List[ManualIssue]:
    """Create one safe manual record per raw issue node without deduplication."""
    records: List[ManualIssue] = []
    severity_map = {
        "critical": IssueSeverity.CRITICAL,
        "high": IssueSeverity.HIGH,
        "medium": IssueSeverity.MEDIUM,
        "low": IssueSeverity.LOW,
        "error": IssueSeverity.HIGH,
        "warning": IssueSeverity.MEDIUM,
        "info": IssueSeverity.LOW,
    }
    for issue_index, issue in enumerate(issues):
        classification = classify_issue_category(issue, authoritative=True)
        nodes = issue.get("nodes")
        node_count = max(1, len(nodes)) if isinstance(nodes, list) else 1
        raw_id = str(issue.get("id") or f"issue-{issue_index}")
        description = str(
            issue.get("description") or issue.get("message") or "Accessibility issue"
        )
        issue_reason = classification.manual_reason or reason
        issue_purpose = (
            "alt_text"
            if classification.category in {IssueCategory.ALT_TEXT, IssueCategory.CHART}
            else purpose
        )
        severity = severity_map.get(
            normalize_category_alias(issue.get("severity", "medium")),
            IssueSeverity.MEDIUM,
        )
        retained_metadata: Dict[str, Any] = {}
        source_metadata = issue.get("metadata")
        if (
            isinstance(source_metadata, dict)
            and source_metadata.get("issue_type") == SCANNED_EQUATION_REGION_ISSUE_TYPE
        ):
            retained_metadata = _bounded_scanned_region_metadata(source_metadata)
        for node_index in range(node_count):
            records.append(
                ManualIssue(
                    issue_id=(
                        f"{raw_id}:node:{node_index}" if node_count > 1 else raw_id
                    ),
                    category=classification.category,
                    severity=severity,
                    description=description,
                    location=issue.get("location"),
                    reason=issue_reason,
                    recommendation="Review and remediate this issue manually.",
                    wcag_criteria=issue.get("wcag_criteria") or issue.get("wcag"),
                    metadata={
                        "purpose": issue_purpose,
                        "node_index": node_index,
                        **retained_metadata,
                    },
                )
            )
    return records


def _bounded_scanned_region_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Retain only bounded provenance needed to remediate one scan region."""
    result: Dict[str, Any] = {
        "issue_type": SCANNED_EQUATION_REGION_ISSUE_TYPE,
    }
    text_fields = (
        "source_kind",
        "parent_occurrence_id",
        "region_id",
        "source_sha256",
        "crop_pixel_sha256",
        "detector_version",
        "threshold_version",
        "ocr_engine_version",
        "ocr_tessdata_sha256",
        "ocr_language",
        "ocr_config",
    )
    for field in text_fields:
        value = metadata.get(field)
        if isinstance(value, str) and 0 < len(value) <= 128:
            result[field] = value
    integer_fields = (
        "page_number",
        "image_xref",
        "image_index",
        "occurrence_ordinal",
        "source_width",
        "source_height",
    )
    for field in integer_fields:
        value = metadata.get(field)
        if type(value) is int and 0 <= value <= 25_000_000:
            result[field] = value
    for field, expected_length in (
        ("pixel_bbox", 4),
        ("pdf_bbox", 4),
        ("parent_bbox", 4),
        ("transform", 6),
    ):
        value = metadata.get(field)
        if not isinstance(value, (list, tuple)) or len(value) != expected_length:
            continue
        if all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            and abs(float(item)) <= 25_000_000
            for item in value
        ):
            result[field] = list(value)
    return result


def merge_partitioned_manual_issues(
    result: Any,
    issues: List[Dict[str, Any]],
    *,
    reason: str,
    purpose: str,
) -> None:
    """Merge partitioned work while preserving the result accounting invariant."""
    records = materialize_manual_issues(issues, reason=reason, purpose=purpose)
    if not records:
        return
    if not hasattr(result, "manual_issues"):
        result.manual_issues = []
    if not hasattr(result, "total_issues"):
        result.total_issues = sum(
            int(getattr(result, field, 0) or 0)
            for field in (
                "fixed_count",
                "manual_count",
                "failed_count",
                "skipped_count",
            )
        )
    result.manual_issues.extend(records)
    result.manual_count += len(records)
    result.total_issues += len(records)


class RemediationConfig(BaseModel):
    """Configuration options for remediation."""

    use_ai: bool = True  # Use AI for generating fixes
    # Legacy nested helpers can acquire the global manager. Authoritative LMS
    # entry points disable that path until explicit client injection lands.
    allow_legacy_nested_ai: bool = True
    ai_model: str = "gemini"  # Which AI model to use
    verify_fixes: bool = True  # Verify fixes after applying
    create_backup: bool = True  # Backup original file
    max_ai_retries: int = 3  # Max retries for AI generation
    timeout_seconds: int = 120  # Timeout for remediation

    # Category-specific settings
    fix_alt_text: bool = True
    fix_headings: bool = True
    fix_contrast: bool = True
    fix_tables: bool = True
    fix_links: bool = True
    fix_lists: bool = True
    fix_language: bool = True
    fix_reading_order: bool = True
    fix_forms: bool = True
    fix_aria: bool = True

    # Output settings
    output_directory: Optional[str] = None
    output_filename: Optional[str] = None
    preserve_original_name: bool = True

    # Output format options
    latex_output_formats: List[OutputFormat] = Field(
        default=[OutputFormat.TEX],
        description="Output formats for LaTeX remediation (tex, pdf, html)",
    )
    multimedia_output_format: OutputFormat = Field(
        default=OutputFormat.INDIVIDUAL,
        description="Output format for multimedia (individual files or zip)",
    )
    include_original_in_zip: bool = Field(
        default=True, description="Include original media file in ZIP archive"
    )


class VerificationResult(BaseModel):
    """Result of remediation verification (re-scan after fixes)."""

    passed: bool = False
    issues_before: int = 0
    issues_after: int = 0
    issues_fixed: List[str] = Field(default_factory=list)
    issues_remaining: List[str] = Field(default_factory=list)
    regressions: List[str] = Field(default_factory=list)  # New issues introduced
    verification_score: float = 0.0  # 0-100 improvement score


class RemediationResult(BaseModel):
    """Result of a remediation operation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_file: str
    output_file: Optional[str] = None
    document_type: str

    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    # Counts
    total_issues: int = 0
    fixed_count: int = 0
    manual_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0

    # Issue lists
    fixed_issues: List[FixedIssue] = Field(default_factory=list)
    manual_issues: List[ManualIssue] = Field(default_factory=list)
    failed_issues: List[Dict[str, Any]] = Field(default_factory=list)

    # Scores
    original_compliance_score: Optional[float] = None
    remediated_compliance_score: Optional[float] = None
    improvement: Optional[float] = None

    # Verification (post-remediation re-scan)
    verification_passed: bool = False
    verification_result: Optional[VerificationResult] = None

    # Status
    success: bool = True
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)

    # Metadata
    config_used: Optional[Dict[str, Any]] = None
    ai_calls_made: int = 0
    backup_path: Optional[str] = None

    _output_claim: Optional[DescriptorBoundOutputClaim] = PrivateAttr(default=None)

    def set_output_claim(self, claim: DescriptorBoundOutputClaim) -> None:
        """Take ownership of one live descriptor-bound output claim."""
        if not isinstance(claim, DescriptorBoundOutputClaim):
            raise TypeError("claim must be a DescriptorBoundOutputClaim")
        if claim.closed:
            raise RuntimeError("Cannot attach a closed output claim")
        if self.has_output_claim():
            raise RuntimeError("RemediationResult already owns a live output claim")
        self._output_claim = claim

    def take_output_claim(self) -> Optional[DescriptorBoundOutputClaim]:
        """Transfer ownership of the live output claim to the caller."""
        claim = self._output_claim
        self._output_claim = None
        if claim is None or claim.closed:
            return None
        return claim

    def has_output_claim(self) -> bool:
        """Return whether this result currently owns a live output claim."""
        return self._output_claim is not None and not self._output_claim.closed

    def open_output_stream(self):
        """Borrow a stream over the exact claimed output bytes."""
        claim = self._output_claim
        if claim is None or claim.closed:
            raise RuntimeError("RemediationResult has no live output claim")
        return claim.open_stream()

    def output_claim_metadata(self) -> Dict[str, Any]:
        """Return publication metadata without exposing descriptor or path state."""
        claim = self._output_claim
        if claim is None or claim.closed:
            raise RuntimeError("RemediationResult has no live output claim")
        return {
            "size_bytes": claim.size,
            "sha256": claim.sha256,
            "mime_type": claim.mime,
            "filename": claim.filename,
        }

    def close_output_claim(self) -> None:
        """Release and detach the owned output claim, idempotently."""
        claim = self._output_claim
        self._output_claim = None
        if claim is not None:
            claim.close()

    def model_copy(self, *, update=None, deep: bool = False):
        if self.has_output_claim():
            raise TypeError("Cannot copy a RemediationResult with a live output claim")
        copied = BaseModel.__deepcopy__(self) if deep else BaseModel.__copy__(self)
        if update:
            if self.model_config.get("extra") == "allow":
                for key, value in update.items():
                    if key in self.__pydantic_fields__:
                        copied.__dict__[key] = value
                    else:
                        if copied.__pydantic_extra__ is None:
                            copied.__pydantic_extra__ = {}
                        copied.__pydantic_extra__[key] = value
            else:
                copied.__dict__.update(update)
            copied.__pydantic_fields_set__.update(update.keys())
        return copied

    def __copy__(self):
        if self.has_output_claim():
            raise TypeError("Cannot copy a RemediationResult with a live output claim")
        return self.model_copy(deep=False)

    def __deepcopy__(self, memo=None):
        if self.has_output_claim():
            raise TypeError("Cannot copy a RemediationResult with a live output claim")
        return self.model_copy(deep=True)

    def __getstate__(self):
        if self.has_output_claim():
            raise TypeError(
                "Cannot pickle a RemediationResult with a live output claim"
            )
        return super().__getstate__()

    def complete(self) -> None:
        """Mark remediation as complete and calculate duration."""
        self.completed_at = datetime.utcnow()
        if self.started_at:
            self.duration_seconds = (
                self.completed_at - self.started_at
            ).total_seconds()

        # Calculate improvement if scores are available
        if (
            self.original_compliance_score is not None
            and self.remediated_compliance_score is not None
        ):
            self.improvement = (
                self.remediated_compliance_score - self.original_compliance_score
            )


class BaseRemediator(ABC):
    """
    Abstract base class for document remediators.

    Subclasses implement document-specific remediation logic for:
    - PDF documents
    - Word documents (.docx)
    - PowerPoint presentations (.pptx)
    - Excel spreadsheets (.xlsx)
    - HTML/CSS/JS code

    Usage:
        remediator = DocxRemediator(scan_result, config)
        result = remediator.remediate()
        if result.success:
            print(f"Fixed {result.fixed_count} issues")
            print(f"Output: {result.output_file}")
    """

    # Document type identifier (override in subclass)
    DOCUMENT_TYPE: str = "unknown"

    # File extensions this remediator handles
    SUPPORTED_EXTENSIONS: List[str] = []

    # Categories that can potentially be auto-fixed by this remediator
    AUTO_FIXABLE_CATEGORIES: List[IssueCategory] = []

    def __init__(
        self,
        file_path: str,
        issues: List[Dict[str, Any]],
        config: Optional[RemediationConfig] = None,
        ai_client: Optional[Any] = None,
        *,
        alt_text_client: Optional[Any] = None,
    ) -> None:
        """
        Initialize the remediator.

        Args:
            file_path: Path to the document to remediate
            issues: List of accessibility issues from scan
            config: Remediation configuration options
            ai_client: Purpose-bound client for non-alt remediation (optional)
            alt_text_client: Purpose-bound client for image/chart descriptions
        """
        from src.education.math_contracts import (
            IMAGE_EQUATION_ISSUE_TYPE,
            SCANNED_EQUATION_REGION_ISSUE_TYPE,
            math_issue_type_from,
        )

        self.file_path = file_path
        configured = config or RemediationConfig()
        visual_equation_types = {
            IMAGE_EQUATION_ISSUE_TYPE,
            SCANNED_EQUATION_REGION_ISSUE_TYPE,
        }
        has_image_equation = any(
            isinstance(raw_issue, dict)
            and math_issue_type_from(raw_issue) in visual_equation_types
            for raw_issue in issues
        )
        # Exact image-equation recognition is never a legacy/ambient AI
        # operation.  Copy rather than mutate the caller's configuration so a
        # mixed batch cannot silently alter a subsequently constructed legacy
        # remediator.
        self.config = (
            configured.model_copy(update={"allow_legacy_nested_ai": False})
            if has_image_equation
            else configured
        )
        self.issues = self._normalize_issues(issues)
        self.ai_client = ai_client
        self.alt_text_client = (
            alt_text_client
            if not has_image_equation
            or getattr(alt_text_client, "purpose", None) == "alt_text"
            else None
        )
        if self.alt_text_client is None and self.config.allow_legacy_nested_ai:
            self.alt_text_client = ai_client

        # Initialize result
        self.result = RemediationResult(
            original_file=file_path,
            document_type=self.DOCUMENT_TYPE,
            total_issues=len(self.issues),
            config_used=self.config.model_dump() if self.config else None,
        )

        # Validate file
        self._validate_file()

    def _validate_file(self) -> None:
        """Validate that the file exists and is supported."""
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File not found: {self.file_path}")

        ext = Path(self.file_path).suffix.lower()
        if self.SUPPORTED_EXTENSIONS and ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )

    def _normalize_issues(self, issues: List[Dict[str, Any]]) -> List[RemediationIssue]:
        """Convert raw issue dicts to RemediationIssue objects."""
        normalized = []
        for issue in issues:
            try:
                classification = classify_issue_category(
                    issue, authoritative=not self.config.allow_legacy_nested_ai
                )
                # Map category — check type/category fields first, fall back to WCAG rule
                raw_category = classification.category.value
                if not raw_category or raw_category == "other":
                    # Try mapping from WCAG rule (e.g., "WCAG 1.3.1" → "structure")
                    rule = issue.get("rule", issue.get("wcag_criterion", ""))
                    if rule:
                        import re as _re

                        wcag_match = _re.search(r"(\d+\.\d+\.\d+)", rule)
                        if wcag_match:
                            criterion = wcag_match.group(1)
                            from .category_mapper import wcag_criterion_to_category

                            raw_category = wcag_criterion_to_category(criterion)
                            # WCAG 1.3.1 covers both structure AND headings —
                            # disambiguate based on issue description
                            if criterion == "1.3.1" and raw_category == "structure":
                                desc = issue.get(
                                    "description", issue.get("message", "")
                                ).lower()
                                if any(
                                    kw in desc
                                    for kw in (
                                        "heading",
                                        " h1",
                                        " h2",
                                        " h3",
                                        " h4",
                                        " h5",
                                        " h6",
                                    )
                                ):
                                    raw_category = "heading"
                if not raw_category:
                    raw_category = "other"
                category = self._map_category(raw_category)

                # Map severity
                severity = self._map_severity(issue.get("severity", "medium"))

                normalized.append(
                    RemediationIssue(
                        id=issue.get("id", str(uuid.uuid4())),
                        category=category,
                        severity=severity,
                        description=issue.get(
                            "description", issue.get("message", "Unknown issue")
                        ),
                        location=issue.get(
                            "location", issue.get("page", issue.get("element"))
                        ),
                        element_type=issue.get("element_type", issue.get("element")),
                        original_content=issue.get(
                            "original_content", issue.get("current_value")
                        ),
                        wcag_criteria=issue.get("wcag_criteria", issue.get("wcag")),
                        fix_suggestion=issue.get(
                            "fix_suggestion", issue.get("recommendation")
                        ),
                        metadata={
                            **issue.get("metadata", {}),
                            **(
                                {
                                    "classification_manual_reason": classification.manual_reason
                                }
                                if classification.manual_reason
                                else {}
                            ),
                            **{
                                k: v
                                for k, v in issue.items()
                                if k
                                not in (
                                    "id",
                                    "type",
                                    "category",
                                    "severity",
                                    "description",
                                    "message",
                                    "location",
                                    "page",
                                    "element",
                                    "element_type",
                                    "original_content",
                                    "current_value",
                                    "wcag_criteria",
                                    "wcag",
                                    "fix_suggestion",
                                    "recommendation",
                                    "metadata",
                                )
                            },
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to normalize issue: {e}")
                # Create a generic issue
                normalized.append(
                    RemediationIssue(
                        category=IssueCategory.OTHER,
                        severity=IssueSeverity.MEDIUM,
                        description=str(issue),
                    )
                )

        return normalized

    def _map_category(self, category_str: str) -> IssueCategory:
        """Map a category string to IssueCategory enum."""
        return _CATEGORY_ALIASES.get(
            normalize_category_alias(category_str), IssueCategory.OTHER
        )

    def _map_severity(self, severity_str: str) -> IssueSeverity:
        """Map a severity string to IssueSeverity enum."""
        severity_map = {
            "critical": IssueSeverity.CRITICAL,
            "high": IssueSeverity.HIGH,
            "medium": IssueSeverity.MEDIUM,
            "low": IssueSeverity.LOW,
            "error": IssueSeverity.HIGH,
            "warning": IssueSeverity.MEDIUM,
            "info": IssueSeverity.LOW,
        }

        normalized = severity_str.lower().strip()
        return severity_map.get(normalized, IssueSeverity.MEDIUM)

    def remediate(self) -> RemediationResult:
        """
        Execute the remediation process.

        This is the main entry point for remediation. It:
        1. Creates a backup if configured
        2. Loads the document
        3. Processes each issue
        4. Saves the remediated document
        5. Verifies fixes if configured
        6. Returns the result

        Returns:
            RemediationResult with details of fixed and remaining issues
        """
        try:
            logger.info(f"Starting remediation of {self.file_path}")

            # Create backup if configured
            if self.config.create_backup:
                self.result.backup_path = self._create_backup()

            # Load the document
            document = self._load_document()

            # Process each issue
            for issue in self.issues:
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error(f"Error processing issue {issue.id}: {e}")
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # Save the remediated document
            output_path = self._save_document(document)
            self.result.output_file = output_path

            # Verify fixes if configured
            if self.config.verify_fixes:
                self._verify_fixes(output_path)

            # Calculate scores if possible
            self._calculate_scores()

            # Mark complete
            self.result.complete()
            logger.info(
                f"Remediation complete: {self.result.fixed_count} fixed, "
                f"{self.result.manual_count} manual, {self.result.failed_count} failed"
            )

        except Exception as e:
            logger.error(f"Remediation failed: {e}")
            self.result.success = False
            self.result.error_message = str(e)
            self.result.complete()

        return self.result

    def _process_issue(self, issue: RemediationIssue, document: Any) -> None:
        """
        Process a single issue.

        Args:
            issue: The issue to process
            document: The loaded document object
        """
        classification_reason = issue.metadata.get("classification_manual_reason")
        if classification_reason:
            self._add_manual_issue(
                issue,
                reason=str(classification_reason),
                recommendation="Review the issue category and apply the fix manually.",
            )
            return

        # Check if this category is enabled for fixing
        if not self._is_category_enabled(issue.category):
            self._add_manual_issue(
                issue,
                reason="Category disabled in configuration",
                recommendation=f"Enable {issue.category.value} fixing in remediation settings",
            )
            return

        # Check if we can auto-fix this issue
        if not self.can_auto_fix(issue):
            self._add_manual_issue(
                issue,
                reason=self._get_manual_reason(issue),
                recommendation=self._get_manual_recommendation(issue),
            )
            return

        # Generate the fix
        fix_content = self._generate_fix(issue, document)

        if fix_content is None:
            self._add_manual_issue(
                issue,
                reason="Could not generate appropriate fix",
                recommendation=self._get_manual_recommendation(issue),
            )
            return

        # Apply the fix
        success = self.apply_fix(issue, document, fix_content)

        if success:
            self._add_fixed_issue(
                issue, fixed_content=fix_content, fix_method=self._get_fix_method(issue)
            )
        else:
            self.result.failed_issues.append(
                {
                    "issue_id": issue.id,
                    "description": issue.description,
                    "error": "Failed to apply fix",
                }
            )
            self.result.failed_count += 1

    def _is_category_enabled(self, category: IssueCategory) -> bool:
        """Check if a category is enabled for fixing."""
        category_settings = {
            IssueCategory.ALT_TEXT: self.config.fix_alt_text,
            IssueCategory.HEADING: self.config.fix_headings,
            IssueCategory.CONTRAST: self.config.fix_contrast,
            IssueCategory.TABLE: self.config.fix_tables,
            IssueCategory.LINK: self.config.fix_links,
            IssueCategory.LIST: self.config.fix_lists,
            IssueCategory.LANGUAGE: self.config.fix_language,
            IssueCategory.READING_ORDER: self.config.fix_reading_order,
            IssueCategory.FORM: self.config.fix_forms,
            IssueCategory.ARIA: self.config.fix_aria,
        }
        return category_settings.get(category, True)

    def _generate_fix(self, issue: RemediationIssue, document: Any) -> Optional[str]:
        """
        Generate a fix for an issue.

        This method determines how to generate the fix:
        1. Use AI if configured and appropriate
        2. Use rule-based fixes for simple cases
        3. Use templates for standard fixes

        Args:
            issue: The issue to fix
            document: The document being remediated

        Returns:
            The fix content, or None if fix couldn't be generated
        """
        # Try rule-based fix first (faster, no AI cost)
        rule_fix = self._get_rule_based_fix(issue, document)
        if rule_fix is not None:
            return rule_fix

        # AI clients are purpose-bound. Image and chart descriptions must never
        # cross into the general remediation client.
        client = self._get_client_for_issue(issue)
        if client is not None:
            try:
                return self._get_ai_generated_fix(issue, document, client=client)
            except Exception as exc:
                logger.warning(
                    "Purpose-bound AI generation failed for issue %s: %s",
                    issue.id,
                    type(exc).__name__,
                )
                return None

        # Use template fix as fallback
        return self._get_template_fix(issue)

    def _get_fix_method(self, issue: RemediationIssue) -> str:
        """Determine which method was used to generate the fix."""
        if self._get_rule_based_fix(issue, None):
            return "rule_based"
        elif self._get_client_for_issue(issue) is not None:
            return "ai_generated"
        else:
            return "template"

    def _add_fixed_issue(
        self,
        issue: RemediationIssue,
        fixed_content: str,
        fix_method: str,
        confidence: float = 1.0,
        notes: Optional[str] = None,
        needs_review: bool = False,
        provider_used: Optional[str] = None,
        model_used: Optional[str] = None,
        source_kind: Optional[str] = None,
        source_locator: Optional[PageRasterRegionLocator | Dict[str, Any]] = None,
        verification_evidence: Optional[Dict[str, Any]] = None,
        visual_semantic_contract: Optional[
            VisualSemanticContract | Dict[str, Any]
        ] = None,
        wcag_criteria: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> None:
        """Add an issue to the fixed issues list."""
        self.result.fixed_issues.append(
            FixedIssue(
                issue_id=issue.id,
                category=issue.category,
                severity=issue.severity,
                description=issue.description,
                location=issue.location,
                original_content=issue.original_content,
                fixed_content=fixed_content,
                fix_method=fix_method,
                confidence=confidence,
                needs_review=needs_review,
                provider_used=provider_used,
                model_used=model_used,
                source_kind=source_kind,
                source_locator=source_locator,
                verification_evidence=verification_evidence,
                visual_semantic_contract=visual_semantic_contract,
                notes=notes,
                wcag_criteria=wcag_criteria,
                page_number=page_number,
            )
        )
        self.result.fixed_count += 1

    def _add_manual_issue(
        self, issue: RemediationIssue, reason: str, recommendation: str
    ) -> None:
        """Add an issue to the manual issues list."""
        self.result.manual_issues.append(
            ManualIssue(
                issue_id=issue.id,
                category=issue.category,
                severity=issue.severity,
                description=issue.description,
                location=issue.location,
                reason=reason,
                recommendation=recommendation,
                wcag_criteria=issue.wcag_criteria,
                metadata=issue.metadata,
            )
        )
        self.result.manual_count += 1

    def _create_backup(self) -> str:
        """Create a backup of the original file."""
        backup_dir = Path(self.file_path).parent / "backups"
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_name = Path(self.file_path).stem
        extension = Path(self.file_path).suffix
        backup_name = f"{original_name}_backup_{timestamp}{extension}"
        backup_path = backup_dir / backup_name

        import shutil

        shutil.copy2(self.file_path, backup_path)

        logger.info(f"Created backup: {backup_path}")
        return str(backup_path)

    def _get_output_path(self) -> str:
        """Determine the output path for the remediated file."""
        if self.config.output_filename:
            if self.config.output_directory:
                return str(
                    Path(self.config.output_directory) / self.config.output_filename
                )
            return str(Path(self.file_path).parent / self.config.output_filename)

        # Generate output filename
        original_path = Path(self.file_path)
        output_name = f"{original_path.stem}_remediated{original_path.suffix}"

        if self.config.output_directory:
            output_dir = Path(self.config.output_directory)
            output_dir.mkdir(parents=True, exist_ok=True)
            return str(output_dir / output_name)

        return str(original_path.parent / output_name)

    def _get_manual_reason(self, issue: RemediationIssue) -> str:
        """Get the reason why an issue can't be auto-fixed."""
        reasons = {
            IssueCategory.ALT_TEXT: "Image context requires human judgment",
            IssueCategory.HEADING: "Document structure needs manual review",
            IssueCategory.CONTRAST: "Color choice may affect branding",
            IssueCategory.TABLE: "Table structure is too complex",
            IssueCategory.LINK: "Link purpose unclear from context",
            IssueCategory.READING_ORDER: "Reading order requires visual inspection",
            IssueCategory.CHART: "Chart data interpretation requires expertise",
        }
        return reasons.get(issue.category, "Issue requires human judgment")

    def _get_manual_recommendation(self, issue: RemediationIssue) -> str:
        """Get a recommendation for manually fixing an issue."""
        recommendations = {
            IssueCategory.ALT_TEXT: (
                "Add descriptive alt text that conveys the image's purpose and content. "
                'For decorative images, use alt="" to mark as decorative.'
            ),
            IssueCategory.HEADING: (
                "Review and correct the heading hierarchy. Headings should follow "
                "a logical order (H1 → H2 → H3) without skipping levels."
            ),
            IssueCategory.CONTRAST: (
                "Ensure text has sufficient contrast against its background. "
                "WCAG AA requires 4.5:1 for normal text and 3:1 for large text."
            ),
            IssueCategory.TABLE: (
                "Add proper table headers using <th> elements. For complex tables, "
                "use scope attributes or id/headers associations."
            ),
            IssueCategory.LINK: (
                "Make link text descriptive of the destination. Avoid generic text "
                "like 'click here' or 'read more'."
            ),
            IssueCategory.READING_ORDER: (
                "Review and correct the reading order to ensure content is presented "
                "in a logical sequence for screen reader users."
            ),
            IssueCategory.CHART: (
                "Provide a detailed text description of the chart's data and trends. "
                "Include key values and insights."
            ),
        }
        return recommendations.get(
            issue.category, "Review the issue and apply an appropriate fix manually."
        )

    def _calculate_scores(self) -> None:
        """Calculate compliance scores if possible."""
        # Subclasses can override to provide accurate scores
        if self.result.total_issues > 0:
            # Estimate improvement based on fixes
            fix_rate = self.result.fixed_count / self.result.total_issues
            # Assume original score was based on issue penalty
            # This is a rough estimate - subclasses should override
            estimated_improvement = fix_rate * 20  # Rough estimate
            self.result.improvement = estimated_improvement

    # Abstract methods that subclasses must implement

    @abstractmethod
    def can_auto_fix(self, issue: RemediationIssue) -> bool:
        """
        Determine if an issue can be automatically fixed.

        Args:
            issue: The issue to check

        Returns:
            True if the issue can be auto-fixed, False otherwise
        """
        pass

    @abstractmethod
    def apply_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply a fix to the document.

        Args:
            issue: The issue being fixed
            document: The document object to modify
            fix_content: The content to apply as the fix

        Returns:
            True if fix was applied successfully, False otherwise
        """
        pass

    @abstractmethod
    def _load_document(self) -> Any:
        """
        Load the document for editing.

        Returns:
            The document object (type depends on document type)
        """
        pass

    @abstractmethod
    def _save_document(self, document: Any) -> str:
        """
        Save the remediated document.

        Args:
            document: The document object to save

        Returns:
            Path to the saved document
        """
        pass

    # Optional methods that subclasses can override

    def _get_rule_based_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """
        Get a rule-based fix for an issue.

        Subclasses can override to provide document-specific rules.

        Args:
            issue: The issue to fix
            document: The document being remediated

        Returns:
            The fix content, or None if no rule applies
        """
        return None

    def _get_template_fix(self, issue: RemediationIssue) -> Optional[str]:
        """
        Get a template-based fix for an issue.

        Args:
            issue: The issue to fix

        Returns:
            The fix content, or None if no template applies
        """
        return None

    def _get_client_for_issue(self, issue: RemediationIssue) -> Optional[Any]:
        """Return the only client permitted for an issue's AI purpose."""
        if issue.category in {IssueCategory.ALT_TEXT, IssueCategory.CHART}:
            return self.alt_text_client if self.config.fix_alt_text else None
        return self.ai_client if self.config.use_ai else None

    def _get_ai_generated_fix(
        self, issue: RemediationIssue, document: Any, *, client: Any
    ) -> Optional[str]:
        """
        Get an AI-generated fix for an issue.

        Args:
            issue: The issue to fix
            document: The document being remediated

        Returns:
            The AI-generated fix content, or None if generation failed
        """
        # Subclasses should override for document-specific AI prompts
        self.result.ai_calls_made += 1
        return None

    def _verify_fixes(self, output_path: str) -> VerificationResult:
        """
        Verify that fixes were applied correctly by re-scanning the document.

        This default implementation can be overridden by subclasses for
        document-specific verification logic.

        Args:
            output_path: Path to the remediated document

        Returns:
            VerificationResult with comparison of issues before/after
        """
        # Default implementation - subclasses should override for real verification
        verification = VerificationResult(
            passed=True,
            issues_before=self.result.total_issues,
            issues_after=self.result.manual_count,  # Assume only manual issues remain
            issues_fixed=[f.issue_id for f in self.result.fixed_issues],
            issues_remaining=[m.issue_id for m in self.result.manual_issues],
            regressions=[],
            verification_score=(
                100.0
                if self.result.manual_count == 0
                else (1 - self.result.manual_count / max(self.result.total_issues, 1))
                * 100
            ),
        )

        self.result.verification_passed = verification.passed
        self.result.verification_result = verification

        return verification
