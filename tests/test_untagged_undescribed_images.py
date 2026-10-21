"""An untagged source is published when its undescribed images are recorded.

Matterhorn 13-004 cannot check figures in a file without a structure tree. The
remediated output is tagged, so an image that stays honestly undescribed (an
equation whose reading was rejected, or an image with no usable description)
makes 13-004 fail with a count. That image was already undescribed in the
source, so remediation introduced no defect, and the verified language, title
and structure fixes must not be withheld because of it.

The change of evidence is accepted only when the output's Figures account for
exactly the source's image occurrences, every Figure still missing /Alt is a
missing-alt finding already recorded as manual work, and every image the output
does describe is backed by its own verified fix. Anything else keeps the
fail-closed result. The integration tests run the real scanner, remediator,
saved-file rescan and Matterhorn comparison. Only the provider and the visual
round-trip verifier are replaced, as in the equation coordination tests.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.image_semantics import (
    FigureFailureEvidence,
    is_untagged_pdf,
)
from src.education.remediation import pdf_remediator
from src.education.remediation.content_tagger_v2 import ContentTaggerV2
from src.education.remediation.image_equation_gate import (
    contains_image_equation_fixes,
    require_image_equation_matterhorn_result,
)
from src.education.remediation.math_fixer import MathFixer
from src.education.remediation.pdf_image_finding_identity import (
    ImageFindingBinding,
    SavedImageCorrespondence,
    undescribed_figures_are_recorded_manual,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.validation.matterhorn import MatterhornValidator
from tests.test_printed_equation_end_to_end import _publish
from tests.test_equation_alt_text_coordination import (
    ALT_TEXT_WITHHELD,
    CUE_TEXT,
    GENERIC_ALT,
    GENERIC_PROMPT_START,
    _fixed,
    _issue_ids,
    _manual,
    _png,
    _Provider,
    _rescan_missing_alt,
    _run,
    _structure,
    _Verifier,
    _write_tagged_figure_source,
    _write_untagged_source,
)

pytestmark = pytest.mark.unit

CHANGED_EVIDENCE = "matterhorn:13-004:changed_failure_evidence"


class _Layout(NamedTuple):
    """Image index of the equation and of the ordinary image on the page."""

    equation: int
    ordinary: int


# The fixture of the equation coordination tests draws the equation first. The
# fixture below draws the ordinary image first.
EQUATION_FIRST = _Layout(equation=0, ordinary=1)
ORDINARY_FIRST = _Layout(equation=1, ordinary=0)


class _RefusingProvider(_Provider):
    """Reads the equation, and cannot describe any other image."""

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **kwargs: Any):
        if prompt.startswith(GENERIC_PROMPT_START):
            self.calls.append(("generic", len(image_data)))
            return {"success": False, "content": ""}
        return super().analyze_image_sync(
            image_data=image_data, prompt=prompt, **kwargs
        )


def _write_ordinary_first_source(path: Path) -> None:
    """An ordinary image, then a cued equation image far below it."""
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=400)
        page.insert_image(fitz.Rect(72, 20, 132, 70), stream=_png("green", (60, 60)))
        page.insert_text((72, 200), CUE_TEXT, fontsize=4)
        page.insert_image(fitz.Rect(72, 210, 192, 250), stream=_png("white", (120, 40)))
        doc.save(path)


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    def install(*, passes: bool) -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", _Verifier(passes))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


def _figure_alts(path: str) -> list[str | None]:
    return [alt for role, alt in _structure(path) if role == "/Figure"]


def _checkpoint(path: str, checkpoint_id: str) -> Any:
    result = MatterhornValidator().validate(path)
    return next(item for item in result.checkpoints if item.id == checkpoint_id)


# ---------------------------------------------------------------------------
# Integration: the real remediator verifies an untagged source
# ---------------------------------------------------------------------------


def test_rejected_equation_and_described_image_publish_the_verified_fixes(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_ordinary_first_source(source)
    layout = ORDINARY_FIRST

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert result.success, result.error_message
        # 13-004 went from "cannot check" to "1 of 2 figures missing /Alt".
        assert _checkpoint(str(source), "13-004").details == (
            "Cannot check figures — no StructTreeRoot"
        )
        assert _checkpoint(result.output_file, "13-004").details == (
            "1 of 2 figures missing /Alt or /ActualText"
        )
        # Saved-file verification accepts that change, so nothing is withheld.
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
        assert "Matterhorn 13-004" in result.verification_result.persistent_failures

        equation_ids = _issue_ids(remediator, layout.equation)
        ordinary_ids = _issue_ids(remediator, layout.ordinary)
        assert {"language", "title", "structure"} <= {
            fix.category.value for fix in result.fixed_issues
        }
        # The ordinary image is described, on its own Figure. The equation image
        # is not, and both of its findings are manual work.
        assert (
            _fixed(result, ordinary_ids["missing_alt_text"]).fixed_content
            == GENERIC_ALT
        )
        assert _manual(result, equation_ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "equation_verification_failed"
        )
        assert _manual(result, equation_ids["missing_alt_text"]).reason == (
            ALT_TEXT_WITHHELD
        )
        assert _figure_alts(result.output_file) == [GENERIC_ALT, None]
        assert _rescan_missing_alt(result.output_file, layout.ordinary) == []
        assert len(_rescan_missing_alt(result.output_file, layout.equation)) == 1
        # Nothing about the equation is gated.
        assert contains_image_equation_fixes(result.fixed_issues) is False
    finally:
        result.close_output_claim()


def test_every_undescribed_image_recorded_manual_is_accepted(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    layout = EQUATION_FIRST

    remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.success, result.error_message
        assert _checkpoint(result.output_file, "13-004").details == (
            "2 of 2 figures missing /Alt or /ActualText"
        )
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
        # The image the provider could not describe is manual work too.
        assert _manual(
            result, _issue_ids(remediator, layout.ordinary)["missing_alt_text"]
        )
        assert _manual(
            result, _issue_ids(remediator, layout.equation)["missing_alt_text"]
        )
        assert _figure_alts(result.output_file) == [None, None]
    finally:
        result.close_output_claim()


def test_verified_equation_and_undescribed_image_keep_the_equation_gate(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=True)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)

    remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.success, result.error_message
        # The equation image is a Formula, not a Figure. Only the image the
        # provider could not describe is a Figure, and it lacks /Alt.
        assert _checkpoint(result.output_file, "13-004").details == (
            "1 of 1 figures missing /Alt or /ActualText"
        )
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
        assert contains_image_equation_fixes(result.fixed_issues) is True
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == [
            "x squared + 1 = 0"
        ]
        assert _figure_alts(result.output_file) == [None]
        # The equation gate still accepts the real validator result: the
        # 13-004 failure is part of the recorded source baseline.
        assert ("13-004", None) in result.source_matterhorn_failures
        require_image_equation_matterhorn_result(
            MatterhornValidator().validate(result.output_file),
            result.source_matterhorn_failures,
        )
    finally:
        result.close_output_claim()


def test_undescribed_figure_without_a_manual_finding_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_ordinary_first_source(source)
    verify = PdfRemediator._verify_fixes

    def verify_without_the_missing_alt_record(self: Any, output_path: str):
        # The equation candidate stays manual, but the missing-alt finding of
        # the same draw is no longer recorded, so its Figure has no owner.
        withheld = {
            item.issue_id
            for item in self.result.manual_issues
            if item.reason == ALT_TEXT_WITHHELD
        }
        assert withheld
        self.result.manual_issues = [
            item for item in self.result.manual_issues if item.issue_id not in withheld
        ]
        return verify(self, output_path)

    monkeypatch.setattr(
        PdfRemediator, "_verify_fixes", verify_without_the_missing_alt_record
    )

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        ids = _issue_ids(remediator, ORDINARY_FIRST.equation)
        assert any(
            item.issue_id == ids[IMAGE_EQUATION_ISSUE_TYPE]
            for item in result.manual_issues
        )
        assert not any(
            item.issue_id == ids["missing_alt_text"] for item in result.manual_issues
        )
        assert result.verification_passed is False
        assert CHANGED_EVIDENCE in result.verification_result.unavailable_checks
    finally:
        result.close_output_claim()


def test_description_of_the_second_image_lands_on_the_second_figure(
    tmp_path: Path, install_verifier
) -> None:
    """An undescribed image before a described one no longer misplaces the text.

    The equation is drawn first and its reading is rejected, so it stays
    undescribed. The generated Figure for the second image records that image's
    box, so the tagger binds the description to the second image's draw and the
    first image keeps an alt-less Figure that is listed as manual work. The file
    verifies and publishes with its other fixes.
    """
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    layout = EQUATION_FIRST

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert result.success, result.error_message
        assert _figure_alts(result.output_file) == [None, GENERIC_ALT]
        # By image, not by position in the tree: the second image is the one
        # that is described, and the equation image is the one still missing.
        assert _rescan_missing_alt(result.output_file, layout.ordinary) == []
        assert len(_rescan_missing_alt(result.output_file, layout.equation)) == 1
        assert _checkpoint(result.output_file, "13-004").details == (
            "1 of 2 figures missing /Alt or /ActualText"
        )
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []

        equation_ids = _issue_ids(remediator, layout.equation)
        ordinary_ids = _issue_ids(remediator, layout.ordinary)
        assert (
            _fixed(result, ordinary_ids["missing_alt_text"]).fixed_content
            == GENERIC_ALT
        )
        assert _manual(result, equation_ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "equation_verification_failed"
        )
        assert _manual(result, equation_ids["missing_alt_text"]).reason == (
            ALT_TEXT_WITHHELD
        )
        assert {"language", "title", "structure", "alt_text"} <= {
            fix.category.value for fix in result.fixed_issues
        }
        scan = SimpleNamespace(issues=[{"id": issue.id} for issue in remediator.issues])
        run = asyncio.run(_publish(tmp_path, source, scan, remediator, result))
    finally:
        result.close_output_claim()

    # The job publishes the file, lists the equation image as manual work and
    # holds nothing for approval.
    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is False
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    assert outcomes[equation_ids["missing_alt_text"]]["status"] == "manual"
    assert outcomes[ordinary_ids["missing_alt_text"]]["status"] == "fixed"
    assert not [item for item in outcomes.values() if item["status"] == "withheld"]


def test_misplaced_description_is_still_refused_if_the_tagger_binds_by_order(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proof does not trust the tagger: a misplaced description is withheld.

    The tagger once bound each generated Figure to the first image in order.
    With that binding restored, the description of the second image lands on the
    first image's Figure. Every finding is recorded and the counts agree, but the
    described image has no verified fix of its own, so the proof refuses it.
    """
    install_verifier(passes=False)

    def bind_by_order(self: Any, elements: Any, used: set, block_rect: Any):
        for index, element in enumerate(elements):
            if index not in used and str(element.get("/S")) == "/Figure":
                used.add(index)
                return (element, "ordinal", 0.50)
        return None

    monkeypatch.setattr(ContentTaggerV2, "_match_figure_to_image", bind_by_order)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    layout = EQUATION_FIRST

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert _figure_alts(result.output_file) == [GENERIC_ALT, None]
        assert _rescan_missing_alt(result.output_file, layout.ordinary) != []
        assert _rescan_missing_alt(result.output_file, layout.equation) == []
        assert result.verification_passed is False
        assert CHANGED_EVIDENCE in result.verification_result.unavailable_checks
    finally:
        result.close_output_claim()


