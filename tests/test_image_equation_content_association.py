"""Exact image-occurrence to Formula/MCID/ParentTree association tests."""

from __future__ import annotations

import dataclasses
import hashlib
from io import BytesIO
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

MATHML = "<math><msup><mi>x</mi><mn>2</mn></msup></math>"


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
    """A staged result without its saved-file contract remains manual."""
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

    assert remediator.result.manual_count == 1
    assert len(remediator.result.manual_issues) == 1
    assert remediator.result.fixed_count == 0
    assert remediator.result.fixed_issues == []


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
    _, _, association, contract = remediator._verified_image_equations[0]
    assert association.success is True
    assert contract.contract_kind == "printed_equation"
    assert contract.locator.source_kind == "embedded_image_occurrence"
    assert contract.locator.page_number == 1
    assert contract.locator.image_index == pending.image_index
    assert contract.locator.occurrence_ordinal == pending.occurrence_ordinal
    assert contract.locator.image_stream_sha256 == pending.image_stream_sha256
    saved = next(
        item
        for item in contract.verification_evidence
        if item.evidence_kind == "standalone_formula_saved_v1"
    )
    assert saved.saved_file_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    with fitz.open(output) as saved_pdf:
        saved_occurrence = _displayed_image_occurrences(saved_pdf[0], 1)[1]
    assert contract.locator.image_xref == saved_occurrence["image_xref"]
    assert contract.locator.occurrence_id == saved_occurrence["occurrence_id"]
    assert saved.image_xref == saved_occurrence["image_xref"]
    from src.education.remediation.base import FixedIssue
    from src.jobs.remediation_subprocess import (
        SubprocessRemediationResult,
        _json_record,
    )

    direct_fix = FixedIssue(
        issue_id=issue.id,
        category=issue.category,
        severity=issue.severity,
        description=issue.description,
        fixed_content=staged.aria_label,
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used=pending.provider_used,
        model_used=pending.model_used,
        source_kind="image_equation",
        verification_evidence=dataclasses.asdict(pending.verification_evidence),
        visual_semantic_contract=contract,
        page_number=pending.page_number,
    )
    wire_record = _json_record(direct_fix)
    from src.education.remediation.output_claim import DescriptorBoundOutputClaim
    from src.jobs.remediation_subprocess import RemediationSubprocessError

    with pytest.raises(RemediationSubprocessError, match="^remediation_failed$"):
        SubprocessRemediationResult({"fixed_issues": [wire_record]})

    matching_claim = DescriptorBoundOutputClaim.from_path(
        output,
        display_path=str(output),
        mime="application/pdf",
    )
    matched = SubprocessRemediationResult(
        {"fixed_issues": [wire_record]}, matching_claim
    )
    assert matched.fixed_issues[0].visual_semantic_contract.model_dump(
        mode="json"
    ) == direct_fix.visual_semantic_contract.model_dump(mode="json")
    matched.close_output_claim()

    different_output = tmp_path / "different.pdf"
    different_output.write_bytes(b"different artifact bytes")
    mismatched_claim = DescriptorBoundOutputClaim.from_path(
        different_output,
        display_path=str(different_output),
        mime="application/pdf",
    )
    try:
        with pytest.raises(RemediationSubprocessError, match="^remediation_failed$"):
            SubprocessRemediationResult(
                {"fixed_issues": [wire_record]}, mismatched_claim
            )
    finally:
        mismatched_claim.close()
    from src.education.remediation.content_tagger_v2 import (
        verify_image_formula_association,
    )

    assert verify_image_formula_association(output, pending, association)
    remediator._pikepdf_doc.close()
    fitz_doc.close()


def test_pdf_writer_preserves_destination_when_contract_construction_fails(
    tmp_path, monkeypatch
):
    from src.education.remediation import pdf_remediator
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
    prior_bytes = b"prior approved destination"
    _make_reused_image_pdf(source)
    output.write_bytes(prior_bytes)
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

    def reject_contract(*_args):
        raise ValueError("incomplete contract")

    monkeypatch.setattr(pdf_remediator, "_printed_equation_contract", reject_contract)

    with pytest.raises(
        RuntimeError, match="Verified image equation provenance contract failed"
    ):
        remediator._write_pdf_output(fitz_doc, str(output))

    assert output.read_bytes() == prior_bytes
    assert remediator._verified_image_equations == []
    assert list(tmp_path.glob(".serialized.pdf.verify-*.pdf")) == []
    remediator._pikepdf_doc.close()
    fitz_doc.close()


