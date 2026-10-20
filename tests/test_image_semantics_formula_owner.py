"""A Formula with its own Alt text owns an image draw, and nothing looser does.

The positive fixture is the saved output of the real association writer, so the
Formula, the marked-content sequence and the ParentTree entry are exactly what
the remediator produces. Every negative starts from that same saved output and
changes one property of the Formula or of its scope.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image
from pikepdf import Array, Dictionary, Name, String

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.completeness import require_complete_pdf_scan
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import (
    FigureFailureEvidence,
    missing_figure_occurrences,
    resolve_image_ownership,
    structured_image_semantics,
)
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
)
from src.education.remediation.math_fixer import MathFixResult
from src.education.remediation.pdf_image_finding_identity import (
    ImageFindingBinding,
    preserve_image_pages,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pdf_structure import PDFStructureTree
from tests.test_image_equation_content_association import _find_formula, _pending

pytestmark = pytest.mark.unit

EQUATION_INDEX = 0
OTHER_INDEX = 1
EQUATION_ALT = "x squared"
MISSING_ALT_MESSAGE = "Image missing alternative text"


def _write_source(path: Path) -> None:
    """One cued equation image and a second draw of the same image far away.

    The text run is long enough (100+ characters) that the processor reads it
    natively instead of falling back to OCR.
    """
    png = io.BytesIO()
    Image.new("RGB", (120, 40), "white").save(png, format="PNG")
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text(
            (72, 80),
            "Equation 1. " + "Readable course material. " * 4,
            fontsize=4,
        )
        xref = page.insert_image(fitz.Rect(72, 90, 192, 130), stream=png.getvalue())
        page.insert_image(fitz.Rect(72, 200, 192, 240), xref=xref)
        doc.save(path)


def _write_saved_equation(source: Path, output: Path) -> None:
    """Run the production writer so the Formula owner is genuinely produced."""
    fitz_doc = fitz.open(source)
    pending = _pending(fitz_doc, 1, EQUATION_INDEX)
    assert pending.alt_text == EQUATION_ALT
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
    try:
        remediator._write_pdf_output(fitz_doc, str(output))
        assert len(remediator._verified_image_equations) == 1
    finally:
        remediator._pikepdf_doc.close()
        fitz_doc.close()


@pytest.fixture(scope="module")
def equation_files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    directory = tmp_path_factory.mktemp("formula-owner")
    source, saved = directory / "source.pdf", directory / "saved.pdf"
    _write_source(source)
    _write_saved_equation(source, saved)
    return source, saved


def _occurrences(path: Path) -> list[dict[str, Any]]:
    with fitz.open(path) as doc:
        return _displayed_image_occurrences(doc[0], 1)


def _semantics(path: Path) -> dict[int, tuple[bool, str | None]]:
    """Resolve through the public entry point; an incomplete scan fails the test."""
    occurrences = _occurrences(path)
    with require_complete_pdf_scan(True):
        result = structured_image_semantics(str(path), 0, occurrences)
    assert result is not None
    return {item["image_index"]: result[item["occurrence_id"]] for item in occurrences}


def _accessible(source: Path, saved: Path) -> frozenset[tuple[int, int]]:
    """The set the remediator trusts after a save (strict scan inside)."""
    occurrence = _occurrences(source)[EQUATION_INDEX]
    binding = ImageFindingBinding(
        key=("alt_text", "Page 1, Image 1", MISSING_ALT_MESSAGE),
        page_number=1,
        image_xref=occurrence["image_xref"],
        image_index=occurrence["image_index"],
        occurrence_ordinal=occurrence["occurrence_ordinal"],
        bbox=tuple(occurrence["bbox"]),
        occurrence_id=occurrence["occurrence_id"],
    )
    saved_images = preserve_image_pages(str(source), str(saved), {"equation": binding})
    return saved_images.accessible


def _findings(path: Path, image_index: int) -> tuple[list[str], list[str]]:
    """Missing-alt messages and equation candidates a fresh scan reports."""
    scan = PDFProcessor().process_pdf(str(path))
    own = [issue for issue in scan.issues if issue.get("image_index") == image_index]
    missing = [
        issue["message"] for issue in own if issue.get("message") == MISSING_ALT_MESSAGE
    ]
    candidates = [
        issue["issue_type"]
        for issue in own
        if issue.get("issue_type") == IMAGE_EQUATION_ISSUE_TYPE
    ]
    return missing, candidates


def _op(name: str, *operands: Any) -> pikepdf.ContentStreamInstruction:
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(name))


def _plain_ops(pdf: pikepdf.Pdf) -> list[pikepdf.ContentStreamInstruction]:
    """The page's paint sequence with every marked-content operator removed."""
    return [
        op
        for op in pikepdf.parse_content_stream(pdf.pages[0])
        if str(op.operator) not in {"BMC", "BDC", "EMC"}
    ]


