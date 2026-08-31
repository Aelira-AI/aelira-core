"""Frozen contracts for atomic mixed STEM PDF composition and approval."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_json_bytes, canonical_sha256
from src.education.mixed_stem_regions import (
    MAX_MIXED_STEM_DOCUMENT_BYTES,
    MAX_MIXED_STEM_PAGES,
    MAX_MIXED_STEM_REGIONS,
    MixedStemRegionV1,
    MixedStemRoutingResultV1,
    NativeTextRegionSourceV1,
    OpenStemRouteV1,
    VerifiedStemRouteV1,
)
from src.education.molecular_graph import canonical_molecular_graph_bytes
from src.education.multi_equation_semantics import MultiEquationSemanticContractV1
from src.education.remediation.mixed_stem_region_router import (
    specialist_contract_matches_region,
)
from src.education.vector_equation_semantics import VectorEquationSemanticContractV1
from src.education.visual_semantic_contract import (
    ChemicalFormulaPdfContract,
    ChemicalStructurePdfContract,
    CommutativeDiagramPdfContract,
    HandwrittenEquationContract,
)

MIXED_STEM_COMPOSITION_POLICY_VERSION = "mixed-stem-composition-v1"
MIXED_STEM_COMPOSITION_BUDGET_VERSION = "mixed-stem-composition-budget-v1"
MAX_COMPOSITION_CONTRACT_BYTES = 32 * 1024 * 1024
MAX_COMPOSITION_DESCRIPTION_BYTES = 2 * 1024 * 1024
MAX_COMPOSITION_ATTACHMENT_BYTES = 16 * 1024 * 1024
MAX_COMPOSITION_STRUCTURE_ELEMENTS = 512
MAX_COMPOSITION_OUTPUT_BYTES = 768 * 1024 * 1024
MAX_COMPOSITION_REVERSE_VERIFY_BYTES = 768 * 1024 * 1024
MAX_COMPOSITION_APPROVAL_SECONDS = 7 * 24 * 60 * 60

_SHA256 = r"^[0-9a-f]{64}$"
_REGION_ID = r"^stemregion-v1-[0-9a-f]{24}$"


def _validate_sha256_values(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in values
    ):
        raise ValueError(f"{label} must contain canonical SHA-256 digests")
    return values


class MixedStemCompositionRejected(ValueError):
    """The requested composition cannot prove one atomic accessible output."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


SpecialistContract: TypeAlias = (
    MultiEquationSemanticContractV1
    | VectorEquationSemanticContractV1
    | HandwrittenEquationContract
    | ChemicalFormulaPdfContract
    | ChemicalStructurePdfContract
    | CommutativeDiagramPdfContract
)

_APPROVED_CONTRACT_TYPES: dict[str, type[BaseModel]] = {
    "printed_equation": MultiEquationSemanticContractV1,
    "vector_equation": VectorEquationSemanticContractV1,
    "handwritten_equation": HandwrittenEquationContract,
    "chemical_formula": ChemicalFormulaPdfContract,
    "chemical_structure": ChemicalStructurePdfContract,
    "commutative_diagram": CommutativeDiagramPdfContract,
}


def _contract_sha256(contract: SpecialistContract) -> str:
    return canonical_sha256(contract.model_dump(mode="json"))


def _accessible_text(contract: SpecialistContract) -> str:
    if isinstance(contract, MultiEquationSemanticContractV1):
        return " ".join(owner.semantic_output.alt_text for owner in contract.owners)
    if isinstance(contract, VectorEquationSemanticContractV1):
        return contract.semantic_plan.semantic_output.alt_text
    if isinstance(contract, HandwrittenEquationContract):
        return contract.semantic_output.alt_text
    if isinstance(contract, ChemicalFormulaPdfContract):
        return contract.semantic_output.verified_notation.speech
    if isinstance(contract, ChemicalStructurePdfContract):
        description = contract.semantic_output.description
        return " ".join(
            (
                description.summary,
                *description.atoms,
                *description.bonds,
                description.topology,
            )
        )
    description = contract.semantic_output.description
    return " ".join(
        (
            description.summary,
            *description.objects,
            *description.arrows,
            *description.paths,
            *description.relations,
        )
    )


