"""Exact PDF Formula association for verified chemical notation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import fitz
import pikepdf
from pikepdf import Array, Name

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from tests.test_image_equation_content_association import _make_reused_image_pdf
from tests.test_scanned_equation_region_association import (
    _pending as _equation_region_pending,
)
from tests.test_scanned_equation_region_association import _write_scan


def _recognition(source: str, normalized_source_sha256: str):
    from src.education.chemical_formula import verify_chemical_notation
    from src.education.chemical_formula_pdf import ChemicalFormulaRecognitionV1

    return ChemicalFormulaRecognitionV1(
        recognition_kind="chemical_formula_recognition_v1",
        verified_notation=verify_chemical_notation(source),
        normalized_source_sha256=normalized_source_sha256,
        provider="gemini",
        model="formula-test-v1",
        response_sha256="2" * 64,
        verifier_version="chemical-formula-pdf-v1",
        attempts=1,
    )


def _pending(document: fitz.Document, source: str = "H2O"):
    from src.education.chemical_formula_pdf import (
        ChemicalFormulaPendingAssociationV1,
        chemical_formula_semantic_output,
    )

    occurrence = _displayed_image_occurrences(document[0], 1)[1]
    stream = document.extract_image(occurrence["image_xref"])["image"]
    stream_sha256 = hashlib.sha256(stream).hexdigest()
    return ChemicalFormulaPendingAssociationV1(
        pending_kind="chemical_formula_pdf_association_v1",
        locator={
            **occurrence,
            "source_kind": "embedded_image_occurrence",
            "image_stream_sha256": stream_sha256,
        },
        semantic_output=chemical_formula_semantic_output(source),
        recognition=_recognition(source, stream_sha256),
    )


def _region_pending(document: fitz.Document, source: str = "NaCl(aq)"):
    from src.education.chemical_formula_pdf import (
        ChemicalFormulaPendingAssociationV1,
        chemical_formula_semantic_output,
    )

    equation_pending = _equation_region_pending(document)
    return ChemicalFormulaPendingAssociationV1(
        pending_kind="chemical_formula_pdf_association_v1",
        locator=equation_pending.locator.model_dump(mode="json"),
        semantic_output=chemical_formula_semantic_output(source),
        recognition=_recognition(source, equation_pending.normalized_crop_sha256),
    )


def _formula_elements(pdf: pikepdf.Pdf):
    found = []

    def collect(element):
        if not hasattr(element, "keys"):
            return
        if str(element.get(Name.S, "")) == "/Formula":
            found.append(element)
        kids = element.get(Name.K)
        children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
        for child in children:
            if hasattr(child, "keys") and str(child.get(Name.Type, "")) != "/MCR":
                collect(child)

    collect(pdf.Root[Name.StructTreeRoot])
    return found


def test_embedded_formula_association_and_saved_contract(tmp_path):
    from src.education.chemical_formula_pdf import build_chemical_formula_pdf_contract
    from src.education.remediation.content_tagger_v2 import (
        associate_image_chemical_formula,
        verify_image_chemical_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            result = associate_image_chemical_formula(pdf, fitz_doc, pending)
            assert result.success is True
            pdf.save(output)

    assert verify_image_chemical_formula_association(output, pending, result)
    contract = build_chemical_formula_pdf_contract(output, pending, result)
    assert contract.contract_kind == "chemical_formula"
    assert contract.locator.source_kind == "embedded_image_occurrence"
    assert contract.verification_evidence[1].evidence_kind == (
        "standalone_chemical_formula_saved_v1"
    )
    from src.education.remediation.base import (
        FixedIssue,
        IssueCategory,
        IssueSeverity,
    )
    from src.services.scan_fix_service import (
        artifact_review_blockers,
        build_scan_fix,
        chemical_formula_review_blockers,
    )

    fix = FixedIssue(
        issue_id="chemical-formula-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Chemical formula needs verified semantics",
        fixed_content=pending.alt_text,
        fix_method="ai_vision",
        confidence=0.9,
        needs_review=False,
        provider_used=pending.recognition.provider,
        model_used=pending.recognition.model,
        source_kind="chemical_formula",
        verification_evidence=contract.verification_evidence[0],
        visual_semantic_contract=contract,
        page_number=1,
    )
    row = build_scan_fix("scan-1", fix)
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert "chemical_formula_not_human_approved" in (
        chemical_formula_review_blockers([row])
    )
    row.review_status = "approved"
    row.reviewed_by = "reviewer-1"
    row.reviewed_at = datetime.now(timezone.utc)
    row.approved_review_digest = row.review_digest
    assert chemical_formula_review_blockers([row]) == []
    assert artifact_review_blockers([row]) == []
    row.fixed_content = row.fixed_content + " changed"
    stale_blockers = artifact_review_blockers([row])
    assert "chemical_formula_evidence_invalid" in stale_blockers
    assert "fix_review_digest_invalid" in stale_blockers
    with pikepdf.open(output) as pdf:
        formulas = _formula_elements(pdf)
        assert len(formulas) == 1
        formula = formulas[0]
        assert str(formula[Name.Alt]) == pending.alt_text
        assert Name("/ActualText") not in formula
        metadata = formula[Name("/AeliraChemicalFormula")]
        assert str(metadata[Name("/NotationKind")]) == "chemical_formula_v1"
        embedded = formula[Name("/AF")][0][Name("/EF")][Name.F]
        assert embedded.read_bytes() == pending.mathml_string.encode("utf-8")


def test_embedded_formula_reverse_verification_rejects_metadata_tamper(tmp_path):
    from src.education.remediation.content_tagger_v2 import (
        associate_image_chemical_formula,
        verify_image_chemical_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    tampered = tmp_path / "tampered.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc, "2H2(g) + O2(g) -> 2H2O(l)")
        with pikepdf.open(source) as pdf:
            result = associate_image_chemical_formula(pdf, fitz_doc, pending)
            pdf.save(output)

    with pikepdf.open(output) as pdf:
        formula = _formula_elements(pdf)[0]
        formula[Name("/AeliraChemicalFormula")][Name("/SourceSHA256")] = "0" * 64
        pdf.save(tampered)
    assert not verify_image_chemical_formula_association(tampered, pending, result)


def test_scanned_region_formula_association_and_saved_contract(tmp_path):
    from src.education.chemical_formula_pdf import build_chemical_formula_pdf_contract
    from src.education.remediation.content_tagger_v2 import (
        associate_scanned_region_chemical_formula,
        verify_scanned_region_chemical_formula_association,
    )
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "scan.pdf"
    output = tmp_path / "scan-output.pdf"
    _write_scan(source)
    with fitz.open(source) as fitz_doc:
        pending = _region_pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            PDFStructureTree(pdf)
            result = associate_scanned_region_chemical_formula(pdf, fitz_doc, pending)
            assert result.success is True
            pdf.save(output)

    assert verify_scanned_region_chemical_formula_association(output, pending, result)
    contract = build_chemical_formula_pdf_contract(output, pending, result)
    assert contract.locator.source_kind == "page_raster_region"
    assert contract.verification_evidence[1].evidence_kind == (
        "scanned_region_chemical_formula_saved_v1"
    )
    assert contract.semantic_output.verified_notation.source_notation == "NaCl(aq)"


def test_scanned_region_formula_reverse_verification_rejects_mathml_tamper(tmp_path):
    from src.education.remediation.content_tagger_v2 import (
        associate_scanned_region_chemical_formula,
        verify_scanned_region_chemical_formula_association,
    )
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "scan.pdf"
    output = tmp_path / "scan-output.pdf"
    tampered = tmp_path / "scan-tampered.pdf"
    _write_scan(source)
    with fitz.open(source) as fitz_doc:
        pending = _region_pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            PDFStructureTree(pdf)
            result = associate_scanned_region_chemical_formula(pdf, fitz_doc, pending)
            pdf.save(output)

    with pikepdf.open(output) as pdf:
        formula = _formula_elements(pdf)[0]
        formula[Name("/AF")][0][Name("/EF")][Name.F].write(b"<math/>")
        pdf.save(tampered)
    assert not verify_scanned_region_chemical_formula_association(
        tampered, pending, result
    )
