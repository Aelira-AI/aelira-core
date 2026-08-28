"""Task16A DB-first lock ordering and descriptor-bound use regressions."""

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pytest

from src.db.models import (
    CloudFile,
    RemediationArtifact,
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanStatus,
)
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
    from src.services.scan_fix_service import (
        artifact_approval_review_digest,
        review_digest_for,
    )

    accepted_fix = SimpleNamespace(
        issue_id="heading-1",
        occurrence_key="c" * 64,
        category="structure",
        severity="high",
        description="Heading level repaired",
        location="page 1",
        original_content="Heading",
        fixed_content="Heading",
        fix_method="automatic",
        provider_used=None,
        model_used=None,
        source_kind=None,
        source_locator=None,
        verification_evidence=None,
        visual_semantic_contract=None,
        confidence=1.0,
        needs_review=False,
        wcag_criteria=["1.3.1"],
        page_number=1,
        review_status="auto_approved",
    )
    accepted_fix.review_digest = review_digest_for(accepted_fix)
    accepted_fix.approved_review_digest = accepted_fix.review_digest
    if artifact.review_status == "approved" and artifact.approval_review_digest is None:
        artifact.approval_review_digest = artifact_approval_review_digest(
            artifact.sha256, [accepted_fix]
        )
    cloud = SimpleNamespace(
        id=CLOUD,
        department_id=DEPT,
        last_scan_id=SCAN,
        provider="canvas",
        current_remediation_artifact_id=artifact.id,
        has_remediated_version=True,
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(
            SimpleNamespace(id=DEPT),
            SimpleNamespace(
                id=SCAN,
                department_id=DEPT,
                scan_type=locked_scan_type,
                status=ScanStatus.COMPLETED,
                remediation_outcome=RemediationOutcome.COMPLETED.value,
            ),
            cloud,
            SimpleNamespace(id=JOB),
            artifact,
        )
    )
    db = MagicMock()
    db.accepted_fixes = [accepted_fix]

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_for_update.return_value = chain
        chain.populate_existing.return_value = chain
        if model is Scan:
            chain.one_or_none.return_value = SimpleNamespace(
                id=SCAN,
                status=ScanStatus.COMPLETED,
                remediation_outcome=RemediationOutcome.COMPLETED.value,
            )
        elif model is ScanFix:
            chain.all.return_value = db.accepted_fixes
        elif model is CloudFile:
            chain.one_or_none.return_value = cloud
        return chain

    db.query.side_effect = query
    return db, cloud


