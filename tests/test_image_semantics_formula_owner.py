"""A Formula with its own Alt text owns an image draw, and nothing looser does.

The positive fixture is the saved output of the real association writer, so the
Formula, the marked-content sequence and the ParentTree entry are exactly what
the remediator produces. Every negative starts from that same saved output and
applies one targeted edit to the Formula, its ancestors or its marked-content
scope. A helper that needs a second edit to stay well formed says so in its
docstring. Scopes the ownership walk cannot attribute at all (an OBJR kid, a
Form XObject draw, BX/EX) make the whole page unresolvable instead. Those
tests assert the failed-closed result and the incomplete-scan record rather
than using the strict helper, which would raise.

An inline image is different. It is never a draw, so it cannot be credited and
cannot make the page unresolvable. It is paint, like text: every marked-content
sequence it sits in stops being image-only content. The tests for it assert
that the Formula loses its credit, that the page still resolves, and that the
other draws on the page keep theirs.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image
from pikepdf import Array, Dictionary, Name, String

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
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
FIGURE_ALT = "A rule separating the sections"
GENERIC_ALT = "A generic caption"
MISSING_ALT_MESSAGE = "Image missing alternative text"
OWNERSHIP_CHECK = "image_checker.image_semantic_ownership"
# The marked-content id the association writer gives the Formula on page 1.
FORMULA_MCID = 2


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
        consensus_evidence=pending.consensus_evidence,
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


def _semantics(
    path: Path, occurrences: list[dict[str, Any]] | None = None
) -> dict[int, tuple[bool, str | None]]:
    """Resolve through the public entry point; an incomplete scan fails the test.

    `occurrences` defaults to the file's own. A page the walk cannot resolve
    records an incomplete check, so it raises here instead of passing as
    "undescribed".
    """
    occurrences = _occurrences(path) if occurrences is None else occurrences
    with require_complete_pdf_scan(True):
        result = structured_image_semantics(str(path), 0, occurrences)
    assert result is not None
    return {item["image_index"]: result[item["occurrence_id"]] for item in occurrences}


def _rebound_occurrences(saved: Path, changed: Path) -> list[dict[str, Any]]:
    """The saved file's two draws, bound to the changed file's image resources.

    Saving a mutated copy renumbers objects, so the saved file's `image_xref`
    values no longer match. Everything else (index, bbox, id) is unchanged, which
    is what lets a changed file reach the content-stream walk even when its own
    displayed-image indexes would not line up with its draws.
    """
    with pikepdf.open(changed) as pdf:
        page = pdf.pages[0]
        images = page.Resources.XObject
        xrefs = [
            images[str(op.operands[0])].objgen[0]
            for op in pikepdf.parse_content_stream(page)
            if str(op.operator) == "Do"
            and images[str(op.operands[0])].get("/Subtype") == Name.Image
        ]
    occurrences = _occurrences(saved)
    assert len(xrefs) == len(occurrences)
    return [{**item, "image_xref": xref} for item, xref in zip(occurrences, xrefs)]


def _unresolved(
    path: Path, occurrences: list[dict[str, Any]] | None = None
) -> dict[int, tuple[bool, str | None]]:
    """Semantics of a page the ownership walk cannot resolve.

    The scan must record the ownership check as incomplete, so the strict
    helper above would raise. Callers still get the per-draw result, which must
    be undescribed for every draw. `occurrences` defaults to the file's own.
    """
    occurrences = _occurrences(path) if occurrences is None else occurrences
    results: list[dict[str, tuple[bool, str | None]] | None] = []
    with pytest.raises(IncompletePDFScanError, match=OWNERSHIP_CHECK):
        with require_complete_pdf_scan(True):
            results.append(structured_image_semantics(str(path), 0, occurrences))
    (result,) = results
    assert result is not None
    return {item["image_index"]: result[item["occurrence_id"]] for item in occurrences}


def _generic_alt_issue(occurrence: dict[str, Any]) -> RemediationIssue:
    """The ordinary missing-alt finding for one displayed image occurrence."""
    return RemediationIssue(
        id=f"generic-alt-{occurrence['image_index'] + 1}",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description=MISSING_ALT_MESSAGE,
        location=f"Page 1, Image {occurrence['image_index'] + 1}",
        metadata={
            "page_number": 1,
            "image_xref": occurrence["image_xref"],
            "image_index": occurrence["image_index"],
            "occurrence_ordinal": occurrence["occurrence_ordinal"],
            "bbox": list(occurrence["bbox"]),
            "occurrence_id": occurrence["occurrence_id"],
        },
    )


def _write_generic_alt(path: Path, tmp_path: Path, index: int) -> tuple[bool, Any]:
    """Run the Figure Alt writer on a copy of `path` for the draw at `index`.

    Returns whether the writer applied the text and the Figure's resulting Alt.
    """
    occurrence = _occurrences(path)[index]
    issue = _generic_alt_issue(occurrence)
    target = tmp_path / f"writer-{path.stem}.pdf"
    shutil.copyfile(path, target)
    remediator = PdfRemediator(str(target), [issue], RemediationConfig(use_ai=False))
    with pikepdf.open(target) as live_pdf, fitz.open(target) as live_view:
        remediator._struct_tree = PDFStructureTree(live_pdf)
        remediator._generated_structure = False
        applied = remediator._apply_alt_text_fix(issue, live_view, GENERIC_ALT)
        alt = _find_role(live_pdf.Root.StructTreeRoot, "/Figure").get("/Alt")
    return applied, None if alt is None else str(alt)


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


def _findings(
    path: Path, image_index: int, *, require_complete: bool = True
) -> tuple[list[str], list[str]]:
    """Missing-alt messages and equation candidates a fresh scan reports."""
    scan = PDFProcessor(require_complete_scan=require_complete).process_pdf(str(path))
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


def _set_content(
    pdf: pikepdf.Pdf,
    ops: Sequence[pikepdf.ContentStreamInstruction | pikepdf.ContentStreamInlineImage],
) -> None:
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


def _actual_text_instead_of_alt(pdf: pikepdf.Pdf) -> None:
    """Swap /Alt for /ActualText: two edits, and /Alt is gone either way.

    The ActualText guard is isolated by `_alt_and_actual_text`, which keeps a
    valid /Alt. This case pins that ActualText is not an acceptable substitute
    for the description.
    """
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


def _marked_ops(
    pdf: pikepdf.Pdf, role: str = "/Formula"
) -> tuple[list[pikepdf.ContentStreamInstruction], int]:
    """The page's operators and the index of the BDC that opens `role`'s content.

    That marked content is `BDC`, the image's `Do`, `EMC`, so the Do sits at
    `bdc + 1` and the closing EMC at `bdc + 2`.
    """
    ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    names = [str(op.operator) for op in ops]
    bdc = next(
        index
        for index, op in enumerate(ops)
        if names[index] == "BDC" and str(op.operands[0]) == role
    )
    assert names[bdc + 1 : bdc + 3] == ["Do", "EMC"]
    return ops, bdc


def _inline_image_op(pdf: pikepdf.Pdf) -> pikepdf.ContentStreamInlineImage:
    """A 1x1 inline image, parsed so its operator is pikepdf's "INLINE IMAGE"."""
    stream = pdf.make_stream(b"BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI")
    (instruction,) = pikepdf.parse_content_stream(stream)
    assert str(instruction.operator) == "INLINE IMAGE"
    return instruction


