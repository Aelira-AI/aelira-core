"""Frozen source-bound graph contracts for mixed STEM PDF regions."""

from __future__ import annotations

import hashlib
import math
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.multi_equation_region import MultiEquationRegionGroupV1
from src.education.vector_equation_cluster import VectorEquationClusterV1
from src.education.visual_semantic_contract import (
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
)

MAX_MIXED_STEM_DOCUMENT_BYTES = 512 * 1024 * 1024
MAX_MIXED_STEM_PAGES = 25_000
MAX_MIXED_STEM_REGIONS = 128
MAX_MIXED_STEM_CONTAINMENTS = 128
MAX_MIXED_STEM_NATIVE_TEXT_BYTES = 256 * 1024
MAX_MIXED_STEM_RASTER_BYTES = 32 * 1024 * 1024
MAX_MIXED_STEM_RASTER_PIXELS = 50_000_000
MAX_MIXED_STEM_SPECIALIST_CALLS = 64
MAX_MIXED_STEM_PROVIDER_CALLS = 128
MAX_MIXED_STEM_SPECIALIST_BYTES = 8 * 1024 * 1024
MIXED_STEM_REGION_POLICY_VERSION = "mixed-stem-region-policy-v1"
MIXED_STEM_BUDGET_VERSION = "mixed-stem-budget-v1"

REGION_KINDS = (
    "native_text",
    "printed_equation",
    "vector_equation",
    "handwritten_equation",
    "chemical_formula",
    "chemical_structure",
    "commutative_diagram",
    "unknown_math_visual",
)

RegionKind: TypeAlias = Literal[
    "native_text",
    "printed_equation",
    "vector_equation",
    "handwritten_equation",
    "chemical_formula",
    "chemical_structure",
    "commutative_diagram",
    "unknown_math_visual",
]
RoutedRegionKind: TypeAlias = Literal[
    "printed_equation",
    "vector_equation",
    "handwritten_equation",
    "chemical_formula",
    "chemical_structure",
    "commutative_diagram",
    "unknown_math_visual",
]

SPECIALIST_CONTRACT_KINDS: dict[str, str] = {
    "printed_equation": "multi_equation_semantic_v1",
    "vector_equation": "vector_equation_semantic_v1",
    "handwritten_equation": "handwritten_equation",
    "chemical_formula": "chemical_formula",
    "chemical_structure": "chemical_structure",
    "commutative_diagram": "commutative_diagram",
}

_SHA256 = r"^[0-9a-f]{64}$"
_REGION_ID = r"^stemregion-v1-[0-9a-f]{24}$"


class MixedStemRegionRejected(ValueError):
    """Mixed-region evidence cannot prove exact bounded ownership."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


def _validate_bbox(value: Any, label: str) -> tuple[float, float, float, float]:
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
        raise ValueError(f"{label} must contain four bounded finite numbers")
    bbox = tuple(float(item) for item in value)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"{label} must have positive area")
    return bbox


class NativeTextRegionSourceV1(_FrozenModel):
    """One exact normalized native-text block extracted from the current PDF."""

    source_kind: Literal["native_text_block"]
    page_number: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    block_index: int = Field(ge=0, le=25_000_000, strict=True)
    bbox: tuple[float, float, float, float]
    text: str = Field(min_length=1, max_length=65_536)
    text_sha256: str = Field(pattern=_SHA256)
    normalization_version: Literal["pdf-native-text-whitespace-v1"]
    source_sha256: str = Field(pattern=_SHA256)
    source_id: str = Field(pattern=r"^textregion-v1-[0-9a-f]{24}$")

    @field_validator("bbox", mode="before")
    @classmethod
    def _finite_bbox(cls, value: Any) -> Any:
        return _validate_bbox(value, "native text bbox")

    @field_validator("text")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("native text must be trimmed printable text")
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> "NativeTextRegionSourceV1":
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("native text digest differs")
        identity = self.canonical_identity()
        expected = canonical_sha256(identity)
        if self.source_sha256 != expected:
            raise ValueError("native text source digest differs")
        if self.source_id != "textregion-v1-" + expected[:24]:
            raise ValueError("native text source id differs")
        return self

    def canonical_identity(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"source_sha256", "source_id"})


class MultiEquationRegionSourceV1(_FrozenModel):
    """One exact #228 group represented as a single routable graph region."""

    source_kind: Literal["multi_equation_group"]
    group: MultiEquationRegionGroupV1
    page_number: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    pdf_bbox: tuple[float, float, float, float]
    source_sha256: str = Field(pattern=_SHA256)
    source_id: str = Field(pattern=r"^eqgroup-v1-[0-9a-f]{24}$")

    @field_validator("pdf_bbox", mode="before")
    @classmethod
    def _finite_bbox(cls, value: Any) -> Any:
        return _validate_bbox(value, "multi-equation group bbox")

    @model_validator(mode="after")
    def _validate_group_binding(self) -> "MultiEquationRegionSourceV1":
        expected_bbox = (
            min(child.pdf_bbox[0] for child in self.group.children),
            min(child.pdf_bbox[1] for child in self.group.children),
            max(child.pdf_bbox[2] for child in self.group.children),
            max(child.pdf_bbox[3] for child in self.group.children),
        )
        if (
            self.page_number != self.group.page_number
            or any(
                abs(actual - expected) > 1e-6
                for actual, expected in zip(self.pdf_bbox, expected_bbox)
            )
            or self.source_sha256 != self.group.group_sha256
            or self.source_id != self.group.group_id
        ):
            raise ValueError("multi-equation source differs from its group")
        return self


