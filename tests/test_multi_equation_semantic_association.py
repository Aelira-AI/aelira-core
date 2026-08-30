"""Atomic semantic ownership for one complete multi-equation raster group."""

from __future__ import annotations

import hashlib
import shutil
from io import BytesIO

import fitz
import pikepdf
import pytest
from PIL import Image, ImageDraw
from pikepdf import Array, Name, Operator, String
from pydantic import ValidationError

from src.education.multi_equation_semantics import (
    MultiEquationSavedEvidenceV1,
    MultiEquationSavedOwnerV1,
    MultiEquationSemanticContractV1,
    build_multi_equation_semantic_contract,
    build_multi_equation_semantic_owner,
    multi_equation_artifact_available,
)
from src.education.pdf_checks.equation_region_detector import (
    OCR_CONFIG,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    SUPPORTED_ENG_TESSDATA_SHA256,
)
from src.education.remediation.equation_recognizer import (
    EquationRecognition,
    EquationRecognitionRejected,
)
from src.education.remediation.equation_verifier import EquationVerificationEvidence
from src.education.remediation.multi_equation_semantics import (
    MultiEquationSemanticPlanner,
    MultiEquationSemanticRejected,
    associate_multi_equation_pdf,
    commit_multi_equation_transaction,
    remediate_multi_equation_pdf,
    verify_multi_equation_formulas,
)
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)

_MATHML = "<math><mi>x</mi><mo>=</mo><mn>1</mn></math>"
_MATHML_SHA256 = hashlib.sha256(_MATHML.encode("utf-8")).hexdigest()


def _ocr_data(lines):
    data = {
        key: []
        for key in (
            "text",
            "conf",
            "left",
            "top",
            "width",
            "height",
            "block_num",
            "par_num",
            "line_num",
        )
    }
    for index, (text, bbox) in enumerate(lines, start=1):
        x0, y0, x1, y1 = bbox
        data["text"].append(text)
        data["conf"].append("95")
        data["left"].append(x0)
        data["top"].append(y0)
        data["width"].append(x1 - x0)
        data["height"].append(y1 - y0)
        data["block_num"].append(1)
        data["par_num"].append(1)
        data["line_num"].append(index)
    return data


def _group(tmp_path, *, whole_system=False):
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.pdf_checks.multi_equation_region_detector import (
        MultiEquationRegionDetector,
    )

    lines = (
        [("{x=1", (30, 30, 90, 42)), ("y=2", (30, 50, 90, 62))]
        if whole_system
        else [("x=1", (30, 30, 90, 42)), ("y=2", (30, 100, 90, 112))]
    )
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    for _, (x0, y0, x1, y1) in lines:
        draw.rectangle((x0 + 1, y0 + 1, x1 - 2, y1 - 2), fill="black")
    payload = BytesIO()
    image.save(payload, format="PNG")
    image.close()
    path = tmp_path / ("system.pdf" if whole_system else "split.pdf")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=payload.getvalue())
    document.save(path)
    document.close()
    document = fitz.open(path)
    detector = MultiEquationRegionDetector(
        ocr_data=lambda _image, **_kwargs: _ocr_data(lines),
        ocr_version=lambda: "5.5.1",
        ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
    )
    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    group = detector.find_group(document, document[0], occurrence)
    assert group is not None
    assert detector.revalidate_group(document, group) == group
    document.close()
    return group


def _semantic():
    return MathMLExpressionV1(
        semantic_kind="mathml_expression_v1",
        mathml=_MATHML,
        alt_text="x equals 1",
        mathml_sha256=_MATHML_SHA256,
    )


def _evidence(source_sha256):
    return PrintedEquationRoundtripEvidenceV1(
        evidence_kind="printed_equation_roundtrip_v1",
        passed=True,
        source_sha256=source_sha256,
        rendered_sha256="b" * 64,
        mathml_sha256=_MATHML_SHA256,
        renderer_version="renderer-v1",
        comparator_version="comparator-v1",
        font_sha256="c" * 64,
        threshold_version="threshold-v1",
        ink_iou=0.95,
        pixel_similarity=0.99,
        required_ink_iou=0.90,
        required_pixel_similarity=0.98,
    )


