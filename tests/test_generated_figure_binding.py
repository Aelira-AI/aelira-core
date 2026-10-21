"""A generated image description is bound to the exact image it describes.

The alt-text pass writes a root-level Figure for each description and the
content tagger later binds it to a draw in the page content. The tagger used to
pair them by order of appearance, so when an earlier image stayed undescribed
the next image's description landed on the wrong image's Figure. A Figure now
records the box of the one image it describes, in default user space, and binds
only to the image block that box fits and nothing else. A Figure that carries a
box is never paired by order, and images that one box could fit are refused
(see test_ambiguous_figure_binding).

The integration tests run the real scanner, remediator and saved-file rescan.
The provider is the only stand-in, and it answers each image by the pixel width
that identifies it, so a description can be traced to the image it was written
for and checked against the image that carries it.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name
from PIL import Image

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import (
    FigureFailureEvidence,
    missing_figure_occurrences,
    structured_image_semantics,
)
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.content_tagger_v2 import (
    ContentTaggerV2,
    _image_draw_rects,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pdf_structure import PDFStructureTree
from tests.test_equation_alt_text_coordination import _structure
from src.education.validation.matterhorn import MatterhornValidator

pytestmark = pytest.mark.unit

NAMES = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
BODY = "Readable course material. " * 4


def _description(index: int) -> str:
    return f"A picture of the {NAMES[index]} sample"


class _ImageProvider:
    """Describes some images and cannot describe the rest, told apart by width."""

    purpose = "alt_text"
    provider = "test-provider"
    model = "test-model"

    def __init__(self, described: set[int]) -> None:
        self.described = described
        self.calls: list[int] = []

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        with Image.open(io.BytesIO(image_data)) as image:
            index = (image.width - 60) // 10
        self.calls.append(index)
        if index in self.described:
            return {"success": True, "content": _description(index)}
        return {"success": False, "content": ""}


def _write_source(
    path: Path,
    count: int,
    *,
    rotate: int = 0,
    media_box: list[int] | None = None,
) -> None:
    """An untagged page of ``count`` images, each identified by its width.

    ``rotate`` and ``media_box`` change how the page is viewed. A media box that
    does not start at the origin moves the whole drawing with it.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=100 * count + 120)
        for index in range(count):
            width = 60 + 10 * index
            buffer = io.BytesIO()
            Image.new("RGB", (width, 40), (40 * index % 256, 90, 200)).save(
                buffer, format="PNG"
            )
            top = 20 + 100 * index
            page.insert_image(
                fitz.Rect(72, top, 72 + width, top + 40), stream=buffer.getvalue()
            )
        page.insert_text((72, 100 * count + 60), BODY, fontsize=4)
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        if rotate:
            page.obj.Rotate = rotate
        if media_box is not None:
            page.obj.MediaBox = Array(media_box)
            page.obj.CropBox = Array(media_box)
            streams = page.obj.Contents
            streams = list(streams) if isinstance(streams, Array) else [streams]
            drawing = b"".join(stream.read_bytes() for stream in streams)
            page.obj.Contents = pdf.make_stream(
                b"q 1 0 0 1 %d %d cm " % (media_box[0], media_box[1]) + drawing + b" Q"
            )
        pdf.save(path)


def _remediate(source: Path, tmp_path: Path, provider: Any):
    scan = PDFProcessor().process_pdf(str(source))
    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=True,
            allow_legacy_nested_ai=False,
            create_backup=False,
            verify_fixes=True,
            output_directory=str(tmp_path / "out"),
        ),
        ai_client=None,
        alt_text_client=provider,
    )
    return remediator, remediator.remediate()