def _inline_after_draw(pdf: pikepdf.Pdf) -> None:
    """An inline image follows the Formula's `Do` inside its marked content."""
    ops, bdc = _marked_ops(pdf)
    _set_content(pdf, [*ops[: bdc + 2], _inline_image_op(pdf), *ops[bdc + 2 :]])


def _inline_before_draw(pdf: pikepdf.Pdf) -> None:
    """An inline image precedes the Formula's `Do` inside its marked content."""
    ops, bdc = _marked_ops(pdf)
    _set_content(pdf, [*ops[: bdc + 1], _inline_image_op(pdf), *ops[bdc + 1 :]])


def _inline_nested_after_draw(pdf: pikepdf.Pdf) -> None:
    """An inline image sits in a nested BMC inside the Formula's marked content."""
    ops, bdc = _marked_ops(pdf)
    nested = [_op("BMC", Name.Span), _inline_image_op(pdf), _op("EMC")]
    _set_content(pdf, [*ops[: bdc + 2], *nested, *ops[bdc + 2 :]])


def _inline_after_figure_draw(pdf: pikepdf.Pdf) -> None:
    """An inline image follows the Figure's `Do` inside the Figure's content."""
    ops, bdc = _marked_ops(pdf, "/Figure")
    _set_content(pdf, [*ops[: bdc + 2], _inline_image_op(pdf), *ops[bdc + 2 :]])