def _attachment_payloads(contract: SpecialistContract) -> tuple[bytes, ...]:
    if isinstance(contract, MultiEquationSemanticContractV1):
        return tuple(
            owner.semantic_output.mathml.encode("utf-8") for owner in contract.owners
        )
    if isinstance(contract, VectorEquationSemanticContractV1):
        return (contract.semantic_plan.semantic_output.mathml.encode("utf-8"),)
    if isinstance(contract, HandwrittenEquationContract):
        return (contract.semantic_output.mathml.encode("utf-8"),)
    if isinstance(contract, ChemicalFormulaPdfContract):
        return (contract.semantic_output.verified_notation.mathml.encode("utf-8"),)
    if isinstance(contract, ChemicalStructurePdfContract):
        return (canonical_molecular_graph_bytes(contract.semantic_output.graph),)
    return (
        canonical_json_bytes(contract.semantic_output.graph.model_dump(mode="json")),
    )


def _structure_role(region_kind: str) -> Literal["P", "Formula", "Figure"]:
    if region_kind == "native_text":
        return "P"
    if region_kind in {
        "printed_equation",
        "vector_equation",
        "handwritten_equation",
        "chemical_formula",
    }:
        return "Formula"
    return "Figure"


class MixedStemCompositionBudgetV1(_FrozenModel):
    """Measured bounded input and planned output work."""

    budget_kind: Literal["mixed_stem_composition_budget_v1"]
    policy_version: Literal["mixed-stem-composition-budget-v1"]
    source_bytes: int = Field(ge=1, le=MAX_MIXED_STEM_DOCUMENT_BYTES, strict=True)
    page_count: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    region_count: int = Field(ge=1, le=MAX_MIXED_STEM_REGIONS, strict=True)
    contract_count: int = Field(ge=0, le=MAX_MIXED_STEM_REGIONS, strict=True)
    contract_bytes: int = Field(ge=0, le=MAX_COMPOSITION_CONTRACT_BYTES, strict=True)
    description_bytes: int = Field(
        ge=0, le=MAX_COMPOSITION_DESCRIPTION_BYTES, strict=True
    )
    attachment_bytes: int = Field(
        ge=0, le=MAX_COMPOSITION_ATTACHMENT_BYTES, strict=True
    )
    structure_elements: int = Field(
        ge=1, le=MAX_COMPOSITION_STRUCTURE_ELEMENTS, strict=True
    )


class MixedStemCompositionEntryV1(_FrozenModel):
    """One source-bound item in canonical accessible reading order."""

    entry_kind: Literal["mixed_stem_composition_entry_v1"]
    ordinal: int = Field(ge=0, le=MAX_MIXED_STEM_REGIONS - 1, strict=True)
    region_id: str = Field(pattern=_REGION_ID)
    region_kind: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    pdf_bbox: tuple[float, float, float, float]
    source_sha256: str = Field(pattern=_SHA256)
    region_sha256: str = Field(pattern=_SHA256)
    structure_role: Literal["P", "Formula", "Figure"]
    structure_element_count: int = Field(
        ge=1, le=MAX_COMPOSITION_STRUCTURE_ELEMENTS, strict=True
    )
    contract_kind: str = Field(default="", max_length=128)
    contract_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    accessible_text: str = Field(min_length=1, max_length=262_144)
    attachment_sha256: tuple[str, ...] = Field(
        default=(), max_length=MAX_COMPOSITION_STRUCTURE_ELEMENTS
    )
    entry_sha256: str = Field(pattern=_SHA256)

    @field_validator("accessible_text")
    @classmethod
    def _printable_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError(
                "composition accessible text must be trimmed printable text"
            )
        return value

    @model_validator(mode="after")
    def _validate_entry(self) -> "MixedStemCompositionEntryV1":
        is_native = self.region_kind == "native_text"
        if is_native != (self.structure_role == "P"):
            raise ValueError("native composition role differs")
        if is_native and (
            self.contract_kind or self.contract_sha256 or self.attachment_sha256
        ):
            raise ValueError("native text cannot carry a specialist contract")
        if not is_native and (not self.contract_kind or not self.contract_sha256):
            raise ValueError("visual composition requires a specialist contract")
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.attachment_sha256
        ):
            raise ValueError("attachment digests must be canonical SHA-256")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("composition entry digest differs")
        return self