def _carried(path: str) -> dict[int, tuple[bool, str | None]]:
    """What each image of the saved file carries, keyed by the image itself.

    The key is the width of the embedded image, read from the saved file, so the
    answer follows the occurrence's own identity and not its position.
    """
    with fitz.open(path) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
        widths = {
            item["occurrence_id"]: doc.extract_image(item["image_xref"])["width"]
            for item in occurrences
        }
    semantics = structured_image_semantics(path, 0, occurrences)
    assert semantics is not None
    return {(widths[key] - 60) // 10: value for key, value in semantics.items()}


# ---------------------------------------------------------------------------
# End to end: every description sits on the image it was written for
# ---------------------------------------------------------------------------

LAYOUTS = [
    "DD",
    "DU",
    "UD",
    "DUD",
    "UDU",
    "UUD",
    "DUU",
    "UDDU",
    "DUDU",
    "UDUD",
    "DUDUD",
    "UDUDU",
    "UUDUUD",
]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_each_description_sits_on_the_image_it_was_written_for(
    tmp_path: Path, layout: str
) -> None:
    source = tmp_path / "source.pdf"
    _write_source(source, len(layout))
    described = {index for index, mark in enumerate(layout) if mark == "D"}
    provider = _ImageProvider(described)

    remediator, result = _remediate(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert sorted(provider.calls) == list(range(len(layout)))

        carried = _carried(result.output_file)
        for index in range(len(layout)):
            expected = (
                (True, _description(index)) if index in described else (False, None)
            )
            assert carried[index] == expected, (layout, index)

        # Each fix names its own image, and every other image is manual work.
        fixed_text = {
            fix.issue_id: fix.fixed_content
            for fix in result.fixed_issues
            if fix.category.value == "alt_text"
        }
        manual_ids = {item.issue_id for item in result.manual_issues}
        for issue in remediator.issues:
            if issue.metadata.get("issue_type") != "missing_alt_text":
                continue
            index = issue.metadata["image_index"]
            if index in described:
                assert fixed_text[issue.id] == _description(index)
            else:
                assert issue.id in manual_ids and issue.id not in fixed_text

        # The saved file verifies, so nothing is withheld. A layout with an
        # undescribed image passes only through the untagged-source proof.
        assert result.verification_passed, (layout, result.verification_result)
        assert _phantom_figures(result.output_file) == []
        assert result.verification_result.unavailable_checks == []
        undescribed = len(layout) - len(described)
        if undescribed:
            checkpoint = next(
                item
                for item in MatterhornValidator()
                .validate(result.output_file)
                .checkpoints
                if item.id == "13-004"
            )
            assert checkpoint.details == (
                f"{undescribed} of {len(layout)} figures missing /Alt or /ActualText"
            )
    finally:
        result.close_output_claim()


PAGES = [
    pytest.param(0, None, id="plain"),
    pytest.param(90, None, id="rotated-90"),
    pytest.param(180, None, id="rotated-180"),
    pytest.param(270, None, id="rotated-270"),
    pytest.param(0, [20, 30, 420, 330 + 0], id="offset-media-box"),
    pytest.param(90, [20, 30, 420, 330 + 0], id="rotated-90-offset-media-box"),
    pytest.param(270, [-40, -60, 360, 240], id="rotated-270-negative-origin"),
]


@pytest.mark.parametrize(("rotate", "media_box"), PAGES)
def test_descriptions_bind_to_their_images_on_any_page(
    tmp_path: Path, rotate: int, media_box: list[int] | None
) -> None:
    """Rotation and a media box that starts away from the origin change nothing.

    The Figure's rectangle and the tagger's rectangle for each draw both come
    from the content stream's own matrices, so they stay in the same space on
    any page, and the descriptions still land on the images they were written
    for when an image before them stays undescribed.
    """
    source = tmp_path / "source.pdf"
    _write_source(source, 3, rotate=rotate, media_box=media_box)

    remediator, result = _remediate(source, tmp_path, _ImageProvider({1, 2}))
    try:
        assert result.success, result.error_message
        carried = _carried(result.output_file)
        assert carried[0] == (False, None)
        assert carried[1] == (True, _description(1))
        assert carried[2] == (True, _description(2))
        assert result.verification_passed, result.verification_result
    finally:
        result.close_output_claim()


def test_figure_box_is_the_draw_rectangle_in_user_space(tmp_path: Path) -> None:
    """The box written on a Figure is the rectangle the tagger compares against."""
    source = tmp_path / "source.pdf"
    _write_source(source, 3)

    remediator, result = _remediate(source, tmp_path, _ImageProvider({0, 2}))
    try:
        assert result.success, result.error_message
        boxes: dict[str, tuple[float, ...]] = {}

        def walk(node: Any) -> None:
            if isinstance(node, Dictionary) and "/S" in node:
                layout = node.get("/A")
                if str(node.S) == "/Figure" and layout is not None:
                    assert str(layout.O) == "/Layout"
                    boxes[str(node.Alt)] = tuple(float(v) for v in layout.BBox)
                kids = node.get("/K")
                for kid in list(kids) if isinstance(kids, Array) else [kids]:
                    walk(kid)

        with (
            pikepdf.open(result.output_file) as pdf,
            fitz.open(result.output_file) as doc,
        ):
            ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
            drawn = list(_image_draw_rects(ops).values())
            height = doc[0].rect.height
            seen = [item["bbox"] for item in _displayed_image_occurrences(doc[0], 1)]
            for kid in list(pdf.Root.StructTreeRoot.K):
                walk(kid)

        # The Figures of the two described images carry their own rectangles.
        assert set(boxes) == {_description(0), _description(2)}
        for index in (0, 2):
            box = boxes[_description(index)]
            x0, y0, x1, y1 = seen[index]
            # An unrotated page: user space is the viewer's box turned upside
            # down, the same flip the tagger applies to the text blocks.
            assert box == pytest.approx((x0, height - y1, x1, height - y0), abs=1e-3)
            assert any(rect == pytest.approx(box, abs=1e-3) for rect in drawn)
    finally:
        result.close_output_claim()


def test_a_box_on_a_figure_changes_no_scanner_or_matterhorn_result(
    tmp_path: Path,
) -> None:
    """A Layout /BBox on a Figure is ordinary structure data.

    The same output with every Figure's /A removed scans and validates the
    same, so recording the box affects neither PDF/UA nor the checks.
    """
    source = tmp_path / "source.pdf"
    _write_source(source, 3)

    remediator, result = _remediate(source, tmp_path, _ImageProvider({0, 2}))
    try:
        assert result.success, result.error_message
        stripped = tmp_path / "stripped.pdf"
        with pikepdf.open(result.output_file) as pdf:
            removed = 0

            def walk(node: Any) -> None:
                nonlocal removed
                if isinstance(node, Dictionary) and "/S" in node:
                    if str(node.S) == "/Figure" and "/A" in node:
                        del node["/A"]
                        removed += 1
                    kids = node.get("/K")
                    for kid in list(kids) if isinstance(kids, Array) else [kids]:
                        walk(kid)

            for kid in list(pdf.Root.StructTreeRoot.K):
                walk(kid)
            assert removed == 2
            pdf.save(stripped)

        def checkpoints(path: str) -> list[Any]:
            return sorted(
                (
                    item.id,
                    item.status.value,
                    item.severity,
                    item.details,
                    item.page_number,
                )
                for item in MatterhornValidator().validate(path).checkpoints
            )

        def findings(path: str) -> list[Any]:
            return sorted(
                (str(issue.get("issue_type")), str(issue.get("message")))
                for issue in PDFProcessor().process_pdf(path).issues
            )

        assert checkpoints(result.output_file) == checkpoints(str(stripped))
        assert findings(result.output_file) == findings(str(stripped))
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# The tagger on its own
# ---------------------------------------------------------------------------


def test_tagger_binds_a_positioned_figure_to_its_own_draw(tmp_path: Path) -> None:
    """The first image has no Figure and the second has one, listed first."""
    source = tmp_path / "source.pdf"
    _write_source(source, 2)
    with pikepdf.open(source) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        box = list(_image_draw_rects(ops).values())[1]
        PDFStructureTree(pdf).add_alt_text_to_image(
            page_num=1, alt_text="The second image", image_index=1, image_bbox=box
        )
        staged = tmp_path / "staged.pdf"
        pdf.save(staged)

    output = tmp_path / "tagged.pdf"
    with pikepdf.open(staged) as pdf, fitz.open(staged) as doc:
        stats = ContentTaggerV2(pdf, doc).tag_all_pages()
        pdf.save(output)

    # The Figure binds to the second draw. The first image and the page text
    # each get an element of their own.
    assert stats["blocks_matched"] == 1
    carried = _carried(str(output))
    assert carried[0] == (False, None)
    assert carried[1] == (True, "The second image")
    assert missing_figure_occurrences(str(output)) == FigureFailureEvidence(
        frozenset({(1, 0)}), frozenset({(1, 1)}), 2
    )


def test_tagger_pairs_an_unpositioned_figure_by_order_as_before(
    tmp_path: Path,
) -> None:
    """A single image or a hand-built structure carries no box and still binds."""
    source = tmp_path / "source.pdf"
    _write_source(source, 1)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf).add_alt_text_to_image(
            page_num=1, alt_text="The only image", image_index=0
        )
        staged = tmp_path / "staged.pdf"
        pdf.save(staged)
    output = tmp_path / "tagged.pdf"
    with pikepdf.open(staged) as pdf, fitz.open(staged) as doc:
        stats = ContentTaggerV2(pdf, doc).tag_all_pages()
        pdf.save(output)

    # The Figure is reused, not replaced: only the page text gets a new element.
    assert stats["blocks_matched"] == 1 and stats["blocks_created"] == 1
    assert _carried(str(output))[0] == (True, "The only image")


def _tagger(tmp_path: Path) -> ContentTaggerV2:
    source = tmp_path / "matcher.pdf"
    _write_source(source, 1)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        pdf.save(tmp_path / "matcher-tree.pdf")
    pdf = pikepdf.open(tmp_path / "matcher-tree.pdf")
    return ContentTaggerV2(pdf, fitz.open(tmp_path / "matcher-tree.pdf"))


def _figure(*box: float, role: str = "/Figure") -> Dictionary:
    element = Dictionary(S=Name(role))
    if box:
        element.A = Dictionary(O=Name.Layout, BBox=Array(list(box)))
    return element


RECT = (0.0, 0.0, 100.0, 100.0)


def test_matcher_binds_only_the_claimed_figure_and_never_a_positioned_one_by_order(
    tmp_path: Path,
) -> None:
    tagger = _tagger(tmp_path)
    elements = [_figure(500, 500, 600, 600), _figure(0, 0, 100, 98)]
    used: set = set()
    element, kind, confidence = tagger._match_figure_to_image(elements, used, 1)
    assert element is elements[1] and kind == "position" and confidence == 0.90
    assert used == {1}
    # Nothing claimed: a positioned Figure is never taken by order.
    assert (
        tagger._match_figure_to_image([_figure(500, 500, 600, 600)], set(), None)
        is None
    )
    # A claim already used is not bound twice.
    assert tagger._match_figure_to_image(elements, {1}, 1) is None


def test_matcher_pairs_an_unpositioned_figure_by_order_past_positioned_ones(
    tmp_path: Path,
) -> None:
    tagger = _tagger(tmp_path)
    elements = [_figure(500, 500, 600, 600), _figure(), _figure(role="/P")]
    used: set = set()
    element, kind, _ = tagger._match_figure_to_image(elements, used, None)
    assert element is elements[1] and kind == "ordinal" and used == {1}
    assert tagger._match_figure_to_image(elements, used, None) is None


def _claims(tagger: ContentTaggerV2, elements: list, rects: dict) -> dict:
    return tagger._positioned_figure_claims(elements, rects)


def test_a_figure_is_claimed_by_the_one_block_it_fits(tmp_path: Path) -> None:
    tagger = _tagger(tmp_path)
    rects = {10: (0.0, 0.0, 100.0, 100.0), 20: (200.0, 0.0, 300.0, 100.0)}
    elements = [_figure(200, 0, 300, 100), _figure(0, 0, 100, 100), _figure()]
    assert _claims(tagger, elements, rects) == {20: 0, 10: 1}


def test_the_threshold_is_the_text_tolerance(tmp_path: Path) -> None:
    tagger = _tagger(tmp_path)
    rects = {10: RECT}
    assert _claims(tagger, [_figure(0, 0, 100, 70)], rects) == {10: 0}
    assert _claims(tagger, [_figure(0, 0, 100, 69)], rects) == {}


def test_coincident_blocks_leave_every_figure_for_them_unclaimed(
    tmp_path: Path,
) -> None:
    """A box that fits one of two images drawn on top of each other fits both."""
    tagger = _tagger(tmp_path)
    rects = {10: RECT, 20: RECT}
    assert _claims(tagger, [_figure(*RECT)], rects) == {}
    assert _claims(tagger, [_figure(*RECT), _figure(*RECT)], rects) == {}


def test_partly_overlapping_blocks_leave_the_figure_unclaimed(tmp_path: Path) -> None:
    """Two 200 by 100 draws 20 apart overlap at 0.818, above the threshold."""
    tagger = _tagger(tmp_path)
    first, second = (100.0, 600.0, 300.0, 700.0), (120.0, 600.0, 320.0, 700.0)
    rects = {10: first, 20: second}
    assert _claims(tagger, [_figure(*second)], rects) == {}
    assert _claims(tagger, [_figure(*first)], rects) == {}
    # Draws that overlap less than the threshold are still told apart.
    apart = {10: first, 20: (200.0, 600.0, 400.0, 700.0)}
    assert _claims(tagger, [_figure(200, 600, 400, 700)], apart) == {20: 0}


def test_two_figures_for_one_block_leave_both_unclaimed(tmp_path: Path) -> None:
    tagger = _tagger(tmp_path)
    assert (
        _claims(tagger, [_figure(0, 0, 100, 98), _figure(0, 0, 100, 96)], {10: RECT})
        == {}
    )


def test_blocks_without_a_known_rectangle_claim_nothing(tmp_path: Path) -> None:
    tagger = _tagger(tmp_path)
    assert _claims(tagger, [_figure(*RECT)], {10: None, 20: None}) == {}
    # An unknown block does not stop the others from being claimed.
    assert _claims(tagger, [_figure(*RECT)], {10: RECT, 20: None}) == {10: 0}


def test_non_figures_and_unpositioned_figures_are_never_claimed(
    tmp_path: Path,
) -> None:
    tagger = _tagger(tmp_path)
    elements = [_figure(*RECT, role="/P"), _figure()]
    assert _claims(tagger, elements, {10: RECT}) == {}


# ---------------------------------------------------------------------------
# A generated Figure that no image was bound to does not survive
# ---------------------------------------------------------------------------


def _tag_with_figures(tmp_path: Path, *, generated: bool) -> tuple[Path, dict]:
    source = tmp_path / "source.pdf"
    _write_source(source, 1)
    with pikepdf.open(source) as pdf:
        tree = PDFStructureTree(pdf)
        # One Figure that fits the image, one whose box fits nothing.
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        fits = list(_image_draw_rects(ops).values())[0]
        tree.add_alt_text_to_image(
            page_num=1, alt_text="Fits the image", image_index=0, image_bbox=fits
        )
        tree.add_alt_text_to_image(
            page_num=1,
            alt_text="Fits nothing",
            image_index=1,
            image_bbox=(500.0, 500.0, 600.0, 600.0),
        )
        staged = tmp_path / "staged.pdf"
        pdf.save(staged)
    output = tmp_path / "tagged.pdf"
    with pikepdf.open(staged) as pdf, fitz.open(staged) as doc:
        stats = ContentTaggerV2(
            pdf, doc, order_generated_structure=generated
        ).tag_all_pages()
        pdf.save(output)
    return output, stats


def _phantom_figures(path: str) -> list[str]:
    """Figures that hold an /Alt and no content to carry it."""
    phantoms: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Dictionary) and "/S" in node:
            if str(node.S) == "/Figure" and "/Alt" in node and "/K" not in node:
                phantoms.append(str(node.Alt))
            kids = node.get("/K")
            for kid in list(kids) if isinstance(kids, Array) else [kids]:
                walk(kid)

    with pikepdf.open(path) as pdf:
        for kid in list(pdf.Root.StructTreeRoot.K):
            walk(kid)
    return phantoms


