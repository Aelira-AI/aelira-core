"""Deterministic ordered-region discovery for multi-equation screenshots."""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO

import fitz
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from src.education.pdf_checks.equation_region_detector import (
    OCR_CONFIG,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    SUPPORTED_ENG_TESSDATA_SHA256,
)


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


class _OCR:
    def __init__(self, lines):
        self.data = _ocr_data(lines)
        self.calls = 0

    def __call__(self, image, **kwargs):
        self.calls += 1
        assert kwargs == {
            "lang": OCR_LANGUAGE,
            "config": OCR_CONFIG,
            "timeout": OCR_TIMEOUT_SECONDS,
        }
        return self.data


def _image(lines):
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    for _, (x0, y0, x1, y1) in lines:
        draw.rectangle((x0 + 1, y0 + 1, x1 - 2, y1 - 2), fill="black")
    payload = BytesIO()
    image.save(payload, format="PNG")
    return payload.getvalue()


def _write_pdf(path, lines, *, duplicate=False, rotation=0):
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    payload = _image(lines)
    page.insert_image(page.rect, stream=payload)
    if duplicate:
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=payload)
    if rotation:
        page.set_rotation(rotation)
    document.save(path)
    document.close()


def _detector(lines):
    from src.education.pdf_checks.multi_equation_region_detector import (
        MultiEquationRegionDetector,
    )

    ocr = _OCR(lines)
    return (
        MultiEquationRegionDetector(
            ocr_data=ocr,
            ocr_version=lambda: "5.5.1",
            ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
        ),
        ocr,
    )


def _find(path, lines):
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    detector, ocr = _detector(lines)
    document = fitz.open(path)
    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    group = detector.find_group(document, document[0], occurrence)
    return detector, ocr, document, group


SPLIT_LINES = [("x=1", (30, 30, 90, 42)), ("y=2", (30, 100, 90, 112))]
SYSTEM_LINES = [("{x=1", (30, 30, 90, 42)), ("y=2", (30, 50, 90, 62))]


def test_split_discovery_is_stable_ordered_and_revalidatable(tmp_path):
    path = tmp_path / "split.pdf"
    _write_pdf(path, SPLIT_LINES)
    before = path.read_bytes()
    detector, ocr, document, group = _find(path, list(reversed(SPLIT_LINES)))

    assert group is not None
    assert group.disposition == "split_children"
    assert [child.pixel_bbox[1] for child in group.children] == [26, 96]
    assert len({child.region_id for child in group.children}) == 2
    assert detector.revalidate_group(document, group) == group
    assert ocr.calls == 1
    document.close()
    assert path.read_bytes() == before


def test_whole_system_disposition_is_explicit(tmp_path):
    path = tmp_path / "system.pdf"
    _write_pdf(path, SYSTEM_LINES)
    detector, _, document, group = _find(path, SYSTEM_LINES)

    assert group is not None
    assert group.disposition == "whole_system"
    assert detector.revalidate_group(document, group) == group
    document.close()


def test_side_by_side_equations_use_left_to_right_split_order(tmp_path):
    lines = [("y=2", (180, 40, 240, 52)), ("x=1", (30, 40, 90, 52))]
    path = tmp_path / "side-by-side.pdf"
    _write_pdf(path, lines)
    _, _, document, group = _find(path, lines)

    assert group is not None
    assert group.disposition == "split_children"
    assert [child.pixel_bbox[0] for child in group.children] == [26, 176]
    document.close()


def test_group_contract_is_frozen_exact_and_digest_bound(tmp_path):
    from src.education.multi_equation_region import MultiEquationRegionGroupV1

    path = tmp_path / "group.pdf"
    _write_pdf(path, SPLIT_LINES)
    _, _, document, group = _find(path, SPLIT_LINES)
    assert group is not None
    with pytest.raises(ValidationError):
        group.disposition = "whole_system"
    with pytest.raises(ValidationError):
        group.children[0].page_number = 2
    value = group.model_dump(mode="json")
    for mutation in (
        {**value, "active": True},
        {**value, "group_sha256": "0" * 64},
        {**value, "group_id": "eqgroup-v1-" + "0" * 24},
    ):
        with pytest.raises(ValidationError):
            MultiEquationRegionGroupV1.model_validate(mutation)
    document.close()


