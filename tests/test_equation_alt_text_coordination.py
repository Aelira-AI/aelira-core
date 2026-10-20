"""One image draw has one owner: the equation fix and its alt-text finding.

The scanner reports an equation image twice: as an equation candidate and as
"Image missing alternative text". The equation specialist runs first. While it
holds the draw, the generic alt-text pass must not call its provider and must
not create a Figure, because a Figure would be a second owner of the same draw.
When the equation is proven in the saved file, the held finding is promoted to
a fix that carries the equation's own description. A draw that already has a
semantic owner, or that cannot be resolved to exactly one drawing operation, is
refused before any equation reading. No classification ran for such a draw, so
its alt-text finding is not held and is handled like any other image.

The full-flow tests run the real scanner, the real remediator and the real
association writer. Only the provider and the visual round-trip verifier are
replaced, because they would need a model and a browser.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pikepdf
import pymupdf as fitz
import pytest
from latex2mathml.converter import convert as latex_to_mathml
from PIL import Image
from pikepdf import Array, Dictionary, Name

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import structured_image_semantics
from src.education.pdf_processor import PDFProcessor
from src.education.remediation import pdf_remediator
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
)
from src.education.remediation.content_tagger_v2 import (
    associate_image_formula,
    image_draw_has_semantic_owner,
    verify_image_formula_association,
)
from src.education.remediation.equation_recognizer import _PROMPT as RECOGNIZER_PROMPT
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.image_equation_gate import contains_image_equation_fixes
from src.education.remediation.math_fixer import (
    IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR,
    IMAGE_EQUATION_EXISTING_OWNER_ERROR,
    MathFixer,
    MathVerificationEvidence,
)
from src.education.remediation.outcome_explanations import manual_explanation
from src.education.remediation.pdf_image_finding_identity import occurrence_identity
from src.education.remediation.pdf_remediator import PdfRemediator
from tests.test_image_equation_content_association import _pending

pytestmark = pytest.mark.unit

LATEX = "x^{2} + 1 = 0"
EQUATION_ALT = "x squared + 1 = 0"
REJECTED_LATEX = "y^{2} = 4"
ALT_TEXT_WITHHELD = "image_equation_alt_text_withheld"
GENERIC_ALT = "A generic description of an image"
GENERIC_PROMPT_START = "Generate concise, descriptive alt text"
EQUATION_INDEX = 0
OTHER_INDEX = 1
SECOND_EQUATION_INDEX = 1
CUE_TEXT = "Equation 1. " + "Readable course material. " * 4


def _png(color: str, size: tuple[int, int]) -> bytes:
    image = io.BytesIO()
    Image.new("RGB", size, color).save(image, format="PNG")
    return image.getvalue()


def _write_untagged_source(path: Path, *, second_image: bool = True) -> None:
    """A cued equation image, and optionally a different image far below it.

    The text run is long enough that the processor reads it natively instead of
    falling back to OCR.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((72, 80), CUE_TEXT, fontsize=4)
        page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png("white", (120, 40)))
        if second_image:
            page.insert_image(
                fitz.Rect(72, 200, 132, 260), stream=_png("green", (60, 60))
            )
        doc.save(path)


def _write_tagged_figure_source(path: Path) -> None:
    """The equation image already belongs to a Figure of a tagged source."""
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((72, 80), CUE_TEXT, fontsize=4)
        page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png("white", (120, 40)))
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        root = pdf.make_indirect(Dictionary(Type=pikepdf.Name.StructTreeRoot))
        parent = pdf.make_indirect(Dictionary(S=pikepdf.Name.P, P=root))
        figure = pdf.make_indirect(
            Dictionary(S=pikepdf.Name.Figure, P=parent, Pg=page.obj)
        )
        leaf = pdf.make_indirect(Dictionary(S=pikepdf.Name.Span, P=figure, K=0))
        figure.K = leaf
        ops = []
        for op in pikepdf.parse_content_stream(page):
            if str(op.operator) == "Do":
                ops.append(
                    pikepdf.ContentStreamInstruction(
                        [pikepdf.Name.Figure, Dictionary(MCID=0)],
                        pikepdf.Operator("BDC"),
                    )
                )
                ops.append(op)
                ops.append(
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))
                )
            else:
                ops.append(op)
        parent.K = Array([figure])
        root.K = Array([parent])
        root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([leaf])])))
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        page.obj.StructParents = 0
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def _write_two_equation_source(path: Path) -> None:
    """Two cued equation images of different widths, far apart on one page."""
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=400)
        page.insert_text((72, 80), CUE_TEXT, fontsize=4)
        page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png("white", (120, 40)))
        page.insert_text((72, 240), CUE_TEXT.replace("1", "2"), fontsize=4)
        page.insert_image(fitz.Rect(72, 250, 212, 290), stream=_png("white", (140, 40)))
        doc.save(path)


Edit = Callable[[pikepdf.Pdf, list[Any]], list[Any]]


