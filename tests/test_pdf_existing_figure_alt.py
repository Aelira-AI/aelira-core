"""Existing image owners are edited only when their full scope is unambiguous."""

import io
import shutil
from contextlib import nullcontext
from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import (
    FigureFailureEvidence,
    missing_figure_occurrences,
    resolve_image_ownership,
)
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pdf_structure import PDFStructureTree
from src.education.validation.matterhorn import CheckpointStatus, MatterhornValidator

pytestmark = pytest.mark.unit


def _fixture(path: Path, mode: str = "valid") -> None:
    png = io.BytesIO()
    Image.new("RGB", (12, 12), "green").save(png, format="PNG")
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(40, 200, 500, 700), "Readable course material. " * 40
        )
        xref = page.insert_image(fitz.Rect(40, 40, 90, 90), stream=png.getvalue())
        page.insert_image(fitz.Rect(140, 40, 190, 90), xref=xref)
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        parent = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, P=root))
        figures, owners, ops = [], [], []
        for op in pikepdf.parse_content_stream(page):
            if str(op.operator) != "Do":
                ops.append(op)
                continue
            index = len(figures)
            figure = pdf.make_indirect(
                pikepdf.Dictionary(S=pikepdf.Name.Figure, P=parent, Pg=page.obj)
            )
            leaf = pdf.make_indirect(
                pikepdf.Dictionary(S=pikepdf.Name.Span, P=figure, K=index)
            )
            figure.K = leaf
            figures.append(figure)
            owners.append(leaf)
            ops.append(
                pikepdf.ContentStreamInstruction(
                    [pikepdf.Name.Figure, pikepdf.Dictionary(MCID=index)],
                    pikepdf.Operator("BDC"),
                )
            )
            if mode == "mixed" and index == 0:
                ops.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("sh")))
            ops.extend(
                [op, pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))]
            )
        parent.K = pikepdf.Array(figures)
        root.K = pikepdf.Array([parent])
        root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array(owners)]))
        )
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        page.obj.StructParents = 0
        if mode == "wrong_parent":
            figures[0].P = root
        elif mode == "wrong_owner":
            root.ParentTree.Nums[1][0] = figures[0]
        elif mode == "shared":
            figures[0].K = pikepdf.Array([owners[0], owners[1]])
            owners[1].P = figures[0]
            parent.K = pikepdf.Array([figures[0]])
        elif mode == "nested":
            figures[0].K = pikepdf.Array([owners[0], figures[1]])
            figures[1].P = figures[0]
            parent.K = pikepdf.Array([figures[0]])
        elif mode == "span_actual_text":
            owners[0].ActualText = pikepdf.String("Existing replacement")
        elif mode == "span_alt":
            owners[0].Alt = pikepdf.String("Existing replacement")
        elif mode == "marker_actual_text":
            next(op for op in ops if str(op.operator) == "BDC").operands[
                1
            ].ActualText = pikepdf.String("Existing replacement")
        elif mode == "ancestor_actual_text":
            parent.ActualText = pikepdf.String("Existing replacement")
        elif mode == "ancestor_alt":
            parent.Alt = pikepdf.String("Existing replacement")
        elif mode == "role_map":
            root.RoleMap = pikepdf.Dictionary(Span=pikepdf.Name.Figure)
        elif mode == "namespace":
            owners[0].NS = pdf.make_indirect(
                pikepdf.Dictionary(NS=pikepdf.String("custom"))
            )
        elif mode == "sibling_actual_text":
            offset = next(
                index for index, op in enumerate(ops) if str(op.operator) == "Do"
            )
            ops[offset:offset] = [
                pikepdf.ContentStreamInstruction(
                    [
                        pikepdf.Name.Span,
                        pikepdf.Dictionary(
                            ActualText=pikepdf.String("Existing replacement")
                        ),
                    ],
                    pikepdf.Operator("BDC"),
                ),
                pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")),
            ]
        elif mode == "orphan":
            figures.append(
                pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.Figure, P=parent))
            )
            parent.K = pikepdf.Array(figures)
        elif mode == "duplicate":
            for op in ops:
                if str(op.operator) == "BDC":
                    op.operands[1].MCID = 0
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def test_same_live_pdf_resolves_nearest_figure_without_append(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    with fitz.open(source) as doc, pikepdf.open(source) as pdf:
        occurrences = _displayed_image_occurrences(doc[0], 1)
        owners = resolve_image_ownership(pdf, str(source), 0, occurrences)
        figure = owners.exclusive_figures[occurrences[0]["occurrence_id"]]
        assert str(figure.S) == "/Figure"
        assert str(figure.K.S) == "/Span"
        before = (
            figure.K.objgen,
            figure.P.objgen,
            pdf.Root.StructTreeRoot.ParentTree.objgen,
        )
        figure.Alt = pikepdf.String("A green square")
        assert before == (
            figure.K.objgen,
            figure.P.objgen,
            pdf.Root.StructTreeRoot.ParentTree.objgen,
        )
        assert len(owners.figures) == 2
        pdf.save(output)
    assert missing_figure_occurrences(str(source)) == FigureFailureEvidence(
        frozenset({(1, 0), (1, 1)}), frozenset(), 2
    )
    assert missing_figure_occurrences(str(output)) == FigureFailureEvidence(
        frozenset({(1, 1)}), frozenset({(1, 0)}), 2
    )
    scan = PDFProcessor().process_pdf(str(output))
    assert [
        i["image_index"]
        for i in scan.issues
        if i.get("issue_type") == "missing_alt_text"
    ] == [1]


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_parent",
        "wrong_owner",
        "shared",
        "mixed",
        "duplicate",
        "orphan",
        "nested",
        "span_actual_text",
        "span_alt",
        "marker_actual_text",
        "ancestor_actual_text",
        "ancestor_alt",
        "role_map",
        "namespace",
        "sibling_actual_text",
    ],
)
def test_unsupported_failure_scope_has_no_complete_proof(mode, tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source, mode)
    assert missing_figure_occurrences(str(source)) is None


