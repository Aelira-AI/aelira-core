"""Bounded remediation failure messages, atomic with the existing terminal state."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

with patch.dict(
    os.environ,
    {"DATABASE_URL": "postgresql://unused:unused@127.0.0.1:1/aelira_contract_test"},
):
    from src.jobs import remediation_job
    from src.db.models import RemediationOutcome, ScanStatus

pytestmark = pytest.mark.unit


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


def scan():
    return SimpleNamespace(
        id="scan-1",
        status=ScanStatus.COMPLETED,
        remediation_outcome=None,
        completed_at=None,
        progress=100,
        progress_message="Processing complete",
        error_message=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("source_text_mapping_unavailable", "PDF text encoding could not be verified"),
        (
            "source_text_scope_unsupported",
            "text stream this remediation path cannot verify",
        ),
        ("private path and document contents", "Check the recorded outcome"),
    ],
)
async def test_manual_refusal_has_specific_safe_message(monkeypatch, reason, expected):
    monkeypatch.setattr(
        remediation_job, "_fence_claim_for_handler_commit", lambda *_: None
    )
    current = scan()
    db = MagicMock()
    with pytest.raises(remediation_job.RemediationJobFailed):
        await remediation_job._commit_terminal_failure(
            SimpleNamespace(),
            db,
            "manual_required",
            scan=current,
            result={"score_verification_reason": reason, "total_issues": 151},
        )
    assert current.status == ScanStatus.FAILED
    assert current.remediation_outcome == RemediationOutcome.MANUAL_REQUIRED.value
    assert current.progress_message == "Remediation needs manual review"
    assert expected in current.error_message
    assert "private path" not in current.error_message
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_failed_commit_restores_presentation_and_domain_state(monkeypatch):
    monkeypatch.setattr(
        remediation_job, "_fence_claim_for_handler_commit", lambda *_: None
    )
    current = scan()
    before = vars(current).copy()
    db = MagicMock()
    db.commit.side_effect = RuntimeError("database unavailable")
    with pytest.raises(remediation_job.RemediationJobFailed) as error:
        await remediation_job._commit_terminal_failure(
            SimpleNamespace(), db, "manual_required", scan=current
        )
    assert error.value.terminal_state_committed is False
    assert vars(current) == before
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_staged_failure_clears_stale_complete_message(monkeypatch):
    monkeypatch.setattr(
        remediation_job, "_fence_claim_for_handler_commit", lambda *_: None
    )
    current = scan()
    current.status = ScanStatus.FAILED
    current.remediation_outcome = RemediationOutcome.REMEDIATION_FAILED.value
    with pytest.raises(remediation_job.RemediationJobFailed):
        await remediation_job._commit_terminal_failure(
            SimpleNamespace(), MagicMock(), "remediation_failed", scan=current
        )
    assert current.progress_message == "Remediation failed"
    assert "before retrying" in current.error_message
