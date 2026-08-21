"""Task16A concurrency, publication lease, and scan-authority contracts."""

from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.db import models
from src.services import remediation_artifact_service as module

DEPARTMENT_ID = "11111111-1111-4111-8111-111111111111"
SCAN_ID = "22222222-2222-4222-8222-222222222222"
CLOUD_FILE_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
ARTIFACT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TOKEN = "b" * 64
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _artifact(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id=ARTIFACT_ID,
        department_id=DEPARTMENT_ID,
        scan_id=SCAN_ID,
        cloud_file_id=CLOUD_FILE_ID,
        remediation_job_id=JOB_ID,
        provider="canvas",
        scan_type="WORD",
        storage_backend="local",
        storage_key=(
            f"{DEPARTMENT_ID}/{SCAN_ID}/{ARTIFACT_ID}/"
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc.docx"
        ),
        filename="fixed.docx",
        mime_type=MIME,
        size_bytes=1,
        sha256="a" * 64,
        lifecycle_status="staging",
        review_status="pending",
        publication_token=TOKEN,
        publication_heartbeat_at=now,
        published_at=None,
        cleanup_claimed_at=None,
        expires_at=now + timedelta(days=1),
        created_at=now,
        updated_at=now,
    )
    values.update(overrides)
    return models.RemediationArtifact(**values)


def test_model_persists_publication_lease_and_canonical_scan_type():
    columns = models.RemediationArtifact.__table__.c
    assert columns.scan_type.nullable is False
    assert columns.scan_type.type.length == 32
    assert columns.publication_token.nullable is True
    assert columns.publication_token.type.length == 64
    assert columns.publication_token.unique is True
    assert columns.publication_heartbeat_at.nullable is True
    assert columns.published_at.nullable is True
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in models.RemediationArtifact.__table__.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert "ck_remediation_artifacts_scan_type" in checks
    assert "WORD" in checks["ck_remediation_artifacts_scan_type"]
    assert "ck_remediation_artifacts_publication_lease" in checks


def test_claim_result_carries_secret_token_without_artifact_repr_contract():
    prepared_fields = module.PreparedRemediationArtifact.__dataclass_fields__
    assert prepared_fields["publication_token"].repr is False
    fields = module.ArtifactClaim.__dataclass_fields__
    assert "publication_token" in fields
    assert fields["publication_token"].repr is False


def test_lock_order_static_guard_has_no_artifact_then_parent_path():
    source = inspect.getsource(module)
    assert "_lock_authority_order" in source
    assert (
        "LOCK_ORDER = (Department, Scan, CloudFile, CloudJobQueue, RemediationArtifact)"
        in source
    )
    # Existing-artifact paths must discover immutable IDs without FOR UPDATE,
    # then delegate all locks to the one canonical helper.
    for method_name in (
        "finalize",
        "approve",
        "reject",
        "mark_written",
        "open_verified",
    ):
        method_source = inspect.getsource(
            getattr(module.RemediationArtifactService, method_name)
        )
        assert any(
            helper in method_source
            for helper in (
                "_artifact_metadata",
                "_lock_existing_artifact",
                "_lock_mutable",
            )
        )
        assert "_lock_and_validate_parents" not in method_source


def test_cleanup_candidate_selection_is_unlocked_and_heartbeat_based():
    source = inspect.getsource(module.RemediationArtifactCleanup.run_batch)
    candidate_source = source.split("for candidate", 1)[0]
    assert "with_for_update" not in candidate_source
    assert "publication_heartbeat_at" in candidate_source
    assert "created_at <= claim_cutoff" not in candidate_source
    assert "skip_locked=True" in source
    assert "_lock_existing_artifact" in source


def test_finalize_requires_exact_publication_token_and_published_at(tmp_path):
    service = module.RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024,
        retention_days=30,
        staging_grace_seconds=60,
    )
    artifact = _artifact(published_at=datetime.now(timezone.utc))
    service._lock_existing_artifact = MagicMock(
        return_value=(
            SimpleNamespace(id=DEPARTMENT_ID),
            SimpleNamespace(id=SCAN_ID, department_id=DEPARTMENT_ID, scan_type="WORD"),
            SimpleNamespace(
                id=CLOUD_FILE_ID,
                department_id=DEPARTMENT_ID,
                last_scan_id=SCAN_ID,
                provider="canvas",
                current_remediation_artifact_id=None,
                has_remediated_version=False,
            ),
            SimpleNamespace(
                id=JOB_ID,
                department_id=DEPARTMENT_ID,
                cloud_file_id=CLOUD_FILE_ID,
                job_type="remediate",
                provider="canvas",
                execution_context={"scan_id": SCAN_ID},
            ),
            artifact,
        )
    )
    service._open_verified = MagicMock()
    service._open_verified.return_value.__enter__.return_value = MagicMock()

    with pytest.raises(module.ArtifactAuthorizationError, match="publication token"):
        service.finalize(
            db=MagicMock(), artifact_id=ARTIFACT_ID, publication_token="c" * 64
        )

    artifact.published_at = None
    with pytest.raises(module.ArtifactAuthorizationError, match="not published"):
        service.finalize(
            db=MagicMock(), artifact_id=ARTIFACT_ID, publication_token=TOKEN
        )

    artifact.published_at = datetime.now(timezone.utc)
    result = service.finalize(
        db=MagicMock(), artifact_id=ARTIFACT_ID, publication_token=TOKEN
    )
    assert result.lifecycle_status == "available"
    assert result.publication_token is None
    assert result.publication_heartbeat_at is None


def test_scan_type_mutation_is_stable_authority_mismatch(tmp_path):
    service = module.RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024,
        retention_days=30,
        staging_grace_seconds=60,
    )
    artifact = _artifact(lifecycle_status="available")
    with pytest.raises(
        module.ArtifactAuthorizationError, match="scan type authority mismatch"
    ):
        service._validate_artifact_scan_type(artifact, "PDF")


def test_cleanup_staging_eligibility_uses_lease_heartbeat_not_created_at():
    now = datetime.now(timezone.utc)
    old_but_live = _artifact(
        created_at=now - timedelta(days=2),
        publication_heartbeat_at=now,
    )
    assert not module.RemediationArtifactCleanup._eligible_after_select(
        old_but_live, now=now, claim_cutoff=now - timedelta(minutes=1)
    )
    old_but_live.publication_heartbeat_at = now - timedelta(minutes=2)
    assert module.RemediationArtifactCleanup._eligible_after_select(
        old_but_live, now=now, claim_cutoff=now - timedelta(minutes=1)
    )
