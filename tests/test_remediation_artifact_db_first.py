"""Task16A DB-first lock ordering and descriptor-bound use regressions."""

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pytest

from src.db.models import RemediationArtifact
from src.services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    ArtifactIntegrityError,
    RemediationArtifactService,
)

DEPT = "11111111-1111-4111-8111-111111111111"
SCAN = "22222222-2222-4222-8222-222222222222"
CLOUD = "33333333-3333-4333-8333-333333333333"
JOB = "44444444-4444-4444-8444-444444444444"
TOKEN = "b" * 64


def _service(tmp_path):
    return RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024 * 1024,
        retention_days=30,
        approved_retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=3600,
    )


def _artifact(service, *, lifecycle="available", scan_type="WORD"):
    artifact_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    key = f"{DEPT}/{SCAN}/{artifact_id}/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.docx"
    path = service.root / key
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    payload = path.read_bytes()
    staging = lifecycle == "staging"
    return RemediationArtifact(
        id=artifact_id,
        department_id=DEPT,
        scan_id=SCAN,
        cloud_file_id=CLOUD,
        remediation_job_id=JOB,
        provider="canvas",
        scan_type=scan_type,
        publication_token=TOKEN if staging else None,
        publication_heartbeat_at=datetime.now(timezone.utc) if staging else None,
        published_at=datetime.now(timezone.utc) if staging else None,
        storage_backend="local",
        storage_key=key,
        filename="fixed.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        lifecycle_status=lifecycle,
        review_status="approved",
        approval_checksum=hashlib.sha256(payload).hexdigest(),
        approved_by_ref="admin@example.com",
        approved_at=datetime.now(timezone.utc),
        cleanup_claimed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )


def _db(service, artifact, *, locked_scan_type="WORD"):
    cloud = SimpleNamespace(
        id=CLOUD,
        department_id=DEPT,
        last_scan_id=SCAN,
        provider="canvas",
        current_remediation_artifact_id=None,
        has_remediated_version=False,
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(
            SimpleNamespace(id=DEPT),
            SimpleNamespace(id=SCAN, department_id=DEPT, scan_type=locked_scan_type),
            cloud,
            SimpleNamespace(id=JOB),
            artifact,
        )
    )
    return MagicMock(), cloud


def test_open_verified_yields_descriptor_bound_stream_after_canonical_lock(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)

    with service.open_verified(
        db,
        artifact,
        department_id=DEPT,
        scan_id=SCAN,
        cloud_file_id=CLOUD,
        require_approved=True,
    ) as stream:
        assert not isinstance(stream, Path)
        assert stream.read(4) == b"PK\x03\x04"

    assert stream.closed
    service._lock_existing_artifact.assert_called_once_with(db, artifact.id)


def test_open_verified_rejects_current_scan_type_mutation(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact, locked_scan_type="PDF")

    with pytest.raises(
        ArtifactAuthorizationError, match="scan type authority mismatch"
    ):
        with service.open_verified(
            db, artifact, department_id=DEPT, scan_id=SCAN, cloud_file_id=CLOUD
        ):
            pass