def test_mixed_table_image_contract_failure_preserves_prior_destination(
    tmp_path, monkeypatch
):
    from src.education.remediation import pdf_remediator
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
    prior_bytes = b"prior approved destination"
    _make_reused_image_pdf(source)
    output.write_bytes(prior_bytes)
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
    remediator._table_expected_bound_cells = 1
    remediator._table_fixed_issue_ids = set()
    remediator._pre_table_pikepdf_doc = pikepdf.open(source)
    remediator._pre_table_structure_modified = True
    monkeypatch.setattr(
        pdf_remediator.TableTagger,
        "verify_file",
        lambda *_args, **_kwargs: True,
    )

    def reject_contract(*_args):
        raise ValueError("incomplete contract")

    monkeypatch.setattr(pdf_remediator, "_printed_equation_contract", reject_contract)

    with pytest.raises(
        RuntimeError, match="Verified image equation provenance contract failed"
    ):
        remediator._write_pdf_output(fitz_doc, str(output))

    assert output.read_bytes() == prior_bytes
    assert remediator._verified_image_equations == []
    assert list(tmp_path.glob(".serialized.pdf.verify-*.pdf")) == []
    remediator._pikepdf_doc.close()
    fitz_doc.close()


def test_public_remediation_preserves_prior_output_on_visual_contract_failure(
    tmp_path, monkeypatch
):
    from src.education.remediation import pdf_remediator
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    remediator = PdfRemediator(
        str(source),
        [
            {
                "id": "equation-1",
                "type": "structure",
                "severity": "high",
                "message": "Equation image is inaccessible",
                "metadata": {"issue_type": "image_equation", "page_number": 1},
            },
            {
                "id": "table-1",
                "type": "table",
                "severity": "high",
                "message": "Table headers are not identified",
                "page_number": 1,
            },
        ],
        RemediationConfig(
            create_backup=False,
            verify_fixes=False,
            output_directory=str(tmp_path),
        ),
    )
    output = Path(remediator._get_output_path())
    prior_bytes = b"prior caller-owned destination"
    output.write_bytes(prior_bytes)
    with fitz.open(source) as source_document:
        pending = _pending(source_document, 1, 1)
    issue = next(issue for issue in remediator.issues if issue.id == "equation-1")
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
    remediator._pending_image_equations = [(issue, staged)]
    monkeypatch.setattr(remediator, "_prepare_table_fixes", lambda: None)

    def stage_with_table_snapshot():
        remediator._working_file_path = str(source)
        remediator._table_expected_bound_cells = 1
        remediator._table_fixed_issue_ids = set()
        remediator._pre_table_pikepdf_doc = pikepdf.open(source)
        remediator._pre_table_structure_modified = True

    monkeypatch.setattr(remediator, "_stage_working_copy", stage_with_table_snapshot)
    monkeypatch.setattr(remediator, "_process_issue", lambda *_args: None)
    monkeypatch.setattr(remediator, "_run_specialist", lambda *_args: None)

    def reject_contract(*_args):
        raise ValueError("incomplete contract")

    monkeypatch.setattr(pdf_remediator, "_printed_equation_contract", reject_contract)

    result = remediator.remediate()

    assert result.success is False
    assert "provenance contract failed" in result.error_message
    assert output.read_bytes() == prior_bytes
    assert list(tmp_path.glob(".*.candidate-*")) == []


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
    "sabotage",
    [
        "k",
        "parent_tree",
        "marked_content",
        "image_stream",
        "extra_do",
        "backlink",
        "bbox",
        "filespec_type",
        "relationship",
        "embedded_type",
        "embedded_subtype",
        "embedded_size",
        "embedded_digest",
    ],
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
        elif sabotage == "backlink":
            formula[Name.P] = pdf.make_indirect(Dictionary({"/Type": Name.StructElem}))
        elif sabotage == "bbox":
            formula[Name.A][Name("/BBox")][0] = -1
        elif sabotage in {
            "filespec_type",
            "relationship",
            "embedded_type",
            "embedded_subtype",
            "embedded_size",
            "embedded_digest",
        }:
            filespec = formula[Name("/AF")][0]
            embedded = filespec[Name("/EF")][Name.F]
            if sabotage == "filespec_type":
                filespec[Name.Type] = Name("/File")
            elif sabotage == "relationship":
                filespec[Name("/AFRelationship")] = Name("/Data")
            elif sabotage == "embedded_type":
                embedded[Name.Type] = Name.XObject
            elif sabotage == "embedded_subtype":
                embedded[Name.Subtype] = Name("/text#2Fplain")
            elif sabotage == "embedded_size":
                embedded[Name("/Params")][Name("/Size")] = 999999
            else:
                embedded[Name("/Params")][Name("/CheckSum")] = pikepdf.String(b"bad")
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


