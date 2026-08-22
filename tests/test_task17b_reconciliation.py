"""Task17B durable Canvas writeback reconciliation tests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _graph(*, attempts: int = 0):
    department = "11111111-1111-4111-8111-111111111111"
    credential_id = "22222222-2222-4222-8222-222222222222"
    cloud_id = "33333333-3333-4333-8333-333333333333"
    artifact_id = "44444444-4444-4444-8444-444444444444"
    correlation = "55555555-5555-4555-8555-555555555555"
    checksum = "a" * 64
    provider_result = {
        "correlation_id": correlation,
        "credential_id": credential_id,
        "canvas_origin": "https://canvas.instructure.com",
        "course_id": "course-7",
        "source_file_id": "file-3",
        "canvas_file_id": "canvas-99",
        "expected_file_name": "lecture_accessible.pdf",
        "artifact_checksum": checksum,
    }
    log = SimpleNamespace(
        id="66666666-6666-4666-8666-666666666666",
        cloud_file_id=cloud_id,
        artifact_id=artifact_id,
        artifact_checksum=checksum,
        correlation_id=correlation,
        reconciliation_status="reconciliation_required",
        provider_result=provider_result,
        reconciliation_attempt_count=attempts,
        reconciliation_lease_token=None,
        reconciliation_leased_at=None,
        reconciliation_lease_expires_at=None,
        reconciliation_next_attempt_at=None,
        reconciliation_last_error=None,
        reconciliation_resolved_at=None,
        reconciliation_resolution=None,
        written_back_at=None,
        canvas_revision=None,
    )
    cloud = SimpleNamespace(
        id=cloud_id,
        department_id=department,
        credential_id=credential_id,
        provider="canvas",
        provider_file_id="file-3",
        provider_parent_id="course-7",
        remediated_file_id=None,
        writeback_status="approved",
        writeback_at=None,
        remediated_compliance_score=91.0,
        last_compliance_score=70.0,
    )
    credential = SimpleNamespace(
        id=credential_id,
        department_id=department,
        provider="canvas",
        is_active=True,
        provider_metadata={"canvas_instance_url": "https://canvas.instructure.com"},
    )
    artifact = SimpleNamespace(id=artifact_id, sha256=checksum)
    return department, log, cloud, credential, artifact


class FakeDB:
    def __init__(self, log, cloud, credential, artifact):
        self.objects = {
            "ContentWritebackLog": {log.id: log},
            "CloudFile": {cloud.id: cloud},
            "CloudOAuthCredentials": {credential.id: credential},
            "RemediationArtifact": {artifact.id: artifact},
        }
        self.commits = 0

    def get(self, model, identifier, **_kwargs):
        return self.objects.get(model.__name__, {}).get(identifier)

    def commit(self):
        self.commits += 1

    def flush(self):
        return None

    def rollback(self):
        return None


@pytest.mark.asyncio
async def test_reconciliation_observer_fences_a_known_provider_file_id():
    from src.services.canvas_reconciliation_service import (
        CanvasReconciliationObserver,
    )

    content = b"verified artifact bytes"
    checksum = hashlib.sha256(content).hexdigest()
    candidates = [
        SimpleNamespace(
            id="wrong-id",
            filename="lecture_accessible.pdf",
            display_name="lecture_accessible.pdf",
            updated_at=datetime.now(timezone.utc),
        ),
        SimpleNamespace(
            id="canvas-99",
            filename="lecture_accessible.pdf",
            display_name="lecture_accessible.pdf",
            updated_at=datetime.now(timezone.utc),
        ),
    ]

    async def download(file_id, destination):
        assert file_id == "canvas-99"
        Path(destination).write_bytes(content)
        return SimpleNamespace(success=True)

    client = SimpleNamespace(
        list_course_files=AsyncMock(return_value=candidates),
        download_file=AsyncMock(side_effect=download),
    )
    result = await CanvasReconciliationObserver(client).observe_exact(
        course_id="course-7",
        source_file_id="file-3",
        candidate_file_id="canvas-99",
        expected_file_name="lecture_accessible.pdf",
        artifact_checksum=checksum,
        correlation_id="55555555-5555-4555-8555-555555555555",
    )

    assert result.outcome == "confirmed"
    assert result.file_id == "canvas-99"


@pytest.mark.asyncio
async def test_reconciliation_confirmed_marks_log_artifact_and_cloud_atomically():
    from src.services.canvas_reconciliation_service import (
        CanvasObservation,
        CanvasReconciliationService,
    )

    department, log, cloud, credential, artifact = _graph()
    db = FakeDB(log, cloud, credential, artifact)
    observer = SimpleNamespace(
        observe_exact=AsyncMock(
            return_value=CanvasObservation(
                outcome="confirmed",
                file_id="canvas-99",
                version="v99",
                checksum="a" * 64,
            )
        ),
        upload_file_stream=AsyncMock(),
    )
    artifact_service = MagicMock()
    service = CanvasReconciliationService(
        observer=observer, artifact_service=artifact_service
    )

    result = await service.handle_job(
        db,
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )

    assert result["success"] is True
    assert result["resolution"] == "confirmed"
    assert log.reconciliation_status == "reconciled"
    assert log.canvas_revision == "v99"
    assert cloud.remediated_file_id == "canvas-99"
    assert cloud.writeback_status == "written_back"
    artifact_service.mark_written.assert_called_once()
    observer.upload_file_stream.assert_not_called()
    assert observer.observe_exact.await_args.kwargs["candidate_file_id"] == "canvas-99"


@pytest.mark.asyncio
async def test_reconciliation_confirmed_absent_resolves_for_user_without_upload():
    from src.services.canvas_reconciliation_service import (
        CanvasObservation,
        CanvasReconciliationService,
    )

    department, log, cloud, credential, artifact = _graph()
    observer = SimpleNamespace(
        observe_exact=AsyncMock(return_value=CanvasObservation(outcome="absent")),
        upload_file_stream=AsyncMock(),
    )
    service = CanvasReconciliationService(
        observer=observer, artifact_service=MagicMock()
    )

    result = await service.handle_job(
        FakeDB(log, cloud, credential, artifact),
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )

    assert result == {
        "success": True,
        "resolution": "failed_manual",
        "retry_safe": True,
    }
    assert log.reconciliation_status == "failed_manual"
    assert cloud.writeback_status == "reconciliation_failed"
    observer.upload_file_stream.assert_not_called()


@pytest.mark.asyncio
async def test_reconciliation_indeterminate_is_bounded_then_manual():
    from src.jobs.contracts import FailureKind, JobFailure
    from src.services.canvas_reconciliation_service import (
        CanvasObservation,
        CanvasReconciliationService,
    )

    department, log, cloud, credential, artifact = _graph(attempts=1)
    observer = SimpleNamespace(
        observe_exact=AsyncMock(return_value=CanvasObservation(outcome="indeterminate"))
    )
    service = CanvasReconciliationService(
        observer=observer, artifact_service=MagicMock(), max_attempts=3
    )
    db = FakeDB(log, cloud, credential, artifact)

    retry = await service.handle_job(
        db,
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )
    assert isinstance(retry, JobFailure)
    assert retry.kind is FailureKind.RETRYABLE
    assert log.reconciliation_status == "reconciliation_required"

    manual = await CanvasReconciliationService(
        observer=observer, artifact_service=MagicMock(), max_attempts=3
    ).handle_job(
        db,
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )
    assert manual["resolution"] == "manual_required"
    assert log.reconciliation_status == "manual_required"


@pytest.mark.asyncio
async def test_reconciliation_survives_restart_and_never_reuploads():
    from src.services.canvas_reconciliation_service import (
        CanvasObservation,
        CanvasReconciliationService,
    )

    department, log, cloud, credential, artifact = _graph()
    db = FakeDB(log, cloud, credential, artifact)
    first_observer = SimpleNamespace(
        observe_exact=AsyncMock(
            return_value=CanvasObservation(outcome="indeterminate")
        ),
        upload_file_stream=AsyncMock(),
    )
    await CanvasReconciliationService(
        observer=first_observer, artifact_service=MagicMock()
    ).handle_job(
        db,
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )
    second_observer = SimpleNamespace(
        observe_exact=AsyncMock(
            return_value=CanvasObservation(
                outcome="confirmed",
                file_id="canvas-99",
                version="v99",
                checksum="a" * 64,
            )
        ),
        upload_file_stream=AsyncMock(),
    )
    result = await CanvasReconciliationService(
        observer=second_observer, artifact_service=MagicMock()
    ).handle_job(
        db,
        payload={"writeback_log_id": log.id},
        department_id=department,
        token_manager=MagicMock(),
    )

    assert result["resolution"] == "confirmed"
    first_observer.upload_file_stream.assert_not_called()
    second_observer.upload_file_stream.assert_not_called()


def test_reconciliation_creation_and_backfill_are_durable():
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.services.canvas_reconciliation_service import CanvasReconciliationService

    creation_source = inspect.getsource(
        CanvasContentScanner._persist_file_reconciliation
    )
    backfill_source = inspect.getsource(CanvasReconciliationService.backfill)
    assert "enqueue_cloud_job" in creation_source
    assert "with_for_update(skip_locked=True)" in backfill_source
    assert "enqueue_cloud_job" in backfill_source
