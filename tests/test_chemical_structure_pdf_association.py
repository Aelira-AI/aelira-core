"""Exact PDF Figure association for verified chemical structures."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import fitz
import pikepdf
from pikepdf import Name

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from tests.test_chemical_structure_pdf_contract import _abbreviation, _methyl_graph
from tests.test_commutative_diagram_pdf_association import (
    _figure_elements,
    _make_reused_image_pdf,
    _region_pending as _diagram_region_pending,
    _write_scan,
)


def _recognition(graph, normalized_source_sha256):
    from src.education.chemical_structure_pdf import ChemicalStructureRecognitionV1
    from src.education.chemical_abbreviation import verify_chemical_abbreviations
    from src.education.visual_semantic_contract import canonical_sha256

    verified_graph, evidence = verify_chemical_abbreviations(graph, [_abbreviation()])
    return ChemicalStructureRecognitionV1(
        recognition_kind="chemical_structure_recognition_v1",
        graph=verified_graph,
        graph_sha256=verified_graph.canonical_sha256,
        abbreviations=evidence,
        abbreviation_evidence_sha256=canonical_sha256(
            [item.model_dump(mode="json") for item in evidence]
        ),
        abbreviation_policy_version="chemical-abbreviation-v1",
        normalized_source_sha256=normalized_source_sha256,
        provider="gemini",
        model="structure-test-v1",
        response_sha256="2" * 64,
        verifier_version="chemical-structure-v1",
        attempts=1,
    )


def _pending(document: fitz.Document):
    from src.education.chemical_structure_pdf import (
        ChemicalStructurePendingAssociationV1,
        chemical_structure_semantic_output,
    )

    occurrence = _displayed_image_occurrences(document[0], 1)[1]
    stream = document.extract_image(occurrence["image_xref"])["image"]
    locator = {
        **occurrence,
        "source_kind": "embedded_image_occurrence",
        "image_stream_sha256": hashlib.sha256(stream).hexdigest(),
    }
    recognition = _recognition(_methyl_graph(), hashlib.sha256(stream).hexdigest())
    return ChemicalStructurePendingAssociationV1(
        pending_kind="chemical_structure_pdf_association_v1",
        locator=locator,
        semantic_output=chemical_structure_semantic_output(recognition.graph),
        recognition=recognition,
    )


def _region_pending(document: fitz.Document):
    from src.education.chemical_structure_pdf import (
        ChemicalStructurePendingAssociationV1,
        chemical_structure_semantic_output,
    )

    diagram_pending = _diagram_region_pending(document)
    recognition = _recognition(
        _methyl_graph(), diagram_pending.recognition.normalized_source_sha256
    )
    return ChemicalStructurePendingAssociationV1(
        pending_kind="chemical_structure_pdf_association_v1",
        locator=diagram_pending.locator.model_dump(mode="json"),
        semantic_output=chemical_structure_semantic_output(recognition.graph),
        recognition=recognition,
    )


def test_embedded_structure_association_and_saved_contract(tmp_path):
    from src.education.chemical_structure_pdf import (
        build_chemical_structure_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_image_chemical_structure,
        verify_image_chemical_structure_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            result = associate_image_chemical_structure(pdf, fitz_doc, pending)
            assert result.success is True
            pdf.save(output)

    assert verify_image_chemical_structure_association(output, pending, result)
    contract = build_chemical_structure_pdf_contract(output, pending, result)
    assert contract.contract_kind == "chemical_structure"
    assert contract.locator.source_kind == "embedded_image_occurrence"
    assert result.attachment_sha256 == pending.semantic_output.graph_sha256
    assert contract.verification_evidence[1].evidence_kind == (
        "standalone_chemical_structure_saved_v1"
    )
    from src.education.remediation.base import (
        FixedIssue,
        IssueCategory,
        IssueSeverity,
    )
    from src.services.scan_fix_service import (
        artifact_review_blockers,
        build_scan_fix,
        chemical_structure_review_blockers,
    )

    fix = FixedIssue(
        issue_id="chemical-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Chemical structure needs verified semantics",
        fixed_content=pending.alt_text,
        fix_method="ai_vision",
        confidence=0.9,
        needs_review=False,
        provider_used=pending.recognition.provider,
        model_used=pending.recognition.model,
        source_kind="chemical_structure",
        verification_evidence=contract.verification_evidence[0],
        visual_semantic_contract=contract,
        page_number=1,
    )
    row = build_scan_fix("scan-1", fix)
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert "chemical_structure_not_human_approved" in (
        chemical_structure_review_blockers([row])
    )
    row.review_status = "approved"
    row.reviewed_by = "reviewer-1"
    row.reviewed_at = datetime.now(timezone.utc)
    row.approved_review_digest = row.review_digest
    assert chemical_structure_review_blockers([row]) == []
    assert artifact_review_blockers([row]) == []
    row.fixed_content = row.fixed_content + " changed"
    stale_blockers = artifact_review_blockers([row])
    assert "chemical_structure_provenance_invalid" in stale_blockers
    assert "fix_review_digest_invalid" in stale_blockers

    with pikepdf.open(output) as pdf:
        figure = _figure_elements(pdf.Root[Name.StructTreeRoot])[0]
        assert str(figure[Name.S]) == "/Figure"
        assert str(figure[Name.Alt]) == pending.alt_text
        metadata = figure[Name("/AeliraChemicalStructure")]
        assert str(metadata[Name("/AbbreviationPolicyVersion")]) == (
            "chemical-abbreviation-v1"
        )
        embedded = figure[Name("/AF")][0][Name("/EF")][Name.F]
        assert str(embedded[Name.Subtype]) == "/application#2Fjson"


def test_embedded_structure_reverse_verification_rejects_tampering(tmp_path):
    from src.education.remediation.content_tagger_v2 import (
        associate_image_chemical_structure,
        verify_image_chemical_structure_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    tampered = tmp_path / "tampered.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            result = associate_image_chemical_structure(pdf, fitz_doc, pending)
            pdf.save(output)

    with pikepdf.open(output) as pdf:
        figure = _figure_elements(pdf.Root[Name.StructTreeRoot])[0]
        figure[Name("/AeliraChemicalStructure")][Name("/GraphSHA256")] = "0" * 64
        pdf.save(tampered)

    assert not verify_image_chemical_structure_association(tampered, pending, result)


def test_raster_region_structure_preserves_rendering_and_ocr_ownership(tmp_path):
    from src.education.chemical_structure_pdf import (
        build_chemical_structure_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_scanned_region_chemical_structure,
        verify_scanned_region_chemical_structure_association,
    )
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "scan.pdf"
    output = tmp_path / "scan-output.pdf"
    _write_scan(source)
    with fitz.open(source) as fitz_doc:
        pending = _region_pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            PDFStructureTree(pdf)
            result = associate_scanned_region_chemical_structure(pdf, fitz_doc, pending)
            assert result.success is True
            pdf.save(output)

    assert verify_scanned_region_chemical_structure_association(output, pending, result)
    contract = build_chemical_structure_pdf_contract(output, pending, result)
    assert contract.locator.source_kind == "page_raster_region"
    assert contract.verification_evidence[1].evidence_kind == (
        "scanned_region_chemical_structure_saved_v1"
    )