def _inline_outside_marked_content(pdf: pikepdf.Pdf) -> None:
    """An inline image after all content, inside no marked-content sequence."""
    ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    _set_content(pdf, [*ops, _inline_image_op(pdf)])


def _bx_ex(pdf: pikepdf.Pdf) -> None:
    """A compatibility section around the whole page content.

    pikepdf reports BX and EX as ordinary operators, unlike an inline image.
    """
    ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    _set_content(pdf, [_op("BX"), *ops, _op("EX")])


def _find_role(root: Any, role: str) -> Any:
    """The single structure element with role `role`."""
    found: list[Any] = []

    def visit(value: Any) -> None:
        if str(value.get("/S", "")) == role:
            found.append(value)
        kids = value.get("/K")
        children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
        for child in children:
            if isinstance(child, Dictionary) and "/S" in child:
                visit(child)

    visit(root)
    assert len(found) == 1
    return found[0]


def _describe_figure(pdf: pikepdf.Pdf) -> None:
    """Give the page's one Figure a description of its own."""
    _find_role(pdf.Root.StructTreeRoot, "/Figure").Alt = String(FIGURE_ALT)


def _formula_group_last(
    pdf: pikepdf.Pdf, *, inline: str | None, describe_figure: bool = False
) -> None:
    """Draw the Formula's image last; optionally put an inline image in its content.

    Swapping the two `q cm BDC Do EMC Q` groups puts any inline image after
    every addressable draw. MuPDF lists an inline image in the page's image
    inventory, so one placed before a draw would shift that draw's displayed
    index; here the displayed indexes still line up with the draws, and only
    the walk's own handling of the inline image is under test.

    `inline` is None, "after" (the Formula's `Do` is followed by the inline
    image) or "nested" (the inline image sits in a BMC inside the Formula's
    sequence). The Figure's description is a second, independent edit.
    """
    ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    names = [str(op.operator) for op in ops]
    first_do = names.index("Do")
    second_do = names.index("Do", first_do + 1)
    formula = ops[first_do - 3 : first_do + 3]
    figure = ops[second_do - 3 : second_do + 3]
    for group in (formula, figure):
        assert [str(op.operator) for op in group] == [
            "q",
            "cm",
            "BDC",
            "Do",
            "EMC",
            "Q",
        ]
    assert str(formula[2].operands[0]) == "/Formula"
    tail: list[Any] = []
    if inline == "after":
        tail = [_inline_image_op(pdf)]
    elif inline == "nested":
        tail = [_op("BMC", Name.Span), _inline_image_op(pdf), _op("EMC")]
    _set_content(
        pdf,
        [*ops[: first_do - 3], *figure, *formula[:4], *tail, *formula[4:]],
    )
    if describe_figure:
        _describe_figure(pdf)


def _described_figure_with_inline_image(pdf: pikepdf.Pdf) -> None:
    """The Figure has a description and an inline image in its marked content."""
    _describe_figure(pdf)
    _inline_after_figure_draw(pdf)


