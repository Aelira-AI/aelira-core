"""Strict, passive contracts for durable visual-semantic provenance.

The only active specialist is ``printed_equation``. Reserved or unknown kinds
are deliberately rejected until a complete, reviewed variant is added here.
"""

from __future__ import annotations

import hashlib
import math
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from src.education.commutative_diagram import (
    AccessibleDiagramDescriptionV1,
    VerifiedCommutativeDiagramV1,
    describe_commutative_diagram,
    render_commutative_diagram_html,
    verify_commutative_diagram,
)
from src.education.canonical_json import canonical_json_bytes, canonical_sha256
from src.education.chemical_abbreviation import (
    ChemicalAbbreviationEvidenceV1,
    verify_chemical_abbreviations,
)
from src.education.chemical_formula import (
    VerifiedChemicalNotationV1,
    verify_chemical_notation,
)
from src.education.equation_region_contract import PageRasterRegionLocator
from src.education.handwritten_math_suitability import (
    POLICY_SHA256 as HANDWRITTEN_SUITABILITY_POLICY_SHA256,
    HandwrittenMathSuitabilityEvidence,
)
from src.education.handwritten_equation_policy import (
    HANDWRITTEN_VERIFIER_POLICY_SHA256,
    HANDWRITTEN_VERIFIER_POLICY_VERSION,
)
from src.education.molecular_graph import (
    AccessibleMolecularDescriptionV1,
    VerifiedMolecularGraphV1,
    describe_molecular_graph,
    verify_molecular_graph,
)

_MAX_CANONICAL_STRING = 131_072
_MAX_DOCUMENT_INDEX = 25_000_000
_MAX_MATHML_BYTES = 32_768
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _validate_printable(value: str, *, label: str) -> str:
    if value != value.strip() or not value.isprintable():
        raise ValueError(f"{label} must be trimmed printable text")
    return value


def _validate_finite_geometry(value: Any, *, size: int, label: str) -> Any:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{label} must contain exactly {size} numbers")
    if any(
        not isinstance(item, (int, float))
        or isinstance(item, bool)
        or not math.isfinite(float(item))
        or abs(float(item)) > _MAX_DOCUMENT_INDEX
        for item in value
    ):
        raise ValueError(f"{label} must contain bounded finite numbers")
    return value


class EmbeddedImageOccurrenceLocator(BaseModel):
    """Exact source identity for one displayed embedded-image occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: Literal["embedded_image_occurrence"]
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_index: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    bbox: tuple[float, float, float, float]
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    occurrence_id: str = Field(pattern=r"^imgocc-v1-[0-9a-f]{24}$")

    @field_validator("bbox", mode="before")
    @classmethod
    def _finite_bbox(cls, value: Any) -> Any:
        return _validate_finite_geometry(value, size=4, label="bbox")

    @model_validator(mode="after")
    def _validate_identity(self) -> "EmbeddedImageOccurrenceLocator":
        x0, y0, x1, y1 = self.bbox
        if x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must have positive area")
        identity = (
            f"{self.page_number}|{self.image_xref}|{self.image_index}|"
            f"{self.occurrence_ordinal}|"
            + ",".join(f"{value:.6f}" for value in self.bbox)
        )
        expected = (
            "imgocc-v1-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        )
        if self.occurrence_id != expected:
            raise ValueError("occurrence_id does not match canonical source identity")
        return self


class FrozenPageRasterRegionLocator(PageRasterRegionLocator):
    """Immutable contract view with the legacy locator wire identity unchanged."""

    model_config = ConfigDict(extra="forbid", frozen=True)


VisualLocator: TypeAlias = Annotated[
    FrozenPageRasterRegionLocator | EmbeddedImageOccurrenceLocator,
    Field(discriminator="source_kind"),
]
VisualLocatorAdapter = TypeAdapter(VisualLocator)


class MathMLExpressionV1(BaseModel):
    """One bounded passive MathML semantic output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_kind: Literal["mathml_expression_v1"]
    mathml: str = Field(min_length=1, max_length=_MAX_MATHML_BYTES)
    alt_text: str = Field(min_length=1, max_length=1_024)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("alt_text")
    @classmethod
    def _printable_alt_text(cls, value: str) -> str:
        return _validate_printable(value, label="alt_text")

    @field_validator("mathml")
    @classmethod
    def _passive_mathml(cls, value: str) -> str:
        from src.education.remediation.equation_verifier import (
            EquationVerificationRejected,
            canonicalize_mathml,
        )

        if (
            value != value.strip()
            or not value.isprintable()
            or len(value.encode("utf-8")) > _MAX_MATHML_BYTES
        ):
            raise ValueError("mathml must be bounded printable text")
        try:
            canonical = canonicalize_mathml(value)
        except EquationVerificationRejected as exc:
            raise ValueError(
                "mathml is outside the passive verifier allowlist"
            ) from exc
        if canonical != value:
            raise ValueError("mathml must already be in canonical verifier form")
        return value

    @model_validator(mode="after")
    def _validate_digest(self) -> "MathMLExpressionV1":
        expected = hashlib.sha256(self.mathml.encode("utf-8")).hexdigest()
        if self.mathml_sha256 != expected:
            raise ValueError("mathml_sha256 does not match the semantic output")
        return self


