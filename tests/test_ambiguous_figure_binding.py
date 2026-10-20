"""A description is written only where it can be bound to one image alone.

A generated Figure records the rectangle of the draw it describes, and the
content tagger binds it to the block that rectangle fits. Two images drawn on
top of each other fit the same rectangle, an image inside marked content is not
a block at all, and a draw that cannot be resolved to one drawing operation has
no rectangle. In each of those cases the finding is refused with its reason
before anything is written: it is never recorded as fixed, no Figure carries a
description that no image holds, and no HTML sidecar is produced.

The integration tests run the real scanner, remediator, tagger and saved-file
rescan. The provider describes images by the pixel width that identifies them.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name
from PIL import Image

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import structured_image_semantics
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.outcome_explanations import (
    EXPLANATIONS,
    manual_explanation,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pdf_structure import PDFStructureTree
from tests.test_generated_figure_binding import (
    _description,
    _ImageProvider,
    _phantom_figures,
    _remediate,
)

pytestmark = pytest.mark.unit

BODY = "Readable course material. " * 4
FIRST = (100.0, 600.0, 300.0, 700.0)
SECOND = (120.0, 600.0, 320.0, 700.0)  # 20 across from FIRST: overlap 0.818
APART = (200.0, 600.0, 400.0, 700.0)  # overlap 0.333 with FIRST


def _png(width: int, height: int = 40) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (width % 256, 90, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_draws(path: Path, rects: list[tuple[float, ...]]) -> None:
    """Distinct images, identified by pixel width 60, 70, ..., drawn at ``rects``."""
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        for index, rect in enumerate(rects):
            page.insert_image(fitz.Rect(*rect), stream=_png(60 + 10 * index))
        page.insert_text((72, 40), BODY, fontsize=4)
        doc.save(path)


def _write_same_image_twice(path: Path, rect: tuple[float, ...]) -> None:
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        xref = page.insert_image(fitz.Rect(*rect), stream=_png(60))
        page.insert_image(fitz.Rect(*rect), xref=xref)
        page.insert_text((72, 40), BODY, fontsize=4)
        doc.save(path)


def _wrap_first_draw(path: Path, opener: pikepdf.ContentStreamInstruction) -> None:
    """Put the first image's ``q ... Do Q`` inside one marked-content scope."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        do = next(i for i, op in enumerate(ops) if str(op.operator) == "Do")
        start = max(i for i in range(do) if str(ops[i].operator) == "q")
        end = next(i for i in range(do, len(ops)) if str(ops[i].operator) == "Q")
        emc = pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))
        ops = ops[:start] + [opener] + ops[start : end + 1] + [emc] + ops[end + 1 :]
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def _instruction(operator: str, *operands: Any) -> pikepdf.ContentStreamInstruction:
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


# What the first image is enclosed in. Each makes it content the tagger leaves
# alone: the tagger makes blocks only of draws outside every scope.
MARKED_CONTENT = {
    "artifact_with_a_property_list": _instruction(
        "BDC", Name.Artifact, Dictionary(Type=Name.Pagination)
    ),
    "plain_artifact": _instruction("BMC", Name.Artifact),
    "span": _instruction("BMC", Name.Span),
}


def _figures(path: str) -> list[tuple[str | None, bool]]:
    """Every Figure of the saved file as (alt, whether it holds content)."""
    found: list[tuple[str | None, bool]] = []

    def walk(node: Any) -> None:
        if isinstance(node, Dictionary) and "/S" in node:
            if str(node.S) == "/Figure":
                found.append((str(node.Alt) if "/Alt" in node else None, "/K" in node))
            kids = node.get("/K")
            for kid in list(kids) if isinstance(kids, Array) else [kids]:
                walk(kid)

    with pikepdf.open(path) as pdf:
        for kid in list(pdf.Root.StructTreeRoot.K):
            walk(kid)
    return found


def _carried(path: str) -> list[tuple[bool, str | None]]:
    """What each displayed image carries, in display order."""
    with fitz.open(path) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
    semantics = structured_image_semantics(path, 0, occurrences)
    assert semantics is not None
    return [semantics[item["occurrence_id"]] for item in occurrences]


def _reasons(result: Any, remediator: PdfRemediator) -> dict[int, str]:
    """The manual reason of each image's missing-alt finding, by image index."""
    by_issue = {item.issue_id: item.reason for item in result.manual_issues}
    return {
        issue.metadata["image_index"]: by_issue[issue.id]
        for issue in remediator.issues
        if issue.metadata.get("issue_type") == "missing_alt_text"
        and issue.id in by_issue
    }


def _fixed_alt_ids(result: Any) -> set[str]:
    return {
        fix.issue_id for fix in result.fixed_issues if fix.category.value == "alt_text"
    }


def _no_sidecar(remediator: PdfRemediator, result: Any) -> None:
    assert remediator._html_output is None
    assert not getattr(remediator, "_alt_texts", {})
    assert [
        item.name
        for item in Path(result.output_file).parent.iterdir()
        if item.suffix == ".html"
    ] == []