MixedStemSource: TypeAlias = Annotated[
    NativeTextRegionSourceV1
    | MultiEquationRegionSourceV1
    | EmbeddedImageOccurrenceLocator
    | FrozenPageRasterRegionLocator
    | VectorEquationClusterV1,
    Field(discriminator="source_kind"),
]


def source_bbox(source: MixedStemSource) -> tuple[float, float, float, float]:
    if isinstance(source, NativeTextRegionSourceV1):
        return tuple(source.bbox)
    if isinstance(source, MultiEquationRegionSourceV1):
        return tuple(source.pdf_bbox)
    if isinstance(source, EmbeddedImageOccurrenceLocator):
        return tuple(source.bbox)
    if isinstance(source, FrozenPageRasterRegionLocator):
        return tuple(source.pdf_bbox)
    return tuple(source.pdf_bbox)


def source_sha256(source: MixedStemSource) -> str:
    if isinstance(source, (NativeTextRegionSourceV1, MultiEquationRegionSourceV1)):
        return source.source_sha256
    if isinstance(source, VectorEquationClusterV1):
        return source.cluster_sha256
    return canonical_sha256(source.model_dump(mode="json"))


def source_page_number(source: MixedStemSource) -> int:
    return int(source.page_number)


class MixedStemRegionV1(_FrozenModel):
    """One typed graph node bound to exact source evidence."""

    node_kind: Literal["mixed_stem_region_v1"]
    region_kind: RegionKind
    source: MixedStemSource
    page_number: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    pdf_bbox: tuple[float, float, float, float]
    source_sha256: str = Field(pattern=_SHA256)
    region_sha256: str = Field(pattern=_SHA256)
    region_id: str = Field(pattern=_REGION_ID)

    @field_validator("pdf_bbox", mode="before")
    @classmethod
    def _finite_bbox(cls, value: Any) -> Any:
        return _validate_bbox(value, "region bbox")

    @model_validator(mode="after")
    def _validate_source_binding(self) -> "MixedStemRegionV1":
        allowed = {
            "native_text_block": {"native_text"},
            "multi_equation_group": {"printed_equation"},
            "pdf_vector_cluster": {"vector_equation"},
            "embedded_image_occurrence": set(REGION_KINDS)
            - {"native_text", "printed_equation", "vector_equation"},
            "page_raster_region": set(REGION_KINDS)
            - {"native_text", "printed_equation", "vector_equation"},
        }
        if self.region_kind not in allowed[self.source.source_kind]:
            raise ValueError("region kind is incompatible with its source")
        if self.page_number != source_page_number(self.source):
            raise ValueError("region page differs from its source")
        if any(
            abs(actual - expected) > 1e-6
            for actual, expected in zip(self.pdf_bbox, source_bbox(self.source))
        ):
            raise ValueError("region bbox differs from its source")
        if self.source_sha256 != source_sha256(self.source):
            raise ValueError("region source digest differs")
        expected = canonical_sha256(self.canonical_identity())
        if self.region_sha256 != expected:
            raise ValueError("region digest differs")
        if self.region_id != "stemregion-v1-" + expected[:24]:
            raise ValueError("region id differs")
        return self

    def canonical_identity(self) -> dict[str, object]:
        return {
            "node_kind": self.node_kind,
            "region_kind": self.region_kind,
            "source_kind": self.source.source_kind,
            "page_number": self.page_number,
            "pdf_bbox": list(self.pdf_bbox),
            "source_sha256": self.source_sha256,
        }


