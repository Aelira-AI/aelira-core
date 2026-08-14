"""Tests for ContentTagger v2 — position-based BDC/EMC matching."""

import pikepdf
from pikepdf import Array, Dictionary, Name


def _make_tagged_pdf():
    """Create a PDF with structure elements that have position data."""
    pdf = pikepdf.new()
    content = b"BT /F1 18 Tf 72 720 Td (Introduction) Tj 0 -30 Td /F1 12 Tf (Body text paragraph.) Tj ET"
    page = pikepdf.Page(
        Dictionary(
            {
                "/Type": Name.Page,
                "/MediaBox": [0, 0, 612, 792],
                "/Contents": pdf.make_stream(content),
                "/Resources": Dictionary(
                    {
                        "/Font": Dictionary(
                            {
                                "/F1": pdf.make_indirect(
                                    Dictionary(
                                        {
                                            "/Type": Name.Font,
                                            "/Subtype": Name("/Type1"),
                                            "/BaseFont": Name("/Helvetica"),
                                        }
                                    )
                                ),
                            }
                        ),
                    }
                ),
            }
        )
    )
    pdf.pages.append(page)

    from src.education.remediation.pdf_structure import PDFStructureTree

    tree = PDFStructureTree(pdf)
    tree.add_heading(1, 1, "Introduction", bbox=(72, 720, 200, 740))
    tree.add_paragraph(1, "Body text paragraph.", bbox=(72, 690, 300, 710))
    return pdf


def test_content_tagger_v2_tags_pages():
    """ContentTagger v2 should inject BDC/EMC markers into content streams."""
    import tempfile
    import os
    import fitz as fitz_mod

    pdf = _make_tagged_pdf()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.content_tagger_v2 import ContentTaggerV2

        tagger = ContentTaggerV2(pdf2, fitz_doc)
        stats = tagger.tag_all_pages()

        assert stats["pages_processed"] >= 1
        assert stats["blocks_matched"] >= 1

        page = pdf2.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]
        assert (
            "BMC" in op_names or "BDC" in op_names
        ), f"Expected BDC/BMC in content stream, got: {op_names}"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)


def test_content_tagger_v2_builds_parent_tree():
    """ContentTagger v2 should populate the ParentTree with MCID mappings."""
    import tempfile
    import os
    import fitz as fitz_mod

    pdf = _make_tagged_pdf()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.content_tagger_v2 import ContentTaggerV2

        tagger = ContentTaggerV2(pdf2, fitz_doc)
        tagger.tag_all_pages()

        struct_root = pdf2.Root.get(Name.StructTreeRoot)
        assert struct_root is not None
        parent_tree = struct_root.get("/ParentTree")
        assert parent_tree is not None
        nums = parent_tree.get("/Nums", Array([]))
        assert len(nums) > 0, "ParentTree /Nums should have entries"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
