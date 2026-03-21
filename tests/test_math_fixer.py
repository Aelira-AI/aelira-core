"""Tests for MathFixer specialist module."""
import pytest


def test_math_fixer_converts_latex_to_formula():
    """MathFixer should create Formula elements with MathML from LaTeX."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name
    import fitz as fitz_mod
    import tempfile, os

    pdf = pikepdf.new()
    page = pikepdf.Page(Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
        "/Contents": pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (x^2 + 1 = 0) Tj ET"),
        "/Resources": Dictionary({
            "/Font": Dictionary({
                "/F1": pdf.make_indirect(Dictionary({
                    "/Type": Name.Font, "/Subtype": Name("/Type1"),
                    "/BaseFont": Name("/Helvetica"),
                })),
            }),
        }),
    }))
    pdf.pages.append(page)

    from src.education.remediation.pdf_structure import PDFStructureTree
    tree = PDFStructureTree(pdf)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)
        tree2 = PDFStructureTree(pdf2)

        from src.education.remediation.math_fixer import MathFixer
        from src.education.remediation.base import RemediationIssue, IssueCategory, IssueSeverity

        issue = RemediationIssue(
            category=IssueCategory.STRUCTURE,
            severity=IssueSeverity.HIGH,
            description="Math content not accessible",
            metadata={
                "issue_type": "raw_latex_code",
                "page_number": 1,
                "equation_text": "x^2 + 1 = 0",
            },
        )

        fixer = MathFixer(pdf2, fitz_doc, struct_tree=tree2)
        results = fixer.fix([issue])

        assert len(results) >= 1
        assert results[0].success

        kids = tree2.kids
        formula_found = any(
            hasattr(k, "S") and str(k.S) == "/Formula"
            for k in kids
        )
        assert formula_found, "Formula element should exist in structure tree"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