def test_partial_existing_figure_repair_is_saved_and_verified(tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source)
    scan = PDFProcessor().process_pdf(str(source))
    images = [
        issue for issue in scan.issues if issue.get("issue_type") == "missing_alt_text"
    ]
    assert len(images) == 2

    class Vision:
        calls = 0

        def analyze_image_sync(self, *, image_data, **_):
            assert image_data
            self.calls += 1
            return {"success": self.calls == 1, "content": "A green square"}

    advisory = dict(
        images[0],
        issue_type="ai_alt_quality_review",
        assessment_type="ai_alt_quality_review",
        review_only=True,
        scoring_included=False,
        has_alt_text=True,
        message="AI assessment asks for review",
    )
    result = PdfRemediator(
        str(source),
        [*scan.issues, advisory],
        RemediationConfig(
            use_ai=True,
            allow_legacy_nested_ai=False,
            create_backup=False,
            output_directory=str(tmp_path / "output"),
        ),
        ai_client=None,
        alt_text_client=Vision(),
    ).remediate()
    try:
        assert result.success
        assert result.verification_passed, result.verification_result
        assert result.has_output_claim()
        image_fixes = [
            fix for fix in result.fixed_issues if fix.category.value == "alt_text"
        ]
        assert len(image_fixes) == 1
        assert image_fixes[0].needs_review
        assert any(
            "excluded from the rule-based score" in issue.reason
            for issue in result.manual_issues
        )
        assert result.score_measurement["source_score"] == scan.compliance_score
        assert result.remediated_compliance_score > result.original_compliance_score
        assert missing_figure_occurrences(result.output_file) == FigureFailureEvidence(
            frozenset({(1, 1)}), frozenset({(1, 0)}), 2
        )
    finally:
        result.close_output_claim()


def _matterhorn_alt_failure(path: Path) -> str:
    checkpoints = MatterhornValidator().validate(str(path)).checkpoints
    alt = next(check for check in checkpoints if check.id == "13-004")
    assert alt.status == CheckpointStatus.FAIL
    return alt.details


def _verify_candidate(source: Path, output: Path, tmp_path: Path, monkeypatch):
    scan = PDFProcessor().process_pdf(str(source))
    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            create_backup=False,
            output_directory=str(tmp_path / "verified"),
        ),
    )
    first = next(
        issue
        for issue in remediator.issues
        if issue.metadata.get("issue_type") == "missing_alt_text"
        and issue.metadata.get("image_index") == 0
    )
    remediator._add_fixed_issue(first, "A green square", "ai_generated")
    # Supply the actual saved candidate to the real scanner, image proof and
    # Matterhorn comparison. Only the output-claim transport is replaced.
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(output)),
    )
    return remediator._verify_fixes(str(output))


