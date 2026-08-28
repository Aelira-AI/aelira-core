"""Exact clipped Formula association for one region inside a page raster."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from io import BytesIO

import fitz
import pikepdf
import pytest
from PIL import Image, ImageDraw
from pikepdf import Array, Name, Operator

from src.education.equation_region_contract import PageRasterRegionLocator
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.equation_region_detector import (
    RasterEquationRegionDetector,
)
from src.education.remediation.content_tagger_v2 import (
    ContentTaggerV2,
    ScannedRegionAssociationError,
    associate_scanned_region_formula,
    preflight_scanned_region_render_budget,
    verify_scanned_region_formula_association,
)
from src.education.remediation.equation_image_source import (
    EquationRegionSource,
    WorkingEquationRegionOccurrence,
)
from src.education.remediation.equation_verifier import VerifierConfig
from src.education.remediation.math_fixer import (
    MathFixResult,
    MathVerificationEvidence,
    PendingScannedRegionAssociation,
)
from src.education.remediation.pdf_structure import PDFStructureTree

MATHML = "<math><mrow><mi>x</mi><mo>+</mo><mi>y</mi><mo>=</mo><mn>2</mn></mrow></math>"


def _write_scan(path) -> None:
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "Equation 1", fill="black")
    draw.text((20, 52), "x + y = 2", fill="black")
    payload = BytesIO()
    image.save(payload, format="PNG")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=payload.getvalue())
    document.save(path)
    document.close()


def _pending(
    document: fitz.Document,
    pixel_bbox: tuple[int, int, int, int] = (16, 16, 100, 76),
) -> PendingScannedRegionAssociation:
    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    info = document[0].get_image_info(xrefs=True)[0]
    source = document.extract_image(occurrence["image_xref"])["image"]
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
        locator_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    locator_data["region_id"] = (
        "eqregion-v1-" + hashlib.sha256(encoded).hexdigest()[:24]
    )
    locator = PageRasterRegionLocator.model_validate(locator_data)
    validated = EquationRegionSource().extract(document, locator_data)
    verifier_config = VerifierConfig()
    evidence = MathVerificationEvidence(
        passed=True,
        source_sha256=validated.normalized_sha256,
        rendered_sha256="b" * 64,
        mathml_sha256=hashlib.sha256(MATHML.encode("utf-8")).hexdigest(),
        renderer_version=verifier_config.renderer_version,
        comparator_version=verifier_config.comparator_version,
        font_sha256=verifier_config.font_sha256,
        threshold_version=verifier_config.threshold_version,
        ink_iou=1.0,
        pixel_similarity=1.0,
        required_ink_iou=verifier_config.required_ink_iou,
        required_pixel_similarity=verifier_config.required_pixel_similarity,
    )
    return PendingScannedRegionAssociation(
        locator=locator,
        working_occurrence=WorkingEquationRegionOccurrence(
            page_number=1,
            image_xref=occurrence["image_xref"],
            image_index=occurrence["image_index"],
            occurrence_ordinal=occurrence["occurrence_ordinal"],
            bbox=tuple(occurrence["bbox"]),
            occurrence_id=occurrence["occurrence_id"],
            transform=tuple(info["transform"]),
        ),
        normalized_crop_sha256=validated.normalized_sha256,
        alt_text="x plus y equals 2",
        mathml_string=MATHML,
        provider_used="test-provider",
        model_used="test-model",
        verification_evidence=evidence,
    )


def test_region_association_marks_only_clipped_replay_and_preserves_pixels(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        association = associate_scanned_region_formula(pdf, fitz_document, pending)
        assert association.formula_bbox == pytest.approx((16.0, 224.0, 100.0, 284.0))
        pdf.save(output)
    fitz_document.close()

    assert verify_scanned_region_formula_association(output, pending, association)
    with pikepdf.open(output) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        names = [str(op.operator) for op in ops]
        assert names.count("Do") == 2
        assert names.count("BDC") == 1
        assert names.count("BMC") == 1
        assert names.count("W") == 1
        formula_index = names.index("BDC")
        assert names[formula_index : formula_index + 9] == [
            "BDC",
            "q",
            "cm",
            "re",
            "W",
            "n",
            "Do",
            "Q",
            "EMC",
        ]


def _formula_element(pdf):
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
    assert len(found) == 1
    return found[0]


def _formula_sequence(ops):
    for index in range(0, len(ops) - 8):
        sequence = ops[index : index + 9]
        if [str(item.operator) for item in sequence] == [
            "BDC",
            "q",
            "cm",
            "re",
            "W",
            "n",
            "Do",
            "Q",
            "EMC",
        ] and str(sequence[0].operands[0]) == "/Formula":
            return index, sequence
    raise AssertionError("Formula association sequence not found")


@pytest.mark.parametrize(
    "mutation",
    [
        "clip",
        "transform",
        "formula_bbox",
        "source_identity",
        "mcid",
        "parent_tree",
        "mathml",
        "output_pixels",
        "reading_order",
    ],
)
def test_saved_region_association_sabotage_fails_closed(tmp_path, mutation):
    from src.education.remediation.content_tagger_v2 import _number_tree_entries

    source = tmp_path / "source.pdf"
    associated = tmp_path / "associated.pdf"
    tampered = tmp_path / f"tampered-{mutation}.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        if mutation == "reading_order":
            later_page = pdf.add_blank_page(page_size=(400, 300))
            struct_root = pdf.Root[Name.StructTreeRoot]
            root_kids = struct_root[Name.K]
            document_element = (
                root_kids[0]
                if isinstance(root_kids, pikepdf.Array) and root_kids
                else struct_root
            )
            later_paragraph = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/P"),
                        "/P": document_element,
                        "/Pg": later_page.obj,
                        "/K": pikepdf.Array([]),
                    }
                )
            )
            current_kids = document_element.get(Name.K)
            current = (
                list(current_kids)
                if isinstance(current_kids, Array)
                else ([current_kids] if current_kids else [])
            )
            document_element[Name.K] = Array([*current, later_paragraph])
        association = associate_scanned_region_formula(pdf, fitz_document, pending)
        pdf.save(associated)
    fitz_document.close()
    assert verify_scanned_region_formula_association(associated, pending, association)

    if mutation == "source_identity":
        with fitz.open(associated) as document:
            xref = int(document[0].get_image_info(xrefs=True)[0]["xref"])
            payload = document.extract_image(xref)["image"]
            with Image.open(BytesIO(payload)) as image:
                image.load()
                changed = image.convert("RGB")
                changed.putpixel((0, 0), (254, 254, 254))
                replacement = BytesIO()
                changed.save(replacement, format="PNG")
                changed.close()
            document[0].replace_image(xref, stream=replacement.getvalue())
            document.save(tampered)
    else:
        with pikepdf.open(associated) as pdf:
            page = pdf.pages[0]
            ops = list(pikepdf.parse_content_stream(page))
            sequence_index, sequence = _formula_sequence(ops)
            formula = _formula_element(pdf)

            if mutation == "clip":
                values = [float(value) for value in sequence[3].operands]
                values[0] += 0.01
                ops[sequence_index + 3] = pikepdf.ContentStreamInstruction(
                    values, Operator("re")
                )
            elif mutation == "transform":
                values = [float(value) for value in sequence[2].operands]
                values[4] += 1.0
                ops[sequence_index + 2] = pikepdf.ContentStreamInstruction(
                    values, Operator("cm")
                )
            elif mutation == "formula_bbox":
                bbox = formula[Name.A][Name("/BBox")]
                bbox[0] = float(bbox[0]) + 1.0
            elif mutation == "mcid":
                properties = sequence[0].operands[1]
                properties[Name.MCID] = association.mcid + 1
            elif mutation == "parent_tree":
                _, entries = _number_tree_entries(pdf.Root[Name.StructTreeRoot])
                page_array = next(
                    value for key, value in entries if key == association.struct_parent
                )
                page_array[association.mcid] = None
            elif mutation == "mathml":
                embedded = formula[Name("/AF")][0][Name("/EF")][Name.F]
                embedded.write(b"<math><mn>999</mn></math>")
            elif mutation == "output_pixels":
                ops.extend(
                    [
                        pikepdf.ContentStreamInstruction([], Operator("q")),
                        pikepdf.ContentStreamInstruction([1, 0, 0], Operator("rg")),
                        pikepdf.ContentStreamInstruction(
                            [350, 250, 20, 20], Operator("re")
                        ),
                        pikepdf.ContentStreamInstruction([], Operator("f")),
                        pikepdf.ContentStreamInstruction([], Operator("Q")),
                    ]
                )
            elif mutation == "reading_order":
                parent = formula[Name.P]
                children = list(parent[Name.K])
                formula_index = next(
                    index
                    for index, child in enumerate(children)
                    if hasattr(child, "objgen")
                    and tuple(child.objgen) == tuple(formula.objgen)
                )
                later_index = next(
                    index
                    for index, child in enumerate(children)
                    if hasattr(child, "keys")
                    and child.get(Name.Pg) is not None
                    and tuple(child[Name.Pg].objgen) == tuple(pdf.pages[1].obj.objgen)
                )
                children[formula_index], children[later_index] = (
                    children[later_index],
                    children[formula_index],
                )
                parent[Name.K] = Array(children)

            if mutation in {"clip", "transform", "mcid", "output_pixels"}:
                page.obj[Name.Contents] = pdf.make_stream(
                    pikepdf.unparse_content_stream(ops)
                )
            pdf.save(tampered)

    assert not verify_scanned_region_formula_association(tampered, pending, association)


def test_region_association_rejects_soft_mask_before_mutation(tmp_path):
    source = tmp_path / "source.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        page = pdf.pages[0]
        xobjects = page.obj[Name.Resources][Name.XObject]
        image = next(iter(xobjects.values()))
        image[Name("/SMask")] = image
        before = pikepdf.unparse_content_stream(
            list(pikepdf.parse_content_stream(page))
        )
        with pytest.raises(
            ScannedRegionAssociationError, match="region_masked_image_unsupported"
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
        assert (
            pikepdf.unparse_content_stream(list(pikepdf.parse_content_stream(page)))
            == before
        )
    fitz_document.close()


def test_region_render_budget_preflight_is_unique_and_aggregate_bounded():
    document = fitz.open()
    document.new_page(width=400, height=300)
    assert preflight_scanned_region_render_budget(document, [1, 1]) == (1,)
    document.close()

    oversized = fitz.open()
    for _ in range(8):
        oversized.new_page(width=700, height=900)
    with pytest.raises(
        ScannedRegionAssociationError,
        match="region_transaction_render_budget_exceeded",
    ):
        preflight_scanned_region_render_budget(oversized, range(1, 9))
    oversized.close()


def test_region_structure_sequence_precedes_existing_later_page_content(tmp_path):
    source = tmp_path / "source.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        later_page = pdf.add_blank_page(page_size=(400, 300))
        struct_root = pdf.Root[Name.StructTreeRoot]
        root_kids = struct_root[Name.K]
        document_element = (
            root_kids[0]
            if isinstance(root_kids, pikepdf.Array) and root_kids
            else struct_root
        )
        later_paragraph = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name("/P"),
                    "/P": document_element,
                    "/Pg": later_page.obj,
                    "/K": pikepdf.Array([]),
                }
            )
        )
        document_element[Name.K] = pikepdf.Array([later_paragraph])

        associate_scanned_region_formula(pdf, fitz_document, pending)

        children = list(document_element[Name.K])
        assert str(children[0][Name.S]) == "/Formula"
        assert tuple(children[1].objgen) == tuple(later_paragraph.objgen)
    fitz_document.close()


def test_region_association_rejects_structure_container_spanning_target_page(
    tmp_path,
):
    source = tmp_path / "source.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        pdf.add_blank_page(page_size=(400, 300))
        page_three = pdf.add_blank_page(page_size=(400, 300))
        struct_root = pdf.Root[Name.StructTreeRoot]
        root_kids = struct_root[Name.K]
        document_element = (
            root_kids[0]
            if isinstance(root_kids, pikepdf.Array) and root_kids
            else struct_root
        )
        section = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name("/Sect"),
                    "/P": document_element,
                    "/K": pikepdf.Array([]),
                }
            )
        )
        first_paragraph = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name("/P"),
                    "/P": section,
                    "/Pg": pdf.pages[0].obj,
                    "/K": pikepdf.Array([]),
                }
            )
        )
        last_paragraph = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name("/P"),
                    "/P": section,
                    "/Pg": page_three.obj,
                    "/K": pikepdf.Array([]),
                }
            )
        )
        section[Name.K] = Array([first_paragraph, last_paragraph])
        document_element[Name.K] = Array([section])

        with pytest.raises(
            ScannedRegionAssociationError,
            match="region_structure_order_ambiguous",
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
    fitz_document.close()


def test_region_association_rejects_page_annotations_before_mutation(tmp_path):
    source = tmp_path / "source.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        page = pdf.pages[0]
        page.obj[Name("/Annots")] = pikepdf.Array([])
        before = pikepdf.unparse_content_stream(
            list(pikepdf.parse_content_stream(page))
        )
        with pytest.raises(
            ScannedRegionAssociationError,
            match="region_page_annotations_unsupported",
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
        assert (
            pikepdf.unparse_content_stream(list(pikepdf.parse_content_stream(page)))
            == before
        )
    fitz_document.close()


@pytest.mark.parametrize(
    "evidence_mutation",
    [
        {"passed": False},
        {"source_sha256": "0" * 64},
    ],
)
def test_region_association_rebinds_live_crop_to_passed_evidence(
    tmp_path, evidence_mutation
):
    source = tmp_path / "source.pdf"
    _write_scan(source)
    fitz_document = fitz.open(source)
    pending = _pending(fitz_document)
    forged = dataclasses.replace(
        pending,
        verification_evidence=dataclasses.replace(
            pending.verification_evidence,
            **evidence_mutation,
        ),
    )
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[forged]
        ).tag_all_pages()
        with pytest.raises(ScannedRegionAssociationError, match="region_crop_changed"):
            associate_scanned_region_formula(pdf, fitz_document, forged)
    fitz_document.close()


def _configured_region_writer(
    source, output, *, pending=None, create_source: bool = True
):
    from src.education.remediation.base import (
        IssueCategory,
        IssueSeverity,
        ManualIssue,
        RemediationConfig,
        RemediationIssue,
        RemediationResult,
    )
    from src.education.remediation.pdf_remediator import PdfRemediator

    if create_source:
        _write_scan(source)
    fitz_document = fitz.open(source)
    pending = pending or _pending(fitz_document)
    issue = RemediationIssue(
        id="region-1",
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.HIGH,
        description="Scanned equation region is inaccessible",
        metadata={"page_number": 1},
    )
    staged = MathFixResult(
        success=False,
        error="scanned_equation_region_association_pending",
        aria_label=pending.alt_text,
        page_number=1,
        has_mathml=True,
        source_kind="image_equation",
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used=pending.provider_used,
        model_used=pending.model_used,
        verification_evidence=pending.verification_evidence,
        pending_association=pending,
    )
    remediator = PdfRemediator(
        str(source),
        [issue],
        RemediationConfig(create_backup=False, verify_fixes=False),
    )
    remediator._pdf = fitz_document
    remediator._pikepdf_doc = pikepdf.open(source)
    remediator._struct_tree = PDFStructureTree(remediator._pikepdf_doc)
    remediator._structure_modified = True
    remediator._pending_image_equations = [(issue, staged)]
    remediator.result = RemediationResult(
        original_file=str(source),
        output_file=str(output),
        document_type="pdf",
        total_issues=1,
        manual_count=1,
        manual_issues=[
            ManualIssue(
                issue_id=issue.id,
                category=issue.category,
                severity=issue.severity,
                description=issue.description,
                reason="pending",
                recommendation="pending",
            )
        ],
    )
    return remediator, fitz_document, pending


def test_pdf_writer_remaps_persists_and_postverifies_region(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    remediator, fitz_document, pending = _configured_region_writer(source, output)

    remediator._write_pdf_output(fitz_document, str(output))

    assert len(remediator._verified_image_equations) == 1
    _, _, _, contract = remediator._verified_image_equations[0]
    assert contract.contract_kind == "printed_equation"
    assert contract.locator.source_kind == "page_raster_region"
    assert contract.locator.model_dump(mode="json") == pending.locator.model_dump(
        mode="json"
    )
    with fitz.open(output) as saved_pdf:
        saved_occurrence = _displayed_image_occurrences(saved_pdf[0], 1)[0]
    saved = next(
        item
        for item in contract.verification_evidence
        if item.evidence_kind == "scanned_region_formula_saved_v1"
    )
    assert saved.saved_file_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert saved.image_xref == saved_occurrence["image_xref"]
    assert saved.image_xref != contract.locator.image_xref
    assert saved.image_stream_sha256 == contract.locator.source_sha256
    assert (
        saved.alt_text_sha256
        == hashlib.sha256(pending.alt_text.encode("utf-8")).hexdigest()
    )
    remediator._reconcile_verified_image_equations()
    assert remediator.result.manual_count == 0
    assert remediator.result.fixed_count == 1
    fixed = remediator.result.fixed_issues[0]
    assert fixed.source_kind == "image_equation"
    assert fixed.source_locator is not None
    assert fixed.source_locator.region_id == pending.locator.region_id
    from pydantic import ValidationError

    from src.education.remediation.base import FixedIssue
    from src.jobs.remediation_subprocess import (
        RemediationSubprocessError,
        SubprocessRemediationResult,
    )

    valid_record = fixed.model_dump(mode="json")
    locator_identity = fixed.source_locator.model_dump(
        mode="json", exclude={"region_id"}
    )
    locator_identity["detector_version"] = "different-detector-v1"
    mismatched_locator = {
        **locator_identity,
        "region_id": "eqregion-v1-"
        + hashlib.sha256(
            json.dumps(
                locator_identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()[:24],
    }
    envelope_mutations = (
        {"source_kind": "chart"},
        {"source_locator": mismatched_locator},
        {
            "verification_evidence": {
                **valid_record["verification_evidence"],
                "rendered_sha256": "0" * 64,
            }
        },
        {"verification_evidence": None},
        {"provider_used": None},
        {"model_used": None},
        {"page_number": None},
        {"fixed_content": "different accessible equation"},
    )
    for mutation in envelope_mutations:
        forged = {**valid_record, **mutation}
        with pytest.raises(ValidationError):
            FixedIssue.model_validate(forged)
        with pytest.raises(RemediationSubprocessError, match="^remediation_failed$"):
            SubprocessRemediationResult({"fixed_issues": [forged]})
    remediator._pikepdf_doc.close()
    fitz_document.close()


def test_pdf_writer_preserves_prior_destination_on_late_verifier_failure(
    tmp_path, monkeypatch
):
    from src.education.remediation import pdf_remediator

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    prior_bytes = b"prior approved destination"
    remediator, fitz_document, _ = _configured_region_writer(source, output)
    output.write_bytes(prior_bytes)
    monkeypatch.setattr(
        pdf_remediator,
        "verify_scanned_region_formula_association",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(
        RuntimeError, match="Post-save image-equation association verification failed"
    ):
        remediator._write_pdf_output(fitz_document, str(output))

    assert output.read_bytes() == prior_bytes
    assert list(tmp_path.glob(".output.pdf.verify-*.pdf")) == []
    assert remediator._verified_image_equations == []
    remediator._pikepdf_doc.close()
    fitz_document.close()


def test_real_ocr_working_copy_keeps_search_layer_and_exact_region(tmp_path):
    ocrmypdf = pytest.importorskip("ocrmypdf")
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is unavailable")
    source = tmp_path / "source.pdf"
    ocr_output = tmp_path / "ocr.pdf"
    associated = tmp_path / "associated.pdf"
    _write_scan(source)
    with fitz.open(source) as original:
        original_occurrence = _displayed_image_occurrences(original[0], 1)[0]
        ocr_data = {
            "text": ["Equation", "1", "x", "+", "y", "=", "2"],
            "conf": [95, 95, 95, 95, 95, 95, 95],
            "left": [20, 62, 20, 30, 40, 50, 60],
            "top": [20, 20, 52, 52, 52, 52, 52],
            "width": [40, 7, 7, 7, 7, 7, 7],
            "height": [12, 12, 12, 12, 12, 12, 12],
            "block_num": [1, 1, 1, 1, 1, 1, 1],
            "par_num": [1, 1, 1, 1, 1, 1, 1],
            "line_num": [1, 1, 2, 2, 2, 2, 2],
        }
        findings = RasterEquationRegionDetector(
            ocr_data=lambda *_args, **_kwargs: ocr_data,
            ocr_version=lambda: "5.5.1",
            ocr_tessdata_sha256=lambda: (
                "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
            ),
        ).find_regions(original, original[0], original_occurrence)
        assert len(findings) == 1
        detected_locator = PageRasterRegionLocator.model_validate(
            {
                key: value
                for key, value in findings[0]["metadata"].items()
                if key in PageRasterRegionLocator.model_fields
            }
        )
        original_pending = dataclasses.replace(
            _pending(original, tuple(detected_locator.pixel_bbox)),
            locator=detected_locator,
        )
    try:
        ocrmypdf.ocr(
            input_file=source,
            output_file=ocr_output,
            force_ocr=False,
            skip_text=True,
            redo_ocr=False,
            optimize=1,
            language=["eng"],
            output_type="pdf",
            progress_bar=False,
            use_threads=True,
            tesseract_oem=3,
            tesseract_pagesegmode=6,
            tesseract_timeout=15.0,
        )
    except ocrmypdf.exceptions.MissingDependencyError as exc:
        pytest.skip(f"OCRmyPDF dependency unavailable: {type(exc).__name__}")

    fitz_document = fitz.open(ocr_output)
    validated = EquationRegionSource().extract(
        fitz_document, original_pending.locator.model_dump(mode="json")
    )
    pending = dataclasses.replace(
        original_pending,
        working_occurrence=validated.working_occurrence,
        normalized_crop_sha256=validated.normalized_sha256,
        verification_evidence=dataclasses.replace(
            original_pending.verification_evidence,
            source_sha256=validated.normalized_sha256,
        ),
    )
    with pikepdf.open(ocr_output) as pdf:
        PDFStructureTree(pdf)
        ContentTaggerV2(
            pdf, fitz_document, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        xobjects = pdf.pages[0].obj[Name.Resources][Name.XObject]
        form_name, form = next(
            (name, value)
            for name, value in xobjects.items()
            if str(value.get(Name.Subtype, "")) == "/Form"
        )
        alias = Name("/OCRAlias")
        assert alias not in xobjects
        original_form_payload = form.read_bytes()
        assert b"3 Tr" in original_form_payload
        form.write(original_form_payload.replace(b"3 Tr", b"0 Tr", 1))
        with pytest.raises(
            ScannedRegionAssociationError, match="region_ocr_text_not_invisible"
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
        assert Name.StructParents not in form
        form.write(original_form_payload)

        xobjects[alias] = form
        with pytest.raises(
            ScannedRegionAssociationError,
            match="region_ocr_form_(?:ambiguous|reused)",
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
        assert Name.StructParents not in form
        del xobjects[alias]
        assert form_name in xobjects

        wrapper = pdf.make_stream(b"q /NestedOCR Do Q")
        wrapper[Name.Type] = Name.XObject
        wrapper[Name.Subtype] = Name.Form
        wrapper[Name.BBox] = pikepdf.Array([0, 0, 400, 300])
        wrapper[Name.Resources] = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary({"/NestedOCR": form}),
            }
        )
        reused_page = pdf.add_blank_page(page_size=(400, 300))
        reused_page.obj[Name.Resources] = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary({"/OCRWrapper": wrapper}),
            }
        )
        reused_page.obj[Name.Contents] = pdf.make_stream(b"q /OCRWrapper Do Q")
        with pytest.raises(
            ScannedRegionAssociationError, match="region_ocr_form_reused"
        ):
            associate_scanned_region_formula(pdf, fitz_document, pending)
        assert Name.StructParents not in form
        del pdf.pages[-1]

    fitz_document.close()

    remediator, writer_document, pending = _configured_region_writer(
        ocr_output,
        associated,
        pending=pending,
        create_source=False,
    )
    remediator._write_pdf_output(writer_document, str(associated))
    assert len(remediator._verified_image_equations) == 1
    _, _, result, contract = remediator._verified_image_equations[0]
    assert result.ocr_group_owners == (("/P", 0), ("/Artifact", -1))
    assert result.ocr_before_mcids == (0,)
    assert result.ocr_after_mcids == ()
    saved_evidence = next(
        item
        for item in contract.verification_evidence
        if item.evidence_kind == "scanned_region_formula_saved_v1"
    )
    assert saved_evidence.ocr_group_owners == result.ocr_group_owners
    remediator._reconcile_verified_image_equations()
    assert remediator.result.fixed_count == 1
    fixed = remediator.result.fixed_issues[0]
    assert fixed.source_locator == pending.locator
    remediator._pikepdf_doc.close()
    writer_document.close()

    assert verify_scanned_region_formula_association(associated, pending, result)
    with fitz.open(associated) as delivered:
        assert "Equation" in delivered[0].get_text()

    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import uuid

    from src.db.models import RemediationOutcome, ScanStatus
    from src.services.remediation_artifact_service import RemediationArtifactService
    from src.services.scan_fix_service import (
        apply_authenticated_batch_review,
        build_scan_fix,
    )

    scan_id = "22222222-2222-4222-8222-222222222222"
    department_id = "11111111-1111-4111-8111-111111111111"
    reviewer_id = "55555555-5555-4555-8555-555555555555"
    persisted_fix = build_scan_fix(scan_id, fixed)
    review_db = MagicMock()
    apply_authenticated_batch_review(
        review_db,
        scan_id=scan_id,
        fixes=[persisted_fix],
        action="approve",
        user_id=reviewer_id,
        reviewed_at=datetime.now(timezone.utc),
    )
    assert persisted_fix.review_status == "approved"

    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=10 * 1024 * 1024,
        retention_days=30,
        approved_retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=3600,
    )
    artifact_id = str(uuid.uuid4())
    stored_name = f"{uuid.uuid4()}.pdf"
    storage_key = f"{department_id}/{scan_id}/{artifact_id}/{stored_name}"
    stored_path = service.root / storage_key
    stored_path.parent.mkdir(parents=True)
    shutil.copyfile(associated, stored_path)
    artifact_bytes = associated.read_bytes()
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    now = datetime.now(timezone.utc)
    artifact = SimpleNamespace(
        id=artifact_id,
        department_id=department_id,
        scan_id=scan_id,
        cloud_file_id=None,
        storage_backend="local",
        storage_key=storage_key,
        filename="associated.pdf",
        mime_type="application/pdf",
        size_bytes=len(artifact_bytes),
        sha256=artifact_sha256,
        provider="local",
        scan_type="PDF",
        lifecycle_status="available",
        review_status="pending",
        expires_at=now + timedelta(days=30),
        cleanup_claimed_at=None,
        written_back_at=None,
        approval_checksum=None,
        approval_review_digest=None,
        approved_by_id=None,
        approved_by_ref=None,
        approved_at=None,
        rejected_by_id=None,
        rejected_by_ref=None,
        rejected_at=None,
    )
    scan = SimpleNamespace(
        id=scan_id,
        current_remediation_artifact_id=artifact_id,
        status=ScanStatus.COMPLETED,
        remediation_outcome=RemediationOutcome.COMPLETED.value,
    )
    query = review_db.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.populate_existing.return_value = query
    query.all.return_value = [persisted_fix]
    service._lock_mutable_graph = lambda _db, _id: (scan, None, artifact)
    service.open_verified = lambda _db, _artifact, **_authority: (
        service._open_verified(artifact, allowed_lifecycle={"available"})
    )

    approved = service.approve(
        review_db,
        artifact_id=artifact_id,
        approved_by_ref="reviewer@example.test",
        approved_by_id=reviewer_id,
        now=now,
    )

    assert approved.approval_checksum == artifact_sha256
    assert approved.approval_review_digest is not None
    with service._open_verified(approved, allowed_lifecycle={"available"}) as stream:
        assert stream.read() == artifact_bytes

    tampered = tmp_path / "tampered.pdf"
    with pikepdf.open(associated) as pdf:
        xobjects = pdf.pages[0].obj[Name.Resources][Name.XObject]
        form = xobjects[Name(result.ocr_resource_name)]
        payload = form.read_bytes()
        assert b"3 Tr" in payload
        form.write(payload.replace(b"3 Tr", b"0 Tr", 1))
        pdf.save(tampered)
    assert not verify_scanned_region_formula_association(tampered, pending, result)