def _owner(child, ordinal):
    normalized = hashlib.sha256(child.region_id.encode("ascii")).hexdigest()
    return build_multi_equation_semantic_owner(
        owner_kind="multi_equation_child_v1",
        ordinal=ordinal,
        region_ids=(child.region_id,),
        pixel_bbox=child.pixel_bbox,
        pdf_bbox=child.pdf_bbox,
        semantic_output=_semantic(),
        normalized_source_sha256=normalized,
        verification_evidence=_evidence(normalized),
        provider="provider",
        model="model",
    )


def _system_owner(group):
    pixel_bbox = (
        min(child.pixel_bbox[0] for child in group.children),
        min(child.pixel_bbox[1] for child in group.children),
        max(child.pixel_bbox[2] for child in group.children),
        max(child.pixel_bbox[3] for child in group.children),
    )
    pdf_bbox = (
        min(child.pdf_bbox[0] for child in group.children),
        min(child.pdf_bbox[1] for child in group.children),
        max(child.pdf_bbox[2] for child in group.children),
        max(child.pdf_bbox[3] for child in group.children),
    )
    normalized = hashlib.sha256(group.group_id.encode("ascii")).hexdigest()
    return build_multi_equation_semantic_owner(
        owner_kind="multi_equation_system_v1",
        ordinal=0,
        region_ids=tuple(child.region_id for child in group.children),
        pixel_bbox=pixel_bbox,
        pdf_bbox=pdf_bbox,
        semantic_output=_semantic(),
        normalized_source_sha256=normalized,
        verification_evidence=_evidence(normalized),
    )


def _formula_bbox(group, owner):
    matrix = group.children[0].transform
    px0, py0, px1, py1 = owner.pixel_bbox
    clip = (
        px0 / group.source_width,
        1.0 - (py1 / group.source_height),
        (px1 - px0) / group.source_width,
        (py1 - py0) / group.source_height,
    )
    return (
        matrix[4] + matrix[0] * clip[0],
        matrix[5] + matrix[3] * clip[1],
        matrix[4] + matrix[0] * (clip[0] + clip[2]),
        matrix[5] + matrix[3] * (clip[1] + clip[3]),
    )


def _saved_owner(group, owner, ordinal):
    return MultiEquationSavedOwnerV1(
        ordinal=ordinal,
        region_ids=owner.region_ids,
        struct_parent=4,
        mcid=20 + ordinal,
        formula_bbox=_formula_bbox(group, owner),
        mathml_sha256=owner.semantic_output.mathml_sha256,
        alt_text_sha256=hashlib.sha256(
            owner.semantic_output.alt_text.encode("utf-8")
        ).hexdigest(),
        attachment_sha256=owner.semantic_output.mathml_sha256,
        backlink_count=1,
        parent_tree_count=1,
    )


def _saved_evidence(group, owners, *, saved_file_sha256="d" * 64):
    return MultiEquationSavedEvidenceV1(
        evidence_kind="multi_equation_saved_v1",
        passed=True,
        saved_file_sha256=saved_file_sha256,
        page_number=group.page_number,
        parent_occurrence_id=group.parent_occurrence_id,
        saved_parent_occurrence_id=group.parent_occurrence_id,
        image_xref=group.image_xref,
        image_index=group.image_index,
        occurrence_ordinal=group.occurrence_ordinal,
        source_sha256=group.source_sha256,
        parent_bbox=group.children[0].parent_bbox,
        transform=group.children[0].transform,
        disposition=group.disposition,
        original_artifact_count=1,
        owners=tuple(
            _saved_owner(group, owner, index) for index, owner in enumerate(owners)
        ),
        render_signatures=((72, 400, 300, 400, 300, "e" * 64),),
    )


def _contract(group, owners):
    return build_multi_equation_semantic_contract(
        group=group,
        owners=owners,
        saved_evidence=_saved_evidence(group, owners),
    )


def test_split_contract_is_frozen_exact_and_ordered(tmp_path) -> None:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    contract = _contract(group, owners)

    assert contract.review_required is True
    assert contract.publication_authorized is False
    assert [owner.region_ids[0] for owner in contract.owners] == [
        child.region_id for child in group.children
    ]
    assert (
        MultiEquationSemanticContractV1.model_validate_json(contract.model_dump_json())
        == contract
    )
    with pytest.raises(ValidationError):
        contract.review_required = False
    value = contract.model_dump(mode="json")
    with pytest.raises(ValidationError):
        MultiEquationSemanticContractV1.model_validate({**value, "active": True})
    with pytest.raises(ValidationError):
        MultiEquationSemanticContractV1.model_validate(
            {**value, "owners": list(reversed(value["owners"]))}
        )