class MixedStemLongDescriptionItemV1(_FrozenModel):
    """One mechanically rendered specialist item in the document description."""

    item_kind: Literal["mixed_stem_long_description_item_v1"]
    ordinal: int = Field(ge=0, le=MAX_MIXED_STEM_REGIONS - 1, strict=True)
    region_id: str = Field(pattern=_REGION_ID)
    region_kind: str = Field(min_length=1, max_length=64)
    contract_sha256: str = Field(pattern=_SHA256)
    accessible_text: str = Field(min_length=1, max_length=262_144)
    item_sha256: str = Field(pattern=_SHA256)

    @field_validator("accessible_text")
    @classmethod
    def _printable_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("long-description item must be trimmed printable text")
        return value

    @model_validator(mode="after")
    def _validate_item(self) -> "MixedStemLongDescriptionItemV1":
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"item_sha256"})
        )
        if self.item_sha256 != expected:
            raise ValueError("long-description item digest differs")
        return self

    def render(self) -> str:
        label = self.region_kind.replace("_", " ")
        return f"{self.ordinal + 1}. {label}: {self.accessible_text}"


class MixedStemCompositionPlanV1(_FrozenModel):
    """Canonical source-derived plan consumed by the atomic PDF writer."""

    plan_kind: Literal["mixed_stem_composition_plan_v1"]
    policy_version: Literal["mixed-stem-composition-v1"]
    source_sha256: str = Field(pattern=_SHA256)
    routing: MixedStemRoutingResultV1
    entries: tuple[MixedStemCompositionEntryV1, ...] = Field(
        min_length=1, max_length=MAX_MIXED_STEM_REGIONS
    )
    long_description_items: tuple[MixedStemLongDescriptionItemV1, ...] = Field(
        max_length=MAX_MIXED_STEM_REGIONS
    )
    long_description_text: str = Field(max_length=MAX_COMPOSITION_DESCRIPTION_BYTES)
    long_description_sha256: str = Field(pattern=_SHA256)
    contract_sha256: tuple[str, ...] = Field(max_length=MAX_MIXED_STEM_REGIONS)
    budget: MixedStemCompositionBudgetV1
    plan_sha256: str = Field(pattern=_SHA256)

    @field_validator("contract_sha256")
    @classmethod
    def _contract_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sha256_values(value, "composition contracts")

    @model_validator(mode="after")
    def _validate_plan(self) -> "MixedStemCompositionPlanV1":
        graph = self.routing.graph
        if self.source_sha256 != graph.document_sha256:
            raise ValueError("composition source differs from graph")
        if len(self.entries) != len(graph.regions):
            raise ValueError("composition entries differ from graph regions")
        if self.budget.region_count != len(self.entries):
            raise ValueError("composition region budget differs")
        if self.budget.page_count != graph.page_count:
            raise ValueError("composition page budget differs")
        for ordinal, (entry, region) in enumerate(zip(self.entries, graph.regions)):
            if (
                entry.ordinal != ordinal
                or entry.region_id != region.region_id
                or entry.region_kind != region.region_kind
                or entry.page_number != region.page_number
                or entry.pdf_bbox != region.pdf_bbox
                or entry.source_sha256 != region.source_sha256
                or entry.region_sha256 != region.region_sha256
            ):
                raise ValueError("composition entry differs from ordered graph")
        visuals = tuple(
            entry for entry in self.entries if entry.region_kind != "native_text"
        )
        if len(visuals) != len(self.long_description_items):
            raise ValueError("long description does not cover every visual")
        for ordinal, (entry, item) in enumerate(
            zip(visuals, self.long_description_items)
        ):
            if (
                item.ordinal != ordinal
                or item.region_id != entry.region_id
                or item.region_kind != entry.region_kind
                or item.contract_sha256 != entry.contract_sha256
                or item.accessible_text != entry.accessible_text
            ):
                raise ValueError("long-description item differs from visual entry")
        rendered = "\n".join(item.render() for item in self.long_description_items)
        if self.long_description_text != rendered:
            raise ValueError("long-description text is not canonical")
        if (
            hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            != self.long_description_sha256
        ):
            raise ValueError("long-description digest differs")
        if self.contract_sha256 != tuple(entry.contract_sha256 for entry in visuals):
            raise ValueError("composition contract digest order differs")
        if self.budget.contract_count != len(self.contract_sha256):
            raise ValueError("composition contract budget differs")
        expected = canonical_sha256(self.canonical_identity())
        if self.plan_sha256 != expected:
            raise ValueError("composition plan digest differs")
        return self

    def canonical_identity(self) -> dict[str, Any]:
        return {
            "plan_kind": self.plan_kind,
            "policy_version": self.policy_version,
            "source_sha256": self.source_sha256,
            "routing_result_sha256": self.routing.result_sha256,
            "entries": [entry.model_dump(mode="json") for entry in self.entries],
            "long_description_items": [
                item.model_dump(mode="json") for item in self.long_description_items
            ],
            "long_description_text": self.long_description_text,
            "long_description_sha256": self.long_description_sha256,
            "contract_sha256": list(self.contract_sha256),
            "budget": self.budget.model_dump(mode="json"),
        }


