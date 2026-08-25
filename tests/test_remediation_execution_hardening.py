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
from src.db.models import CloudFile, CloudOAuthCredentials, Scan, ScanResult, ScanType
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
        progress=50,
        result_data={"fixed_count": 2, "storage_key": "/srv/output.pdf"},
        last_error_code="/srv/traceback ValueError",
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
    )
    monkeypatch.setattr(routes, "_artifact_is_downloadable", lambda *_: (False, None))
    response = routes._public_job_shape(MagicMock(), job, "scan-1")
    assert response["progress_message"] == "Failed"
    assert response["error_code"] is None
    assert "/srv/" not in str(response)
    assert public_job_result(job.result_data) == {"fixed_count": 2}
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
