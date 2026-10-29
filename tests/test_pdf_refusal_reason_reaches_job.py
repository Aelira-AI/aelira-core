"""A PDF the remediator refuses for a known reason keeps that reason.

The remediator records why it declined a file. That reason has to survive the
private subprocess boundary and the job result so the dashboard can show its
existing message instead of a generic terminal failure.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs import remediation_job
from src.jobs.contracts import public_job_result
from src.jobs.remediation_subprocess import (
    RemediationSubprocessError,
    run_remediation_subprocess,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pdfs" / "textbook_chapter.pdf"
ISSUES = [
    {
        "id": "issue-1",
        "category": "language",
        "type": "missing_language",
        "severity": "high",
        "description": "Document language is not set",
    }
]


@pytest.mark.parametrize(
    "reason", ["source_text_mapping_unavailable", "source_text_scope_unsupported"]
)
def test_error_carries_known_refusal_reasons(reason):
    error = RemediationSubprocessError(
        "remediation_failed", score_verification_reason=reason
    )
    assert error.score_verification_reason == reason


@pytest.mark.parametrize(
    "reason", [None, "", "/srv/uploads/secret.pdf", "incomplete_comparison", 7]
)
def test_error_drops_reasons_that_are_not_known_refusals(reason):
    error = RemediationSubprocessError(
        "remediation_failed", score_verification_reason=reason
    )
    assert error.score_verification_reason is None


async def test_real_child_refusal_reaches_the_parent(tmp_path):
    with pytest.raises(RemediationSubprocessError) as refusal:
        await run_remediation_subprocess(
            source_path=str(FIXTURE),
            scan_type="PDF",
            issues=ISSUES,
            options={"use_ai": False},
            work_root=tmp_path / "work",
            timeout_seconds=120,
            termination_grace_seconds=0.5,
        )
    assert str(refusal.value) == "remediation_failed"
    assert refusal.value.score_verification_reason == "source_text_mapping_unavailable"


def _job_database(scan):
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = (
            scan
            if model is remediation_job.Scan
            else (
                SimpleNamespace(issues=ISSUES, compliance_score=48.2)
                if model is remediation_job.ScanResult
                else None
            )
        )
        chain.one_or_none.return_value = chain.first.return_value
        return chain

    db.query.side_effect = query
    return db


def _scan(path):
    return SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=remediation_job.ScanType.PDF,
        storage_path=str(path),
        file_name=path.name,
        file_hash=None,
    )


async def _process(monkeypatch, tmp_path, source):
    scan = _scan(source)
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        lambda: SimpleNamespace(root=tmp_path / "managed"),
    )
    result = await remediation_job.process_remediation_job(
        {"scan_id": scan.id, "department_id": scan.department_id, "job_id": "job-1"},
        _job_database(scan),
        assert_owned=AsyncMock(),
    )
    return scan, result


@pytest.mark.parametrize(
    "reason", ["source_text_mapping_unavailable", "source_text_scope_unsupported"]
)
async def test_job_result_keeps_the_refusal_reason(monkeypatch, tmp_path, reason):
    source = tmp_path / "source.pdf"
    source.write_bytes(FIXTURE.read_bytes())
    runner = AsyncMock(
        side_effect=RemediationSubprocessError(
            "remediation_failed", score_verification_reason=reason
        )
    )
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", runner)

    scan, result = await _process(monkeypatch, tmp_path, source)

    assert result["success"] is False
    assert result["error"] == "remediation_failed"
    assert result["score_verification_reason"] == reason
    persisted = remediation_job._safe_failure_result(result["error"], result, scan)
    assert persisted["score_verified"] is False
    assert persisted["score_verification_reason"] == reason
    public = public_job_result(persisted)
    assert public["score_verified"] is False
    assert public["score_verification_reason"] == reason


async def test_job_result_does_not_invent_a_reason(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(FIXTURE.read_bytes())
    runner = AsyncMock(side_effect=RemediationSubprocessError("remediation_failed"))
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", runner)

    scan, result = await _process(monkeypatch, tmp_path, source)

    assert result["error"] == "remediation_failed"
    assert "score_verification_reason" not in result
    persisted = remediation_job._safe_failure_result(result["error"], result, scan)
    assert persisted["score_verification_reason"] == "legacy_unverified"


async def test_refused_pdf_reaches_the_job_result_end_to_end(monkeypatch, tmp_path):
    """Real remediator, real child process, real job layer: no stubs between."""
    source = tmp_path / "textbook_chapter.pdf"
    shutil.copyfile(FIXTURE, source)
    scan, result = await _process(monkeypatch, tmp_path, source)

    assert result["success"] is False
    assert result["error"] == "remediation_failed"
    assert result["score_verification_reason"] == "source_text_mapping_unavailable"
    public = public_job_result(
        remediation_job._safe_failure_result(result["error"], result, scan)
    )
    assert public["score_verified"] is False
    assert public["score_verification_reason"] == "source_text_mapping_unavailable"
