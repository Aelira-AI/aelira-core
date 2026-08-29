"""Focused contracts for durable, killable remediation execution."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.education import remediation_routes as routes
from src.db.models import CloudJobStatus
from src.db.models import (
    CloudFile,
    CloudOAuthCredentials,
    Scan,
    ScanFix,
    ScanResult,
    ScanType,
)
from src.jobs.contracts import public_job_error_code, public_job_result
from src.jobs.remediation_subprocess import (
    RemediationSubprocessError,
    RemediationSubprocessTimeout,
    _claim_output,
    run_remediation_subprocess,
)
from src.services.remediation_artifact_service import ArtifactAuthorizationError


def _request(prefer: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/education/remediate/scan-1",
            "headers": [(b"prefer", prefer.encode())],
        }
    )


@pytest.mark.parametrize(
    "header",
    ("respond-async", "Respond-Async", "wait=3, RESPOND-ASYNC; handling=strict"),
)
def test_respond_async_preference_is_case_insensitive_and_list_safe(header):
    assert routes._prefer_respond_async(_request(header)) is True


@pytest.mark.asyncio
async def test_respond_async_returns_exact_202_contract(monkeypatch):
    waiter = MagicMock()
    monkeypatch.setattr(routes, "_wait_for_remediation_job", waiter)
    response = await routes._respond_for_enqueued_job(
        SimpleNamespace(id="job-1", status="pending"),
        scan_id="scan-1",
        department_id="department-1",
        respond_async=True,
    )
    assert response.status_code == 202
    assert response.body == (
        b'{"job_id":"job-1","scan_id":"scan-1","status":"pending",'
        b'"status_url":"/education/remediation/jobs/job-1"}'
    )
    waiter.assert_not_called()


def test_active_dedupe_fingerprint_is_canonical_and_option_sensitive():
    first = routes._remediation_dedupe_key(
        "scan-1", {"use_ai": True, "latex_formats": ["html", "pdf"]}
    )
    reordered = routes._remediation_dedupe_key(
        "scan-1", {"latex_formats": ["html", "pdf"], "use_ai": True}
    )
    changed = routes._remediation_dedupe_key(
        "scan-1", {"use_ai": False, "latex_formats": ["html", "pdf"]}
    )
    assert first == reordered
    assert first != changed


def test_public_router_exposes_durable_job_contracts():
    contracts = {
        (route.path, frozenset(route.methods or ())) for route in routes.router.routes
    }
    assert ("/remediate/{scan_id}", frozenset({"POST"})) in contracts
    assert ("/remediation/jobs/{job_id}", frozenset({"GET"})) in contracts
    assert (
        "/scans/{scan_id}/remediation/latest",
        frozenset({"GET"}),
    ) in contracts
    assert (
        "/remediation/jobs/{job_id}/download",
        frozenset({"GET"}),
    ) in contracts
    assert ("/scans/{scan_id}/remediated", frozenset({"GET"})) in contracts
    assert (
        "/scans/{scan_id}/remediated/formats",
        frozenset({"GET"}),
    ) in contracts
    assert ("/code/remediate/{scan_id}", frozenset({"POST"})) in contracts
    assert ("/remediate/batch", frozenset({"POST"})) in contracts


def test_latest_remediation_job_returns_null_for_authorized_scan_without_history(
    monkeypatch,
):
    scan = SimpleNamespace(id="scan-1", department_id="department-1")
    scan_query = MagicMock()
    scan_query.filter.return_value.one_or_none.return_value = scan
    job_query = MagicMock()
    job_query.filter.return_value.order_by.return_value.first.return_value = None
    db = MagicMock()
    db.query.side_effect = [scan_query, job_query]
    principal = SimpleNamespace(department_id="department-1")
    authorize = MagicMock()
    monkeypatch.setattr(routes, "authorize_scan_access", authorize)

    result = routes.get_latest_remediation_job("scan-1", db, principal)

    assert result is None
    authorize.assert_called_once_with(db, scan, principal)


def test_public_job_shape_exposes_recorded_total_and_aggregate_remaining(monkeypatch):
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
            "fixed_count": 2,
            "manual_count": 1,
            "failed_count": 2,
            "skipped_count": 3,
            "total_issues": 8,
        },
    )
    monkeypatch.setattr(
        routes,
        "_artifact_is_downloadable",
        MagicMock(return_value=(False, None)),
    )

    result = routes._public_job_shape(MagicMock(), job, "scan-1")

    assert result["fixed_count"] == 2
    assert result["remaining_count"] == 6
    assert result["total_issues"] == 8


def test_local_enqueue_disables_retry_and_uses_scan_option_fingerprint(monkeypatch):
    scan = SimpleNamespace(id="scan-1", storage_path="/uploads/source.pdf")
    principal = SimpleNamespace(department_id="department-1", user_id="user-1")
    db = MagicMock()
    queued = SimpleNamespace(id="job-1", status="pending")
    enqueue = MagicMock(return_value=queued)
    monkeypatch.setattr(routes, "authorize_scan_access", MagicMock(return_value=None))
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=None)
    )
    monkeypatch.setattr(routes.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(routes, "enqueue_cloud_job", enqueue)

    result = routes._enqueue_scan_remediation(
        db,
        scan=scan,
        principal=principal,
        options={"use_ai": False, "generate_alt_text": False},
    )

    assert result is queued
    assert enqueue.call_args.kwargs["provider"] == "local"
    assert enqueue.call_args.kwargs["max_retries"] == 0
    assert enqueue.call_args.kwargs["payload"]["scan_id"] == "scan-1"
    assert enqueue.call_args.kwargs["dedupe_key"].startswith("remediate:scan-1:")
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_code_remediation_route_enqueues_approved_fixes(monkeypatch, tmp_path):
    source = tmp_path / "source.html"
    source.write_text("<html></html>")
    scan = SimpleNamespace(
        id="scan-1",
        scan_type=ScanType.CODE,
        storage_path=str(source),
        result=SimpleNamespace(issues=[{"id": "issue-1"}]),
    )
    job = SimpleNamespace(id="job-1", status="pending")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(id="fix-1")
    ]
    principal = SimpleNamespace(department_id="department-1", user_id="user-1")
    enqueue = MagicMock(return_value=job)
    response = AsyncMock(return_value={"queued": True})
    monkeypatch.setattr(
        routes.ScanService, "get_scan_with_result", MagicMock(return_value=scan)
    )
    monkeypatch.setattr(routes, "authorize_scan_access", MagicMock())
    monkeypatch.setattr(routes, "_enqueue_scan_remediation", enqueue)
    monkeypatch.setattr(routes, "_respond_for_enqueued_job", response)

    result = await routes.remediate_code_scan(
        "scan-1",
        _request("respond-async"),
        db=db,
        principal=principal,
    )

    assert result == {"queued": True}
    options = enqueue.call_args.kwargs["options"]
    assert options["approved_fixes_only"] is True
    assert options["use_ai"] is False
    response.assert_awaited_once_with(
        job,
        scan_id="scan-1",
        department_id="department-1",
        respond_async=True,
    )


@pytest.mark.asyncio
async def test_batch_authorizes_every_scan_then_commits_all_jobs_once(monkeypatch):
    scans = {
        scan_id: SimpleNamespace(
            id=scan_id,
            result=SimpleNamespace(issues=[{"id": f"issue-{scan_id}"}]),
        )
        for scan_id in ("scan-1", "scan-2")
    }
    principal = SimpleNamespace(
        department_id="department-1",
        user_id="user-1",
        as_legacy_tuple=lambda: (None, "user-1", "department-1"),
    )
    db = MagicMock()
    authorize = MagicMock()
    enqueue = MagicMock(
        side_effect=[
            SimpleNamespace(id="job-1"),
            SimpleNamespace(id="job-2"),
        ]
    )
    monkeypatch.setattr(
        routes.ScanService,
        "get_scan_with_result",
        MagicMock(side_effect=lambda *, db, scan_id: scans[scan_id]),
    )
    monkeypatch.setattr(routes, "authorize_scan_access", authorize)
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=None)
    )
    monkeypatch.setattr(routes, "_enqueue_scan_remediation", enqueue)
    feature = AsyncMock()
    monkeypatch.setattr(routes, "require_feature", feature)

    result = await routes._batch_remediate_impl(
        scan_ids=["scan-1", "scan-2"],
        use_ai=False,
        background_tasks=MagicMock(),
        db=db,
        principal=principal,
    )

    assert result["job_ids"] == ["job-1", "job-2"]
    assert result["scans_queued"] == ["scan-1", "scan-2"]
    assert all(call.kwargs["commit"] is False for call in enqueue.call_args_list)
    assert [call.args[1].id for call in authorize.call_args_list[:2]] == [
        "scan-1",
        "scan-2",
    ]
    feature.assert_awaited_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_batch_enqueue_failure_rolls_back_all_jobs(monkeypatch):
    scans = {
        scan_id: SimpleNamespace(id=scan_id, result=SimpleNamespace(issues=[{}]))
        for scan_id in ("scan-1", "scan-2")
    }
    principal = SimpleNamespace(
        department_id="department-1",
        user_id="user-1",
        as_legacy_tuple=lambda: (None, "user-1", "department-1"),
    )
    db = MagicMock()
    monkeypatch.setattr(
        routes.ScanService,
        "get_scan_with_result",
        MagicMock(side_effect=lambda *, db, scan_id: scans[scan_id]),
    )
    monkeypatch.setattr(routes, "authorize_scan_access", MagicMock())
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=None)
    )
    monkeypatch.setattr(routes, "require_feature", AsyncMock())
    monkeypatch.setattr(
        routes,
        "_enqueue_scan_remediation",
        MagicMock(side_effect=[SimpleNamespace(id="job-1"), RuntimeError("boom")]),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await routes._batch_remediate_impl(
            scan_ids=["scan-1", "scan-2"],
            use_ai=False,
            background_tasks=MagicMock(),
            db=db,
            principal=principal,
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once()


def test_job_status_lookup_hides_cross_account_job():
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    principal = SimpleNamespace(department_id="department-2")

    with pytest.raises(HTTPException) as exc_info:
        routes._authorized_remediation_job(db, "job-1", principal)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_legacy_wait_is_nonblocking_and_never_exceeds_300_seconds(monkeypatch):
    def slow_snapshot(*_args):
        import time

        time.sleep(0.02)
        return {"status": "pending"}

    monkeypatch.setattr(routes, "_load_remediation_job", slow_snapshot)
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(8):
            await asyncio.sleep(0.005)
            ticks += 1

    snapshot, _ = await asyncio.gather(
        routes._wait_for_remediation_job(
            "job-1", "department-1", timeout_seconds=0.06, poll_seconds=0.005
        ),
        ticker(),
    )
    assert snapshot == {"status": "pending"}
    assert ticks == 8
    assert routes.LEGACY_REMEDIATION_WAIT_SECONDS == 300.0


def test_public_job_projection_drops_paths_payloads_and_unknown_errors(monkeypatch):
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-1",
        status=CloudJobStatus.FAILED.value,
        progress=500,
        result_data={
            "fixed_count": 2,
            "original_compliance_score": 71.5,
            "remediated_compliance_score": 93.0,
            "manual_count": "/srv/private-count",
            "scan_id": "../../foreign-scan",
            "compliance_improvement": float("inf"),
            "storage_key": "/srv/output.pdf",
        },
        last_error_code="/srv/traceback ValueError",
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
    )
    monkeypatch.setattr(routes, "_artifact_is_downloadable", lambda *_: (False, None))
    response = routes._public_job_shape(MagicMock(), job, "scan-1")
    assert response["progress_message"] == "Failed"
    assert response["progress"] == 100
    assert response["error_code"] is None
    assert response["original_score"] == 71.5
    assert response["remediated_score"] == 93.0
    assert "/srv/" not in str(response)
    assert public_job_result(job.result_data) == {
        "fixed_count": 2,
        "original_compliance_score": 71.5,
        "remediated_compliance_score": 93.0,
    }
    assert public_job_error_code(job.last_error_code) is None


@pytest.mark.asyncio
async def test_hard_timeout_reaps_child_and_removes_work_directory(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("<!doctype html><html lang='en'></html>")
    work_root = tmp_path / "work"
    with pytest.raises(RemediationSubprocessTimeout, match="job_execution_timeout"):
        await run_remediation_subprocess(
            source_path=str(source),
            scan_type="CODE",
            issues=[],
            options={"use_ai": False},
            work_root=work_root,
            timeout_seconds=0.0001,
            termination_grace_seconds=0.01,
        )
    assert list(work_root.iterdir()) == []


@pytest.mark.asyncio
async def test_normal_child_returns_only_serializable_result_and_bound_output(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("<!doctype html><html lang='en'><title>ok</title></html>")
    work_root = tmp_path / "work"
    execution = await run_remediation_subprocess(
        source_path=str(source),
        scan_type="CODE",
        issues=[],
        options={"use_ai": False},
        work_root=work_root,
        timeout_seconds=20,
        termination_grace_seconds=0.1,
    )
    try:
        assert execution.success is True
        assert execution.fixed_count == 0
        assert execution.has_output_claim() is True
        with execution.open_output_stream() as stream:
            assert b"<html" in stream.read()
        assert list(work_root.iterdir()) == []
    finally:
        execution.close_output_claim()


@pytest.mark.asyncio
async def test_nonserializable_child_request_fails_with_stable_code(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("<!doctype html><html lang='en'></html>")
    with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
        await run_remediation_subprocess(
            source_path=str(source),
            scan_type="CODE",
            issues=[],
            options={"invalid": object()},
            work_root=tmp_path / "work",
            timeout_seconds=20,
            termination_grace_seconds=0.1,
        )
    assert list((tmp_path / "work").iterdir()) == []


def test_timeout_is_an_allowlisted_terminal_public_code():
    assert public_job_error_code("job_execution_timeout") == "job_execution_timeout"


def test_remediation_deadline_defaults_are_30_minutes_and_10_seconds(monkeypatch):
    from src.config.settings import Settings

    monkeypatch.delenv("REMEDIATION_EXECUTION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("REMEDIATION_TERMINATION_GRACE_SECONDS", raising=False)
    timeout_factory = Settings.model_fields[
        "remediation_execution_timeout_seconds"
    ].default_factory
    grace_factory = Settings.model_fields[
        "remediation_termination_grace_seconds"
    ].default_factory
    assert timeout_factory is not None
    assert grace_factory is not None
    assert timeout_factory() == 1800.0
    assert grace_factory() == 10.0


def _claim_child_output(path, work_dir):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd = os.open(work_dir, flags)
    try:
        return _claim_output(str(path), work_dir=work_dir, work_dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def test_parent_claim_owns_exact_child_bytes_after_path_replacement(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = work_dir / "fixed.pdf"
    original = b"%PDF-exact-child-output"
    output.write_bytes(original)
    claim = _claim_child_output(output, work_dir)
    assert claim is not None
    try:
        replacement = work_dir / "replacement.pdf"
        replacement.write_bytes(b"%PDF-replacement")
        os.replace(replacement, output)
        assert claim.size == len(original)
        assert claim.sha256 == hashlib.sha256(original).hexdigest()
        with claim.open_stream() as stream:
            assert stream.read() == original
    finally:
        claim.close()


def test_parent_claim_rejects_symlinked_output_components(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "fixed.pdf").write_bytes(b"%PDF-outside")
    (work_dir / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RemediationSubprocessError, match="remediation_failed"):
        _claim_child_output(work_dir / "nested" / "fixed.pdf", work_dir)


def test_image_equation_artifact_is_not_downloadable_before_approval(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1", cloud_file_id=None, sha256="a" * 64, provider_result={}
    )
    job = SimpleNamespace(
        status=CloudJobStatus.COMPLETED.value,
        department_id="department-1",
        cloud_file_id=None,
        result_data={"artifact_id": "artifact-1"},
    )
    db = MagicMock()
    db.get.return_value = artifact
    db.query.return_value.filter.return_value.first.return_value = ("fix-1",)

    class Service:
        def resolve_record(self, *_args, **kwargs):
            assert kwargs["require_approved"] is True
            assert kwargs["approval_checksum"] == "a" * 64
            raise ArtifactAuthorizationError("approval required")

    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)


def test_equation_artifact_stays_gated_if_current_fix_row_is_removed(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1", cloud_file_id=None, sha256="a" * 64, provider_result={}
    )
    job = SimpleNamespace(
        status=CloudJobStatus.COMPLETED.value,
        department_id="department-1",
        cloud_file_id=None,
        result_data={"artifact_id": "artifact-1", "download_available": False},
    )
    db = MagicMock()
    db.get.return_value = artifact
    db.query.return_value.filter.return_value.first.return_value = None

    class Service:
        def resolve_record(self, *_args, **kwargs):
            assert kwargs["require_approved"] is True
            assert kwargs["approval_checksum"] == "a" * 64
            raise ArtifactAuthorizationError("approval required")

    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)


def test_download_validation_uses_job_cloud_authority(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1",
        cloud_file_id="cloud-file-other",
        sha256="a" * 64,
        provider_result={},
    )
    job = SimpleNamespace(
        status=CloudJobStatus.COMPLETED.value,
        department_id="department-1",
        cloud_file_id="cloud-file-1",
        result_data={"artifact_id": "artifact-1"},
    )
    db = MagicMock()
    db.get.return_value = artifact
    db.query.return_value.filter.return_value.first.return_value = None

    class Service:
        def resolve_record(self, *_args, **kwargs):
            assert kwargs["cloud_file_id"] == "cloud-file-1"

    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (True, artifact)


@pytest.mark.asyncio
async def test_local_job_uses_scan_authority_without_cloud_credentials(monkeypatch):
    from src.jobs import remediation_job

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        storage_path="/uploads/source.pdf",
        status="completed",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={
            "scan_id": "scan-1",
            "options": {"use_ai": False},
            "requested_by_id": "user-1",
        },
        execution_context={},
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()

    def get(model, identity, **_kwargs):
        if model is Scan and identity == "scan-1":
            return scan
        if model in {CloudFile, CloudOAuthCredentials}:
            return None
        return None

    db.get.side_effect = get
    process = AsyncMock(
        return_value={
            "success": True,
            "scan_id": "scan-1",
            "fixed_count": 0,
            "manual_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }
    )
    runtime = object()
    runtime_factory = MagicMock(return_value=runtime)
    monkeypatch.setattr(remediation_job, "workspace_provider_runtime", runtime_factory)
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    result = await remediation_job.handle_remediation_job(job, db, object())
    assert result["success"] is True
    call = process.await_args
    assert call.args[0]["provider"] == "local"
    assert call.args[0]["file_path"] == "/uploads/source.pdf"
    assert call.args[0]["options"] == {"use_ai": False}
    runtime_factory.assert_called_once_with("department-1")
    assert call.kwargs["lms_policy_authoritative"] is False
    assert call.kwargs["ai_client"] is runtime
    assert call.kwargs["alt_text_client"] is runtime


@pytest.mark.asyncio
async def test_claimed_queue_timeout_returns_terminal_code_without_retry_hint(
    tmp_path, monkeypatch
):
    from src.jobs import remediation_job

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.PDF,
        storage_path=str(source),
        status="processing",
        remediation_outcome=None,
        completed_at=None,
        metadata={},
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is Scan:
            chain.first.return_value = scan
        elif model is ScanResult:
            chain.first.return_value = SimpleNamespace(
                issues=[{"id": "issue-1", "category": "heading"}]
            )
        return chain

    db.query.side_effect = query
    service = SimpleNamespace(root=tmp_path / "artifacts")
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: service),
    )
    child = AsyncMock(side_effect=RemediationSubprocessTimeout("job_execution_timeout"))
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", child)
    result = await remediation_job.process_remediation_job(
        {
            "job_id": "job-1",
            "scan_id": "scan-1",
            "department_id": "department-1",
            "file_path": str(source),
            "options": {"use_ai": False},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )
    assert result == {
        "success": False,
        "error": "job_execution_timeout",
        "scan_id": "scan-1",
    }
    assert "failure_kind" not in result
    child.assert_awaited_once()


def test_zero_retry_budget_is_not_coerced_to_default():
    from src.jobs.remediation_job import (
        RetryableRemediationJobError,
        transition_retryable_remediation_job,
    )

    job = SimpleNamespace(
        status=CloudJobStatus.PROCESSING.value,
        result_data={"scan_id": "scan-1"},
        retry_count=0,
        max_retries=0,
        error_message=None,
        progress=20,
        progress_message="Publishing",
        completed_at=None,
    )
    db = MagicMock()

    transition_retryable_remediation_job(
        job, db, RetryableRemediationJobError("remediation_artifact_retryable")
    )

    assert job.retry_count == 1
    assert job.status == CloudJobStatus.FAILED.value
    assert job.completed_at is not None
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_worker_revalidates_approved_code_fixes_before_child(
    monkeypatch, tmp_path
):
    from src.jobs import remediation_job

    source = tmp_path / "source.html"
    source.write_text("<html><img></html>")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.CODE,
        storage_path=str(source),
        status="processing",
        remediation_outcome=None,
        completed_at=None,
        metadata={},
    )
    scan_result = SimpleNamespace(issues=[{"id": "unapproved-source-issue"}])
    approved = SimpleNamespace(
        id="fix-1",
        issue_id="approved-issue",
        category="alt_text",
        severity="high",
        description="Missing alt text",
        location="line 1",
        original_content="<img>",
        fixed_content='<img alt="approved">',
        wcag_criteria="1.1.1",
        review_status="approved",
        updated_at=None,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is Scan:
            chain.first.return_value = scan
        elif model is ScanResult:
            chain.first.return_value = scan_result
        elif model is ScanFix:
            chain.all.return_value = [approved]
        return chain

    db.query.side_effect = query
    child = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            fixed_count=0,
            manual_count=1,
            failed_count=0,
            skipped_count=0,
            total_issues=1,
            fixed_issues=[],
            manual_issues=[],
            verification_passed=True,
            close_output_claim=MagicMock(),
        )
    )
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", child)
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: SimpleNamespace(root=tmp_path / "artifacts")),
    )

    result = await remediation_job.process_remediation_job(
        {
            "job_id": "job-1",
            "scan_id": "scan-1",
            "department_id": "department-1",
            "file_path": str(source),
            "options": {"use_ai": False, "approved_fixes_only": True},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )

    assert result["error"] == "manual_required"
    child_issues = child.await_args.kwargs["issues"]
    assert child_issues == [
        {
            "id": "approved-issue",
            "category": "alt_text",
            "severity": "high",
            "description": "Missing alt text",
            "location": "line 1",
            "original_content": "<img>",
            "fix_suggestion": '<img alt="approved">',
            "fixed_content": '<img alt="approved">',
            "wcag_criteria": "1.1.1",
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_worker_fails_closed_when_code_approval_is_revoked(monkeypatch):
    from src.jobs import remediation_job

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.CODE,
        status="processing",
        remediation_outcome=None,
        completed_at=None,
        metadata={},
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is Scan:
            chain.first.return_value = scan
        elif model is ScanResult:
            chain.first.return_value = SimpleNamespace(issues=[{"id": "issue-1"}])
        elif model is ScanFix:
            chain.all.return_value = []
        return chain

    db.query.side_effect = query
    child = AsyncMock()
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", child)

    result = await remediation_job.process_remediation_job(
        {
            "job_id": "job-1",
            "scan_id": "scan-1",
            "department_id": "department-1",
            "options": {"use_ai": False, "approved_fixes_only": True},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )

    assert result == {
        "success": False,
        "error": "manual_required",
        "scan_id": "scan-1",
    }
    child.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_queue_defers_ai_to_workspace_runtime(monkeypatch):
    from src.jobs import remediation_job

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.PDF,
        storage_path="/uploads/source.pdf",
        status="processing",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={
            "scan_id": "scan-1",
            "options": {"use_ai": False, "generate_alt_text": True},
            "requested_by_id": "user-1",
        },
        execution_context={
            "alt_text_requested": True,
            "requested_purposes": ["alt_text"],
        },
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identity, **_kwargs: (
        scan if model is Scan and identity == "scan-1" else None
    )
    bind = MagicMock()
    process = AsyncMock(
        return_value={
            "success": True,
            "scan_id": "scan-1",
            "fixed_count": 0,
            "manual_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }
    )
    runtime = object()
    runtime_factory = MagicMock(return_value=runtime)
    monkeypatch.setattr(remediation_job.LMSRemediationClient, "bind_if_allowed", bind)
    monkeypatch.setattr(remediation_job, "workspace_provider_runtime", runtime_factory)
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)

    await remediation_job.handle_remediation_job(job, db, object())

    bind.assert_not_called()
    runtime_factory.assert_called_once_with("department-1")
    call = process.await_args
    assert call.kwargs["lms_policy_authoritative"] is False
    assert call.kwargs["alt_text_client"] is runtime
    assert call.kwargs["ai_client"] is runtime


@pytest.mark.parametrize("configured", (True, False))
def test_local_child_rebinds_alt_text_without_legacy_provider_fallback(
    monkeypatch, tmp_path, configured
):
    from src.jobs import remediation_subprocess
    from src.ai.lms_remediation_client import LMSRemediationClient

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    purpose_client = SimpleNamespace(purpose="alt_text", provider="ollama")
    bind = MagicMock(return_value=purpose_client if configured else None)
    constructor = MagicMock(return_value=object())
    provider_manager = MagicMock()
    monkeypatch.setattr(LMSRemediationClient, "bind_if_allowed", bind)
    monkeypatch.setattr(
        "src.education.remediation.pdf_remediator.PdfRemediator", constructor
    )
    monkeypatch.setattr("src.ai.providers.get_provider_manager", provider_manager)

    remediation_subprocess._build_remediator(
        {
            "scan_type": "PDF",
            "issues": [],
            "options": {"use_ai": False},
            "lms_binding": {
                "department_id": "department-1",
                "job_id": "job-1",
                "scan_id": "scan-1",
                "remediation": False,
                "alt_text": configured,
            },
        },
        source,
        tmp_path,
    )

    provider_manager.assert_not_called()
    kwargs = constructor.call_args.kwargs
    assert kwargs["ai_client"] is None
    assert kwargs["alt_text_client"] is (purpose_client if configured else None)
    assert kwargs["config"].allow_legacy_nested_ai is False
    assert kwargs["config"].fix_alt_text is configured


@pytest.mark.asyncio
async def test_inherited_artifact_download_revalidates_equation_approval(monkeypatch):
    artifact = SimpleNamespace(
        id="artifact-1",
        filename="fixed.pdf",
        mime_type="application/pdf",
        size_bytes=5,
        sha256="a" * 64,
        provider_result={"requires_approval": True},
    )
    principal = SimpleNamespace(department_id="department-1")
    opened = {}

    class Context:
        def __enter__(self):
            from io import BytesIO

            return BytesIO(b"fixed")

        def __exit__(self, *_args):
            return None

    class Service:
        def open_verified(self, *_args, **kwargs):
            opened.update(kwargs)
            return Context()

    monkeypatch.setattr(
        routes,
        "_managed_artifact_authority",
        MagicMock(return_value=(SimpleNamespace(id="scan-1"), None, artifact)),
    )
    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )

    await routes.download_managed_artifact(
        "scan-1", "artifact-1", db=MagicMock(), principal=principal
    )

    assert opened["require_approved"] is True
    assert opened["approval_checksum"] == "a" * 64


@pytest.mark.asyncio
async def test_code_fix_revocation_after_child_aborts_before_publication(
    monkeypatch, tmp_path
):
    from src.jobs import remediation_job

    source = tmp_path / "source.html"
    source.write_text("<html><img></html>")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.CODE,
        storage_path=str(source),
        status="processing",
        remediation_outcome=None,
        completed_at=None,
        metadata={},
    )
    approved = SimpleNamespace(
        id="fix-1",
        issue_id="issue-1",
        category="alt_text",
        severity="high",
        description="Missing alt text",
        location="line 1",
        original_content="<img>",
        fixed_content='<img alt="approved">',
        wcag_criteria="1.1.1",
        review_status="approved",
        updated_at=None,
    )
    db = MagicMock()
    db.no_autoflush = nullcontext()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_for_update.return_value = chain
        chain.populate_existing.return_value = chain
        if model is Scan:
            chain.first.return_value = scan
            chain.one_or_none.return_value = scan
        elif model is ScanResult:
            chain.first.return_value = SimpleNamespace(issues=[{"id": "issue-1"}])
        elif model is ScanFix:
            chain.all.return_value = [approved]
        return chain

    db.query.side_effect = query

    async def child(**_kwargs):
        approved.review_status = "rejected"
        return SimpleNamespace(
            success=True,
            fixed_count=1,
            manual_count=0,
            failed_count=0,
            skipped_count=0,
            total_issues=1,
            fixed_issues=[SimpleNamespace(issue_id="issue-1")],
            manual_issues=[],
            verification_passed=True,
            close_output_claim=MagicMock(),
        )

    service = SimpleNamespace(
        root=tmp_path / "artifacts", claim_and_publish_stream=MagicMock()
    )
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", child)
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: service),
    )

    result = await remediation_job.process_remediation_job(
        {
            "job_id": "job-1",
            "scan_id": "scan-1",
            "department_id": "department-1",
            "file_path": str(source),
            "options": {"use_ai": False, "approved_fixes_only": True},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )

    assert result == {
        "success": False,
        "error": "manual_required",
        "scan_id": "scan-1",
    }
    service.claim_and_publish_stream.assert_not_called()


@pytest.mark.asyncio
async def test_handler_revalidates_code_fix_under_completion_fence(monkeypatch):
    from src.jobs import remediation_job

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.CODE,
        storage_path="/uploads/source.html",
        status="processing",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={"scan_id": "scan-1", "options": {"use_ai": False}},
        execution_context={},
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identity, **_kwargs: (
        scan if model is Scan and identity == "scan-1" else None
    )
    result = remediation_job.RemediationProcessingResult(
        {
            "success": True,
            "scan_id": "scan-1",
            "artifact_id": "artifact-1",
        },
        approved_fix_snapshot={"fix-1": ("approved",)},
    )
    events = []
    monkeypatch.setattr(
        remediation_job, "process_remediation_job", AsyncMock(return_value=result)
    )
    monkeypatch.setattr(
        remediation_job,
        "_fence_claim_for_handler_commit",
        lambda *_args: events.append("handler_fenced"),
    )

    def reject(*_args, **_kwargs):
        events.append("approval_revalidated")
        raise remediation_job.ApprovedFixAuthorityError("revoked")

    monkeypatch.setattr(remediation_job, "_lock_and_revalidate_approved_fixes", reject)
    monkeypatch.setattr(
        remediation_job,
        "_abort_completion_publication",
        lambda *_args, **_kwargs: events.append("publication_aborted"),
    )
    terminal = AsyncMock(
        side_effect=remediation_job.RemediationJobFailed("manual_required")
    )
    monkeypatch.setattr(remediation_job, "_commit_terminal_failure", terminal)

    with pytest.raises(remediation_job.RemediationJobFailed, match="manual_required"):
        await remediation_job.handle_remediation_job(job, db, object())

    assert events == [
        "handler_fenced",
        "approval_revalidated",
        "publication_aborted",
    ]
    terminal.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_marks_only_revalidated_code_fixes_applied(monkeypatch):
    from src.jobs import remediation_job

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.CODE,
        storage_path="/uploads/source.html",
        status="processing",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={"scan_id": "scan-1", "options": {"use_ai": False}},
        execution_context={},
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identity, **_kwargs: (
        scan if model is Scan and identity == "scan-1" else None
    )
    approved = SimpleNamespace(
        id="fix-1", issue_id="issue-1", review_status="approved", updated_at=None
    )
    result = remediation_job.RemediationProcessingResult(
        {"success": True, "scan_id": "scan-1"},
        approved_fix_snapshot={"fix-1": ("approved",)},
        applied_fix_ids=frozenset({"issue-1"}),
    )
    monkeypatch.setattr(
        remediation_job, "process_remediation_job", AsyncMock(return_value=result)
    )
    monkeypatch.setattr(remediation_job, "_fence_claim_for_handler_commit", MagicMock())
    revalidate = MagicMock(return_value=[approved])
    monkeypatch.setattr(
        remediation_job, "_lock_and_revalidate_approved_fixes", revalidate
    )

    returned = await remediation_job.handle_remediation_job(job, db, object())

    assert returned["success"] is True
    assert approved.review_status == "applied"
    assert approved.updated_at is not None
    revalidate.assert_called_once()
    db.commit.assert_called_once()