def _orphan(pdf: pikepdf.Pdf, page: Any, parent: Any, alt: str) -> Dictionary:
    """A Figure with an /Alt, a position that fits no image, and no content."""
    return Dictionary(
        S=Name.Figure,
        Alt=pikepdf.String(alt),
        Pg=page.obj,
        P=parent,
        A=Dictionary(O=Name.Layout, BBox=Array([500, 500, 600, 600])),
    )


def _tag_structure(tmp_path: Path, build: Any) -> tuple[dict, Path]:
    """Tag a one-image page whose generated structure ``build`` supplies."""
    source = tmp_path / "source.pdf"
    _write_source(source, 1)
    with pikepdf.open(source) as pdf:
        PDFStructureTree(pdf)
        root = pdf.Root.StructTreeRoot
        root.K = Array(build(pdf, pdf.pages[0], root))
        staged = tmp_path / "staged.pdf"
        pdf.save(staged)
    output = tmp_path / "tagged.pdf"
    with pikepdf.open(staged) as pdf, fitz.open(staged) as doc:
        stats = ContentTaggerV2(
            pdf, doc, order_generated_structure=True
        ).tag_all_pages()
        pdf.save(output)
    return stats, output


def _elements(path: str) -> list[tuple[str, str | None]]:
    """Every structure element as (role, its /Alt or /ActualText), in order."""
    found: list[tuple[str, str | None]] = []

    def walk(node: Any) -> None:
        if isinstance(node, Dictionary) and "/S" in node:
            text = node.get("/Alt", node.get("/ActualText"))
            found.append((str(node.S), str(text) if text is not None else None))
            kids = node.get("/K")
            for kid in list(kids) if isinstance(kids, Array) else [kids]:
                walk(kid)

    with pikepdf.open(path) as pdf:
        kids = pdf.Root.StructTreeRoot.get("/K")
        for kid in list(kids) if isinstance(kids, Array) else [kids]:
            walk(kid)
    return found


