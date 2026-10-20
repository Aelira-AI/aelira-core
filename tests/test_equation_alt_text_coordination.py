"""One image draw has one owner: the equation fix and its alt-text finding.

The scanner reports an equation image twice: as an equation candidate and as
"Image missing alternative text". The equation specialist runs first. While it
holds the draw, the generic alt-text pass must not call its provider and must
not create a Figure, because a Figure would be a second owner of the same draw.
When the equation is proven in the saved file, the held finding is promoted to
a fix that carries the equation's own description. A draw that already has a
semantic owner in a tagged source is refused before any provider call.

The full-flow tests run the real scanner, the real remediator and the real
association writer. Only the provider and the visual round-trip verifier are
replaced, because they would need a model and a browser.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from latex2mathml.converter import convert as latex_to_mathml
from PIL import Image
from pikepdf import Array, Dictionary

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_processor import PDFProcessor
from src.education.remediation import pdf_remediator
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
)
from src.education.remediation.content_tagger_v2 import image_draw_has_semantic_owner
from src.education.remediation.equation_recognizer import _PROMPT as RECOGNIZER_PROMPT
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.image_equation_gate import contains_image_equation_fixes
from src.education.remediation.math_fixer import (
    IMAGE_EQUATION_EXISTING_OWNER_ERROR,
    MathFixer,
    MathVerificationEvidence,
)
from src.education.remediation.pdf_image_finding_identity import occurrence_identity
from src.education.remediation.pdf_remediator import PdfRemediator

pytestmark = pytest.mark.unit

LATEX = "x^{2} + 1 = 0"
EQUATION_ALT = "x squared + 1 = 0"
GENERIC_ALT = "A generic description of an image"
GENERIC_PROMPT_START = "Generate concise, descriptive alt text"
EQUATION_INDEX = 0
OTHER_INDEX = 1
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

    def __init__(self, classification: str = "printed_equation") -> None:
        self.classification = classification
        self.calls: list[tuple[str, int]] = []
        self.generic_images: list[str] = []

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        if prompt == RECOGNIZER_PROMPT:
            self.calls.append(("recognition", len(image_data)))
            payload = (
                {"classification": "printed_equation", "latex": LATEX}
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
    """Stands in for the browser round trip: it passes or fails on demand."""

    def __init__(self, passes: bool) -> None:
        self.passes = passes

    def canonicalize_mathml(self, mathml: str) -> str:
        return canonicalize_mathml(mathml)

    def verify(self, image: Any, latex: str) -> MathVerificationEvidence:
        mathml = self.canonicalize_mathml(latex_to_mathml(latex))
        return MathVerificationEvidence(
            passed=self.passes,
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
    def install(*, passes: bool = True) -> None:
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
        alt_manual = _manual(result, ids["missing_alt_text"])
        assert alt_manual.reason == "equation_verification_failed"
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


# ---------------------------------------------------------------------------
# Tagged source: an existing owner is refused before any provider call
# ---------------------------------------------------------------------------


def test_figure_owned_equation_is_refused_before_any_provider_call(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier()
    source = tmp_path / "tagged.pdf"
    _write_tagged_figure_source(source)
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        assert provider.calls == []
        ids = _issue_ids(remediator, EQUATION_INDEX)
        assert (
            _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE]).reason
            == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        )
        # The same draw's generic alt-text finding is held, not described.
        assert (
            _manual(result, ids["missing_alt_text"]).reason
            == IMAGE_EQUATION_EXISTING_OWNER_ERROR
        )
        assert not contains_image_equation_fixes(result.fixed_issues)
        # The rest of the remediation went ahead.
        assert result.fixed_count > 0
        structure = _structure(result.output_file)
        assert [role for role, _ in structure if role == "/Formula"] == []
        assert [alt for role, alt in structure if role == "/Figure"] == [None]
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


@pytest.mark.parametrize(
    ("operator", "tag", "mcid", "expected"),
    [
        ("BDC", "P", 0, True),
        ("BMC", "Span", None, True),
        ("BMC", "Artifact", None, False),
    ],
)
def test_probe_judges_any_enclosing_sequence_by_its_tag(
    tmp_path: Path, operator: str, tag: str, mcid: int | None, expected: bool
) -> None:
    source = tmp_path / "source.pdf"
    _write_untagged_source(source, second_image=False)
    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        operands: list[Any] = [pikepdf.Name("/" + tag)]
        if mcid is not None:
            operands.append(Dictionary(MCID=mcid))
        wrapped = [
            pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(operator)),
            *pikepdf.parse_content_stream(page),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")),
        ]
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(wrapped))
        pdf.save(source)
    assert _probe(source) is expected


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
    remediator._hold_equation_occurrence(equation, "blocked", "some_reason")

    held = _alt_issue(IDENTITY)
    assert remediator._defer_alt_text_to_equation(held) is True
    assert [item.reason for item in remediator.result.manual_issues] == ["some_reason"]

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
    remediator._hold_equation_occurrence(region, "blocked", "some_reason")
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
