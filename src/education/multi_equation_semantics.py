"""Frozen atomic semantic ownership for one multi-equation raster group."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.multi_equation_region import MultiEquationRegionGroupV1
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)

_SHA256 = r"^[0-9a-f]{64}$"
_REGION_ID = r"^eqregion-v1-[0-9a-f]{24}$"
_MAX_INDEX = 25_000_000
_MAX_RENDER_SIGNATURES = 8


def _positive_finite_bbox(value: Any, *, label: str) -> Any:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must contain four values")
    if any(
        not isinstance(item, (int, float))
        or isinstance(item, bool)
        or not math.isfinite(float(item))
        or abs(float(item)) > _MAX_INDEX
        for item in value
    ):
        raise ValueError(f"{label} must contain bounded finite values")
    if float(value[0]) >= float(value[2]) or float(value[1]) >= float(value[3]):
        raise ValueError(f"{label} must have positive area")
    return value


def _validate_group_owners(
    group: MultiEquationRegionGroupV1,
    owners: tuple["MultiEquationSemanticOwnerV1", ...],
) -> None:
    child_region_ids = tuple(child.region_id for child in group.children)
    if group.disposition == "split_children":
        if len(owners) != len(group.children):
            raise ValueError("split groups require one owner per child")
        for ordinal, (owner, child) in enumerate(zip(owners, group.children)):
            if (
                owner.owner_kind != "multi_equation_child_v1"
                or owner.ordinal != ordinal
                or owner.region_ids != (child.region_id,)
                or owner.pixel_bbox != child.pixel_bbox
                or any(
                    abs(actual - wanted) > 1e-6
                    for actual, wanted in zip(owner.pdf_bbox, child.pdf_bbox)
                )
            ):
                raise ValueError("split semantic owner differs from child source")
        return
    if len(owners) != 1:
        raise ValueError("whole systems require exactly one owner")
    owner = owners[0]
    pixel_union = (
        min(child.pixel_bbox[0] for child in group.children),
        min(child.pixel_bbox[1] for child in group.children),
        max(child.pixel_bbox[2] for child in group.children),
        max(child.pixel_bbox[3] for child in group.children),
    )
    pdf_union = (
        min(child.pdf_bbox[0] for child in group.children),
        min(child.pdf_bbox[1] for child in group.children),
        max(child.pdf_bbox[2] for child in group.children),
        max(child.pdf_bbox[3] for child in group.children),
    )
    if (
        owner.owner_kind != "multi_equation_system_v1"
        or owner.ordinal != 0
        or owner.region_ids != child_region_ids
        or owner.pixel_bbox != pixel_union
        or any(
            abs(actual - wanted) > 1e-6
            for actual, wanted in zip(owner.pdf_bbox, pdf_union)
        )
    ):
        raise ValueError("whole-system owner differs from group union")


def _formula_bbox_for_owner(
    group: MultiEquationRegionGroupV1,
    owner: "MultiEquationSemanticOwnerV1",
) -> tuple[float, float, float, float]:
    """Map a detector crop into PDF default user space for /BBox."""

    matrix = tuple(float(value) for value in group.children[0].transform)
    px0, py0, px1, py1 = owner.pixel_bbox
    width = float(group.source_width)
    height = float(group.source_height)
    clip = (
        px0 / width,
        1.0 - (py1 / height),
        (px1 - px0) / width,
        (py1 - py0) / height,
    )
    return (
        matrix[4] + matrix[0] * clip[0],
        matrix[5] + matrix[3] * clip[1],
        matrix[4] + matrix[0] * (clip[0] + clip[2]),
        matrix[5] + matrix[3] * (clip[1] + clip[3]),
    )


class MultiEquationSemanticOwnerV1(BaseModel):
    """One verified semantic owner within an exact screenshot group."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_kind: Literal["multi_equation_child_v1", "multi_equation_system_v1"]
    ordinal: int = Field(ge=0, le=7, strict=True)
    region_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    pixel_bbox: tuple[int, int, int, int]
    pdf_bbox: tuple[float, float, float, float]
    semantic_output: MathMLExpressionV1
    normalized_source_sha256: str = Field(pattern=_SHA256)
    verification_evidence: PrintedEquationRoundtripEvidenceV1
    provider: Optional[str] = Field(default=None, max_length=200)
    model: Optional[str] = Field(default=None, max_length=200)
    owner_sha256: str = Field(pattern=_SHA256)

    @field_validator("region_ids")
    @classmethod
    def _unique_region_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            re.fullmatch(_REGION_ID, item) is None for item in value
        ):
            raise ValueError("semantic owner region identity is invalid")
        return value

    @field_validator("pixel_bbox", mode="before")
    @classmethod
    def _pixel_bbox(cls, value: Any) -> Any:
        _positive_finite_bbox(value, label="pixel_bbox")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
            raise ValueError("pixel_bbox must contain integers")
        return value

    @field_validator("pdf_bbox", mode="before")
    @classmethod
    def _pdf_bbox(cls, value: Any) -> Any:
        return _positive_finite_bbox(value, label="pdf_bbox")

    @field_validator("provider", "model")
    @classmethod
    def _bounded_identity(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (not value or not value.isprintable()):
            raise ValueError("provider identity must be bounded printable text")
        return value

    @model_validator(mode="after")
    def _validate_owner(self) -> "MultiEquationSemanticOwnerV1":
        evidence = self.verification_evidence
        if (
            not evidence.passed
            or evidence.ink_iou < evidence.required_ink_iou
            or evidence.pixel_similarity < evidence.required_pixel_similarity
            or evidence.source_sha256 != self.normalized_source_sha256
            or evidence.mathml_sha256 != self.semantic_output.mathml_sha256
        ):
            raise ValueError("semantic owner verification evidence does not pass")
        if self.owner_kind == "multi_equation_child_v1" and len(self.region_ids) != 1:
            raise ValueError("child owners require exactly one region")
        material = self.model_dump(mode="json", exclude={"owner_sha256"})
        if self.owner_sha256 != canonical_sha256(material):
            raise ValueError("semantic owner digest differs")
        return self


class MultiEquationSavedOwnerV1(BaseModel):
    """Saved-file identity for one Formula owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=0, le=7, strict=True)
    region_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    struct_parent: int = Field(ge=0, le=_MAX_INDEX, strict=True)
    mcid: int = Field(ge=0, le=_MAX_INDEX, strict=True)
    formula_bbox: tuple[float, float, float, float]
    mathml_sha256: str = Field(pattern=_SHA256)
    alt_text_sha256: str = Field(pattern=_SHA256)
    attachment_sha256: str = Field(pattern=_SHA256)
    backlink_count: Literal[1]
    parent_tree_count: Literal[1]

    @field_validator("formula_bbox", mode="before")
    @classmethod
    def _formula_bbox(cls, value: Any) -> Any:
        return _positive_finite_bbox(value, label="formula_bbox")

    @model_validator(mode="after")
    def _validate_attachment(self) -> "MultiEquationSavedOwnerV1":
        if self.attachment_sha256 != self.mathml_sha256:
            raise ValueError("saved MathML attachment digest differs")
        return self


class MultiEquationSavedEvidenceV1(BaseModel):
    """Aggregate evidence recovered from the reopened saved PDF."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: Literal["multi_equation_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256)
    page_number: int = Field(ge=1, le=_MAX_INDEX, strict=True)
    parent_occurrence_id: str = Field(pattern=r"^imgocc-v1-[0-9a-f]{24}$")
    saved_parent_occurrence_id: str = Field(pattern=r"^imgocc-v1-[0-9a-f]{24}$")
    image_xref: int = Field(ge=1, le=_MAX_INDEX, strict=True)
    image_index: int = Field(ge=0, le=_MAX_INDEX, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=_MAX_INDEX, strict=True)
    source_sha256: str = Field(pattern=_SHA256)
    parent_bbox: tuple[float, float, float, float]
    transform: tuple[float, float, float, float, float, float]
    disposition: Literal["split_children", "whole_system"]
    original_artifact_count: Literal[1]
    owners: tuple[MultiEquationSavedOwnerV1, ...] = Field(min_length=1, max_length=8)
    render_signatures: tuple[tuple[int, int, int, int, int, str], ...] = Field(
        min_length=1, max_length=_MAX_RENDER_SIGNATURES
    )

    @field_validator("parent_bbox", mode="before")
    @classmethod
    def _parent_bbox(cls, value: Any) -> Any:
        return _positive_finite_bbox(value, label="parent_bbox")

    @field_validator("transform", mode="before")
    @classmethod
    def _transform(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or len(value) != 6:
            raise ValueError("saved transform must contain six values")
        if any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            or abs(float(item)) > _MAX_INDEX
            for item in value
        ):
            raise ValueError("saved transform must contain bounded finite values")
        return value

    @field_validator("render_signatures")
    @classmethod
    def _render_signatures(
        cls, value: tuple[tuple[int, int, int, int, int, str], ...]
    ) -> tuple[tuple[int, int, int, int, int, str], ...]:
        for signature in value:
            if len(signature) != 6 or any(
                not isinstance(item, int) or isinstance(item, bool)
                for item in signature[:5]
            ):
                raise ValueError("saved render signature is invalid")
            if any(item <= 0 or item > _MAX_INDEX for item in signature[:5]):
                raise ValueError("saved render signature is unbounded")
            if (
                not isinstance(signature[5], str)
                or re.fullmatch(_SHA256, signature[5]) is None
            ):
                raise ValueError("saved render digest is invalid")
        return value

    @model_validator(mode="after")
    def _saved_occurrence_identity(self) -> "MultiEquationSavedEvidenceV1":
        identity = (
            f"{self.page_number}|{self.image_xref}|{self.image_index}|"
            f"{self.occurrence_ordinal}|"
            + ",".join(f"{value:.6f}" for value in self.parent_bbox)
        )
        expected = "imgocc-v1-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        if self.saved_parent_occurrence_id != expected:
            raise ValueError("saved parent occurrence identity differs")
        return self