# ---------------------------------------------------------------------------
# Images drawn on top of each other
# ---------------------------------------------------------------------------

OVERLAPPING = [
    pytest.param([FIRST, SECOND], {1}, id="partial-overlap-second-described"),
    pytest.param([FIRST, SECOND], {0}, id="partial-overlap-first-described"),
    pytest.param([FIRST, SECOND], {0, 1}, id="partial-overlap-both-described"),
    pytest.param([FIRST, FIRST], {1}, id="coincident-second-described"),
    pytest.param([FIRST, FIRST], {0, 1}, id="coincident-both-described"),
]


@pytest.mark.parametrize(("rects", "described"), OVERLAPPING)
def test_overlapping_draws_get_no_description_and_nothing_is_misplaced(
    tmp_path: Path, rects: list[tuple[float, ...]], described: set[int]
) -> None:
    """A box that fits one of two overlapping images fits both, so none is bound.

    Before this, the tagger took the first draw it visited and the description of
    the second image landed on the first image's Figure.
    """
    source = tmp_path / "source.pdf"
    _write_draws(source, rects)

    remediator, result = _remediate(source, tmp_path, _ImageProvider(described))
    try:
        assert result.success, result.error_message
        reasons = _reasons(result, remediator)
        assert set(reasons) == {0, 1}
        # Each described image is refused with the reason, never credited.
        for index in described:
            assert reasons[index] == "image_draw_ambiguous"
        assert not _fixed_alt_ids(result)
        # Nothing is attached to either image, and no Figure holds a description.
        assert _carried(result.output_file) == [(False, None), (False, None)]
        assert [alt for alt, _ in _figures(result.output_file)] == [None, None]
        assert _phantom_figures(result.output_file) == []
        _no_sidecar(remediator, result)
        # Both images are recorded as manual work, so the file still publishes.
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
    finally:
        result.close_output_claim()


def test_the_same_image_drawn_twice_in_one_place_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _write_same_image_twice(source, FIRST)

    remediator, result = _remediate(source, tmp_path, _ImageProvider({0}))
    try:
        assert result.success, result.error_message
        assert _reasons(result, remediator) == {
            0: "image_draw_ambiguous",
            1: "image_draw_ambiguous",
        }
        assert not _fixed_alt_ids(result)
        assert [alt for alt, _ in _figures(result.output_file)] == [None, None]
        assert _phantom_figures(result.output_file) == []
        _no_sidecar(remediator, result)
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


def test_draws_that_overlap_less_than_the_threshold_still_bind(tmp_path: Path) -> None:
    """The refusal is for draws one box could fit, not for every near neighbour."""
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST, APART])

    remediator, result = _remediate(source, tmp_path, _ImageProvider({0, 1}))
    try:
        assert result.success, result.error_message
        assert _carried(result.output_file) == [
            (True, _description(0)),
            (True, _description(1)),
        ]
        assert _reasons(result, remediator) == {}
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# A draw inside marked content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(MARKED_CONTENT))
def test_an_image_inside_marked_content_is_refused_and_leaves_no_phantom_figure(
    tmp_path: Path, case: str
) -> None:
    """The tagger makes no block of a draw inside a scope, so nothing could bind.

    Before this, a Figure with a box and an /Alt was written for such an image,
    never bound, and left in the saved file with no content, while the finding
    was credited as fixed.
    """
    source = tmp_path / "source.pdf"
    _write_draws(source, [(50.0, 600.0, 150.0, 700.0), (250.0, 600.0, 350.0, 700.0)])
    _wrap_first_draw(source, MARKED_CONTENT[case])

    remediator, result = _remediate(source, tmp_path, _ImageProvider({0, 1}))
    try:
        assert result.success, result.error_message
        assert _reasons(result, remediator) == {0: "image_in_marked_content"}
        # Only the plain image is described, on a Figure that holds its content.
        assert _figures(result.output_file) == [(_description(1), True)]
        assert _phantom_figures(result.output_file) == []
        assert _fixed_alt_ids(result) == {
            issue.id
            for issue in remediator.issues
            if issue.metadata.get("image_index") == 1
            and issue.metadata.get("issue_type") == "missing_alt_text"
        }
        _no_sidecar(remediator, result)
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# A draw that cannot be resolved: refused, never recorded as fixed
#
# A whole run refuses a page with a Form XObject, or with a zero-size draw,
# before any description is placed, and a stream whose marked content does not
# balance is covered with the equation tests. The step that places the
# description is therefore called directly here.
# ---------------------------------------------------------------------------