def _regions(plain: list[pikepdf.ContentStreamInstruction]) -> tuple[int, int, int]:
    """Indexes in `plain` where the text group ends and each `q cm Do Q` starts."""
    names = [str(op.operator) for op in plain]
    text_end = names.index("Q") + 1
    first = names.index("Do") - 2
    second = names.index("Do", names.index("Do") + 1) - 2
    assert names[0] == "q" and names[text_end - 1] == "Q"
    assert (text_end, second, len(names)) == (first, first + 4, second + 4)
    for start in (first, second):
        assert names[start : start + 4] == ["q", "cm", "Do", "Q"]
    return text_end, first, second


def _set_content(pdf: pikepdf.Pdf, ops: list[pikepdf.ContentStreamInstruction]) -> None:
    pdf.pages[0].obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))


def _markers(pdf: pikepdf.Pdf) -> dict[str, Any]:
    """The marker operators the writer emitted, keyed by the role they carry."""
    found: dict[str, Any] = {}
    for op in pikepdf.parse_content_stream(pdf.pages[0]):
        if str(op.operator) == "BDC":
            found[str(op.operands[0])] = op
    return found


def _count_role(pdf: pikepdf.Pdf, role: str) -> int:
    def walk(node: Any) -> int:
        count = int(str(node.get("/S", "")) == role)
        kids = node.get("/K")
        children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
        return count + sum(
            walk(child)
            for child in children
            if isinstance(child, Dictionary) and "/S" in child
        )

    return walk(pdf.Root.StructTreeRoot)


def _text_element(pdf: pikepdf.Pdf) -> Any:
    root = pdf.Root.StructTreeRoot
    document = _find_formula(root).P
    for child in document.K:
        if str(child.S) == "/P":
            return child
    raise AssertionError("text element not found")


def _drop_alt(pdf: pikepdf.Pdf) -> None:
    del _find_formula(pdf.Root.StructTreeRoot)["/Alt"]


def _empty_alt(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).Alt = String("")


def _blank_alt(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).Alt = String("  \t ")


def _actual_text_only(pdf: pikepdf.Pdf) -> None:
    formula = _find_formula(pdf.Root.StructTreeRoot)
    del formula["/Alt"]
    formula.ActualText = String(EQUATION_ALT)


def _alt_and_actual_text(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).ActualText = String(EQUATION_ALT)


def _non_string_alt(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).Alt = Name("/x")


def _undecodable_alt(pdf: pikepdf.Pdf) -> None:
    """A UTF-8 byte-order-marked string that ends mid-character."""
    _find_formula(pdf.Root.StructTreeRoot).Alt = pikepdf.Object.parse(b"<EFBBBFC3>")


def _ancestor_alt(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).P.Alt = String("The whole page")


def _ancestor_actual_text(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).P.ActualText = String("The whole page")


def _remapped_role(pdf: pikepdf.Pdf) -> None:
    pdf.Root.StructTreeRoot.RoleMap = Dictionary({"/Formula": Name.Figure})


def _namespace(pdf: pikepdf.Pdf) -> None:
    _find_formula(pdf.Root.StructTreeRoot).NS = pdf.make_indirect(
        Dictionary(NS=String("urn:example:custom"))
    )