def _assert_parent_tree_matches_structure(path: str) -> None:
    """Every ParentTree entry is a reachable element, and the page's key exists."""
    with pikepdf.open(path) as pdf:
        root = pdf.Root.StructTreeRoot
        reachable: set = set()

        def walk(node: Any) -> None:
            if isinstance(node, Dictionary) and "/S" in node:
                if node.is_indirect:
                    reachable.add(node.objgen)
                kids = node.get("/K")
                for kid in list(kids) if isinstance(kids, Array) else [kids]:
                    walk(kid)

        kids = root.get("/K")
        for kid in list(kids) if isinstance(kids, Array) else [kids]:
            walk(kid)
        numbers = list(root.ParentTree.Nums)
        assert int(pdf.pages[0].obj.StructParents) in [
            int(key) for key in numbers[0::2]
        ]
        for value in numbers[1::2]:
            for element in list(value) if isinstance(value, Array) else [value]:
                if isinstance(element, Dictionary):
                    assert element.objgen in reachable


def test_a_direct_orphan_is_removed_by_what_it_is_not_by_object_number(
    tmp_path: Path,
) -> None:
    """A direct dictionary has no object number, so none may stand for another.

    The root holds an indirect heading, a direct paragraph with replacement text
    and a direct orphan Figure. Comparing object numbers saw (0, 0) for both
    direct kids and removed the paragraph along with the Figure.
    """

    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        heading = pdf.make_indirect(
            Dictionary(
                S=Name.H1,
                ActualText=pikepdf.String("Heading text"),
                Pg=page.obj,
                P=root,
            )
        )
        paragraph = Dictionary(
            S=Name.P,
            ActualText=pikepdf.String("Some other paragraph"),
            Pg=page.obj,
            P=root,
        )
        return [heading, paragraph, _orphan(pdf, page, root, "Direct orphan")]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 1
    elements = _elements(str(output))
    assert ("/H1", "Heading text") in elements
    assert ("/P", "Some other paragraph") in elements
    assert not [alt for role, alt in elements if alt == "Direct orphan"]
    assert _phantom_figures(str(output)) == []
    _assert_parent_tree_matches_structure(str(output))


