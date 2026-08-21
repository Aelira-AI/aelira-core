"""Task16B2 parent cleanup scoping and resumability regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db.models import CloudFile, CloudOAuthCredentials, Department, Scan, User
from src.services.account_deletion_service import AccountDeletionService
from src.services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)

DEPT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
OTHER_USER = "33333333-3333-4333-8333-333333333333"
SCAN = "44444444-4444-4444-8444-444444444444"
OTHER_SCAN = "55555555-5555-4555-8555-555555555555"
ARTIFACT = "66666666-6666-4666-8666-666666666666"


@pytest.mark.parametrize(
    "model",
    (Department, User, CloudOAuthCredentials, Scan, CloudFile),
)
def test_cleanup_fence_model_columns_are_bounded_paired_and_nullable(model):
    columns = model.__table__.columns
    assert columns.artifact_cleanup_token.nullable is True
    assert columns.artifact_cleanup_token.type.length == 64
    assert columns.artifact_cleanup_claimed_at.nullable is True
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    check = checks[f"ck_{model.__tablename__}_artifact_cleanup_fence"]
    assert "artifact_cleanup_token IS NULL" in check
    assert "artifact_cleanup_claimed_at IS NULL" in check


def test_parent_cleanup_transaction_hides_cryptographic_fence_token():
    fields = __import__(
        "src.services.remediation_artifact_service",
        fromlist=["ParentCleanupTransaction"],
    ).ParentCleanupTransaction.__dataclass_fields__
    assert fields["cleanup_token"].repr is False


def _service(tmp_path):
    return RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024,
        retention_days=30,
        approved_retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=600,
    )


def test_account_deletion_uses_user_scoped_artifact_cleanup():
    now = datetime.now(timezone.utc)
    user = SimpleNamespace(
        id=USER,
        email="departing@example.com",
        department_id=DEPT,
        deletion_scheduled_for=now - timedelta(seconds=1),
    )
    department = SimpleNamespace(id=DEPT, tier="team", owner_user_id=OTHER_USER)
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is User:
            chain.first.return_value = user
        elif model is Department:
            chain.first.return_value = department
        elif model is Scan:
            chain.all.return_value = []
        else:
            chain.first.return_value = None
        return chain

    db.query.side_effect = query
    cleanup = MagicMock()
    cleanup.cleanup_for_user.return_value.count = 0

    with (
        patch.object(RemediationArtifactService, "from_settings", return_value=cleanup),
        patch("src.services.account_deletion_service.AuditService"),
    ):
        assert AccountDeletionService().execute_scheduled_deletion(db, USER)

    cleanup.cleanup_for_user.assert_called_once_with(
        db,
        department_id=DEPT,
        user_id=USER,
        destructive_actor_ref="account_deletion",
    )
    cleanup.delete_for_department.assert_not_called()


def test_parent_cleanup_claim_is_committed_before_unlink_and_retry_resumes(tmp_path):
    service = _service(tmp_path)
    artifact = SimpleNamespace(
        id=ARTIFACT,
        department_id=DEPT,
        scan_id=SCAN,
        cloud_file_id=None,
        storage_backend="local",
        storage_key=f"{DEPT}/{SCAN}/{ARTIFACT}/77777777-7777-4777-8777-777777777777.docx",
        lifecycle_status="available",
        review_status="pending",
        written_back_at=None,
        cleanup_claimed_at=None,
        cleanup_reason=None,
        cleanup_owner=None,
    )
    events = []
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.commit.side_effect = lambda: events.append("claim_commit")
    service._claim_parent_artifacts = MagicMock(
        return_value=([artifact], datetime.now(timezone.utc))
    )
    service._stage_claimed_parent_cleanup = MagicMock()
    service.delete_known = MagicMock(
        side_effect=lambda _artifact: events.append("unlink") or True
    )

    cleanup = service._prepare_parent_cleanup(
        db,
        artifacts=[artifact],
        reason="scan_delete",
        owner=SCAN,
        force=False,
    )

    assert events[:2] == ["claim_commit", "unlink"]
    assert cleanup.artifact_ids == (ARTIFACT,)
    assert cleanup.count == 1
    assert cleanup.cleanup_token
    assert len(cleanup.cleanup_token) >= 43
    service._stage_claimed_parent_cleanup.assert_called_once()


def test_parent_cleanup_preauthorizes_every_artifact_before_unlink(tmp_path):
    service = _service(tmp_path)
    approved = SimpleNamespace(
        id=ARTIFACT,
        review_status="approved",
        written_back_at=None,
        cleanup_claimed_at=None,
        cleanup_reason=None,
        cleanup_owner=None,
    )
    service.delete_known = MagicMock()
    db = MagicMock()

    with pytest.raises(ArtifactAuthorizationError, match="artifact_cleanup_required"):
        service._prepare_parent_cleanup(
            db,
            artifacts=[approved],
            reason="provider_disconnect",
            owner=DEPT,
            force=False,
        )

    service.delete_known.assert_not_called()
    db.commit.assert_not_called()


def test_cleanup_for_user_query_scopes_scans_by_user_and_department(tmp_path):
    service = _service(tmp_path)
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.order_by.return_value = scan_query
    scan_query.limit.return_value = scan_query
    scan_query.all.return_value = []
    db = MagicMock()
    db.query.return_value = scan_query
    service._locked = MagicMock(
        side_effect=lambda _db, model, identity, _label: SimpleNamespace(
            id=identity,
            department_id=DEPT if model is User else None,
            artifact_cleanup_token=None,
            artifact_cleanup_claimed_at=None,
        )
    )

    cleanup = service.cleanup_for_user(
        db,
        department_id=DEPT,
        user_id=USER,
        destructive_actor_ref="account_deletion",
    )

    assert cleanup.count == 0
    compiled = " ".join(str(call) for call in scan_query.filter.call_args.args)
    assert "scans.user_id" in compiled
    assert "scans.department_id" in compiled