class MixedStemAdjacencyV1(_FrozenModel):
    """One exact consecutive reading-order edge."""

    edge_kind: Literal["reading_order_adjacent_v1"]
    before_region_id: str = Field(pattern=_REGION_ID)
    after_region_id: str = Field(pattern=_REGION_ID)

    @model_validator(mode="after")
    def _different_nodes(self) -> "MixedStemAdjacencyV1":
        if self.before_region_id == self.after_region_id:
            raise ValueError("adjacency cannot self-reference")
        return self


class MixedStemContainmentV1(_FrozenModel):
    """One explicit visual-to-native-label ownership edge."""

    edge_kind: Literal["contains_label_v1"]
    parent_region_id: str = Field(pattern=_REGION_ID)
    child_region_id: str = Field(pattern=_REGION_ID)

    @model_validator(mode="after")
    def _different_nodes(self) -> "MixedStemContainmentV1":
        if self.parent_region_id == self.child_region_id:
            raise ValueError("containment cannot self-reference")
        return self


class MixedStemGraphBudgetV1(_FrozenModel):
    """Measured bounded work that produced one graph."""

    budget_kind: Literal["mixed_stem_graph_budget_v1"]
    policy_version: Literal["mixed-stem-budget-v1"]
    document_bytes: int = Field(ge=1, le=MAX_MIXED_STEM_DOCUMENT_BYTES, strict=True)
    page_count: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    region_count: int = Field(ge=1, le=MAX_MIXED_STEM_REGIONS, strict=True)
    containment_count: int = Field(ge=0, le=MAX_MIXED_STEM_CONTAINMENTS, strict=True)
    native_text_bytes: int = Field(
        ge=0, le=MAX_MIXED_STEM_NATIVE_TEXT_BYTES, strict=True
    )
    raster_bytes: int = Field(ge=0, le=MAX_MIXED_STEM_RASTER_BYTES, strict=True)
    raster_pixels: int = Field(ge=0, le=MAX_MIXED_STEM_RASTER_PIXELS, strict=True)


def _reading_key(region: MixedStemRegionV1) -> tuple[object, ...]:
    return (
        region.page_number,
        region.pdf_bbox[1],
        region.pdf_bbox[0],
        region.pdf_bbox[3],
        region.pdf_bbox[2],
        REGION_KINDS.index(region.region_kind),
        region.region_id,
    )


def _contains(parent: MixedStemRegionV1, child: MixedStemRegionV1) -> bool:
    return (
        parent.page_number == child.page_number
        and parent.pdf_bbox[0] <= child.pdf_bbox[0]
        and parent.pdf_bbox[1] <= child.pdf_bbox[1]
        and parent.pdf_bbox[2] >= child.pdf_bbox[2]
        and parent.pdf_bbox[3] >= child.pdf_bbox[3]
    )


def _overlaps(first: MixedStemRegionV1, second: MixedStemRegionV1) -> bool:
    return (
        first.page_number == second.page_number
        and first.pdf_bbox[0] < second.pdf_bbox[2]
        and second.pdf_bbox[0] < first.pdf_bbox[2]
        and first.pdf_bbox[1] < second.pdf_bbox[3]
        and second.pdf_bbox[1] < first.pdf_bbox[3]
    )