def test_state_mutations_reject_durable_cleanup_claim(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.cleanup_claimed_at = datetime.now(timezone.utc)
    db, _ = _db(service, artifact)

    for action in (
        lambda: service.approve(db, artifact_id=artifact.id, approved_by_ref="admin"),
        lambda: service.reject(db, artifact_id=artifact.id, rejected_by_ref="admin"),
        lambda: service.mark_written(db, artifact_id=artifact.id),
    ):
        with pytest.raises(ArtifactAuthorizationError, match="cleanup"):
            action()


def test_mark_written_uses_written_retention_setting(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)

    service.mark_written(
        db,
        artifact_id=artifact.id,
        provider_result={"revision": "42", "ok": True},
        now=now,
    )

    assert artifact.written_back_at == now
    assert artifact.expires_at == now + timedelta(days=7)
    assert artifact.provider_result == {"revision": "42", "ok": True}


def test_mark_written_retry_is_semantic_noop_without_retention_extension(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    first = datetime(2026, 8, 21, tzinfo=timezone.utc)
    original_result = {"revision": "42", "meta": {"b": 2, "a": 1}}

    service.mark_written(
        db, artifact_id=artifact.id, provider_result=original_result, now=first
    )
    first_expiry = artifact.expires_at
    service.mark_written(
        db,
        artifact_id=artifact.id,
        provider_result={"meta": {"a": 1, "b": 2}, "revision": "42"},
        now=first + timedelta(days=3),
    )

    assert artifact.written_back_at == first
    assert artifact.expires_at == first_expiry
    assert artifact.provider_result == original_result


def test_mark_written_matching_retry_after_expiry_fails_without_mutation(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    first = datetime(2026, 8, 21, tzinfo=timezone.utc)
    result = {"revision": "42"}

    service.mark_written(db, artifact_id=artifact.id, provider_result=result, now=first)
    durable_state = (
        artifact.written_back_at,
        artifact.expires_at,
        artifact.provider_result,
        artifact.lifecycle_status,
        artifact.review_status,
    )
    flush_count = db.flush.call_count

    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.mark_written(
            db,
            artifact_id=artifact.id,
            provider_result={"revision": "42"},
            now=first + timedelta(days=7),
        )

    assert (
        artifact.written_back_at,
        artifact.expires_at,
        artifact.provider_result,
        artifact.lifecycle_status,
        artifact.review_status,
    ) == durable_state
    assert db.flush.call_count == flush_count


def test_mark_written_retry_with_contradictory_result_is_stable_conflict(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    first = datetime(2026, 8, 21, tzinfo=timezone.utc)
    service.mark_written(
        db, artifact_id=artifact.id, provider_result={"revision": "42"}, now=first
    )

    with pytest.raises(ArtifactAuthorizationError, match="conflicts"):
        service.mark_written(
            db,
            artifact_id=artifact.id,
            provider_result={"revision": "43"},
            now=first + timedelta(days=1),
        )

    assert artifact.written_back_at == first
    assert artifact.expires_at == first + timedelta(days=7)


def test_expired_unwritten_artifact_cannot_be_written_or_approved(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.expires_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    db, _ = _db(service, artifact)
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)

    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.mark_written(
            db, artifact_id=artifact.id, provider_result={"revision": "42"}, now=now
        )
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.approve(
            db, artifact_id=artifact.id, approved_by_ref="admin@example.com", now=now
        )


def test_approve_sets_writeback_deadline_and_retry_preserves_original_expiry(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_id = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    artifact.provider_result = {"remediation_snapshot": {"issues_fixed": 3}}
    first = datetime.now(timezone.utc)
    artifact.expires_at = first + timedelta(hours=1)
    db, _ = _db(service, artifact)

    service.approve(
        db,
        artifact_id=artifact.id,
        approved_by_id="user-1",
        approved_by_ref="admin@example.com",
        now=first,
    )
    service.approve(
        db,
        artifact_id=artifact.id,
        approved_by_id="user-1",
        approved_by_ref="admin@example.com",
        now=first + timedelta(days=1),
    )

    assert artifact.approved_at == first
    assert artifact.approval_checksum == artifact.sha256
    assert artifact.expires_at == first + timedelta(days=30)
    assert artifact.provider_result == {"remediation_snapshot": {"issues_fixed": 3}}


def test_mark_written_is_allowed_before_approval_deadline_and_rejected_at_it(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    deadline = datetime(2026, 9, 20, tzinfo=timezone.utc)
    artifact.expires_at = deadline
    db, _ = _db(service, artifact)

    service.mark_written(
        db,
        artifact_id=artifact.id,
        provider_result={"revision": "before"},
        now=deadline - timedelta(microseconds=1),
    )
    assert artifact.written_back_at == deadline - timedelta(microseconds=1)

    artifact.written_back_at = None
    artifact.provider_result = None
    artifact.expires_at = deadline
    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.mark_written(
            db,
            artifact_id=artifact.id,
            provider_result={"revision": "at"},
            now=deadline,
        )


def test_approve_matching_retry_after_expiry_fails_without_mutation(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_id = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    first = artifact.expires_at - timedelta(hours=1)
    db, _ = _db(service, artifact)

    service.approve(
        db,
        artifact_id=artifact.id,
        approved_by_id="user-1",
        approved_by_ref="admin@example.com",
        now=first,
    )
    durable_state = (
        artifact.approved_at,
        artifact.expires_at,
        artifact.review_status,
        artifact.approval_checksum,
    )
    approval_deadline = artifact.expires_at
    flush_count = db.flush.call_count

    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.approve(
            db,
            artifact_id=artifact.id,
            approved_by_id="user-1",
            approved_by_ref="admin@example.com",
            now=approval_deadline,
        )

    assert (
        artifact.approved_at,
        artifact.expires_at,
        artifact.review_status,
        artifact.approval_checksum,
    ) == durable_state
    assert db.flush.call_count == flush_count


def test_approve_retry_different_actor_or_checksum_conflicts(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)

    with pytest.raises(ArtifactAuthorizationError, match="conflicts"):
        service.approve(
            db, artifact_id=artifact.id, approved_by_ref="other@example.com"
        )
    artifact.approved_by_ref = "admin@example.com"
    artifact.approval_checksum = "0" * 64
    with pytest.raises(ArtifactAuthorizationError, match="conflicts"):
        service.approve(
            db, artifact_id=artifact.id, approved_by_ref="admin@example.com"
        )


def test_reject_retry_same_actor_preserves_timestamp_and_conflicts_other_actor(
    tmp_path,
):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    db, _ = _db(service, artifact)
    first = datetime(2026, 8, 21, tzinfo=timezone.utc)

    service.reject(
        db,
        artifact_id=artifact.id,
        rejected_by_id="user-1",
        rejected_by_ref="admin@example.com",
        now=first,
    )
    service.reject(
        db,
        artifact_id=artifact.id,
        rejected_by_id="user-1",
        rejected_by_ref="admin@example.com",
        now=first + timedelta(days=1),
    )
    assert artifact.rejected_at == first

    with pytest.raises(ArtifactAuthorizationError, match="conflicts"):
        service.reject(
            db,
            artifact_id=artifact.id,
            rejected_by_id="user-2",
            rejected_by_ref="other@example.com",
        )


def test_reject_matching_retry_after_expiry_fails_without_mutation(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    first = artifact.expires_at - timedelta(hours=1)
    db, _ = _db(service, artifact)

    service.reject(
        db,
        artifact_id=artifact.id,
        rejected_by_id="user-1",
        rejected_by_ref="admin@example.com",
        now=first,
    )
    durable_state = (
        artifact.rejected_at,
        artifact.expires_at,
        artifact.review_status,
        artifact.rejected_by_id,
        artifact.rejected_by_ref,
    )
    flush_count = db.flush.call_count

    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.reject(
            db,
            artifact_id=artifact.id,
            rejected_by_id="user-1",
            rejected_by_ref="admin@example.com",
            now=first + timedelta(hours=1),
        )

    assert (
        artifact.rejected_at,
        artifact.expires_at,
        artifact.review_status,
        artifact.rejected_by_id,
        artifact.rejected_by_ref,
    ) == durable_state
    assert db.flush.call_count == flush_count


def test_reject_fails_closed_for_approved_or_expired_pending_artifact(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)

    with pytest.raises(ArtifactAuthorizationError, match="cannot be rejected"):
        service.reject(db, artifact_id=artifact.id, rejected_by_ref="admin")

    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    artifact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ArtifactAuthorizationError, match="expired"):
        service.reject(db, artifact_id=artifact.id, rejected_by_ref="admin")


def test_finalize_requires_token_published_marker_and_canonical_lock(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service, lifecycle="staging")
    artifact.review_status = "pending"
    artifact.approval_checksum = None
    artifact.approved_by_ref = None
    artifact.approved_at = None
    db, cloud = _db(service, artifact)

    result = service.finalize(db, artifact_id=artifact.id, publication_token=TOKEN)

    assert result.lifecycle_status == "available"
    assert result.publication_token is None
    assert result.publication_heartbeat_at is None
    assert cloud.current_remediation_artifact_id == artifact.id


def test_publish_refuses_preexisting_final_name_under_artifact_lock(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service, lifecycle="staging")
    db, _ = _db(service, artifact)
    source_root = tmp_path / "trusted"
    source_root.mkdir()
    source = source_root / "fixed.docx"
    source.write_bytes((service.root / artifact.storage_key).read_bytes())

    with pytest.raises(ArtifactIntegrityError, match="already exists"):
        service.publish_claimed(
            db,
            artifact,
            publication_token=TOKEN,
            source_path=source,
            trusted_temp_root=source_root,
        )


def test_publication_is_fd_relative_atomic_no_overwrite():
    source = inspect.getsource(RemediationArtifactService)
    assert "os.link(" in source
    assert "src_dir_fd=" in source
    assert "dst_dir_fd=" in source
    assert "os.replace(" not in source
