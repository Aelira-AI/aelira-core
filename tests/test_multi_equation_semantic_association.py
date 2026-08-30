"""Atomic semantic ownership for one complete multi-equation raster group."""

from __future__ import annotations

import hashlib
from io import BytesIO

import fitz
import pytest
from PIL import Image, ImageDraw
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
    commit_multi_equation_transaction,
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


def _saved_owner(owner, ordinal):
    return MultiEquationSavedOwnerV1(
        ordinal=ordinal,
        region_ids=owner.region_ids,
        struct_parent=4,
        mcid=20 + ordinal,
        formula_bbox=owner.pdf_bbox,
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
        image_xref=group.image_xref,
        source_sha256=group.source_sha256,
        disposition=group.disposition,
        original_artifact_count=1,
        owners=tuple(_saved_owner(owner, index) for index, owner in enumerate(owners)),
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
