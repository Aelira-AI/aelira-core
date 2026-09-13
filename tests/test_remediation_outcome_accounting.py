"""Published outcomes account for source findings, not unpublished file changes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.contracts import public_job_result


def test_withheld_changes_remain_unresolved_and_survive_failure_projection():
    from src.jobs.remediation_job import _safe_failure_result
    from src.education.remediation.outcome_accounting import outcome_accounting

    issues = [{"id": str(index)} for index in range(8)]
    result = SimpleNamespace(
        fixed_issues=[SimpleNamespace(issue_id=str(index)) for index in range(5)],
        manual_issues=[SimpleNamespace(issue_id=str(index)) for index in range(5, 8)],
        failed_issues=[],
    )
    counts = outcome_accounting(issues, result, published=False)
    assert counts["fixed_count"] == 0
    assert counts["withheld_count"] == 5
    assert counts["manual_count"] == 3
    assert counts["remaining_count"] == counts["total_issues"] == 8
    assert counts["outcome_unreported_count"] == 0
    projected = public_job_result(_safe_failure_result("manual_required", counts, None))
    assert projected["issue_outcomes"] == counts["issue_outcomes"]
    assert projected["withheld_count"] == 5
    assert projected["remaining_count"] == 8


def test_unmatched_and_ambiguous_outcomes_are_never_invented():
    from src.education.remediation.outcome_accounting import outcome_accounting

    issues = [{"id": "duplicate"}, {"id": "duplicate"}, {"id": "missing"}]
    result = SimpleNamespace(fixed_issues=[SimpleNamespace(issue_id="duplicate")])
    counts = outcome_accounting(issues, result, published=True)
    assert counts["fixed_count"] == 0
    assert counts["outcome_unreported_count"] == 3
    assert counts["remaining_count"] == 3
    assert [item["source_index"] for item in counts["issue_outcomes"]] == [0, 1, 2]


def test_public_outcomes_are_bounded_and_cannot_leak_freeform_details():
    result = public_job_result(
        {
            "issue_outcomes": [
                {
                    "source_index": 0,
                    "issue_id": "good-id",
                    "status": "withheld",
                    "reason": "/private/secret",
                },
                {"source_index": 1, "issue_id": "/private/secret", "status": "manual"},
                {"source_index": 2, "status": "invented"},
            ]
        }
    )
    assert result["issue_outcomes"] == [
        {"source_index": 0, "issue_id": "good-id", "status": "withheld"},
        {"source_index": 1, "status": "manual"},
    ]


def test_large_finding_ledger_survives_queue_json_without_silent_truncation():
    from src.jobs.contracts import JobSuccess

    records = [
        {"source_index": index, "issue_id": f"source-{index}", "status": "unreported"}
        for index in range(1000)
    ]
    result = JobSuccess({"issue_outcomes": records})
    assert result.result["issue_outcomes"] == records


def test_generated_ids_are_not_presented_as_persisted_source_ids():
    from src.education.remediation.outcome_accounting import outcome_accounting

    result = SimpleNamespace(fixed_issues=[SimpleNamespace(issue_id="source-0")])
    counts = outcome_accounting(
        [{"id": "source-0"}],
        result,
        published=False,
        original_issues=[{"description": "Missing title"}],
    )
    assert counts["issue_outcomes"] == [
        {"source_index": 0, "source_index_scope": "original_scan", "status": "withheld"}
    ]


def test_terminal_failure_relabels_provisional_fixed_outcomes_as_withheld():
    from src.jobs.remediation_job import _safe_failure_result

    result = _safe_failure_result(
        "remediation_artifact_unavailable",
        {
            "fixed_count": 1,
            "total_issues": 1,
            "issue_outcomes": [{"source_index": 0, "status": "fixed"}],
        },
        None,
    )
    assert result["fixed_count"] == 0
    assert result["withheld_count"] == 1
    assert result["issue_outcomes"][0]["status"] == "withheld"


def test_legacy_failed_job_accounts_for_unreported_remainder(monkeypatch):
    from src.api.education import remediation_routes as routes

    job = SimpleNamespace(
        id="job-1",
        status="failed",
        progress=100,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code="manual_required",
        result_data={
            "fixed_count": 0,
            "manual_count": 3,
            "failed_count": 0,
            "skipped_count": 0,
            "total_issues": 8,
        },
    )
    monkeypatch.setattr(routes, "_artifact_is_downloadable", lambda *_: (False, None))
    result = routes._public_job_shape(MagicMock(), job, "scan-1")
    assert result["remaining_count"] == 8
    assert result["outcome_unreported_count"] == 5
    assert result["issue_outcomes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manual_count,verified,error",
    [(3, True, "manual_required"), (0, False, "remediation_artifact_unavailable")],
)
async def test_worker_refuses_partial_and_regressed_candidates_with_complete_accounting(
    tmp_path, monkeypatch, manual_count, verified, error
):
    from src.db.models import Scan, ScanResult, ScanType
    from src.jobs import remediation_job

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.PDF,
        storage_path=str(source),
        status="processing",
        remediation_outcome=None,
        completed_at=None,
    )
    issues = [
        {"id": str(index), "description": "Missing heading"} for index in range(8)
    ]
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is Scan:
            chain.first.return_value = scan
            chain.one_or_none.return_value = scan
        elif model is ScanResult:
            chain.first.return_value = SimpleNamespace(
                issues=issues, compliance_score=47.4
            )
        return chain

    db.query.side_effect = query
    fixed_count = 8 - manual_count
    child_result = SimpleNamespace(
        success=True,
        total_issues=8,
        fixed_count=fixed_count,
        manual_count=manual_count,
        failed_count=0,
        skipped_count=0,
        fixed_issues=[
            SimpleNamespace(issue_id=str(index)) for index in range(fixed_count)
        ],
        manual_issues=[
            SimpleNamespace(issue_id=str(index)) for index in range(fixed_count, 8)
        ],
        verification_passed=verified,
        has_output_claim=lambda: True,
        close_output_claim=MagicMock(),
    )
    monkeypatch.setattr(
        remediation_job,
        "run_remediation_subprocess",
        AsyncMock(return_value=child_result),
    )
    service = SimpleNamespace(
        root=tmp_path / "artifacts", claim_and_publish_stream=MagicMock()
    )
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: service),
    )
    result = await remediation_job.process_remediation_job(
        {
            "job_id": "job-1",
            "scan_id": "scan-1",
            "department_id": "dept-1",
            "file_path": str(source),
            "options": {"use_ai": False},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )
    assert result["success"] is False
    assert result["error"] == error
    assert result["fixed_count"] == 0
    assert result["withheld_count"] == fixed_count
    assert result["manual_count"] == manual_count
    assert result["remaining_count"] == 8
    assert len(result["issue_outcomes"]) == 8
    service.claim_and_publish_stream.assert_not_called()
