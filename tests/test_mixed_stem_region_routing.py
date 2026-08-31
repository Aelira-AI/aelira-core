"""Typed source-bound routing for mixed STEM PDF regions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import fitz
import pytest
from pydantic import ValidationError

from src.education.canonical_json import canonical_sha256
from src.education.mixed_stem_regions import (
    REGION_KINDS,
    MixedStemContainmentV1,
    MixedStemRegionGraphV1,
    MixedStemRoutingResultV1,
    OpenStemRouteV1,
    VerifiedStemRouteV1,
    build_mixed_stem_region,
    build_multi_equation_source,
)
from src.education.multi_equation_semantics import MultiEquationSemanticContractV1
from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.remediation.mixed_stem_region_router import (
    MixedStemRoutingRejected,
    SourceMeasurement,
    SpecialistAdapter,
    SpecialistInvocation,
    discover_mixed_stem_region_graph,
    extract_native_text_sources,
    route_mixed_stem_regions,
)
from src.education.remediation.vector_equation_semantics import (
    remediate_vector_equation_pdf,
)
from src.education.vector_equation_semantics import VectorEquationSemanticContractV1
from src.education.visual_semantic_contract import (
    ChemicalFormulaPdfContract,
    ChemicalStructurePdfContract,
    CommutativeDiagramPdfContract,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    HandwrittenEquationContract,
)
from tests.test_chemical_formula_pdf_contract import _contract as _chemical_contract
from tests.test_chemical_structure_pdf_contract import (
    _pdf_contract as _chemical_structure_contract,
)
from tests.test_commutative_diagram_pdf_contract import (
    _pdf_contract as _diagram_contract,
)
from tests.test_handwritten_visual_semantic_contract import (
    _contract as _handwritten_contract,
)
from tests.test_multi_equation_semantic_association import (
    _contract as _multi_contract,
)
from tests.test_multi_equation_semantic_association import _group, _owner
from tests.test_vector_equation_semantic_association import _Recognizer, _Verifier


def _write_pdf(path, *, nested_label=False) -> None:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text(
        (40, 215 if nested_label else 30),
        "diagram label" if nested_label else "Native introduction",
        fontsize=10,
    )
    document.save(path)
    document.close()


def _write_blank_pdf(path, *, pages=1) -> None:
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=600, height=800)
    document.save(path)
    document.close()


def _embedded_source(
    *, ordinal: int, bbox: tuple[float, float, float, float]
) -> EmbeddedImageOccurrenceLocator:
    page_number = 1
    image_xref = ordinal + 1
    image_index = ordinal
    identity = f"{page_number}|{image_xref}|{image_index}|{ordinal}|" + ",".join(
        f"{value:.6f}" for value in bbox
    )
    return EmbeddedImageOccurrenceLocator(
        source_kind="embedded_image_occurrence",
        page_number=page_number,
        image_xref=image_xref,
        image_index=image_index,
        occurrence_ordinal=ordinal,
        bbox=bbox,
        image_stream_sha256=hashlib.sha256(identity.encode()).hexdigest(),
        occurrence_id="imgocc-v1-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
    )


def _visual(kind: str, ordinal: int, y: float):
    return build_mixed_stem_region(
        region_kind=kind,
        source=_embedded_source(ordinal=ordinal, bbox=(40, y, 180, y + 40)),
    )


@dataclass
class _SourceProbe:
    fail_region_source_sha256: str | None = None
    raster_bytes: int = 1_000
    raster_pixels: int = 10_000
    calls: list[str] = field(default_factory=list)

    def revalidate(self, _path, _document, source) -> SourceMeasurement:
        digest = getattr(source, "source_sha256", None)
        if digest is None:
            digest = getattr(source, "cluster_sha256", None)
        if digest is None:
            digest = canonical_sha256(source.model_dump(mode="json"))
        self.calls.append(digest)
        if digest == self.fail_region_source_sha256:
            raise MixedStemRoutingRejected("fixture_source_changed")
        if source.source_kind == "native_text_block":
            return SourceMeasurement()
        return SourceMeasurement(
            raster_bytes=self.raster_bytes,
            raster_pixels=self.raster_pixels,
        )


def _graph(path, regions, *, containments=(), probe=None):
    return discover_mixed_stem_region_graph(
        path,
        regions,
        containments=containments,
        source_revalidator=probe or _SourceProbe(),
    )


def test_region_vocabulary_is_complete_and_explicit() -> None:
    assert REGION_KINDS == (
        "native_text",
        "printed_equation",
        "vector_equation",
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
        "unknown_math_visual",
    )


def test_document_identity_native_text_and_frozen_contract(tmp_path) -> None:
    source = tmp_path / "mixed.pdf"
    _write_pdf(source)
    visual = _visual("chemical_formula", 0, 100)
    graph = _graph(source, (visual,))

    assert graph.document_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert graph.page_count == 1
    assert graph.regions[0].region_kind == "native_text"
    assert graph.regions[0].source.text == "Native introduction"
    assert graph.regions[1] == visual
    assert graph.adjacency[0].before_region_id == graph.regions[0].region_id
    assert MixedStemRegionGraphV1.model_validate_json(graph.model_dump_json()) == graph
    with pytest.raises(ValidationError):
        graph.graph_sha256 = "0" * 64
    with pytest.raises(ValidationError):
        MixedStemRegionGraphV1.model_validate(
            {**graph.model_dump(mode="json"), "unknown": True}
        )


def test_source_provenance_geometry_region_identity_and_page_bounds_are_exact(
    tmp_path,
) -> None:
    from tests.test_visual_semantic_contract import _embedded_locator, _page_locator

    page_source = FrozenPageRasterRegionLocator.model_validate(_page_locator())
    region = build_mixed_stem_region(region_kind="chemical_formula", source=page_source)
    assert region.source.source_kind == "page_raster_region"
    assert region.pdf_bbox == page_source.pdf_bbox
    raw = region.model_dump(mode="json")
    raw["pdf_bbox"][2] += 1
    with pytest.raises(ValidationError, match="bbox differs"):
        type(region).model_validate(raw)

    source = tmp_path / "one-page.pdf"
    _write_pdf(source)
    page_two = build_mixed_stem_region(
        region_kind="chemical_structure",
        source=EmbeddedImageOccurrenceLocator.model_validate(_embedded_locator()),
    )
    with pytest.raises(MixedStemRoutingRejected, match="graph_rejected"):
        _graph(source, (page_two,))


def test_reading_order_is_input_independent_and_adjacency_is_exact(tmp_path) -> None:
    source = tmp_path / "order.pdf"
    _write_pdf(source)
    lower = _visual("chemical_formula", 1, 300)
    upper = _visual("handwritten_equation", 0, 100)

    first = _graph(source, (lower, upper))
    second = _graph(source, (upper, lower))

    assert first == second
    assert [region.region_kind for region in first.regions] == [
        "native_text",
        "handwritten_equation",
        "chemical_formula",
    ]
    raw = first.model_dump(mode="json")
    raw["adjacency"] = list(reversed(raw["adjacency"]))
    with pytest.raises(ValidationError, match="adjacency"):
        MixedStemRegionGraphV1.model_validate(raw)


def test_nested_label_overlap_requires_one_allowed_typed_containment(
    tmp_path, monkeypatch
) -> None:
    from src.education.remediation import mixed_stem_region_router as module

    source = tmp_path / "nested.pdf"
    _write_pdf(source, nested_label=True)
    parent = build_mixed_stem_region(
        region_kind="commutative_diagram",
        source=_embedded_source(ordinal=0, bbox=(20, 180, 220, 260)),
    )
    with fitz.open(source) as document:
        native = build_mixed_stem_region(
            region_kind="native_text", source=extract_native_text_sources(document)[0]
        )

    with pytest.raises(MixedStemRoutingRejected, match="graph_rejected"):
        _graph(source, (parent,))

    edge = MixedStemContainmentV1(
        edge_kind="contains_label_v1",
        parent_region_id=parent.region_id,
        child_region_id=native.region_id,
    )
    graph = _graph(source, (parent,), containments=(edge,))
    assert graph.containment == (edge,)
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_CONTAINMENTS", 0)
        with pytest.raises(MixedStemRoutingRejected, match="containment_limit"):
            _graph(source, (parent,), containments=(edge,))

    wrong_parent = build_mixed_stem_region(
        region_kind="chemical_formula",
        source=_embedded_source(ordinal=1, bbox=(20, 180, 220, 260)),
    )
    wrong_edge = edge.model_copy(update={"parent_region_id": wrong_parent.region_id})
    with pytest.raises(MixedStemRoutingRejected, match="graph_rejected"):
        _graph(source, (wrong_parent,), containments=(wrong_edge,))


def test_containment_topology_duplicate_parentage_and_graph_digest_tampering(
    tmp_path,
) -> None:
    source = tmp_path / "parents.pdf"
    _write_pdf(source, nested_label=True)
    first = build_mixed_stem_region(
        region_kind="commutative_diagram",
        source=_embedded_source(ordinal=0, bbox=(20, 180, 220, 260)),
    )
    second = build_mixed_stem_region(
        region_kind="chemical_structure",
        source=_embedded_source(ordinal=1, bbox=(10, 170, 230, 270)),
    )
    with fitz.open(source) as document:
        child = build_mixed_stem_region(
            region_kind="native_text", source=extract_native_text_sources(document)[0]
        )
    edges = (
        MixedStemContainmentV1(
            edge_kind="contains_label_v1",
            parent_region_id=first.region_id,
            child_region_id=child.region_id,
        ),
        MixedStemContainmentV1(
            edge_kind="contains_label_v1",
            parent_region_id=second.region_id,
            child_region_id=child.region_id,
        ),
    )
    with pytest.raises(MixedStemRoutingRejected, match="graph_rejected"):
        _graph(source, (first, second), containments=edges)

    plain = _graph(source, (_visual("chemical_formula", 3, 400),))
    raw = plain.model_dump(mode="json")
    raw["graph_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="graph digest"):
        MixedStemRegionGraphV1.model_validate(raw)


def _multi_equation_contract(tmp_path) -> MultiEquationSemanticContractV1:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    return _multi_contract(group, owners)


def _handwritten() -> HandwrittenEquationContract:
    from tests.test_visual_semantic_contract import _embedded_locator, _standalone_saved

    return HandwrittenEquationContract.model_validate(
        _handwritten_contract(_embedded_locator(), _standalone_saved())
    )


def _raster_contract(kind):
    return {
        "handwritten_equation": _handwritten,
        "chemical_formula": lambda: ChemicalFormulaPdfContract.model_validate(
            _chemical_contract()
        ),
        "chemical_structure": lambda: ChemicalStructurePdfContract.model_validate(
            _chemical_structure_contract()
        ),
        "commutative_diagram": lambda: CommutativeDiagramPdfContract.model_validate(
            _diagram_contract()
        ),
    }[kind]()


def _adapter(kind, contract, calls):
    expected_types = {
        "printed_equation": MultiEquationSemanticContractV1,
        "vector_equation": VectorEquationSemanticContractV1,
        "handwritten_equation": HandwrittenEquationContract,
        "chemical_formula": ChemicalFormulaPdfContract,
        "chemical_structure": ChemicalStructurePdfContract,
        "commutative_diagram": CommutativeDiagramPdfContract,
    }

    def invoke(region):
        calls.append((kind, region.region_id))
        return SpecialistInvocation(contract=contract, provider_calls=1)

    return SpecialistAdapter(
        region_kind=kind,
        contract_type=expected_types[kind],
        max_provider_calls=1,
        invoke=invoke,
        verify=lambda _region, checked: checked == contract,
    )


def test_unknown_route_unavailable_bridge_and_publication_gate(
    tmp_path,
) -> None:
    source = tmp_path / "routes.pdf"
    _write_pdf(source)
    kinds = (
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
        "unknown_math_visual",
    )
    regions = tuple(
        _visual(kind, index, 100 + index * 70) for index, kind in enumerate(kinds)
    )
    probe = _SourceProbe()
    graph = _graph(source, regions, probe=probe)
    result = route_mixed_stem_regions(source, graph, (), source_revalidator=probe)

    assert not any(isinstance(route, VerifiedStemRouteV1) for route in result.routes)
    assert all(isinstance(route, OpenStemRouteV1) for route in result.routes)
    assert all(route.reason == "specialist_unavailable" for route in result.routes[:-1])
    assert isinstance(result.routes[-1], OpenStemRouteV1)
    assert result.routes[-1].reason == "unknown_math_visual"
    assert result.unresolved_region_ids == tuple(region.region_id for region in regions)
    assert result.review_required is True
    assert result.publication_authorized is False
    assert result.authorizes_artifact_availability() is False
    assert result.budget.provider_calls == 0
    assert (
        MixedStemRoutingResultV1.model_validate_json(result.model_dump_json()) == result
    )


@pytest.mark.parametrize(
    "kind",
    (
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
    ),
)
def test_each_raster_specialist_routes_only_its_exact_source(tmp_path, kind) -> None:
    contract = _raster_contract(kind)
    source = tmp_path / f"{kind}.pdf"
    _write_blank_pdf(source, pages=contract.locator.page_number)
    region = build_mixed_stem_region(region_kind=kind, source=contract.locator)
    graph = _graph(source, (region,))
    calls = []

    result = route_mixed_stem_regions(
        source,
        graph,
        (_adapter(kind, contract, calls),),
        source_revalidator=_SourceProbe(),
    )

    assert calls == [(kind, region.region_id)]
    assert isinstance(result.routes[0], VerifiedStemRouteV1)
    assert result.budget.provider_calls == 1


def test_contract_binding_specialist_verifier_uses_exact_region_source(
    tmp_path,
) -> None:
    source = tmp_path / "binding.pdf"
    document = fitz.open()
    document.new_page(width=300, height=200)
    document.save(source)
    document.close()
    contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    region = build_mixed_stem_region(
        region_kind="chemical_formula", source=contract.locator
    )
    graph = _graph(source, (region,))
    adapter = SpecialistAdapter(
        region_kind="chemical_formula",
        contract_type=ChemicalFormulaPdfContract,
        max_provider_calls=1,
        invoke=lambda _region: SpecialistInvocation(
            contract=contract, provider_calls=1
        ),
        verify=lambda current, checked: checked.locator == current.source,
    )

    result = route_mixed_stem_regions(
        source, graph, (adapter,), source_revalidator=_SourceProbe()
    )

    assert result.routes[0].source_sha256 == region.source_sha256
    assert result.routes[0].contract_sha256 == canonical_sha256(
        contract.model_dump(mode="json")
    )


def test_printed_route_group_source_uses_exact_231_contract(tmp_path) -> None:
    group = _group(tmp_path)
    source = tmp_path / "split.pdf"
    region = build_mixed_stem_region(
        region_kind="printed_equation", source=build_multi_equation_source(group)
    )
    graph = discover_mixed_stem_region_graph(source, (region,))
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    contract = _multi_contract(group, owners)
    calls = []

    result = route_mixed_stem_regions(
        source, graph, (_adapter("printed_equation", contract, calls),)
    )

    assert calls == [("printed_equation", region.region_id)]
    assert result.routes[0].contract_kind == "multi_equation_semantic_v1"


def _write_vector_pdf(path) -> None:
    document = fitz.open()
    page = document.new_page(width=300, height=220)
    page.insert_text((36, 28), "Equation", fontsize=10)
    shape = page.new_shape()
    for start, end in (
        ((50, 80), (62, 60)),
        ((50, 60), (62, 80)),
        ((75, 66), (95, 66)),
        ((75, 73), (95, 73)),
        ((108, 60), (118, 70)),
        ((128, 60), (118, 70)),
        ((118, 70), (118, 82)),
    ):
        shape.draw_line(start, end)
    shape.finish(color=(0, 0, 0), width=2)
    shape.commit()
    document.save(path)
    document.close()


def test_vector_route_uses_exact_234_contract(tmp_path) -> None:
    source = tmp_path / "vector.pdf"
    output = tmp_path / "vector-associated.pdf"
    _write_vector_pdf(source)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    region = build_mixed_stem_region(region_kind="vector_equation", source=cluster)
    graph = discover_mixed_stem_region_graph(source, (region,))
    contract = remediate_vector_equation_pdf(
        source, output, cluster, _Recognizer(), _Verifier()
    )
    calls = []

    result = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("vector_equation", contract, calls),),
    )

    assert calls == [("vector_equation", region.region_id)]
    assert result.routes[0].contract_kind == "vector_equation_semantic_v1"


def test_unavailable_specialist_is_explicit_without_fallback(tmp_path) -> None:
    source = tmp_path / "unavailable.pdf"
    _write_blank_pdf(source, pages=2)
    handwritten_contract = _handwritten()
    chemical_contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    handwritten = build_mixed_stem_region(
        region_kind="handwritten_equation", source=handwritten_contract.locator
    )
    chemical = build_mixed_stem_region(
        region_kind="chemical_formula", source=chemical_contract.locator
    )
    graph = _graph(source, (handwritten, chemical))
    calls = []
    adapter = _adapter("handwritten_equation", handwritten_contract, calls)

    result = route_mixed_stem_regions(
        source, graph, (adapter,), source_revalidator=_SourceProbe()
    )

    assert calls == [("handwritten_equation", handwritten.region_id)]
    assert result.routes[0].reason == "specialist_unavailable"
    assert result.unresolved_region_ids == (chemical.region_id,)


def test_atomic_failure_wrong_adapter_contract_and_verifier(tmp_path) -> None:
    source = tmp_path / "atomic.pdf"
    _write_blank_pdf(source)
    contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    region = build_mixed_stem_region(
        region_kind="chemical_formula", source=contract.locator
    )
    graph = _graph(source, (region,))
    wrong = SpecialistAdapter(
        region_kind="chemical_formula",
        contract_type=HandwrittenEquationContract,
        max_provider_calls=1,
        invoke=lambda _region: SpecialistInvocation(
            contract=_handwritten(), provider_calls=1
        ),
        verify=lambda _region, _contract: True,
    )
    with pytest.raises(MixedStemRoutingRejected, match="specialist_invalid"):
        route_mixed_stem_regions(
            source, graph, (wrong,), source_revalidator=_SourceProbe()
        )

    rejected = SpecialistAdapter(
        region_kind="chemical_formula",
        contract_type=ChemicalFormulaPdfContract,
        max_provider_calls=1,
        invoke=lambda _region: SpecialistInvocation(
            contract=contract, provider_calls=1
        ),
        verify=lambda _region, _contract: False,
    )
    with pytest.raises(MixedStemRoutingRejected, match="verification_failed"):
        route_mixed_stem_regions(
            source, graph, (rejected,), source_revalidator=_SourceProbe()
        )

    misreported = SpecialistAdapter(
        region_kind="chemical_formula",
        contract_type=ChemicalFormulaPdfContract,
        max_provider_calls=1,
        invoke=lambda _region: SpecialistInvocation(
            contract=contract, provider_calls=2
        ),
        verify=lambda _region, _contract: True,
    )
    with pytest.raises(MixedStemRoutingRejected, match="provider_call_count_invalid"):
        route_mixed_stem_regions(
            source, graph, (misreported,), source_revalidator=_SourceProbe()
        )

    original = source.read_bytes()

    def mutate_source(_region):
        source.write_bytes(original + b"\n% changed during specialist call\n")
        return SpecialistInvocation(contract=contract, provider_calls=1)

    mutating = SpecialistAdapter(
        region_kind="chemical_formula",
        contract_type=ChemicalFormulaPdfContract,
        max_provider_calls=1,
        invoke=mutate_source,
        verify=lambda _region, _contract: True,
    )
    with pytest.raises(MixedStemRoutingRejected, match="document_changed"):
        route_mixed_stem_regions(
            source, graph, (mutating,), source_revalidator=_SourceProbe()
        )


def test_source_revalidation_stale_document_rejects_before_specialist(tmp_path) -> None:
    source = tmp_path / "stale.pdf"
    _write_blank_pdf(source)
    contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    region = build_mixed_stem_region(
        region_kind="chemical_formula", source=contract.locator
    )
    graph = _graph(source, (region,))
    calls = []
    adapter = _adapter("chemical_formula", contract, calls)
    stale = _SourceProbe(fail_region_source_sha256=region.source_sha256)

    with pytest.raises(MixedStemRoutingRejected, match="fixture_source_changed"):
        route_mixed_stem_regions(source, graph, (adapter,), source_revalidator=stale)
    assert calls == []

    changed = tmp_path / "changed.pdf"
    _write_blank_pdf(changed, pages=2)
    with pytest.raises(MixedStemRoutingRejected, match="document_changed"):
        route_mixed_stem_regions(
            changed, graph, (adapter,), source_revalidator=_SourceProbe()
        )
    assert calls == []


def test_budgets_reject_before_calls_or_result(tmp_path, monkeypatch) -> None:
    from src.education.remediation import mixed_stem_region_router as module

    source = tmp_path / "budget.pdf"
    _write_blank_pdf(source)
    contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    region = build_mixed_stem_region(
        region_kind="chemical_formula", source=contract.locator
    )
    calls = []
    adapter = _adapter("chemical_formula", contract, calls)

    with monkeypatch.context() as patch:
        patch.setattr(
            module, "MAX_MIXED_STEM_DOCUMENT_BYTES", source.stat().st_size - 1
        )
        with pytest.raises(MixedStemRoutingRejected, match="document_byte_limit"):
            _graph(source, (region,))
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_PAGES", 0)
        with pytest.raises(MixedStemRoutingRejected, match="page_limit"):
            _graph(source, (region,))
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_REGIONS", 0)
        with pytest.raises(MixedStemRoutingRejected, match="region_limit"):
            _graph(source, (region,))
    text_source = tmp_path / "native-budget.pdf"
    _write_pdf(text_source)
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_NATIVE_TEXT_BYTES", 1)
        with pytest.raises(MixedStemRoutingRejected, match="native_text_limit"):
            _graph(text_source, ())
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_RASTER_BYTES", 1)
        with pytest.raises(MixedStemRoutingRejected, match="raster_byte_limit"):
            _graph(source, (region,), probe=_SourceProbe(raster_bytes=2))
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_RASTER_PIXELS", 1)
        with pytest.raises(MixedStemRoutingRejected, match="raster_pixel_limit"):
            _graph(source, (region,), probe=_SourceProbe(raster_pixels=2))
    assert calls == []

    graph = _graph(source, (region,))
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_SPECIALIST_CALLS", 0)
        with pytest.raises(MixedStemRoutingRejected, match="specialist_call_limit"):
            route_mixed_stem_regions(
                source, graph, (adapter,), source_revalidator=_SourceProbe()
            )
    assert calls == []
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_PROVIDER_CALLS", 0)
        with pytest.raises(MixedStemRoutingRejected, match="provider_call_limit"):
            route_mixed_stem_regions(
                source, graph, (adapter,), source_revalidator=_SourceProbe()
            )
    assert calls == []
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_MIXED_STEM_SPECIALIST_BYTES", 1)
        with pytest.raises(MixedStemRoutingRejected, match="specialist_payload_limit"):
            route_mixed_stem_regions(
                source, graph, (adapter,), source_revalidator=_SourceProbe()
            )
    assert calls == [("chemical_formula", region.region_id)]


def test_route_binding_and_result_tampering_are_rejected(tmp_path) -> None:
    source = tmp_path / "tamper.pdf"
    _write_blank_pdf(source)
    contract = ChemicalFormulaPdfContract.model_validate(_chemical_contract())
    region = build_mixed_stem_region(
        region_kind="chemical_formula", source=contract.locator
    )
    graph = _graph(source, (region,))
    result = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("chemical_formula", contract, []),),
        source_revalidator=_SourceProbe(),
    )

    raw = result.model_dump(mode="json")
    raw["routes"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        MixedStemRoutingResultV1.model_validate(raw)
    raw = result.model_dump(mode="json")
    raw["unresolved_region_ids"] = [region.region_id]
    with pytest.raises(ValidationError):
        MixedStemRoutingResultV1.model_validate(raw)
    raw = result.model_dump(mode="json")
    raw["publication_authorized"] = True
    with pytest.raises(ValidationError):
        MixedStemRoutingResultV1.model_validate(raw)