class ChemicalFormulaSemanticV1(BaseModel):
    """One #225-verified notation and its deterministic projections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_kind: Literal["chemical_formula_semantic_v1"]
    verified_notation: VerifiedChemicalNotationV1

    @model_validator(mode="after")
    def _validate_notation_authority(self) -> "ChemicalFormulaSemanticV1":
        expected = verify_chemical_notation(self.verified_notation.source_notation)
        if expected != self.verified_notation:
            raise ValueError("chemical formula semantics disagree with #225")
        return self


class CommutativeDiagramSemanticV1(BaseModel):
    """One verified diagram graph and its deterministic accessible outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_kind: Literal["commutative_diagram_semantic_v1"]
    graph: VerifiedCommutativeDiagramV1
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description: AccessibleDiagramDescriptionV1
    description_sha256: str = Field(pattern=_SHA256_PATTERN)
    rendered_html: str = Field(min_length=1, max_length=_MAX_CANONICAL_STRING)
    rendered_html_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_one_graph_source(self) -> "CommutativeDiagramSemanticV1":
        graph = verify_commutative_diagram(self.graph)
        description = describe_commutative_diagram(graph)
        rendered_html = render_commutative_diagram_html(graph)
        if self.graph_sha256 != graph.canonical_sha256:
            raise ValueError("graph_sha256 does not match the verified graph")
        if self.description != description:
            raise ValueError("description does not match the verified graph")
        if self.description.graph_sha256 != self.graph_sha256:
            raise ValueError("description does not identify the verified graph")
        if self.description_sha256 != canonical_sha256(
            description.model_dump(mode="json")
        ):
            raise ValueError("description_sha256 does not match the description")
        if self.rendered_html != rendered_html:
            raise ValueError("rendered_html does not match the verified graph")
        if (
            self.rendered_html_sha256
            != hashlib.sha256(rendered_html.encode("utf-8")).hexdigest()
        ):
            raise ValueError("rendered_html_sha256 does not match rendered_html")
        return self


class ChemicalStructureSemanticV1(BaseModel):
    """One verified molecular graph and its deterministic description."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_kind: Literal["chemical_structure_semantic_v1"]
    graph: VerifiedMolecularGraphV1
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description: AccessibleMolecularDescriptionV1
    description_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_one_graph_source(self) -> "ChemicalStructureSemanticV1":
        graph = verify_molecular_graph(self.graph)
        description = describe_molecular_graph(graph)
        if self.graph_sha256 != graph.canonical_sha256:
            raise ValueError("graph_sha256 does not match the verified graph")
        if self.description != description:
            raise ValueError("description does not match the verified graph")
        if self.description.graph_sha256 != self.graph_sha256:
            raise ValueError("description does not identify the verified graph")
        if self.description_sha256 != canonical_sha256(
            description.model_dump(mode="json")
        ):
            raise ValueError("description_sha256 does not match the description")
        return self


SemanticOutput: TypeAlias = Annotated[
    MathMLExpressionV1
    | ChemicalFormulaSemanticV1
    | ChemicalStructureSemanticV1
    | CommutativeDiagramSemanticV1,
    Field(discriminator="semantic_kind"),
]
SemanticOutputAdapter = TypeAdapter(SemanticOutput)


class PrintedEquationRoundtripEvidenceV1(BaseModel):
    """Bounded pixel-comparison evidence for printed-equation semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["printed_equation_roundtrip_v1"]
    passed: bool = Field(strict=True)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    rendered_sha256: str = Field(pattern=_SHA256_PATTERN)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    renderer_version: str = Field(min_length=1, max_length=128)
    comparator_version: str = Field(min_length=1, max_length=128)
    font_sha256: str = Field(pattern=_SHA256_PATTERN)
    threshold_version: str = Field(min_length=1, max_length=128)
    ink_iou: float = Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)
    pixel_similarity: float = Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)
    required_ink_iou: float = Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)
    required_pixel_similarity: float = Field(
        ge=0.0, le=1.0, allow_inf_nan=False, strict=True
    )

    @field_validator("renderer_version", "comparator_version", "threshold_version")
    @classmethod
    def _printable_versions(cls, value: str) -> str:
        return _validate_printable(value, label="evidence version")