def _instruction(operator: str, *operands: Any) -> pikepdf.ContentStreamInstruction:
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def _edit_page_content(path: Path, edit: Edit) -> None:
    """Rewrite page 1's content stream in place through `edit`."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = edit(pdf, list(pikepdf.parse_content_stream(page)))
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def _first_draw(ops: list[Any]) -> int:
    return next(index for index, op in enumerate(ops) if str(op.operator) == "Do")


def _wrap_first_draw(operator: str, *operands: Any) -> Edit:
    """Enclose the equation's `Do` in one marked-content sequence."""

    def edit(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
        index = _first_draw(ops)
        return [
            *ops[:index],
            _instruction(operator, *operands),
            ops[index],
            _instruction("EMC"),
            *ops[index + 1 :],
        ]

    return edit


def _named_property_resource(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    """`/Artifact /MC0 BDC`, with /MC0 a real entry of the page's /Properties."""
    pdf.pages[0].Resources.Properties = Dictionary(
        {"/MC0": Dictionary(Type=Name.Layout)}
    )
    return _wrap_first_draw("BDC", Name.Artifact, Name("/MC0"))(pdf, ops)


def _stray_emc_before_content(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    return [_instruction("EMC"), *ops]


def _stray_emc_after_content(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    return [*ops, _instruction("EMC")]


def _marked_content_never_closed(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    return [*ops, _instruction("BMC", Name.Span)]


def _draw_inside_form_xobject(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    """The equation's image is drawn by a Form XObject, not by the page."""
    index = _first_draw(ops)
    draw = ops[index]
    name = str(draw.operands[0])
    images = pdf.pages[0].Resources.XObject
    form = pdf.make_stream(pikepdf.unparse_content_stream([draw]))
    form.Type = Name.XObject
    form.Subtype = Name.Form
    form.BBox = Array([0, 0, 400, 300])
    form.Resources = Dictionary(XObject=Dictionary({name: images[name]}))
    images["/FmEquation"] = form
    return [*ops[:index], _instruction("Do", Name("/FmEquation")), *ops[index + 1 :]]


def _zero_area_second_draw(pdf: pikepdf.Pdf, ops: list[Any]) -> list[Any]:
    """The same image is drawn again at zero size: two draws, one displayed."""
    index = _first_draw(ops)
    again = [
        _instruction("q"),
        _instruction("cm", 0, 0, 0, 0, 0, 0),
        ops[index],
        _instruction("Q"),
    ]
    return [*ops[: index + 1], *again, *ops[index + 1 :]]


def _image_sha(path: Path, image_index: int) -> str:
    with fitz.open(path) as doc:
        occurrence = _displayed_image_occurrences(doc[0], 1)[image_index]
        return hashlib.sha256(
            doc.extract_image(occurrence["image_xref"])["image"]
        ).hexdigest()


class _Provider:
    """An alt-text-purpose client that records every call by kind."""

    purpose = "alt_text"
    provider = "test-provider"
    model = "test-model"

    def __init__(
        self,
        classification: str = "printed_equation",
        latex_by_width: dict[int, str] | None = None,
    ) -> None:
        self.classification = classification
        # Reading returned for an image of the given pixel width, so one
        # document can hold equations that read differently.
        self.latex_by_width = latex_by_width or {}
        self.calls: list[tuple[str, int]] = []
        self.generic_images: list[str] = []

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        if prompt == RECOGNIZER_PROMPT:
            self.calls.append(("recognition", len(image_data)))
            with Image.open(io.BytesIO(image_data)) as image:
                latex = self.latex_by_width.get(image.width, LATEX)
            payload = (
                {"classification": "printed_equation", "latex": latex}
                if self.classification == "printed_equation"
                else {"classification": "not_equation", "latex": None}
            )
            return {"success": True, "content": json.dumps(payload)}
        if prompt.startswith(GENERIC_PROMPT_START):
            self.calls.append(("generic", len(image_data)))
            self.generic_images.append(hashlib.sha256(image_data).hexdigest())
            return {"success": True, "content": GENERIC_ALT}
        self.calls.append(("other", len(image_data)))
        return {"success": False, "content": ""}

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


class _Verifier:
    """Stands in for the browser round trip: it passes or fails on demand.

    `passes` is a fixed answer, or a function of the LaTeX reading when one
    document needs one equation accepted and another rejected.
    """

    def __init__(self, passes: bool | Callable[[str], bool]) -> None:
        self.passes = passes

    def canonicalize_mathml(self, mathml: str) -> str:
        return canonicalize_mathml(mathml)

    def verify(self, image: Any, latex: str) -> MathVerificationEvidence:
        mathml = self.canonicalize_mathml(latex_to_mathml(latex))
        return MathVerificationEvidence(
            passed=self.passes(latex) if callable(self.passes) else self.passes,
            source_sha256=image.normalized_sha256,
            rendered_sha256="b" * 64,
            mathml_sha256=hashlib.sha256(mathml.encode()).hexdigest(),
            renderer_version="renderer-v1",
            comparator_version="comparator-v1",
            font_sha256="f" * 64,
            threshold_version="threshold-v1",
            ink_iou=1.0,
            pixel_similarity=1.0,
            required_ink_iou=0.9,
            required_pixel_similarity=0.98,
        )


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    def install(*, passes: bool | Callable[[str], bool] = True) -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", _Verifier(passes))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


def _run(
    source: Path, tmp_path: Path, provider: _Provider, *, verify_fixes: bool = True
):
    scan = PDFProcessor().process_pdf(str(source))
    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=True,
            allow_legacy_nested_ai=False,
            create_backup=False,
            verify_fixes=verify_fixes,
            output_directory=str(tmp_path / "out"),
        ),
        ai_client=None,
        alt_text_client=provider,
    )
    return remediator, remediator.remediate()


def _issue_ids(remediator: PdfRemediator, image_index: int) -> dict[str, str]:
    """The equation candidate and the missing-alt finding of one image draw."""
    found: dict[str, str] = {}
    for issue in remediator.issues:
        if issue.metadata.get("image_index") != image_index:
            continue
        kind = issue.metadata.get("issue_type")
        if kind in {IMAGE_EQUATION_ISSUE_TYPE, "missing_alt_text"}:
            found[kind] = issue.id
    return found


def _manual(result: Any, issue_id: str) -> Any:
    matches = [item for item in result.manual_issues if item.issue_id == issue_id]
    assert len(matches) == 1, [item.issue_id for item in result.manual_issues]
    return matches[0]


def _fixed(result: Any, issue_id: str) -> Any:
    matches = [item for item in result.fixed_issues if item.issue_id == issue_id]
    assert len(matches) == 1, [item.issue_id for item in result.fixed_issues]
    return matches[0]


def _structure(path: str) -> list[tuple[str, str | None]]:
    """Every structure element as (role, alt) in document order."""
    found: list[tuple[str, str | None]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, Dictionary) or "/S" not in node:
            return
        alt = node.get("/Alt")
        found.append((str(node.S), str(alt) if alt is not None else None))
        kids = node.get("/K")
        for child in list(kids) if isinstance(kids, Array) else [kids]:
            walk(child)

    with pikepdf.open(path) as pdf:
        if "/StructTreeRoot" in pdf.Root:
            kids = pdf.Root.StructTreeRoot.get("/K")
            for child in list(kids) if isinstance(kids, Array) else [kids]:
                walk(child)
    return found


def _rescan_missing_alt(path: str, image_index: int) -> list[str]:
    scan = PDFProcessor().process_pdf(path)
    return [
        issue["message"]
        for issue in scan.issues
        if issue.get("image_index") == image_index
        and issue.get("issue_type") == "missing_alt_text"
    ]


# ---------------------------------------------------------------------------
# Untagged source: the equation owns the draw, the alt-text finding follows it
# ---------------------------------------------------------------------------


def test_deferred_occurrence_never_reaches_the_alt_text_provider(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        # One reading of the equation, and one generic description for the
        # other image only. The equation draw is never described generically.
        assert provider.kinds.count("recognition") == 1
        assert provider.generic_images == [_image_sha(source, OTHER_INDEX)]
        assert _image_sha(source, EQUATION_INDEX) not in provider.generic_images
        assert provider.kinds.count("other") == 0
    finally:
        result.close_output_claim()


def test_verified_equation_promotes_its_alt_text_finding(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert result.verification_passed, result.verification_result
        ids = _issue_ids(remediator, EQUATION_INDEX)

        equation_fix = _fixed(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation_fix.source_kind == "image_equation"
        assert equation_fix.fixed_content == EQUATION_ALT
        assert contains_image_equation_fixes(result.fixed_issues)

        promoted = _fixed(result, ids["missing_alt_text"])
        assert promoted.fixed_content == EQUATION_ALT
        assert promoted.needs_review is True
        assert promoted.confidence is not None and promoted.confidence <= 0.55
        assert promoted.source_kind is None
        assert promoted.visual_semantic_contract is None
        assert promoted.verification_passed is True
        assert promoted.saved_file_verification["issue_id"] == promoted.issue_id
        assert not any(
            item.issue_id == ids["missing_alt_text"] for item in result.manual_issues
        )
    finally:
        result.close_output_claim()


def test_one_formula_and_no_orphan_figure_for_the_deferred_draw(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert result.success, result.error_message
        structure = _structure(result.output_file)
        formulas = [alt for role, alt in structure if role == "/Formula"]
        figures = [alt for role, alt in structure if role == "/Figure"]
        assert formulas == [EQUATION_ALT]
        # Exactly one Figure: the other image's. The equation draw has none.
        assert figures == [GENERIC_ALT]
        assert _rescan_missing_alt(result.output_file, EQUATION_INDEX) == []
        assert _rescan_missing_alt(result.output_file, OTHER_INDEX) == []
    finally:
        result.close_output_claim()


def test_equation_with_unverified_reading_stays_manual_and_undescribed(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        assert _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "equation_verification_failed"
        )
        # The equation keeps its own error. The image's alt-text finding gets
        # the dedicated reason that no description was written for it.
        alt_manual = _manual(result, ids["missing_alt_text"])
        assert alt_manual.reason == ALT_TEXT_WITHHELD
        assert manual_explanation(alt_manual)["reason_code"] == ALT_TEXT_WITHHELD
        assert "no generic image description" in alt_manual.recommendation
        # Neither the provider nor the structure tree describes that image.
        assert _image_sha(source, EQUATION_INDEX) not in provider.generic_images
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == []
        # The generic tagger still gives the undescribed equation image its
        # ordinary alt-less Figure. No description of it exists anywhere.
        figures = [alt for role, alt in structure if role == "/Figure"]
        assert sorted(figures, key=str) == sorted([GENERIC_ALT, None], key=str)
        assert not contains_image_equation_fixes(result.fixed_issues)
    finally:
        result.close_output_claim()


def test_not_equation_classification_still_gets_generic_alt_text(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider(classification="not_equation")

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        # The specialist did not recognise an equation, so nothing blocks the
        # ordinary description of that image.
        assert _image_sha(source, EQUATION_INDEX) in provider.generic_images
        generic = _fixed(result, ids["missing_alt_text"])
        assert generic.fixed_content == GENERIC_ALT
        assert generic.source_kind is None
        assert not contains_image_equation_fixes(result.fixed_issues)
        assert _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE]).reason
    finally:
        result.close_output_claim()


def test_one_verified_and_one_rejected_equation_in_one_document(
    tmp_path: Path, install_verifier
) -> None:
    """The verified equation is promoted and gated; the rejected one writes nothing."""
    install_verifier(passes=lambda latex: latex == LATEX)
    source = tmp_path / "source.pdf"
    _write_two_equation_source(source)
    provider = _Provider(latex_by_width={120: LATEX, 140: REJECTED_LATEX})

    remediator, result = _run(source, tmp_path, provider)
    try:
        # The review-gated guard passes because every association written has
        # its gated fix, so the run as a whole still succeeds.
        assert result.success, result.error_message
        accepted = _issue_ids(remediator, EQUATION_INDEX)
        rejected = _issue_ids(remediator, SECOND_EQUATION_INDEX)

        # The rejected image keeps its alt-less Figure, which the document-level
        # Matterhorn check cannot clear, so only the verified fixes are judged.
        equation_fix = _fixed(result, accepted[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation_fix.source_kind == "image_equation"
        assert equation_fix.fixed_content == EQUATION_ALT
        assert equation_fix.needs_review is True
        assert equation_fix.verification_passed is True
        promoted = _fixed(result, accepted["missing_alt_text"])
        assert promoted.fixed_content == EQUATION_ALT
        assert promoted.needs_review is True
        assert promoted.source_kind is None
        assert promoted.verification_passed is True

        assert (
            _manual(result, rejected[IMAGE_EQUATION_ISSUE_TYPE]).reason
            == "equation_verification_failed"
        )
        withheld = _manual(result, rejected["missing_alt_text"])
        assert withheld.reason == ALT_TEXT_WITHHELD
        assert manual_explanation(withheld)["reason_code"] == ALT_TEXT_WITHHELD
        gated = [
            fix for fix in result.fixed_issues if fix.source_kind == "image_equation"
        ]
        assert [fix.issue_id for fix in gated] == [accepted[IMAGE_EQUATION_ISSUE_TYPE]]
        assert contains_image_equation_fixes(result.fixed_issues)
        assert len(remediator._verified_image_equations) == 1

        # Both readings were paid for. Neither equation image was described
        # generically, and the rejected one has no Formula and no description.
        assert provider.kinds.count("recognition") == 2
        assert provider.generic_images == []
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == [EQUATION_ALT]
        assert [alt for role, alt in structure if role == "/Figure"] == [None]
        assert _rescan_missing_alt(result.output_file, EQUATION_INDEX) == []
        assert _rescan_missing_alt(result.output_file, SECOND_EQUATION_INDEX) == [
            "Image missing alternative text"
        ]
    finally:
        result.close_output_claim()


def test_promoted_alt_text_without_saved_file_verification(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider, verify_fixes=False)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)

        equation_fix = _fixed(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation_fix.source_kind == "image_equation"
        assert equation_fix.fixed_content == EQUATION_ALT
        promoted = _fixed(result, ids["missing_alt_text"])
        assert promoted.fixed_content == EQUATION_ALT
        assert promoted.needs_review is True
        assert promoted.confidence is not None and promoted.confidence <= 0.55
        assert promoted.source_kind is None
        # No rescan ran, so neither fix carries a saved-file receipt.
        assert equation_fix.saved_file_verification is None
        assert promoted.saved_file_verification is None
        assert contains_image_equation_fixes(result.fixed_issues)

        # One reading of the equation, one generic description for the other
        # image, and nothing else.
        assert provider.kinds.count("recognition") == 1
        assert provider.generic_images == [_image_sha(source, OTHER_INDEX)]
        assert provider.kinds.count("other") == 0
        assert len(provider.calls) == 2
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == [EQUATION_ALT]
        assert [alt for role, alt in structure if role == "/Figure"] == [GENERIC_ALT]
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# Existing owner and unresolved draw: refused before any equation reading
# ---------------------------------------------------------------------------


def test_figure_owned_equation_is_refused_and_its_image_is_still_described(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "tagged.pdf"
    _write_tagged_figure_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        # No equation was read. The ordinary description of a Figure that lacks
        # alternative text ran once for the one image on the page.
        assert provider.kinds == ["generic"]
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        assert (
            manual_explanation(equation)["reason_code"]
            == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        )
        # No classification ran, so nothing holds the alt-text finding. It is
        # fixed by writing into the Figure that already owns the draw.
        assert remediator._equation_holds == {}
        described = _fixed(result, ids["missing_alt_text"])
        assert described.fixed_content == GENERIC_ALT
        assert described.source_kind is None
        assert described.verification_passed is True
        assert result.verification_passed, result.verification_result
        assert not contains_image_equation_fixes(result.fixed_issues)
        structure = _structure(result.output_file)
        assert [role for role, _ in structure if role == "/Formula"] == []
        assert [alt for role, alt in structure if role == "/Figure"] == [GENERIC_ALT]
        assert _rescan_missing_alt(result.output_file, EQUATION_INDEX) == []
    finally:
        result.close_output_claim()


# Property forms the saved-file verification does not read as a plain Artifact.
ARTIFACT_WITH_PROPERTY_LIST = {
    "dictionary_without_mcid": _wrap_first_draw(
        "BDC", Name.Artifact, Dictionary(Type=Name.Layout)
    ),
    "named_property_resource": _named_property_resource,
}


@pytest.mark.parametrize("case", sorted(ARTIFACT_WITH_PROPERTY_LIST))
def test_artifact_with_a_property_list_is_refused_before_any_equation_reading(
    case: str, tmp_path: Path, install_verifier
) -> None:
    """The verification counted such an Artifact as another owner after the fact.

    The probe now reads it the same way, so the equation is refused before the
    provider is paid, and the run no longer fails as a whole.
    """
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, ARTIFACT_WITH_PROPERTY_LIST[case])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert result.verification_passed, result.verification_result
        assert provider.kinds.count("recognition") == 0
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        assert (
            manual_explanation(equation)["reason_code"]
            == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        )
        # Both images are described by the ordinary pass, the equation's too.
        assert remediator._equation_holds == {}
        assert sorted(provider.generic_images) == sorted(
            [_image_sha(source, EQUATION_INDEX), _image_sha(source, OTHER_INDEX)]
        )
        assert _fixed(result, ids["missing_alt_text"]).fixed_content == GENERIC_ALT
        assert not contains_image_equation_fixes(result.fixed_issues)
        assert result.fixed_count > 0
    finally:
        result.close_output_claim()


def test_plain_artifact_around_the_draw_is_refused_before_any_equation_reading(
    tmp_path: Path, install_verifier
) -> None:
    """Any marked content around the draw makes the equation unsupported.

    The writer and the saved-file verification accept a plain Artifact, but the
    ownership walk of the saved-file rescan refuses a marked-content id nested
    inside one. That used to fail the run after the reading was paid for and
    set the written file aside.
    """
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, _wrap_first_draw("BMC", Name.Artifact))
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert result.verification_passed, result.verification_result
        assert provider.kinds.count("recognition") == 0
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        assert (
            manual_explanation(equation)["reason_code"]
            == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        )
        # The other findings are handled as usual, the equation image's included.
        assert remediator._equation_holds == {}
        assert sorted(provider.generic_images) == sorted(
            [_image_sha(source, EQUATION_INDEX), _image_sha(source, OTHER_INDEX)]
        )
        assert _fixed(result, ids["missing_alt_text"]).fixed_content == GENERIC_ALT
        assert result.fixed_count > 0
        assert not contains_image_equation_fixes(result.fixed_issues)
        # The output was published and nothing was set aside.
        assert result.output_file and Path(result.output_file).is_file()
        assert list((tmp_path / "out").rglob("*failed-output*")) == []
        assert [role for role, _ in _structure(result.output_file)].count(
            "/Formula"
        ) == 0
    finally:
        result.close_output_claim()


# Marked content that does not balance cannot be resolved to one drawing
# operation, and the saved file could not be verified either.
UNBALANCED_MARKED_CONTENT = {
    "stray_emc_before_content": _stray_emc_before_content,
    "stray_emc_after_content": _stray_emc_after_content,
    "marked_content_never_closed": _marked_content_never_closed,
}


@pytest.mark.parametrize("case", sorted(UNBALANCED_MARKED_CONTENT))
@pytest.mark.parametrize("verify_fixes", [True, False])
def test_unresolvable_draw_is_refused_before_any_equation_reading(
    case: str, verify_fixes: bool, tmp_path: Path, install_verifier
) -> None:
    """Before this, the reading was paid for and the writer then failed the run."""
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, UNBALANCED_MARKED_CONTENT[case])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider, verify_fixes=verify_fixes)
    try:
        assert result.success, result.error_message
        assert provider.kinds.count("recognition") == 0
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR
        assert (
            manual_explanation(equation)["reason_code"]
            == IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR
        )
        assert remediator._equation_holds == {}
        assert not contains_image_equation_fixes(result.fixed_issues)
        if not verify_fixes:
            # With the saved-file rescan off, the page's other findings are
            # applied as usual, the equation image's description included.
            assert result.fixed_count > 0
            described = _fixed(result, ids["missing_alt_text"])
            assert described.fixed_content == GENERIC_ALT
            assert sorted(provider.generic_images) == sorted(
                [_image_sha(source, EQUATION_INDEX), _image_sha(source, OTHER_INDEX)]
            )
    finally:
        result.close_output_claim()


def test_recognised_equation_that_fails_still_holds_its_alt_text_finding(
    tmp_path: Path, install_verifier
) -> None:
    """Holding is for a recognised equation only: the generic path stays out."""
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert [hold.state for hold in remediator._equation_holds.values()] == [
            "blocked"
        ]
        assert _image_sha(source, EQUATION_INDEX) not in provider.generic_images
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# Ownership probe
# ---------------------------------------------------------------------------


def _probe(path: Path, **overrides: Any) -> bool | None:
    with fitz.open(path) as doc, pikepdf.open(path) as pdf:
        occurrence = _displayed_image_occurrences(doc[0], 1)[EQUATION_INDEX]
        identity = {
            "page_number": occurrence["page_number"],
            "image_xref": occurrence["image_xref"],
            "image_index": occurrence["image_index"],
            "occurrence_ordinal": occurrence["occurrence_ordinal"],
            "bbox": tuple(occurrence["bbox"]),
            "occurrence_id": occurrence["occurrence_id"],
            **overrides,
        }
        return image_draw_has_semantic_owner(pdf, doc, **identity)


def test_probe_reports_a_bare_draw_as_unowned(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    assert _probe(source) is False


def test_probe_reports_a_figure_wrapped_draw_as_owned(tmp_path: Path) -> None:
    source = tmp_path / "tagged.pdf"
    _write_tagged_figure_source(source)
    assert _probe(source) is True


class OwnerCase(NamedTuple):
    """One shape of page content around the equation's draw.

    `probe` is what the ownership probe reports. `verified` is whether the real
    association writer and the saved-file verification accept the page, and
    `rescanned` whether the ownership walk of the saved-file rescan then credits
    the Formula. The probe must refuse whatever any of the three would refuse.
    """

    edit: Edit
    probe: bool | None
    verified: bool
    rescanned: bool


# Every way the page can enclose, or fail to resolve, the equation's draw. The
# probe reports True or None exactly when the writer, the verification or the
# rescan walk would reject the written Formula, and False only when all three
# accept it. A plain Artifact is the case that needs all three: the writer and
# the verification accept it, and only the rescan walk refuses a marked-content
# id nested inside it, which would fail the run after the provider was paid.
OWNER_CASES: dict[str, OwnerCase] = {
    "bare_draw": OwnerCase(lambda pdf, ops: ops, False, True, True),
    "paragraph_with_mcid": OwnerCase(
        _wrap_first_draw("BDC", Name.P, Dictionary(MCID=0)), True, False, False
    ),
    "span_without_properties": OwnerCase(
        _wrap_first_draw("BMC", Name.Span), True, False, True
    ),
    "artifact_without_properties": OwnerCase(
        _wrap_first_draw("BMC", Name.Artifact), True, True, False
    ),
    "artifact_dictionary_without_mcid": OwnerCase(
        ARTIFACT_WITH_PROPERTY_LIST["dictionary_without_mcid"], True, False, False
    ),
    "artifact_named_property_resource": OwnerCase(
        ARTIFACT_WITH_PROPERTY_LIST["named_property_resource"], True, False, False
    ),
    "stray_emc_before_content": OwnerCase(
        _stray_emc_before_content, None, False, False
    ),
    "stray_emc_after_content": OwnerCase(_stray_emc_after_content, None, False, False),
    "marked_content_never_closed": OwnerCase(
        _marked_content_never_closed, None, False, False
    ),
    "draw_inside_form_xobject": OwnerCase(
        _draw_inside_form_xobject, None, False, False
    ),
    "zero_area_second_draw": OwnerCase(_zero_area_second_draw, None, False, False),
}


def _owner_source(tmp_path: Path, case: str) -> Path:
    source = tmp_path / f"{case}.pdf"
    _write_untagged_source(source, second_image=False)
    _edit_page_content(source, OWNER_CASES[case].edit)
    return source


@pytest.mark.parametrize("case", sorted(OWNER_CASES))
def test_probe_reports_what_the_enclosing_marked_content_makes_of_the_draw(
    case: str, tmp_path: Path
) -> None:
    source = _owner_source(tmp_path, case)
    assert _probe(source) is OWNER_CASES[case].probe


def _written_and_verified(source: Path, output: Path) -> bool:
    """Run the real association writer and the saved-file verification."""
    return _written_verified_and_rescanned(source, output)[0]


def _written_verified_and_rescanned(source: Path, output: Path) -> tuple[bool, bool]:
    """Whether the writer and verification accept, and the rescan credits it.

    The second value runs the ownership walk the saved-file rescan uses, in a
    strict scan, on the saved output. It is True only when that walk completes
    and credits the Formula's alternative text to the equation's draw.
    """
    with fitz.open(source) as fitz_doc, pikepdf.open(source) as pdf:
        pending = _pending(fitz_doc, 1, EQUATION_INDEX)
        result = associate_image_formula(pdf, fitz_doc, pending)
        if not result.success:
            return False, False
        pdf.save(output)
    verified = verify_image_formula_association(output, pending, result)
    with fitz.open(output) as saved:
        occurrences = _displayed_image_occurrences(saved[0], 1)
    try:
        with require_complete_pdf_scan(True):
            semantics = structured_image_semantics(str(output), 0, occurrences)
    except IncompletePDFScanError:
        return verified, False
    credited = semantics is not None and semantics[
        occurrences[EQUATION_INDEX]["occurrence_id"]
    ] == (True, pending.alt_text)
    return verified, credited


@pytest.mark.parametrize("case", sorted(OWNER_CASES))
def test_probe_is_never_less_strict_than_writer_verification_and_rescan(
    case: str, tmp_path: Path
) -> None:
    source = _owner_source(tmp_path, case)
    verified, rescanned = _written_verified_and_rescanned(
        source, tmp_path / "output.pdf"
    )
    assert (verified, rescanned) == (
        OWNER_CASES[case].verified,
        OWNER_CASES[case].rescanned,
    )
    assert (_probe(source) is False) == (verified and rescanned)


def test_probe_and_verification_read_marked_content_through_one_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.education.remediation import content_tagger_v2

    source = _owner_source(tmp_path, "paragraph_with_mcid")
    real = content_tagger_v2._marked_content_owner
    reads: list[str] = []

    def spy(op: Any) -> tuple[str, int]:
        reads.append(str(op.operator))
        return real(op)

    monkeypatch.setattr(content_tagger_v2, "_marked_content_owner", spy)
    assert _probe(source) is True
    assert reads == ["BDC"]

    reads.clear()
    assert _written_and_verified(source, tmp_path / "output.pdf") is False
    # The enclosing paragraph and the Formula the writer added.
    assert reads == ["BDC", "BDC"]


def test_probe_makes_no_claim_when_the_draw_cannot_be_resolved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    assert _probe(source, occurrence_id="imgocc-v1-" + "0" * 24) is None
    assert _probe(source, occurrence_ordinal=5) is None
    assert _probe(source, page_number=9) is None


# ---------------------------------------------------------------------------
# Identity matching and recognition flags
# ---------------------------------------------------------------------------


def _alt_issue(identity: tuple[Any, ...]) -> RemediationIssue:
    page, xref, index, ordinal, bbox, occurrence_id = identity
    return RemediationIssue(
        id=f"alt-{occurrence_id}",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Image missing alternative text",
        location=f"Page {page}, Image {index + 1}",
        metadata={
            "issue_type": "missing_alt_text",
            "rule": "WCAG 1.1.1",
            "has_alt_text": False,
            "page_number": page,
            "image_xref": xref,
            "image_index": index,
            "occurrence_ordinal": ordinal,
            "bbox": list(bbox),
            "occurrence_id": occurrence_id,
        },
    )


def _bare_remediator(tmp_path: Path) -> PdfRemediator:
    source = tmp_path / "bare.pdf"
    source.write_bytes(b"%PDF-probe")
    return PdfRemediator(str(source), [], RemediationConfig(create_backup=False))


IDENTITY = (1, 9, 0, 0, (72.0, 90.0, 192.0, 130.0), "imgocc-v1-" + "a" * 24)


def test_hold_matches_the_full_occurrence_identity_only(tmp_path: Path) -> None:
    remediator = _bare_remediator(tmp_path)
    equation = _alt_issue(IDENTITY)
    equation.category = IssueCategory.STRUCTURE
    equation.metadata["issue_type"] = IMAGE_EQUATION_ISSUE_TYPE
    equation.location = "Page 1, Image 1, Occurrence 1"
    assert occurrence_identity(equation.metadata) == IDENTITY
    remediator._hold_equation_occurrence(equation, "blocked")

    held = _alt_issue(IDENTITY)
    assert remediator._defer_alt_text_to_equation(held) is True
    assert [item.reason for item in remediator.result.manual_issues] == [
        ALT_TEXT_WITHHELD
    ]

    # Same page, image number and xref, but another displayed occurrence.
    other_id = "imgocc-v1-" + "b" * 24
    for variant in (
        (*IDENTITY[:5], other_id),
        (1, 9, 0, 1, IDENTITY[4], IDENTITY[5]),
        (1, 9, 0, 0, (72.0, 90.0, 192.0, 131.0), IDENTITY[5]),
        (1, 10, 0, 0, IDENTITY[4], IDENTITY[5]),
    ):
        assert remediator._defer_alt_text_to_equation(_alt_issue(variant)) is False
    assert len(remediator.result.manual_issues) == 1


def test_hold_ignores_findings_that_are_not_image_equation_candidates(
    tmp_path: Path,
) -> None:
    remediator = _bare_remediator(tmp_path)
    region = _alt_issue(IDENTITY)
    region.category = IssueCategory.STRUCTURE
    region.metadata["issue_type"] = "scanned_equation_region_manual"
    remediator._hold_equation_occurrence(region, "blocked")
    assert remediator._defer_alt_text_to_equation(_alt_issue(IDENTITY)) is False


class _Source:
    def __init__(self) -> None:
        self.payload = SimpleNamespace(
            identity=SimpleNamespace(
                page_number=1,
                image_xref=7,
                image_index=0,
                occurrence_ordinal=0,
                bbox=(1.0, 2.0, 3.0, 4.0),
                occurrence_id="occ-1",
            ),
            source_sha256="c" * 64,
            normalized_sha256="a" * 64,
            jpeg_bytes=b"\xff\xd8",
        )

    def extract(self, document: Any, metadata: Any) -> Any:
        return self.payload


class _Recognizer:
    def __init__(self, classification: str, latex: str | None = "x^2") -> None:
        self.classification = classification
        self.latex = latex

    def recognize(self, payload: Any) -> Any:
        return SimpleNamespace(
            classification=self.classification,
            latex=self.latex,
            provider="p",
            model="m",
        )


def _fixer(recognizer: _Recognizer, verifier: Any) -> MathFixer:
    class _Tree:
        pass

    return MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=_Tree(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        image_source=_Source(),
        equation_recognizer=recognizer,
        equation_verifier=verifier,
    )


_METADATA = {
    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
    "page_number": 1,
    "image_xref": 7,
    "image_index": 0,
    "occurrence_ordinal": 0,
    "bbox": (1.0, 2.0, 3.0, 4.0),
    "occurrence_id": "occ-1",
}


@pytest.mark.parametrize(
    ("classification", "passes", "expected_error", "recognised"),
    [
        ("printed_equation", False, "equation_verification_failed", True),
        ("printed_equation", True, "image_equation_association_pending", True),
        ("not_equation", True, "handwritten_math_suitability_rejected", False),
    ],
)
def test_result_records_whether_the_specialist_recognised_an_equation(
    classification: str, passes: bool, expected_error: str, recognised: bool
) -> None:
    mathml = latex_to_mathml("x^2")
    verifier = SimpleNamespace(
        verify=lambda image, latex: MathVerificationEvidence(
            passed=passes,
            source_sha256="a" * 64,
            rendered_sha256="b" * 64,
            mathml_sha256=hashlib.sha256(mathml.encode()).hexdigest(),
            renderer_version="renderer-v1",
            comparator_version="comparator-v1",
            font_sha256="f" * 64,
            threshold_version="threshold-v1",
            ink_iou=1.0,
            pixel_similarity=1.0,
            required_ink_iou=0.9,
            required_pixel_similarity=0.98,
        )
    )
    result = _fixer(_Recognizer(classification), verifier)._fix_math_issue(
        SimpleNamespace(metadata=_METADATA)
    )
    assert result.error == expected_error
    assert result.equation_recognized is recognised


def test_unreadable_image_is_not_counted_as_a_recognised_equation() -> None:
    class Failing:
        def recognize(self, payload: Any) -> Any:
            raise RuntimeError("provider down")

    result = _fixer(Failing(), SimpleNamespace())._fix_math_issue(
        SimpleNamespace(metadata=_METADATA)
    )
    assert result.error == "equation_recognition_rejected"
    assert result.equation_recognized is False


_REFUSABLE_METADATA = {**_METADATA, "occurrence_id": "imgocc-v1-" + "a" * 24}


@pytest.mark.parametrize(
    ("probe_result", "expected_error"),
    [
        (True, IMAGE_EQUATION_EXISTING_OWNER_ERROR),
        (None, IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR),
    ],
)
def test_refused_draw_reaches_neither_source_extraction_nor_recognition(
    monkeypatch: pytest.MonkeyPatch, probe_result: bool | None, expected_error: str
) -> None:
    from src.education.remediation import content_tagger_v2

    monkeypatch.setattr(
        content_tagger_v2,
        "image_draw_has_semantic_owner",
        lambda *args, **kwargs: probe_result,
    )
    calls: list[str] = []

    class Source(_Source):
        def extract(self, document: Any, metadata: Any) -> Any:
            calls.append("extract")
            return super().extract(document, metadata)

    class Recognizer(_Recognizer):
        def recognize(self, payload: Any) -> Any:
            calls.append("recognize")
            return super().recognize(payload)

    fixer = _fixer(Recognizer("printed_equation"), SimpleNamespace())
    fixer.image_source = Source()
    result = fixer._fix_math_issue(SimpleNamespace(metadata=_REFUSABLE_METADATA))

    assert result.error == expected_error
    assert result.pending_association is None
    # Nothing was classified, so the specialist did not recognise an equation.
    assert result.equation_recognized is False
    assert calls == []


def test_resolved_unowned_draw_goes_on_to_extraction_and_recognition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.education.remediation import content_tagger_v2

    monkeypatch.setattr(
        content_tagger_v2,
        "image_draw_has_semantic_owner",
        lambda *args, **kwargs: False,
    )
    calls: list[str] = []

    class Recognizer(_Recognizer):
        def recognize(self, payload: Any) -> Any:
            calls.append("recognize")
            return super().recognize(payload)

    fixer = _fixer(Recognizer("not_equation"), SimpleNamespace())
    result = fixer._fix_math_issue(SimpleNamespace(metadata=_REFUSABLE_METADATA))

    assert calls == ["recognize"]
    assert result.error not in {
        IMAGE_EQUATION_EXISTING_OWNER_ERROR,
        IMAGE_EQUATION_DRAW_UNRESOLVED_ERROR,
    }


def test_deferred_alt_text_reason_is_a_public_code(tmp_path: Path) -> None:
    remediator = _bare_remediator(tmp_path)
    equation = _alt_issue(IDENTITY)
    equation.category = IssueCategory.STRUCTURE
    equation.metadata["issue_type"] = IMAGE_EQUATION_ISSUE_TYPE
    remediator._hold_equation_occurrence(equation, "pending")

    assert remediator._defer_alt_text_to_equation(_alt_issue(IDENTITY)) is True
    (manual,) = remediator.result.manual_issues
    assert manual.reason == "image_equation_alt_text_deferred"
    assert manual_explanation(manual)["reason_code"] == (
        "image_equation_alt_text_deferred"
    )


def test_withheld_alt_text_reason_is_a_public_code(tmp_path: Path) -> None:
    remediator = _bare_remediator(tmp_path)
    equation = _alt_issue(IDENTITY)
    equation.category = IssueCategory.STRUCTURE
    equation.metadata["issue_type"] = IMAGE_EQUATION_ISSUE_TYPE
    remediator._hold_equation_occurrence(equation, "blocked")

    assert remediator._defer_alt_text_to_equation(_alt_issue(IDENTITY)) is True
    (manual,) = remediator.result.manual_issues
    assert manual.reason == ALT_TEXT_WITHHELD
    assert manual_explanation(manual)["reason_code"] == ALT_TEXT_WITHHELD