def test_verifier_allows_balanced_nested_artifact_bmc(tmp_path):
    """A non-semantic Artifact BMC may wrap the exact Formula owner."""
    from src.education.remediation.content_tagger_v2 import (
        associate_image_formula,
        verify_image_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / "artifact.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        formula_start = next(
            index
            for index, op in enumerate(ops)
            if str(op.operator) == "BDC" and str(op.operands[0]) == "/Formula"
        )
        formula_end = next(
            index
            for index in range(formula_start + 1, len(ops))
            if str(ops[index].operator) == "EMC"
        )
        ops.insert(
            formula_start,
            pikepdf.ContentStreamInstruction([Name.Artifact], pikepdf.Operator("BMC")),
        )
        ops.insert(
            formula_end + 2,
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")),
        )
        pdf.pages[0].obj[Name.Contents] = pdf.make_stream(
            pikepdf.unparse_content_stream(ops)
        )
        pdf.save(output)
    fitz_doc.close()

    assert verify_image_formula_association(output, pending, result)


@pytest.mark.parametrize("owner_operator", ["BMC", "BDC"])
def test_verifier_rejects_additional_semantic_owner(tmp_path, owner_operator):
    """The image draw must have Formula as its sole semantic owner."""
    from src.education.remediation.content_tagger_v2 import (
        associate_image_formula,
        verify_image_formula_association,
    )

    source = tmp_path / "source.pdf"
    output = tmp_path / f"extra-{owner_operator}.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        start = next(
            index
            for index, op in enumerate(ops)
            if str(op.operator) == "BDC" and str(op.operands[0]) == "/Formula"
        )
        operands = (
            [Name.Figure]
            if owner_operator == "BMC"
            else [Name.Figure, Dictionary({"/MCID": result.mcid + 100})]
        )
        ops.insert(
            start + 1,
            pikepdf.ContentStreamInstruction(
                operands, pikepdf.Operator(owner_operator)
            ),
        )
        do_index = next(
            index
            for index in range(start + 2, len(ops))
            if str(ops[index].operator) == "Do"
        )
        ops.insert(
            do_index + 1,
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")),
        )
        pdf.pages[0].obj[Name.Contents] = pdf.make_stream(
            pikepdf.unparse_content_stream(ops)
        )
        pdf.save(output)
    fitz_doc.close()

    assert not verify_image_formula_association(output, pending, result)


def test_generic_tagger_excludes_exact_pending_equation_draw(tmp_path):
    """The generic pass must not create a Figure owner for the pending draw."""
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        stats = ContentTaggerV2(
            pdf, fitz_doc, excluded_image_occurrences=[pending]
        ).tag_all_pages()
        assert stats["blocks_created"] == 1
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        owners = []
        stack = []
        for op in ops:
            operator = str(op.operator)
            if operator in {"BMC", "BDC"}:
                stack.append(str(op.operands[0]))
            elif operator == "EMC":
                stack.pop()
            elif operator == "Do":
                owners.append(tuple(stack))
        assert owners == [("/Figure",), ()]
    fitz_doc.close()


def test_duplicate_pending_occurrence_rejected_before_tagger_mutation(
    tmp_path, monkeypatch
):
    """Two pending requests for one occurrence fail before generic tagging."""
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
    staged = SimpleNamespace(pending_association=pending)
    remediator._pending_image_equations = [
        (SimpleNamespace(id="equation-1"), staged),
        (SimpleNamespace(id="equation-2"), staged),
    ]
    called = False

    def sabotaged_tagger(self):
        nonlocal called
        called = True
        raise AssertionError("tagger must not run")

    monkeypatch.setattr(ContentTaggerV2, "tag_all_pages", sabotaged_tagger)
    with pytest.raises(
        RuntimeError, match="Duplicate pending image-equation occurrence"
    ):
        remediator._write_pdf_output(fitz_doc, str(output))
    assert called is False
    remediator._pikepdf_doc.close()
    fitz_doc.close()