class HandwrittenEquationConsensusEvidenceV1(BaseModel):
    """Exact two-reading semantic agreement evidence for handwritten math."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["handwritten_equation_consensus_v1"]
    passed: Literal[True]
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    suitability_evidence: HandwrittenMathSuitabilityEvidence
    suitability_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    suitability_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_policy_version: Literal["handwritten-equation-consensus-v1"]
    verifier_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    agreement_count: Literal[2]
    required_agreement_count: Literal[2]
    primary_mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_latex_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_latex_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_provider: str = Field(min_length=1, max_length=200)
    primary_model: str = Field(min_length=1, max_length=200)
    verifier_provider: str = Field(min_length=1, max_length=200)
    verifier_model: str = Field(min_length=1, max_length=200)

    @field_validator(
        "primary_provider", "primary_model", "verifier_provider", "verifier_model"
    )
    @classmethod
    def _printable_identities(cls, value: str) -> str:
        return _validate_printable(value, label="provider identity")

    @model_validator(mode="after")
    def _validate_consensus(self) -> "HandwrittenEquationConsensusEvidenceV1":
        if (
            self.suitability_policy_sha256 != HANDWRITTEN_SUITABILITY_POLICY_SHA256
            or self.suitability_evidence.policy_sha256
            != HANDWRITTEN_SUITABILITY_POLICY_SHA256
            or self.suitability_evidence.disposition != "eligible"
        ):
            raise ValueError("handwritten suitability evidence must be eligible")
        if (
            self.verifier_policy_version != HANDWRITTEN_VERIFIER_POLICY_VERSION
            or self.verifier_policy_sha256 != HANDWRITTEN_VERIFIER_POLICY_SHA256
        ):
            raise ValueError("handwritten verifier policy identity is invalid")
        if self.source_sha256 != self.suitability_evidence.source_sha256:
            raise ValueError("consensus source does not match suitability evidence")
        if (
            self.suitability_evidence_sha256
            != self.suitability_evidence.evidence_sha256
        ):
            raise ValueError("consensus does not match suitability evidence identity")
        if (
            self.mathml_sha256 != self.primary_mathml_sha256
            or self.mathml_sha256 != self.verifier_mathml_sha256
        ):
            raise ValueError("independent handwritten readings do not agree")
        return self


class StandaloneFormulaSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for a saved standalone image Formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["standalone_formula_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)


class ScannedRegionFormulaSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for a saved clipped-region Formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["scanned_region_formula_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    resource_name: str = Field(min_length=1, max_length=128)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    formula_bbox: tuple[float, float, float, float]
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)
    ocr_resource_name: str = Field(max_length=128)
    ocr_struct_parent: int = Field(ge=-1, le=_MAX_DOCUMENT_INDEX, strict=True)
    ocr_group_owners: tuple[tuple[str, int], ...] = Field(max_length=4_096)
    ocr_before_mcids: tuple[int, ...] = Field(max_length=4_096)
    ocr_after_mcids: tuple[int, ...] = Field(max_length=4_096)
    ocr_payload_sha256: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    ocr_font_sha256: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    page_text_sha256: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")

    @field_validator("formula_bbox", mode="before")
    @classmethod
    def _finite_formula_bbox(cls, value: Any) -> Any:
        return _validate_finite_geometry(value, size=4, label="formula_bbox")

    @field_validator("resource_name", "ocr_resource_name")
    @classmethod
    def _printable_resource_names(cls, value: str) -> str:
        if value and (value != value.strip() or not value.isprintable()):
            raise ValueError("resource names must be bounded printable text")
        return value

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("render_signatures must be a non-empty sequence")
        for signature in value:
            if not isinstance(signature, (list, tuple)) or len(signature) != 6:
                raise ValueError("render signatures must have exactly six values")
            if any(
                not isinstance(item, int) or isinstance(item, bool)
                for item in signature[:5]
            ):
                raise ValueError("render signature geometry must contain integers")
            if any(not 1 <= item <= _MAX_DOCUMENT_INDEX for item in signature[:5]):
                raise ValueError("render signature geometry must be bounded")
        return value

    @field_validator("ocr_group_owners", mode="before")
    @classmethod
    def _bounded_ocr_owners(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("ocr_group_owners must be a sequence")
        for owner in value:
            if (
                not isinstance(owner, (list, tuple))
                or len(owner) != 2
                or not isinstance(owner[0], str)
                or not owner[0]
                or len(owner[0]) > 128
                or owner[0] != owner[0].strip()
                or not owner[0].isprintable()
                or not isinstance(owner[1], int)
                or isinstance(owner[1], bool)
                or not -1 <= owner[1] <= _MAX_DOCUMENT_INDEX
            ):
                raise ValueError("ocr_group_owners contains invalid passive data")
        return value

    @field_validator("ocr_before_mcids", "ocr_after_mcids", mode="before")
    @classmethod
    def _bounded_mcids(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 <= item <= _MAX_DOCUMENT_INDEX
            for item in value
        ):
            raise ValueError("OCR MCIDs must be bounded non-negative integers")
        return value

    @model_validator(mode="after")
    def _validate_saved_geometry_and_ocr(self) -> "ScannedRegionFormulaSavedEvidenceV1":
        x0, y0, x1, y1 = self.formula_bbox
        if x0 >= x1 or y0 >= y1:
            raise ValueError("formula_bbox must have positive area")
        has_ocr = bool(self.ocr_resource_name)
        ocr_fields_present = (
            self.ocr_struct_parent >= 0
            and bool(self.ocr_group_owners)
            and bool(self.ocr_payload_sha256)
            and bool(self.ocr_font_sha256)
            and bool(self.page_text_sha256)
        )
        if has_ocr != ocr_fields_present:
            raise ValueError(
                "OCR reverse-verification fields must be complete or absent"
            )
        if not has_ocr and (
            self.ocr_struct_parent != -1
            or self.ocr_group_owners
            or self.ocr_before_mcids
            or self.ocr_after_mcids
            or self.ocr_payload_sha256
            or self.ocr_font_sha256
            or self.page_text_sha256
        ):
            raise ValueError("absent OCR evidence must use exact empty sentinels")
        return self


class ChemicalFormulaRecognitionEvidenceV1(BaseModel):
    """Provider provenance accepted only after #225 verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["chemical_formula_recognition_v1"]
    passed: Literal[True]
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sha256: str = Field(pattern=_SHA256_PATTERN)
    speech_sha256: str = Field(pattern=_SHA256_PATTERN)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["chemical-formula-pdf-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _printable_identity(cls, value: str) -> str:
        return _validate_printable(value, label="provider identity")


class StandaloneChemicalFormulaSavedEvidenceV1(StandaloneFormulaSavedEvidenceV1):
    """Reverse-verification identity for one saved chemical Formula."""

    evidence_kind: Literal["standalone_chemical_formula_saved_v1"]
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sha256: str = Field(pattern=_SHA256_PATTERN)
    speech_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        return _validate_saved_render_signatures(value)


class ScannedRegionChemicalFormulaSavedEvidenceV1(ScannedRegionFormulaSavedEvidenceV1):
    """Reverse-verification identity for one clipped chemical Formula."""

    evidence_kind: Literal["scanned_region_chemical_formula_saved_v1"]
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sha256: str = Field(pattern=_SHA256_PATTERN)
    speech_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)