class MixedStemSavedCompositionEvidenceV1(_FrozenModel):
    """Aggregate proof read from the reopened combined candidate."""

    evidence_kind: Literal["mixed_stem_saved_composition_evidence_v1"]
    output_sha256: str = Field(pattern=_SHA256)
    output_bytes: int = Field(ge=1, le=MAX_COMPOSITION_OUTPUT_BYTES, strict=True)
    page_count: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    plan_sha256: str = Field(pattern=_SHA256)
    structure_sha256: str = Field(pattern=_SHA256)
    parent_tree_sha256: str = Field(pattern=_SHA256)
    attachment_sha256: tuple[str, ...] = Field(
        max_length=MAX_COMPOSITION_STRUCTURE_ELEMENTS
    )
    long_description_sha256: str = Field(pattern=_SHA256)
    render_sha256: tuple[str, ...] = Field(max_length=MAX_MIXED_STEM_PAGES * 2)
    visible_text_sha256: tuple[str, ...] = Field(max_length=MAX_MIXED_STEM_PAGES)
    reverse_verified_bytes: int = Field(
        ge=1, le=MAX_COMPOSITION_REVERSE_VERIFY_BYTES, strict=True
    )
    evidence_sha256: str = Field(pattern=_SHA256)

    @field_validator("attachment_sha256", "render_sha256", "visible_text_sha256")
    @classmethod
    def _evidence_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sha256_values(value, "saved composition evidence")

    @model_validator(mode="after")
    def _validate_evidence(self) -> "MixedStemSavedCompositionEvidenceV1":
        if self.reverse_verified_bytes != self.output_bytes:
            raise ValueError("reverse verification did not cover exact output bytes")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )
        if self.evidence_sha256 != expected:
            raise ValueError("saved composition evidence digest differs")
        return self


class MixedStemCompositionResultV1(_FrozenModel):
    """Review-gated result that never self-authorizes availability."""

    result_kind: Literal["mixed_stem_composition_result_v1"]
    plan: MixedStemCompositionPlanV1
    evidence: MixedStemSavedCompositionEvidenceV1
    review_required: Literal[True]
    publication_authorized: Literal[False]
    result_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_result(self) -> "MixedStemCompositionResultV1":
        if (
            self.evidence.plan_sha256 != self.plan.plan_sha256
            or self.evidence.page_count != self.plan.routing.graph.page_count
            or self.evidence.long_description_sha256
            != self.plan.long_description_sha256
            or self.evidence.attachment_sha256
            != tuple(
                digest
                for entry in self.plan.entries
                for digest in entry.attachment_sha256
            )
            or len(self.evidence.render_sha256) != self.evidence.page_count * 2
            or len(self.evidence.visible_text_sha256) != self.evidence.page_count
        ):
            raise ValueError("composition evidence differs from plan")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("composition result digest differs")
        return self

    def authorizes_artifact_availability(self) -> bool:
        return False