def _bdc_property(position: str, key: str, text: str) -> Callable[[pikepdf.Pdf], None]:
    """A BDC property `key` on, around or inside the Formula's MCID sequence.

    "on" adds it to the Formula's own BDC next to the MCID, "around" opens a
    wrapping `BDC /Span` before the Formula's BDC, and "inside" opens an empty
    `BDC /Span` after it. Nothing is painted by the extra marker.
    """

    def change(pdf: pikepdf.Pdf) -> None:
        ops, bdc = _marked_ops(pdf)
        extra = Dictionary({key: String(text)})
        if position == "on":
            mcid = int(ops[bdc].operands[1].MCID)
            props = Dictionary({"/MCID": mcid, key: String(text)})
            ops[bdc] = _op("BDC", Name.Formula, props)
        elif position == "around":
            ops = [
                *ops[:bdc],
                _op("BDC", Name.Span, extra),
                *ops[bdc : bdc + 3],
                _op("EMC"),
                *ops[bdc + 3 :],
            ]
        else:
            ops = [
                *ops[: bdc + 1],
                _op("BDC", Name.Span, extra),
                _op("EMC"),
                *ops[bdc + 1 :],
            ]
        _set_content(pdf, ops)

    return change


def _nested_bmc(*, paint_text: bool) -> Callable[[pikepdf.Pdf], None]:
    """A plain BMC inside the Formula's MCID sequence, optionally painting text."""

    def change(pdf: pikepdf.Pdf) -> None:
        ops, bdc = _marked_ops(pdf)
        inner = [_op("BMC", Name.Span)]
        if paint_text:
            inner += [
                _op("BT"),
                _op("Tf", Name("/helv"), 4),
                _op("Tj", String("x")),
                _op("ET"),
            ]
        inner.append(_op("EMC"))
        _set_content(pdf, [*ops[: bdc + 2], *inner, *ops[bdc + 2 :]])

    return change


def _ancestor_remapped_role(pdf: pikepdf.Pdf) -> None:
    """The Formula resolves directly, but its Document ancestor is remapped."""
    root = pdf.Root.StructTreeRoot
    _find_formula(root).P.S = Name("/Chapter")
    root.RoleMap = Dictionary({"/Chapter": Name.Sect})


def _ancestor_namespace(pdf: pikepdf.Pdf) -> None:
    """The Formula resolves directly, but its Document ancestor has a namespace."""
    _find_formula(pdf.Root.StructTreeRoot).P.NS = pdf.make_indirect(
        Dictionary(NS=String("urn:example:custom"))
    )


def _child_owns_mcid(pdf: pikepdf.Pdf) -> None:
    """A child element of the Formula owns the MCID; the Formula owns none.

    The ParentTree entry moves to the child, so the structure stays consistent.
    """
    root = pdf.Root.StructTreeRoot
    formula = _find_formula(root)
    child = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.Span,
            P=formula,
            Pg=pdf.pages[0].obj,
            K=formula.K,
        )
    )
    formula.K = Array([child])
    entries = root.ParentTree.Nums[1]
    slot = next(
        index
        for index, owner in enumerate(entries)
        if owner is not None and owner.objgen == formula.objgen
    )
    entries[slot] = child


def _objr_kid(pdf: pikepdf.Pdf) -> None:
    """The Formula also holds an object reference to a page annotation."""
    formula = _find_formula(pdf.Root.StructTreeRoot)
    page = pdf.pages[0].obj
    annotation = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([72, 90, 192, 130]),
        )
    )
    page.Annots = Array([annotation])
    formula.K = Array([formula.K, Dictionary(Type=Name.OBJR, Pg=page, Obj=annotation)])


def _form_xobject_draw(pdf: pikepdf.Pdf) -> None:
    """A Form XObject is drawn inside the Formula's marked content."""
    page = pdf.pages[0]
    form = pdf.make_stream(b"")
    form.Type = Name.XObject
    form.Subtype = Name.Form
    form.BBox = Array([0, 0, 10, 10])
    page.Resources.XObject["/fzForm"] = form
    ops, bdc = _marked_ops(pdf)
    _set_content(pdf, [*ops[: bdc + 2], _op("Do", Name("/fzForm")), *ops[bdc + 2 :]])


