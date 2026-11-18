"""A partially tagged source publishes with its unowned images listed.

A source with a structure tree can still draw an image outside every
structure element. Matterhorn 13-004 then passes on the source with "No
Figure elements found", because no Figure exists to check, while the image
has no alternative text to lose. Tagging that image turns the pass into a
count of undescribed figures. That is the source's own existing defect made
visible, the same pattern #714 accepted for an untagged source, and the
document's other verified fixes must not be withheld because of it.

The change is accepted only when the output's undescribed Figures correspond
exactly to source images proven to have no structure owner, each is recorded
as manual work in the same result, every image the output does describe is
backed by its own verified fix, and the validator's count agrees. A figure
that owns no source image, one whose source image already had a structure
owner, or a source that cannot be fully resolved keeps the fail-closed
result. The integration tests run the real scanner, remediator, saved-file
rescan and Matterhorn comparison; only the description provider and the
visual round-trip verifier are replaced, as in #714's tests.
"""

from __future__ import annotations

import asyncio
import io
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.image_semantics import (
    FigureFailureEvidence,
    images_without_structure_owners,
)
from src.education.remediation import pdf_remediator
from src.education.remediation.math_fixer import MathFixer
from src.education.remediation.pdf_image_finding_identity import (
    ImageFindingBinding,
    SavedImageCorrespondence,
    undescribed_figures_are_recorded_manual,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.validation.matterhorn import MatterhornValidator
from tests.test_equation_alt_text_coordination import (
    _issue_ids,
    _manual,
    _png,
    _run,
    _structure,
    _Verifier,
    _write_untagged_source,
)
from tests.test_printed_equation_end_to_end import _publish

pytestmark = pytest.mark.unit

NO_DESCRIPTION = "No usable image description was returned"
BODY = "Readable course material about rivers and their banks. " * 6


class _RefusingProvider:
    """An alt-text client that never describes anything."""

    purpose = "alt_text"
    provider = "test-provider"
    model = "test-model"

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        return {"success": False, "content": ""}


def _instruction(operator: str, *operands: Any) -> pikepdf.ContentStreamInstruction:
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def _write_partially_tagged_source(path: Path, *, owner: str = "unowned") -> None:
    """A tagged source whose P owns only the text; the image draw is bare.

    With ``owner="owned"`` the image instead sits in marked content a second
    Paragraph owns, so it has a structure owner even though no Figure does.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((72, 80), BODY, fontsize=4)
        page.insert_image(fitz.Rect(72, 90, 192, 130), stream=_png("green", (120, 40)))
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        text_start = next(i for i, op in enumerate(ops) if str(op.operator) == "BT")
        text_end = next(i for i, op in enumerate(ops) if str(op.operator) == "ET")
        draw = next(i for i, op in enumerate(ops) if str(op.operator) == "Do")
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        paragraph = pdf.make_indirect(Dictionary(S=Name.P, P=root, K=0))
        image_owner = None
        if owner == "unowned":
            image_ops = [ops[draw]]
            parent_tree = [paragraph]
        elif owner == "owned":
            image_owner = pdf.make_indirect(Dictionary(S=Name.P, P=root, K=1))
            image_ops = [
                _instruction("BDC", Name.P, Dictionary(MCID=1)),
                ops[draw],
                _instruction("EMC"),
            ]
            parent_tree = [paragraph, image_owner]
        else:  # pragma: no cover - fixture guard
            raise ValueError(owner)
        new_ops = [
            *ops[:text_start],
            _instruction("BDC", Name.P, Dictionary(MCID=0)),
            *ops[text_start : text_end + 1],
            _instruction("EMC"),
            *image_ops,
            *ops[draw + 1 :],
        ]
        root.K = (
            Array([paragraph])
            if image_owner is None
            else Array([paragraph, image_owner])
        )
        root.ParentTree = pdf.make_indirect(
            Dictionary(Nums=Array([0, Array(parent_tree)]))
        )
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        page.obj.StructParents = 0
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
        pdf.save(path)


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


def _figure_alts(path: str) -> list[str | None]:
    return [alt for role, alt in _structure(path) if role == "/Figure"]


def _checkpoint(path: str, checkpoint_id: str) -> Any:
    result = MatterhornValidator().validate(path)
    return next(item for item in result.checkpoints if item.id == checkpoint_id)


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    def install(*, passes: bool) -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", _Verifier(passes))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


# ---------------------------------------------------------------------------
# Integration: the real remediator verifies a partially tagged source
# ---------------------------------------------------------------------------


def test_partially_tagged_source_publishes_with_the_image_listed(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_partially_tagged_source(source)

    remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.success, result.error_message
        # 13-004 passed on the source because no Figure existed to check.
        assert _checkpoint(str(source), "13-004").details == (
            "No Figure elements found in structure tree"
        )
        assert _checkpoint(result.output_file, "13-004").details == (
            "1 of 1 figures missing /Alt or /ActualText"
        )
        # The failure is the source's own defect made visible, so the file
        # verifies and its other fixes are kept.
        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
        assert result.verification_result.regressions == []
        assert ("13-004", None) in result.source_matterhorn_failures
        image_ids = _issue_ids(remediator, 0)
        assert _manual(result, image_ids["missing_alt_text"]).reason == NO_DESCRIPTION
        assert _figure_alts(result.output_file) == [None]
        assert {"language", "title", "structure"} <= {
            fix.category.value for fix in result.fixed_issues
        }
        from types import SimpleNamespace

        scan = SimpleNamespace(issues=[{"id": issue.id} for issue in remediator.issues])
        run = asyncio.run(_publish(tmp_path, source, scan, remediator, result))
    finally:
        result.close_output_claim()

    # The job publishes the file, lists the undescribed image as manual work
    # and holds nothing for approval.
    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is False
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    assert outcomes[image_ids["missing_alt_text"]]["status"] == "manual"
    assert not [item for item in outcomes.values() if item["status"] == "withheld"]


@pytest.mark.parametrize("alt", [None, "A stray description"])
def test_extra_output_figure_that_owns_no_source_image_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch, alt
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_partially_tagged_source(source)
    materialize = PdfRemediator._materialize_output_claim_for_verification

    @contextmanager
    def with_stray_figure(self: Any):
        with materialize(self) as path:
            _add_stray_figure(path, alt)
            yield path

    monkeypatch.setattr(
        PdfRemediator, "_materialize_output_claim_for_verification", with_stray_figure
    )

    _remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.verification_passed is False
        assert "Matterhorn 13-004" in result.verification_result.regressions
    finally:
        result.close_output_claim()


def test_unowned_image_without_a_manual_finding_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_partially_tagged_source(source)
    verify = PdfRemediator._verify_fixes

    def verify_without_the_missing_alt_record(self: Any, output_path: str):
        # The Figure stays undescribed, but its missing-alt finding is no
        # longer recorded, so nothing accounts for the failure.
        withheld = {
            item.issue_id
            for item in self.result.manual_issues
            if item.reason == NO_DESCRIPTION
        }
        assert withheld
        self.result.manual_issues = [
            item for item in self.result.manual_issues if item.issue_id not in withheld
        ]
        return verify(self, output_path)

    monkeypatch.setattr(
        PdfRemediator, "_verify_fixes", verify_without_the_missing_alt_record
    )

    remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        image_ids = _issue_ids(remediator, 0)
        assert not any(
            item.issue_id == image_ids["missing_alt_text"]
            for item in result.manual_issues
        )
        assert result.verification_passed is False
        assert "Matterhorn 13-004" in result.verification_result.regressions
    finally:
        result.close_output_claim()


@pytest.mark.parametrize(
    "proof",
    [frozenset(), None],
    ids=["every-source-image-owned", "source-cannot-be-resolved"],
)
def test_figure_that_matches_no_unowned_source_image_still_fails_closed(
    tmp_path: Path,
    install_verifier,
    monkeypatch: pytest.MonkeyPatch,
    proof: frozenset[tuple[int, int]] | None,
) -> None:
    """Every other condition holds, but the source proof does not cover it.

    Only an image proven to have no structure owner may take the new proof.
    A source whose images are all owned, or that cannot be fully resolved,
    keeps the fail-closed result exactly as before.
    """
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_partially_tagged_source(source)
    monkeypatch.setattr(
        "src.education.pdf_checks.image_semantics.images_without_structure_owners",
        lambda path: proof,
    )

    _remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.verification_passed is False
        assert "Matterhorn 13-004" in result.verification_result.regressions
    finally:
        result.close_output_claim()


def test_image_with_a_structure_owner_is_untouched_by_the_new_proof(
    tmp_path: Path, install_verifier
) -> None:
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_partially_tagged_source(source, owner="owned")

    _remediator, result = _run(source, tmp_path, _RefusingProvider())
    try:
        assert result.success, result.error_message
        # An owned image never becomes an undescribed Figure, so the pass on
        # the source is also a pass on the output.
        assert _checkpoint(result.output_file, "13-004").status.value == "pass"
        assert result.verification_passed, result.verification_result
        assert ("13-004", None) not in (result.source_matterhorn_failures or [])
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# Unit: the source-side proof, one file at a time
# ---------------------------------------------------------------------------


def test_images_without_structure_owners_proves_the_unowned_draw(
    tmp_path: Path,
) -> None:
    unowned = tmp_path / "unowned.pdf"
    owned = tmp_path / "owned.pdf"
    untagged = tmp_path / "untagged.pdf"
    broken = tmp_path / "broken.pdf"
    _write_partially_tagged_source(unowned)
    _write_partially_tagged_source(owned, owner="owned")
    _write_untagged_source(untagged)
    broken.write_bytes(b"%PDF-1.7\nnot a document")

    assert images_without_structure_owners(str(unowned)) == frozenset({(1, 0)})
    assert images_without_structure_owners(str(owned)) == frozenset()
    # An untagged file is is_untagged_pdf's answer, never this one.
    assert images_without_structure_owners(str(untagged)) is None
    assert images_without_structure_owners(str(broken)) is None
    assert images_without_structure_owners(str(tmp_path / "missing.pdf")) is None


# ---------------------------------------------------------------------------
# Unit: the acceptance conditions of the extended matching, one at a time
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


def _binding(index: int) -> ImageFindingBinding:
    return ImageFindingBinding(
        key=(
            "alt_text",
            f"Page 1, Image {index + 1}",
            "Image missing alternative text",
        ),
        page_number=1,
        image_xref=8 + index,
        image_index=index,
        occurrence_ordinal=0,
        bbox=_bbox(index),
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


UNOWNED_CASES = [
    pytest.param(
        _figures((0,)),
        _saved(0),
        _details(1, 1),
        [_binding(0)],
        [],
        frozenset({(1, 0)}),
        True,
        id="unowned-image-recorded-manual",
    ),
    pytest.param(
        _figures((0, 1)),
        _saved(0, 1),
        _details(2, 2),
        [_binding(0), _binding(1)],
        [],
        frozenset({(1, 0), (1, 1)}),
        True,
        id="two-unowned-images",
    ),
    pytest.param(
        _figures((0,), (1,)),
        _saved(0, 1, accessible=(1,)),
        _details(1, 2),
        [_binding(0)],
        [_binding(1)],
        frozenset({(1, 0)}),
        True,
        id="unowned-undescribed-own-described",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0),
        _details(1, 1),
        [_binding(0)],
        [],
        frozenset(),
        False,
        id="every-source-image-owned",
    ),
    pytest.param(
        _figures((0, 1)),
        _saved(0, 1),
        _details(2, 2),
        [_binding(0), _binding(1)],
        [],
        frozenset({(1, 1)}),
        False,
        id="one-figure-belongs-to-an-owned-image",
    ),
    pytest.param(
        _figures((0, 1)),
        _saved(0, 1),
        _details(2, 2),
        [_binding(0), _binding(1)],
        [],
        frozenset({(1, 0)}),
        False,
        id="second-figure-belongs-to-an-owned-image",
    ),
    pytest.param(
        _figures((0,)),
        _saved(0),
        _details(1, 1),
        [_binding(0)],
        [],
        frozenset({(1, 0), (1, 5)}),
        True,
        id="more-unowned-than-figures-is-fine",
    ),
]


@pytest.mark.parametrize(
    ("figures", "saved", "details", "manual", "verified", "source_unowned", "expected"),
    UNOWNED_CASES,
)
def test_source_ownership_narrows_the_acceptance(
    figures, saved, details, manual, verified, source_unowned, expected
) -> None:
    assert (
        undescribed_figures_are_recorded_manual(
            figures, saved, manual, verified, details, source_unowned=source_unowned
        )
        is expected
    )