def test_whole_system_contract_owns_exact_union_once(tmp_path) -> None:
    group = _group(tmp_path, whole_system=True)
    owner = _system_owner(group)
    contract = _contract(group, (owner,))

    assert len(contract.owners) == 1
    assert contract.owners[0].region_ids == tuple(
        child.region_id for child in group.children
    )
    with pytest.raises(ValidationError):
        _contract(
            group,
            tuple(_owner(child, index) for index, child in enumerate(group.children)),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "0" * 64),
        ("disposition", "whole_system"),
        ("original_artifact_count", 2),
    ],
)
def test_saved_contract_tampering_is_rejected(tmp_path, field, value) -> None:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    contract = _contract(group, owners)
    dumped = contract.model_dump(mode="json")
    dumped["saved_evidence"][field] = value

    with pytest.raises(ValidationError):
        MultiEquationSemanticContractV1.model_validate(dumped)


def test_owner_digest_and_verification_evidence_are_bound(tmp_path) -> None:
    group = _group(tmp_path)
    owner = _owner(group.children[0], 0)
    value = owner.model_dump(mode="json")

    for mutation in (
        {**value, "owner_sha256": "0" * 64},
        {**value, "normalized_source_sha256": "0" * 64},
        {**value, "region_ids": (group.children[1].region_id,)},
    ):
        with pytest.raises(ValidationError):
            type(owner).model_validate(mutation)


def test_unapproved_contract_cannot_authorize_artifact_availability(tmp_path) -> None:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    contract = _contract(group, owners)

    assert multi_equation_artifact_available(contract, human_approved=False) is False
    assert multi_equation_artifact_available(contract, human_approved=True) is True


def test_detector_contract_calls_are_bounded_and_pinned() -> None:
    assert OCR_LANGUAGE == "eng"
    assert "psm" in OCR_CONFIG
    assert OCR_TIMEOUT_SECONDS > 0


class _Recognizer:
    def __init__(self, *, fail_at=None):
        self.calls = []
        self.system_calls = []
        self.fail_at = fail_at

    def recognize(self, source):
        self.calls.append(source)
        if len(self.calls) == self.fail_at:
            raise EquationRecognitionRejected("provider_failure")
        return EquationRecognition(
            classification="printed_equation",
            latex="x=1",
            provider="provider",
            model="model",
        )

    def recognize_system(self, source):
        self.system_calls.append(source)
        return self.recognize(source)


class _Verifier:
    converter = staticmethod(lambda _latex: _MATHML)

    @staticmethod
    def canonicalize_mathml(mathml):
        return mathml

    @staticmethod
    def verify(source, _latex):
        return EquationVerificationEvidence(
            passed=True,
            source_sha256=source.normalized_sha256,
            rendered_sha256="b" * 64,
            mathml_sha256=_MATHML_SHA256,
            renderer_version="renderer-v1",
            comparator_version="comparator-v1",
            font_sha256="c" * 64,
            threshold_version="threshold-v1",
            ink_iou=0.95,
            pixel_similarity=0.99,
            required_ink_iou=0.90,
            required_pixel_similarity=0.98,
        )


def _planned_owners(path, group):
    class _AcceptedDetector:
        @staticmethod
        def revalidate_group(_document, value):
            return value

    with fitz.open(path) as document:
        return MultiEquationSemanticPlanner(
            _Recognizer(), _Verifier(), detector=_AcceptedDetector()
        ).plan(document, group)


def test_split_recognition_verifies_every_child_in_order(tmp_path) -> None:
    group = _group(tmp_path)
    document = fitz.open(tmp_path / "split.pdf")
    recognizer = _Recognizer()
    owners = MultiEquationSemanticPlanner(recognizer, _Verifier()).plan(document, group)
    document.close()

    assert len(recognizer.calls) == len(group.children) == len(owners)
    assert [owner.region_ids for owner in owners] == [
        (child.region_id,) for child in group.children
    ]
    assert all(owner.owner_kind == "multi_equation_child_v1" for owner in owners)