def test_an_orphan_nested_in_the_structure_is_removed_from_its_own_parent(
    tmp_path: Path,
) -> None:
    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        section = pdf.make_indirect(Dictionary(S=Name.Sect, Pg=page.obj, P=root))
        orphan = pdf.make_indirect(_orphan(pdf, page, section, "Nested orphan"))
        sibling = pdf.make_indirect(
            Dictionary(
                S=Name.P,
                ActualText=pikepdf.String("Inside the section"),
                Pg=page.obj,
                P=section,
            )
        )
        section.K = Array([orphan, sibling])
        return [section]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 1
    elements = _elements(str(output))
    assert ("/P", "Inside the section") in elements
    assert not [alt for role, alt in elements if alt == "Nested orphan"]
    with pikepdf.open(output) as pdf:
        section = next(
            kid
            for kid in pikepdf.Array(
                next(
                    item
                    for item in pdf.Root.StructTreeRoot.K
                    if str(item.S) == "/Document"
                ).K
            )
            if str(kid.S) == "/Sect"
        )
        # The parent's /K stays a well-formed array of what is left.
        assert isinstance(section.K, Array)
        assert [str(kid.S) for kid in section.K] == ["/P"]
    _assert_parent_tree_matches_structure(str(output))


@pytest.mark.parametrize("shape", ["only_child_in_an_array", "single_dictionary"])
def test_a_parent_left_without_kids_loses_its_k(tmp_path: Path, shape: str) -> None:
    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        section = pdf.make_indirect(Dictionary(S=Name.Sect, Pg=page.obj, P=root))
        orphan = pdf.make_indirect(_orphan(pdf, page, section, "Only child"))
        section.K = Array([orphan]) if shape == "only_child_in_an_array" else orphan
        return [section]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 1
    assert not [alt for role, alt in _elements(str(output)) if alt == "Only child"]
    with pikepdf.open(output) as pdf:
        document = next(
            item for item in pdf.Root.StructTreeRoot.K if str(item.S) == "/Document"
        )
        section = next(kid for kid in document.K if str(kid.S) == "/Sect")
        assert "/K" not in section
    _assert_parent_tree_matches_structure(str(output))