def _make_form_image_pdf(path: Path) -> None:
    pdf = pikepdf.new()
    image = pdf.make_stream(b"\x00")
    image[Name.Type] = Name.XObject
    image[Name.Subtype] = Name.Image
    image[Name.Width] = 1
    image[Name.Height] = 1
    image[Name.ColorSpace] = Name.DeviceGray
    image[Name.BitsPerComponent] = 8
    image = pdf.make_indirect(image)
    form = pdf.make_stream(b"q 1 0 0 1 0 0 cm /Im0 Do Q")
    form[Name.Type] = Name.XObject
    form[Name.Subtype] = Name.Form
    form[Name("/BBox")] = Array([0, 0, 1, 1])
    form[Name.Resources] = Dictionary({"/XObject": Dictionary({"/Im0": image})})
    form = pdf.make_indirect(form)
    page = pikepdf.Page(
        Dictionary(
            {
                "/Type": Name.Page,
                "/MediaBox": Array([0, 0, 300, 300]),
                "/Contents": pdf.make_stream(b"q 60 0 0 30 120 140 cm /Fm0 Do Q"),
                "/Resources": Dictionary({"/XObject": Dictionary({"/Fm0": form})}),
            }
        )
    )
    pdf.pages.append(page)
    pdf.save(path)


def test_form_xobject_image_fails_closed_before_mutation(tmp_path):
    """v0.9.6 explicitly routes Form-contained image equations to manual review."""
    from src.education.remediation.content_tagger_v2 import associate_image_formula

    source = tmp_path / "form.pdf"
    _make_form_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 0)
    with pikepdf.open(source) as pdf:
        before_content = pdf.pages[0].obj[Name.Contents].read_bytes()
        assert Name.StructTreeRoot not in pdf.Root
        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success is False
        assert result.error == "form_xobject_image_manual"
        assert Name.StructTreeRoot not in pdf.Root
        assert pdf.pages[0].obj[Name.Contents].read_bytes() == before_content
    fitz_doc.close()


def test_form_detection_is_not_hidden_by_earlier_direct_image_alias():
    from src.education.remediation.content_tagger_v2 import (
        _form_xobject_reaches_image,
    )

    pdf = pikepdf.new()
    image = pdf.make_stream(b"\x00")
    image[Name.Type] = Name.XObject
    image[Name.Subtype] = Name.Image
    image[Name.Width] = 1
    image[Name.Height] = 1
    image[Name.ColorSpace] = Name.DeviceGray
    image[Name.BitsPerComponent] = 8
    image = pdf.make_indirect(image)
    form = pdf.make_stream(b"/Nested Do")
    form[Name.Type] = Name.XObject
    form[Name.Subtype] = Name.Form
    form[Name.Resources] = Dictionary({"/XObject": Dictionary({"/Nested": image})})
    form = pdf.make_indirect(form)
    resources = Dictionary({"/XObject": Dictionary({"/Direct": image, "/Form": form})})

    assert _form_xobject_reaches_image(resources, int(image.objgen[0]), set()) is True


def test_parent_tree_kids_preserve_unrelated_direct_entry(tmp_path):
    """Legal /Kids trees are updated in place and annotation mappings survive."""
    from src.education.remediation.content_tagger_v2 import associate_image_formula
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        tree = PDFStructureTree(pdf)
        page_array = pdf.make_indirect(Array([]))
        annotation_owner = pdf.make_indirect(
            Dictionary({"/Type": Name.StructElem, "/S": Name.P})
        )
        leaf = pdf.make_indirect(
            Dictionary(
                {
                    "/Nums": Array([42, page_array, 99, annotation_owner]),
                    "/Limits": Array([42, 99]),
                }
            )
        )
        tree.struct_root[Name.ParentTree] = Dictionary(
            {"/Kids": Array([leaf]), "/Limits": Array([42, 99])}
        )
        pdf.pages[0].obj[Name.StructParents] = 42

        result = associate_image_formula(pdf, fitz_doc, pending)
        assert result.success
        parent_tree = tree.struct_root[Name.ParentTree]
        assert Name("/Kids") in parent_tree and Name.Nums not in parent_tree
        nums = parent_tree[Name("/Kids")][0][Name.Nums]
        entries = {int(nums[i]): nums[i + 1] for i in range(0, len(nums), 2)}
        assert entries[99].objgen == annotation_owner.objgen
        assert isinstance(entries[42], Array)
        assert entries[42][result.mcid] is not None
    fitz_doc.close()