def test_whole_system_recognizes_and_verifies_one_exact_union(tmp_path) -> None:
    group = _group(tmp_path, whole_system=True)
    document = fitz.open(tmp_path / "system.pdf")
    recognizer = _Recognizer()
    owners = MultiEquationSemanticPlanner(recognizer, _Verifier()).plan(document, group)
    document.close()

    assert len(recognizer.calls) == len(owners) == 1
    assert recognizer.system_calls == recognizer.calls
    owner = owners[0]
    assert owner.owner_kind == "multi_equation_system_v1"
    assert owner.region_ids == tuple(child.region_id for child in group.children)
    assert (recognizer.calls[0].width, recognizer.calls[0].height) == (
        owner.pixel_bbox[2] - owner.pixel_bbox[0],
        owner.pixel_bbox[3] - owner.pixel_bbox[1],
    )


def test_partial_split_recognition_returns_no_owner_subset(tmp_path) -> None:
    group = _group(tmp_path)
    document = fitz.open(tmp_path / "split.pdf")
    recognizer = _Recognizer(fail_at=2)

    with pytest.raises(MultiEquationSemanticRejected):
        MultiEquationSemanticPlanner(recognizer, _Verifier()).plan(document, group)
    document.close()
    assert len(recognizer.calls) == 2


def test_stale_group_rejects_before_recognition(tmp_path) -> None:
    group = _group(tmp_path)
    document = fitz.open(tmp_path / "split.pdf")
    recognizer = _Recognizer()

    class _StaleDetector:
        @staticmethod
        def revalidate_group(_document, _group):
            return None

    with pytest.raises(MultiEquationSemanticRejected, match="group_stale"):
        MultiEquationSemanticPlanner(
            recognizer,
            _Verifier(),
            detector=_StaleDetector(),
        ).plan(document, group)
    document.close()
    assert recognizer.calls == []