class MixedStemCompositionApprovalV1(_FrozenModel):
    """One bounded human approval for the exact complete composition review."""

    approval_kind: Literal["mixed_stem_composition_approval_v1"]
    output_sha256: str = Field(pattern=_SHA256)
    result_sha256: str = Field(pattern=_SHA256)
    plan_sha256: str = Field(pattern=_SHA256)
    contract_sha256: tuple[str, ...] = Field(max_length=MAX_MIXED_STEM_REGIONS)
    review_sha256: str = Field(pattern=_SHA256)
    approved_at: datetime
    expires_at: datetime
    human_approved: Literal[True]
    approval_sha256: str = Field(pattern=_SHA256)

    @field_validator("contract_sha256")
    @classmethod
    def _approval_contract_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sha256_values(value, "approval contracts")

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approval timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_approval(self) -> "MixedStemCompositionApprovalV1":
        seconds = (self.expires_at - self.approved_at).total_seconds()
        if not 0 < seconds <= MAX_COMPOSITION_APPROVAL_SECONDS:
            raise ValueError("composition approval lifetime is invalid")
        expected = canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"approval_sha256"},
            )
        )
        if self.approval_sha256 != expected:
            raise ValueError("composition approval digest differs")
        return self


def _validated_contract(
    region: MixedStemRegionV1,
    route: VerifiedStemRouteV1,
    contract: BaseModel,
) -> SpecialistContract:
    expected_type = _APPROVED_CONTRACT_TYPES.get(region.region_kind)
    if expected_type is None or type(contract) is not expected_type:
        raise MixedStemCompositionRejected("mixed_stem_composition_contract_type")
    try:
        checked = expected_type.model_validate_json(contract.model_dump_json())
    except (TypeError, ValueError) as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_contract_invalid"
        ) from exc
    if (
        not specialist_contract_matches_region(region, checked)
        or checked.contract_kind != route.contract_kind
        or _contract_sha256(checked) != route.contract_sha256
    ):
        raise MixedStemCompositionRejected("mixed_stem_composition_contract_mismatch")
    return checked  # type: ignore[return-value]


def _build_entry(
    ordinal: int,
    region: MixedStemRegionV1,
    contract: SpecialistContract | None,
) -> MixedStemCompositionEntryV1:
    if contract is None:
        if not isinstance(region.source, NativeTextRegionSourceV1):
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_contract_missing"
            )
        accessible_text = region.source.text
        contract_kind = ""
        contract_sha256 = ""
        attachments: tuple[bytes, ...] = ()
        element_count = 1
    else:
        accessible_text = _accessible_text(contract)
        contract_kind = contract.contract_kind
        contract_sha256 = _contract_sha256(contract)
        attachments = _attachment_payloads(contract)
        element_count = (
            len(contract.owners)
            if isinstance(contract, MultiEquationSemanticContractV1)
            else 1
        )
    fields: dict[str, Any] = {
        "entry_kind": "mixed_stem_composition_entry_v1",
        "ordinal": ordinal,
        "region_id": region.region_id,
        "region_kind": region.region_kind,
        "page_number": region.page_number,
        "pdf_bbox": region.pdf_bbox,
        "source_sha256": region.source_sha256,
        "region_sha256": region.region_sha256,
        "structure_role": _structure_role(region.region_kind),
        "structure_element_count": element_count,
        "contract_kind": contract_kind,
        "contract_sha256": contract_sha256,
        "accessible_text": accessible_text,
        "attachment_sha256": tuple(
            hashlib.sha256(payload).hexdigest() for payload in attachments
        ),
    }
    fields["entry_sha256"] = canonical_sha256(fields)
    return MixedStemCompositionEntryV1.model_validate(fields)