def test_parent_tree_kids_insert_new_key_sorted_and_update_limits(tmp_path):
    from src.education.remediation.content_tagger_v2 import associate_image_formula
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        tree = PDFStructureTree(pdf)
        owner_42 = pdf.make_indirect(
            Dictionary({"/Type": Name.StructElem, "/S": Name.P})
        )
        owner_99 = pdf.make_indirect(
            Dictionary({"/Type": Name.StructElem, "/S": Name.P})
        )
        leaf = pdf.make_indirect(
            Dictionary(
                {
                    "/Nums": Array([42, owner_42, 99, owner_99]),
                    "/Limits": Array([42, 99]),
                }
            )
        )
        tree.struct_root[Name.ParentTree] = Dictionary(
            {"/Kids": Array([leaf]), "/Limits": Array([42, 99])}
        )

        result = associate_image_formula(pdf, fitz_doc, pending)

        assert result.success
        nums = leaf[Name.Nums]
        keys = [int(nums[index]) for index in range(0, len(nums), 2)]
        assert keys == [result.struct_parent, 42, 99]
        assert list(leaf[Name("/Limits")]) == [result.struct_parent, 99]
        assert list(tree.struct_root[Name.ParentTree][Name("/Limits")]) == [
            result.struct_parent,
            99,
        ]
    fitz_doc.close()


def test_association_rolls_back_when_append_sabotaged(tmp_path, monkeypatch):
    """A failure after structure mutation leaves no Formula or content change."""
    import src.education.remediation.content_tagger_v2 as module

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        original_append = module._append_formula_to_structure
        before_content = pdf.pages[0].obj[Name.Contents].read_bytes()
        before_serialized = BytesIO()
        pdf.save(before_serialized)
        before_root = pdf.Root.get(Name.StructTreeRoot)
        before_kids = list(before_root.get(Name.K, Array([]))) if before_root else []

        def sabotage(target_pdf, formula):
            original_append(target_pdf, formula)
            raise RuntimeError("post-mutation sabotage")

        monkeypatch.setattr(module, "_append_formula_to_structure", sabotage)
        result = module.associate_image_formula(pdf, fitz_doc, pending)
        assert result.success is False
        assert pdf.pages[0].obj[Name.Contents].read_bytes() == before_content
        root = pdf.Root.get(Name.StructTreeRoot)
        after_kids = list(root.get(Name.K, Array([]))) if root is not None else []
        assert [kid.objgen for kid in after_kids] == [kid.objgen for kid in before_kids]
        assert not any(str(kid.get(Name.S, "")) == "/Formula" for kid in after_kids)
        after_serialized = BytesIO()
        pdf.save(after_serialized)
        assert after_serialized.getvalue() == before_serialized.getvalue()
    fitz_doc.close()


def test_association_rolls_back_replaced_number_tree_after_stream_failure(
    tmp_path, monkeypatch
):
    import src.education.remediation.content_tagger_v2 as module
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        tree = PDFStructureTree(pdf)
        owner_42 = pdf.make_indirect(
            Dictionary({"/Type": Name.StructElem, "/S": Name.P})
        )
        owner_99 = pdf.make_indirect(
            Dictionary({"/Type": Name.StructElem, "/S": Name.P})
        )
        leaf = pdf.make_indirect(
            Dictionary(
                {
                    "/Nums": Array([42, owner_42, 99, owner_99]),
                    "/Limits": Array([42, 99]),
                }
            )
        )
        tree.struct_root[Name.ParentTree] = Dictionary(
            {"/Kids": Array([leaf]), "/Limits": Array([42, 99])}
        )
        before = BytesIO()
        pdf.save(before)
        monkeypatch.setattr(
            module.pikepdf,
            "unparse_content_stream",
            lambda ops: (_ for _ in ()).throw(RuntimeError("stream sabotage")),
        )

        result = module.associate_image_formula(pdf, fitz_doc, pending)

        assert result.success is False
        after = BytesIO()
        pdf.save(after)
        assert after.getvalue() == before.getvalue()
    fitz_doc.close()