def test_transaction_replaces_output_only_after_saved_verification(tmp_path) -> None:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    source = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    source.write_bytes(b"%PDF-1.4\noriginal")
    output.write_bytes(b"prior output")
    original = source.read_bytes()

    def associate(candidate, _group, _owners):
        candidate.write_bytes(candidate.read_bytes() + b"\nassociated")

    def verify(candidate, checked_group, checked_owners):
        return _saved_evidence(
            checked_group,
            checked_owners,
            saved_file_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )

    contract = commit_multi_equation_transaction(
        source,
        output,
        group,
        owners,
        associate=associate,
        verify_saved=verify,
    )

    assert source.read_bytes() == original
    assert output.read_bytes() == original + b"\nassociated"
    assert (
        contract.saved_evidence.saved_file_sha256
        == hashlib.sha256(output.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("failure_stage", ["associate", "verify", "digest"])
def test_transaction_failure_preserves_source_and_prior_output(
    tmp_path, failure_stage
) -> None:
    group = _group(tmp_path)
    owners = tuple(_owner(child, index) for index, child in enumerate(group.children))
    source = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    source.write_bytes(b"%PDF-1.4\noriginal")
    output.write_bytes(b"prior output")

    def associate(candidate, _group, _owners):
        candidate.write_bytes(candidate.read_bytes() + b"\npartial")
        if failure_stage == "associate":
            raise RuntimeError("association failed")

    def verify(candidate, checked_group, checked_owners):
        if failure_stage == "verify":
            raise RuntimeError("verification failed")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if failure_stage == "digest":
            digest = "0" * 64
        return _saved_evidence(
            checked_group,
            checked_owners,
            saved_file_sha256=digest,
        )

    with pytest.raises(MultiEquationSemanticRejected):
        commit_multi_equation_transaction(
            source,
            output,
            group,
            owners,
            associate=associate,
            verify_saved=verify,
        )

    assert source.read_bytes() == b"%PDF-1.4\noriginal"
    assert output.read_bytes() == b"prior output"
    assert list(tmp_path.glob(".output.pdf.multi-equation-*.pdf")) == []


@pytest.mark.parametrize("whole_system", [False, True])
def test_saved_pdf_has_one_artifact_and_ordered_exact_formula_owners(
    tmp_path, whole_system
) -> None:
    group = _group(tmp_path, whole_system=whole_system)
    source = tmp_path / ("system.pdf" if whole_system else "split.pdf")
    owners = _planned_owners(source, group)
    output = tmp_path / ("system-output.pdf" if whole_system else "split-output.pdf")
    original = source.read_bytes()

    contract = remediate_multi_equation_pdf(source, output, group, owners)

    assert source.read_bytes() == original
    assert contract.saved_evidence.original_artifact_count == 1
    assert [owner.ordinal for owner in contract.saved_evidence.owners] == list(
        range(len(owners))
    )
    assert [owner.formula_bbox for owner in contract.saved_evidence.owners] == [
        pytest.approx(_formula_bbox(group, owner)) for owner in owners
    ]
    with pikepdf.open(output) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        names = [str(op.operator) for op in ops]
        assert names.count("BMC") == 1
        assert names.count("BDC") == len(owners)
        assert names.count("Do") == len(owners) + 1


def _saved_formulas(pdf):
    from src.education.remediation.content_tagger_v2 import (
        _collect_structure_elements_by_tag,
    )

    found = []
    roots = pdf.Root[Name.StructTreeRoot].get(Name.K)
    for root in list(roots) if isinstance(roots, Array) else [roots]:
        _collect_structure_elements_by_tag(root, found, "/Formula")
    return found


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "clip",
        "formula_bbox",
        "structure_order",
        "parent_tree",
        "mathml",
        "alt",
        "pixels",
    ],
)
def test_saved_group_tampering_rejects_the_complete_proof(tmp_path, mutation) -> None:
    from src.education.remediation.content_tagger_v2 import _number_tree_entries

    group = _group(tmp_path)
    source = tmp_path / "split.pdf"
    owners = _planned_owners(source, group)
    associated = tmp_path / f"associated-{mutation}.pdf"
    associated.write_bytes(source.read_bytes())
    association = associate_multi_equation_pdf(associated, group, owners)
    assert verify_multi_equation_formulas(associated, group, owners, association).passed

    if mutation == "source":
        changed = tmp_path / "source-changed.pdf"
        with fitz.open(associated) as document:
            xref = int(document[0].get_image_info(xrefs=True)[0]["xref"])
            payload = document.extract_image(xref)["image"]
            with Image.open(BytesIO(payload)) as image:
                image.load()
                replacement_image = image.convert("RGB")
                replacement_image.putpixel((0, 0), (254, 254, 254))
                replacement = BytesIO()
                replacement_image.save(replacement, format="PNG")
                replacement_image.close()
            document[0].replace_image(xref, stream=replacement.getvalue())
            document.save(changed)
        associated.write_bytes(changed.read_bytes())

    else:
        with pikepdf.open(associated, allow_overwriting_input=True) as pdf:
            page = pdf.pages[0]
            formulas = _saved_formulas(pdf)
            if mutation == "clip":
                ops = list(pikepdf.parse_content_stream(page))
                bdc_index = next(
                    index
                    for index, op in enumerate(ops)
                    if str(op.operator) == "BDC" and str(op.operands[0]) == "/Formula"
                )
                values = [float(value) for value in ops[bdc_index + 3].operands]
                values[0] += 0.01
                ops[bdc_index + 3] = pikepdf.ContentStreamInstruction(
                    values, Operator("re")
                )
                page.obj[Name.Contents] = pdf.make_stream(
                    pikepdf.unparse_content_stream(ops)
                )
            elif mutation == "formula_bbox":
                formulas[0][Name.A][Name("/BBox")][0] += 1.0
            elif mutation == "structure_order":
                parent = formulas[0][Name.P]
                children = list(parent[Name.K])
                first = next(
                    index
                    for index, child in enumerate(children)
                    if tuple(child.objgen) == tuple(formulas[0].objgen)
                )
                second = next(
                    index
                    for index, child in enumerate(children)
                    if tuple(child.objgen) == tuple(formulas[1].objgen)
                )
                children[first], children[second] = children[second], children[first]
                parent[Name.K] = Array(children)
            elif mutation == "parent_tree":
                _, entries = _number_tree_entries(pdf.Root[Name.StructTreeRoot])
                page_array = next(
                    value for key, value in entries if key == association.struct_parent
                )
                page_array[association.owners[0].mcid] = None
            elif mutation == "mathml":
                embedded = formulas[0][Name("/AF")][0][Name("/EF")][Name.F]
                embedded.write(b"<math><mn>999</mn></math>")
            elif mutation == "alt":
                formulas[0][Name.Alt] = String("changed meaning")
            elif mutation == "pixels":
                ops = list(pikepdf.parse_content_stream(page))
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
                page.obj[Name.Contents] = pdf.make_stream(
                    pikepdf.unparse_content_stream(ops)
                )
            pdf.save(associated)

    with pytest.raises(MultiEquationSemanticRejected):
        verify_multi_equation_formulas(associated, group, owners, association)


