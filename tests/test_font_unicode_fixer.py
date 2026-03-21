"""Tests for FontUnicodeFixer specialist module."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String


def test_font_unicode_fixer_flags_missing_tounicode():
    """FontUnicodeFixer should flag fonts with no /ToUnicode and no /Differences."""
    import fitz as fitz_mod
    import tempfile, os

    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary({
        "/Type": Name.Font,
        "/Subtype": Name("/Type1"),
        "/BaseFont": Name("/CustomFont"),
        "/Encoding": Dictionary({
            "/Type": Name("/Encoding"),
            "/BaseEncoding": Name("/WinAnsiEncoding"),
        }),
    }))
    page = pikepdf.Page(Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
        "/Contents": pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (Test) Tj ET"),
        "/Resources": Dictionary({"/Font": Dictionary({"/F1": font})}),
    }))
    pdf.pages.append(page)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.font_unicode_fixer import FontUnicodeFixer
        from src.education.remediation.base import RemediationIssue, IssueCategory, IssueSeverity

        issue = RemediationIssue(
            category=IssueCategory.STRUCTURE,
            severity=IssueSeverity.MEDIUM,
            description="Font missing /ToUnicode CMap",
            metadata={"issue_type": "missing_tounicode"},
        )

        fixer = FontUnicodeFixer(pdf2, fitz_doc)
        results = fixer.fix([issue])

        assert len(results) >= 1
        assert any(r.confidence == 0.0 or r.success for r in results)

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