def build_mixed_stem_composition_plan(
    routing: MixedStemRoutingResultV1,
    contracts: tuple[BaseModel, ...] | list[BaseModel],
) -> MixedStemCompositionPlanV1:
    """Bind one fully resolved #236 result to its exact full contracts."""

    try:
        checked_routing = MixedStemRoutingResultV1.model_validate_json(
            routing.model_dump_json()
        )
    except (TypeError, ValueError) as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_routing_invalid"
        ) from exc
    if any(isinstance(route, OpenStemRouteV1) for route in checked_routing.routes):
        raise MixedStemCompositionRejected("mixed_stem_composition_unresolved_routes")
    checked_contracts = tuple(contracts)
    verified_routes = tuple(
        route
        for route in checked_routing.routes
        if isinstance(route, VerifiedStemRouteV1)
    )
    if len(checked_contracts) != len(verified_routes):
        raise MixedStemCompositionRejected("mixed_stem_composition_contract_set")
    by_digest: dict[str, BaseModel] = {}
    contract_bytes = 0
    for contract in checked_contracts:
        if not isinstance(contract, BaseModel):
            raise MixedStemCompositionRejected("mixed_stem_composition_contract_set")
        digest = canonical_sha256(contract.model_dump(mode="json"))
        if digest in by_digest:
            raise MixedStemCompositionRejected("mixed_stem_composition_contract_set")
        payload = contract.model_dump_json().encode("utf-8")
        contract_bytes += len(payload)
        if contract_bytes > MAX_COMPOSITION_CONTRACT_BYTES:
            raise MixedStemCompositionRejected("mixed_stem_composition_contract_limit")
        by_digest[digest] = contract
    route_by_region = {route.region_id: route for route in verified_routes}
    entries: list[MixedStemCompositionEntryV1] = []
    attachment_bytes = 0
    for ordinal, region in enumerate(checked_routing.graph.regions):
        if region.region_kind == "native_text":
            entries.append(_build_entry(ordinal, region, None))
            continue
        route = route_by_region.get(region.region_id)
        if route is None:
            raise MixedStemCompositionRejected("mixed_stem_composition_route_missing")
        supplied = by_digest.pop(route.contract_sha256, None)
        if supplied is None:
            raise MixedStemCompositionRejected("mixed_stem_composition_contract_set")
        checked = _validated_contract(region, route, supplied)
        attachment_bytes += sum(
            len(payload) for payload in _attachment_payloads(checked)
        )
        if attachment_bytes > MAX_COMPOSITION_ATTACHMENT_BYTES:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_attachment_limit"
            )
        entries.append(_build_entry(ordinal, region, checked))
    if by_digest:
        raise MixedStemCompositionRejected("mixed_stem_composition_contract_set")
    visuals = tuple(entry for entry in entries if entry.region_kind != "native_text")
    items: list[MixedStemLongDescriptionItemV1] = []
    for ordinal, entry in enumerate(visuals):
        fields: dict[str, Any] = {
            "item_kind": "mixed_stem_long_description_item_v1",
            "ordinal": ordinal,
            "region_id": entry.region_id,
            "region_kind": entry.region_kind,
            "contract_sha256": entry.contract_sha256,
            "accessible_text": entry.accessible_text,
        }
        fields["item_sha256"] = canonical_sha256(fields)
        items.append(MixedStemLongDescriptionItemV1.model_validate(fields))
    description = "\n".join(item.render() for item in items)
    description_bytes = len(description.encode("utf-8"))
    if description_bytes > MAX_COMPOSITION_DESCRIPTION_BYTES:
        raise MixedStemCompositionRejected("mixed_stem_composition_description_limit")
    structure_elements = (
        2
        + checked_routing.graph.page_count
        + sum(1 + entry.structure_element_count for entry in entries)
    )
    try:
        budget = MixedStemCompositionBudgetV1(
            budget_kind="mixed_stem_composition_budget_v1",
            policy_version=MIXED_STEM_COMPOSITION_BUDGET_VERSION,
            source_bytes=checked_routing.graph.budget.document_bytes,
            page_count=checked_routing.graph.page_count,
            region_count=len(entries),
            contract_count=len(checked_contracts),
            contract_bytes=contract_bytes,
            description_bytes=description_bytes,
            attachment_bytes=attachment_bytes,
            structure_elements=structure_elements,
        )
    except ValueError as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_budget_limit"
        ) from exc
    fields = {
        "plan_kind": "mixed_stem_composition_plan_v1",
        "policy_version": MIXED_STEM_COMPOSITION_POLICY_VERSION,
        "source_sha256": checked_routing.graph.document_sha256,
        "routing": checked_routing,
        "entries": tuple(entries),
        "long_description_items": tuple(items),
        "long_description_text": description,
        "long_description_sha256": hashlib.sha256(
            description.encode("utf-8")
        ).hexdigest(),
        "contract_sha256": tuple(entry.contract_sha256 for entry in visuals),
        "budget": budget,
    }
    identity = {
        **fields,
        "routing_result_sha256": checked_routing.result_sha256,
    }
    identity.pop("routing")
    fields["plan_sha256"] = canonical_sha256(identity)
    return MixedStemCompositionPlanV1.model_validate(fields)