def test_hybrid_parent_tree_kids_and_nums_rejects_without_mutation(tmp_path):
    from src.education.remediation.content_tagger_v2 import associate_image_formula
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, 1)
    with pikepdf.open(source) as pdf:
        tree = PDFStructureTree(pdf)
        owner = pdf.make_indirect(Dictionary({"/Type": Name.StructElem, "/S": Name.P}))
        leaf = pdf.make_indirect(
            Dictionary({"/Nums": Array([42, owner]), "/Limits": Array([42, 42])})
        )
        tree.struct_root[Name.ParentTree] = Dictionary(
            {
                "/Kids": Array([leaf]),
                "/Nums": Array([99, owner]),
                "/Limits": Array([42, 42]),
            }
        )
        before = BytesIO()
        pdf.save(before)

        result = associate_image_formula(pdf, fitz_doc, pending)

        assert result.success is False
        after = BytesIO()
        pdf.save(after)
        assert after.getvalue() == before.getvalue()
    fitz_doc.close()


def test_pdf_writer_emits_handwritten_contract_from_hmer_evidence(tmp_path):
    """The shared save transaction selects HMER from its staged evidence type."""
    from src.education.handwritten_math_suitability import (
        POLICY_SHA256,
        classify_handwritten_math_suitability,
    )
    from src.education.remediation.base import (
        IssueCategory,
        IssueSeverity,
        RemediationConfig,
        RemediationIssue,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HANDWRITTEN_VERIFIER_POLICY_SHA256,
        HANDWRITTEN_VERIFIER_POLICY_VERSION,
        HandwrittenEquationVerificationEvidence,
    )
    from src.education.remediation.pdf_remediator import PdfRemediator
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "source.pdf"
    output = tmp_path / "serialized-hmer.pdf"
    _make_reused_image_pdf(source)
    fitz_doc = fitz.open(source)
    printed_pending = _pending(fitz_doc, 1, 1)
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "handwritten_math"
        / "images"
        / "legible-linear.png"
    ).read_bytes()
    suitability = classify_handwritten_math_suitability(fixture)
    mathml_sha256 = hashlib.sha256(MATHML.encode()).hexdigest()
    consensus = HandwrittenEquationVerificationEvidence(
        passed=True,
        source_sha256=suitability.source_sha256,
        suitability_evidence=suitability,
        suitability_evidence_sha256=suitability.evidence_sha256,
        suitability_policy_sha256=POLICY_SHA256,
        verifier_policy_version=HANDWRITTEN_VERIFIER_POLICY_VERSION,
        verifier_policy_sha256=HANDWRITTEN_VERIFIER_POLICY_SHA256,
        agreement_count=2,
        required_agreement_count=2,
        mathml_sha256=mathml_sha256,
        primary_mathml_sha256=mathml_sha256,
        verifier_mathml_sha256=mathml_sha256,
        primary_response_sha256="1" * 64,
        verifier_response_sha256="2" * 64,
        primary_latex_sha256="3" * 64,
        verifier_latex_sha256="4" * 64,
        primary_provider="fixture-primary",
        primary_model="hmer-primary-v1",
        verifier_provider="fixture-verifier",
        verifier_model="hmer-verifier-v1",
    )
    pending = dataclasses.replace(
        printed_pending,
        provider_used=consensus.primary_provider,
        model_used=consensus.primary_model,
        verification_evidence=consensus,
    )
    issue = RemediationIssue(
        id="handwritten-equation-1",
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.HIGH,
        description="Handwritten equation image is inaccessible",
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
        verification_evidence=consensus,
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
    _, _, association, contract = remediator._verified_image_equations[0]
    assert association.success is True
    assert contract.contract_kind == "handwritten_equation"
    assert contract.normalized_source_sha256 == suitability.source_sha256
    assert {evidence.evidence_kind for evidence in contract.verification_evidence} == {
        "handwritten_equation_consensus_v1",
        "standalone_formula_saved_v1",
    }
    from src.education.remediation.base import FixedIssue
    from src.services.scan_fix_service import (
        build_scan_fix,
        image_equation_review_blockers,
    )

    durable_fix = FixedIssue(
        issue_id=issue.id,
        category=issue.category,
        severity=issue.severity,
        description=issue.description,
        fixed_content=pending.alt_text,
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used=pending.provider_used,
        model_used=pending.model_used,
        source_kind="image_equation",
        verification_evidence=dataclasses.asdict(consensus),
        visual_semantic_contract=contract,
        page_number=1,
    )
    row = build_scan_fix("scan-hmer", durable_fix)
    assert row.review_status == "pending"
    assert row.needs_review is True
    assert "image_equation_not_human_approved" in image_equation_review_blockers([row])
    fitz_doc.close()
