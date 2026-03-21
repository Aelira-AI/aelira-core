"""Tests for LinkFixer specialist module."""
import pytest
import pikepdf
from pikepdf import Array, Dictionary, Name, String


def _make_pdf_with_links():
    """Create a PDF with link annotations missing /Contents."""
    pdf = pikepdf.new()
    page = pikepdf.Page(Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
        "/Contents": pdf.make_stream(
            b"BT /F1 12 Tf 72 720 Td (Click here for details) Tj ET"
        ),
        "/Resources": Dictionary({
            "/Font": Dictionary({
                "/F1": pdf.make_indirect(Dictionary({
                    "/Type": Name.Font,
                    "/Subtype": Name("/Type1"),
                    "/BaseFont": Name("/Helvetica"),
                })),
            }),
        }),
    }))
    pdf.pages.append(page)

    link = pdf.make_indirect(Dictionary({
        "/Type": Name("/Annot"),
        "/Subtype": Name("/Link"),
        "/Rect": Array([72, 710, 250, 730]),
        "/A": Dictionary({
            "/Type": Name("/Action"),
            "/S": Name("/URI"),
            "/URI": String("https://example.com/report"),
        }),
    }))
    pdf.pages[0].obj["/Annots"] = Array([link])
    return pdf


def test_link_fixer_adds_contents():
    """LinkFixer should add /Contents from visible text under link rect."""
    import fitz as fitz_mod
    import tempfile, os

    pdf = _make_pdf_with_links()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.link_fixer import LinkFixer
        from src.education.remediation.base import RemediationIssue, IssueCategory, IssueSeverity

        issue = RemediationIssue(
            category=IssueCategory.LINK,
            severity=IssueSeverity.HIGH,
            description="Link missing accessible name",
            metadata={"issue_type": "links_missing_alt", "page_number": 1},
        )

        fixer = LinkFixer(pdf2, fitz_doc)
        results = fixer.fix([issue])

        assert len(results) >= 1
        assert results[0].success

        annots = pdf2.pages[0].obj.get("/Annots", Array([]))
        link = annots[0]
        assert "/Contents" in link, "Link should have /Contents after fix"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
