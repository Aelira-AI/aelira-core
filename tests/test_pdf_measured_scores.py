"""PDF score comparisons require complete paired scans and exact finding evidence."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator

FINDING = {
    "id": "lang",
    "type": "language",
    "severity": "high",
    "message": "Document language is missing",
    "location": "Document",
}


def setup_remediator(tmp_path, monkeypatch, before, after):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    saved = tmp_path / "saved.pdf"
    saved.write_bytes(b"%PDF-saved-test")
    remediator = PdfRemediator(
        str(source), [FINDING], config=RemediationConfig(use_ai=False)
    )
    remediator._add_fixed_issue(remediator.issues[0], "en", "rule_based")
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(saved)),
    )
    processor = Mock()
    processor.process_pdf.side_effect = [before, after]
    constructor = Mock(return_value=processor)
    monkeypatch.setattr("src.education.pdf_processor.PDFProcessor", constructor)
    monkeypatch.setattr(
        "src.education.validation.matterhorn.MatterhornValidator.validate",
        lambda *_: SimpleNamespace(
            checkpoints=[SimpleNamespace(id="09-004", status="pass", page_number=None)]
        ),
    )
    return remediator, constructor


def scan(score, findings):
    return SimpleNamespace(compliance_score=score, issues=findings)


def test_pdf_paired_scores_and_verified_disappearance(tmp_path, monkeypatch):
    remediator, constructor = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(100, [])
    )
    verification = remediator._verify_fixes("saved.pdf")
    remediator._calculate_scores()
    assert constructor.call_args.kwargs["require_complete_scan"] is True
    assert verification.passed is True
    assert remediator.result.fixed_count == 1
    assert remediator.result.original_compliance_score == 92
    assert remediator.result.remediated_compliance_score == 100
    assert remediator.result.improvement == 8


def test_pdf_failed_output_scan_is_not_a_verified_fix(tmp_path, monkeypatch):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), RuntimeError("checker failed")
    )
    remediator._verify_fixes("saved.pdf")
    remediator._calculate_scores()
    assert remediator.result.score_provenance is None
    assert remediator.result.remediated_compliance_score is None
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1


def test_pdf_unchanged_finding_has_no_fix_credit(tmp_path, monkeypatch):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(92, [FINDING])
    )
    remediator._verify_fixes("saved.pdf")
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.remediated_compliance_score == 92


def test_pdf_does_not_invent_attribution_for_unreproduced_source(tmp_path, monkeypatch):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(100, []), scan(100, [])
    )
    remediator._verify_fixes("saved.pdf")
    assert remediator.result.fixed_count == 0
    assert remediator.result.remediated_compliance_score is None


def test_pdf_actual_regression_remains_measured(tmp_path, monkeypatch):
    introduced = {**FINDING, "message": "New language finding", "severity": "critical"}
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(49, [introduced])
    )
    remediator._verify_fixes("saved.pdf")
    remediator._calculate_scores()
    assert remediator.result.verification_passed is False
    assert remediator.result.score_provenance == "scanner_rescan"
    assert remediator.result.remediated_compliance_score == 49
    assert remediator.result.improvement == pytest.approx(-43)


@pytest.mark.parametrize(
    "before_status,after_status,new_failure",
    [
        ("fail", "fail", False),
        ("pass", "fail", True),
        ("fail", "pass", False),
    ],
)
def test_matterhorn_uses_source_baseline(
    tmp_path, monkeypatch, before_status, after_status, new_failure
):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(100, [])
    )

    def result(status):
        return SimpleNamespace(
            checkpoints=[
                SimpleNamespace(
                    id="09-004",
                    name="Role mappings",
                    status=status,
                    page_number=None,
                    severity="error",
                    details="Same recorded role-mapping failure",
                )
            ]
        )

    validator = Mock(side_effect=[result(before_status), result(after_status)])
    monkeypatch.setattr(
        "src.education.validation.matterhorn.MatterhornValidator.validate", validator
    )
    verification = remediator._verify_fixes("saved.pdf")
    assert validator.call_count == 2
    assert bool(verification.regressions) is new_failure
    assert verification.passed is not new_failure
    assert bool(verification.persistent_failures) is (
        before_status == after_status == "fail"
    )


def test_matterhorn_unavailable_is_not_a_verification_pass(tmp_path, monkeypatch):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(100, [])
    )
    monkeypatch.setattr(
        "src.education.validation.matterhorn.MatterhornValidator.validate",
        lambda *_: None,
    )
    verification = remediator._verify_fixes("saved.pdf")
    assert verification.passed is False
    assert verification.unavailable_checks == ["matterhorn"]


def test_worsening_existing_matterhorn_failure_cannot_pass(tmp_path, monkeypatch):
    remediator, _ = setup_remediator(
        tmp_path, monkeypatch, scan(92, [FINDING]), scan(100, [])
    )

    def result(details):
        return SimpleNamespace(
            checkpoints=[
                SimpleNamespace(
                    id="13-004",
                    name="Alt text",
                    status="fail",
                    severity="error",
                    page_number=None,
                    details=details,
                )
            ]
        )

    validator = Mock(
        side_effect=[
            result("1 of 3 figures missing /Alt or /ActualText"),
            result("2 of 3 figures missing /Alt or /ActualText"),
        ]
    )
    monkeypatch.setattr(
        "src.education.validation.matterhorn.MatterhornValidator.validate", validator
    )
    verification = remediator._verify_fixes("saved.pdf")
    assert verification.passed is False
    assert verification.unavailable_checks == [
        "matterhorn:13-004:changed_failure_evidence"
    ]
    assert verification.persistent_failures == []
