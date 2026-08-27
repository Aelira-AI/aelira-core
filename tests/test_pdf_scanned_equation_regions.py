from io import BytesIO

import fitz
import numpy as np
import pytest
from PIL import Image, ImageDraw

from src.education.math_contracts import (
    IMAGE_EQUATION_ISSUE_TYPE,
    SCANNED_EQUATION_REGION_ISSUE_TYPE,
    math_issue_type_from,
)
from src.education.pdf_checks.equation_region_detector import (
    MAX_CANDIDATES_PER_DOCUMENT,
    MAX_OCR_BOXES,
    MAX_REGION_PAGES_PER_DOCUMENT,
    OCR_CONFIG,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    RasterEquationRegionDetector,
    SUPPORTED_ENG_TESSDATA_SHA256,
    _bounded_component_count,
)
from src.education.pdf_checks.image_checker import (
    ImageAccessibilityChecker,
    _displayed_image_occurrences,
)
from src.education.pdf_checks.math_checker import MathEquationChecker
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
    classify_issue_category,
    materialize_manual_issues,
)
from src.education.remediation.math_fixer import MathFixer
from src.education.remediation.pdf_remediator import PdfRemediator
from src.jobs.remediation_job import _partition_authoritative_document_issues


def _scan_png(
    *,
    prose=False,
    blank=False,
    boundary_ink=False,
    noisy=False,
    distant_equation_number=False,
    vertical_gap_ink=False,
    detached_equation_parts=False,
    cue_edge_ink=False,
    distant_cue_edge_ink=False,
    disconnected_cue_edge_ink=False,
):
    image = Image.new("RGB", (400, 300), "white")
    if not blank:
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), "Equation 1", fill="black")
        draw.text((20, 52), "This is prose" if prose else "x + y = 2", fill="black")
        if boundary_ink:
            draw.line((20, 42, 80, 42), fill="black", width=1)
        if noisy:
            for y in range(100, 300, 2):
                for x in range(0, 400, 4):
                    draw.point((x, y), fill="black")
        if distant_equation_number:
            draw.text((180, 52), "(1)", fill="black")
        if vertical_gap_ink:
            draw.text((32, 28), "2", fill="black")
        if cue_edge_ink:
            draw.line((72, 31, 72, 32), fill="black", width=1)
        if distant_cue_edge_ink:
            draw.line((180, 31, 180, 32), fill="black", width=1)
        if disconnected_cue_edge_ink:
            draw.line((20, 32, 74, 32), fill="black", width=1)
        if detached_equation_parts:
            draw.line((24, 44, 70, 44), fill="black", width=1)
            draw.point((62, 68), fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _small_png():
    output = BytesIO()
    Image.new("RGB", (120, 40), "white").save(output, format="PNG")
    return output.getvalue()


def _write_scan_pdf(
    path,
    *,
    image_bytes=None,
    mixed=False,
    rotation=0,
    pdf_cue=False,
    vector=False,
):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=image_bytes or _scan_png())
    if mixed:
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=_scan_png(blank=True))
    if pdf_cue:
        page.insert_text((20, 290), "Equation 99")
    if vector:
        page.draw_rect(fitz.Rect(300, 200, 350, 250), color=(0, 0, 0))
    if rotation:
        page.set_rotation(rotation)
    doc.save(path)
    doc.close()