def test_an_orphan_deep_in_the_structure_is_found(tmp_path: Path) -> None:
    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        outer = pdf.make_indirect(Dictionary(S=Name.Sect, Pg=page.obj, P=root))
        inner = pdf.make_indirect(Dictionary(S=Name.Div, Pg=page.obj, P=outer))
        inner.K = Array([_orphan(pdf, page, inner, "Deep orphan")])
        outer.K = Array([inner])
        return [outer]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 1
    assert not [alt for role, alt in _elements(str(output)) if alt == "Deep orphan"]
    _assert_parent_tree_matches_structure(str(output))


def test_an_orphan_formula_with_alt_is_removed_too(tmp_path: Path) -> None:
    """The predicate covers every tag a block can be paired with, Formula included."""

    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        orphan = _orphan(pdf, page, root, "Orphan formula")
        orphan.S = Name.Formula
        return [orphan]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 1
    assert not [alt for role, alt in _elements(str(output)) if alt == "Orphan formula"]


def test_elements_that_are_not_orphans_are_left_alone(tmp_path: Path) -> None:
    """No /Alt, a content child, or no page: none of these is an unbound orphan."""

    def build(pdf: pikepdf.Pdf, page: Any, root: Any) -> list[Any]:
        without_alt = Dictionary(S=Name.Figure, Pg=page.obj, P=root)
        with_content = Dictionary(
            S=Name.Figure, Alt=pikepdf.String("Has content"), Pg=page.obj, P=root, K=7
        )
        on_no_page = Dictionary(S=Name.Figure, Alt=pikepdf.String("No page"), P=root)
        return [without_alt, with_content, on_no_page]

    stats, output = _tag_structure(tmp_path, build)

    assert stats["figures_unbound_removed"] == 0
    alts = [alt for role, alt in _elements(str(output)) if role == "/Figure"]
    assert "Has content" in alts and "No page" in alts


