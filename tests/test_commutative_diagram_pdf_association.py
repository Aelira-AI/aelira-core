"""Exact PDF Figure association for verified commutative diagrams."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import fitz
import pikepdf
from PIL import Image, ImageDraw
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.image_checker import _displayed_image_occurrences


def _figure_elements(element):
    found = []
    if not hasattr(element, "keys"):
        return found
    if str(element.get(Name.S, "")) == "/Figure":
        found.append(element)
    kids = element.get(Name.K)
    children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    for child in children:
        if hasattr(child, "keys") and str(child.get(Name.Type, "")) != "/MCR":
            found.extend(_figure_elements(child))
    return found


def _triangle() -> dict[str, object]:
    return {
        "contract_kind": "commutative_diagram_v1",
        "nodes": [{"node_id": value} for value in "abc"],
        "edges": [
            {
                "edge_id": edge,
                "source_node_id": source,
                "target_node_id": target,
                "direction": "directed",
            }
            for edge, source, target in (
                ("f", "a", "b"),
                ("g", "b", "c"),
                ("h", "a", "c"),
            )
        ],
        "labels": [
            {
                "label_id": f"node-{value}",
                "text": value.upper(),
                "target_kind": "node",
                "target_id": value,
            }
            for value in "abc"
        ]
        + [
            {
                "label_id": f"edge-{value}",
                "text": value,
                "target_kind": "edge",
                "target_id": value,
            }
            for value in "fgh"
        ],
        "paths": [
            {
                "path_id": "direct",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["h"],
            },
            {
                "path_id": "composed",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["f", "g"],
            },
        ],
        "relations": [
            {
                "relation_id": "triangle-commutes",
                "path_ids": ["direct", "composed"],
            }
        ],
        "layout": [],
        "unresolved_crossings": [],
    }


def _make_reused_image_pdf(path: Path) -> None:
    pdf = pikepdf.new()
    image = pdf.make_stream(b"\x00")
    image[Name.Type] = Name.XObject
    image[Name.Subtype] = Name.Image
    image[Name.Width] = 1
    image[Name.Height] = 1
    image[Name.ColorSpace] = Name.DeviceGray
    image[Name.BitsPerComponent] = 8
    image = pdf.make_indirect(image)
    page = pikepdf.Page(
        Dictionary(
            {
                "/Type": Name.Page,
                "/MediaBox": Array([0, 0, 300, 300]),
                "/Contents": pdf.make_stream(
                    b"q 40 0 0 20 10 20 cm /Im0 Do Q\n"
                    b"q 60 0 0 30 120 140 cm /Im0 Do Q"
                ),
                "/Resources": Dictionary({"/XObject": Dictionary({"/Im0": image})}),
            }
        )
    )
    pdf.pages.append(page)
    pdf.save(path)


def _write_scan(path: Path) -> None:
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "Diagram", fill="black")
    draw.line((20, 55, 100, 55), fill="black", width=3)
    payload = BytesIO()
    image.save(payload, format="PNG")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=payload.getvalue())
    document.save(path)
    document.close()


def _pending(document: fitz.Document):
    from src.education.commutative_diagram import verify_commutative_diagram
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramPendingAssociationV1,
        CommutativeDiagramRecognitionV1,
        commutative_diagram_semantic_output,
    )

    occurrence = _displayed_image_occurrences(document[0], 1)[1]
    stream = document.extract_image(occurrence["image_xref"])["image"]
    locator = {
        **occurrence,
        "source_kind": "embedded_image_occurrence",
        "image_stream_sha256": hashlib.sha256(stream).hexdigest(),
    }
    graph = verify_commutative_diagram(_triangle())
    recognition = CommutativeDiagramRecognitionV1(
        recognition_kind="commutative_diagram_recognition_v1",
        graph=graph,
        graph_sha256=graph.canonical_sha256,
        normalized_source_sha256=hashlib.sha256(stream).hexdigest(),
        provider="gemini",
        model="diagram-test-v1",
        response_sha256="2" * 64,
        verifier_version="commutative-diagram-v1",
        attempts=1,
    )
    return CommutativeDiagramPendingAssociationV1(
        pending_kind="commutative_diagram_pdf_association_v1",
        locator=locator,
        semantic_output=commutative_diagram_semantic_output(graph),
        recognition=recognition,
    )


def _region_pending(document: fitz.Document):
    from src.education.commutative_diagram import verify_commutative_diagram
    from src.education.commutative_diagram_pdf import (
        CommutativeDiagramPendingAssociationV1,
        CommutativeDiagramRecognitionV1,
        commutative_diagram_semantic_output,
    )
    from src.education.equation_region_contract import PageRasterRegionLocator
    from src.education.remediation.equation_image_source import EquationRegionSource

    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    info = document[0].get_image_info(xrefs=True)[0]
    source = document.extract_image(occurrence["image_xref"])["image"]
    pixel_bbox = (16, 16, 120, 80)
    with Image.open(BytesIO(source)) as image:
        image.load()
        crop = image.crop(pixel_bbox)
        header = f"{crop.mode}|{crop.width}|{crop.height}|".encode("ascii")
        crop_sha256 = hashlib.sha256(header + crop.tobytes()).hexdigest()
        width, height = image.size
        crop.close()
    locator_data = {
        "source_kind": "page_raster_region",
        "page_number": 1,
        "parent_occurrence_id": occurrence["occurrence_id"],
        "image_xref": occurrence["image_xref"],
        "image_index": occurrence["image_index"],
        "occurrence_ordinal": occurrence["occurrence_ordinal"],
        "parent_bbox": list(occurrence["bbox"]),
        "pixel_bbox": list(pixel_bbox),
        "pdf_bbox": [float(value) for value in pixel_bbox],
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "crop_pixel_sha256": crop_sha256,
        "source_width": width,
        "source_height": height,
        "detector_version": "raster-equation-region-v1",
        "threshold_version": "grayscale-lt245-v1",
        "ocr_engine_version": "5.5.1",
        "ocr_tessdata_sha256": (
            "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
        ),
        "ocr_language": "eng",
        "ocr_config": "--oem 3 --psm 6",
        "transform": list(info["transform"]),
    }
    encoded = json.dumps(
        locator_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    locator_data["region_id"] = (
        "eqregion-v1-" + hashlib.sha256(encoded).hexdigest()[:24]
    )
    locator = PageRasterRegionLocator.model_validate(locator_data)
    normalized = (
        EquationRegionSource().extract(document, locator_data).normalized_sha256
    )
    graph = verify_commutative_diagram(_triangle())
    recognition = CommutativeDiagramRecognitionV1(
        recognition_kind="commutative_diagram_recognition_v1",
        graph=graph,
        graph_sha256=graph.canonical_sha256,
        normalized_source_sha256=normalized,
        provider="gemini",
        model="diagram-test-v1",
        response_sha256="2" * 64,
        verifier_version="commutative-diagram-v1",
        attempts=1,
    )
    return CommutativeDiagramPendingAssociationV1(
        pending_kind="commutative_diagram_pdf_association_v1",
        locator=locator.model_dump(mode="json"),
        semantic_output=commutative_diagram_semantic_output(graph),
        recognition=recognition,
    )


def test_embedded_diagram_association_uses_exact_figure_and_json_supplement(tmp_path):
    from src.education.commutative_diagram_pdf import (
        build_commutative_diagram_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_image_commutative_diagram,
        verify_image_commutative_diagram_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            result = associate_image_commutative_diagram(pdf, fitz_doc, pending)
            assert result.success is True
            pdf.save(output)

    assert verify_image_commutative_diagram_association(output, pending, result)
    contract = build_commutative_diagram_pdf_contract(output, pending, result)
    assert contract.contract_kind == "commutative_diagram"
    assert contract.locator.source_kind == "embedded_image_occurrence"
    assert (
        contract.verification_evidence[1].saved_file_sha256
        == hashlib.sha256(output.read_bytes()).hexdigest()
    )
    from src.education.remediation.base import (
        FixedIssue,
        IssueCategory,
        IssueSeverity,
    )
    from src.services.scan_fix_service import (
        artifact_review_blockers,
        build_scan_fix,
        commutative_diagram_review_blockers,
    )

    fix = FixedIssue(
        issue_id="diagram-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Diagram needs an accessible representation",
        fixed_content=pending.alt_text,
        fix_method="ai_vision",
        confidence=0.9,
        needs_review=False,
        provider_used=pending.recognition.provider,
        model_used=pending.recognition.model,
        source_kind="commutative_diagram",
        verification_evidence=contract.verification_evidence[0],
        visual_semantic_contract=contract,
        page_number=1,
    )
    row = build_scan_fix("scan-1", fix)
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert "commutative_diagram_not_human_approved" in (
        commutative_diagram_review_blockers([row])
    )
    row.review_status = "approved"
    row.reviewed_by = "reviewer-1"
    row.reviewed_at = datetime.now(timezone.utc)
    row.approved_review_digest = row.review_digest
    assert commutative_diagram_review_blockers([row]) == []
    assert artifact_review_blockers([row]) == []

    row.fixed_content = row.fixed_content + " changed"
    stale_blockers = artifact_review_blockers([row])
    assert "commutative_diagram_provenance_invalid" in stale_blockers
    assert "fix_review_digest_invalid" in stale_blockers
    with pikepdf.open(output) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        tags = [str(op.operands[0]) for op in ops if str(op.operator) == "BDC"]
        assert tags == ["/Figure"]

        root = pdf.Root[Name.StructTreeRoot]
        figure = _figure_elements(root)[0]
        assert str(figure[Name.S]) == "/Figure"
        assert str(figure[Name.Alt]) == pending.semantic_output.description.summary
        embedded = figure[Name("/AF")][0][Name("/EF")][Name.F]
        assert str(embedded[Name.Subtype]) == "/application#2Fjson"


def test_embedded_diagram_reverse_verification_rejects_attachment_tampering(tmp_path):
    from src.education.remediation.content_tagger_v2 import (
        associate_image_commutative_diagram,
        verify_image_commutative_diagram_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    tampered = tmp_path / "tampered.pdf"
    _make_reused_image_pdf(source)
    with fitz.open(source) as fitz_doc:
        pending = _pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            result = associate_image_commutative_diagram(pdf, fitz_doc, pending)
            pdf.save(output)

    with pikepdf.open(output) as pdf:
        figure = _figure_elements(pdf.Root[Name.StructTreeRoot])[0]
        embedded = figure[Name("/AF")][0][Name("/EF")][Name.F]
        embedded.write(b'{"tampered":true}')
        pdf.save(tampered)

    assert not verify_image_commutative_diagram_association(tampered, pending, result)


def test_raster_region_diagram_uses_one_clipped_figure_and_preserves_rendering(
    tmp_path,
):
    from src.education.commutative_diagram_pdf import (
        build_commutative_diagram_pdf_contract,
    )
    from src.education.remediation.content_tagger_v2 import (
        associate_scanned_region_commutative_diagram,
        verify_scanned_region_commutative_diagram_association,
    )
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "scan.pdf"
    output = tmp_path / "scan-output.pdf"
    _write_scan(source)
    with fitz.open(source) as fitz_doc:
        pending = _region_pending(fitz_doc)
        with pikepdf.open(source) as pdf:
            PDFStructureTree(pdf)
            result = associate_scanned_region_commutative_diagram(
                pdf, fitz_doc, pending
            )
            assert result.success is True
            pdf.save(output)

    assert verify_scanned_region_commutative_diagram_association(
        output, pending, result
    )
    contract = build_commutative_diagram_pdf_contract(output, pending, result)
    assert contract.locator.source_kind == "page_raster_region"
    assert contract.verification_evidence[1].evidence_kind == (
        "scanned_region_diagram_saved_v1"
    )
    with pikepdf.open(output) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        names = [str(op.operator) for op in ops]
        assert names.count("Do") == 2
        assert [str(op.operands[0]) for op in ops if str(op.operator) == "BDC"] == [
            "/Figure"
        ]
