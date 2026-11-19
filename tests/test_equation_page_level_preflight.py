"""Refuse a whole page before paying for the equation reading.

The printed-equation pre-check refused a draw that other marked content
enclosed or that resolved to no single drawing operation. The saved-file
rescan's ownership walk refuses a page for more than that: a BX/EX
compatibility section anywhere in the stream, a marked-content property
whose /Properties resource is missing, a marked-content id used twice.
Such a page can never be credited, and until this pre-check the equation
was read, verified and associated first, the guard then set the whole
output aside: the reading was paid for and the document's other fixes
were lost.

The pre-flight now calls the rescan's own resolution
(``page_ownership_refusal`` shares the walk), so the two paths cannot
disagree, and a page the rescan cannot resolve becomes manual work with
an explained reason. The full-flow tests run the real scanner, the real
remediator and the real association writer; only the provider and the
browser round-trip verifier are replaced, because they would need a
model and a browser.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import (
    _resolve_image_ownership,
    page_ownership_refusal,
)
from src.education.remediation import pdf_remediator
from src.education.remediation.base import IssueCategory
from src.education.remediation.image_equation_gate import contains_image_equation_fixes
from src.education.remediation.math_fixer import (
    IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR,
    MathFixer,
)
from src.education.remediation.outcome_explanations import manual_explanation
from tests.test_equation_alt_text_coordination import (
    CUE_TEXT,
    EQUATION_INDEX,
    GENERIC_ALT,
    _Provider,
    _Verifier,
    _edit_page_content,
    _first_draw,
    _instruction,
    _issue_ids,
    _manual,
    _png,
    _run,
    _structure,
    _write_untagged_source,
)

pytestmark = pytest.mark.unit

# Each edit marks the page in a way that encloses no draw and still refuses
# the whole page for the saved-file ownership walk.
REFUSALS: dict[str, Any] = {
    "compatibility_section": lambda pdf, ops: [
        *ops[: _first_draw(ops)],
        _instruction("BX"),
        _instruction("EX"),
        *_ops_from_first_draw(pdf, ops),
    ],
    "missing_property_resource": lambda pdf, ops: [
        *ops[: _first_draw(ops)],
        _instruction("BDC", Name.Span, Name("/Absent")),
        _instruction("EMC"),
        *_ops_from_first_draw(pdf, ops),
    ],
    "reused_marked_content_id": lambda pdf, ops: [
        *ops[: _first_draw(ops)],
        _instruction("BDC", Name.Span, Dictionary(MCID=0)),
        _instruction("EMC"),
        _instruction("BDC", Name.Span, Dictionary(MCID=0)),
        _instruction("EMC"),
        *_ops_from_first_draw(pdf, ops),
    ],
}


def _ops_from_first_draw(pdf, ops):
    index = _first_draw(ops)
    return [*ops[index:]]


def _write_tagged_source_without_marked_draws(path: Path) -> None:
    """A tagged document whose image draws are enclosed by nothing."""
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((72, 80), CUE_TEXT, fontsize=4)
        page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png("white", (120, 40)))
        page.insert_image(fitz.Rect(72, 200, 132, 260), stream=_png("green", (60, 60)))
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        parent = pdf.make_indirect(Dictionary(S=Name.P, P=root, K=Array([])))
        root.K = Array([parent])
        root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([])])))
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        page.obj.StructParents = 0
        pdf.save(path)


@pytest.fixture
def install_verifier(monkeypatch):
    def install(*, passes: bool = True) -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", _Verifier(passes))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


@pytest.mark.parametrize("shape", sorted(REFUSALS))
def test_preflight_reports_the_rescans_own_page_level_cause(shape: str, tmp_path: Path):
    """One walk, not a copy of its rules.

    On a tagged page the pre-flight answers with exactly the cause the
    rescan's ownership resolution raises, and stays silent on exactly the
    pages that resolution resolves.
    """
    source = tmp_path / "s.pdf"
    _write_tagged_source_without_marked_draws(source)
    _edit_page_content(source, REFUSALS[shape])
    with pikepdf.open(source) as pdf, fitz.open(source) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
        with pytest.raises(ValueError) as refused:
            _resolve_image_ownership(pdf, str(source), 0, occurrences)
        assert page_ownership_refusal(pdf, str(source), 0, occurrences) == str(
            refused.value
        )
    clean = tmp_path / "clean.pdf"
    _write_tagged_source_without_marked_draws(clean)
    with pikepdf.open(clean) as pdf, fitz.open(clean) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
        assert page_ownership_refusal(pdf, str(clean), 0, occurrences) is None


@pytest.mark.parametrize("shape", sorted(REFUSALS))
def test_page_level_refusal_sends_the_equation_to_manual_work_unpaid(
    shape: str, tmp_path: Path, install_verifier
) -> None:
    """No equation reading on a page the rescan could never credit.

    The finding is manual with the reason that names the structural
    cause, and nothing about the refused equation is written: no hold on
    the alt-text finding, no Formula, no equation fix.
    """
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, REFUSALS[shape])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert provider.kinds.count("recognition") == 0
        assert provider.kinds.count("second_reading") == 0
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR
        explanation = manual_explanation(equation)
        assert (
            explanation["reason_code"] == IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR
        )
        assert "compatibility section" in explanation["reason"]
        assert "property resource is missing" in explanation["reason"]
        assert "marked-content id used more than once" in explanation["reason"]
        assert explanation["attempt"] == "not_attempted"
        assert remediator._equation_holds == {}
        assert not contains_image_equation_fixes(result.fixed_issues)
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("shape", ["compatibility_section", "reused_marked_content_id"])
def test_refused_equation_keeps_the_documents_other_fixes(
    shape: str, tmp_path: Path, install_verifier
) -> None:
    """The page-level refusal costs the document nothing else.

    The run completes and the output is published; the other image on
    the page keeps its description, written by the ordinary pass, and
    the refused equation leaves no Formula behind. Before this, the run
    failed whole and the published file was withheld.
    """
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, REFUSALS[shape])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert provider.kinds.count("recognition") == 0
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR
        assert result.output_file and Path(result.output_file).is_file()
        structure = _structure(result.output_file)
        assert [role for role, _ in structure].count("/Formula") == 0
        assert GENERIC_ALT in [
            alt for role, alt in structure if role == "/Figure" and alt
        ]
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("shape", ["compatibility_section", "reused_marked_content_id"])
def test_other_fixes_are_credited_when_the_rescan_is_off(
    shape: str, tmp_path: Path, install_verifier
) -> None:
    """Without the rescan the refused page's other findings are fixed as usual."""
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, REFUSALS[shape])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider, verify_fixes=False)
    try:
        assert provider.kinds.count("recognition") == 0
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR
        assert any(
            fixed.category == IssueCategory.ALT_TEXT
            and fixed.fixed_content == GENERIC_ALT
            for fixed in result.fixed_issues
        )
        assert result.fixed_count > 0
        assert not contains_image_equation_fixes(result.fixed_issues)
    finally:
        result.close_output_claim()


def test_missing_property_page_fails_only_by_its_own_guards(
    tmp_path: Path, install_verifier
) -> None:
    """A page whose property resource is missing dies by the text guard, unpaid.

    Any fix this document saves would already be refused by the
    source-glyph binding guard, which cannot decode marked content whose
    property name resolves to nothing. That refusal is the document's,
    not the equation's: what this test pins is that the equation path
    adds no provider spend, no association and no hold to it, and that
    the ordinary description pass still ran for both images.
    """
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    _edit_page_content(source, REFUSALS["missing_property_resource"])
    provider = _Provider()

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert provider.kinds.count("recognition") == 0
        assert provider.kinds.count("second_reading") == 0
        assert provider.kinds.count("generic") == 2
        ids = _issue_ids(remediator, EQUATION_INDEX)
        equation = _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        assert equation.reason == IMAGE_EQUATION_PAGE_OWNERSHIP_UNRESOLVED_ERROR
        assert remediator._equation_holds == {}
        assert result.error_message == "source_text_mapping_unavailable"
    finally:
        result.close_output_claim()