def test_retry_cannot_append_a_second_group_and_preserves_prior_output(
    tmp_path,
) -> None:
    group = _group(tmp_path)
    source = tmp_path / "split.pdf"
    owners = _planned_owners(source, group)
    first = tmp_path / "first.pdf"
    retry = tmp_path / "retry.pdf"
    remediate_multi_equation_pdf(source, first, group, owners)
    first_bytes = first.read_bytes()
    retry.write_bytes(b"prior output")

    with pytest.raises(MultiEquationSemanticRejected):
        remediate_multi_equation_pdf(first, retry, group, owners)

    assert first.read_bytes() == first_bytes
    assert retry.read_bytes() == b"prior output"


@pytest.mark.parametrize("budget", ["semantic", "render"])
def test_aggregate_budget_refusal_preserves_prior_output(
    tmp_path, monkeypatch, budget
) -> None:
    from src.education.remediation import content_tagger_v2, multi_equation_semantics

    group = _group(tmp_path)
    source = tmp_path / "split.pdf"
    owners = _planned_owners(source, group)
    output = tmp_path / f"budget-{budget}.pdf"
    output.write_bytes(b"prior output")
    if budget == "semantic":
        monkeypatch.setattr(
            multi_equation_semantics, "_MAX_SEMANTIC_BYTES_PER_GROUP", 1
        )
    else:
        monkeypatch.setattr(
            content_tagger_v2, "_REGION_MAX_TRANSACTION_RENDER_BYTES", 1
        )

    with pytest.raises(MultiEquationSemanticRejected):
        remediate_multi_equation_pdf(source, output, group, owners)

    assert output.read_bytes() == b"prior output"


def test_semantic_evidence_for_another_crop_cannot_reach_association(tmp_path) -> None:
    group = _group(tmp_path)
    forged = tuple(_owner(child, index) for index, child in enumerate(group.children))
    source = tmp_path / "split.pdf"
    output = tmp_path / "forged-source.pdf"
    output.write_bytes(b"prior output")

    with pytest.raises(MultiEquationSemanticRejected, match="semantic_source_changed"):
        remediate_multi_equation_pdf(source, output, group, forged)

    assert output.read_bytes() == b"prior output"


def test_real_ocr_form_keeps_group_formula_order_and_search_layer(tmp_path) -> None:
    ocrmypdf = pytest.importorskip("ocrmypdf")
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is unavailable")
    source = tmp_path / "split.pdf"
    ocr_source = tmp_path / "split-ocr.pdf"
    output = tmp_path / "split-ocr-associated.pdf"
    lines = [("x=1", (30, 30, 90, 42)), ("y=2", (30, 100, 90, 112))]
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    for text, (x0, y0, _x1, _y1) in lines:
        draw.text((x0, y0), text, fill="black")
    payload = BytesIO()
    image.save(payload, format="PNG")
    image.close()
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=payload.getvalue())
    document.save(source)
    document.close()
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.pdf_checks.multi_equation_region_detector import (
        MultiEquationRegionDetector,
    )

    with fitz.open(source) as document:
        detector = MultiEquationRegionDetector(
            ocr_data=lambda _image, **_kwargs: _ocr_data(lines),
            ocr_version=lambda: "5.5.1",
            ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
        )
        occurrence = _displayed_image_occurrences(document[0], 1)[0]
        group = detector.find_group(document, document[0], occurrence)
        assert group is not None
    owners = _planned_owners(source, group)
    try:
        ocrmypdf.ocr(
            input_file=source,
            output_file=ocr_source,
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

    contract = remediate_multi_equation_pdf(ocr_source, output, group, owners)

    evidence = contract.saved_evidence
    assert [owner.mcid for owner in evidence.owners] == sorted(
        owner.mcid for owner in evidence.owners
    )
    with fitz.open(output) as delivered:
        assert delivered[0].get_text("text").strip()
