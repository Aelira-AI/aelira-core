"""Focused contracts for durable, killable remediation execution."""

from __future__ import annotations

import asyncio
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
    artifact = SimpleNamespace(id="artifact-1", cloud_file_id=None)
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
            raise ArtifactAuthorizationError("approval required")

    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)


def test_equation_artifact_stays_gated_if_current_fix_row_is_removed(monkeypatch):
    artifact = SimpleNamespace(id="artifact-1", cloud_file_id=None)
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
            raise ArtifactAuthorizationError("approval required")

    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: Service()),
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)


def test_download_validation_uses_job_cloud_authority(monkeypatch):
    artifact = SimpleNamespace(id="artifact-1", cloud_file_id="cloud-file-other")
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
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    result = await remediation_job.handle_remediation_job(job, db, object())
    assert result["success"] is True
    call = process.await_args
    assert call.args[0]["provider"] == "local"
    assert call.args[0]["file_path"] == "/uploads/source.pdf"
    assert call.args[0]["options"] == {"use_ai": False}


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