class MixedStemRegionGraphV1(_FrozenModel):
    """Canonical ordered typed region graph for one exact PDF."""

    graph_kind: Literal["mixed_stem_region_graph_v1"]
    document_sha256: str = Field(pattern=_SHA256)
    page_count: int = Field(ge=1, le=MAX_MIXED_STEM_PAGES, strict=True)
    policy_version: Literal["mixed-stem-region-policy-v1"]
    budget: MixedStemGraphBudgetV1
    regions: tuple[MixedStemRegionV1, ...] = Field(
        min_length=1, max_length=MAX_MIXED_STEM_REGIONS
    )
    adjacency: tuple[MixedStemAdjacencyV1, ...] = Field(
        max_length=MAX_MIXED_STEM_REGIONS - 1
    )
    containment: tuple[MixedStemContainmentV1, ...] = Field(
        max_length=MAX_MIXED_STEM_CONTAINMENTS
    )
    graph_sha256: str = Field(pattern=_SHA256)
    graph_id: str = Field(pattern=r"^stemgraph-v1-[0-9a-f]{24}$")

    @model_validator(mode="after")
    def _validate_graph(self) -> "MixedStemRegionGraphV1":
        if self.page_count != self.budget.page_count:
            raise ValueError("graph page count differs from budget")
        if self.budget.region_count != len(self.regions):
            raise ValueError("graph region count differs from budget")
        if self.budget.containment_count != len(self.containment):
            raise ValueError("graph containment count differs from budget")
        if tuple(sorted(self.regions, key=_reading_key)) != self.regions:
            raise ValueError("regions are not in canonical reading order")
        by_id = {region.region_id: region for region in self.regions}
        if len(by_id) != len(self.regions):
            raise ValueError("region identity is duplicated")
        if any(region.page_number > self.page_count for region in self.regions):
            raise ValueError("region page exceeds the current document")
        expected_adjacency = tuple(
            (before.region_id, after.region_id)
            for before, after in zip(self.regions, self.regions[1:])
        )
        actual_adjacency = tuple(
            (edge.before_region_id, edge.after_region_id) for edge in self.adjacency
        )
        if actual_adjacency != expected_adjacency:
            raise ValueError("adjacency differs from canonical reading order")
        parent_by_child: dict[str, str] = {}
        containment_pairs: set[tuple[str, str]] = set()
        for edge in self.containment:
            if edge.parent_region_id not in by_id or edge.child_region_id not in by_id:
                raise ValueError("containment references an unknown region")
            parent = by_id[edge.parent_region_id]
            child = by_id[edge.child_region_id]
            if (
                parent.region_kind not in {"chemical_structure", "commutative_diagram"}
                or child.region_kind != "native_text"
                or not _contains(parent, child)
            ):
                raise ValueError("containment type or geometry is unsupported")
            if child.region_id in parent_by_child:
                raise ValueError("contained region has multiple direct parents")
            parent_by_child[child.region_id] = parent.region_id
            containment_pairs.add((parent.region_id, child.region_id))
        if len(containment_pairs) != len(self.containment):
            raise ValueError("containment edge is duplicated")
        for index, first in enumerate(self.regions):
            for second in self.regions[index + 1 :]:
                if _overlaps(first, second) and (
                    (first.region_id, second.region_id) not in containment_pairs
                    and (second.region_id, first.region_id) not in containment_pairs
                ):
                    raise ValueError("regions overlap without typed containment")
        expected = canonical_sha256(self.canonical_identity())
        if self.graph_sha256 != expected:
            raise ValueError("region graph digest differs")
        if self.graph_id != "stemgraph-v1-" + expected[:24]:
            raise ValueError("region graph id differs")
        return self

    def canonical_identity(self) -> dict[str, object]:
        return {
            "graph_kind": self.graph_kind,
            "document_sha256": self.document_sha256,
            "page_count": self.page_count,
            "policy_version": self.policy_version,
            "budget": self.budget.model_dump(mode="json"),
            "regions": [region.canonical_identity() for region in self.regions],
            "adjacency": [edge.model_dump(mode="json") for edge in self.adjacency],
            "containment": [edge.model_dump(mode="json") for edge in self.containment],
        }


class VerifiedStemRouteV1(_FrozenModel):
    """One exact source-to-specialist contract binding."""

    route_status: Literal["verified"]
    region_id: str = Field(pattern=_REGION_ID)
    region_kind: Literal[
        "printed_equation",
        "vector_equation",
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
    ]
    source_sha256: str = Field(pattern=_SHA256)
    contract_kind: str = Field(min_length=1, max_length=128)
    contract_sha256: str = Field(pattern=_SHA256)
    binding_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_binding(self) -> "VerifiedStemRouteV1":
        if self.contract_kind != SPECIALIST_CONTRACT_KINDS[self.region_kind]:
            raise ValueError("specialist contract kind differs from region kind")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("specialist binding digest differs")
        return self