def _second_page(owner: str, mcid: int) -> Callable[[pikepdf.Pdf], None]:
    """Append a page whose marked-content id `mcid` is owned through an MCR /Pg.

    `owner` is "formula" (the Formula reaches onto page 2), "text" (the page's
    text element does, so the Formula is untouched) or "nobody" (the page has
    no structure at all).
    """

    def change(pdf: pikepdf.Pdf) -> None:
        root = pdf.Root.StructTreeRoot
        formula = _find_formula(root)
        assert int(formula.K.MCID) == FORMULA_MCID
        pdf.add_blank_page(page_size=(400, 300))
        second = pdf.pages[1].obj
        second.StructParents = 1
        entries = Array([])
        if owner != "nobody":
            element = formula if owner == "formula" else _text_element(pdf)
            second.Contents = pdf.make_stream(
                f"/Span << /MCID {mcid} >> BDC EMC".encode()
            )
            entries = Array([None] * mcid + [element])
            element.K = Array(
                [element.K, Dictionary(Type=Name.MCR, Pg=second, MCID=mcid)]
            )
        tree = root.ParentTree
        tree.Nums.append(1)
        tree.Nums.append(entries)
        tree.Limits = Array([0, 1])

    return change


def _mutate(
    saved: Path,
    tmp_path: Path,
    change: Callable[[pikepdf.Pdf], None],
    name: str = "mutated.pdf",
) -> Path:
    target = tmp_path / name
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
    issue = _generic_alt_issue(occurrences[EQUATION_INDEX])
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
    "actual_text_instead_of_alt": _actual_text_instead_of_alt,
    "alt_and_actual_text": _alt_and_actual_text,
    "ancestor_alt": _ancestor_alt,
    "ancestor_actual_text": _ancestor_actual_text,
    "remapped_role": _remapped_role,
    "namespace": _namespace,
    "shared_mcid": _shared_mcid,
    "mixed_text_and_image_mcid": _mixed_mcid,
    "text_element_in_formula_scope": _text_child,
    "nested_in_figure": _inside_figure,
    "ancestor_remapped_role": _ancestor_remapped_role,
    "ancestor_namespace": _ancestor_namespace,
    "mcid_owned_by_formula_child": _child_owns_mcid,
    **{
        f"bdc_{label}_{position}_mcid": _bdc_property(position, key, EQUATION_ALT)
        for position in ("on", "around", "inside")
        for label, key in (("alt", "/Alt"), ("actual_text", "/ActualText"))
        # An ActualText wrapper outside any MCID also fails the reading-order
        # check by itself; it is covered by OWNERSHIP_ONLY_NEGATIVES instead.
        if (label, position) != ("actual_text", "around")
    },
}

# Changes the whole-file rescan cannot follow for an unrelated reason, so only
# the ownership result is asserted. Painting text changes the page's paint, which
# the save-correspondence proof rejects before ownership is consulted.
OWNERSHIP_ONLY_NEGATIVES = {
    "text_painted_in_nested_bmc": _nested_bmc(paint_text=True),
    "bdc_actual_text_around_mcid": _bdc_property("around", "/ActualText", EQUATION_ALT),
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


@pytest.mark.parametrize("name", sorted(OWNERSHIP_ONLY_NEGATIVES))
def test_ownership_alone_refuses_what_the_rescan_cannot_follow(
    name, equation_files, tmp_path
):
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, OWNERSHIP_ONLY_NEGATIVES[name])
    assert _semantics(changed) == {
        EQUATION_INDEX: (False, None),
        OTHER_INDEX: (False, None),
    }


def test_unmutated_copy_is_credited_so_each_refusal_is_the_mutation(
    equation_files, tmp_path
):
    """Round-tripping the saved output unchanged keeps the credit."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, lambda pdf: None)
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


@pytest.mark.parametrize("position", ["on", "around", "inside"])
def test_bdc_property_that_is_not_replacement_text_keeps_the_credit(
    position, equation_files, tmp_path
):
    """The BDC placements above refuse because of /Alt and /ActualText only."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _bdc_property(position, "/Lang", "en-AU"))
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