def test_approved_retry_rechecks_image_equation_human_review(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    original_query = db.query.side_effect
    forged = SimpleNamespace(
        source_kind="image_equation",
        review_status="auto_approved",
        reviewed_by=None,
        reviewed_at=None,
        verification_evidence={"passed": True},
    )

    def query(model):
        chain = original_query(model)
        if model is ScanFix:
            chain.all.return_value = [forged]
        return chain

    db.query.side_effect = query
    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        service.approve(
            db,
            artifact_id=artifact.id,
            approved_by_ref="admin@example.com",
        )
    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None


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


def test_approved_consumption_revalidates_fixes_and_invalidates_stale_approval(
    tmp_path,
):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, cloud = _db(service, artifact)
    original_query = db.query.side_effect

    def query(model):
        chain = original_query(model)
        if model is ScanFix:
            chain.all.return_value = [SimpleNamespace(review_status="pending")]
        return chain

    db.query.side_effect = query
    service._open_verified = MagicMock()

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        with service.open_verified(
            db,
            artifact,
            department_id=DEPT,
            scan_id=SCAN,
            cloud_file_id=CLOUD,
            require_approved=True,
        ):
            pass

    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approved_by_ref is None
    assert artifact.approved_at is None
    assert cloud.writeback_status == "pending_review"
    assert cloud.has_remediated_version is False
    service._open_verified.assert_not_called()
    db.flush.assert_called()


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


def test_old_local_artifact_is_rejected_when_scan_points_to_new_current(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    artifact.cloud_file_id = None
    artifact.remediation_job_id = None
    artifact.provider = "local"
    scan = SimpleNamespace(
        id=SCAN,
        department_id=DEPT,
        scan_type="WORD",
        current_remediation_artifact_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(SimpleNamespace(id=DEPT), scan, None, None, artifact)
    )

    with pytest.raises(ArtifactAuthorizationError, match="exact current output"):
        service.reject(
            MagicMock(), artifact_id=artifact.id, rejected_by_ref="reviewer@example.com"
        )


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


def test_mark_written_revalidates_fixes_and_invalidates_stale_approval(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, cloud = _db(service, artifact)
    original_query = db.query.side_effect

    def query(model):
        chain = original_query(model)
        if model is ScanFix:
            chain.all.return_value = [SimpleNamespace(review_status="rejected")]
        return chain

    db.query.side_effect = query

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        service.mark_written(
            db, artifact_id=artifact.id, provider_result={"revision": "42"}
        )

    assert artifact.written_back_at is None
    assert artifact.provider_result is None
    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert cloud.writeback_status == "pending_review"
    assert cloud.has_remediated_version is False
    db.flush.assert_called()


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
    assert artifact.approval_review_digest is not None
    assert artifact.approval_review_digest != artifact.approval_checksum
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


def test_approved_open_invalidates_mismatched_or_missing_review_binding(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, cloud = _db(service, artifact)
    artifact.approval_review_digest = "d" * 64

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        with service.open_verified(
            db,
            artifact,
            department_id=DEPT,
            scan_id=SCAN,
            cloud_file_id=CLOUD,
            require_approved=True,
        ):
            pass

    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None
    assert cloud.writeback_status == "pending_review"

    artifact.review_status = "approved"
    artifact.approval_checksum = artifact.sha256
    artifact.approved_by_ref = "admin@example.com"
    artifact.approved_at = datetime.now(timezone.utc)
    db.accepted_fixes[0].approved_review_digest = None
    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        with service.open_verified(
            db,
            artifact,
            department_id=DEPT,
            scan_id=SCAN,
            cloud_file_id=CLOUD,
            require_approved=True,
        ):
            pass
    assert artifact.approval_review_digest is None


def test_mark_written_invalidates_changed_accepted_fix_set(tmp_path):
    from copy import copy
    from src.services.scan_fix_service import review_digest_for

    service = _service(tmp_path)
    artifact = _artifact(service)
    db, cloud = _db(service, artifact)
    added = copy(db.accepted_fixes[0])
    added.occurrence_key = "e" * 64
    added.issue_id = "heading-2"
    added.review_digest = review_digest_for(added)
    added.approved_review_digest = added.review_digest
    db.accepted_fixes.append(added)

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        service.mark_written(db, artifact_id=artifact.id)

    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None
    assert cloud.writeback_status == "pending_review"


def test_approval_retry_requires_the_exact_current_accepted_fix_set(tmp_path):
    from copy import copy
    from src.services.scan_fix_service import review_digest_for

    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    added = copy(db.accepted_fixes[0])
    added.occurrence_key = "e" * 64
    added.issue_id = "heading-2"
    added.review_digest = review_digest_for(added)
    added.approved_review_digest = added.review_digest
    db.accepted_fixes.append(added)

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        service.approve(
            db,
            artifact_id=artifact.id,
            approved_by_ref="admin@example.com",
        )

    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None


def test_written_artifact_reports_stale_review_without_mutating_terminal_state(
    tmp_path,
):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    artifact.written_back_at = datetime.now(timezone.utc)
    artifact.approval_review_digest = "d" * 64
    durable = (
        artifact.review_status,
        artifact.approval_checksum,
        artifact.approval_review_digest,
        artifact.approved_by_ref,
        artifact.approved_at,
    )

    with pytest.raises(ArtifactAuthorizationError, match="approval became stale"):
        service.mark_written(db, artifact_id=artifact.id)

    assert (
        artifact.review_status,
        artifact.approval_checksum,
        artifact.approval_review_digest,
        artifact.approved_by_ref,
        artifact.approved_at,
    ) == durable


def test_forced_terminal_rejection_clears_both_approval_bindings(tmp_path):
    service = _service(tmp_path)
    artifact = _artifact(service)
    db, _ = _db(service, artifact)
    assert artifact.approval_review_digest is not None

    service._force_terminal_rejection(
        artifact,
        actor_ref="cleanup",
        now=datetime.now(timezone.utc),
    )

    assert artifact.review_status == "rejected"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None


def test_stale_invalidation_survives_request_rollback(monkeypatch, tmp_path):
    from sqlalchemy import JSON, Boolean, Column, DateTime, Float, String, create_engine
    from sqlalchemy.orm import Session, declarative_base

    from src.services import remediation_artifact_service as module
    from src.services.scan_fix_service import review_digest_for

    base = declarative_base()

    class DurableFix(base):
        __tablename__ = "durable_fixes"
        id = Column(String, primary_key=True)
        scan_id = Column(String, nullable=False)
        issue_id = Column(String)
        occurrence_key = Column(String)
        category = Column(String)
        severity = Column(String)
        description = Column(String)
        location = Column(String)
        original_content = Column(String)
        fixed_content = Column(String)
        fix_method = Column(String)
        provider_used = Column(String)
        model_used = Column(String)
        source_kind = Column(String)
        source_locator = Column(JSON)
        verification_evidence = Column(JSON)
        visual_semantic_contract = Column(JSON)
        confidence = Column(Float)
        needs_review = Column(Boolean)
        wcag_criteria = Column(JSON)
        page_number = Column(String)
        review_status = Column(String)
        review_digest = Column(String)
        approved_review_digest = Column(String)

    class DurableArtifact(base):
        __tablename__ = "durable_artifacts"
        id = Column(String, primary_key=True)
        scan_id = Column(String, nullable=False)
        review_status = Column(String)
        written_back_at = Column(DateTime(timezone=True))
        sha256 = Column(String)
        approval_checksum = Column(String)
        approval_review_digest = Column(String)
        approved_by_id = Column(String)
        approved_by_ref = Column(String)
        approved_at = Column(DateTime(timezone=True))

    class DurableCloud(base):
        __tablename__ = "durable_clouds"
        id = Column(String, primary_key=True)
        writeback_status = Column(String)
        has_remediated_version = Column(Boolean)
        remediation_origin = Column(String)

    class DurableAudit(base):
        __tablename__ = "durable_audits"
        id = Column(String, primary_key=True, default=lambda: "audit-1")
        scan_id = Column(String)
        user_id = Column(String)
        action = Column(String)
        details = Column(JSON)

    class CallerMutation(base):
        __tablename__ = "caller_mutations"
        id = Column(String, primary_key=True)
        value = Column(String)

    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    base.metadata.create_all(engine)
    fix = DurableFix(
        id="fix-1",
        scan_id=SCAN,
        issue_id="heading-1",
        occurrence_key="c" * 64,
        category="structure",
        severity="high",
        description="Heading level repaired",
        location="page 1",
        original_content="Heading",
        fixed_content="Heading",
        fix_method="automatic",
        confidence=1.0,
        needs_review=False,
        wcag_criteria=["1.3.1"],
        page_number="1",
        review_status="auto_approved",
    )
    fix.review_digest = review_digest_for(fix)
    fix.approved_review_digest = fix.review_digest
    artifact = DurableArtifact(
        id="artifact-1",
        scan_id=SCAN,
        review_status="approved",
        sha256="a" * 64,
        approval_checksum="a" * 64,
        approval_review_digest="d" * 64,
        approved_by_id="user-1",
        approved_by_ref="admin@example.com",
        approved_at=datetime.now(timezone.utc),
    )
    cloud = DurableCloud(
        id="cloud-1",
        writeback_status="approved",
        has_remediated_version=True,
        remediation_origin="manual",
    )
    monkeypatch.setattr(module, "ScanFix", DurableFix)
    monkeypatch.setattr(module, "ReviewAuditLog", DurableAudit)
    service = module.RemediationArtifactService.__new__(
        module.RemediationArtifactService
    )
    service._lock_existing_artifact = lambda session, _: (
        SimpleNamespace(id=DEPT),
        SimpleNamespace(
            id=SCAN,
            status=ScanStatus.COMPLETED,
            remediation_outcome=RemediationOutcome.COMPLETED.value,
        ),
        session.get(DurableCloud, "cloud-1"),
        None,
        session.get(DurableArtifact, "artifact-1"),
    )

    with Session(engine) as session:
        session.add_all([fix, artifact, cloud])
        session.commit()
        session.add(CallerMutation(id="must-rollback", value="partial writeback"))
        with pytest.raises(module.ArtifactApprovalStaleError):
            service._require_current_approval(
                session,
                artifact,
                SimpleNamespace(
                    status=ScanStatus.COMPLETED,
                    remediation_outcome=RemediationOutcome.COMPLETED.value,
                ),
                cloud,
            )
        session.rollback()

    with Session(engine) as session:
        durable_artifact = session.get(DurableArtifact, "artifact-1")
        durable_cloud = session.get(DurableCloud, "cloud-1")
        assert durable_artifact.review_status == "pending"
        assert durable_artifact.approval_checksum is None
        assert durable_artifact.approval_review_digest is None
        assert durable_artifact.approved_by_ref is None
        assert durable_cloud.writeback_status == "pending_review"
        assert durable_cloud.has_remediated_version is False
        assert session.query(DurableAudit).count() == 1
        assert session.get(CallerMutation, "must-rollback") is None


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