def _ocr_data(*, equation=True, duplicate_cue=False):
    rows = [
        ("Equation", 95, 20, 20, 44, 12, 1, 1, 1),
        ("1", 95, 68, 20, 6, 12, 1, 1, 1),
    ]
    if duplicate_cue:
        rows.extend(
            [
                ("Formula", 95, 200, 20, 36, 12, 2, 1, 1),
                ("2", 95, 240, 20, 6, 12, 2, 1, 1),
            ]
        )
    if equation:
        rows.extend(
            [
                ("x", 96, 20, 52, 6, 10, 1, 1, 2),
                ("+", 96, 30, 52, 6, 10, 1, 1, 2),
                ("y", 96, 40, 52, 6, 10, 1, 1, 2),
                ("=", 96, 50, 52, 6, 10, 1, 1, 2),
                ("2", 96, 60, 52, 6, 10, 1, 1, 2),
            ]
        )
    else:
        rows.extend(
            [
                ("This", 96, 20, 52, 18, 10, 1, 1, 2),
                ("is", 96, 42, 52, 8, 10, 1, 1, 2),
                ("prose", 96, 54, 52, 24, 10, 1, 1, 2),
            ]
        )
    keys = (
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
    return {key: [row[index] for row in rows] for index, key in enumerate(keys)}


class _OCR:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    def __call__(self, image, **kwargs):
        self.calls += 1
        assert image.size == (400, 300)
        assert kwargs == {
            "lang": OCR_LANGUAGE,
            "config": OCR_CONFIG,
            "timeout": OCR_TIMEOUT_SECONDS,
        }
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def _checker(data):
    ocr = _OCR(data)
    detector = RasterEquationRegionDetector(
        ocr_data=ocr,
        ocr_version=lambda: "5.5.1",
        ocr_tessdata_sha256=lambda: next(iter(SUPPORTED_ENG_TESSDATA_SHA256)),
    )
    return MathEquationChecker(region_detector=detector), detector, ocr


def _image_issues(path):
    return ImageAccessibilityChecker().check(str(path), {})


def test_supported_scan_yields_stable_manual_region_and_valid_evidence(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    checker, detector, ocr = _checker(_ocr_data())

    first = checker.find_image_equation_candidates(str(path), _image_issues(path))
    second = checker.find_image_equation_candidates(str(path), _image_issues(path))

    assert len(first) == 1
    assert first == second
    candidate = first[0]
    assert candidate["issue_type"] == SCANNED_EQUATION_REGION_ISSUE_TYPE
    assert candidate["metadata"]["source_kind"] == "page_raster_region"
    assert candidate["metadata"]["classification_manual_reason"]
    assert candidate["bbox"] != candidate["metadata"]["parent_bbox"]
    assert len(candidate["metadata"]["source_sha256"]) == 64
    assert len(candidate["metadata"]["crop_pixel_sha256"]) == 64
    assert detector.validate_evidence(str(path), candidate["metadata"])
    assert ocr.calls == 2


def test_region_evidence_rejects_source_or_geometry_drift(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    checker, detector, _ = _checker(_ocr_data())
    metadata = checker.find_image_equation_candidates(str(path), _image_issues(path))[
        0
    ]["metadata"]

    changed_source = {**metadata, "source_sha256": "0" * 64}
    changed_bbox = {**metadata, "pixel_bbox": [0, 0, 10, 10]}
    non_integer_bbox = {
        **metadata,
        "pixel_bbox": [float(value) for value in metadata["pixel_bbox"]],
    }
    changed_region_id = {**metadata, "region_id": "eqregion-v1-" + ("0" * 24)}

    assert not detector.validate_evidence(str(path), changed_source)
    assert not detector.validate_evidence(str(path), changed_bbox)
    assert not detector.validate_evidence(str(path), non_integer_bbox)
    assert not detector.validate_evidence(str(path), changed_region_id)


def test_detached_fraction_and_script_parts_are_inside_proven_crop(tmp_path):
    path = tmp_path / "detached-parts.pdf"
    _write_scan_pdf(path, image_bytes=_scan_png(detached_equation_parts=True))
    checker, detector, _ = _checker(_ocr_data())

    candidate = checker.find_image_equation_candidates(str(path), _image_issues(path))[
        0
    ]
    x0, y0, x1, y1 = candidate["metadata"]["pixel_bbox"]

    assert x0 <= 20
    assert y0 <= 40
    assert x1 >= 71
    assert y1 >= 73
    assert detector.validate_evidence(str(path), candidate["metadata"])


def test_bounded_cue_ocr_edge_ink_does_not_hide_a_valid_region(tmp_path):
    path = tmp_path / "cue-edge-ink.pdf"
    _write_scan_pdf(path, image_bytes=_scan_png(cue_edge_ink=True))
    checker, detector, _ = _checker(_ocr_data())

    candidate = checker.find_image_equation_candidates(str(path), _image_issues(path))[
        0
    ]

    assert detector.validate_evidence(str(path), candidate["metadata"])


def test_distant_ink_on_tolerated_cue_edge_row_is_rejected(tmp_path):
    path = tmp_path / "distant-cue-edge-ink.pdf"
    _write_scan_pdf(path, image_bytes=_scan_png(distant_cue_edge_ink=True))
    checker, _, ocr = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []
    assert ocr.calls == 1


def test_disconnected_ink_inside_cue_bbox_edge_row_is_rejected(tmp_path):
    path = tmp_path / "disconnected-cue-edge-ink.pdf"
    _write_scan_pdf(path, image_bytes=_scan_png(disconnected_cue_edge_ink=True))
    checker, _, ocr = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []
    assert ocr.calls == 1


@pytest.mark.parametrize(
    "data",
    [
        _ocr_data(equation=False),
        _ocr_data(duplicate_cue=True),
        RuntimeError("tesseract unavailable"),
        {"text": ["Equation"]},
    ],
)
def test_prose_ambiguous_and_ocr_failures_return_no_region(tmp_path, data):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(
        path, image_bytes=_scan_png(prose=data == _ocr_data(equation=False))
    )
    checker, _, _ = _checker(data)

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []


@pytest.mark.parametrize(
    "version,tessdata_sha256",
    [
        ("unknown", next(iter(SUPPORTED_ENG_TESSDATA_SHA256))),
        ("5.4.0", next(iter(SUPPORTED_ENG_TESSDATA_SHA256))),
        ("5.5.1", "0" * 64),
    ],
)
def test_unsupported_ocr_identity_fails_closed(tmp_path, version, tessdata_sha256):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    occurrence_detector = RasterEquationRegionDetector(
        ocr_data=_OCR(_ocr_data()),
        ocr_version=lambda: version,
        ocr_tessdata_sha256=lambda: tessdata_sha256,
    )
    checker = MathEquationChecker(region_detector=occurrence_detector)

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []


@pytest.mark.parametrize(
    "image_bytes",
    [
        _scan_png(blank=True),
        _scan_png(boundary_ink=True),
        _scan_png(noisy=True),
        _scan_png(distant_equation_number=True),
        _scan_png(vertical_gap_ink=True),
    ],
)
def test_blank_boundary_touching_and_excessive_ink_fail_closed(tmp_path, image_bytes):
    path = tmp_path / "unsafe-crop.pdf"
    _write_scan_pdf(path, image_bytes=image_bytes)
    checker, _, _ = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []


def test_full_page_scan_never_uses_whole_image_equation_path(tmp_path):
    path = tmp_path / "scan-with-pdf-cue.pdf"
    _write_scan_pdf(path, image_bytes=_scan_png(blank=True), pdf_cue=True)
    checker, _, _ = _checker({key: [] for key in _ocr_data()})

    candidates = checker.find_image_equation_candidates(str(path), _image_issues(path))

    assert candidates == []
    assert not any(
        item.get("issue_type") == IMAGE_EQUATION_ISSUE_TYPE for item in candidates
    )


def test_native_pdf_text_makes_page_mixed_and_skips_region_ocr(tmp_path):
    path = tmp_path / "mixed-native-text.pdf"
    _write_scan_pdf(path, pdf_cue=True)
    checker, _, ocr = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []
    assert ocr.calls == 0


@pytest.mark.parametrize("mixed,rotation", [(True, 0), (False, 90)])
def test_mixed_and_rotated_pages_fail_closed_before_ocr(tmp_path, mixed, rotation):
    path = tmp_path / f"unsupported-{mixed}-{rotation}.pdf"
    _write_scan_pdf(path, mixed=mixed, rotation=rotation)
    checker, _, ocr = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []
    assert ocr.calls == 0


def test_vector_overlay_fails_closed_before_ocr(tmp_path):
    path = tmp_path / "vector-overlay.pdf"
    _write_scan_pdf(path, vector=True)
    checker, _, ocr = _checker(_ocr_data())

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []
    assert ocr.calls == 0


def test_ocr_box_limit_rejects_without_partial_result(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    data = {key: values * (MAX_OCR_BOXES + 1) for key, values in _ocr_data().items()}
    checker, _, _ = _checker(data)

    assert checker.find_image_equation_candidates(str(path), _image_issues(path)) == []


def test_document_candidate_limit_discards_all_regions(tmp_path):
    path = tmp_path / "many-pages.pdf"
    doc = fitz.open()
    image = _scan_png(blank=True)
    pages = (MAX_CANDIDATES_PER_DOCUMENT // 5) + 1
    for _ in range(pages):
        page = doc.new_page(width=400, height=300)
        page.insert_image(page.rect, stream=image)
    doc.save(path)
    doc.close()

    class _OnePerPage:
        def find_regions(self, doc, page, occurrence):
            return [
                {"issue_type": SCANNED_EQUATION_REGION_ISSUE_TYPE} for _ in range(5)
            ]

    checker = MathEquationChecker(region_detector=_OnePerPage())

    assert checker.find_image_equation_candidates(str(path), []) == []


def test_scan_page_limit_refuses_document_before_any_ocr_work(tmp_path):
    path = tmp_path / "too-many-scan-pages.pdf"
    doc = fitz.open()
    image = _scan_png(blank=True)
    for _ in range(MAX_REGION_PAGES_PER_DOCUMENT + 1):
        page = doc.new_page(width=400, height=300)
        page.insert_image(page.rect, stream=image)
    doc.save(path)
    doc.close()

    class _ForbiddenDetector:
        def find_regions(self, doc, page, occurrence):
            pytest.fail("over-limit documents must be rejected before OCR")

    checker = MathEquationChecker(region_detector=_ForbiddenDetector())

    assert checker.find_image_equation_candidates(str(path), []) == []


def test_pixel_to_pdf_coordinate_mapping_is_exact():
    mapped = RasterEquationRegionDetector._map_to_pdf_bbox(
        (100, 50, 300, 150), (400, 300), (0.0, 0.0, 200.0, 150.0)
    )

    assert mapped == (50.0, 25.0, 150.0, 75.0)


def test_component_cap_counts_objects_not_horizontal_raster_runs():
    ink = np.zeros((600, 1200), dtype=bool)
    for x in range(10, 1190, 12):
        ink[50:550, x : x + 2] = True

    assert _bounded_component_count(ink) == 99


def test_region_issue_has_no_math_fixer_route(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    checker, _, _ = _checker(_ocr_data())
    candidate = checker.find_image_equation_candidates(str(path), _image_issues(path))[
        0
    ]

    assert math_issue_type_from(candidate) is None
    assert SCANNED_EQUATION_REGION_ISSUE_TYPE not in MathFixer.HANDLED_ISSUE_TYPES
    assert candidate["metadata"]["classification_manual_reason"] == (
        "scanned_equation_region_requires_exact_subregion_association"
    )

    direct = object.__new__(PdfRemediator)
    direct.config = RemediationConfig()
    normalized = direct._normalize_issues([candidate])[0]
    assert normalized.metadata["region_id"] == candidate["region_id"]
    assert normalized.metadata["classification_manual_reason"]


def test_authoritative_queue_partition_retains_bounded_region_evidence(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    checker, _, _ = _checker(_ocr_data())
    candidate = checker.find_image_equation_candidates(str(path), _image_issues(path))[
        0
    ]

    classification = classify_issue_category(candidate, authoritative=True)
    automatic, manual = _partition_authoritative_document_issues([candidate])
    records = materialize_manual_issues(
        manual, reason="partitioned_manual", purpose="document"
    )

    assert classification.category == IssueCategory.STRUCTURE
    assert classification.manual_reason == (
        "scanned_equation_region_requires_exact_subregion_association"
    )
    assert automatic == []
    assert manual == [candidate]
    assert len(records) == 1
    assert records[0].metadata["region_id"] == candidate["region_id"]
    assert (
        records[0].metadata["source_sha256"] == candidate["metadata"]["source_sha256"]
    )
    assert records[0].metadata["pdf_bbox"] == candidate["bbox"]


def test_pdf_remediator_honors_manual_only_region_before_any_fixer():
    issue = RemediationIssue(
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="manual region",
        metadata={
            "issue_type": SCANNED_EQUATION_REGION_ISSUE_TYPE,
            "classification_manual_reason": (
                "scanned_equation_region_requires_exact_subregion_association"
            ),
        },
    )
    remediator = object.__new__(PdfRemediator)
    recorded = []
    remediator._add_manual_issue = lambda *args, **kwargs: recorded.append(kwargs)
    remediator._is_category_enabled = lambda category: pytest.fail(
        "manual-only findings must stop before category dispatch"
    )

    remediator._process_issue(issue, object())

    assert recorded == [
        {
            "reason": "scanned_equation_region_requires_exact_subregion_association",
            "recommendation": "Review the issue category and apply the fix manually.",
        }
    ]


def test_standalone_equation_image_regression_remains_addressable(tmp_path):
    path = tmp_path / "standalone.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((72, 80), "Equation 1")
    page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_small_png())
    doc.save(path)
    doc.close()
    checker, _, ocr = _checker(_ocr_data())

    candidates = checker.find_image_equation_candidates(str(path), _image_issues(path))

    assert len(candidates) == 1
    assert candidates[0]["issue_type"] == IMAGE_EQUATION_ISSUE_TYPE
    assert ocr.calls == 0


def test_occurrence_geometry_is_exactly_reused_by_detector(tmp_path):
    path = tmp_path / "scan.pdf"
    _write_scan_pdf(path)
    with fitz.open(path) as doc:
        occurrence = _displayed_image_occurrences(doc[0], 1)[0]

    assert occurrence["bbox"] == (0.0, 0.0, 400.0, 300.0)
