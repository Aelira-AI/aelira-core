"""Review-gated content in a saved PDF must always be backed by its gated fix.

A verified image equation is written into the PDF as a Formula. The fix that
describes it carries the review-gated source kind, which is what routes the
artifact to a human before anyone can download it. If that fix is later dropped
(the saved-file rescan demotes it, the promotion is skipped, the rescan is
unavailable) the Formula is still in the file, and nothing marks the file as
needing review. The remediator must therefore fail the whole run, close the
output claim and quarantine the published file.

The full-flow tests use the real scanner, remediator and association writer.
Only the provider and the visual round-trip verifier are replaced.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pikepdf
import pytest

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_processor import PDFProcessor
from src.education.remediation import pdf_remediator
from src.education.remediation.base import RemediationConfig
from src.education.remediation.image_equation_gate import contains_image_equation_fixes
from src.education.remediation.math_fixer import MathFixer
from src.education.remediation.pdf_remediator import (
    PdfRemediator,
    ReviewGatedContentError,
)
from tests.test_equation_alt_text_coordination import (
    EQUATION_ALT,
    EQUATION_INDEX,
    _Provider,
    _Verifier,
    _issue_ids,
    _structure,
    _write_untagged_source,
)

pytestmark = pytest.mark.unit

DEMOTION_REASON = "The saved-file rescan did not verify this finding was resolved."


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    """Replace the browser round trip with a verifier that passes on demand."""

    def install(*, passes: bool = True) -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", _Verifier(passes))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


def _remediator(
    source: Path, tmp_path: Path, *, verify_fixes: bool = True
) -> PdfRemediator:
    scan = PDFProcessor().process_pdf(str(source))
    return PdfRemediator(
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
        alt_text_client=_Provider(),
    )


def _demote_after_verification(
    monkeypatch: pytest.MonkeyPatch, remediator: PdfRemediator, issue_ids: set[str]
) -> None:
    """Make the saved-file rescan drop the given fixes, as v0.9.13 did."""
    original = remediator._verify_fixes

    def demoting(output_path: str) -> Any:
        verification = original(output_path)
        originals = {issue.id: issue for issue in remediator.issues}
        result = remediator.result
        for fixed in [f for f in result.fixed_issues if f.issue_id in issue_ids]:
            remediator._add_manual_issue(
                originals[fixed.issue_id],
                reason=DEMOTION_REASON,
                recommendation="Review the saved PDF and rescan after correction.",
            )
        result.fixed_issues = [
            f for f in result.fixed_issues if f.issue_id not in issue_ids
        ]
        result.fixed_count = len(result.fixed_issues)
        return verification

    monkeypatch.setattr(remediator, "_verify_fixes", demoting)


def _quarantined(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "out").glob(".*.failed-output-*/output"))


def _assert_failed_closed(result: Any, remediator: PdfRemediator, tmp_path: Path):
    """The shared outcome: failed, named, claim closed, output quarantined."""
    assert result.success is False
    assert "review-gated" in (result.error_message or "").lower()
    assert result.has_output_claim() is False
    assert result.output_file is None
    assert not Path(remediator._get_output_path()).exists()
    held = _quarantined(tmp_path)
    assert len(held) == 1
    assert any(
        "review-gated visual content cleanup retained unverified output" in warning
        for warning in result.warnings
    )
    # What was set aside is the file that carried the unvouched Formula.
    assert [alt for role, alt in _structure(str(held[0])) if role == "/Formula"] == [
        EQUATION_ALT
    ]


def test_demoted_equation_fix_fails_the_run_and_quarantines_the_output(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path)
    ids = _issue_ids(remediator, EQUATION_INDEX)
    _demote_after_verification(
        monkeypatch,
        remediator,
        {ids[IMAGE_EQUATION_ISSUE_TYPE], ids["missing_alt_text"]},
    )

    result = remediator.remediate()

    _assert_failed_closed(result, remediator, tmp_path)
    assert not contains_image_equation_fixes(result.fixed_issues)


def test_equation_fix_alone_demoted_still_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promoted alt-text fix is not review-gated, so it cannot back the file."""
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path)
    ids = _issue_ids(remediator, EQUATION_INDEX)
    _demote_after_verification(
        monkeypatch, remediator, {ids[IMAGE_EQUATION_ISSUE_TYPE]}
    )

    result = remediator.remediate()

    _assert_failed_closed(result, remediator, tmp_path)


@pytest.mark.parametrize("verify_fixes", [True, False])
def test_skipped_reconcile_fails_closed(
    tmp_path: Path,
    install_verifier,
    monkeypatch: pytest.MonkeyPatch,
    verify_fixes: bool,
) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path, verify_fixes=verify_fixes)
    monkeypatch.setattr(remediator, "_reconcile_verified_image_equations", lambda: None)

    result = remediator.remediate()

    _assert_failed_closed(result, remediator, tmp_path)