class OpenStemRouteV1(_FrozenModel):
    """One explicit region that has no verified specialist binding."""

    route_status: Literal["open"]
    region_id: str = Field(pattern=_REGION_ID)
    region_kind: RoutedRegionKind
    source_sha256: str = Field(pattern=_SHA256)
    reason: Literal["unknown_math_visual", "specialist_unavailable"]

    @model_validator(mode="after")
    def _validate_reason(self) -> "OpenStemRouteV1":
        if (self.region_kind == "unknown_math_visual") != (
            self.reason == "unknown_math_visual"
        ):
            raise ValueError("open route reason differs from region kind")
        return self


StemRoute: TypeAlias = Annotated[
    VerifiedStemRouteV1 | OpenStemRouteV1,
    Field(discriminator="route_status"),
]


class MixedStemRoutingBudgetV1(_FrozenModel):
    """Measured bounded specialist work for one routing transaction."""

    budget_kind: Literal["mixed_stem_routing_budget_v1"]
    policy_version: Literal["mixed-stem-budget-v1"]
    specialist_calls: int = Field(ge=0, le=MAX_MIXED_STEM_SPECIALIST_CALLS, strict=True)
    provider_calls: int = Field(ge=0, le=MAX_MIXED_STEM_PROVIDER_CALLS, strict=True)
    specialist_payload_bytes: int = Field(
        ge=0, le=MAX_MIXED_STEM_SPECIALIST_BYTES, strict=True
    )


class MixedStemRoutingResultV1(_FrozenModel):
    """Frozen unapproved bridge from region discovery to atomic composition."""

    result_kind: Literal["mixed_stem_routing_result_v1"]
    graph: MixedStemRegionGraphV1
    routes: tuple[StemRoute, ...] = Field(max_length=MAX_MIXED_STEM_REGIONS)
    unresolved_region_ids: tuple[str, ...] = Field(max_length=MAX_MIXED_STEM_REGIONS)
    budget: MixedStemRoutingBudgetV1
    review_required: Literal[True]
    publication_authorized: Literal[False]
    result_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_result(self) -> "MixedStemRoutingResultV1":
        routed_regions = tuple(
            region
            for region in self.graph.regions
            if region.region_kind != "native_text"
        )
        if len(self.routes) != len(routed_regions):
            raise ValueError("every visual region requires one route state")
        open_ids: list[str] = []
        verified_count = 0
        for region, route in zip(routed_regions, self.routes):
            if (
                route.region_id != region.region_id
                or route.region_kind != region.region_kind
                or route.source_sha256 != region.source_sha256
            ):
                raise ValueError("route differs from its ordered graph region")
            if isinstance(route, OpenStemRouteV1):
                open_ids.append(route.region_id)
            else:
                verified_count += 1
        if tuple(open_ids) != self.unresolved_region_ids:
            raise ValueError("unresolved region list differs from open routes")
        if verified_count != self.budget.specialist_calls:
            raise ValueError("routing call count differs from verified routes")
        expected = canonical_sha256(self.canonical_identity())
        if self.result_sha256 != expected:
            raise ValueError("routing result digest differs")
        return self

    def canonical_identity(self) -> dict[str, object]:
        return {
            "result_kind": self.result_kind,
            "graph_sha256": self.graph.graph_sha256,
            "routes": [route.model_dump(mode="json") for route in self.routes],
            "unresolved_region_ids": list(self.unresolved_region_ids),
            "budget": self.budget.model_dump(mode="json"),
            "review_required": self.review_required,
            "publication_authorized": self.publication_authorized,
        }

    def authorizes_artifact_availability(self) -> bool:
        """#236 never authorizes a composed artifact."""

        return False