def test_contract_rejects_reordered_overlapping_or_cross_parent_children(tmp_path):
    from src.education.multi_equation_region import MultiEquationRegionGroupV1

    path = tmp_path / "children.pdf"
    _write_pdf(path, SPLIT_LINES)
    _, _, document, group = _find(path, SPLIT_LINES)
    assert group is not None
    value = group.model_dump(mode="json")
    reordered = deepcopy(value)
    reordered["children"].reverse()
    overlapping = deepcopy(value)
    overlapping["children"][1]["pixel_bbox"] = overlapping["children"][0]["pixel_bbox"]
    cross_parent = deepcopy(value)
    cross_parent["children"][1]["source_sha256"] = "0" * 64
    for mutation in (reordered, overlapping, cross_parent):
        with pytest.raises(ValidationError):
            MultiEquationRegionGroupV1.model_validate(mutation)
    document.close()


def test_contract_rejects_a_valid_child_locator_with_another_parent_transform(
    tmp_path,
):
    from src.education.multi_equation_region import (
        MultiEquationRegionRejected,
        build_multi_equation_group,
    )
    from src.education.pdf_checks.equation_region_detector import _canonical_digest
    from src.education.visual_semantic_contract import FrozenPageRasterRegionLocator

    path = tmp_path / "transform.pdf"
    _write_pdf(path, SPLIT_LINES)
    _, _, document, group = _find(path, SPLIT_LINES)
    assert group is not None
    changed = group.children[1].model_dump(mode="json")
    changed["parent_bbox"][0] += 1.0
    changed["parent_bbox"][2] += 1.0
    changed["pdf_bbox"][0] += 1.0
    changed["pdf_bbox"][2] += 1.0
    changed["transform"][4] += 1.0
    changed["region_id"] = (
        "eqregion-v1-"
        + _canonical_digest(
            {key: value for key, value in changed.items() if key != "region_id"}
        )[:24]
    )
    changed_child = FrozenPageRasterRegionLocator.model_validate(changed)

    with pytest.raises(MultiEquationRegionRejected):
        build_multi_equation_group(
            disposition="split_children",
            children=(group.children[0], changed_child),
        )
    document.close()


@pytest.mark.parametrize(
    "lines",
    [
        [("x=1", (30, 30, 90, 42))],
        [("x=1", (30, 30, 90, 42)), ("y=2", (30, 66, 90, 78))],
        [("x=1", (30, 30, 90, 42)), ("y=2", (30, 38, 90, 50))],
    ],
)
def test_single_ambiguous_and_overlapping_geometry_refuses(tmp_path, lines):
    path = tmp_path / "refuse.pdf"
    _write_pdf(path, lines)
    _, _, document, group = _find(path, lines)
    assert group is None
    document.close()


def test_non_math_or_low_confidence_line_cannot_become_a_partial_group(tmp_path):
    lines = [
        ("x=1", (30, 30, 90, 42)),
        ("ordinary prose", (30, 100, 140, 112)),
    ]
    path = tmp_path / "partial.pdf"
    _write_pdf(path, lines)
    _, _, document, group = _find(path, lines)
    assert group is None
    document.close()


def test_low_confidence_math_and_unowned_ink_refuse_the_whole_group(tmp_path):
    path = tmp_path / "unowned.pdf"
    extra = ("z=3", (200, 180, 260, 192))
    _write_pdf(path, SPLIT_LINES + [extra])

    detector, ocr = _detector(SPLIT_LINES + [extra])
    ocr.data["conf"][-1] = "40"
    document = fitz.open(path)
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    assert detector.find_group(document, document[0], occurrence) is None
    document.close()

    detector, _ = _detector(SPLIT_LINES)
    document = fitz.open(path)
    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    assert detector.find_group(document, document[0], occurrence) is None
    document.close()


def test_clipped_child_refuses_the_whole_group(tmp_path):
    lines = [("x=1", (2, 2, 60, 14)), ("y=2", (30, 100, 90, 112))]
    path = tmp_path / "clipped.pdf"
    _write_pdf(path, lines)
    _, _, document, group = _find(path, lines)
    assert group is None
    document.close()


@pytest.mark.parametrize("duplicate,rotation", [(True, 0), (False, 90)])
def test_duplicate_image_and_rotated_parent_refuse_before_ocr(
    tmp_path, duplicate, rotation
):
    path = tmp_path / "unsupported.pdf"
    _write_pdf(path, SPLIT_LINES, duplicate=duplicate, rotation=rotation)
    detector, ocr = _detector(SPLIT_LINES)
    document = fitz.open(path)
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    assert detector.find_group(document, document[0], occurrence) is None
    assert ocr.calls == 0
    document.close()