def test_nested_bmc_that_paints_nothing_keeps_the_credit(equation_files, tmp_path):
    """Only the text painted inside the nested BMC refuses, not the BMC itself."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _nested_bmc(paint_text=False))
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


@pytest.mark.parametrize(
    "mcid", [0, FORMULA_MCID], ids=["different_number", "same_number"]
)
def test_formula_that_also_owns_an_mcid_on_another_page_is_not_credited(
    mcid, equation_files, tmp_path
):
    """Page 2's MCID can differ from the Formula's here or reuse its number.

    Marked-content ids are only unique per page, so ownership is tracked as
    (page, MCID). A Formula reaching onto another page stops owning "outright"
    even when the number on that page matches the one it owns on page 1.
    """
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _second_page("formula", mcid))
    assert _semantics(changed) == {
        EQUATION_INDEX: (False, None),
        OTHER_INDEX: (False, None),
    }


def test_mcid_number_reused_by_another_element_keeps_the_credit(
    equation_files, tmp_path
):
    """Reuse alone is harmless: the text element owns that number on page 2."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _second_page("text", FORMULA_MCID))
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


def test_second_page_with_no_structure_keeps_the_credit(equation_files, tmp_path):
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _second_page("nobody", FORMULA_MCID))
    assert _semantics(changed)[EQUATION_INDEX] == (True, EQUATION_ALT)


UNRESOLVABLE = {
    "objr_kid": _objr_kid,
    "form_xobject_draw": _form_xobject_draw,
    # Unchanged by the inline-image rule: pikepdf reports BX and EX as plain
    # operators and the walk has never attributed paint inside them.
    "bx_ex": _bx_ex,
}


@pytest.mark.parametrize("name", sorted(UNRESOLVABLE))
def test_unattributable_scope_fails_the_whole_page_closed(
    name, equation_files, tmp_path
):
    """No draw on the page is credited, and the scan is marked incomplete.

    The saved file's occurrences are re-bound to the changed file so the
    resource-number check cannot fail the page first; the scope check in the
    content-stream walk is what is under test.
    """
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, UNRESOLVABLE[name])
    assert _unresolved(changed, _rebound_occurrences(saved, changed)) == {
        EQUATION_INDEX: (False, None),
        OTHER_INDEX: (False, None),
    }


INLINE_IN_FORMULA_CONTENT = {
    "before_the_draw": _inline_before_draw,
    "after_the_draw": _inline_after_draw,
    "nested_in_a_bmc": _inline_nested_after_draw,
}


@pytest.mark.parametrize("name", sorted(INLINE_IN_FORMULA_CONTENT))
def test_inline_image_in_formula_content_is_not_credited_and_the_page_resolves(
    name, equation_files, tmp_path
):
    """The Formula's marked content holds paint that is not its image.

    An inline image before a draw shifts that draw's displayed index, so the
    saved file's occurrences are re-bound to the changed file, as for the
    unresolvable scopes above. The strict helper raises if the page failed to
    resolve, so reaching the assertion proves it did: the walk neither
    credits the Formula nor gives up on the page.
    """
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, INLINE_IN_FORMULA_CONTENT[name])
    assert _semantics(changed, _rebound_occurrences(saved, changed)) == {
        EQUATION_INDEX: (False, None),
        OTHER_INDEX: (False, None),
    }


