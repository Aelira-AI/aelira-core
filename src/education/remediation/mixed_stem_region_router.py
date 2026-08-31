"""Bounded discovery and exact specialist routing for mixed STEM regions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import fitz
from pydantic import BaseModel

from src.education.canonical_json import canonical_sha256
from src.education.mixed_stem_regions import (
    MAX_MIXED_STEM_CONTAINMENTS,
    MAX_MIXED_STEM_DOCUMENT_BYTES,
    MAX_MIXED_STEM_NATIVE_TEXT_BYTES,
    MAX_MIXED_STEM_PAGES,
    MAX_MIXED_STEM_PROVIDER_CALLS,
    MAX_MIXED_STEM_RASTER_BYTES,
    MAX_MIXED_STEM_RASTER_PIXELS,
    MAX_MIXED_STEM_REGIONS,
    MAX_MIXED_STEM_SPECIALIST_BYTES,
    MAX_MIXED_STEM_SPECIALIST_CALLS,
    MIXED_STEM_BUDGET_VERSION,
    MIXED_STEM_REGION_POLICY_VERSION,
    SPECIALIST_CONTRACT_KINDS,
    MixedStemAdjacencyV1,
    MixedStemContainmentV1,
    MixedStemGraphBudgetV1,
    MultiEquationRegionSourceV1,
    MixedStemRegionGraphV1,
    MixedStemRegionV1,
    MixedStemRoutingBudgetV1,
    MixedStemRoutingResultV1,
    NativeTextRegionSourceV1,
    OpenStemRouteV1,
    REGION_KINDS,
    build_mixed_stem_region,
    build_native_text_source,
    build_verified_stem_route,
)
from src.education.multi_equation_semantics import MultiEquationSemanticContractV1
from src.education.pdf_checks.multi_equation_region_detector import (
    MultiEquationRegionDetector,
)
from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.remediation.equation_image_source import (
    EquationImageSource,
    EquationRegionSource,
    ImageSourceRejected,
)
from src.education.vector_equation_cluster import VectorEquationClusterV1
from src.education.vector_equation_semantics import VectorEquationSemanticContractV1
from src.education.visual_semantic_contract import (
    ChemicalFormulaPdfContract,
    ChemicalStructurePdfContract,
    CommutativeDiagramPdfContract,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    HandwrittenEquationContract,
)


class MixedStemRoutingRejected(ValueError):
    """The mixed STEM page could not be routed without ambiguity."""


@dataclass(frozen=True)
class SourceMeasurement:
    """Bounded work measured while revalidating one exact source."""

    raster_bytes: int = 0
    raster_pixels: int = 0


class MixedStemSourceRevalidator(Protocol):
    """Source boundary used by discovery and immediately before routing."""

    def revalidate(
        self,
        source_path: Path,
        document: fitz.Document,
        source: Any,
    ) -> SourceMeasurement: ...


@dataclass(frozen=True)
class SpecialistAdapter:
    """One completed specialist contract plus its independent verifier seam."""

    region_kind: str
    contract_type: type[BaseModel]
    max_provider_calls: int
    invoke: Callable[[MixedStemRegionV1], "SpecialistInvocation"]
    verify: Callable[[MixedStemRegionV1, BaseModel], bool]


@dataclass(frozen=True)
class SpecialistInvocation:
    """One specialist result with the exact provider work it consumed."""

    contract: BaseModel
    provider_calls: int


_APPROVED_CONTRACT_TYPES: dict[str, type[BaseModel]] = {
    "printed_equation": MultiEquationSemanticContractV1,
    "vector_equation": VectorEquationSemanticContractV1,
    "handwritten_equation": HandwrittenEquationContract,
    "chemical_formula": ChemicalFormulaPdfContract,
    "chemical_structure": ChemicalStructurePdfContract,
    "commutative_diagram": CommutativeDiagramPdfContract,
}
_MAX_PROVIDER_CALLS_PER_SPECIALIST = 2


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _document_identity(path: Path) -> tuple[int, int, str]:
    try:
        document_bytes = path.stat().st_size
    except OSError as exc:
        raise MixedStemRoutingRejected("mixed_stem_source_unavailable") from exc
    if document_bytes <= 0 or document_bytes > MAX_MIXED_STEM_DOCUMENT_BYTES:
        raise MixedStemRoutingRejected("mixed_stem_document_byte_limit")
    try:
        with fitz.open(path) as document:
            page_count = int(document.page_count)
    except Exception as exc:
        raise MixedStemRoutingRejected("mixed_stem_source_unavailable") from exc
    if page_count <= 0 or page_count > MAX_MIXED_STEM_PAGES:
        raise MixedStemRoutingRejected("mixed_stem_page_limit")
    return document_bytes, page_count, _file_sha256(path)


def _require_document_identity(path: Path, expected: tuple[int, int, str]) -> None:
    if _document_identity(path) != expected:
        raise MixedStemRoutingRejected("mixed_stem_document_changed")


def extract_native_text_sources(
    document: fitz.Document,
) -> tuple[NativeTextRegionSourceV1, ...]:
    """Extract exact bounded native-text blocks in stable source order."""

    sources: list[NativeTextRegionSourceV1] = []
    total_bytes = 0
    for page_index in range(document.page_count):
        page = document[page_index]
        try:
            blocks = list(page.get_text("blocks", sort=False))
        except Exception as exc:
            raise MixedStemRoutingRejected(
                "mixed_stem_native_text_unavailable"
            ) from exc
        for fallback_index, block in enumerate(blocks):
            if len(block) < 5 or (len(block) >= 7 and int(block[6]) != 0):
                continue
            text = " ".join(str(block[4]).split())
            if not text:
                continue
            encoded = text.encode("utf-8")
            total_bytes += len(encoded)
            if total_bytes > MAX_MIXED_STEM_NATIVE_TEXT_BYTES:
                raise MixedStemRoutingRejected("mixed_stem_native_text_limit")
            block_index = int(block[5]) if len(block) >= 6 else fallback_index
            try:
                sources.append(
                    build_native_text_source(
                        page_number=page_index + 1,
                        block_index=block_index,
                        bbox=tuple(float(value) for value in block[:4]),
                        text=text,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise MixedStemRoutingRejected(
                    "mixed_stem_native_text_invalid"
                ) from exc
    return tuple(sources)


class DefaultMixedStemSourceRevalidator:
    """Reopen each typed source through its completed source boundary."""

    def revalidate(
        self,
        source_path: Path,
        document: fitz.Document,
        source: Any,
    ) -> SourceMeasurement:
        if isinstance(source, NativeTextRegionSourceV1):
            matches = [
                current
                for current in extract_native_text_sources(document)
                if current.source_id == source.source_id
            ]
            if len(matches) != 1 or matches[0] != source:
                raise MixedStemRoutingRejected("mixed_stem_native_text_changed")
            return SourceMeasurement()
        if isinstance(source, MultiEquationRegionSourceV1):
            checked = MultiEquationRegionDetector().revalidate_group(
                document, source.group
            )
            if checked != source.group:
                raise MixedStemRoutingRejected("mixed_stem_multi_equation_changed")
            try:
                payload = document.extract_image(source.group.image_xref)["image"]
                if not isinstance(payload, bytes) or not payload:
                    raise ValueError
            except Exception as exc:
                raise MixedStemRoutingRejected(
                    "mixed_stem_multi_equation_changed"
                ) from exc
            return SourceMeasurement(
                raster_bytes=len(payload),
                raster_pixels=source.group.source_width * source.group.source_height,
            )
        if isinstance(source, EmbeddedImageOccurrenceLocator):
            try:
                validated = EquationImageSource().extract(
                    document, source.model_dump(mode="json")
                )
            except ImageSourceRejected as exc:
                raise MixedStemRoutingRejected("mixed_stem_image_changed") from exc
            if validated.source_sha256 != source.image_stream_sha256:
                raise MixedStemRoutingRejected("mixed_stem_image_changed")
            return SourceMeasurement(
                raster_bytes=len(validated.jpeg_bytes),
                raster_pixels=validated.width * validated.height,
            )
        if isinstance(source, FrozenPageRasterRegionLocator):
            try:
                validated = EquationRegionSource().extract(
                    document, source.model_dump(mode="json")
                )
            except ImageSourceRejected as exc:
                raise MixedStemRoutingRejected(
                    "mixed_stem_raster_region_changed"
                ) from exc
            return SourceMeasurement(
                raster_bytes=len(validated.jpeg_bytes),
                raster_pixels=validated.width * validated.height,
            )
        if isinstance(source, VectorEquationClusterV1):
            if not VectorEquationClusterDetector().revalidate(source_path, source):
                raise MixedStemRoutingRejected("mixed_stem_vector_region_changed")
            return SourceMeasurement(
                raster_bytes=len(source.raster.png_bytes),
                raster_pixels=source.raster.width * source.raster.height,
            )
        raise MixedStemRoutingRejected("mixed_stem_source_kind_unsupported")


def _validate_containment_input(
    containments: Iterable[MixedStemContainmentV1],
) -> tuple[MixedStemContainmentV1, ...]:
    try:
        checked = tuple(
            MixedStemContainmentV1.model_validate(edge) for edge in containments
        )
    except (TypeError, ValueError) as exc:
        raise MixedStemRoutingRejected("mixed_stem_containment_invalid") from exc
    if len(checked) > MAX_MIXED_STEM_CONTAINMENTS:
        raise MixedStemRoutingRejected("mixed_stem_containment_limit")
    return tuple(
        sorted(
            checked,
            key=lambda edge: (edge.parent_region_id, edge.child_region_id),
        )
    )


def discover_mixed_stem_region_graph(
    source_path: str | Path,
    visual_regions: Iterable[MixedStemRegionV1],
    *,
    containments: Iterable[MixedStemContainmentV1] = (),
    source_revalidator: MixedStemSourceRevalidator | None = None,
) -> MixedStemRegionGraphV1:
    """Build one current canonical graph from native text and typed visuals."""

    path = Path(source_path)
    document_bytes, page_count, document_sha256 = _document_identity(path)
    document_identity = (document_bytes, page_count, document_sha256)
    try:
        checked_visuals = tuple(
            MixedStemRegionV1.model_validate(region) for region in visual_regions
        )
    except (TypeError, ValueError) as exc:
        raise MixedStemRoutingRejected("mixed_stem_region_invalid") from exc
    if any(region.region_kind == "native_text" for region in checked_visuals):
        raise MixedStemRoutingRejected("mixed_stem_native_text_is_extracted")
    if len(checked_visuals) > MAX_MIXED_STEM_REGIONS:
        raise MixedStemRoutingRejected("mixed_stem_region_limit")
    checked_containments = _validate_containment_input(containments)
    revalidator = source_revalidator or DefaultMixedStemSourceRevalidator()
    try:
        with fitz.open(path) as document:
            native_sources = extract_native_text_sources(document)
            regions = (
                tuple(
                    build_mixed_stem_region(region_kind="native_text", source=source)
                    for source in native_sources
                )
                + checked_visuals
            )
            if not regions or len(regions) > MAX_MIXED_STEM_REGIONS:
                raise MixedStemRoutingRejected("mixed_stem_region_limit")
            raster_bytes = 0
            raster_pixels = 0
            for region in regions:
                measurement = revalidator.revalidate(path, document, region.source)
                raster_bytes += measurement.raster_bytes
                raster_pixels += measurement.raster_pixels
                if raster_bytes > MAX_MIXED_STEM_RASTER_BYTES:
                    raise MixedStemRoutingRejected("mixed_stem_raster_byte_limit")
                if raster_pixels > MAX_MIXED_STEM_RASTER_PIXELS:
                    raise MixedStemRoutingRejected("mixed_stem_raster_pixel_limit")
    except MixedStemRoutingRejected:
        raise
    except Exception as exc:
        raise MixedStemRoutingRejected("mixed_stem_source_revalidation_failed") from exc
    _require_document_identity(path, document_identity)

    ordered = tuple(
        sorted(
            regions,
            key=lambda region: (
                region.page_number,
                region.pdf_bbox[1],
                region.pdf_bbox[0],
                region.pdf_bbox[3],
                region.pdf_bbox[2],
                REGION_KINDS.index(region.region_kind),
                region.region_id,
            ),
        )
    )
    adjacency = tuple(
        MixedStemAdjacencyV1(
            edge_kind="reading_order_adjacent_v1",
            before_region_id=before.region_id,
            after_region_id=after.region_id,
        )
        for before, after in zip(ordered, ordered[1:])
    )
    native_text_bytes = sum(
        len(region.source.text.encode("utf-8"))
        for region in ordered
        if isinstance(region.source, NativeTextRegionSourceV1)
    )
    try:
        budget = MixedStemGraphBudgetV1(
            budget_kind="mixed_stem_graph_budget_v1",
            policy_version=MIXED_STEM_BUDGET_VERSION,
            document_bytes=document_bytes,
            page_count=page_count,
            region_count=len(ordered),
            containment_count=len(checked_containments),
            native_text_bytes=native_text_bytes,
            raster_bytes=raster_bytes,
            raster_pixels=raster_pixels,
        )
        fields: dict[str, Any] = {
            "graph_kind": "mixed_stem_region_graph_v1",
            "document_sha256": document_sha256,
            "page_count": page_count,
            "policy_version": MIXED_STEM_REGION_POLICY_VERSION,
            "budget": budget,
            "regions": ordered,
            "adjacency": adjacency,
            "containment": checked_containments,
        }
        identity = {
            **fields,
            "regions": [region.canonical_identity() for region in ordered],
        }
        digest = canonical_sha256(identity)
        fields["graph_sha256"] = digest
        fields["graph_id"] = "stemgraph-v1-" + digest[:24]
        return MixedStemRegionGraphV1.model_validate(fields)
    except (TypeError, ValueError) as exc:
        raise MixedStemRoutingRejected("mixed_stem_graph_rejected") from exc


def _checked_adapters(
    adapters: Iterable[SpecialistAdapter],
) -> Mapping[str, SpecialistAdapter]:
    result: dict[str, SpecialistAdapter] = {}
    for adapter in adapters:
        expected = _APPROVED_CONTRACT_TYPES.get(adapter.region_kind)
        if (
            expected is None
            or adapter.contract_type is not expected
            or not isinstance(adapter.max_provider_calls, int)
            or isinstance(adapter.max_provider_calls, bool)
            or not 0 <= adapter.max_provider_calls <= _MAX_PROVIDER_CALLS_PER_SPECIALIST
            or not callable(adapter.invoke)
            or not callable(adapter.verify)
            or adapter.region_kind in result
        ):
            raise MixedStemRoutingRejected("mixed_stem_specialist_invalid")
        result[adapter.region_kind] = adapter
    return result


def specialist_contract_matches_region(
    region: MixedStemRegionV1, contract: BaseModel
) -> bool:
    """Bind each approved specialist contract to its exact typed source."""

    if region.region_kind == "printed_equation":
        return (
            isinstance(region.source, MultiEquationRegionSourceV1)
            and isinstance(contract, MultiEquationSemanticContractV1)
            and contract.group == region.source.group
        )
    if region.region_kind == "vector_equation":
        return (
            isinstance(region.source, VectorEquationClusterV1)
            and isinstance(contract, VectorEquationSemanticContractV1)
            and contract.cluster == region.source
        )
    if region.region_kind in {
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
    }:
        return getattr(contract, "locator", None) == region.source
    return False


def _revalidate_graph_source(
    path: Path, graph: MixedStemRegionGraphV1
) -> tuple[fitz.Document, int, int]:
    document_bytes, page_count, document_sha256 = _document_identity(path)
    if (
        page_count != graph.page_count
        or document_sha256 != graph.document_sha256
        or document_bytes != graph.budget.document_bytes
    ):
        raise MixedStemRoutingRejected("mixed_stem_document_changed")
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise MixedStemRoutingRejected("mixed_stem_source_unavailable") from exc
    return document, document_bytes, page_count


def route_mixed_stem_regions(
    source_path: str | Path,
    graph: MixedStemRegionGraphV1,
    adapters: Iterable[SpecialistAdapter],
    *,
    source_revalidator: MixedStemSourceRevalidator | None = None,
) -> MixedStemRoutingResultV1:
    """Revalidate then route every visual once, atomically and without fallback."""

    try:
        checked_graph = MixedStemRegionGraphV1.model_validate(graph)
    except (TypeError, ValueError) as exc:
        raise MixedStemRoutingRejected("mixed_stem_graph_invalid") from exc
    adapter_map = _checked_adapters(adapters)
    supported_count = sum(
        region.region_kind in adapter_map for region in checked_graph.regions
    )
    if supported_count > MAX_MIXED_STEM_SPECIALIST_CALLS:
        raise MixedStemRoutingRejected("mixed_stem_specialist_call_limit")
    reserved_provider_calls = sum(
        adapter_map[region.region_kind].max_provider_calls
        for region in checked_graph.regions
        if region.region_kind in adapter_map
    )
    if reserved_provider_calls > MAX_MIXED_STEM_PROVIDER_CALLS:
        raise MixedStemRoutingRejected("mixed_stem_provider_call_limit")
    path = Path(source_path)
    document, _, _ = _revalidate_graph_source(path, checked_graph)
    revalidator = source_revalidator or DefaultMixedStemSourceRevalidator()
    try:
        for region in checked_graph.regions:
            revalidator.revalidate(path, document, region.source)
    except MixedStemRoutingRejected:
        raise
    except Exception as exc:
        raise MixedStemRoutingRejected("mixed_stem_source_revalidation_failed") from exc
    finally:
        document.close()
    _require_document_identity(
        path,
        (
            checked_graph.budget.document_bytes,
            checked_graph.page_count,
            checked_graph.document_sha256,
        ),
    )

    routes: list[Any] = []
    unresolved: list[str] = []
    specialist_calls = 0
    provider_calls = 0
    specialist_payload_bytes = 0
    for region in checked_graph.regions:
        if region.region_kind == "native_text":
            continue
        if region.region_kind == "unknown_math_visual":
            route = OpenStemRouteV1(
                route_status="open",
                region_id=region.region_id,
                region_kind=region.region_kind,
                source_sha256=region.source_sha256,
                reason="unknown_math_visual",
            )
            routes.append(route)
            unresolved.append(region.region_id)
            continue
        adapter = adapter_map.get(region.region_kind)
        if adapter is None:
            route = OpenStemRouteV1(
                route_status="open",
                region_id=region.region_id,
                region_kind=region.region_kind,
                source_sha256=region.source_sha256,
                reason="specialist_unavailable",
            )
            routes.append(route)
            unresolved.append(region.region_id)
            continue
        try:
            invocation = adapter.invoke(region)
            if not isinstance(invocation, SpecialistInvocation):
                raise MixedStemRoutingRejected("mixed_stem_specialist_result_type")
            if (
                not isinstance(invocation.provider_calls, int)
                or isinstance(invocation.provider_calls, bool)
                or not 0 <= invocation.provider_calls <= adapter.max_provider_calls
            ):
                raise MixedStemRoutingRejected("mixed_stem_provider_call_count_invalid")
            contract = invocation.contract
            expected_type = _APPROVED_CONTRACT_TYPES[region.region_kind]
            if type(contract) is not expected_type:
                raise MixedStemRoutingRejected("mixed_stem_specialist_contract_type")
            payload = contract.model_dump_json()
            checked_contract = expected_type.model_validate_json(payload)
            if checked_contract != contract:
                raise MixedStemRoutingRejected("mixed_stem_specialist_contract_changed")
            if (
                getattr(checked_contract, "contract_kind", None)
                != SPECIALIST_CONTRACT_KINDS[region.region_kind]
                or not specialist_contract_matches_region(region, checked_contract)
                or adapter.verify(region, checked_contract) is not True
            ):
                raise MixedStemRoutingRejected(
                    "mixed_stem_specialist_verification_failed"
                )
        except MixedStemRoutingRejected:
            raise
        except Exception as exc:
            raise MixedStemRoutingRejected("mixed_stem_specialist_failed") from exc
        specialist_calls += 1
        provider_calls += invocation.provider_calls
        if provider_calls > MAX_MIXED_STEM_PROVIDER_CALLS:
            raise MixedStemRoutingRejected("mixed_stem_provider_call_limit")
        specialist_payload_bytes += len(payload.encode("utf-8"))
        if specialist_payload_bytes > MAX_MIXED_STEM_SPECIALIST_BYTES:
            raise MixedStemRoutingRejected("mixed_stem_specialist_payload_limit")
        routes.append(
            build_verified_stem_route(
                region=region,
                contract_kind=checked_contract.contract_kind,
                contract_sha256=canonical_sha256(
                    checked_contract.model_dump(mode="json")
                ),
            )
        )

    _require_document_identity(
        path,
        (
            checked_graph.budget.document_bytes,
            checked_graph.page_count,
            checked_graph.document_sha256,
        ),
    )

    try:
        budget = MixedStemRoutingBudgetV1(
            budget_kind="mixed_stem_routing_budget_v1",
            policy_version=MIXED_STEM_BUDGET_VERSION,
            specialist_calls=specialist_calls,
            provider_calls=provider_calls,
            specialist_payload_bytes=specialist_payload_bytes,
        )
        fields: dict[str, Any] = {
            "result_kind": "mixed_stem_routing_result_v1",
            "graph": checked_graph,
            "routes": tuple(routes),
            "unresolved_region_ids": tuple(unresolved),
            "budget": budget,
            "review_required": True,
            "publication_authorized": False,
        }
        identity = {
            "result_kind": fields["result_kind"],
            "graph_sha256": checked_graph.graph_sha256,
            "routes": [route.model_dump(mode="json") for route in routes],
            "unresolved_region_ids": unresolved,
            "budget": budget.model_dump(mode="json"),
            "review_required": True,
            "publication_authorized": False,
        }
        fields["result_sha256"] = canonical_sha256(identity)
        return MixedStemRoutingResultV1.model_validate(fields)
    except (TypeError, ValueError) as exc:
        raise MixedStemRoutingRejected("mixed_stem_routing_result_rejected") from exc


__all__ = [
    "DefaultMixedStemSourceRevalidator",
    "MixedStemRoutingRejected",
    "MixedStemSourceRevalidator",
    "SourceMeasurement",
    "SpecialistAdapter",
    "SpecialistInvocation",
    "discover_mixed_stem_region_graph",
    "extract_native_text_sources",
    "route_mixed_stem_regions",
    "specialist_contract_matches_region",
]