def build_mixed_stem_composition_result(
    plan: MixedStemCompositionPlanV1,
    evidence: MixedStemSavedCompositionEvidenceV1,
) -> MixedStemCompositionResultV1:
    fields: dict[str, Any] = {
        "result_kind": "mixed_stem_composition_result_v1",
        "plan": plan,
        "evidence": evidence,
        "review_required": True,
        "publication_authorized": False,
    }
    fields["result_sha256"] = canonical_sha256(fields)
    return MixedStemCompositionResultV1.model_validate(fields)


def build_mixed_stem_composition_approval(
    result: MixedStemCompositionResultV1,
    *,
    review_sha256: str,
    approved_at: datetime,
    expires_at: datetime,
) -> MixedStemCompositionApprovalV1:
    checked = MixedStemCompositionResultV1.model_validate_json(result.model_dump_json())
    fields: dict[str, Any] = {
        "approval_kind": "mixed_stem_composition_approval_v1",
        "output_sha256": checked.evidence.output_sha256,
        "result_sha256": checked.result_sha256,
        "plan_sha256": checked.plan.plan_sha256,
        "contract_sha256": checked.plan.contract_sha256,
        "review_sha256": review_sha256,
        "approved_at": approved_at,
        "expires_at": expires_at,
        "human_approved": True,
    }
    provisional = MixedStemCompositionApprovalV1.model_construct(
        **fields, approval_sha256="0" * 64
    )
    fields["approval_sha256"] = canonical_sha256(
        provisional.model_dump(mode="json", exclude={"approval_sha256"})
    )
    return MixedStemCompositionApprovalV1.model_validate(fields)


def mixed_stem_composition_artifact_available(
    result: MixedStemCompositionResultV1,
    approval: MixedStemCompositionApprovalV1 | None,
    *,
    review_sha256: str,
    now: datetime,
) -> bool:
    """Authorize only one exact current, unexpired human-reviewed candidate."""

    if approval is None or now.tzinfo is None or now.utcoffset() is None:
        return False
    try:
        checked_result = MixedStemCompositionResultV1.model_validate_json(
            result.model_dump_json()
        )
        checked_approval = MixedStemCompositionApprovalV1.model_validate_json(
            approval.model_dump_json()
        )
    except (TypeError, ValueError):
        return False
    now_utc = now.astimezone(timezone.utc)
    return (
        checked_approval.human_approved is True
        and checked_approval.output_sha256 == checked_result.evidence.output_sha256
        and checked_approval.result_sha256 == checked_result.result_sha256
        and checked_approval.plan_sha256 == checked_result.plan.plan_sha256
        and checked_approval.contract_sha256 == checked_result.plan.contract_sha256
        and checked_approval.review_sha256 == review_sha256
        and checked_approval.approved_at <= now_utc < checked_approval.expires_at
    )


__all__ = [
    "MAX_COMPOSITION_APPROVAL_SECONDS",
    "MAX_COMPOSITION_ATTACHMENT_BYTES",
    "MAX_COMPOSITION_CONTRACT_BYTES",
    "MAX_COMPOSITION_DESCRIPTION_BYTES",
    "MAX_COMPOSITION_OUTPUT_BYTES",
    "MAX_COMPOSITION_REVERSE_VERIFY_BYTES",
    "MAX_COMPOSITION_STRUCTURE_ELEMENTS",
    "MIXED_STEM_COMPOSITION_BUDGET_VERSION",
    "MIXED_STEM_COMPOSITION_POLICY_VERSION",
    "MixedStemCompositionApprovalV1",
    "MixedStemCompositionBudgetV1",
    "MixedStemCompositionEntryV1",
    "MixedStemCompositionPlanV1",
    "MixedStemCompositionRejected",
    "MixedStemCompositionResultV1",
    "MixedStemLongDescriptionItemV1",
    "MixedStemSavedCompositionEvidenceV1",
    "build_mixed_stem_composition_approval",
    "build_mixed_stem_composition_plan",
    "build_mixed_stem_composition_result",
    "mixed_stem_composition_artifact_available",
]
