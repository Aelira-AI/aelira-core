"""Tests for RoleMappingFixer specialist module."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String


def _make_pdf_with_nonstandard_tags():
    """Create a PDF with non-standard structure tags."""
    pdf = pikepdf.new()
    page = pikepdf.Page(Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
    }))
    pdf.pages.append(page)

    struct_root = pdf.make_indirect(Dictionary({
        "/Type": Name.StructTreeRoot,
        "/K": Array([]),
        "/ParentTree": Dictionary({"/Nums": Array([])}),
        "/RoleMap": Dictionary({}),
    }))
    pdf.Root[Name.StructTreeRoot] = struct_root

    elem = pdf.make_indirect(Dictionary({
        "/Type": Name.StructElem,
        "/S": Name("/textbox"),
        "/P": struct_root,
    }))
    struct_root["/K"].append(elem)

    elem2 = pdf.make_indirect(Dictionary({
        "/Type": Name.StructElem,
        "/S": Name("/Normal"),
        "/P": struct_root,
    }))
    struct_root["/K"].append(elem2)

    return pdf


def test_role_mapping_fixer_maps_nonstandard_tags():
    """RoleMappingFixer should add /RoleMap entries for non-standard tags."""
    import fitz as fitz_mod
    import tempfile, os

    pdf = _make_pdf_with_nonstandard_tags()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        tmp_path = f.name

    try:
        fitz_doc = fitz_mod.open(tmp_path)
        pdf2 = pikepdf.open(tmp_path)

        from src.education.remediation.role_mapping_fixer import RoleMappingFixer
        from src.education.remediation.base import RemediationIssue, IssueCategory, IssueSeverity

        issue = RemediationIssue(
            category=IssueCategory.STRUCTURE,
            severity=IssueSeverity.HIGH,
            description="Non-standard tags without role mapping",
            metadata={"issue_type": "missing_role_map"},
        )

        fixer = RoleMappingFixer(pdf2, fitz_doc)
        results = fixer.fix([issue])

        assert any(r.success for r in results)

        role_map = pdf2.Root[Name.StructTreeRoot]["/RoleMap"]
        assert "/textbox" in role_map
        assert "/Normal" in role_map

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)