def test_rebinding_occurrences_alone_keeps_the_formula_credit(equation_files, tmp_path):
    """Control for the inline cases above: only the inline image removes credit."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, lambda pdf: None)
    assert _semantics(changed, _rebound_occurrences(saved, changed)) == {
        EQUATION_INDEX: (True, EQUATION_ALT),
        OTHER_INDEX: (False, None),
    }


# With the Formula drawn last, an inline image placed after its `Do` is listed
# last in the page's image inventory, so no displayed index shifts and the
# page's own occurrences can be used as they are. The Figure is drawn first.
FIGURE_FIRST = 0
FORMULA_LAST = 1


@pytest.mark.parametrize("inline", ["after", "nested"])
def test_unrelated_described_figure_stays_credited_beside_an_inline_image(
    inline, equation_files, tmp_path
):
    """A described Figure elsewhere on the page is not reported as missing alt.

    A non-strict scan, the only kind that reports missing alternative text for
    a page it could not fully inventory, must still credit the Figure. The
    Formula, whose sequence holds the inline image, is not credited.
    """
    _, saved = equation_files
    control = _mutate(
        saved,
        tmp_path,
        partial(_formula_group_last, inline=None, describe_figure=True),
        "control.pdf",
    )
    assert _semantics(control) == {
        FIGURE_FIRST: (True, FIGURE_ALT),
        FORMULA_LAST: (True, EQUATION_ALT),
    }
    # Strict scans refuse nothing without the inline image.
    PDFProcessor().process_pdf(str(control))

    changed = _mutate(
        saved,
        tmp_path,
        partial(_formula_group_last, inline=inline, describe_figure=True),
        "inline.pdf",
    )
    assert [item["image_index"] for item in _occurrences(changed)] == [0, 1]
    assert _semantics(changed) == {
        FIGURE_FIRST: (True, FIGURE_ALT),
        FORMULA_LAST: (False, None),
    }

    figure_missing, figure_candidates = _findings(
        changed, FIGURE_FIRST, require_complete=False
    )
    assert figure_missing == []
    assert figure_candidates == []
    formula_missing, _ = _findings(changed, FORMULA_LAST, require_complete=False)
    assert formula_missing == [MISSING_ALT_MESSAGE]

    # The inline image has no addressable entry, so a strict scan still refuses.
    with pytest.raises(IncompletePDFScanError, match="unaddressable_image"):
        PDFProcessor().process_pdf(str(changed))


def test_inline_image_outside_marked_content_does_not_block_a_formula_credit(
    equation_files, tmp_path
):
    """Outside every marked-content sequence an inline image marks nothing."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _inline_outside_marked_content)
    assert [item["image_index"] for item in _occurrences(changed)] == [0, 1]
    assert _semantics(changed) == {
        EQUATION_INDEX: (True, EQUATION_ALT),
        OTHER_INDEX: (False, None),
    }
    missing, candidates = _findings(changed, EQUATION_INDEX, require_complete=False)
    assert missing == []
    assert candidates == []
    other_missing, _ = _findings(changed, OTHER_INDEX, require_complete=False)
    assert other_missing == [MISSING_ALT_MESSAGE]


def test_inline_image_in_figure_content_is_never_an_exclusive_figure(
    equation_files, tmp_path
):
    """Like text painted in a Figure's content, an inline image stops it being
    an exclusive owner, so the Figure Alt writer refuses to write over it.

    The page still resolves, and the Formula on it keeps its credit.
    """
    _, saved = equation_files
    other_id = _occurrences(saved)[OTHER_INDEX]["occurrence_id"]
    with pikepdf.open(saved) as pdf:
        ownership = resolve_image_ownership(pdf, str(saved), 0, _occurrences(saved))
        assert other_id in ownership.exclusive_figures
    assert missing_figure_occurrences(str(saved)) is not None
    # Control: without the inline image the writer describes this Figure.
    assert _write_generic_alt(saved, tmp_path, OTHER_INDEX) == (True, GENERIC_ALT)

    changed = _mutate(saved, tmp_path, _inline_after_figure_draw)
    occurrences = _occurrences(changed)
    assert [item["image_index"] for item in occurrences] == [0, 1]
    with pikepdf.open(changed) as pdf:
        ownership = resolve_image_ownership(pdf, str(changed), 0, occurrences)
    assert occurrences[OTHER_INDEX]["occurrence_id"] not in ownership.exclusive_figures
    assert _semantics(changed) == {
        EQUATION_INDEX: (True, EQUATION_ALT),
        OTHER_INDEX: (False, None),
    }
    # The Figure-failure evidence for Matterhorn 13-004 is withheld, as it is
    # for a Figure whose content paints text.
    assert missing_figure_occurrences(str(changed)) is None
    assert _write_generic_alt(changed, tmp_path, OTHER_INDEX) == (False, None)


def test_described_figure_with_inline_image_in_its_content_keeps_its_credit(
    equation_files, tmp_path
):
    """Scanner credit comes from the Figure's own Alt, not from exclusivity."""
    _, saved = equation_files
    changed = _mutate(saved, tmp_path, _described_figure_with_inline_image)
    assert _semantics(changed) == {
        EQUATION_INDEX: (True, EQUATION_ALT),
        OTHER_INDEX: (True, FIGURE_ALT),
    }
    missing, candidates = _findings(changed, OTHER_INDEX, require_complete=False)
    assert missing == []
    assert candidates == []
