"""Atomic saved-file composition for fully resolved mixed STEM region graphs."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fitz
import pikepdf
from pikepdf import Array, Dictionary, Name, String
from pydantic import BaseModel

from src.education.canonical_json import canonical_sha256
from src.education.chemical_formula_pdf import (
    ChemicalFormulaPendingAssociationV1,
    ChemicalFormulaRecognitionV1,
)
from src.education.chemical_structure_pdf import (
    ChemicalStructurePendingAssociationV1,
    ChemicalStructureRecognitionV1,
)
from src.education.commutative_diagram_pdf import (
    CommutativeDiagramPendingAssociationV1,
    CommutativeDiagramRecognitionV1,
)
from src.education.mixed_stem_composition import (
    MAX_COMPOSITION_OUTPUT_BYTES,
    MixedStemCompositionPlanV1,
    MixedStemCompositionRejected,
    MixedStemCompositionResultV1,
    MixedStemSavedCompositionEvidenceV1,
    build_mixed_stem_composition_plan,
    build_mixed_stem_composition_result,
)
from src.education.multi_equation_semantics import MultiEquationSemanticContractV1
from src.education.remediation.content_tagger_v2 import (
    associate_image_chemical_formula,
    associate_image_chemical_structure,
    associate_image_commutative_diagram,
    associate_image_formula,
    associate_scanned_region_chemical_formula,
    associate_scanned_region_chemical_structure,
    associate_scanned_region_commutative_diagram,
    associate_scanned_region_formula,
    verify_image_chemical_formula_association,
    verify_image_chemical_structure_association,
    verify_image_commutative_diagram_association,
    verify_image_formula_association,
    verify_scanned_region_chemical_formula_association,
    verify_scanned_region_chemical_structure_association,
    verify_scanned_region_commutative_diagram_association,
    verify_scanned_region_formula_association,
)
from src.education.remediation.equation_image_source import (
    EquationImageSource,
    EquationRegionSource,
)
from src.education.remediation.multi_equation_semantics import (
    MultiEquationAssociation,
    associate_multi_equation_formulas,
    verify_multi_equation_formulas,
)
from src.education.remediation.pdf_structure import PDFStructureTree
from src.education.remediation.vector_equation_semantics import (
    VectorEquationAssociation,
    associate_vector_equation_formula,
    verify_vector_equation_formula_association,
)
from src.education.vector_equation_semantics import VectorEquationSemanticContractV1
from src.education.visual_semantic_contract import (
    ChemicalFormulaPdfContract,
    ChemicalFormulaRecognitionEvidenceV1,
    ChemicalStructurePdfContract,
    ChemicalStructureRecognitionEvidenceV1,
    CommutativeDiagramPdfContract,
    CommutativeDiagramRecognitionEvidenceV1,
    EmbeddedImageOccurrenceLocator,
    HandwrittenEquationConsensusEvidenceV1,
    HandwrittenEquationContract,
)


@dataclass(frozen=True)
class AppliedStemSpecialist:
    """In-memory identities required to locate and reverify one saved region."""

    region_id: str
    region_kind: str
    page_number: int
    mcids: tuple[int, ...]
    verify_saved: Callable[[Path], bool]


@dataclass(frozen=True)
class _Baseline:
    source_sha256: str
    source_bytes: int
    page_count: int
    render_sha256: tuple[str, ...]
    visible_text_sha256: tuple[str, ...]
    metadata_sha256: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_sha256(document: fitz.Document) -> tuple[str, ...]:
    digests: list[str] = []
    for page in document:
        for dpi in (72, 144):
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            if pixmap.width > 16_384 or pixmap.height > 16_384:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_render_limit"
                )
            material = (
                f"{page.number + 1}|{dpi}|{pixmap.width}|{pixmap.height}|"
                f"{pixmap.n}|{pixmap.stride}|"
            ).encode("ascii") + bytes(pixmap.samples)
            digests.append(hashlib.sha256(material).hexdigest())
    return tuple(digests)


def _visible_text_sha256(document: fitz.Document) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(page.get_text("text").encode("utf-8")).hexdigest()
        for page in document
    )


def _metadata_sha256(document: fitz.Document) -> str:
    return canonical_sha256(
        {str(key): str(value) for key, value in sorted(document.metadata.items())}
    )


def _baseline(path: Path) -> _Baseline:
    try:
        source_bytes = path.stat().st_size
        source_sha256 = _file_sha256(path)
        with fitz.open(path) as document:
            page_count = document.page_count
            render = _render_sha256(document)
            text = _visible_text_sha256(document)
            metadata = _metadata_sha256(document)
    except MixedStemCompositionRejected:
        raise
    except Exception as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_source_unavailable"
        ) from exc
    return _Baseline(
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        page_count=page_count,
        render_sha256=render,
        visible_text_sha256=text,
        metadata_sha256=metadata,
    )


def _recognition_evidence(contract: BaseModel, expected: type[BaseModel]) -> BaseModel:
    matches = [
        item
        for item in getattr(contract, "verification_evidence", ())
        if isinstance(item, expected)
    ]
    if len(matches) != 1:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_recognition_evidence"
        )
    return matches[0]


def _validate_visual_contract_source(
    contract: BaseModel, locator: Any, fitz_doc: Any
) -> None:
    try:
        if isinstance(locator, EmbeddedImageOccurrenceLocator):
            expected = (
                EquationImageSource()
                .extract(fitz_doc, locator.model_dump(mode="json"))
                .normalized_sha256
                if isinstance(contract, HandwrittenEquationContract)
                else locator.image_stream_sha256
            )
        else:
            expected = (
                EquationRegionSource()
                .extract(fitz_doc, locator.model_dump(mode="json"))
                .normalized_sha256
            )
    except Exception as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_specialist_source_changed"
        ) from exc
    if getattr(contract, "normalized_source_sha256", None) != expected:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_specialist_source_changed"
        )


def _formula_pending(
    contract: HandwrittenEquationContract, fitz_doc: Any, locator: Any
) -> Any:
    evidence = _recognition_evidence(contract, HandwrittenEquationConsensusEvidenceV1)
    if isinstance(locator, EmbeddedImageOccurrenceLocator):
        return SimpleNamespace(
            page_number=locator.page_number,
            image_xref=locator.image_xref,
            image_index=locator.image_index,
            occurrence_ordinal=locator.occurrence_ordinal,
            bbox=tuple(locator.bbox),
            occurrence_id=locator.occurrence_id,
            image_stream_sha256=locator.image_stream_sha256,
            alt_text=contract.semantic_output.alt_text,
            mathml_string=contract.semantic_output.mathml,
            verification_evidence=evidence,
        )
    validated = EquationRegionSource().extract(
        fitz_doc, locator.model_dump(mode="json")
    )
    return SimpleNamespace(
        locator=locator,
        working_occurrence=validated.working_occurrence,
        normalized_crop_sha256=validated.normalized_sha256,
        alt_text=contract.semantic_output.alt_text,
        mathml_string=contract.semantic_output.mathml,
        verification_evidence=evidence,
        page_number=locator.page_number,
        image_xref=locator.image_xref,
        image_index=locator.image_index,
        occurrence_ordinal=locator.occurrence_ordinal,
        occurrence_id=locator.parent_occurrence_id,
        bbox=tuple(locator.pdf_bbox),
        region_bbox=tuple(locator.pdf_bbox),
        parent_bbox=tuple(locator.parent_bbox),
        working_parent_bbox=tuple(locator.parent_bbox),
    )


def _chemical_formula_pending(
    contract: ChemicalFormulaPdfContract,
    locator: Any,
) -> ChemicalFormulaPendingAssociationV1:
    evidence = _recognition_evidence(contract, ChemicalFormulaRecognitionEvidenceV1)
    recognition = ChemicalFormulaRecognitionV1(
        recognition_kind="chemical_formula_recognition_v1",
        verified_notation=contract.semantic_output.verified_notation,
        normalized_source_sha256=evidence.normalized_source_sha256,
        provider=evidence.provider,
        model=evidence.model,
        response_sha256=evidence.response_sha256,
        verifier_version=evidence.verifier_version,
        attempts=evidence.attempts,
    )
    return ChemicalFormulaPendingAssociationV1(
        pending_kind="chemical_formula_pdf_association_v1",
        locator=locator,
        semantic_output=contract.semantic_output,
        recognition=recognition,
    )


def _chemical_structure_pending(
    contract: ChemicalStructurePdfContract,
    locator: Any,
) -> ChemicalStructurePendingAssociationV1:
    evidence = _recognition_evidence(contract, ChemicalStructureRecognitionEvidenceV1)
    recognition = ChemicalStructureRecognitionV1(
        recognition_kind="chemical_structure_recognition_v1",
        graph=contract.semantic_output.graph,
        graph_sha256=evidence.graph_sha256,
        abbreviations=evidence.abbreviations,
        abbreviation_evidence_sha256=evidence.abbreviation_evidence_sha256,
        abbreviation_policy_version=evidence.abbreviation_policy_version,
        normalized_source_sha256=evidence.normalized_source_sha256,
        provider=evidence.provider,
        model=evidence.model,
        response_sha256=evidence.response_sha256,
        verifier_version=evidence.verifier_version,
        attempts=evidence.attempts,
    )
    return ChemicalStructurePendingAssociationV1(
        pending_kind="chemical_structure_pdf_association_v1",
        locator=locator,
        semantic_output=contract.semantic_output,
        recognition=recognition,
    )


def _diagram_pending(
    contract: CommutativeDiagramPdfContract,
    locator: Any,
) -> CommutativeDiagramPendingAssociationV1:
    evidence = _recognition_evidence(contract, CommutativeDiagramRecognitionEvidenceV1)
    recognition = CommutativeDiagramRecognitionV1(
        recognition_kind="commutative_diagram_recognition_v1",
        graph=contract.semantic_output.graph,
        graph_sha256=evidence.graph_sha256,
        normalized_source_sha256=evidence.normalized_source_sha256,
        provider=evidence.provider,
        model=evidence.model,
        response_sha256=evidence.response_sha256,
        verifier_version=evidence.verifier_version,
        attempts=evidence.attempts,
    )
    return CommutativeDiagramPendingAssociationV1(
        pending_kind="commutative_diagram_pdf_association_v1",
        locator=locator,
        semantic_output=contract.semantic_output,
        recognition=recognition,
    )


def _apply_specialist(
    pdf: Any,
    fitz_doc: Any,
    region_id: str,
    source_locator: Any,
    contract: BaseModel,
) -> AppliedStemSpecialist:
    if isinstance(contract, MultiEquationSemanticContractV1):
        association: MultiEquationAssociation = associate_multi_equation_formulas(
            pdf, fitz_doc, contract.group, contract.owners
        )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="printed_equation",
            page_number=contract.group.page_number,
            mcids=tuple(owner.mcid for owner in association.owners),
            verify_saved=lambda path: bool(
                verify_multi_equation_formulas(
                    path, contract.group, contract.owners, association
                )
            ),
        )
    if isinstance(contract, VectorEquationSemanticContractV1):
        association: VectorEquationAssociation = associate_vector_equation_formula(
            pdf, fitz_doc, contract.cluster, contract.semantic_plan
        )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="vector_equation",
            page_number=contract.cluster.page_number,
            mcids=association.mcids,
            verify_saved=lambda path: verify_vector_equation_formula_association(
                path, contract.cluster, contract.semantic_plan, association
            ),
        )
    if isinstance(contract, HandwrittenEquationContract):
        _validate_visual_contract_source(contract, source_locator, fitz_doc)
        pending = _formula_pending(contract, fitz_doc, source_locator)
        if isinstance(source_locator, EmbeddedImageOccurrenceLocator):
            association = associate_image_formula(pdf, fitz_doc, pending)
            verifier = partial(
                verify_image_formula_association,
                pending=pending,
                expected=association,
            )
        else:
            association = associate_scanned_region_formula(pdf, fitz_doc, pending)
            verifier = partial(
                verify_scanned_region_formula_association,
                pending=pending,
                expected=association,
            )
        if getattr(association, "success", True) is not True:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_specialist_association_failed"
            )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="handwritten_equation",
            page_number=source_locator.page_number,
            mcids=(association.mcid,),
            verify_saved=verifier,
        )
    if isinstance(contract, ChemicalFormulaPdfContract):
        _validate_visual_contract_source(contract, source_locator, fitz_doc)
        pending = _chemical_formula_pending(contract, source_locator)
        if isinstance(source_locator, EmbeddedImageOccurrenceLocator):
            association = associate_image_chemical_formula(pdf, fitz_doc, pending)
            verifier = partial(
                verify_image_chemical_formula_association,
                pending=pending,
                expected=association,
            )
        else:
            association = associate_scanned_region_chemical_formula(
                pdf, fitz_doc, pending
            )
            verifier = partial(
                verify_scanned_region_chemical_formula_association,
                pending=pending,
                expected=association,
            )
        if association.success is not True:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_specialist_association_failed"
            )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="chemical_formula",
            page_number=source_locator.page_number,
            mcids=(association.mcid,),
            verify_saved=verifier,
        )
    if isinstance(contract, ChemicalStructurePdfContract):
        _validate_visual_contract_source(contract, source_locator, fitz_doc)
        pending = _chemical_structure_pending(contract, source_locator)
        if isinstance(source_locator, EmbeddedImageOccurrenceLocator):
            association = associate_image_chemical_structure(pdf, fitz_doc, pending)
            verifier = partial(
                verify_image_chemical_structure_association,
                pending=pending,
                expected=association,
            )
        else:
            association = associate_scanned_region_chemical_structure(
                pdf, fitz_doc, pending
            )
            verifier = partial(
                verify_scanned_region_chemical_structure_association,
                pending=pending,
                expected=association,
            )
        if association.success is not True:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_specialist_association_failed"
            )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="chemical_structure",
            page_number=source_locator.page_number,
            mcids=(association.mcid,),
            verify_saved=verifier,
        )
    if isinstance(contract, CommutativeDiagramPdfContract):
        _validate_visual_contract_source(contract, source_locator, fitz_doc)
        pending = _diagram_pending(contract, source_locator)
        if isinstance(source_locator, EmbeddedImageOccurrenceLocator):
            association = associate_image_commutative_diagram(pdf, fitz_doc, pending)
            verifier = partial(
                verify_image_commutative_diagram_association,
                pending=pending,
                expected=association,
            )
        else:
            association = associate_scanned_region_commutative_diagram(
                pdf, fitz_doc, pending
            )
            verifier = partial(
                verify_scanned_region_commutative_diagram_association,
                pending=pending,
                expected=association,
            )
        if association.success is not True:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_specialist_association_failed"
            )
        return AppliedStemSpecialist(
            region_id=region_id,
            region_kind="commutative_diagram",
            page_number=source_locator.page_number,
            mcids=(association.mcid,),
            verify_saved=verifier,
        )
    raise MixedStemCompositionRejected("mixed_stem_composition_contract_type")


def _children(element: Any) -> list[Any]:
    if not hasattr(element, "get"):
        return []
    kids = element.get(Name.K)
    if isinstance(kids, Array):
        return list(kids)
    return [kids] if kids is not None else []


def _walk_structure(element: Any) -> Iterable[Any]:
    if not hasattr(element, "keys"):
        return
    yield element
    for child in _children(element):
        if hasattr(child, "keys") and str(child.get(Name.Type, "")) != "/MCR":
            yield from _walk_structure(child)


def _same_object(first: Any, second: Any) -> bool:
    return (
        hasattr(first, "objgen")
        and hasattr(second, "objgen")
        and tuple(first.objgen) == tuple(second.objgen)
    )


def _remove_child(parent: Any, child: Any) -> None:
    children = _children(parent)
    kept = [item for item in children if not _same_object(item, child)]
    if len(kept) == len(children):
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_structure_backlink_missing"
        )
    parent[Name.K] = Array(kept)


def _marked_content_references(element: Any) -> tuple[Any, ...]:
    kids = element.get(Name.K) if hasattr(element, "get") else None
    candidates = list(kids) if isinstance(kids, Array) else [kids]
    return tuple(
        candidate
        for candidate in candidates
        if hasattr(candidate, "keys") and str(candidate.get(Name.Type, "")) == "/MCR"
    )


def _owned_elements(
    pdf: Any,
    *,
    page_number: int,
    mcids: tuple[int, ...],
) -> tuple[Any, ...]:
    root = pdf.Root[Name.StructTreeRoot]
    page_objgen = tuple(pdf.pages[page_number - 1].obj.objgen)
    found: dict[int, Any] = {}
    for element in _walk_structure(root):
        for mcr in _marked_content_references(element):
            mcid = int(mcr[Name.MCID])
            if (
                mcid not in mcids
                or not hasattr(mcr.get(Name.Pg), "objgen")
                or tuple(mcr.get(Name.Pg).objgen) != page_objgen
            ):
                continue
            if mcid in found and not _same_object(found[mcid], element):
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_structure_owner_ambiguous"
                )
            found[mcid] = element
    if set(found) != set(mcids) or len(found) != len(mcids):
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_structure_owner_missing"
        )
    ordered: list[Any] = []
    for mcid in mcids:
        if not any(_same_object(found[mcid], existing) for existing in ordered):
            ordered.append(found[mcid])
    return tuple(ordered)


def _make_indirect(pdf: Any, values: dict[str, Any]) -> Any:
    return pdf.make_indirect(Dictionary(values))


def _build_structure_hierarchy(
    pdf: Any,
    plan: MixedStemCompositionPlanV1,
    applied: tuple[AppliedStemSpecialist, ...],
) -> None:
    tree = PDFStructureTree(pdf)
    struct_root = tree.struct_root
    document_owner = _make_indirect(
        pdf,
        {
            "/Type": Name.StructElem,
            "/S": Name.Document,
            "/P": struct_root,
            "/AeliraCompositionSHA256": String(plan.plan_sha256),
        },
    )
    long_description = _make_indirect(
        pdf,
        {
            "/Type": Name.StructElem,
            "/S": Name.Sect,
            "/P": document_owner,
            "/ActualText": String(plan.long_description_text),
            "/AeliraLongDescriptionSHA256": String(plan.long_description_sha256),
        },
    )
    by_region = {item.region_id: item for item in applied}
    region_containers: dict[str, Any] = {}
    for entry in plan.entries:
        page_obj = pdf.pages[entry.page_number - 1].obj
        container = _make_indirect(
            pdf,
            {
                "/Type": Name.StructElem,
                "/S": Name.Sect,
                "/Pg": page_obj,
                "/AeliraRegionID": String(entry.region_id),
                "/AeliraRegionSHA256": String(entry.region_sha256),
                "/AeliraSourceSHA256": String(entry.source_sha256),
                "/AeliraEntrySHA256": String(entry.entry_sha256),
            },
        )
        if entry.region_kind == "native_text":
            semantic = _make_indirect(
                pdf,
                {
                    "/Type": Name.StructElem,
                    "/S": Name.P,
                    "/Pg": page_obj,
                    "/P": container,
                    "/ActualText": String(entry.accessible_text),
                },
            )
            container[Name.K] = Array([semantic])
        else:
            item = by_region.get(entry.region_id)
            if item is None:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_applied_region_missing"
                )
            semantic_elements = _owned_elements(
                pdf, page_number=item.page_number, mcids=item.mcids
            )
            if len(semantic_elements) != entry.structure_element_count:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_structure_count"
                )
            for semantic in semantic_elements:
                parent = semantic.get(Name.P)
                if not hasattr(parent, "keys"):
                    raise MixedStemCompositionRejected(
                        "mixed_stem_composition_structure_parent_missing"
                    )
                _remove_child(parent, semantic)
                semantic[Name.P] = container
            container[Name.K] = Array(list(semantic_elements))
            container[Name("/Ref")] = Array([long_description])
            container[Name("/AeliraContractSHA256")] = String(entry.contract_sha256)
        region_containers[entry.region_id] = container

    parent_by_child = {
        edge.child_region_id: edge.parent_region_id
        for edge in plan.routing.graph.containment
    }
    page_sections: dict[int, Any] = {}
    page_children: dict[int, list[Any]] = {
        page_number: [] for page_number in range(1, plan.routing.graph.page_count + 1)
    }
    entry_ordinal = {entry.region_id: entry.ordinal for entry in plan.entries}
    for entry in plan.entries:
        container = region_containers[entry.region_id]
        parent_id = parent_by_child.get(entry.region_id)
        if parent_id is None:
            page_children[entry.page_number].append(container)
            continue
        if entry_ordinal[parent_id] >= entry.ordinal:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_containment_order"
            )
        parent = region_containers[parent_id]
        existing = _children(parent)
        parent[Name.K] = Array(existing + [container])
        container[Name.P] = parent

    for page_number, children in page_children.items():
        page_section = _make_indirect(
            pdf,
            {
                "/Type": Name.StructElem,
                "/S": Name.Sect,
                "/P": document_owner,
                "/Pg": pdf.pages[page_number - 1].obj,
                "/AeliraPageNumber": page_number,
                "/K": Array(children),
            },
        )
        for child in children:
            child[Name.P] = page_section
        page_sections[page_number] = page_section
    document_owner[Name.K] = Array(
        [page_sections[index] for index in sorted(page_sections)] + [long_description]
    )
    struct_root[Name.K] = Array([document_owner])


def _page_parent_tree_entries(struct_root: Any) -> dict[int, Any]:
    parent_tree = struct_root.get(Name.ParentTree)
    nums = parent_tree.get(Name.Nums) if hasattr(parent_tree, "get") else None
    if not isinstance(nums, Array) or len(nums) % 2:
        raise MixedStemCompositionRejected("mixed_stem_composition_parent_tree_invalid")
    entries: dict[int, Any] = {}
    for index in range(0, len(nums), 2):
        key = int(nums[index])
        if key in entries:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_parent_tree_duplicate"
            )
        entries[key] = nums[index + 1]
    return entries


def _attachment_digests(element: Any) -> tuple[str, ...]:
    found: list[str] = []
    for current in _walk_structure(element):
        attachments = current.get(Name("/AF"))
        if not isinstance(attachments, Array):
            continue
        for filespec in attachments:
            embedded = filespec.get(Name("/EF"))
            stream = embedded.get(Name.F) if hasattr(embedded, "get") else None
            if stream is None:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_attachment_invalid"
                )
            found.append(hashlib.sha256(stream.read_bytes()).hexdigest())
    return tuple(found)


def _verify_structure(
    pdf: Any,
    plan: MixedStemCompositionPlanV1,
) -> tuple[str, str, tuple[str, ...]]:
    struct_root = pdf.Root.get(Name.StructTreeRoot)
    roots = _children(struct_root)
    if len(roots) != 1 or str(roots[0].get(Name.S, "")) != "/Document":
        raise MixedStemCompositionRejected("mixed_stem_composition_document_owner")
    document_owner = roots[0]
    if str(
        document_owner.get(Name("/AeliraCompositionSHA256"), "")
    ) != plan.plan_sha256 or not _same_object(document_owner.get(Name.P), struct_root):
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_plan_binding_changed"
        )
    document_children = _children(document_owner)
    if len(document_children) != plan.routing.graph.page_count + 1:
        raise MixedStemCompositionRejected("mixed_stem_composition_page_sections")
    long_description = document_children[-1]
    if (
        str(long_description.get(Name.S, "")) != "/Sect"
        or str(long_description.get(Name("/ActualText"), ""))
        != plan.long_description_text
        or str(long_description.get(Name("/AeliraLongDescriptionSHA256"), ""))
        != plan.long_description_sha256
        or not _same_object(long_description.get(Name.P), document_owner)
        or _children(long_description)
    ):
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_long_description_invalid"
        )
    flattened: list[str] = []
    structure_material: list[dict[str, Any]] = []
    attachment_sha256: list[str] = []
    parent_entries = _page_parent_tree_entries(struct_root)
    parent_material: list[dict[str, Any]] = []
    expected_by_id = {entry.region_id: entry for entry in plan.entries}

    def inspect_region(container: Any, page_number: int, expected_parent: Any) -> None:
        region_id = str(container.get(Name("/AeliraRegionID"), ""))
        entry = expected_by_id.get(region_id)
        if entry is None or entry.page_number != page_number:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_saved_region_unknown"
            )
        if (
            str(container.get(Name("/AeliraRegionSHA256"), "")) != entry.region_sha256
            or str(container.get(Name("/AeliraSourceSHA256"), ""))
            != entry.source_sha256
            or str(container.get(Name("/AeliraEntrySHA256"), "")) != entry.entry_sha256
            or not _same_object(container.get(Name.P), expected_parent)
            or not _same_object(container.get(Name.Pg), pdf.pages[page_number - 1].obj)
        ):
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_saved_region_changed"
            )
        flattened.append(region_id)
        semantic_children = [
            child
            for child in _children(container)
            if not str(child.get(Name("/AeliraRegionID"), ""))
        ]
        nested = [
            child
            for child in _children(container)
            if str(child.get(Name("/AeliraRegionID"), ""))
        ]
        if len(semantic_children) != entry.structure_element_count:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_saved_structure_count"
            )
        if entry.region_kind == "native_text":
            if (
                len(semantic_children) != 1
                or str(semantic_children[0].get(Name.S, "")) != "/P"
                or str(semantic_children[0].get(Name("/ActualText"), ""))
                != entry.accessible_text
                or not _same_object(semantic_children[0].get(Name.P), container)
            ):
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_native_structure_changed"
                )
        else:
            references = container.get(Name("/Ref"))
            if (
                not isinstance(references, Array)
                or len(references) != 1
                or not _same_object(references[0], long_description)
                or str(container.get(Name("/AeliraContractSHA256"), ""))
                != entry.contract_sha256
            ):
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_description_reference_changed"
                )
            roles: list[str] = []
            mcids: list[int] = []
            for semantic in semantic_children:
                role = str(semantic.get(Name.S, ""))
                if role != f"/{entry.structure_role}" or not _same_object(
                    semantic.get(Name.P), container
                ):
                    raise MixedStemCompositionRejected(
                        "mixed_stem_composition_semantic_role_changed"
                    )
                mcrs = _marked_content_references(semantic)
                if not mcrs:
                    raise MixedStemCompositionRejected(
                        "mixed_stem_composition_saved_mcr_missing"
                    )
                roles.append(role)
                for mcr in mcrs:
                    mcid = int(mcr.get(Name.MCID, -1))
                    page = pdf.pages[page_number - 1]
                    struct_parent = int(page.obj.get(Name.StructParents, -1))
                    page_array = parent_entries.get(struct_parent)
                    if (
                        mcid < 0
                        or not _same_object(mcr.get(Name.Pg), page.obj)
                        or not isinstance(page_array, Array)
                        or mcid >= len(page_array)
                        or not _same_object(page_array[mcid], semantic)
                    ):
                        raise MixedStemCompositionRejected(
                            "mixed_stem_composition_parent_tree_owner_changed"
                        )
                    mcids.append(mcid)
                    parent_material.append(
                        {
                            "page_number": page_number,
                            "struct_parent": struct_parent,
                            "mcid": mcid,
                            "region_id": region_id,
                        }
                    )
            region_attachments = _attachment_digests(container)
            if region_attachments != entry.attachment_sha256:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_attachment_changed"
                )
            attachment_sha256.extend(region_attachments)
            structure_material.append(
                {
                    "region_id": region_id,
                    "page_number": page_number,
                    "roles": roles,
                    "mcids": mcids,
                }
            )
        for child in nested:
            inspect_region(child, page_number, container)

    for page_number, page_section in enumerate(document_children[:-1], start=1):
        if (
            str(page_section.get(Name.S, "")) != "/Sect"
            or int(page_section.get(Name("/AeliraPageNumber"), -1)) != page_number
            or not _same_object(page_section.get(Name.P), document_owner)
            or not _same_object(
                page_section.get(Name.Pg), pdf.pages[page_number - 1].obj
            )
        ):
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_page_section_changed"
            )
        for container in _children(page_section):
            inspect_region(container, page_number, page_section)
    if tuple(flattened) != tuple(entry.region_id for entry in plan.entries):
        raise MixedStemCompositionRejected("mixed_stem_composition_saved_order_changed")
    return (
        canonical_sha256(structure_material),
        canonical_sha256(parent_material),
        tuple(attachment_sha256),
    )


def verify_saved_mixed_stem_composition(
    path: Path,
    plan: MixedStemCompositionPlanV1,
    applied: tuple[AppliedStemSpecialist, ...],
    baseline: _Baseline,
) -> MixedStemSavedCompositionEvidenceV1:
    """Reopen the serialized candidate and prove aggregate plus specialist state."""

    try:
        output_bytes = path.stat().st_size
        if output_bytes <= 0 or output_bytes > MAX_COMPOSITION_OUTPUT_BYTES:
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_output_byte_limit"
            )
        output_sha256 = _file_sha256(path)
        with fitz.open(path) as document:
            page_count = document.page_count
            render_sha256 = _render_sha256(document)
            text_sha256 = _visible_text_sha256(document)
            metadata_sha256 = _metadata_sha256(document)
        if (
            page_count != baseline.page_count
            or render_sha256 != baseline.render_sha256
            or text_sha256 != baseline.visible_text_sha256
            or metadata_sha256 != baseline.metadata_sha256
        ):
            raise MixedStemCompositionRejected(
                "mixed_stem_composition_document_preservation_failed"
            )
        for item in applied:
            if item.verify_saved(path) is not True:
                raise MixedStemCompositionRejected(
                    "mixed_stem_composition_specialist_saved_verification_failed"
                )
        with pikepdf.open(path) as pdf:
            structure_sha256, parent_tree_sha256, attachments = _verify_structure(
                pdf, plan
            )
        fields: dict[str, Any] = {
            "evidence_kind": "mixed_stem_saved_composition_evidence_v1",
            "output_sha256": output_sha256,
            "output_bytes": output_bytes,
            "page_count": page_count,
            "plan_sha256": plan.plan_sha256,
            "structure_sha256": structure_sha256,
            "parent_tree_sha256": parent_tree_sha256,
            "attachment_sha256": attachments,
            "long_description_sha256": plan.long_description_sha256,
            "render_sha256": render_sha256,
            "visible_text_sha256": text_sha256,
            "reverse_verified_bytes": output_bytes,
        }
        fields["evidence_sha256"] = canonical_sha256(fields)
        return MixedStemSavedCompositionEvidenceV1.model_validate(fields)
    except MixedStemCompositionRejected:
        raise
    except Exception as exc:
        raise MixedStemCompositionRejected(
            "mixed_stem_composition_saved_verification_failed"
        ) from exc


def compose_mixed_stem_pdf(
    source_path: str | Path,
    output_path: str | Path,
    routing: Any,
    contracts: tuple[BaseModel, ...] | list[BaseModel],
) -> MixedStemCompositionResultV1:
    """Write, reverse-verify, then atomically promote one complete composition."""

    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    if source == output:
        raise MixedStemCompositionRejected("mixed_stem_composition_output_is_source")
    baseline = _baseline(source)
    plan = build_mixed_stem_composition_plan(routing, contracts)
    if (
        plan.source_sha256 != baseline.source_sha256
        or plan.budget.source_bytes != baseline.source_bytes
        or plan.routing.graph.page_count != baseline.page_count
    ):
        raise MixedStemCompositionRejected("mixed_stem_composition_source_changed")
    contract_by_digest = {
        canonical_sha256(contract.model_dump(mode="json")): contract
        for contract in contracts
    }
    source_by_region = {
        region.region_id: region.source for region in plan.routing.graph.regions
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{output.name}.mixed-stem-", suffix=".pdf", dir=output.parent
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    try:
        shutil.copyfile(source, candidate)
        applied: list[AppliedStemSpecialist] = []
        with (
            fitz.open(candidate) as fitz_doc,
            pikepdf.open(candidate, allow_overwriting_input=True) as pdf,
        ):
            PDFStructureTree(pdf)
            for entry in plan.entries:
                if entry.region_kind == "native_text":
                    continue
                contract = contract_by_digest.get(entry.contract_sha256)
                if contract is None:
                    raise MixedStemCompositionRejected(
                        "mixed_stem_composition_contract_set"
                    )
                applied.append(
                    _apply_specialist(
                        pdf,
                        fitz_doc,
                        entry.region_id,
                        source_by_region[entry.region_id],
                        contract,
                    )
                )
            _build_structure_hierarchy(pdf, plan, tuple(applied))
            pdf.save(candidate)
        if _file_sha256(source) != baseline.source_sha256:
            raise MixedStemCompositionRejected("mixed_stem_composition_source_changed")
        evidence = verify_saved_mixed_stem_composition(
            candidate, plan, tuple(applied), baseline
        )
        result = build_mixed_stem_composition_result(plan, evidence)
        if _file_sha256(source) != baseline.source_sha256:
            raise MixedStemCompositionRejected("mixed_stem_composition_source_changed")
        os.replace(candidate, output)
        return result
    except Exception:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "AppliedStemSpecialist",
    "compose_mixed_stem_pdf",
    "verify_saved_mixed_stem_composition",
]
