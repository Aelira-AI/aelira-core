"""Task17B durable handler truth and payload tests."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_registry_has_real_handler_for_every_enqueueable_type():
    from src.jobs.registry import EXECUTABLE_JOB_TYPES, build_default_registry

    registry = build_default_registry()
    assert "webhook_refresh" in EXECUTABLE_JOB_TYPES
    assert registry.job_types == EXECUTABLE_JOB_TYPES


@pytest.mark.asyncio
async def test_cloud_sync_partial_folder_failure_is_typed_retryable(monkeypatch):
    from src.jobs.cloud_sync_job import CloudSyncJob, handle_sync_job
    from src.jobs.contracts import FailureKind, JobFailure

    credential = SimpleNamespace(id="cred-1", provider="google", department_id="dept-1")
    folders = [
        SimpleNamespace(
            id="folder-1",
            folder_name="One",
            provider_folder_id="remote-1",
            sync_subfolders=True,
        ),
        SimpleNamespace(
            id="folder-2",
            folder_name="Two",
            provider_folder_id="remote-2",
            sync_subfolders=True,
        ),
    ]
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    folder_query = MagicMock()
    folder_query.filter.return_value.all.return_value = folders
    db = MagicMock()
    db.query.side_effect = lambda model: (
        credential_query if model.__name__ == "CloudOAuthCredentials" else folder_query
    )
    run = AsyncMock(
        side_effect=[
            {
                "files_discovered": 1,
                "files_updated": 0,
                "files_unchanged": 0,
                "scan_jobs_created": 1,
            },
            RuntimeError("provider unavailable"),
        ]
    )
    monkeypatch.setattr(CloudSyncJob, "run", run)
    job = SimpleNamespace(
        credential_id="cred-1",
        department_id="dept-1",
        provider="google",
        payload={
            "credential_id": "cred-1",
            "provider": "google",
            "folder_ids": ["folder-1", "folder-2"],
        },
        _assert_owned=AsyncMock(),
    )

    result = await handle_sync_job(job, db, MagicMock())

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.RETRYABLE
    assert result.code == "cloud_sync_partial_failure"
    assert result.details == {"processed": 1, "failed": 1}


@pytest.mark.asyncio
async def test_upload_rejects_paths_and_requires_managed_artifact_id(tmp_path):
    from src.jobs.contracts import FailureKind, JobFailure
    from src.jobs.upload_job import process_upload_job

    local = tmp_path / "unmanaged.docx"
    local.write_bytes(b"not-authorized")
    result = await process_upload_job(
        {
            "id": "job-1",
            "file_path": str(local),
            "cloud_file_id": "file-1",
            "department_id": "dept-1",
            "provider": "google",
        },
        MagicMock(),
    )

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.DETERMINISTIC
    assert result.code == "managed_artifact_id_required"


def test_handler_entrypoints_read_payload_and_never_terminalize_queue():
    from src.jobs.cloud_scan_job import handle_scan_job
    from src.jobs.cloud_sync_job import handle_sync_job
    from src.jobs.remediation_job import handle_remediation_job
    from src.jobs.upload_job import handle_upload_job

    handlers = (
        handle_scan_job,
        handle_sync_job,
        handle_remediation_job,
        handle_upload_job,
    )
    for handler in handlers:
        source = inspect.getsource(handler)
        assert "job.payload" in source
        assert "job.status =" not in source
        assert "job.completed_at =" not in source