def _add_stray_figure(path: str, alt: str | None) -> None:
    """Give the saved output one more Figure than the source has images."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        document = pdf.Root.StructTreeRoot.K
        document = document[0] if isinstance(document, Array) else document
        stray = pdf.make_indirect(Dictionary(S=Name.Figure, P=document))
        if alt is not None:
            stray.Alt = pikepdf.String(alt)
        kids = document.get("/K")
        document.K = Array(
            [*kids, stray]
            if isinstance(kids, Array)
            else [stray] if kids is None else [kids, stray]
        )
        pdf.save(path)


@pytest.mark.parametrize("alt", ["A stray description", None])
def test_extra_output_figure_that_owns_no_source_image_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch, alt
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    materialize = PdfRemediator._materialize_output_claim_for_verification

    @contextmanager
    def with_stray_figure(self: Any):
        with materialize(self) as path:
            _add_stray_figure(path, alt)
            yield path

    monkeypatch.setattr(
        PdfRemediator, "_materialize_output_claim_for_verification", with_stray_figure
    )

    remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.verification_passed is False
        assert CHANGED_EVIDENCE in result.verification_result.unavailable_checks
    finally:
        result.close_output_claim()


def test_source_that_is_not_provably_untagged_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other condition holds, but the source may merely be unresolvable.

    ``missing_figure_occurrences`` is None for an untagged source and for a
    tagged one it cannot read. Only the first may take the new proof.
    """
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_ordinary_first_source(source)
    monkeypatch.setattr(
        "src.education.pdf_checks.image_semantics.is_untagged_pdf", lambda path: False
    )

    remediator, result = _run(source, tmp_path, _Provider())
    try:
        assert result.verification_passed is False
        assert CHANGED_EVIDENCE in result.verification_result.unavailable_checks
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# Unit: the acceptance conditions, one at a time
# ---------------------------------------------------------------------------


