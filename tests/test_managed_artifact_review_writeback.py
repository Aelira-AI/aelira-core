"""Task 16B2 managed artifact review/writeback lifecycle tests."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import hashlib
import uuid

import pytest

from src.db.models import (
    CloudFile,
    CloudOAuthCredentials,
    ContentWritebackLog,
    RemediationOutcome,
    Scan,
    ScanStatus,
)
from src.education.canvas_content_scanner import CanvasContentScanner


def _id() -> str:
    return str(uuid.uuid4())


class _ArtifactService:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.opened = False
        self.marked = False

    @contextmanager
    def open_verified(self, db, artifact, **authority):
        assert authority == {
            "department_id": artifact.department_id,
            "scan_id": artifact.scan_id,
            "cloud_file_id": artifact.cloud_file_id,
            "require_approved": True,
            "approval_checksum": artifact.sha256,
        }
        self.opened = True
        stream = BytesIO(self.payload)
        try:
            yield stream
            assert not stream.closed
        finally:
            stream.close()

    def mark_written(self, db, *, artifact_id, provider_result):
        self.marked = True
        return SimpleNamespace(id=artifact_id, provider_result=provider_result)


def _file_graph(payload=b"%PDF-1.7\n"):
    department_id, scan_id, cloud_id, artifact_id = (_id() for _ in range(4))
    digest = hashlib.sha256(payload).hexdigest()
    scan = SimpleNamespace(
        id=scan_id,
        department_id=department_id,
        status=ScanStatus.COMPLETED,
        remediation_outcome=RemediationOutcome.COMPLETED.value,
        scan_type="PDF",
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        department_id=department_id,
        scan_id=scan_id,
        cloud_file_id=cloud_id,
        provider="canvas",
        filename="syllabus_fixed.pdf",
        mime_type="application/pdf",
        size_bytes=len(payload),
        sha256=digest,
        approval_checksum=digest,
        approved_at=datetime.now(timezone.utc),
        review_status="approved",
        lifecycle_status="available",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        written_back_at=None,
    )
    cloud = CloudFile(
        id=cloud_id,
        department_id=department_id,
        last_scan_id=scan_id,
        current_remediation_artifact_id=artifact_id,
        credential_id=_id(),
        provider="canvas",
        content_source="file",
        file_name="syllabus.pdf",
        file_type="pdf",
        mime_type="application/pdf",
        provider_file_id="9001",
        provider_parent_id="101",
        provider_version="v1",
        provider_modified_at=None,
        writeback_status="approved",
        has_remediated_version=True,
        remediated_compliance_score=96.0,
        last_compliance_score=70.0,
        remediated_file_id=None,
        writeback_at=None,
    )
    return cloud, scan, artifact


def _persisted_canvas_authority(db, cloud):
    credential = CloudOAuthCredentials(
        id=cloud.credential_id,
        department_id=cloud.department_id,
        provider="canvas",
        access_token="encrypted-access-token",
        refresh_token="encrypted-refresh-token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        provider_metadata={"canvas_instance_url": "http://localhost:3000"},
        is_active=True,
    )

    def get(model, identity):
        if model is CloudOAuthCredentials and identity == credential.id:
            return credential
        if model is CloudFile and identity == cloud.id:
            return cloud
        return None

    db.get.side_effect = get
    return credential


def test_local_current_pointer_and_reconciliation_constraints_match_contract():
    pointer = Scan.__table__.c.current_remediation_artifact_id
    assert pointer.nullable is True
    assert pointer.foreign_keys
    assert next(iter(pointer.foreign_keys)).ondelete == "SET NULL"
    constraint_names = {
        constraint.name for constraint in ContentWritebackLog.__table__.constraints
    }
    assert {
        "ck_content_writeback_log_artifact_binding",
        "ck_content_writeback_log_reconciliation",
        "ck_content_writeback_log_correlation_id",
    } <= constraint_names


@pytest.mark.asyncio
async def test_canvas_writeback_consumes_verified_descriptor_and_records_artifact():
    cloud, scan, artifact = _file_graph()
    service = _ArtifactService(b"%PDF-1.7\n")
    client = MagicMock()
    client.upload_file_stream = AsyncMock(
        return_value=SimpleNamespace(
            success=True, file_id="77", web_view_link="https://canvas/files/77"
        )
    )
    db = MagicMock()
    scanner = CanvasContentScanner(
        canvas_client=client,
        db=db,
        department_id=cloud.department_id,
        credential_id=cloud.credential_id,
        artifact_service=service,
    )
    scanner._lock_file_writeback_graph = MagicMock(return_value=(cloud, scan, artifact))

    result = await scanner.write_back_file(cloud, approved_by="user-1")

    assert result["success"] is True
    assert service.opened and service.marked
    kwargs = client.upload_file_stream.await_args.kwargs
    assert kwargs["stream"].closed is True
    assert kwargs["file_name"] == "syllabus_accessible.pdf"
    assert kwargs["size_bytes"] == artifact.size_bytes
    added = [call.args[0] for call in db.add.call_args_list]
    assert added[-1].artifact_id == artifact.id
    assert added[-1].artifact_checksum == artifact.sha256
    assert "local:" not in added[-1].remediated_body
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_canvas_integrity_failure_makes_zero_outbound_calls():
    cloud, scan, artifact = _file_graph()
    service = _ArtifactService(b"tampered")

    @contextmanager
    def fail(*args, **kwargs):
        from src.services.remediation_artifact_service import ArtifactIntegrityError

        raise ArtifactIntegrityError("bad bytes")
        yield

    service.open_verified = fail
    client = MagicMock()
    client.upload_file_stream = AsyncMock()
    scanner = CanvasContentScanner(
        canvas_client=client,
        db=MagicMock(),
        department_id=cloud.department_id,
        credential_id=cloud.credential_id,
        artifact_service=service,
    )
    scanner._lock_file_writeback_graph = MagicMock(return_value=(cloud, scan, artifact))

    result = await scanner.write_back_file(cloud, approved_by="user-1")

    assert result["success"] is False
    assert result["error_code"] == "artifact_unavailable"
    client.upload_file_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_canvas_indeterminate_upload_persists_durable_reconciliation_log():
    cloud, scan, artifact = _file_graph()
    service = _ArtifactService(b"%PDF-1.7\n")
    client = MagicMock()
    client.upload_file_stream = AsyncMock(
        return_value=SimpleNamespace(
            success=False,
            outcome="indeterminate",
            correlation_id="55555555-5555-4555-8555-555555555555",
            provider_result={"phase": "upload"},
            error="Canvas upload outcome is indeterminate",
        )
    )
    db = MagicMock()
    _persisted_canvas_authority(db, cloud)
    scanner = CanvasContentScanner(
        canvas_client=client,
        db=db,
        department_id=cloud.department_id,
        credential_id=cloud.credential_id,
        artifact_service=service,
    )
    scanner._lock_file_writeback_graph = MagicMock(return_value=(cloud, scan, artifact))

    result = await scanner.write_back_file(cloud, approved_by="user-1")

    assert result["error_code"] == "writeback_reconciliation_required"
    assert result["retry_safe"] is False
    client.upload_file_stream.assert_awaited_once()
    assert service.marked is False
    required = [
        call.args[0]
        for call in db.add.call_args_list
        if getattr(call.args[0], "reconciliation_status", None)
        == "reconciliation_required"
    ]
    assert len(required) == 1
    assert required[0].artifact_id == artifact.id
    assert required[0].artifact_checksum == artifact.sha256
    assert required[0].approved_by == "user-1"
    db.rollback.assert_called()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_canvas_definite_rejection_does_not_create_reconciliation_log():
    cloud, scan, artifact = _file_graph()
    service = _ArtifactService(b"%PDF-1.7\n")
    client = MagicMock()
    client.upload_file_stream = AsyncMock(
        return_value=SimpleNamespace(
            success=False,
            outcome="definite_failure",
            correlation_id=None,
            provider_result={"phase": "upload", "status_code": 422},
            error="Canvas file upload failed",
        )
    )
    db = MagicMock()
    scanner = CanvasContentScanner(
        canvas_client=client,
        db=db,
        department_id=cloud.department_id,
        credential_id=cloud.credential_id,
        artifact_service=service,
    )
    scanner._lock_file_writeback_graph = MagicMock(return_value=(cloud, scan, artifact))

    result = await scanner.write_back_file(cloud, approved_by="user-1")

    assert result == {"success": False, "stale": False, "error": "Canvas upload failed"}
    client.upload_file_stream.assert_awaited_once()
    assert service.marked is False
    reconciliation_logs = [
        call.args[0]
        for call in db.add.call_args_list
        if getattr(call.args[0], "reconciliation_status", None)
        == "reconciliation_required"
    ]
    assert reconciliation_logs == []
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_success_then_database_failure_persists_reconciliation_separately():
    cloud, scan, artifact = _file_graph()
    service = _ArtifactService(b"%PDF-1.7\n")
    client = MagicMock()
    client.upload_file_stream = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            outcome="success",
            correlation_id="66666666-6666-4666-8666-666666666666",
            provider_result={"canvas_file_id": "77"},
            file_id="77",
            file_name="syllabus_accessible.pdf",
            web_view_link="https://canvas/files/77",
        )
    )
    db = MagicMock()
    db.commit.side_effect = [RuntimeError("commit lost"), None]
    _persisted_canvas_authority(db, cloud)
    scanner = CanvasContentScanner(
        canvas_client=client,
        db=db,
        department_id=cloud.department_id,
        credential_id=cloud.credential_id,
        artifact_service=service,
    )
    scanner._lock_file_writeback_graph = MagicMock(return_value=(cloud, scan, artifact))

    result = await scanner.write_back_file(cloud, approved_by="user-1")

    assert result["error_code"] == "writeback_reconciliation_required"
    required = [
        call.args[0]
        for call in db.add.call_args_list
        if getattr(call.args[0], "reconciliation_status", None)
        == "reconciliation_required"
    ]
    assert len(required) == 1
    assert db.commit.call_count == 2
