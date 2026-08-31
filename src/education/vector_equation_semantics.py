"""Frozen semantics and saved-file evidence for exact vector equations."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.vector_equation_cluster import (
    MAX_VECTOR_OBJECT_NUMBER,
    MAX_VECTOR_OPERATOR_SPANS,
    MAX_VECTOR_RESOURCES,
    VectorEquationClusterV1,
    VectorResourceIdentityV1,
)
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_PROVIDER_IDENTITY = 200
_MAX_RENDER_SIGNATURES = 16


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VectorEquationSemanticPlanV1(_FrozenModel):
    """Verified semantics bound to one pre-mutation #229 cluster."""

    plan_kind: Literal["vector_equation_semantic_plan_v1"]
    cluster_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_output: MathMLExpressionV1
    verification_evidence: PrintedEquationRoundtripEvidenceV1
    provider: str = Field(min_length=1, max_length=_MAX_PROVIDER_IDENTITY)
    model: str = Field(min_length=1, max_length=_MAX_PROVIDER_IDENTITY)
    review_required: Literal[True]
    publication_authorized: Literal[False]
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("provider", "model")
    @classmethod
    def _printable_identity(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("provider identity must be bounded printable text")
        return value

    @model_validator(mode="after")
    def _bind_evidence(self) -> "VectorEquationSemanticPlanV1":
        if not self.verification_evidence.passed:
            raise ValueError("vector equation verification did not pass")
        if (
            self.verification_evidence.source_sha256 != self.normalized_source_sha256
            or self.verification_evidence.mathml_sha256
            != self.semantic_output.mathml_sha256
        ):
            raise ValueError("vector equation evidence is not source-bound")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("plan_sha256 does not match the semantic plan")
        return self


class VectorMarkedSpanSavedV1(_FrozenModel):
    """One reopened marked-content wrapper around one original operator span."""

    span_kind: Literal["vector_marked_span_saved_v1"]
    ordinal: int = Field(ge=0, lt=MAX_VECTOR_OPERATOR_SPANS, strict=True)
    content_stream_index: int = Field(ge=0, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    stream_object_number: int = Field(ge=1, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    stream_generation: int = Field(ge=0, le=65_535, strict=True)
    mcid: int = Field(ge=0, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    first_operator: int = Field(ge=0, le=10_000_000, strict=True)
    last_operator: int = Field(ge=0, le=10_000_000, strict=True)
    operator_count: int = Field(ge=1, le=1_000_000, strict=True)
    operators_sha256: str = Field(pattern=_SHA256_PATTERN)
    graphics_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    unwrapped_stream_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _exact_span(self) -> "VectorMarkedSpanSavedV1":
        if (
            self.last_operator < self.first_operator
            or self.operator_count != self.last_operator - self.first_operator + 1
        ):
            raise ValueError("saved operator span is inconsistent")
        return self


class VectorFormulaSavedEvidenceV1(_FrozenModel):
    """Reverse-verification evidence from the reopened associated PDF."""

    evidence_kind: Literal["vector_formula_saved_v1"]
    passed: Literal[True]
    saved_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    struct_parent: int = Field(ge=0, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    formula_object_number: int = Field(ge=1, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    formula_generation: int = Field(ge=0, le=65_535, strict=True)
    marked_spans: tuple[VectorMarkedSpanSavedV1, ...] = Field(
        min_length=1, max_length=MAX_VECTOR_OPERATOR_SPANS
    )
    formula_bbox: tuple[float, float, float, float]
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)
    alt_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    resource_identities: tuple[VectorResourceIdentityV1, ...] = Field(
        max_length=MAX_VECTOR_RESOURCES
    )
    render_signatures: tuple[tuple[int, int, int, int, int, str], ...] = Field(
        min_length=1, max_length=_MAX_RENDER_SIGNATURES
    )
    page_text_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("formula_bbox", mode="before")
    @classmethod
    def _bounded_bbox(cls, value: Any) -> Any:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or any(
                not isinstance(item, (int, float))
                or isinstance(item, bool)
                or not math.isfinite(float(item))
                or abs(float(item)) > 25_000_000
                for item in value
            )
        ):
            raise ValueError("formula_bbox must contain four bounded finite numbers")
        if float(value[2]) <= float(value[0]) or float(value[3]) <= float(value[1]):
            raise ValueError("formula_bbox must have positive area")
        return value

    @field_validator("render_signatures")
    @classmethod
    def _render_digests(cls, value):
        for signature in value:
            if (
                len(signature) != 6
                or any(
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item <= 0
                    or item > 100_000_000
                    for item in signature[:5]
                )
                or not isinstance(signature[5], str)
            ):
                raise ValueError("render signature is malformed")
            if re.fullmatch(_SHA256_PATTERN, signature[5]) is None:
                raise ValueError("render signature digest is malformed")
        return value

    @model_validator(mode="after")
    def _complete_saved_spans(self) -> "VectorFormulaSavedEvidenceV1":
        if tuple(span.ordinal for span in self.marked_spans) != tuple(
            range(len(self.marked_spans))
        ):
            raise ValueError("saved span ordinals must be complete and ordered")
        if len({span.mcid for span in self.marked_spans}) != len(self.marked_spans):
            raise ValueError("saved span MCIDs must be unique")
        if len(set(self.render_signatures)) != len(self.render_signatures):
            raise ValueError("render signatures must be unique")
        return self


class VectorEquationSemanticContractV1(_FrozenModel):
    """One review-gated vector Formula result consumable by mixed STEM routing."""

    contract_kind: Literal["vector_equation_semantic_v1"]
    cluster: VectorEquationClusterV1
    semantic_plan: VectorEquationSemanticPlanV1
    saved_evidence: VectorFormulaSavedEvidenceV1
    review_required: Literal[True]
    publication_authorized: Literal[False]
    specialist_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _bind_complete_result(self) -> "VectorEquationSemanticContractV1":
        if self.semantic_plan.cluster_sha256 != self.cluster.cluster_sha256:
            raise ValueError("semantic plan identifies another vector cluster")
        if self.saved_evidence.page_number != self.cluster.page_number or any(
            abs(current - expected) > 1e-6
            for current, expected in zip(
                self.saved_evidence.formula_bbox, self.cluster.pdf_bbox
            )
        ):
            raise ValueError("saved page or bbox differs from the source cluster")
        if len(self.saved_evidence.marked_spans) != len(self.cluster.operator_spans):
            raise ValueError("saved evidence does not cover every operator span")
        for ordinal, (saved, original) in enumerate(
            zip(self.saved_evidence.marked_spans, self.cluster.operator_spans)
        ):
            if (
                saved.ordinal != ordinal
                or saved.first_operator != original.first_operator
                or saved.last_operator != original.last_operator
                or saved.operator_count != original.operator_count
                or saved.operators_sha256 != original.operators_sha256
                or saved.graphics_state_sha256 != original.graphics_state_sha256
            ):
                raise ValueError("saved span differs from the source cluster")
        if (
            self.saved_evidence.mathml_sha256
            != self.semantic_plan.semantic_output.mathml_sha256
            or self.saved_evidence.alt_text_sha256
            != hashlib.sha256(
                self.semantic_plan.semantic_output.alt_text.encode("utf-8")
            ).hexdigest()
            or self.saved_evidence.resource_identities != self.cluster.resources
        ):
            raise ValueError("saved semantics or resources differ from the plan")
        specialist = {
            "contract_kind": self.contract_kind,
            "cluster": self.cluster,
            "semantic_plan": self.semantic_plan,
            "review_required": self.review_required,
            "publication_authorized": self.publication_authorized,
        }
        if self.specialist_sha256 != canonical_sha256(specialist):
            raise ValueError("specialist_sha256 does not match the result")
        material = {
            **specialist,
            "saved_evidence": self.saved_evidence,
            "specialist_sha256": self.specialist_sha256,
        }
        if self.contract_sha256 != canonical_sha256(material):
            raise ValueError("contract_sha256 does not match the result")
        return self

    def authorizes_artifact_availability(self) -> bool:
        """Verification never substitutes for the mandatory human approval."""

        return self.publication_authorized and not self.review_required


def build_vector_equation_semantic_plan(
    *,
    cluster_sha256: str,
    normalized_source_sha256: str,
    semantic_output: MathMLExpressionV1,
    verification_evidence: PrintedEquationRoundtripEvidenceV1,
    provider: str,
    model: str,
) -> VectorEquationSemanticPlanV1:
    material = {
        "plan_kind": "vector_equation_semantic_plan_v1",
        "cluster_sha256": cluster_sha256,
        "normalized_source_sha256": normalized_source_sha256,
        "semantic_output": semantic_output,
        "verification_evidence": verification_evidence,
        "provider": provider,
        "model": model,
        "review_required": True,
        "publication_authorized": False,
    }
    return VectorEquationSemanticPlanV1(
        **material,
        plan_sha256=canonical_sha256(material),
    )


def build_vector_equation_semantic_contract(
    *,
    cluster: VectorEquationClusterV1,
    semantic_plan: VectorEquationSemanticPlanV1,
    saved_evidence: VectorFormulaSavedEvidenceV1,
) -> VectorEquationSemanticContractV1:
    specialist = {
        "contract_kind": "vector_equation_semantic_v1",
        "cluster": cluster,
        "semantic_plan": semantic_plan,
        "review_required": True,
        "publication_authorized": False,
    }
    specialist_sha256 = canonical_sha256(specialist)
    material = {
        **specialist,
        "saved_evidence": saved_evidence,
        "specialist_sha256": specialist_sha256,
    }
    return VectorEquationSemanticContractV1(
        **material,
        contract_sha256=canonical_sha256(material),
    )


__all__ = [
    "VectorEquationSemanticContractV1",
    "VectorEquationSemanticPlanV1",
    "VectorFormulaSavedEvidenceV1",
    "VectorMarkedSpanSavedV1",
    "build_vector_equation_semantic_contract",
    "build_vector_equation_semantic_plan",
]