def _bbox(index: int) -> tuple[float, ...]:
    return (72.0, 90.0 + 100.0 * index, 192.0, 130.0 + 100.0 * index)


def _occurrence(index: int) -> dict[str, Any]:
    return {
        "page_number": 1,
        "image_xref": 20 + index,
        "image_index": index,
        "occurrence_ordinal": 0,
        "bbox": _bbox(index),
        "occurrence_id": "imgocc-v1-" + f"{index:024x}",
    }


def _binding(index: int, *, ordinal: int = 0, bbox: Any = None) -> ImageFindingBinding:
    return ImageFindingBinding(
        key=(
            "alt_text",
            f"Page 1, Image {index + 1}",
            "Image missing alternative text",
        ),
        page_number=1,
        image_xref=8 + index,
        image_index=index,
        occurrence_ordinal=ordinal,
        bbox=bbox or _bbox(index),
        occurrence_id="imgocc-v1-" + f"{index + 100:024x}",
    )


def _saved(*indexes: int, accessible: tuple[int, ...] = ()) -> SavedImageCorrespondence:
    return SavedImageCorrespondence(
        {(1, index): _occurrence(index) for index in indexes},
        frozenset((1, index) for index in accessible),
    )


def _figures(
    missing: tuple[int, ...], described: tuple[int, ...] = (), count: int | None = None
) -> FigureFailureEvidence:
    return FigureFailureEvidence(
        frozenset((1, index) for index in missing),
        frozenset((1, index) for index in described),
        len(missing) + len(described) if count is None else count,
    )