def _shared_mcid(pdf: pikepdf.Pdf) -> None:
    """Both draws sit inside the Formula's one marked-content sequence."""
    plain = _plain_ops(pdf)
    text_end, first, second = _regions(plain)
    marker = _markers(pdf)
    text = [
        plain[0],
        marker["/P"],
        *plain[1 : text_end - 1],
        _op("EMC"),
        plain[text_end - 1],
    ]
    draws = [*plain[first : first + 4], *plain[second : second + 4]]
    # The Figure's MCID stays present (empty) so reading-order stays complete.
    _set_content(
        pdf,
        [*text, marker["/Formula"], *draws, _op("EMC"), marker["/Figure"], _op("EMC")],
    )


def _mixed_mcid(pdf: pikepdf.Pdf) -> None:
    """The Formula's marked content also paints the page's text."""
    plain = _plain_ops(pdf)
    text_end, first, second = _regions(plain)
    marker = _markers(pdf)
    q, cm, draw, restore = plain[second : second + 4]
    # The text element's MCID stays present (empty) so reading-order stays complete.
    _set_content(
        pdf,
        [
            marker["/P"],
            _op("EMC"),
            marker["/Formula"],
            *plain[:text_end],
            *plain[first : first + 4],
            _op("EMC"),
            q,
            cm,
            marker["/Figure"],
            draw,
            _op("EMC"),
            restore,
        ],
    )


def _text_child(pdf: pikepdf.Pdf) -> None:
    """The Formula's structure scope also contains the text element's MCID."""
    formula = _find_formula(pdf.Root.StructTreeRoot)
    text = _text_element(pdf)
    document = formula.P
    document.K = Array([child for child in document.K if child.objgen != text.objgen])
    text.P = formula
    formula.K = Array([formula.K, text])


def _inside_figure(pdf: pikepdf.Pdf) -> None:
    """Formula nested in a Figure that has no Alt of its own."""
    formula = _find_formula(pdf.Root.StructTreeRoot)
    document = formula.P
    figure = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.Figure,
            P=document,
            Pg=pdf.pages[0].obj,
            K=Array([formula]),
        )
    )
    formula.P = figure
    document.K = Array(
        [figure if child.objgen == formula.objgen else child for child in document.K]
    )


def _mutate(saved: Path, tmp_path: Path, change: Callable[[pikepdf.Pdf], None]) -> Path:
    target = tmp_path / "mutated.pdf"
    with pikepdf.open(saved) as pdf:
        change(pdf)
        pdf.save(target)
    return target


def test_source_scan_reports_the_defect(equation_files):
    """Without this the rescan assertions below could pass vacuously."""
    source, _ = equation_files
    missing, candidates = _findings(source, EQUATION_INDEX)
    assert missing == [MISSING_ALT_MESSAGE]
    assert candidates == [IMAGE_EQUATION_ISSUE_TYPE]


def test_structured_semantics_credit_the_formula_alt(equation_files):
    _, saved = equation_files
    semantics = _semantics(saved)
    assert semantics[EQUATION_INDEX] == (True, EQUATION_ALT)
    # The other draw of the same image keeps its generic, undescribed Figure.
    assert semantics[OTHER_INDEX] == (False, None)


def test_saved_image_correspondence_marks_only_the_formula_accessible(equation_files):
    source, saved = equation_files
    assert _accessible(source, saved) == frozenset({(1, EQUATION_INDEX)})


def test_rescan_reports_neither_missing_alt_nor_equation_candidate(equation_files):
    _, saved = equation_files
    missing, candidates = _findings(saved, EQUATION_INDEX)
    assert missing == []
    assert candidates == []
    # The unrelated draw of the same image is still reported.
    other_missing, other_candidates = _findings(saved, OTHER_INDEX)
    assert other_missing == [MISSING_ALT_MESSAGE]
    assert other_candidates == []


def test_formula_alt_is_trimmed_like_figure_alt(equation_files, tmp_path):
    _, saved = equation_files

    def padded(pdf: pikepdf.Pdf) -> None:
        _find_formula(pdf.Root.StructTreeRoot).Alt = String("  x squared \n")

    changed = _mutate(saved, tmp_path, padded)
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