def build_native_text_source(
    *,
    page_number: int,
    block_index: int,
    bbox: tuple[float, float, float, float],
    text: str,
) -> NativeTextRegionSourceV1:
    normalized = " ".join(text.split())
    fields: dict[str, object] = {
        "source_kind": "native_text_block",
        "page_number": page_number,
        "block_index": block_index,
        "bbox": bbox,
        "text": normalized,
        "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "normalization_version": "pdf-native-text-whitespace-v1",
    }
    digest = canonical_sha256(fields)
    fields["source_sha256"] = digest
    fields["source_id"] = "textregion-v1-" + digest[:24]
    return NativeTextRegionSourceV1.model_validate(fields)


def build_mixed_stem_region(
    *, region_kind: RegionKind, source: MixedStemSource
) -> MixedStemRegionV1:
    fields: dict[str, object] = {
        "node_kind": "mixed_stem_region_v1",
        "region_kind": region_kind,
        "source": source,
        "page_number": source_page_number(source),
        "pdf_bbox": source_bbox(source),
        "source_sha256": source_sha256(source),
    }
    identity = {
        "node_kind": fields["node_kind"],
        "region_kind": fields["region_kind"],
        "source_kind": source.source_kind,
        "page_number": fields["page_number"],
        "pdf_bbox": list(fields["pdf_bbox"]),
        "source_sha256": fields["source_sha256"],
    }
    digest = canonical_sha256(identity)
    fields["region_sha256"] = digest
    fields["region_id"] = "stemregion-v1-" + digest[:24]
    return MixedStemRegionV1.model_validate(fields)


def build_multi_equation_source(
    group: MultiEquationRegionGroupV1,
) -> MultiEquationRegionSourceV1:
    checked = MultiEquationRegionGroupV1.model_validate(group)
    bbox = (
        min(child.pdf_bbox[0] for child in checked.children),
        min(child.pdf_bbox[1] for child in checked.children),
        max(child.pdf_bbox[2] for child in checked.children),
        max(child.pdf_bbox[3] for child in checked.children),
    )
    return MultiEquationRegionSourceV1(
        source_kind="multi_equation_group",
        group=checked,
        page_number=checked.page_number,
        pdf_bbox=bbox,
        source_sha256=checked.group_sha256,
        source_id=checked.group_id,
    )


def build_verified_stem_route(
    *, region: MixedStemRegionV1, contract_kind: str, contract_sha256: str
) -> VerifiedStemRouteV1:
    fields: dict[str, object] = {
        "route_status": "verified",
        "region_id": region.region_id,
        "region_kind": region.region_kind,
        "source_sha256": region.source_sha256,
        "contract_kind": contract_kind,
        "contract_sha256": contract_sha256,
    }
    fields["binding_sha256"] = canonical_sha256(fields)
    return VerifiedStemRouteV1.model_validate(fields)


__all__ = [
    "MAX_MIXED_STEM_CONTAINMENTS",
    "MAX_MIXED_STEM_DOCUMENT_BYTES",
    "MAX_MIXED_STEM_NATIVE_TEXT_BYTES",
    "MAX_MIXED_STEM_PAGES",
    "MAX_MIXED_STEM_PROVIDER_CALLS",
    "MAX_MIXED_STEM_RASTER_BYTES",
    "MAX_MIXED_STEM_RASTER_PIXELS",
    "MAX_MIXED_STEM_REGIONS",
    "MAX_MIXED_STEM_SPECIALIST_BYTES",
    "MAX_MIXED_STEM_SPECIALIST_CALLS",
    "MIXED_STEM_BUDGET_VERSION",
    "MIXED_STEM_REGION_POLICY_VERSION",
    "MixedStemAdjacencyV1",
    "MixedStemContainmentV1",
    "MixedStemGraphBudgetV1",
    "MultiEquationRegionSourceV1",
    "MixedStemRegionGraphV1",
    "MixedStemRegionRejected",
    "MixedStemRegionV1",
    "MixedStemRoutingBudgetV1",
    "MixedStemRoutingResultV1",
    "NativeTextRegionSourceV1",
    "OpenStemRouteV1",
    "REGION_KINDS",
    "SPECIALIST_CONTRACT_KINDS",
    "VerifiedStemRouteV1",
    "build_mixed_stem_region",
    "build_multi_equation_source",
    "build_native_text_source",
    "build_verified_stem_route",
    "source_bbox",
    "source_page_number",
    "source_sha256",
]