def _details(missing: int, figures: int) -> str:
    return f"{missing} of {figures} figures missing /Alt or /ActualText"


ONE = dict(figures=_figures((0,)), saved=_saved(0), details=_details(1, 1))

ACCEPTED = [
    pytest.param(*ONE.values(), [_binding(0)], [], id="one-image"),
    pytest.param(
        _figures((0, 1)),
        _saved(0, 1),
        _details(2, 2),
        [_binding(0), _binding(1)],
        [],
        id="two-images",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 2),
        [_binding(0)],
        [_binding(1)],
        id="one-undescribed-one-described",
    ),
    pytest.param(
        _figures((1,)),
        _saved(0, 1, accessible=(0,)),
        _details(1, 1),
        [_binding(1)],
        [_binding(0)],
        id="one-undescribed-one-formula",
    ),
]


@pytest.mark.parametrize(
    ("figures", "saved", "details", "manual", "verified"), ACCEPTED
)
def test_exact_accounting_of_recorded_images_is_accepted(
    figures, saved, details, manual, verified
) -> None:
    assert (
        undescribed_figures_are_recorded_manual(
            figures, saved, manual, verified, details
        )
        is True
    )


REFUSED = [
    pytest.param(*ONE.values(), [], [], id="no-manual-finding"),
    pytest.param(
        _figures((0,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 1),
        [_binding(1)],
        [],
        id="manual-finding-of-another-image",
    ),
    pytest.param(
        *ONE.values(), [_binding(0, bbox=_bbox(5))], [], id="same-index-other-box"
    ),
    pytest.param(
        *ONE.values(), [_binding(0, ordinal=1)], [], id="same-index-other-ordinal"
    ),
    pytest.param(
        *ONE.values(), [_binding(0), _binding(0)], [], id="two-findings-one-figure"
    ),
    pytest.param(
        _figures((0, 1)),
        _saved(0, 1),
        _details(2, 2),
        [_binding(0)],
        [],
        id="one-of-two-undescribed-is-unmatched",
    ),
    pytest.param(
        *ONE.values(), [_binding(0)], [_binding(0)], id="verified-fix-on-undescribed"
    ),
    pytest.param(
        _figures(()), _saved(0), _details(0, 0), [_binding(0)], [], id="nothing-missing"
    ),
    pytest.param(
        _figures((0,), count=2),
        _saved(0),
        _details(1, 2),
        [_binding(0)],
        [],
        id="figure-count-exceeds-images",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0, 1),
        _details(1, 1),
        [_binding(0)],
        [],
        id="image-with-no-figure-and-no-credit",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0),
        _details(1, 2),
        [_binding(0)],
        [],
        id="described-figure-outside-the-images",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(0, 1)),
        _details(1, 2),
        [_binding(0)],
        [_binding(1)],
        id="undescribed-figure-also-credited",
    ),
    pytest.param(
        _figures((0,), (0,), count=2),
        _saved(0),
        _details(1, 2),
        [_binding(0)],
        [],
        id="same-image-undescribed-and-described",
    ),
    pytest.param(
        _figures((1,), (0,)),
        _saved(0, 1, accessible=(0,)),
        _details(1, 2),
        [_binding(1)],
        [],
        id="described-image-has-no-verified-fix",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 2),
        [_binding(0)],
        [_binding(1, bbox=_bbox(5))],
        id="verified-fix-of-another-draw",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 2),
        [_binding(0)],
        [_binding(1), _binding(1)],
        id="two-verified-fixes-one-image",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 2),
        [_binding(0), _binding(1)],
        [_binding(1)],
        id="described-image-also-recorded-manual",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0),
        _details(2, 1),
        [_binding(0)],
        [],
        id="validator-count-disagrees",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0),
        _details(1, 3),
        [_binding(0)],
        [],
        id="validator-figure-total-disagrees",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0),
        "Cannot check figures — no StructTreeRoot",
        [_binding(0)],
        [],
        id="validator-detail-unreadable",
    ),
    pytest.param(
        _figures((0,)), _saved(0), None, [_binding(0)], [], id="validator-detail-absent"
    ),
]


@pytest.mark.parametrize(("figures", "saved", "details", "manual", "verified"), REFUSED)
def test_any_unaccounted_figure_is_refused(
    figures, saved, details, manual, verified
) -> None:
    assert (
        undescribed_figures_are_recorded_manual(
            figures, saved, manual, verified, details
        )
        is False
    )


def test_is_untagged_pdf_separates_untagged_from_unreadable_and_tagged(
    tmp_path: Path,
) -> None:
    untagged, tagged = tmp_path / "untagged.pdf", tmp_path / "tagged.pdf"
    _write_untagged_source(untagged)
    _write_tagged_figure_source(tagged)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot a document")

    assert is_untagged_pdf(str(untagged)) is True
    assert is_untagged_pdf(str(tagged)) is False
    assert is_untagged_pdf(str(broken)) is False
    assert is_untagged_pdf(str(tmp_path / "missing.pdf")) is False