def test_generated_figure_with_alt_that_binds_to_nothing_is_removed(
    tmp_path: Path,
) -> None:
    output, stats = _tag_with_figures(tmp_path, generated=True)
    assert stats["figures_unbound_removed"] == 1
    assert _phantom_figures(str(output)) == []
    assert _carried(str(output))[0] == (True, "Fits the image")
    assert [
        alt
        for role, alt in _structure(str(output))
        if role == "/Figure" and alt is not None
    ] == ["Fits the image"]


def test_a_tagged_source_keeps_figures_it_already_had(tmp_path: Path) -> None:
    """Only a structure generated in this run is cleaned up."""
    output, stats = _tag_with_figures(tmp_path, generated=False)
    assert stats["figures_unbound_removed"] == 0
    assert _phantom_figures(str(output)) == ["Fits nothing"]


# ---------------------------------------------------------------------------
# Draw rectangles and the user-space box
# ---------------------------------------------------------------------------


def _ops(stream: bytes) -> list[Any]:
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()
        page.obj.Contents = pdf.make_stream(stream)
        return list(pikepdf.parse_content_stream(page))


def test_draw_rectangles_follow_the_graphics_state() -> None:
    ops = _ops(
        b"q 100 0 0 50 10 20 cm /A Do Q "
        b"q 1 0 0 1 5 5 cm q 40 0 0 40 0 0 cm /B Do Q /C Do Q "
        b"0.5 0 0 0.5 0 0 cm /D Do"
    )
    rects = _image_draw_rects(ops)
    draws = [index for index, op in enumerate(ops) if str(op.operator) == "Do"]
    assert [rects[index] for index in draws] == [
        (10.0, 20.0, 110.0, 70.0),
        (5.0, 5.0, 45.0, 45.0),
        (5.0, 5.0, 6.0, 6.0),
        (0.0, 0.0, 0.5, 0.5),
    ]


def test_draw_rectangle_of_a_rotated_image_is_its_extent() -> None:
    ops = _ops(b"q 0 30 -20 0 50 10 cm /A Do Q")
    (rect,) = _image_draw_rects(ops).values()
    assert rect == (30.0, 10.0, 50.0, 40.0)


@pytest.mark.parametrize(
    "stream",
    [b"Q /A Do", b"q 1 0 0 cm /A Do Q", b"q a b c d e f cm /A Do Q"],
)
def test_unfollowable_graphics_state_yields_no_rectangles(stream: bytes) -> None:
    try:
        ops = _ops(stream)
    except Exception:
        pytest.skip("pikepdf refuses to parse this stream")
    assert _image_draw_rects(ops) == {}