def test_child_count_overflow_refuses_without_truncation(tmp_path):
    lines = [
        (f"x={index}", (30, 10 + index * 30, 90, 20 + index * 30)) for index in range(9)
    ]
    path = tmp_path / "overflow.pdf"
    _write_pdf(path, lines)
    _, ocr, document, group = _find(path, lines)
    assert group is None
    assert ocr.calls == 1
    document.close()


def test_document_page_budget_refuses_before_ocr(tmp_path):
    from src.education.pdf_checks.multi_equation_region_detector import (
        MAX_MULTI_EQUATION_PAGES_PER_DOCUMENT,
    )

    path = tmp_path / "many-pages.pdf"
    document = fitz.open()
    payload = _image(SPLIT_LINES)
    for _ in range(MAX_MULTI_EQUATION_PAGES_PER_DOCUMENT + 1):
        page = document.new_page(width=400, height=300)
        page.insert_image(page.rect, stream=payload)
    document.save(path)
    document.close()

    detector, ocr = _detector(SPLIT_LINES)
    with fitz.open(path) as document:
        assert detector.find_document_groups(document) == ()
    assert ocr.calls == 0


def test_aggregate_validation_budget_refuses_without_partial_group(
    tmp_path, monkeypatch
):
    import src.education.pdf_checks.multi_equation_region_detector as module

    path = tmp_path / "budget.pdf"
    _write_pdf(path, SPLIT_LINES)
    monkeypatch.setattr(module, "MAX_MULTI_EQUATION_VALIDATION_PIXELS", 1)
    _, _, document, group = _find(path, SPLIT_LINES)
    assert group is None
    document.close()


def test_malformed_ocr_and_unsupported_identity_refuse(tmp_path):
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.pdf_checks.multi_equation_region_detector import (
        MultiEquationRegionDetector,
    )

    path = tmp_path / "ocr.pdf"
    _write_pdf(path, SPLIT_LINES)
    document = fitz.open(path)
    occurrence = _displayed_image_occurrences(document[0], 1)[0]
    malformed = MultiEquationRegionDetector(
        ocr_data=lambda *args, **kwargs: {"text": ["x=1"]},
        ocr_version=lambda: "5.5.1",
        ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
    )
    unsupported = MultiEquationRegionDetector(
        ocr_data=_OCR(SPLIT_LINES),
        ocr_version=lambda: "unknown",
        ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
    )
    assert malformed.find_group(document, document[0], occurrence) is None
    assert unsupported.find_group(document, document[0], occurrence) is None
    document.close()


def test_revalidation_rejects_every_group_and_child_identity_tamper(tmp_path):
    path = tmp_path / "tamper.pdf"
    _write_pdf(path, SPLIT_LINES)
    detector, _, document, group = _find(path, SPLIT_LINES)
    assert group is not None
    for field in ("source_sha256", "group_sha256", "group_id"):
        value = group.model_dump(mode="json")
        value[field] = "eqgroup-v1-" + "0" * 24 if field == "group_id" else "0" * 64
        assert detector.revalidate_group(document, value) is None
    child_tamper = group.model_dump(mode="json")
    child_tamper["children"][0]["crop_pixel_sha256"] = "0" * 64
    assert detector.revalidate_group(document, child_tamper) is None
    document.close()


def test_revalidation_rejects_changed_original_source(tmp_path):
    source = tmp_path / "source.pdf"
    changed = tmp_path / "changed.pdf"
    _write_pdf(source, SPLIT_LINES)
    detector, _, document, group = _find(source, SPLIT_LINES)
    assert group is not None
    document.close()

    changed_lines = SPLIT_LINES + [("title", (280, 250, 340, 262))]
    _write_pdf(changed, changed_lines)
    with fitz.open(changed) as changed_document:
        assert detector.revalidate_group(changed_document, group) is None


def test_typed_json_round_trip_is_lossless(tmp_path):
    from src.education.multi_equation_region import MultiEquationRegionGroupV1

    path = tmp_path / "roundtrip.pdf"
    _write_pdf(path, SPLIT_LINES)
    _, _, document, group = _find(path, SPLIT_LINES)
    assert group is not None
    assert (
        MultiEquationRegionGroupV1.model_validate_json(group.model_dump_json()) == group
    )
    document.close()
