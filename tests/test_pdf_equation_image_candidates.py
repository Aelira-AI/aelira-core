from io import BytesIO

import fitz
import pytest
from PIL import Image

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.image_checker import ImageAccessibilityChecker
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_checker import _occurrence_alt_lookup
from src.education.pdf_checks.math_checker import MathEquationChecker


class _ForbiddenImageGenerator:
    def __getattr__(self, name):
        raise AssertionError(
            f"AI provider must not be used during scan-only discovery: {name}"
        )


def _png_bytes():
    output = BytesIO()
    Image.new("RGB", (120, 40), "white").save(output, format="PNG")
    return output.getvalue()


def _write_pdf(path, *, duplicate=False, cross_page=False, cue="Equation 1"):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((72, 80), cue)
    xref = page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png_bytes())
    if duplicate:
        page.insert_image(fitz.Rect(72, 150, 192, 190), xref=xref)
    if cross_page:
        page = doc.new_page(width=400, height=300)
        page.insert_text((72, 80), cue)
        page.insert_image(fitz.Rect(72, 90, 192, 130), xref=xref)
    doc.save(path)
    doc.close()


def _scan(path):
    return ImageAccessibilityChecker(
        generate_alt_text=False,
        validate_alt_text=False,
        image_generator=_ForbiddenImageGenerator(),
    ).check(str(path), {})


def test_scan_only_discovers_every_displayed_occurrence_without_ai(tmp_path):
    path = tmp_path / "duplicates.pdf"
    _write_pdf(path, duplicate=True, cross_page=True)

    first = _scan(path)
    second = _scan(path)

    assert len(first) == 3
    assert [issue.occurrence_id for issue in first] == [
        issue.occurrence_id for issue in second
    ]
    assert len({issue.occurrence_id for issue in first}) == 3
    assert len({issue.image_xref for issue in first}) == 1
    page_one = [issue for issue in first if issue.page_number == 1]
    assert [issue.occurrence_ordinal for issue in page_one] == [0, 1]
    assert [issue.image_index for issue in page_one] == [0, 1]
    assert page_one[0].bbox != page_one[1].bbox


def test_only_exact_occurrences_with_local_equation_cues_become_candidates(tmp_path):
    equation = tmp_path / "equation.pdf"
    figure = tmp_path / "figure.pdf"
    _write_pdf(equation, cue="Equation 1")
    _write_pdf(figure, cue="Figure 1")
    checker = MathEquationChecker()

    candidates = checker.find_image_equation_candidates(str(equation), _scan(equation))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["issue_type"] == IMAGE_EQUATION_ISSUE_TYPE
    assert candidate["category"] == "structure"
    assert candidate["rule"] == "WCAG 1.1.1"
    for field in (
        "page_number",
        "image_xref",
        "image_index",
        "occurrence_ordinal",
        "bbox",
        "occurrence_id",
    ):
        assert candidate[field] is not None
    assert checker.find_image_equation_candidates(str(figure), _scan(figure)) == []


def test_unaddressable_occurrences_are_left_manual():
    from src.education.pdf_checks.models import PDFImageIssue

    fields = PDFImageIssue.model_fields
    assert fields["image_xref"].is_required()
    assert fields["bbox"].is_required()
    assert fields["occurrence_id"].is_required()


def test_occurrence_identity_is_immutable(tmp_path):
    path = tmp_path / "immutable.pdf"
    _write_pdf(path)
    issue = _scan(path)[0]

    with pytest.raises(Exception):
        issue.occurrence_id = "xref-only"


def test_invalid_xrefs_and_missing_bboxes_are_not_addressable_candidates():
    class _Page:
        def get_images(self, full=True):
            return [(7,)]

        def get_image_info(self, xrefs=True):
            return [
                {"xref": 7, "bbox": (10.25, 20.5, 90.75, 55.125)},
                {"xref": 0, "bbox": (0, 0, 10, 10)},
                {"xref": 999, "bbox": (0, 0, 10, 10)},
                {"xref": 7, "bbox": None},
                {"xref": 7, "bbox": (0, 0, float("nan"), 10)},
            ]

    occurrences = _displayed_image_occurrences(_Page(), 3)

    assert len(occurrences) == 1
    assert occurrences[0]["image_xref"] == 7
    assert occurrences[0]["bbox"] == (10.25, 20.5, 90.75, 55.125)


def test_display_index_does_not_depend_on_resource_alias_order():
    class _Page:
        def get_images(self, full=True):
            return [(7,), (7,)]

        def get_image_info(self, xrefs=True):
            return [{"xref": 7, "bbox": (10.0, 20.0, 90.0, 55.0)}]

    occurrences = _displayed_image_occurrences(_Page(), 1)

    assert len(occurrences) == 1
    assert occurrences[0]["image_index"] == 0


def test_invalid_earlier_draw_does_not_shift_later_resource_index():
    class _Page:
        def get_images(self, full=True):
            return [(7,), (7,)]

        def get_image_info(self, xrefs=True):
            return [
                {"xref": 7, "bbox": None},
                {"xref": 7, "bbox": (10.0, 20.0, 90.0, 55.0)},
            ]

    occurrences = _displayed_image_occurrences(_Page(), 1)

    assert len(occurrences) == 1
    assert occurrences[0]["image_index"] == 1
    assert occurrences[0]["occurrence_ordinal"] == 1


def test_alt_lookup_is_occurrence_specific_and_parses_page_once():
    class _Doc:
        def xref_object(self, xref):
            return ""

    class _Page:
        calls = 0

        def get_text(self, mode):
            assert mode == "dict"
            self.calls += 1
            return {
                "blocks": [
                    {
                        "type": 1,
                        "xref": 7,
                        "bbox": (10.0, 20.0, 90.0, 55.0),
                        "alt": "first occurrence",
                    }
                ]
            }

    occurrences = [
        {
            "image_xref": 7,
            "bbox": (10.0, 20.0, 90.0, 55.0),
            "occurrence_id": "first",
        },
        {
            "image_xref": 7,
            "bbox": (10.0, 70.0, 90.0, 105.0),
            "occurrence_id": "second",
        },
    ]
    page = _Page()

    lookup = _occurrence_alt_lookup(_Doc(), page, occurrences)

    assert page.calls == 1
    assert lookup["first"] == (True, "first occurrence")
    assert lookup["second"] == (False, None)


def test_alt_lookup_does_not_parse_pages_without_images():
    class _Page:
        def get_text(self, mode):
            raise AssertionError("empty image pages must not be parsed")

    assert _occurrence_alt_lookup(object(), _Page(), []) == {}


def test_alt_lookup_parse_failure_keeps_occurrences_missing_and_scan_continuable():
    class _Doc:
        def xref_object(self, xref):
            return ""

    class _Page:
        def get_text(self, mode):
            raise RuntimeError("malformed page")

    occurrence = {
        "image_xref": 7,
        "bbox": (10.0, 20.0, 90.0, 55.0),
        "occurrence_id": "affected",
    }

    assert _occurrence_alt_lookup(_Doc(), _Page(), [occurrence]) == {
        "affected": (False, None)
    }