def _false_partial_candidate(source: Path, output: Path, mode: str) -> None:
    with pikepdf.open(source) as pdf:
        page = pdf.pages[0]
        parent = pdf.Root.StructTreeRoot.K[0]
        first = parent.K[0]
        if mode == "replacement_figure":
            # Keep the original image MCID reachable through a non-Figure
            # owner, then add an unrelated described Figure. Matterhorn sees
            # one of two Figures fixed, but the first image remains missing.
            first.S = pikepdf.Name.Span
            parent.K.append(
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        S=pikepdf.Name.Figure,
                        P=parent,
                        Pg=page.obj,
                        Alt=pikepdf.String("A green square"),
                    )
                )
            )
        elif mode == "artifact_substitution":
            # The Figure gains /Alt while its image is made an Artifact.
            # The draw no longer belongs to the Figure that got the text.
            first.Alt = pikepdf.String("A green square")
            ops = list(pikepdf.parse_content_stream(page))
            first_marker = next(
                index for index, op in enumerate(ops) if str(op.operator) == "BDC"
            )
            ops[first_marker : first_marker + 1] = [
                pikepdf.ContentStreamInstruction(
                    [pikepdf.Name.Figure, pikepdf.Dictionary(MCID=0)],
                    pikepdf.Operator("BDC"),
                ),
                pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")),
                pikepdf.ContentStreamInstruction(
                    [pikepdf.Name.Artifact], pikepdf.Operator("BMC")
                ),
            ]
            page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        else:
            raise AssertionError(mode)
        pdf.save(output)


@pytest.mark.parametrize("mode", ["replacement_figure", "artifact_substitution"])
def test_partial_matterhorn_count_cannot_mask_lost_image_ownership(
    tmp_path, monkeypatch, mode
):
    source, output = tmp_path / "source.pdf", tmp_path / "candidate.pdf"
    _fixture(source)
    _false_partial_candidate(source, output, mode)

    assert (
        _matterhorn_alt_failure(source) == "2 of 2 figures missing /Alt or /ActualText"
    )
    assert (
        _matterhorn_alt_failure(output) == "1 of 2 figures missing /Alt or /ActualText"
    )
    evidence = missing_figure_occurrences(str(output))
    assert evidence is None or (1, 0) not in evidence.described

    verification = _verify_candidate(source, output, tmp_path, monkeypatch)
    assert verification.passed is False
    assert (
        "matterhorn:13-004:changed_failure_evidence" in verification.unavailable_checks
    )


def test_live_figure_binding_uses_immutable_source_when_stage_xrefs_change(tmp_path):
    source = tmp_path / "source.pdf"
    staged = tmp_path / "staged.pdf"
    output = tmp_path / "output.pdf"
    _fixture(source)
    shutil.copyfile(source, staged)
    with fitz.open(source) as doc:
        source_occurrences = _displayed_image_occurrences(doc[0], 1)
    source_xref = source_occurrences[0]["image_xref"]

    with pikepdf.open(staged, allow_overwriting_input=True) as pdf:
        xobjects = pdf.pages[0].Resources.XObject
        for name, image in list(xobjects.items()):
            if image.objgen[0] != source_xref:
                continue
            replacement = pdf.make_stream(image.read_raw_bytes())
            for key, value in image.items():
                if key != "/Length":
                    replacement[key] = value
            xobjects[pikepdf.Name("/UnusedOriginalImage")] = image
            xobjects[name] = replacement
        pdf.save(staged)
    with fitz.open(staged) as doc:
        staged_occurrences = _displayed_image_occurrences(doc[0], 1)
    assert staged_occurrences[0]["image_xref"] != source_xref

    scan = PDFProcessor().process_pdf(str(source))
    remediator = PdfRemediator(
        str(source), scan.issues, RemediationConfig(use_ai=False)
    )
    issue = next(
        item
        for item in remediator.issues
        if item.metadata.get("issue_type") == "missing_alt_text"
        and item.metadata.get("image_index") == 0
    )
    remediator._working_file_path = str(staged)
    with pikepdf.open(source) as live_pdf, fitz.open(source) as live_view:
        remediator._struct_tree = PDFStructureTree(live_pdf)
        remediator._generated_structure = False
        assert (
            live_pdf.pages[0]
            .Resources.XObject[next(iter(live_pdf.pages[0].Resources.XObject.keys()))]
            .objgen[0]
            == source_xref
        )
        assert remediator._apply_alt_text_fix(issue, live_view, "A green square")
        live_pdf.save(output)

    assert missing_figure_occurrences(str(output)) == FigureFailureEvidence(
        frozenset({(1, 1)}), frozenset({(1, 0)}), 2
    )
