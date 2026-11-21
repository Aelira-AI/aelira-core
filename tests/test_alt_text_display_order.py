"""Stored alt text belongs to the image the findings counted, by display order.

Image findings number images in display order, the order the page content
draws them. The accessible-HTML alternative looks stored text up by that same
identity, never by the order resources are listed, so a page that draws its
images reverse of resource order still shows each stored text on its own
image. Text stored for an index no displayed draw carries is refused, and a
fix the saved PDF would not carry is refused instead of recorded.
"""

from __future__ import annotations

import base64
import io
from html.parser import HTMLParser
from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image

from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationIssue,
)
from src.education.remediation.outcome_explanations import manual_explanation
from src.education.remediation.pdf_remediator import (
    _ALT_TEXT_NOT_EMBEDDED,
    PdfRemediator,
)

pytestmark = pytest.mark.unit

RED = (255, 32, 32)
BLUE = (32, 32, 255)


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _swap_draws(path: Path) -> None:
    """Rewrite the page content so the second inserted image draws first."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        do_positions = [i for i, op in enumerate(ops) if str(op.operator) == "Do"]
        assert len(do_positions) == 2
        blocks = []
        for do in do_positions:
            start = max(i for i in range(do) if str(ops[i].operator) == "q")
            end = min(i for i in range(do, len(ops)) if str(ops[i].operator) == "Q")
            blocks.append((start, end))
        (a0, a1), (b0, b1) = blocks
        assert a1 < b0
        swapped = (
            ops[:a0]
            + ops[b0 : b1 + 1]
            + ops[a1 + 1 : b0]
            + ops[a0 : a1 + 1]
            + ops[b1 + 1 :]
        )
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(swapped))
        pdf.save(path)


def _two_image_page(path: Path, *, reversed_draws: bool) -> Path:
    """Red inserted first, blue second; optionally drawn blue-then-red."""
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), "Two images, one draw order each way.")
        page.insert_image(fitz.Rect(72, 150, 150, 228), stream=_png(RED))
        page.insert_image(fitz.Rect(72, 300, 150, 378), stream=_png(BLUE))
        document.save(str(path))
    if reversed_draws:
        _swap_draws(path)
    return path


class _ImageCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _generate(
    path: Path, alt_texts: dict[tuple[int, int], str]
) -> list[dict[str, str]]:
    remediator = PdfRemediator(str(path), [])
    remediator._alt_texts = dict(alt_texts)
    with fitz.open(str(path)) as document:
        assert remediator._generate_accessible_html(document)
    assert remediator._html_output is not None
    collector = _ImageCollector()
    collector.feed(remediator._html_output)
    return collector.images


def _color_of_src(src: str) -> str:
    header, _, payload = src.partition(",")
    assert header == "data:image/png;base64"
    image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
    pixel = image.getpixel((image.width // 2, image.height // 2))
    return "red" if pixel[0] > 128 else "blue"


def _single_image_page(path: Path) -> Path:
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), "One image, drawn once.")
        page.insert_image(fitz.Rect(72, 150, 150, 228), stream=_png(RED))
        document.save(str(path))
    return path


def test_reversed_draw_order_keeps_each_stored_alt_text_on_its_own_image(
    tmp_path: Path,
) -> None:
    path = _two_image_page(tmp_path / "reversed.pdf", reversed_draws=True)
    images = _generate(
        path,
        {
            (1, 0): "alt for the first displayed image",
            (1, 1): "alt for the second displayed image",
        },
    )
    assert len(images) == 2
    # Display order is blue then red; the stored texts follow that identity.
    assert _color_of_src(images[0]["src"]) == "blue"
    assert images[0]["alt"] == "alt for the first displayed image"
    assert _color_of_src(images[1]["src"]) == "red"
    assert images[1]["alt"] == "alt for the second displayed image"


def test_aligned_orders_pair_by_the_same_display_index(tmp_path: Path) -> None:
    path = _two_image_page(tmp_path / "aligned.pdf", reversed_draws=False)
    images = _generate(
        path,
        {
            (1, 0): "alt for the first displayed image",
            (1, 1): "alt for the second displayed image",
        },
    )
    assert len(images) == 2
    assert _color_of_src(images[0]["src"]) == "red"
    assert images[0]["alt"] == "alt for the first displayed image"
    assert _color_of_src(images[1]["src"]) == "blue"
    assert images[1]["alt"] == "alt for the second displayed image"


def test_text_stored_for_no_displayed_draw_is_never_emitted(tmp_path: Path) -> None:
    path = _two_image_page(tmp_path / "phantom.pdf", reversed_draws=True)
    remediator = PdfRemediator(str(path), [])
    remediator._alt_texts = {(1, 0): "the blue one", (1, 7): "phantom description"}
    with fitz.open(str(path)) as document:
        assert remediator._generate_accessible_html(document)
    rendered_output = remediator._html_output
    assert rendered_output is not None
    assert "phantom description" not in rendered_output
    collector = _ImageCollector()
    collector.feed(rendered_output)
    assert len(collector.images) == 2
    assert collector.images[0]["alt"] == "the blue one"


class _FailingStructure:
    def add_alt_text_to_image(self, **kwargs) -> bool:
        return False


def _alt_issue() -> RemediationIssue:
    return RemediationIssue(
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Image missing alternative text",
        metadata={"page_number": 1, "issue_type": "missing_alt_text"},
    )


def test_embed_failure_is_refused_without_sidecar_or_credit(tmp_path: Path) -> None:
    path = _single_image_page(tmp_path / "embed-failure.pdf")
    remediator = PdfRemediator(str(path), [])
    remediator._struct_tree = _FailingStructure()
    remediator._generated_structure = True
    issue = _alt_issue()
    with fitz.open(str(path)) as document:
        assert not remediator._apply_alt_text_fix(issue, document, "Some description")
    assert remediator._source_binding_refusals[issue.id] == _ALT_TEXT_NOT_EMBEDDED
    assert remediator._html_output is None
    assert not getattr(remediator, "_alt_texts", {})


def test_missing_structure_is_refused_without_sidecar_or_credit(
    tmp_path: Path,
) -> None:
    path = _two_image_page(tmp_path / "no-structure.pdf", reversed_draws=False)
    remediator = PdfRemediator(str(path), [])
    remediator._struct_tree = None
    issue = _alt_issue()
    with fitz.open(str(path)) as document:
        assert not remediator._apply_alt_text_fix(issue, document, "Some description")
    assert remediator._source_binding_refusals[issue.id] == _ALT_TEXT_NOT_EMBEDDED
    assert remediator._html_output is None
    assert not getattr(remediator, "_alt_texts", {})


def test_refusal_carries_a_public_explanation() -> None:
    info = manual_explanation({"reason": _ALT_TEXT_NOT_EMBEDDED})
    assert info["reason_code"] == "alt_text_not_embedded"
    assert info["attempt"] == "not_applied"
    assert "saved PDF" in info["reason"]