def test_formula_owner_never_becomes_an_exclusive_figure(equation_files, tmp_path):
    _, saved = equation_files
    occurrences = _occurrences(saved)
    equation_id = occurrences[EQUATION_INDEX]["occurrence_id"]
    other_id = occurrences[OTHER_INDEX]["occurrence_id"]

    with pikepdf.open(saved) as pdf:
        ownership = resolve_image_ownership(pdf, str(saved), 0, occurrences)
        assert ownership.semantics[equation_id] == (True, EQUATION_ALT)
        assert equation_id not in ownership.exclusive_figures
        assert set(ownership.exclusive_figures) == {other_id}
        assert len(ownership.figures) == 1

    # Figure-failure evidence ignores the Formula entirely.
    assert missing_figure_occurrences(str(saved)) == FigureFailureEvidence(
        frozenset({(1, OTHER_INDEX)}), frozenset(), 1
    )

    # The remediator therefore still refuses to write a Figure /Alt over it.
    occurrence = occurrences[EQUATION_INDEX]
    issue = RemediationIssue(
        id="generic-alt-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description=MISSING_ALT_MESSAGE,
        location="Page 1, Image 1",
        metadata={
            "page_number": 1,
            "image_xref": occurrence["image_xref"],
            "image_index": occurrence["image_index"],
            "occurrence_ordinal": occurrence["occurrence_ordinal"],
            "bbox": list(occurrence["bbox"]),
            "occurrence_id": occurrence["occurrence_id"],
        },
    )
    target = tmp_path / "refusal.pdf"
    shutil.copyfile(saved, target)
    remediator = PdfRemediator(str(target), [issue], RemediationConfig(use_ai=False))
    with pikepdf.open(target) as live_pdf, fitz.open(target) as live_view:
        remediator._struct_tree = PDFStructureTree(live_pdf)
        remediator._generated_structure = False
        figures = _count_role(live_pdf, "/Figure")
        assert figures == 1
        applied = remediator._apply_alt_text_fix(issue, live_view, "A generic caption")
        assert applied is False
        assert _count_role(live_pdf, "/Figure") == figures
        formula = _find_formula(live_pdf.Root.StructTreeRoot)
        assert str(formula.Alt) == EQUATION_ALT
    reason = remediator._source_binding_refusals[issue.id]
    assert "ambiguous, shared or unsupported" in reason


def test_undecodable_formula_alt_neither_credits_nor_breaks_the_page(
    equation_files, tmp_path
):
    """Other checks cannot read such a string, but ownership still resolves for
    every draw, exactly as it did before Formula owners were considered."""
    source, saved = equation_files
    changed = _mutate(saved, tmp_path, _undecodable_alt)
    assert _semantics(changed) == {
        EQUATION_INDEX: (False, None),
        OTHER_INDEX: (False, None),
    }
    assert _accessible(source, changed) == frozenset()


NEGATIVES = {
    "no_alt": _drop_alt,
    "empty_alt": _empty_alt,
    "blank_alt": _blank_alt,
    "non_string_alt": _non_string_alt,
    "actual_text_only": _actual_text_only,
    "alt_and_actual_text": _alt_and_actual_text,
    "ancestor_alt": _ancestor_alt,
    "ancestor_actual_text": _ancestor_actual_text,
    "remapped_role": _remapped_role,
    "namespace": _namespace,
    "shared_mcid": _shared_mcid,
    "mixed_text_and_image_mcid": _mixed_mcid,
    "text_element_in_formula_scope": _text_child,
    "nested_in_figure": _inside_figure,
}


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_malformed_or_shared_formula_owner_stays_inaccessible(
    name, equation_files, tmp_path
):
    source, saved = equation_files
    changed = _mutate(saved, tmp_path, NEGATIVES[name])

    # Strict scan inside: a refusal caused by an unreadable structure tree
    # would raise here instead of passing for the wrong reason.
    assert _semantics(changed)[EQUATION_INDEX] == (False, None)
    assert _accessible(source, changed) == frozenset()

    missing, candidates = _findings(changed, EQUATION_INDEX)
    assert missing == [MISSING_ALT_MESSAGE]
    assert candidates == [IMAGE_EQUATION_ISSUE_TYPE]


def test_unmutated_copy_is_credited_so_each_refusal_is_the_mutation(
    equation_files, tmp_path
):
    """Round-tripping the saved output unchanged keeps the credit."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, lambda pdf: None)
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)
