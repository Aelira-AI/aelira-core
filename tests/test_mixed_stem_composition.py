"""Atomic mixed STEM PDF composition and approval contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz
import pytest
import pikepdf
from pydantic import ValidationError

from src.education.canonical_json import canonical_sha256
from src.education.mixed_stem_composition import (
    MIXED_STEM_COMPOSITION_POLICY_VERSION,
    MixedStemCompositionPlanV1,
    MixedStemCompositionRejected,
    MixedStemCompositionResultV1,
    MixedStemSavedCompositionEvidenceV1,
    build_mixed_stem_composition_approval,
    build_mixed_stem_composition_plan,
    build_mixed_stem_composition_result,
    mixed_stem_composition_artifact_available,
)
from src.education.mixed_stem_regions import (
    MixedStemContainmentV1,
    build_mixed_stem_region,
    build_multi_equation_source,
)
from src.education.remediation.mixed_stem_composer import compose_mixed_stem_pdf
from src.education.remediation.mixed_stem_region_router import (
    extract_native_text_sources,
    route_mixed_stem_regions,
)
from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.remediation.vector_equation_semantics import (
    remediate_vector_equation_pdf,
)
from tests.test_mixed_stem_region_routing import (
    _SourceProbe,
    _adapter,
    _graph,
    _raster_contract,
    _write_blank_pdf,
)
from tests.test_multi_equation_semantic_association import (
    _contract as _multi_contract,
)
from tests.test_multi_equation_semantic_association import _group, _planned_owners
from tests.test_vector_equation_semantic_association import (
    _Recognizer as _VectorRecognizer,
)
from tests.test_vector_equation_semantic_association import _Verifier as _VectorVerifier
from tests.test_vector_equation_semantic_association import _write_vector_pdf


def test_composition_policy_is_explicit() -> None:
    assert MIXED_STEM_COMPOSITION_POLICY_VERSION == "mixed-stem-composition-v1"


def _routed_contract(tmp_path, kind="chemical_formula"):
    contract = _raster_contract(kind)
    source = tmp_path / f"{kind}.pdf"
    _write_blank_pdf(source, pages=contract.locator.page_number)
    region = build_mixed_stem_region(region_kind=kind, source=contract.locator)
    graph = _graph(source, (region,))
    result = route_mixed_stem_regions(
        source,
        graph,
        (_adapter(kind, contract, []),),
        source_revalidator=_SourceProbe(),
    )
    return source, result, contract


def _saved_evidence(plan):
    fields = {
        "evidence_kind": "mixed_stem_saved_composition_evidence_v1",
        "output_sha256": "a" * 64,
        "output_bytes": 4096,
        "page_count": plan.routing.graph.page_count,
        "plan_sha256": plan.plan_sha256,
        "structure_sha256": "b" * 64,
        "parent_tree_sha256": "c" * 64,
        "attachment_sha256": tuple(
            digest for entry in plan.entries for digest in entry.attachment_sha256
        ),
        "long_description_sha256": plan.long_description_sha256,
        "render_sha256": ("d" * 64, "f" * 64),
        "visible_text_sha256": ("e" * 64,),
        "reverse_verified_bytes": 4096,
    }
    fields["evidence_sha256"] = canonical_sha256(fields)
    return MixedStemSavedCompositionEvidenceV1.model_validate(fields)


@pytest.mark.parametrize(
    "kind",
    (
        "handwritten_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
    ),
)
def test_contract_binding_visual_semantics_and_long_description(tmp_path, kind) -> None:
    _source, routing, contract = _routed_contract(tmp_path, kind)

    plan = build_mixed_stem_composition_plan(routing, (contract,))

    assert plan.source_sha256 == routing.graph.document_sha256
    assert plan.routing.result_sha256 == routing.result_sha256
    assert len(plan.entries) == 1
    assert plan.entries[0].region_kind == kind
    assert plan.entries[0].contract_sha256 == canonical_sha256(
        contract.model_dump(mode="json")
    )
    assert plan.long_description_items[0].region_id == plan.entries[0].region_id
    assert plan.long_description_text == plan.long_description_items[0].render()
    assert plan.budget.contract_count == 1
    assert plan.budget.attachment_bytes > 0
    assert (
        MixedStemCompositionPlanV1.model_validate_json(plan.model_dump_json()) == plan
    )


def test_resolved_routes_and_exact_contract_set_are_required(tmp_path) -> None:
    source, routing, contract = _routed_contract(tmp_path)
    region = routing.graph.regions[0]
    open_routing = route_mixed_stem_regions(
        source,
        routing.graph,
        (),
        source_revalidator=_SourceProbe(),
    )

    with pytest.raises(MixedStemCompositionRejected, match="unresolved_routes"):
        build_mixed_stem_composition_plan(open_routing, ())
    with pytest.raises(MixedStemCompositionRejected, match="contract_set"):
        build_mixed_stem_composition_plan(routing, ())
    with pytest.raises(MixedStemCompositionRejected, match="contract_set"):
        build_mixed_stem_composition_plan(routing, (contract, contract))

    other = _raster_contract("chemical_structure")
    with pytest.raises(MixedStemCompositionRejected, match="contract_set"):
        build_mixed_stem_composition_plan(routing, (other,))
    assert region.region_kind == "chemical_formula"


@pytest.mark.parametrize(
    ("limit_name", "message"),
    (
        ("MAX_COMPOSITION_CONTRACT_BYTES", "contract_limit"),
        ("MAX_COMPOSITION_ATTACHMENT_BYTES", "attachment_limit"),
        ("MAX_COMPOSITION_DESCRIPTION_BYTES", "description_limit"),
    ),
)
def test_composition_input_budgets_reject_before_writing(
    tmp_path, monkeypatch, limit_name, message
) -> None:
    from src.education import mixed_stem_composition as module

    _source, routing, contract = _routed_contract(tmp_path)
    monkeypatch.setattr(module, limit_name, 1)

    with pytest.raises(MixedStemCompositionRejected, match=message):
        build_mixed_stem_composition_plan(routing, (contract,))
    assert tuple(tmp_path.glob(".*.mixed-stem-*.pdf")) == ()


def test_frozen_contracts_and_plan_digest_reject_tampering(tmp_path) -> None:
    _source, routing, contract = _routed_contract(tmp_path)
    plan = build_mixed_stem_composition_plan(routing, (contract,))

    with pytest.raises(ValidationError):
        plan.plan_sha256 = "0" * 64
    raw = plan.model_dump(mode="json")
    raw["entries"][0]["accessible_text"] = "changed"
    with pytest.raises(ValidationError):
        MixedStemCompositionPlanV1.model_validate(raw)
    raw = plan.model_dump(mode="json")
    raw["unknown"] = True
    with pytest.raises(ValidationError):
        MixedStemCompositionPlanV1.model_validate(raw)


def test_result_never_self_authorizes_and_approval_binds_exact_review(tmp_path) -> None:
    _source, routing, contract = _routed_contract(tmp_path)
    plan = build_mixed_stem_composition_plan(routing, (contract,))
    result = build_mixed_stem_composition_result(plan, _saved_evidence(plan))
    approved_at = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
    review_sha256 = "f" * 64
    approval = build_mixed_stem_composition_approval(
        result,
        review_sha256=review_sha256,
        approved_at=approved_at,
        expires_at=approved_at + timedelta(hours=1),
    )
    assert type(approval).model_validate_json(approval.model_dump_json()) == approval

    assert result.review_required is True
    assert result.publication_authorized is False
    assert result.authorizes_artifact_availability() is False
    assert (
        mixed_stem_composition_artifact_available(
            result,
            None,
            review_sha256=review_sha256,
            now=approved_at,
        )
        is False
    )
    assert (
        mixed_stem_composition_artifact_available(
            result,
            approval,
            review_sha256=review_sha256,
            now=approved_at + timedelta(minutes=1),
        )
        is True
    )
    assert (
        mixed_stem_composition_artifact_available(
            result,
            approval,
            review_sha256="0" * 64,
            now=approved_at + timedelta(minutes=1),
        )
        is False
    )
    assert (
        mixed_stem_composition_artifact_available(
            result,
            approval,
            review_sha256=review_sha256,
            now=approval.expires_at,
        )
        is False
    )
    changed_fields = result.evidence.model_dump(mode="json")
    changed_fields["output_sha256"] = "9" * 64
    changed_fields["evidence_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in changed_fields.items()
            if key != "evidence_sha256"
        }
    )
    changed_result = build_mixed_stem_composition_result(
        plan, MixedStemSavedCompositionEvidenceV1.model_validate(changed_fields)
    )
    assert (
        mixed_stem_composition_artifact_available(
            changed_result,
            approval,
            review_sha256=review_sha256,
            now=approved_at + timedelta(minutes=1),
        )
        is False
    )


def test_result_and_approval_digest_tampering_reject(tmp_path) -> None:
    _source, routing, contract = _routed_contract(tmp_path)
    plan = build_mixed_stem_composition_plan(routing, (contract,))
    result = build_mixed_stem_composition_result(plan, _saved_evidence(plan))
    raw_result = result.model_dump(mode="json")
    raw_result["result_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        MixedStemCompositionResultV1.model_validate(raw_result)

    evidence_fields = _saved_evidence(plan).model_dump(mode="json")
    evidence_fields["attachment_sha256"] = ["9" * 64]
    evidence_fields["evidence_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in evidence_fields.items()
            if key != "evidence_sha256"
        }
    )
    forged_evidence = MixedStemSavedCompositionEvidenceV1.model_validate(
        evidence_fields
    )
    with pytest.raises(ValidationError, match="evidence differs from plan"):
        build_mixed_stem_composition_result(plan, forged_evidence)

    approved_at = datetime.now(timezone.utc)
    approval = build_mixed_stem_composition_approval(
        result,
        review_sha256="f" * 64,
        approved_at=approved_at,
        expires_at=approved_at + timedelta(minutes=5),
    )
    raw_approval = approval.model_dump(mode="json")
    raw_approval["output_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        type(approval).model_validate(raw_approval)


def _printed_composition(tmp_path):
    group = _group(tmp_path)
    source = tmp_path / "split.pdf"
    owners = _planned_owners(source, group)
    contract = _multi_contract(group, owners)
    region = build_mixed_stem_region(
        region_kind="printed_equation",
        source=build_multi_equation_source(group),
    )
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("printed_equation", contract, []),),
    )
    return source, routing, contract


def test_atomic_writer_reuses_printed_specialist_and_reverse_verifies(tmp_path) -> None:
    source, routing, contract = _printed_composition(tmp_path)
    output = tmp_path / "composed.pdf"
    source_before = source.read_bytes()
    graph = routing.graph

    result = compose_mixed_stem_pdf(source, output, routing, (contract,))

    assert source.read_bytes() == source_before
    assert output.exists()
    assert result.evidence.output_sha256 != graph.document_sha256
    assert result.evidence.plan_sha256 == result.plan.plan_sha256
    assert result.authorizes_artifact_availability() is False
    with pikepdf.open(output) as pdf:
        root = pdf.Root[pikepdf.Name.StructTreeRoot]
        assert len(root[pikepdf.Name.K]) == 1
        document = root[pikepdf.Name.K][0]
        assert str(document[pikepdf.Name.S]) == "/Document"
        assert str(document["/AeliraCompositionSHA256"]) == result.plan.plan_sha256


def test_atomic_writer_failure_preserves_prior_output_and_removes_candidate(
    tmp_path, monkeypatch
) -> None:
    from src.education.remediation import mixed_stem_composer as module

    source, routing, contract = _printed_composition(tmp_path)
    output = tmp_path / "prior.pdf"
    output.write_bytes(b"prior output")

    def reject_saved(*_args):
        raise MixedStemCompositionRejected("injected_saved_verification_failure")

    monkeypatch.setattr(module, "verify_saved_mixed_stem_composition", reject_saved)
    with pytest.raises(
        MixedStemCompositionRejected, match="injected_saved_verification_failure"
    ):
        compose_mixed_stem_pdf(source, output, routing, (contract,))

    assert output.read_bytes() == b"prior output"
    assert tuple(tmp_path.glob(".prior.pdf.mixed-stem-*.pdf")) == ()


def test_output_byte_budget_failure_rolls_back(tmp_path, monkeypatch) -> None:
    from src.education.remediation import mixed_stem_composer as module

    source, routing, contract = _printed_composition(tmp_path)
    output = tmp_path / "bounded.pdf"
    output.write_bytes(b"prior output")
    monkeypatch.setattr(module, "MAX_COMPOSITION_OUTPUT_BYTES", 1)

    with pytest.raises(MixedStemCompositionRejected, match="output_byte_limit"):
        compose_mixed_stem_pdf(source, output, routing, (contract,))
    assert output.read_bytes() == b"prior output"
    assert tuple(tmp_path.glob(".bounded.pdf.mixed-stem-*.pdf")) == ()


def test_atomic_writer_rejects_source_destination_and_stale_source(tmp_path) -> None:
    source, routing, contract = _printed_composition(tmp_path)

    with pytest.raises(MixedStemCompositionRejected, match="output_is_source"):
        compose_mixed_stem_pdf(source, source, routing, (contract,))

    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Subject"] = "changed after routing"
        pdf.save(source)
    output = tmp_path / "stale.pdf"
    output.write_bytes(b"prior output")
    with pytest.raises(MixedStemCompositionRejected, match="source_changed"):
        compose_mixed_stem_pdf(source, output, routing, (contract,))
    assert output.read_bytes() == b"prior output"


def test_saved_structure_sabotage_is_rejected_before_promotion(
    tmp_path, monkeypatch
) -> None:
    from src.education.remediation import mixed_stem_composer as module

    source, routing, contract = _printed_composition(tmp_path)
    output = tmp_path / "sabotaged.pdf"
    output.write_bytes(b"prior output")

    original = module.verify_saved_mixed_stem_composition

    def sabotage(candidate, plan, _applied, baseline):
        with pikepdf.open(candidate, allow_overwriting_input=True) as pdf:
            document = pdf.Root[pikepdf.Name.StructTreeRoot][pikepdf.Name.K][0]
            document["/AeliraCompositionSHA256"] = "0" * 64
            pdf.save(candidate)
        return original(candidate, plan, (), baseline)

    monkeypatch.setattr(module, "verify_saved_mixed_stem_composition", sabotage)
    with pytest.raises(MixedStemCompositionRejected):
        compose_mixed_stem_pdf(source, output, routing, (contract,))
    assert output.read_bytes() == b"prior output"
    assert tuple(tmp_path.glob(".sabotaged.pdf.mixed-stem-*.pdf")) == ()


def test_saved_extra_attachment_is_rejected_before_promotion(
    tmp_path, monkeypatch
) -> None:
    from src.education.remediation import mixed_stem_composer as module

    source, routing, contract = _printed_composition(tmp_path)
    output = tmp_path / "extra-attachment.pdf"
    output.write_bytes(b"prior output")

    original = module.verify_saved_mixed_stem_composition

    def add_extra_attachment(candidate, plan, _applied, baseline):
        with pikepdf.open(candidate, allow_overwriting_input=True) as pdf:
            document = pdf.Root[pikepdf.Name.StructTreeRoot][pikepdf.Name.K][0]
            container = document[pikepdf.Name.K][0][pikepdf.Name.K][0]
            semantic = container[pikepdf.Name.K][0]
            embedded = pdf.make_stream(b"unapproved attachment")
            filespec = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name.Filespec,
                        "/F": pikepdf.String("extra.bin"),
                        "/EF": pikepdf.Dictionary({"/F": embedded}),
                    }
                )
            )
            semantic["/AF"] = pikepdf.Array([filespec, *list(semantic["/AF"])])
            pdf.save(candidate)
        return original(candidate, plan, (), baseline)

    monkeypatch.setattr(
        module, "verify_saved_mixed_stem_composition", add_extra_attachment
    )
    with pytest.raises(MixedStemCompositionRejected, match="attachment_changed"):
        compose_mixed_stem_pdf(source, output, routing, (contract,))
    assert output.read_bytes() == b"prior output"


def test_retry_from_same_source_has_stable_structural_evidence(tmp_path) -> None:
    source, routing, contract = _printed_composition(tmp_path)
    first = compose_mixed_stem_pdf(source, tmp_path / "first.pdf", routing, (contract,))
    second = compose_mixed_stem_pdf(
        source, tmp_path / "second.pdf", routing, (contract,)
    )

    assert first.plan.plan_sha256 == second.plan.plan_sha256
    assert first.evidence.structure_sha256 == second.evidence.structure_sha256
    assert first.evidence.parent_tree_sha256 == second.evidence.parent_tree_sha256
    assert first.evidence.attachment_sha256 == second.evidence.attachment_sha256
    assert first.evidence.render_sha256 == second.evidence.render_sha256


def test_multi_page_native_text_is_composed_in_page_reading_order(tmp_path) -> None:
    source = tmp_path / "multi-page.pdf"
    document = fitz.open()
    for page_number, text in enumerate(("First page", "Second page"), start=1):
        page = document.new_page(width=300, height=200)
        page.insert_text((30, 30), f"{page_number}. {text}")
    document.save(source)
    document.close()
    graph = _graph(source, ())
    routing = route_mixed_stem_regions(source, graph, ())
    result = compose_mixed_stem_pdf(
        source, tmp_path / "multi-page-composed.pdf", routing, ()
    )

    assert [
        (entry.page_number, entry.accessible_text) for entry in result.plan.entries
    ] == [
        (1, "1. First page"),
        (2, "2. Second page"),
    ]
    with pikepdf.open(tmp_path / "multi-page-composed.pdf") as pdf:
        document_owner = pdf.Root[pikepdf.Name.StructTreeRoot][pikepdf.Name.K][0]
        page_sections = list(document_owner[pikepdf.Name.K][:-1])
        assert [int(section["/AeliraPageNumber"]) for section in page_sections] == [
            1,
            2,
        ]


def test_atomic_writer_reuses_vector_specialist_and_keeps_native_order(
    tmp_path,
) -> None:
    source = tmp_path / "vector.pdf"
    specialist_output = tmp_path / "vector-specialist.pdf"
    composed = tmp_path / "vector-composed.pdf"
    _write_vector_pdf(source, decoration=True)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    contract = remediate_vector_equation_pdf(
        source,
        specialist_output,
        cluster,
        _VectorRecognizer(),
        _VectorVerifier(),
    )
    region = build_mixed_stem_region(region_kind="vector_equation", source=cluster)
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("vector_equation", contract, []),),
    )

    result = compose_mixed_stem_pdf(source, composed, routing, (contract,))

    assert [entry.region_kind for entry in result.plan.entries] == [
        "native_text",
        "vector_equation",
    ]
    assert result.evidence.attachment_sha256 == result.plan.entries[1].attachment_sha256
    assert result.evidence.render_sha256


def test_multi_page_mixed_document_keeps_visual_on_its_source_page(tmp_path) -> None:
    vector_only = tmp_path / "vector-only.pdf"
    source = tmp_path / "multi-page-mixed.pdf"
    specialist_output = tmp_path / "multi-page-vector-specialist.pdf"
    composed = tmp_path / "multi-page-mixed-composed.pdf"
    _write_vector_pdf(vector_only, decoration=True)
    with fitz.open(vector_only) as document:
        first_page = document.new_page(pno=0, width=240, height=180)
        first_page.insert_text((36, 28), "Introduction", fontsize=10)
        document.save(source)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    assert cluster.page_number == 2
    contract = remediate_vector_equation_pdf(
        source,
        specialist_output,
        cluster,
        _VectorRecognizer(),
        _VectorVerifier(),
    )
    region = build_mixed_stem_region(region_kind="vector_equation", source=cluster)
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("vector_equation", contract, []),),
    )

    result = compose_mixed_stem_pdf(source, composed, routing, (contract,))

    assert [
        (entry.page_number, entry.region_kind) for entry in result.plan.entries
    ] == [
        (1, "native_text"),
        (2, "native_text"),
        (2, "vector_equation"),
    ]
    with pikepdf.open(composed) as pdf:
        document = pdf.Root[pikepdf.Name.StructTreeRoot][pikepdf.Name.K][0]
        page_sections = list(document[pikepdf.Name.K][:-1])
        assert [int(section["/AeliraPageNumber"]) for section in page_sections] == [
            1,
            2,
        ]


def _embedded_specialist_contract(tmp_path, kind, *, nested_label=False):
    from src.education.chemical_formula_pdf import (
        build_chemical_formula_pdf_contract,
    )
    from src.education.chemical_structure_pdf import (
        build_chemical_structure_pdf_contract,
    )
    from src.education.commutative_diagram_pdf import (
        build_commutative_diagram_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_image_chemical_formula,
        associate_image_chemical_structure,
        associate_image_commutative_diagram,
    )
    from tests.test_chemical_formula_pdf_association import (
        _pending as formula_pending,
    )
    from tests.test_chemical_structure_pdf_association import (
        _pending as structure_pending,
    )
    from tests.test_commutative_diagram_pdf_association import (
        _make_reused_image_pdf,
    )
    from tests.test_commutative_diagram_pdf_association import (
        _pending as diagram_pending,
    )

    source = tmp_path / f"{kind}.pdf"
    specialist_output = tmp_path / f"{kind}-specialist.pdf"
    _make_reused_image_pdf(source)
    if nested_label:
        with fitz.open(source) as document:
            document[0].insert_text((130, 145), "Me", fontsize=8)
            document.saveIncr()
    functions = {
        "chemical_formula": (
            formula_pending,
            associate_image_chemical_formula,
            build_chemical_formula_pdf_contract,
        ),
        "chemical_structure": (
            structure_pending,
            associate_image_chemical_structure,
            build_chemical_structure_pdf_contract,
        ),
        "commutative_diagram": (
            diagram_pending,
            associate_image_commutative_diagram,
            build_commutative_diagram_pdf_contract,
        ),
    }
    pending_builder, associate, build_contract = functions[kind]
    with fitz.open(source) as fitz_doc:
        pending = pending_builder(fitz_doc)
        with pikepdf.open(source) as pdf:
            association = associate(pdf, fitz_doc, pending)
            assert association.success is True
            pdf.save(specialist_output)
    return (
        source,
        pending.locator,
        build_contract(specialist_output, pending, association),
    )


@pytest.mark.parametrize(
    "kind", ("chemical_formula", "chemical_structure", "commutative_diagram")
)
def test_atomic_writer_reuses_embedded_visual_specialists(tmp_path, kind) -> None:
    source, source_locator, contract = _embedded_specialist_contract(tmp_path, kind)
    assert contract.locator.image_xref != source_locator.image_xref
    region = build_mixed_stem_region(region_kind=kind, source=source_locator)
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter(kind, contract, []),),
    )

    result = compose_mixed_stem_pdf(
        source, tmp_path / f"{kind}-composed.pdf", routing, (contract,)
    )

    assert [entry.region_kind for entry in result.plan.entries] == [kind]
    assert result.evidence.attachment_sha256 == result.plan.entries[0].attachment_sha256


def test_declared_native_label_is_nested_under_its_visual_owner(tmp_path) -> None:
    source, source_locator, contract = _embedded_specialist_contract(
        tmp_path, "chemical_structure", nested_label=True
    )
    parent = build_mixed_stem_region(
        region_kind="chemical_structure", source=source_locator
    )
    with fitz.open(source) as document:
        native_source = extract_native_text_sources(document)[0]
    child = build_mixed_stem_region(region_kind="native_text", source=native_source)
    containment = MixedStemContainmentV1(
        edge_kind="contains_label_v1",
        parent_region_id=parent.region_id,
        child_region_id=child.region_id,
    )
    graph = _graph(source, (parent,), containments=(containment,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("chemical_structure", contract, []),),
    )

    result = compose_mixed_stem_pdf(
        source, tmp_path / "nested-composed.pdf", routing, (contract,)
    )

    assert [entry.region_id for entry in result.plan.entries] == [
        parent.region_id,
        child.region_id,
    ]
    with pikepdf.open(tmp_path / "nested-composed.pdf") as pdf:
        document = pdf.Root[pikepdf.Name.StructTreeRoot][pikepdf.Name.K][0]
        saved_parent = document[pikepdf.Name.K][0][pikepdf.Name.K][0]
        nested = [
            item
            for item in saved_parent[pikepdf.Name.K]
            if str(item.get("/AeliraRegionID", ""))
        ]
        assert [str(item["/AeliraRegionID"]) for item in nested] == [child.region_id]


def _scanned_specialist_contract(tmp_path, kind):
    from src.education.chemical_formula_pdf import (
        build_chemical_formula_pdf_contract,
    )
    from src.education.chemical_structure_pdf import (
        build_chemical_structure_pdf_contract,
    )
    from src.education.commutative_diagram_pdf import (
        build_commutative_diagram_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_scanned_region_chemical_formula,
        associate_scanned_region_chemical_structure,
        associate_scanned_region_commutative_diagram,
    )
    from src.education.remediation.pdf_structure import PDFStructureTree
    from tests.test_chemical_formula_pdf_association import (
        _region_pending as formula_pending,
    )
    from tests.test_chemical_structure_pdf_association import (
        _region_pending as structure_pending,
    )
    from tests.test_commutative_diagram_pdf_association import (
        _region_pending as diagram_pending,
    )
    from tests.test_commutative_diagram_pdf_association import (
        _write_scan as write_diagram_scan,
    )
    from tests.test_scanned_equation_region_association import (
        _write_scan as write_formula_scan,
    )

    source = tmp_path / f"{kind}-scan.pdf"
    specialist_output = tmp_path / f"{kind}-scan-specialist.pdf"
    (write_formula_scan if kind == "chemical_formula" else write_diagram_scan)(source)
    functions = {
        "chemical_formula": (
            formula_pending,
            associate_scanned_region_chemical_formula,
            build_chemical_formula_pdf_contract,
        ),
        "chemical_structure": (
            structure_pending,
            associate_scanned_region_chemical_structure,
            build_chemical_structure_pdf_contract,
        ),
        "commutative_diagram": (
            diagram_pending,
            associate_scanned_region_commutative_diagram,
            build_commutative_diagram_pdf_contract,
        ),
    }
    pending_builder, associate, build_contract = functions[kind]
    with fitz.open(source) as fitz_doc:
        pending = pending_builder(fitz_doc)
        with pikepdf.open(source) as pdf:
            PDFStructureTree(pdf)
            association = associate(pdf, fitz_doc, pending)
            assert association.success is True
            pdf.save(specialist_output)
    return (
        source,
        pending.locator,
        build_contract(specialist_output, pending, association),
    )


@pytest.mark.parametrize(
    "kind", ("chemical_formula", "chemical_structure", "commutative_diagram")
)
def test_atomic_writer_reuses_scanned_region_specialists(tmp_path, kind) -> None:
    source, source_locator, contract = _scanned_specialist_contract(tmp_path, kind)
    region = build_mixed_stem_region(region_kind=kind, source=source_locator)
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter(kind, contract, []),),
    )

    result = compose_mixed_stem_pdf(
        source, tmp_path / f"{kind}-scan-composed.pdf", routing, (contract,)
    )

    assert [entry.region_kind for entry in result.plan.entries] == [kind]
    assert result.evidence.attachment_sha256 == result.plan.entries[0].attachment_sha256


def _handwritten_specialist_contract(tmp_path):
    from src.education.handwritten_math_suitability import (
        POLICY_SHA256,
        classify_handwritten_math_suitability,
    )
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.remediation.content_tagger_v2 import associate_image_formula
    from src.education.remediation.equation_image_source import EquationImageSource
    from src.education.remediation.handwritten_equation_verifier import (
        HANDWRITTEN_VERIFIER_POLICY_SHA256,
        HANDWRITTEN_VERIFIER_POLICY_VERSION,
        HandwrittenEquationVerificationEvidence,
    )
    from src.education.remediation.math_fixer import PendingEquationAssociation
    from src.education.remediation.pdf_remediator import (
        _handwritten_equation_contract,
    )
    from src.education.visual_semantic_contract import EmbeddedImageOccurrenceLocator
    from tests.test_image_equation_content_association import MATHML

    source = tmp_path / "handwritten.pdf"
    specialist_output = tmp_path / "handwritten-specialist.pdf"
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "handwritten_math"
        / "images"
        / "legible-linear.png"
    ).read_bytes()
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_image(fitz.Rect(40, 40, 180, 120), stream=fixture)
    document.save(source)
    document.close()
    with fitz.open(source) as fitz_doc:
        occurrence = _displayed_image_occurrences(fitz_doc[0], 1)[0]
        stream = fitz_doc.extract_image(occurrence["image_xref"])["image"]
        locator = EmbeddedImageOccurrenceLocator(
            source_kind="embedded_image_occurrence",
            **occurrence,
            image_stream_sha256=hashlib.sha256(stream).hexdigest(),
        )
        validated = EquationImageSource().extract(
            fitz_doc, locator.model_dump(mode="json")
        )
        suitability = classify_handwritten_math_suitability(validated.jpeg_bytes)
        mathml_sha256 = hashlib.sha256(MATHML.encode("utf-8")).hexdigest()
        consensus = HandwrittenEquationVerificationEvidence(
            passed=True,
            source_sha256=validated.normalized_sha256,
            suitability_evidence=suitability,
            suitability_evidence_sha256=suitability.evidence_sha256,
            suitability_policy_sha256=POLICY_SHA256,
            verifier_policy_version=HANDWRITTEN_VERIFIER_POLICY_VERSION,
            verifier_policy_sha256=HANDWRITTEN_VERIFIER_POLICY_SHA256,
            agreement_count=2,
            required_agreement_count=2,
            mathml_sha256=mathml_sha256,
            primary_mathml_sha256=mathml_sha256,
            verifier_mathml_sha256=mathml_sha256,
            primary_response_sha256="1" * 64,
            verifier_response_sha256="2" * 64,
            primary_latex_sha256="3" * 64,
            verifier_latex_sha256="4" * 64,
            primary_provider="fixture-primary",
            primary_model="hmer-primary-v1",
            verifier_provider="fixture-verifier",
            verifier_model="hmer-verifier-v1",
        )
        pending = PendingEquationAssociation(
            page_number=locator.page_number,
            image_xref=locator.image_xref,
            image_index=locator.image_index,
            occurrence_ordinal=locator.occurrence_ordinal,
            bbox=locator.bbox,
            occurrence_id=locator.occurrence_id,
            image_stream_sha256=locator.image_stream_sha256,
            alt_text="x squared",
            mathml_string=MATHML,
            provider_used=consensus.primary_provider,
            model_used=consensus.primary_model,
            verification_evidence=consensus,
        )
        with pikepdf.open(source) as pdf:
            association = associate_image_formula(pdf, fitz_doc, pending)
            assert association.success is True
            pdf.save(specialist_output)
    contract = _handwritten_equation_contract(specialist_output, pending, association)
    return source, locator, contract


def test_atomic_writer_reuses_handwritten_specialist(tmp_path) -> None:
    source, source_locator, contract = _handwritten_specialist_contract(tmp_path)
    region = build_mixed_stem_region(
        region_kind="handwritten_equation", source=source_locator
    )
    graph = _graph(source, (region,))
    routing = route_mixed_stem_regions(
        source,
        graph,
        (_adapter("handwritten_equation", contract, []),),
    )

    result = compose_mixed_stem_pdf(
        source, tmp_path / "handwritten-composed.pdf", routing, (contract,)
    )

    assert [entry.region_kind for entry in result.plan.entries] == [
        "handwritten_equation"
    ]
    assert result.evidence.attachment_sha256 == result.plan.entries[0].attachment_sha256