def test_unavailable_rescan_fails_closed(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable rescan demotes every fix; the written Formula must not stay."""
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path)

    def unavailable() -> Any:
        raise RuntimeError("verification workspace unavailable")

    monkeypatch.setattr(
        remediator, "_materialize_output_claim_for_verification", unavailable
    )

    result = remediator.remediate()

    _assert_failed_closed(result, remediator, tmp_path)


def test_verify_fixes_false_behaves_as_before(tmp_path: Path, install_verifier) -> None:
    """Nothing is demoted, so the guard has nothing to object to and no receipt."""
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path, verify_fixes=False)

    result = remediator.remediate()
    try:
        assert result.success, result.error_message
        assert result.has_output_claim() is True
        assert Path(result.output_file).is_file()
        assert contains_image_equation_fixes(result.fixed_issues)
        gated = [f for f in result.fixed_issues if f.source_kind == "image_equation"]
        assert len(gated) == 1
        assert gated[0].saved_file_verification is None
        assert [a for r, a in _structure(result.output_file) if r == "/Formula"] == [
            EQUATION_ALT
        ]
        assert _quarantined(tmp_path) == []
    finally:
        result.close_output_claim()


def test_fully_verified_run_is_untouched(tmp_path: Path, install_verifier) -> None:
    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    remediator = _remediator(source, tmp_path)

    result = remediator.remediate()
    try:
        assert result.success, result.error_message
        assert result.verification_passed
        gated = [f for f in result.fixed_issues if f.source_kind == "image_equation"]
        assert len(gated) == 1
        assert gated[0].saved_file_verification["issue_id"] == gated[0].issue_id
        assert result.has_output_claim() is True
        assert Path(result.output_file).is_file()
        assert _quarantined(tmp_path) == []
        with pikepdf.open(result.output_file) as pdf:
            assert "/StructTreeRoot" in pdf.Root
    finally:
        result.close_output_claim()


# ---------------------------------------------------------------------------
# The guard on its own
# ---------------------------------------------------------------------------

RECEIPT = {"method_version": "pdf-finding-presence-v1", "issue_id": "eq"}


def _fix(
    issue_id: str = "eq",
    *,
    source_kind: str | None = "image_equation",
    verification_passed: bool = True,
    receipt: dict[str, Any] | None = RECEIPT,
) -> SimpleNamespace:
    return SimpleNamespace(
        issue_id=issue_id,
        source_kind=source_kind,
        verification_passed=verification_passed,
        saved_file_verification=receipt,
    )


def _guard(
    fixes: list[SimpleNamespace],
    *,
    written: bool = True,
    verify_fixes: bool = True,
) -> None:
    remediator = PdfRemediator.__new__(PdfRemediator)
    remediator.result = SimpleNamespace(fixed_issues=fixes)
    remediator.config = SimpleNamespace(verify_fixes=verify_fixes)
    remediator._verified_image_equations = (
        [(SimpleNamespace(id="eq"), None, None, None)] if written else []
    )
    remediator._require_review_gated_fixes_survive()


def test_guard_passes_when_nothing_gated_was_written() -> None:
    _guard([], written=False)
    _guard([_fix(source_kind=None)], written=False)


def test_guard_passes_for_a_verified_gated_fix_with_its_receipt() -> None:
    _guard([_fix()])


@pytest.mark.parametrize(
    "fixes",
    [
        pytest.param([], id="no-fix"),
        pytest.param([_fix(source_kind=None)], id="not-gated"),
        pytest.param([_fix(source_kind="unrelated_kind")], id="unknown-kind"),
        pytest.param([_fix(verification_passed=False)], id="verification-failed"),
        pytest.param([_fix(issue_id="other")], id="other-issue"),
        pytest.param([_fix(receipt=None)], id="no-receipt"),
        pytest.param([_fix(receipt={})], id="empty-receipt"),
        pytest.param(
            [_fix(receipt={**RECEIPT, "method_version": "other"})], id="wrong-method"
        ),
        pytest.param(
            [_fix(receipt={**RECEIPT, "issue_id": "other"})], id="wrong-receipt-issue"
        ),
    ],
)
def test_guard_raises_for_every_way_a_gated_fix_can_be_missing(
    fixes: list[SimpleNamespace],
) -> None:
    with pytest.raises(ReviewGatedContentError, match="review-gated"):
        _guard(fixes)


def test_guard_asks_for_no_receipt_when_saved_file_verification_is_off() -> None:
    _guard([_fix(receipt=None)], verify_fixes=False)
    with pytest.raises(ReviewGatedContentError):
        _guard([], verify_fixes=False)
    with pytest.raises(ReviewGatedContentError):
        _guard([_fix(verification_passed=False, receipt=None)], verify_fixes=False)


def test_failure_branch_does_not_quarantine_unrelated_output(
    tmp_path: Path, install_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no gated content written, the generic failure path is unchanged."""
    install_verifier(passes=False)
    source = tmp_path / "source.pdf"
    _write_untagged_source(source, second_image=False)
    remediator = _remediator(source, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        remediator,
        "_quarantine_failed_output",
        lambda *args, **kwargs: calls.append("quarantine"),
    )
    monkeypatch.setattr(
        pdf_remediator.PdfRemediator,
        "_calculate_scores",
        lambda self: (_ for _ in ()).throw(RuntimeError("late failure")),
    )

    result = remediator.remediate()

    assert result.success is False
    assert calls == []