class MultiEquationSemanticContractV1(BaseModel):
    """Complete atomic contract for one reviewed-but-unapproved group."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_kind: Literal["multi_equation_semantic_v1"]
    group: MultiEquationRegionGroupV1
    owners: tuple[MultiEquationSemanticOwnerV1, ...] = Field(min_length=1, max_length=8)
    saved_evidence: MultiEquationSavedEvidenceV1
    review_required: Literal[True]
    publication_authorized: Literal[False]
    specialist_sha256: str = Field(pattern=_SHA256)
    contract_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_contract(self) -> "MultiEquationSemanticContractV1":
        _validate_group_owners(self.group, self.owners)

        saved = self.saved_evidence
        if (
            saved.page_number != self.group.page_number
            or saved.parent_occurrence_id != self.group.parent_occurrence_id
            or saved.image_index != self.group.image_index
            or saved.occurrence_ordinal != self.group.occurrence_ordinal
            or saved.source_sha256 != self.group.source_sha256
            or saved.disposition != self.group.disposition
            or len(saved.owners) != len(self.owners)
            or any(
                abs(actual - wanted) > 1e-6
                for actual, wanted in zip(
                    saved.parent_bbox, self.group.children[0].parent_bbox
                )
            )
            or any(
                abs(actual - wanted) > 1e-6
                for actual, wanted in zip(
                    saved.transform, self.group.children[0].transform
                )
            )
        ):
            raise ValueError("saved evidence differs from semantic group")
        for owner, saved_owner in zip(self.owners, saved.owners):
            alt_sha256 = hashlib.sha256(
                owner.semantic_output.alt_text.encode("utf-8")
            ).hexdigest()
            if (
                saved_owner.ordinal != owner.ordinal
                or saved_owner.region_ids != owner.region_ids
                or saved_owner.mathml_sha256 != owner.semantic_output.mathml_sha256
                or saved_owner.alt_text_sha256 != alt_sha256
                or any(
                    abs(actual - wanted) > 1e-6
                    for actual, wanted in zip(
                        saved_owner.formula_bbox,
                        _formula_bbox_for_owner(self.group, owner),
                    )
                )
            ):
                raise ValueError("saved owner differs from verified semantics")

        specialist_material = {
            "contract_kind": self.contract_kind,
            "group": self.group,
            "owners": self.owners,
            "review_required": self.review_required,
            "publication_authorized": self.publication_authorized,
        }
        if self.specialist_sha256 != canonical_sha256(specialist_material):
            raise ValueError("multi-equation specialist digest differs")
        contract_material = {
            **specialist_material,
            "saved_evidence": self.saved_evidence,
            "specialist_sha256": self.specialist_sha256,
        }
        if self.contract_sha256 != canonical_sha256(contract_material):
            raise ValueError("multi-equation contract digest differs")
        return self


def build_multi_equation_semantic_owner(
    *,
    owner_kind: Literal["multi_equation_child_v1", "multi_equation_system_v1"],
    ordinal: int,
    region_ids: tuple[str, ...],
    pixel_bbox: tuple[int, int, int, int],
    pdf_bbox: tuple[float, float, float, float],
    semantic_output: MathMLExpressionV1,
    normalized_source_sha256: str,
    verification_evidence: PrintedEquationRoundtripEvidenceV1,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> MultiEquationSemanticOwnerV1:
    """Build one owner with its digest derived from every semantic field."""

    material = {
        "owner_kind": owner_kind,
        "ordinal": ordinal,
        "region_ids": region_ids,
        "pixel_bbox": pixel_bbox,
        "pdf_bbox": pdf_bbox,
        "semantic_output": semantic_output,
        "normalized_source_sha256": normalized_source_sha256,
        "verification_evidence": verification_evidence,
        "provider": provider,
        "model": model,
    }
    return MultiEquationSemanticOwnerV1(
        **material,
        owner_sha256=canonical_sha256(material),
    )


def build_multi_equation_semantic_contract(
    *,
    group: MultiEquationRegionGroupV1,
    owners: tuple[MultiEquationSemanticOwnerV1, ...],
    saved_evidence: MultiEquationSavedEvidenceV1,
) -> MultiEquationSemanticContractV1:
    """Build the review-gated contract after saved-file verification passes."""

    specialist_material = {
        "contract_kind": "multi_equation_semantic_v1",
        "group": group,
        "owners": owners,
        "review_required": True,
        "publication_authorized": False,
    }
    specialist_sha256 = canonical_sha256(specialist_material)
    contract_material = {
        **specialist_material,
        "saved_evidence": saved_evidence,
        "specialist_sha256": specialist_sha256,
    }
    return MultiEquationSemanticContractV1(
        **contract_material,
        contract_sha256=canonical_sha256(contract_material),
    )


def validate_multi_equation_semantic_plan(
    group: Any,
    owners: Any,
) -> tuple[MultiEquationRegionGroupV1, tuple[MultiEquationSemanticOwnerV1, ...]]:
    """Validate complete ordered ownership before any PDF mutation begins."""

    validated_group = MultiEquationRegionGroupV1.model_validate(group)
    if not isinstance(owners, (list, tuple)):
        raise ValueError("multi-equation owners must be an ordered sequence")
    validated_owners = tuple(
        MultiEquationSemanticOwnerV1.model_validate(owner) for owner in owners
    )
    _validate_group_owners(validated_group, validated_owners)
    return validated_group, validated_owners


def multi_equation_artifact_available(
    contract: MultiEquationSemanticContractV1,
    *,
    human_approved: bool,
) -> bool:
    """Require an explicit human approval signal before artifact availability."""

    validated = MultiEquationSemanticContractV1.model_validate(contract)
    return bool(
        human_approved
        and validated.review_required
        and not validated.publication_authorized
        and validated.saved_evidence.passed
    )


__all__ = [
    "MultiEquationSavedEvidenceV1",
    "MultiEquationSavedOwnerV1",
    "MultiEquationSemanticContractV1",
    "MultiEquationSemanticOwnerV1",
    "build_multi_equation_semantic_contract",
    "build_multi_equation_semantic_owner",
    "multi_equation_artifact_available",
    "validate_multi_equation_semantic_plan",
]