def _write_form_nested(path: Path) -> None:
    """An image drawn once directly and once more inside a Form XObject."""
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(72, 100, 172, 140), stream=_png(60))
        page.insert_text((72, 40), BODY, fontsize=4)
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        xobjects = page.Resources.XObject
        image = next(iter(xobjects.values()))
        form = pdf.make_stream(b"q 100 0 0 40 0 0 cm /Inner Do Q")
        form.Type = Name.XObject
        form.Subtype = Name.Form
        form.BBox = Array([0, 0, 100, 40])
        form.Resources = Dictionary(XObject=Dictionary({"/Inner": image}))
        xobjects["/FmNested"] = form
        streams = page.obj.Contents
        streams = list(streams) if isinstance(streams, Array) else [streams]
        drawing = b"".join(stream.read_bytes() for stream in streams)
        page.obj.Contents = pdf.make_stream(
            drawing + b" q 1 0 0 1 72 300 cm /FmNested Do Q"
        )
        pdf.save(path)


def _prepared(
    source: Path, tmp_path: Path
) -> tuple[PdfRemediator, pikepdf.Pdf, fitz.Document]:
    """A remediator ready to place one description in a generated structure."""
    scan = PDFProcessor().process_pdf(str(source))
    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False, create_backup=False, output_directory=str(tmp_path / "out")
        ),
    )
    pdf = pikepdf.open(source)
    remediator._struct_tree = PDFStructureTree(pdf)
    remediator._generated_structure = True
    return remediator, pdf, fitz.open(source)


def _alt_issue(remediator: PdfRemediator, index: int):
    return next(
        issue
        for issue in remediator.issues
        if issue.metadata.get("issue_type") == "missing_alt_text"
        and issue.metadata.get("image_index") == index
    )


def _structure_figures(pdf: pikepdf.Pdf) -> list[Any]:
    return [
        kid
        for kid in list(pdf.Root.StructTreeRoot.K)
        if isinstance(kid, Dictionary) and str(kid.get("/S")) == "/Figure"
    ]


def test_an_image_also_drawn_inside_a_form_is_refused_without_a_sidecar(
    tmp_path: Path,
) -> None:
    """The direct draws and the displayed occurrences disagree in number.

    A whole run refuses a page with a Form XObject earlier still, so the step
    that places the description is called directly, with its own state.
    """
    source = tmp_path / "source.pdf"
    _write_form_nested(source)
    remediator, pdf, document = _prepared(source, tmp_path)
    try:
        issue = _alt_issue(remediator, 0)
        assert remediator._apply_alt_text_fix(issue, document, "A description") is False
        assert remediator._source_binding_refusals[issue.id] == "image_draw_unresolved"
        assert remediator._html_output is None
        assert not getattr(remediator, "_alt_texts", {})
        assert _structure_figures(pdf) == []
        assert not remediator.result.fixed_issues
    finally:
        document.close()
        pdf.close()


# ---------------------------------------------------------------------------
# A finding with no occurrence identity
# ---------------------------------------------------------------------------


@contextmanager
def _unidentified(source: Path, tmp_path: Path):
    remediator, pdf, document = _prepared(source, tmp_path)
    try:
        issue = _alt_issue(remediator, 0)
        issue.metadata.pop("occurrence_id")
        yield remediator, pdf, document, issue
    finally:
        document.close()
        pdf.close()


def test_an_unidentified_finding_is_refused_when_the_page_has_other_images(
    tmp_path: Path,
) -> None:
    """With no identity the tagger could only pair the Figure by order."""
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST, APART])
    with _unidentified(source, tmp_path) as (remediator, pdf, document, issue):
        assert remediator._apply_alt_text_fix(issue, document, "A description") is False
        assert (
            remediator._source_binding_refusals[issue.id]
            == "image_occurrence_unidentified"
        )
        assert _structure_figures(pdf) == []
        assert remediator._html_output is None


def test_an_unidentified_finding_is_written_for_the_pages_single_image(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    _write_draws(source, [FIRST])
    with _unidentified(source, tmp_path) as (remediator, pdf, document, issue):
        assert remediator._apply_alt_text_fix(issue, document, "A description") is True
        (figure,) = _structure_figures(pdf)
        assert str(figure.Alt) == "A description"
        # No position is recorded: the one image block is the only candidate.
        assert "/A" not in figure
        assert not getattr(remediator, "_source_binding_refusals", {})


# ---------------------------------------------------------------------------
# The refusals are explained to the user
# ---------------------------------------------------------------------------

REASONS = [
    "image_draw_unresolved",
    "image_draw_ambiguous",
    "image_in_marked_content",
    "image_occurrence_unidentified",
]


@pytest.mark.parametrize("code", REASONS)
def test_each_refusal_reason_has_its_own_public_explanation(code: str) -> None:
    reason, next_step, attempt = EXPLANATIONS[code]
    assert reason.endswith(".") and next_step.endswith(".")
    assert attempt == "not_applied"
    explained = manual_explanation({"reason": code, "issue_id": "x"})
    assert explained["reason_code"] == code
    assert explained["reason"] == reason
    assert "\n" not in reason and "/" not in reason
