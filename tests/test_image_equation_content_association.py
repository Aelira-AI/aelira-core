"""Exact image-occurrence to Formula/MCID/ParentTree association tests."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import fitz
import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.remediation.math_fixer import (
    MathFixResult,
    MathVerificationEvidence,
    PendingEquationAssociation,
)

MATHML = (
    '<math xmlns="http://www.w3.org/1998/Math/MathML">'
    "<msup><mi>x</mi><mn>2</mn></msup></math>"
)


def _evidence() -> MathVerificationEvidence:
    return MathVerificationEvidence(
        passed=True,
        source_sha256="a" * 64,
        rendered_sha256="b" * 64,
        mathml_sha256=hashlib.sha256(MATHML.encode()).hexdigest(),
        renderer_version="renderer-v1",
        comparator_version="comparator-v1",
        font_sha256="f" * 64,
        threshold_version="threshold-v1",
        ink_iou=1.0,
        pixel_similarity=1.0,
        required_ink_iou=0.9,
        required_pixel_similarity=0.98,
    )


def _make_reused_image_pdf(path: Path, *, page_count: int = 1) -> None:
    pdf = pikepdf.new()
    image = pdf.make_stream(b"\x00")
    image[Name.Type] = Name.XObject
    image[Name.Subtype] = Name.Image
    image[Name.Width] = 1
    image[Name.Height] = 1
    image[Name.ColorSpace] = Name.DeviceGray
    image[Name.BitsPerComponent] = 8
    image = pdf.make_indirect(image)

    for page_index in range(page_count):
        draws = [b"q 40 0 0 20 10 20 cm /Im0 Do Q"]
        if page_index == 0:
            draws.append(b"q 60 0 0 30 120 140 cm /Im0 Do Q")
        page = pikepdf.Page(
            Dictionary(
                {
                    "/Type": Name.Page,
                    "/MediaBox": Array([0, 0, 300, 300]),
                    "/Contents": pdf.make_stream(b"\n".join(draws)),
                    "/Resources": Dictionary({"/XObject": Dictionary({"/Im0": image})}),
                }
            )
        )
        pdf.pages.append(page)
    pdf.save(path)


def _pending(document: fitz.Document, page_number: int, display_index: int):
    occurrence = _displayed_image_occurrences(document[page_number - 1], page_number)[
        display_index
    ]
    return PendingEquationAssociation(
        **occurrence,
        image_stream_sha256=hashlib.sha256(
            document.extract_image(occurrence["image_xref"])["image"]
        ).hexdigest(),
        alt_text="x squared",
        mathml_string=MATHML,
        provider_used="gemini",
        model_used="vision-model",
        verification_evidence=_evidence(),
    )


def _add_existing_parent_mapping(pdf: pikepdf.Pdf) -> None:
    from src.education.remediation.pdf_structure import PDFStructureTree

    tree = PDFStructureTree(pdf)
    page = pdf.pages[0]
    existing = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name.StructElem,
                "/S": Name.P,
                "/P": tree.struct_root,
                "/Pg": page.obj,
                "/K": Dictionary({"/Type": Name("/MCR"), "/Pg": page.obj, "/MCID": 7}),
            }
        )
    )
    tree.kids.append(existing)
    page.obj[Name.StructParents] = 42
    parent_array = Array([None] * 8)
    parent_array[7] = existing
    tree.struct_root[Name.ParentTree][Name.Nums] = Array(
        [42, pdf.make_indirect(parent_array)]
    )
    original = page.obj[Name.Contents].read_bytes()
    page.obj[Name.Contents] = pdf.make_stream(
        b"/P <</MCID 7>> BDC\n" + original + b"\nEMC"
    )


def test_associates_only_requested_occurrence_when_xref_is_reused(tmp_path):
    """A repeated xref is resolved by full display identity and draw ordinal."""
    from src.education.remediation.content_tagger_v2 import (
        associate_image_formula,
        verify_image_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)

    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success is True
        assert result.mcid >= 0
        pdf.save(output)
    fitz_doc.close()

    assert verify_image_formula_association(output, pending, result) is True

    with pikepdf.open(output) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        marked_draws = []
        stack = []
        for op in ops:
            name = str(op.operator)
            if name == "BDC":
                stack.append((str(op.operands[0]), int(op.operands[1]["/MCID"])))
            elif name == "EMC":
                stack.pop()
            elif name == "Do":
                marked_draws.append(tuple(stack))
        assert marked_draws[0] == ()
        assert marked_draws[1] == (("/Formula", result.mcid),)


def test_reconciliation_promotes_only_postsave_verified_staged_result():
    """A staged result becomes ai_vision FixedIssue only after reverse verification."""
    from types import SimpleNamespace

    from src.education.remediation.base import (
        IssueCategory,
        IssueSeverity,
        ManualIssue,
        RemediationIssue,
        RemediationResult,
    )
    from src.education.remediation.pdf_remediator import PdfRemediator

    issue = RemediationIssue(
        id="equation-1",
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.HIGH,
        description="Equation image is inaccessible",
        metadata={"page_number": 1},
    )
    pending = PendingEquationAssociation(
        page_number=1,
        image_xref=5,
        image_index=1,
        occurrence_ordinal=1,
        bbox=(120.0, 130.0, 180.0, 160.0),
        occurrence_id="source-occurrence",
        image_stream_sha256="c" * 64,
        alt_text="x squared",
        mathml_string=MATHML,
        provider_used="gemini",
        model_used="vision-model",
        verification_evidence=_evidence(),
    )
    staged = MathFixResult(
        success=False,
        error="image_equation_association_pending",
        aria_label=pending.alt_text,
        page_number=1,
        has_mathml=True,
        source_kind="image_equation",
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used="gemini",
        model_used="vision-model",
        verification_evidence=pending.verification_evidence,
        pending_association=pending,
    )
    remediator = PdfRemediator.__new__(PdfRemediator)
    remediator.result = RemediationResult(
        original_file="input.pdf",
        output_file="output.pdf",
        document_type="pdf",
        total_issues=1,
        manual_count=1,
        manual_issues=[
            ManualIssue(
                issue_id=issue.id,
                category=issue.category,
                severity=issue.severity,
                description=issue.description,
                reason="pending",
                recommendation="pending",
            )
        ],
    )
    remediator._verified_image_equations = [
        (issue, staged, SimpleNamespace(success=True))
    ]

    remediator._reconcile_verified_image_equations()

    assert remediator.result.manual_count == 0
    assert remediator.result.manual_issues == []
    assert remediator.result.fixed_count == 1
    fixed = remediator.result.fixed_issues[0]
    assert fixed.fix_method == "ai_vision"
    assert fixed.confidence == 0.55
    assert fixed.needs_review is True
    assert fixed.model_used == "vision-model"
    assert "gemini" in (fixed.notes or "")
    assert pending.verification_evidence.mathml_sha256 in (fixed.notes or "")


def test_pdf_writer_consumes_pending_after_generic_tagger_and_postverifies(tmp_path):
    """Writer associates after v2 tagging and records only reopened verification."""
    from src.education.remediation.base import (
        IssueCategory,
        IssueSeverity,
        RemediationConfig,
        RemediationIssue,
    )
    from src.education.remediation.pdf_remediator import PdfRemediator
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    output = tmp_path / "serialized.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    issue = RemediationIssue(
        id="equation-1",
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.HIGH,
        description="Equation image is inaccessible",
        metadata={"page_number": 1},
    )
    staged = MathFixResult(
        success=False,
        error="image_equation_association_pending",
        aria_label=pending.alt_text,
        page_number=1,
        has_mathml=True,
        source_kind="image_equation",
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used=pending.provider_used,
        model_used=pending.model_used,
        verification_evidence=pending.verification_evidence,
        pending_association=pending,
    )
    remediator = PdfRemediator(
        str(source),
        [issue],
        RemediationConfig(create_backup=False, verify_fixes=False),
    )
    remediator._pdf = fitz_doc
    remediator._pikepdf_doc = pikepdf.open(source)
    remediator._struct_tree = PDFStructureTree(remediator._pikepdf_doc)
    remediator._structure_modified = True
    remediator._pending_image_equations = [(issue, staged)]

    remediator._write_pdf_output(fitz_doc, str(output))

    assert len(remediator._verified_image_equations) == 1
    association = remediator._verified_image_equations[0][2]
    assert association.success is True
    from src.education.remediation.content_tagger_v2 import (
        verify_image_formula_association,
    )

    assert verify_image_formula_association(output, pending, association)
    remediator._pikepdf_doc.close()
    fitz_doc.close()


def test_generic_tagger_preserves_existing_structparents_mcids_and_parenttree(tmp_path):
    """The prerequisite generic pass merges instead of overwriting existing mappings."""
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2

    initial = tmp_path / "initial.pdf"
    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(initial)
    with pikepdf.open(initial) as pdf:
        _add_existing_parent_mapping(pdf)
        pdf.save(source)

    fitz_doc = fitz.open(source)
    with pikepdf.open(source) as pdf:
        ContentTaggerV2(pdf, fitz_doc).tag_all_pages()

        assert int(pdf.pages[0].obj[Name.StructParents]) == 42
        nums = pdf.Root[Name.StructTreeRoot][Name.ParentTree][Name.Nums]
        assert [int(nums[index]) for index in range(0, len(nums), 2)] == [42]
        page_array = nums[1]
        assert len(page_array) > 7
        assert page_array[7] is not None
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        mcids = [
            int(op.operands[1][Name.MCID])
            for op in ops
            if str(op.operator) == "BDC" and Name.MCID in op.operands[1]
        ]
        assert len(mcids) == len(set(mcids))
        assert 7 in mcids
    fitz_doc.close()


def test_cross_page_xref_reuse_marks_only_requested_page(tmp_path):
    from src.education.remediation.content_tagger_v2 import (
        associate_image_formula,
        verify_image_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source, page_count=2)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 2, 0)
    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success
        pdf.save(output)
    fitz_doc.close()

    assert verify_image_formula_association(output, pending, result)
    with pikepdf.open(output) as pdf:
        page_tags = []
        for page in pdf.pages:
            page_tags.append(
                [
                    str(op.operands[0])
                    for op in pikepdf.parse_content_stream(page)
                    if str(op.operator) == "BDC"
                ]
            )
        assert "/Formula" not in page_tags[0]
        assert page_tags[1] == ["/Formula"]


@pytest.mark.parametrize(
    "change",
    [
        {"occurrence_id": "forged-occurrence"},
        {"image_index": 0},
        {"occurrence_ordinal": 0},
        {"bbox": (10.0, 20.0, 50.0, 40.0)},
    ],
)
def test_ambiguous_or_partial_identity_fails_without_bbox_orphan(tmp_path, change):
    from src.education.remediation.content_tagger_v2 import associate_image_formula

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = dataclasses.replace(_pending(fitz_doc, 1, 1), **change)
    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success is False
        root = pdf.Root.get(Name.StructTreeRoot)
        if root is not None:
            assert all(
                str(kid.get(Name.S, "")) != "/Formula"
                for kid in root.get(Name.K, Array([]))
                if hasattr(kid, "keys")
            )
    fitz_doc.close()


def _find_formula(root):
    found = []

    def visit(value):
        if not hasattr(value, "keys"):
            return
        if str(value.get(Name.S, "")) == "/Formula":
            found.append(value)
        kids = value.get(Name.K)
        children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
        for child in children:
            if hasattr(child, "keys") and str(child.get(Name.Type, "")) != "/MCR":
                visit(child)

    kids = root.get(Name.K)
    children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    for child in children:
        visit(child)
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    "sabotage", ["k", "parent_tree", "marked_content", "image_stream", "extra_do"]
)
def test_postsave_reverse_verification_rejects_sabotage(tmp_path, sabotage):
    from src.education.remediation.content_tagger_v2 import (
        associate_image_formula,
        verify_image_formula_association,
    )

    source = tmp_path / "source.pdf"
    associated = tmp_path / "associated.pdf"
    sabotaged = tmp_path / f"{sabotage}.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success
        pdf.save(associated)
    fitz_doc.close()

    with pikepdf.open(associated) as pdf:
        formula = _find_formula(pdf.Root[Name.StructTreeRoot])
        if sabotage == "k":
            del formula[Name.K]
        elif sabotage == "parent_tree":
            nums = pdf.Root[Name.StructTreeRoot][Name.ParentTree][Name.Nums]
            page_array = next(
                nums[index + 1]
                for index in range(0, len(nums), 2)
                if int(nums[index]) == result.struct_parent
            )
            page_array[result.mcid] = None
        elif sabotage == "marked_content":
            replacement = []
            for op in pikepdf.parse_content_stream(pdf.pages[0]):
                if str(op.operator) == "BDC" and str(op.operands[0]) == "/Formula":
                    op = pikepdf.ContentStreamInstruction(
                        [Name.Figure, op.operands[1]], pikepdf.Operator("BDC")
                    )
                replacement.append(op)
            pdf.pages[0].obj[Name.Contents] = pdf.make_stream(
                pikepdf.unparse_content_stream(replacement)
            )
        elif sabotage == "image_stream":
            pdf.pages[0].obj[Name.Resources][Name.XObject]["/Im0"].write(b"\x01")
        else:
            other = pdf.make_stream(b"\x01")
            other[Name.Type] = Name.XObject
            other[Name.Subtype] = Name.Image
            other[Name.Width] = 1
            other[Name.Height] = 1
            other[Name.ColorSpace] = Name.DeviceGray
            other[Name.BitsPerComponent] = 8
            pdf.pages[0].obj[Name.Resources][Name.XObject]["/Im1"] = pdf.make_indirect(
                other
            )
            replacement = []
            inside_formula = False
            for op in pikepdf.parse_content_stream(pdf.pages[0]):
                if str(op.operator) == "BDC" and str(op.operands[0]) == "/Formula":
                    inside_formula = True
                if str(op.operator) == "EMC" and inside_formula:
                    replacement.append(
                        pikepdf.ContentStreamInstruction(
                            [Name("/Im1")], pikepdf.Operator("Do")
                        )
                    )
                    inside_formula = False
                replacement.append(op)
            pdf.pages[0].obj[Name.Contents] = pdf.make_stream(
                pikepdf.unparse_content_stream(replacement)
            )
        pdf.save(sabotaged)

    assert not verify_image_formula_association(sabotaged, pending, result)


def test_pending_equation_forbids_v1_fallback(tmp_path, monkeypatch):
    """Generic tagger failure is fatal when exact Formula association is pending."""
    from types import SimpleNamespace

    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2
    from src.education.remediation.pdf_remediator import PdfRemediator
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    remediator = PdfRemediator(
        str(source), [], RemediationConfig(create_backup=False, verify_fixes=False)
    )
    remediator._pdf = fitz_doc
    remediator._pikepdf_doc = pikepdf.open(source)
    remediator._struct_tree = PDFStructureTree(remediator._pikepdf_doc)
    remediator._structure_modified = True
    remediator._pending_image_equations = [
        (SimpleNamespace(id="equation-1"), SimpleNamespace(pending_association=pending))
    ]
    monkeypatch.setattr(
        ContentTaggerV2,
        "tag_all_pages",
        lambda self: (_ for _ in ()).throw(RuntimeError("sabotaged tagger")),
    )

    with pytest.raises(RuntimeError, match="ContentTaggerV2 failed"):
        remediator._write_pdf_output(fitz_doc, str(output))

    assert remediator._verified_image_equations == []
    remediator._pikepdf_doc.close()
    fitz_doc.close()
