"""Strict, passive contracts for durable visual-semantic provenance.

The only active specialist is ``printed_equation``. Reserved or unknown kinds
are deliberately rejected until a complete, reviewed variant is added here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from src.education.equation_region_contract import PageRasterRegionLocator

_MAX_COLLECTION_ITEMS = 4_096
_MAX_CANONICAL_DEPTH = 32
_MAX_CANONICAL_INTEGER = 9_007_199_254_740_991
_MAX_CANONICAL_STRING = 131_072
_MAX_DOCUMENT_INDEX = 25_000_000
_MAX_MATHML_BYTES = 32_768
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _passive_json_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON value without invoking arbitrary object hooks."""
    if depth > _MAX_CANONICAL_DEPTH:
        raise ValueError("canonical value exceeds the nesting limit")
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_CANONICAL_INTEGER:
            raise ValueError("canonical integer exceeds the exact JSON range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > _MAX_CANONICAL_INTEGER:
            raise ValueError("canonical float must be bounded and finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_CANONICAL_STRING or not value.isprintable():
            raise ValueError("canonical text must be bounded and printable")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError("canonical mapping exceeds the item limit")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            if not key or len(key) > 256 or key != key.strip() or not key.isprintable():
                raise ValueError(
                    "canonical mapping keys must be bounded printable text"
                )
            result[key] = _passive_json_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError("canonical sequence exceeds the item limit")
        return [_passive_json_value(item, depth=depth + 1) for item in value]
    raise TypeError("canonical values must contain only passive JSON data")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode bounded passive data with stable mapping and sequence semantics."""
    return json.dumps(
        _passive_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of :func:`canonical_json_bytes`."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


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


SemanticOutput: TypeAlias = Annotated[
    MathMLExpressionV1,
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


VerificationEvidence: TypeAlias = Annotated[
    PrintedEquationRoundtripEvidenceV1
    | StandaloneFormulaSavedEvidenceV1
    | ScannedRegionFormulaSavedEvidenceV1,
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


VisualSemanticContract: TypeAlias = Annotated[
    PrintedEquationContract,
    Field(discriminator="contract_kind"),
]
VisualSemanticContractAdapter = TypeAdapter(VisualSemanticContract)


__all__ = [
    "EmbeddedImageOccurrenceLocator",
    "FrozenPageRasterRegionLocator",
    "MathMLExpressionV1",
    "PrintedEquationContract",
    "PrintedEquationRoundtripEvidenceV1",
    "ScannedRegionFormulaSavedEvidenceV1",
    "SemanticOutput",
    "SemanticOutputAdapter",
    "StandaloneFormulaSavedEvidenceV1",
    "VerificationEvidence",
    "VerificationEvidenceAdapter",
    "VisualLocator",
    "VisualLocatorAdapter",
    "VisualSemanticContract",
    "VisualSemanticContractAdapter",
    "canonical_json_bytes",
    "canonical_sha256",
]
