"""
Tests for image content tagging in ContentTaggerV2.

A screenshot or scan has no BT/ET operators at all. When the tagger only
looked for text blocks it emitted no BDC and left /ParentTree /Nums empty, so
any Figure element carrying generated alt text was never linked to the image
it described — a structure tree that reads as tagged but is unreachable to a
screen reader.
"""

import fitz
import pikepdf
import pytest

from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.remediation.content_tagger_v2 import ContentTaggerV2
from src.education.remediation.pdf_structure import PDFStructureTree

ALT_TEXT = "A biochemical pathway diagram"


def _make_image_only_pdf(path: str) -> None:
    """Build a screenshot-like PDF: one full-page raster image, no text."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 500))
    pix.set_rect(pix.irect, (200, 220, 240))
    page.insert_image(fitz.Rect(50, 50, 562, 742), pixmap=pix)
    doc.save(path)
    doc.close()


def _make_text_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 720), "Hello accessible world", fontsize=14)
    doc.save(path)
    doc.close()


def _remediate(src: str, out: str, *, alt_text=ALT_TEXT, full=False) -> dict:
    """Apply the structure steps a real remediation applies, then tag."""
    pdf = pikepdf.open(src)
    tree = PDFStructureTree(pdf)
    if alt_text is not None:
        tree.add_alt_text_to_image(page_num=0, image_index=0, alt_text=alt_text)
    if full:
        tree.set_document_language("en")
        tree.set_document_title("Screenshot")
        tree.set_pdfua_identifier()
    mid = out + ".mid.pdf"
    pdf.save(mid)
    pdf.close()

    pdf2 = pikepdf.open(mid)
    fdoc = fitz.open(mid)
    stats = ContentTaggerV2(pdf2, fdoc).tag_all_pages()
    pdf2.save(out)
    pdf2.close()
    fdoc.close()
    return stats


def _operators(path: str) -> list:
    pdf = pikepdf.open(path)
    try:
        return [str(op.operator) for op in pikepdf.parse_content_stream(pdf.pages[0])]
    finally:
        pdf.close()


def _parent_tree_len(path: str) -> int:
    pdf = pikepdf.open(path)
    try:
        root = pdf.Root.get(pikepdf.Name.StructTreeRoot)
        if root is None:
            return 0
        ptree = root.get(pikepdf.Name.ParentTree)
        if ptree is None:
            return 0
        nums = ptree.get(pikepdf.Name.Nums)
        return 0 if nums is None else len(nums)
    finally:
        pdf.close()


def test_image_only_page_gets_marked_content(tmp_path):
    """Regression: a screenshot PDF must emit BDC and populate the ParentTree."""
    src = str(tmp_path / "shot.pdf")
    out = str(tmp_path / "out.pdf")
    _make_image_only_pdf(src)

    assert "BT" not in _operators(src), "fixture must have no text blocks"

    stats = _remediate(src, out)

    assert stats["blocks_matched"] == 1
    assert "BDC" in _operators(out)
    assert _parent_tree_len(out) > 0


def test_existing_figure_is_reused_not_orphaned(tmp_path):
    """The Figure holding generated alt text must be the one linked to the image.

    Creating a fresh Figure would satisfy the tagging check while leaving the
    described alt text attached to nothing.
    """
    src = str(tmp_path / "shot.pdf")
    out = str(tmp_path / "out.pdf")
    _make_image_only_pdf(src)

    stats = _remediate(src, out)
    assert stats["blocks_created"] == 0, "should reuse, not create"

    pdf = pikepdf.open(out)
    try:
        figures = []

        def walk(el):
            if not hasattr(el, "keys"):
                return
            if pikepdf.Name.S in el:
                if str(el[pikepdf.Name.S]).lstrip("/") == "Figure":
                    figures.append(el)
            kids = el.get(pikepdf.Name.K)
            if kids is None:
                return
            if not isinstance(kids, pikepdf.Array):
                kids = [kids]
            for k in kids:
                walk(k)

        root = pdf.Root[pikepdf.Name.StructTreeRoot]
        kids = root.get(pikepdf.Name.K, pikepdf.Array([]))
        if not isinstance(kids, pikepdf.Array):
            kids = [kids]
        for k in kids:
            walk(k)

        assert len(figures) == 1, "no duplicate Figure should be created"
        figure = figures[0]
        assert str(figure.get(pikepdf.Name.Alt)) == ALT_TEXT
        # The alt text is only reachable if the Figure points at marked content.
        assert figure.get(pikepdf.Name.K) is not None
    finally:
        pdf.close()


def test_screenshot_scores_clean_after_full_remediation(tmp_path):
    """End to end: a fully remediated screenshot reports no structure issues."""
    src = str(tmp_path / "shot.pdf")
    out = str(tmp_path / "out.pdf")
    _make_image_only_pdf(src)

    before = StructureTreeChecker().check(src)
    assert before, "fixture should start non-compliant"

    _remediate(src, out, full=True)
    after = StructureTreeChecker().check(out)

    assert after == [], f"expected a clean structure check, got {after}"


def test_unmatched_image_creates_a_figure_not_a_paragraph(tmp_path):
    """An image with no structure element gets a Figure, never a P."""
    src = str(tmp_path / "shot.pdf")
    out = str(tmp_path / "out.pdf")
    _make_image_only_pdf(src)

    stats = _remediate(src, out, alt_text=None)
    assert stats["blocks_created"] == 1

    pdf = pikepdf.open(out)
    try:
        types = []

        def walk(el):
            if not hasattr(el, "keys"):
                return
            if pikepdf.Name.S in el:
                types.append(str(el[pikepdf.Name.S]).lstrip("/"))
            kids = el.get(pikepdf.Name.K)
            if kids is None:
                return
            if not isinstance(kids, pikepdf.Array):
                kids = [kids]
            for k in kids:
                walk(k)

        root = pdf.Root[pikepdf.Name.StructTreeRoot]
        kids = root.get(pikepdf.Name.K, pikepdf.Array([]))
        if not isinstance(kids, pikepdf.Array):
            kids = [kids]
        for k in kids:
            walk(k)

        assert "Figure" in types
        assert "P" not in types
    finally:
        pdf.close()


def test_text_pages_are_unaffected(tmp_path):
    """The text path must keep working exactly as before."""
    src = str(tmp_path / "text.pdf")
    out = str(tmp_path / "out.pdf")
    _make_text_pdf(src)

    pdf = pikepdf.open(src)
    PDFStructureTree(pdf).set_document_language("en")
    mid = str(tmp_path / "mid.pdf")
    pdf.save(mid)
    pdf.close()

    pdf2 = pikepdf.open(mid)
    fdoc = fitz.open(mid)
    stats = ContentTaggerV2(pdf2, fdoc).tag_all_pages()
    pdf2.save(out)
    pdf2.close()
    fdoc.close()

    assert stats["blocks_matched"] + stats["blocks_created"] >= 1
    assert "BDC" in _operators(out)
    assert _parent_tree_len(out) > 0


@pytest.mark.parametrize("subtype", ["/Form", None])
def test_non_image_xobject_is_not_treated_as_an_image(tmp_path, subtype):
    """Only /Subtype /Image draws count; a Form XObject must be ignored."""
    src = str(tmp_path / "shot.pdf")
    _make_image_only_pdf(src)

    pdf = pikepdf.open(src)
    try:
        tagger = ContentTaggerV2(pdf, fitz.open(src))
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        do_ops = [o for o in ops if str(o.operator) == "Do"]
        assert do_ops, "fixture should draw an XObject"

        xobjects = page.obj[pikepdf.Name.Resources][pikepdf.Name.XObject]
        key = list(xobjects.keys())[0]
        if subtype is None:
            del xobjects[key][pikepdf.Name.Subtype]
        else:
            xobjects[key][pikepdf.Name.Subtype] = pikepdf.Name(subtype)

        assert tagger._is_image_xobject(page, do_ops[0]) is False
    finally:
        pdf.close()
