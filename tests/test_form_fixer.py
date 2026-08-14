"""Tests for FormFixer specialist module."""

import pikepdf
from pikepdf import Array, Dictionary, Name, String


def _make_pdf_with_form():
    """Create a minimal PDF with an unlabeled form field."""
    pdf = pikepdf.new()
    page = pikepdf.Page(
        Dictionary(
            {
                "/Type": Name.Page,
                "/MediaBox": [0, 0, 612, 792],
                "/Contents": pdf.make_stream(
                    b"BT /F1 12 Tf 72 702 Td (Full Name:) Tj ET"
                ),
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

    field = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Annot"),
                "/Subtype": Name("/Widget"),
                "/FT": Name("/Tx"),
                "/T": String("name_field"),
                "/Rect": Array([72, 680, 250, 700]),
                "/P": pdf.pages[0].obj,
            }
        )
    )
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        Dictionary(
            {
                "/Fields": Array([field]),
            }
        )
    )
    pdf.pages[0].obj["/Annots"] = Array([field])
    return pdf


def test_form_fixer_adds_tu_label():
    """FormFixer should add /TU tooltip to unlabeled fields."""
    import fitz as fitz_mod
    import tempfile
    import os

    pdf = _make_pdf_with_form()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.form_fixer import FormFixer
        from src.education.remediation.base import (
            RemediationIssue,
            IssueCategory,
            IssueSeverity,
        )

        issue = RemediationIssue(
            category=IssueCategory.FORM,
            severity=IssueSeverity.HIGH,
            description="Form field missing tooltip/label",
            metadata={"issue_type": "unlabeled_form_fields", "page_number": 1},
        )

        fixer = FormFixer(pdf2, fitz_doc)
        results = fixer.fix([issue])

        assert len(results) >= 1
        assert results[0].success

        fields = pdf2.Root["/AcroForm"]["/Fields"]
        field = fields[0]
        assert "/TU" in field, "Field should have /TU tooltip after fix"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)


def test_form_fixer_adds_tabs():
    """FormFixer should add /Tabs /S to pages missing tab order."""
    import fitz as fitz_mod
    import tempfile
    import os

    pdf = _make_pdf_with_form()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.form_fixer import FormFixer
        from src.education.remediation.base import (
            RemediationIssue,
            IssueCategory,
            IssueSeverity,
        )

        issue = RemediationIssue(
            category=IssueCategory.FORM,
            severity=IssueSeverity.MEDIUM,
            description="Page missing tab order",
            metadata={"issue_type": "missing_tab_order", "page_number": 1},
        )

        fixer = FormFixer(pdf2, fitz_doc)
        results = fixer.fix([issue])

        assert len(results) >= 1
        page = pdf2.pages[0]
        assert "/Tabs" in page.obj, "Page should have /Tabs after fix"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
