"""Task16A durable, canonically locked artifact cleanup contracts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.db.models import RemediationArtifact
from src.services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactCleanup,
    RemediationArtifactService,
)


def _artifact(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        department_id="11111111-1111-4111-8111-111111111111",
        scan_id="22222222-2222-4222-8222-222222222222",
        cloud_file_id="33333333-3333-4333-8333-333333333333",
        remediation_job_id="44444444-4444-4444-8444-444444444444",
        provider="canvas",
        scan_type="WORD",
        storage_backend="local",
        storage_key=(
            "11111111-1111-4111-8111-111111111111/"
            "22222222-2222-4222-8222-222222222222/"
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.docx"
        ),
        filename="fixed.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=10,
        sha256="a" * 64,
        lifecycle_status="expired",
        review_status="rejected",
        publication_token=None,
        publication_heartbeat_at=None,
        published_at=None,
        written_back_at=None,
        cleanup_claimed_at=None,
        deleted_at=None,
        expires_at=now - timedelta(days=1),
        created_at=now - timedelta(days=31),
    )
    values.update(overrides)
    return RemediationArtifact(**values)


class RecordingQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []
        self.limit_value = None
        self.lock_kwargs = None

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def order_by(self, *_ordering):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def with_for_update(self, **kwargs):
        self.lock_kwargs = kwargs
        return self

    def all(self):
        return self.rows[: self.limit_value]


class LockingService:
    def __init__(self, artifact, events=None, removed=True):
        self.artifact = artifact
        self.events = events if events is not None else []
        self.removed = removed
        self.cloud = SimpleNamespace(
            current_remediation_artifact_id=artifact.id,
            has_remediated_version=True,
        )
        self.lock_calls = []

    def _lock_existing_artifact(self, _db, artifact_id, *, skip_locked=False):
        self.lock_calls.append((artifact_id, skip_locked))
        return (
            SimpleNamespace(id=self.artifact.department_id),
            SimpleNamespace(id=self.artifact.scan_id, scan_type="WORD"),
            self.cloud,
            SimpleNamespace(id=self.artifact.remediation_job_id),
            self.artifact,
        )

    def delete_known(self, artifact):
        self.events.append("delete")
        assert artifact is self.artifact
        return self.removed


def _cleanup(service, batch_size=25, grace=600):
    return RemediationArtifactCleanup(
        service=service, batch_size=batch_size, staging_grace_seconds=grace
    )


def _db(rows, events=None):
    query = RecordingQuery(rows)
    db = MagicMock()
    db.query.return_value = query
    if events is not None:
        db.commit.side_effect = lambda: events.append("commit")
    return db, query


def _compiled(criteria):
    return " AND ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in criteria
    )


def test_candidate_query_is_bounded_unlocked_and_heartbeat_based():
    db, query = _db([])
    result = _cleanup(LockingService(_artifact())).run_batch(
        db, now=datetime(2026, 8, 21, tzinfo=timezone.utc)
    )
    assert query.limit_value == 25
    assert query.lock_kwargs is None
    compiled = _compiled(query.criteria)
    assert "publication_heartbeat_at <=" in compiled
    assert "created_at <=" not in compiled
    assert result == {"claimed": 0, "deleted": 0, "missing": 0, "failed": 0}


def test_live_publisher_is_not_cleanup_eligible_but_stale_publisher_is():
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="staging",
        review_status="pending",
        publication_token="b" * 64,
        publication_heartbeat_at=now,
    )
    cutoff = now - timedelta(seconds=600)
    assert not RemediationArtifactCleanup._eligible_after_select(
        artifact, now=now, claim_cutoff=cutoff
    )
    artifact.publication_heartbeat_at = cutoff
    assert RemediationArtifactCleanup._eligible_after_select(
        artifact, now=now, claim_cutoff=cutoff
    )


@pytest.mark.parametrize("lifecycle", ["available", "expired", "superseded"])
def test_expired_pending_nonstaging_artifacts_are_claimed_and_deleted(lifecycle):
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status=lifecycle,
        review_status="pending",
        expires_at=now - timedelta(seconds=1),
    )
    service = LockingService(artifact)
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 1, "missing": 0, "failed": 0}
    assert artifact.lifecycle_status == "deleted"


def test_unexpired_pending_available_artifact_is_held():
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="available",
        review_status="pending",
        expires_at=now + timedelta(seconds=1),
    )
    service = LockingService(artifact)
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 0, "deleted": 0, "missing": 0, "failed": 0}
    assert artifact.lifecycle_status == "available"


def test_explicit_rejection_remains_eligible_under_terminal_lifecycle_policy():
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="expired",
        review_status="rejected",
        rejected_at=now - timedelta(seconds=1),
        rejected_by_ref="admin@example.com",
        expires_at=now + timedelta(days=30),
    )

    assert RemediationArtifactCleanup._eligible_after_select(
        artifact, now=now, claim_cutoff=now
    )


@pytest.mark.parametrize("lifecycle", ["available", "expired", "superseded"])
def test_unexpired_approved_unwritten_artifacts_are_held(lifecycle):
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status=lifecycle,
        review_status="approved",
        expires_at=now + timedelta(seconds=1),
    )
    assert not RemediationArtifactCleanup._eligible_after_select(
        artifact, now=now, claim_cutoff=now
    )


@pytest.mark.parametrize("lifecycle", ["available", "expired", "superseded"])
def test_expired_approved_unwritten_artifacts_are_selected_and_deleted(lifecycle):
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status=lifecycle,
        review_status="approved",
        approved_at=now - timedelta(days=30),
        approved_by_ref="admin@example.com",
        approval_checksum="a" * 64,
        expires_at=now,
    )
    service = LockingService(artifact)
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 1, "missing": 0, "failed": 0}
    db.delete.assert_called_once_with(artifact)


def test_candidate_query_holds_approved_unwritten_only_until_deadline():
    db, query = _db([])
    _cleanup(LockingService(_artifact()), batch_size=1).run_batch(
        db, now=datetime(2026, 8, 21, tzinfo=timezone.utc)
    )

    compiled = _compiled(query.criteria)
    assert "review_status != 'approved'" in compiled
    assert "written_back_at IS NOT NULL" in compiled
    assert "review_status = 'approved'" in compiled
    assert "written_back_at IS NULL" in compiled
    assert "expires_at <=" in compiled
    assert "review_status = 'rejected'" not in compiled
    assert query.limit_value == 1


def test_cleanup_claim_commits_before_delete_and_uses_canonical_skip_locked_lock():
    events = []
    artifact = _artifact()
    service = LockingService(artifact, events)
    db, _ = _db([artifact], events)
    now = datetime.now(timezone.utc)

    result = _cleanup(service).run_batch(db, now=now)

    assert events == ["commit", "delete", "commit"]
    assert service.lock_calls == [(artifact.id, True), (artifact.id, False)]
    assert artifact.lifecycle_status == "deleted"
    assert artifact.cleanup_claimed_at is None
    assert service.cloud.current_remediation_artifact_id is None
    db.delete.assert_called_once_with(artifact)
    assert result == {"claimed": 1, "deleted": 1, "missing": 0, "failed": 0}


def test_stale_publication_cleanup_claim_clears_lease_only_after_delete():
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="staging",
        review_status="pending",
        publication_token="b" * 64,
        publication_heartbeat_at=now - timedelta(hours=1),
    )
    service = LockingService(artifact, removed=False)
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 0, "missing": 1, "failed": 0}
    assert artifact.lifecycle_status == "deleted"
    assert artifact.publication_token is None
    assert artifact.publication_heartbeat_at is None


def test_stale_staging_cleanup_removes_crash_partial_and_finalizes_row(tmp_path):
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="staging",
        review_status="pending",
        publication_token="b" * 64,
        publication_heartbeat_at=now - timedelta(hours=1),
    )
    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024,
        retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=600,
    )
    partial = service.root / f"{artifact.storage_key}.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"simulated hard-crash bytes")
    cloud = SimpleNamespace(
        current_remediation_artifact_id=None, has_remediated_version=False
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(
            SimpleNamespace(id=artifact.department_id),
            SimpleNamespace(id=artifact.scan_id, scan_type="WORD"),
            cloud,
            SimpleNamespace(id=artifact.remediation_job_id),
            artifact,
        )
    )
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 1, "missing": 0, "failed": 0}
    assert artifact.lifecycle_status == "deleted"
    assert artifact.cleanup_claimed_at is None
    assert not partial.exists()
    assert not (service.root / artifact.storage_key).exists()


def test_stale_staging_cleanup_finalizes_when_publish_crashed_before_mkdir(tmp_path):
    now = datetime.now(timezone.utc)
    artifact = _artifact(
        lifecycle_status="staging",
        review_status="pending",
        publication_token="b" * 64,
        publication_heartbeat_at=now - timedelta(hours=1),
    )
    service = RemediationArtifactService(
        root=tmp_path / "never-created",
        max_bytes=1024,
        retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=600,
    )
    cloud = SimpleNamespace(
        current_remediation_artifact_id=None, has_remediated_version=False
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(
            SimpleNamespace(id=artifact.department_id),
            SimpleNamespace(id=artifact.scan_id, scan_type="WORD"),
            cloud,
            SimpleNamespace(id=artifact.remediation_job_id),
            artifact,
        )
    )
    db, _ = _db([artifact])

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 0, "missing": 1, "failed": 0}
    assert artifact.lifecycle_status == "deleted"
    assert artifact.cleanup_claimed_at is None


def test_delete_failure_keeps_durable_cleanup_claim_for_retry():
    artifact = _artifact()
    service = LockingService(artifact)
    service.delete_known = MagicMock(side_effect=OSError("disk unavailable"))
    db, _ = _db([artifact])
    now = datetime.now(timezone.utc)

    result = _cleanup(service).run_batch(db, now=now)

    assert result == {"claimed": 1, "deleted": 0, "missing": 0, "failed": 1}
    assert artifact.cleanup_claimed_at == now
    assert db.commit.call_count == 1


def test_cleanup_has_no_orphan_filesystem_scan_surface():
    names = set(RemediationArtifactCleanup.run_batch.__code__.co_names)
    assert not {"rglob", "walk", "glob", "iterdir"} & names


def test_publisher_owner_check_rejects_cleanup_claim():
    artifact = _artifact(
        lifecycle_status="staging",
        review_status="pending",
        publication_token="b" * 64,
        publication_heartbeat_at=datetime.now(timezone.utc),
        cleanup_claimed_at=datetime.now(timezone.utc),
    )
    from src.services.remediation_artifact_service import RemediationArtifactService

    with pytest.raises(ArtifactAuthorizationError, match="lease"):
        RemediationArtifactService._require_publication_owner(artifact, "b" * 64)