class CommutativeDiagramRecognitionEvidenceV1(BaseModel):
    """Provider provenance independently accepted by the graph verifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["commutative_diagram_recognition_v1"]
    passed: Literal[True]
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["commutative-diagram-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _printable_identity(cls, value: str) -> str:
        return _validate_printable(value, label="provider identity")


class StandaloneDiagramSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for an embedded-image diagram Figure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["standalone_diagram_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description_sha256: str = Field(pattern=_SHA256_PATTERN)
    rendered_html_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    attachment_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        return _validate_saved_render_signatures(value)


class ScannedRegionDiagramSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for a clipped raster-region diagram Figure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["scanned_region_diagram_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    resource_name: str = Field(min_length=1, max_length=128)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description_sha256: str = Field(pattern=_SHA256_PATTERN)
    rendered_html_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    attachment_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagram_bbox: tuple[float, float, float, float]
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)
    ocr_resource_name: str = Field(default="", max_length=128)
    ocr_struct_parent: int = Field(
        default=-1, ge=-1, le=_MAX_DOCUMENT_INDEX, strict=True
    )
    ocr_group_owners: tuple[tuple[str, int], ...] = Field(default=(), max_length=4_096)
    ocr_before_mcids: tuple[int, ...] = Field(default=(), max_length=4_096)
    ocr_after_mcids: tuple[int, ...] = Field(default=(), max_length=4_096)
    ocr_payload_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    ocr_font_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    page_text_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")

    @field_validator("resource_name", "ocr_resource_name")
    @classmethod
    def _printable_resource_name(cls, value: str) -> str:
        return _validate_printable(value, label="resource_name")

    @field_validator("diagram_bbox", mode="before")
    @classmethod
    def _finite_diagram_bbox(cls, value: Any) -> Any:
        return _validate_finite_geometry(value, size=4, label="diagram_bbox")

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        return _validate_saved_render_signatures(value)

    @field_validator("ocr_group_owners", mode="before")
    @classmethod
    def _bounded_ocr_owners(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("ocr_group_owners must be a sequence")
        for owner in value:
            if (
                not isinstance(owner, (list, tuple))
                or len(owner) != 2
                or not isinstance(owner[0], str)
                or not owner[0]
                or len(owner[0]) > 128
                or owner[0] != owner[0].strip()
                or not owner[0].isprintable()
                or not isinstance(owner[1], int)
                or isinstance(owner[1], bool)
                or not -1 <= owner[1] <= _MAX_DOCUMENT_INDEX
            ):
                raise ValueError("ocr_group_owners contains invalid passive data")
        return value

    @field_validator("ocr_before_mcids", "ocr_after_mcids", mode="before")
    @classmethod
    def _bounded_ocr_mcids(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 <= item <= _MAX_DOCUMENT_INDEX
            for item in value
        ):
            raise ValueError("OCR diagram MCIDs must be bounded integers")
        return value

    @model_validator(mode="after")
    def _positive_diagram_bbox(self) -> "ScannedRegionDiagramSavedEvidenceV1":
        x0, y0, x1, y1 = self.diagram_bbox
        if x0 >= x1 or y0 >= y1:
            raise ValueError("diagram_bbox must have positive area")
        has_ocr = bool(self.ocr_resource_name)
        complete_ocr = (
            self.ocr_struct_parent >= 0
            and bool(self.ocr_group_owners)
            and bool(self.ocr_payload_sha256)
            and bool(self.ocr_font_sha256)
            and bool(self.page_text_sha256)
        )
        if has_ocr != complete_ocr:
            raise ValueError(
                "OCR diagram reverse-verification fields must be complete or absent"
            )
        if not has_ocr and (
            self.ocr_struct_parent != -1
            or self.ocr_group_owners
            or self.ocr_before_mcids
            or self.ocr_after_mcids
            or self.ocr_payload_sha256
            or self.ocr_font_sha256
            or self.page_text_sha256
        ):
            raise ValueError("absent diagram OCR evidence must use exact sentinels")
        return self


class ChemicalStructureRecognitionEvidenceV1(BaseModel):
    """Provider provenance accepted by graph and abbreviation verifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["chemical_structure_recognition_v1"]
    passed: Literal[True]
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviations: tuple[ChemicalAbbreviationEvidenceV1, ...] = Field(max_length=32)
    abbreviation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviation_policy_version: Literal["chemical-abbreviation-v1"]
    abbreviation_count: int = Field(ge=0, le=32, strict=True)
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["chemical-structure-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _printable_identity(cls, value: str) -> str:
        return _validate_printable(value, label="provider identity")

    @model_validator(mode="after")
    def _validate_abbreviation_evidence(
        self,
    ) -> "ChemicalStructureRecognitionEvidenceV1":
        if self.abbreviation_count != len(self.abbreviations):
            raise ValueError("abbreviation_count does not match evidence")
        if self.abbreviation_evidence_sha256 != canonical_sha256(
            [item.model_dump(mode="json") for item in self.abbreviations]
        ):
            raise ValueError("abbreviation evidence digest does not match")
        return self


class StandaloneChemicalStructureSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for an embedded chemical Figure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["standalone_chemical_structure_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    attachment_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        return _validate_saved_render_signatures(value)


class ScannedRegionChemicalStructureSavedEvidenceV1(BaseModel):
    """Reverse-verification identity for a clipped chemical Figure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["scanned_region_chemical_structure_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    image_xref: int = Field(ge=1, le=_MAX_DOCUMENT_INDEX, strict=True)
    resource_name: str = Field(min_length=1, max_length=128)
    struct_parent: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_DOCUMENT_INDEX, strict=True)
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    description_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_stream_sha256: str = Field(pattern=_SHA256_PATTERN)
    attachment_sha256: str = Field(pattern=_SHA256_PATTERN)
    metadata_sha256: str = Field(pattern=_SHA256_PATTERN)
    structure_bbox: tuple[float, float, float, float]
    render_signatures: tuple[
        tuple[int, int, int, int, int, Annotated[str, Field(pattern=_SHA256_PATTERN)]],
        ...,
    ] = Field(min_length=1, max_length=16)
    ocr_resource_name: str = Field(default="", max_length=128)
    ocr_struct_parent: int = Field(
        default=-1, ge=-1, le=_MAX_DOCUMENT_INDEX, strict=True
    )
    ocr_group_owners: tuple[tuple[str, int], ...] = Field(default=(), max_length=4_096)
    ocr_before_mcids: tuple[int, ...] = Field(default=(), max_length=4_096)
    ocr_after_mcids: tuple[int, ...] = Field(default=(), max_length=4_096)
    ocr_payload_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    ocr_font_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    page_text_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")

    @field_validator("resource_name", "ocr_resource_name")
    @classmethod
    def _printable_resource_name(cls, value: str) -> str:
        return _validate_printable(value, label="resource_name")

    @field_validator("structure_bbox", mode="before")
    @classmethod
    def _finite_structure_bbox(cls, value: Any) -> Any:
        return _validate_finite_geometry(value, size=4, label="structure_bbox")

    @field_validator("render_signatures", mode="before")
    @classmethod
    def _bounded_render_signatures(cls, value: Any) -> Any:
        return _validate_saved_render_signatures(value)

    @field_validator("ocr_group_owners", mode="before")
    @classmethod
    def _bounded_ocr_owners(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("ocr_group_owners must be a sequence")
        for owner in value:
            if (
                not isinstance(owner, (list, tuple))
                or len(owner) != 2
                or not isinstance(owner[0], str)
                or not owner[0]
                or len(owner[0]) > 128
                or owner[0] != owner[0].strip()
                or not owner[0].isprintable()
                or not isinstance(owner[1], int)
                or isinstance(owner[1], bool)
                or not -1 <= owner[1] <= _MAX_DOCUMENT_INDEX
            ):
                raise ValueError("ocr_group_owners contains invalid passive data")
        return value

    @field_validator("ocr_before_mcids", "ocr_after_mcids", mode="before")
    @classmethod
    def _bounded_ocr_mcids(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 <= item <= _MAX_DOCUMENT_INDEX
            for item in value
        ):
            raise ValueError("OCR structure MCIDs must be bounded integers")
        return value

    @model_validator(mode="after")
    def _positive_structure_bbox(
        self,
    ) -> "ScannedRegionChemicalStructureSavedEvidenceV1":
        x0, y0, x1, y1 = self.structure_bbox
        if x0 >= x1 or y0 >= y1:
            raise ValueError("structure_bbox must have positive area")
        has_ocr = bool(self.ocr_resource_name)
        complete_ocr = (
            self.ocr_struct_parent >= 0
            and bool(self.ocr_group_owners)
            and bool(self.ocr_payload_sha256)
            and bool(self.ocr_font_sha256)
            and bool(self.page_text_sha256)
        )
        if has_ocr != complete_ocr:
            raise ValueError(
                "OCR structure reverse-verification fields must be complete or absent"
            )
        if not has_ocr and (
            self.ocr_struct_parent != -1
            or self.ocr_group_owners
            or self.ocr_before_mcids
            or self.ocr_after_mcids
            or self.ocr_payload_sha256
            or self.ocr_font_sha256
            or self.page_text_sha256
        ):
            raise ValueError("absent structure OCR evidence must use exact sentinels")
        return self


def _validate_saved_render_signatures(value: Any) -> Any:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("render_signatures must be a non-empty sequence")
    for signature in value:
        if not isinstance(signature, (list, tuple)) or len(signature) != 6:
            raise ValueError("render signatures must have exactly six values")
        if any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in signature[:5]
        ):
            raise ValueError("render signature geometry must contain integers")
        if any(not 1 <= item <= _MAX_DOCUMENT_INDEX for item in signature[:5]):
            raise ValueError("render signature geometry must be bounded")
    return value


VerificationEvidence: TypeAlias = Annotated[
    PrintedEquationRoundtripEvidenceV1
    | HandwrittenEquationConsensusEvidenceV1
    | StandaloneFormulaSavedEvidenceV1
    | ScannedRegionFormulaSavedEvidenceV1
    | ChemicalFormulaRecognitionEvidenceV1
    | StandaloneChemicalFormulaSavedEvidenceV1
    | ScannedRegionChemicalFormulaSavedEvidenceV1
    | CommutativeDiagramRecognitionEvidenceV1
    | StandaloneDiagramSavedEvidenceV1
    | ScannedRegionDiagramSavedEvidenceV1
    | ChemicalStructureRecognitionEvidenceV1
    | StandaloneChemicalStructureSavedEvidenceV1
    | ScannedRegionChemicalStructureSavedEvidenceV1,
    Field(discriminator="evidence_kind"),
]
VerificationEvidenceAdapter = TypeAdapter(VerificationEvidence)


class PrintedEquationContract(BaseModel):
    """Complete v1 specialist output and reverse-verification contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["printed_equation"]
    locator: VisualLocator
    semantic_output: SemanticOutput
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    verification_evidence: tuple[VerificationEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_variant_pairing_and_digests(self) -> "PrintedEquationContract":
        roundtrip = [
            item
            for item in self.verification_evidence
            if isinstance(item, PrintedEquationRoundtripEvidenceV1)
        ]
        standalone = [
            item
            for item in self.verification_evidence
            if isinstance(item, StandaloneFormulaSavedEvidenceV1)
        ]
        scanned = [
            item
            for item in self.verification_evidence
            if isinstance(item, ScannedRegionFormulaSavedEvidenceV1)
        ]
        if len(roundtrip) != 1 or len(standalone) + len(scanned) != 1:
            raise ValueError(
                "printed equations require exact roundtrip and saved evidence"
            )
        saved = standalone[0] if standalone else scanned[0]
        roundtrip_evidence = roundtrip[0]
        if (
            not roundtrip_evidence.passed
            or roundtrip_evidence.ink_iou < roundtrip_evidence.required_ink_iou
            or roundtrip_evidence.pixel_similarity
            < roundtrip_evidence.required_pixel_similarity
        ):
            raise ValueError("printed equation roundtrip evidence must pass its policy")
        if self.normalized_source_sha256 != roundtrip_evidence.source_sha256:
            raise ValueError(
                "normalized source digest does not match roundtrip evidence"
            )
        if any(
            item.mathml_sha256 != self.semantic_output.mathml_sha256
            for item in self.verification_evidence
        ):
            raise ValueError("evidence does not match the semantic output digest")
        expected_alt_text_sha256 = hashlib.sha256(
            self.semantic_output.alt_text.encode("utf-8")
        ).hexdigest()
        if saved.alt_text_sha256 != expected_alt_text_sha256:
            raise ValueError("saved evidence does not match the semantic alt text")

        if isinstance(self.locator, EmbeddedImageOccurrenceLocator):
            if (
                not standalone
                or saved.page_number != self.locator.page_number
                or saved.image_xref != self.locator.image_xref
                or saved.occurrence_ordinal != self.locator.occurrence_ordinal
                or saved.image_stream_sha256 != self.locator.image_stream_sha256
            ):
                raise ValueError(
                    "embedded image locators require matching standalone saved evidence"
                )
        else:
            px0, py0, px1, py1 = self.locator.pixel_bbox
            scale_x, _, _, scale_y, offset_x, offset_y = self.locator.transform
            expected_formula_bbox = (
                offset_x + scale_x * px0 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py1 / self.locator.source_height),
                offset_x + scale_x * px1 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py0 / self.locator.source_height),
            )
            if (
                not scanned
                or saved.page_number != self.locator.page_number
                or saved.image_stream_sha256 != self.locator.source_sha256
                or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(
                        saved.formula_bbox, expected_formula_bbox
                    )
                )
            ):
                raise ValueError(
                    "page raster regions require matching scanned-region saved evidence"
                )

        specialist_material = {
            "contract_kind": self.contract_kind,
            "locator": self.locator,
            "semantic_output": self.semantic_output,
            "normalized_source_sha256": self.normalized_source_sha256,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("specialist_sha256 does not match the specialist output")
        contract_material = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("contract_sha256 does not match the complete contract")
        return self


class ChemicalFormulaPdfContract(BaseModel):
    """Complete #225 recognition, PDF association, and saved-file proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["chemical_formula"]
    locator: VisualLocator
    semantic_output: ChemicalFormulaSemanticV1
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    verification_evidence: tuple[VerificationEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_variant_pairing_and_digests(self) -> "ChemicalFormulaPdfContract":
        recognition = [
            item
            for item in self.verification_evidence
            if isinstance(item, ChemicalFormulaRecognitionEvidenceV1)
        ]
        standalone = [
            item
            for item in self.verification_evidence
            if isinstance(item, StandaloneChemicalFormulaSavedEvidenceV1)
        ]
        scanned = [
            item
            for item in self.verification_evidence
            if isinstance(item, ScannedRegionChemicalFormulaSavedEvidenceV1)
        ]
        if len(recognition) != 1 or len(standalone) + len(scanned) != 1:
            raise ValueError(
                "chemical formulas require exact recognition and saved evidence"
            )
        recognition_evidence = recognition[0]
        saved = standalone[0] if standalone else scanned[0]
        notation = self.semantic_output.verified_notation
        expected_metadata_sha256 = canonical_sha256(
            {
                "notation_kind": notation.notation.notation_kind,
                "source_sha256": notation.source_sha256,
                "semantic_sha256": notation.semantic_sha256,
                "speech_sha256": notation.speech_sha256,
                "mathml_sha256": notation.mathml_sha256,
            }
        )
        if (
            recognition_evidence.normalized_source_sha256
            != self.normalized_source_sha256
            or recognition_evidence.source_sha256 != notation.source_sha256
            or recognition_evidence.semantic_sha256 != notation.semantic_sha256
            or recognition_evidence.speech_sha256 != notation.speech_sha256
            or recognition_evidence.mathml_sha256 != notation.mathml_sha256
            or saved.source_sha256 != notation.source_sha256
            or saved.semantic_sha256 != notation.semantic_sha256
            or saved.speech_sha256 != notation.speech_sha256
            or saved.mathml_sha256 != notation.mathml_sha256
            or saved.metadata_sha256 != expected_metadata_sha256
        ):
            raise ValueError("chemical formula evidence disagrees with #225 semantics")
        expected_alt_text_sha256 = hashlib.sha256(
            notation.speech.encode("utf-8")
        ).hexdigest()
        if saved.alt_text_sha256 != expected_alt_text_sha256:
            raise ValueError("saved evidence does not match chemistry-aware speech")

        if isinstance(self.locator, EmbeddedImageOccurrenceLocator):
            if (
                not standalone
                or saved.page_number != self.locator.page_number
                or saved.image_xref != self.locator.image_xref
                or saved.occurrence_ordinal != self.locator.occurrence_ordinal
                or saved.image_stream_sha256 != self.locator.image_stream_sha256
            ):
                raise ValueError(
                    "embedded image locators require matching chemical saved evidence"
                )
        else:
            px0, py0, px1, py1 = self.locator.pixel_bbox
            scale_x, _, _, scale_y, offset_x, offset_y = self.locator.transform
            expected_bbox = (
                offset_x + scale_x * px0 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py1 / self.locator.source_height),
                offset_x + scale_x * px1 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py0 / self.locator.source_height),
            )
            if (
                not scanned
                or saved.page_number != self.locator.page_number
                or saved.image_stream_sha256 != self.locator.source_sha256
                or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(saved.formula_bbox, expected_bbox)
                )
            ):
                raise ValueError(
                    "page raster locators require matching chemical saved evidence"
                )

        specialist_material = {
            "contract_kind": self.contract_kind,
            "locator": self.locator,
            "semantic_output": self.semantic_output,
            "normalized_source_sha256": self.normalized_source_sha256,
            "recognition_evidence": recognition_evidence,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("specialist_sha256 does not match the specialist output")
        contract_material = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("contract_sha256 does not match the complete contract")
        return self


class CommutativeDiagramPdfContract(BaseModel):
    """Complete graph recognition, PDF association, and reverse-verification proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["commutative_diagram"]
    locator: VisualLocator
    semantic_output: CommutativeDiagramSemanticV1
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    verification_evidence: tuple[VerificationEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_variant_pairing_and_digests(self) -> "CommutativeDiagramPdfContract":
        recognition = [
            item
            for item in self.verification_evidence
            if isinstance(item, CommutativeDiagramRecognitionEvidenceV1)
        ]
        standalone = [
            item
            for item in self.verification_evidence
            if isinstance(item, StandaloneDiagramSavedEvidenceV1)
        ]
        scanned = [
            item
            for item in self.verification_evidence
            if isinstance(item, ScannedRegionDiagramSavedEvidenceV1)
        ]
        if len(recognition) != 1 or len(standalone) + len(scanned) != 1:
            raise ValueError(
                "commutative diagrams require exact recognition and saved evidence"
            )
        recognition_evidence = recognition[0]
        saved = standalone[0] if standalone else scanned[0]
        semantic = self.semantic_output
        if (
            recognition_evidence.normalized_source_sha256
            != self.normalized_source_sha256
            or recognition_evidence.graph_sha256 != semantic.graph_sha256
            or saved.graph_sha256 != semantic.graph_sha256
            or saved.description_sha256 != semantic.description_sha256
            or saved.rendered_html_sha256 != semantic.rendered_html_sha256
        ):
            raise ValueError("diagram evidence disagrees with the specialist output")
        expected_alt_text_sha256 = hashlib.sha256(
            semantic.description.summary.encode("utf-8")
        ).hexdigest()
        if saved.alt_text_sha256 != expected_alt_text_sha256:
            raise ValueError("saved evidence does not match the diagram alt text")
        if saved.attachment_sha256 != canonical_sha256(
            semantic.graph.model_dump(mode="json")
        ):
            raise ValueError("saved graph attachment does not match the verified graph")
        expected_metadata_sha256 = canonical_sha256(
            {
                "graph_sha256": semantic.graph_sha256,
                "description_sha256": semantic.description_sha256,
                "rendered_html_sha256": semantic.rendered_html_sha256,
            }
        )
        if saved.metadata_sha256 != expected_metadata_sha256:
            raise ValueError("saved metadata does not match accessible diagram output")

        if isinstance(self.locator, EmbeddedImageOccurrenceLocator):
            if (
                not standalone
                or saved.page_number != self.locator.page_number
                or saved.image_xref != self.locator.image_xref
                or saved.occurrence_ordinal != self.locator.occurrence_ordinal
                or saved.image_stream_sha256 != self.locator.image_stream_sha256
            ):
                raise ValueError(
                    "embedded image locators require matching diagram saved evidence"
                )
        else:
            px0, py0, px1, py1 = self.locator.pixel_bbox
            scale_x, _, _, scale_y, offset_x, offset_y = self.locator.transform
            expected_bbox = (
                offset_x + scale_x * px0 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py1 / self.locator.source_height),
                offset_x + scale_x * px1 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py0 / self.locator.source_height),
            )
            if (
                not scanned
                or saved.page_number != self.locator.page_number
                or saved.image_stream_sha256 != self.locator.source_sha256
                or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(saved.diagram_bbox, expected_bbox)
                )
            ):
                raise ValueError(
                    "page raster locators require matching diagram saved evidence"
                )

        specialist_material = {
            "contract_kind": self.contract_kind,
            "locator": self.locator,
            "semantic_output": self.semantic_output,
            "normalized_source_sha256": self.normalized_source_sha256,
            "recognition_evidence": recognition_evidence,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("specialist_sha256 does not match the specialist output")
        contract_material = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("contract_sha256 does not match the complete contract")
        return self


class ChemicalStructurePdfContract(BaseModel):
    """Complete graph recognition, PDF association, and saved proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["chemical_structure"]
    locator: VisualLocator
    semantic_output: ChemicalStructureSemanticV1
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    verification_evidence: tuple[VerificationEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_variant_pairing_and_digests(self) -> "ChemicalStructurePdfContract":
        recognition = [
            item
            for item in self.verification_evidence
            if isinstance(item, ChemicalStructureRecognitionEvidenceV1)
        ]
        standalone = [
            item
            for item in self.verification_evidence
            if isinstance(item, StandaloneChemicalStructureSavedEvidenceV1)
        ]
        scanned = [
            item
            for item in self.verification_evidence
            if isinstance(item, ScannedRegionChemicalStructureSavedEvidenceV1)
        ]
        if len(recognition) != 1 or len(standalone) + len(scanned) != 1:
            raise ValueError(
                "chemical structures require exact recognition and saved evidence"
            )
        recognition_evidence = recognition[0]
        saved = standalone[0] if standalone else scanned[0]
        semantic = self.semantic_output
        verified_graph, verified_abbreviations = verify_chemical_abbreviations(
            semantic.graph, recognition_evidence.abbreviations
        )
        if (
            verified_graph.canonical_sha256 != semantic.graph_sha256
            or verified_abbreviations != recognition_evidence.abbreviations
            or recognition_evidence.normalized_source_sha256
            != self.normalized_source_sha256
            or recognition_evidence.graph_sha256 != semantic.graph_sha256
            or saved.graph_sha256 != semantic.graph_sha256
            or saved.description_sha256 != semantic.description_sha256
            or saved.abbreviation_evidence_sha256
            != recognition_evidence.abbreviation_evidence_sha256
        ):
            raise ValueError("chemical evidence disagrees with the specialist output")
        expected_alt_text_sha256 = hashlib.sha256(
            semantic.description.summary.encode("utf-8")
        ).hexdigest()
        if saved.alt_text_sha256 != expected_alt_text_sha256:
            raise ValueError("saved evidence does not match chemical alt text")
        if saved.attachment_sha256 != semantic.graph_sha256:
            raise ValueError("saved attachment does not match the verified graph")
        expected_metadata_sha256 = canonical_sha256(
            {
                "graph_sha256": semantic.graph_sha256,
                "graph_identifier": semantic.graph.graph_identifier,
                "description_sha256": semantic.description_sha256,
                "attachment_sha256": saved.attachment_sha256,
                "abbreviation_evidence_sha256": (
                    recognition_evidence.abbreviation_evidence_sha256
                ),
                "abbreviation_policy_version": (
                    recognition_evidence.abbreviation_policy_version
                ),
            }
        )
        if saved.metadata_sha256 != expected_metadata_sha256:
            raise ValueError("saved metadata does not match chemical output")

        if isinstance(self.locator, EmbeddedImageOccurrenceLocator):
            if (
                not standalone
                or saved.page_number != self.locator.page_number
                or saved.image_xref != self.locator.image_xref
                or saved.occurrence_ordinal != self.locator.occurrence_ordinal
                or saved.image_stream_sha256 != self.locator.image_stream_sha256
            ):
                raise ValueError(
                    "embedded image locators require matching chemical saved evidence"
                )
        else:
            px0, py0, px1, py1 = self.locator.pixel_bbox
            scale_x, _, _, scale_y, offset_x, offset_y = self.locator.transform
            expected_bbox = (
                offset_x + scale_x * px0 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py1 / self.locator.source_height),
                offset_x + scale_x * px1 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py0 / self.locator.source_height),
            )
            if (
                not scanned
                or saved.page_number != self.locator.page_number
                or saved.image_stream_sha256 != self.locator.source_sha256
                or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(saved.structure_bbox, expected_bbox)
                )
            ):
                raise ValueError(
                    "page raster locators require matching chemical saved evidence"
                )

        specialist_material = {
            "contract_kind": self.contract_kind,
            "locator": self.locator,
            "semantic_output": self.semantic_output,
            "normalized_source_sha256": self.normalized_source_sha256,
            "recognition_evidence": recognition_evidence,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("specialist_sha256 does not match the specialist output")
        contract_material = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("contract_sha256 does not match the complete contract")
        return self


class HandwrittenEquationContract(BaseModel):
    """Complete HMER specialist output and reverse-verification contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["handwritten_equation"]
    locator: VisualLocator
    semantic_output: SemanticOutput
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    verification_evidence: tuple[VerificationEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_variant_pairing_and_digests(
        self,
    ) -> "HandwrittenEquationContract":
        consensus = [
            item
            for item in self.verification_evidence
            if isinstance(item, HandwrittenEquationConsensusEvidenceV1)
        ]
        standalone = [
            item
            for item in self.verification_evidence
            if isinstance(item, StandaloneFormulaSavedEvidenceV1)
        ]
        scanned = [
            item
            for item in self.verification_evidence
            if isinstance(item, ScannedRegionFormulaSavedEvidenceV1)
        ]
        if len(consensus) != 1 or len(standalone) + len(scanned) != 1:
            raise ValueError(
                "handwritten equations require exact consensus and saved evidence"
            )
        consensus_evidence = consensus[0]
        saved = standalone[0] if standalone else scanned[0]
        if self.normalized_source_sha256 != consensus_evidence.source_sha256:
            raise ValueError("normalized source digest does not match HMER evidence")
        if any(
            item.mathml_sha256 != self.semantic_output.mathml_sha256
            for item in self.verification_evidence
        ):
            raise ValueError("evidence does not match the semantic output digest")
        expected_alt_text_sha256 = hashlib.sha256(
            self.semantic_output.alt_text.encode("utf-8")
        ).hexdigest()
        if saved.alt_text_sha256 != expected_alt_text_sha256:
            raise ValueError("saved evidence does not match the semantic alt text")

        if isinstance(self.locator, EmbeddedImageOccurrenceLocator):
            if (
                not standalone
                or saved.page_number != self.locator.page_number
                or saved.image_xref != self.locator.image_xref
                or saved.occurrence_ordinal != self.locator.occurrence_ordinal
                or saved.image_stream_sha256 != self.locator.image_stream_sha256
            ):
                raise ValueError(
                    "embedded image locators require matching standalone saved evidence"
                )
        else:
            px0, py0, px1, py1 = self.locator.pixel_bbox
            scale_x, _, _, scale_y, offset_x, offset_y = self.locator.transform
            expected_formula_bbox = (
                offset_x + scale_x * px0 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py1 / self.locator.source_height),
                offset_x + scale_x * px1 / self.locator.source_width,
                offset_y + scale_y * (1.0 - py0 / self.locator.source_height),
            )
            if (
                not scanned
                or saved.page_number != self.locator.page_number
                or saved.image_stream_sha256 != self.locator.source_sha256
                or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(
                        saved.formula_bbox, expected_formula_bbox
                    )
                )
            ):
                raise ValueError(
                    "page raster regions require matching scanned-region saved evidence"
                )

        specialist_material = {
            "contract_kind": self.contract_kind,
            "locator": self.locator,
            "semantic_output": self.semantic_output,
            "normalized_source_sha256": self.normalized_source_sha256,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("specialist_sha256 does not match the specialist output")
        contract_material = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("contract_sha256 does not match the complete contract")
        return self


VisualSemanticContract: TypeAlias = Annotated[
    PrintedEquationContract
    | ChemicalFormulaPdfContract
    | CommutativeDiagramPdfContract
    | ChemicalStructurePdfContract
    | HandwrittenEquationContract,
    Field(discriminator="contract_kind"),
]
VisualSemanticContractAdapter = TypeAdapter(VisualSemanticContract)


__all__ = [
    "ChemicalStructurePdfContract",
    "ChemicalStructureRecognitionEvidenceV1",
    "ChemicalStructureSemanticV1",
    "ChemicalFormulaPdfContract",
    "ChemicalFormulaRecognitionEvidenceV1",
    "ChemicalFormulaSemanticV1",
    "CommutativeDiagramPdfContract",
    "CommutativeDiagramRecognitionEvidenceV1",
    "CommutativeDiagramSemanticV1",
    "EmbeddedImageOccurrenceLocator",
    "FrozenPageRasterRegionLocator",
    "HandwrittenEquationConsensusEvidenceV1",
    "HandwrittenEquationContract",
    "MathMLExpressionV1",
    "PrintedEquationContract",
    "PrintedEquationRoundtripEvidenceV1",
    "ScannedRegionFormulaSavedEvidenceV1",
    "ScannedRegionChemicalStructureSavedEvidenceV1",
    "ScannedRegionChemicalFormulaSavedEvidenceV1",
    "ScannedRegionDiagramSavedEvidenceV1",
    "SemanticOutput",
    "SemanticOutputAdapter",
    "StandaloneFormulaSavedEvidenceV1",
    "StandaloneChemicalStructureSavedEvidenceV1",
    "StandaloneChemicalFormulaSavedEvidenceV1",
    "StandaloneDiagramSavedEvidenceV1",
    "VerificationEvidence",
    "VerificationEvidenceAdapter",
    "VisualLocator",
    "VisualLocatorAdapter",
    "VisualSemanticContract",
    "VisualSemanticContractAdapter",
    "canonical_json_bytes",
    "canonical_sha256",
]
