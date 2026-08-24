"""Tests for MathFixer specialist module."""


def test_math_fixer_converts_latex_to_formula():
    """MathFixer should create Formula elements with MathML from LaTeX."""
    import pikepdf
    from pikepdf import Dictionary, Name
    import fitz as fitz_mod
    import tempfile
    import os

    pdf = pikepdf.new()
    page = pikepdf.Page(
        Dictionary(
            {
                "/Type": Name.Page,
                "/MediaBox": [0, 0, 612, 792],
                "/Contents": pdf.make_stream(
                    b"BT /F1 12 Tf 72 720 Td (x^2 + 1 = 0) Tj ET"
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
        from src.education.remediation.base import (
            RemediationIssue,
            IssueCategory,
            IssueSeverity,
        )

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
        formula_found = any(hasattr(k, "S") and str(k.S) == "/Formula" for k in kids)
        assert formula_found, "Formula element should exist in structure tree"

        fitz_doc.close()
        pdf2.close()
    finally:
        os.unlink(tmp_path)


def test_image_equation_pipeline_is_injected_and_stops_before_association():
    from types import SimpleNamespace

    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
    from src.education.remediation.math_fixer import MathFixer

    calls = []
    validated = SimpleNamespace(identity=SimpleNamespace(occurrence_id="occ-1"))

    class Source:
        def extract(self, document, identity):
            calls.append(("source", document, identity["occurrence_id"]))
            return validated

    class Recognizer:
        def recognize(self, image):
            calls.append(("recognizer", image))
            return SimpleNamespace(
                classification="printed_equation", latex="x^2 + 1 = 0"
            )

    class Verifier:
        def verify(self, image, latex):
            calls.append(("verifier", image, latex))
            return SimpleNamespace(passed=True)

    class StructTree:
        def add_formula(self, **kwargs):
            raise AssertionError("association is deferred and must not mutate")

    fitz_doc = SimpleNamespace()
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        fitz_doc,
        struct_tree=StructTree(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        image_source=Source(),
        equation_recognizer=Recognizer(),
        equation_verifier=Verifier(),
    )
    result = fixer._fix_math_issue(
        SimpleNamespace(
            metadata={
                "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
                "page_number": 1,
                "image_xref": 7,
                "image_index": 0,
                "occurrence_ordinal": 0,
                "bbox": (1.0, 2.0, 3.0, 4.0),
                "occurrence_id": "occ-1",
            }
        )
    )

    assert not result.success
    assert result.error == "image_equation_association_unavailable"
    assert [call[0] for call in calls] == ["source", "recognizer", "verifier"]


def test_image_equation_unexpected_metadata_error_is_stable_and_redacted():
    from types import SimpleNamespace

    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
    from src.education.remediation.math_fixer import MathFixer

    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        equation_verifier=SimpleNamespace(),
    )
    results = fixer.fix(
        [
            SimpleNamespace(
                metadata={
                    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
                    "page_number": "private-invalid-page",
                }
            )
        ]
    )

    assert results[0].error == "math_fix_failed"
    assert "private-invalid-page" not in results[0].error
