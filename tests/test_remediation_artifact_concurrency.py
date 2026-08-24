"""Task16A concurrency, publication lease, and scan-authority contracts."""

from datetime import datetime, timedelta, timezone
import inspect
import os
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

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
    assert module.LOCK_ORDER == (
        models.Department,
        models.User,
        models.CloudOAuthCredentials,
        models.Scan,
        models.CloudFile,
        models.CloudJobQueue,
        models.RemediationArtifact,
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

    finalizer_source = inspect.getsource(
        module.RemediationArtifactService._stage_claimed_parent_cleanup
    )
    assert "_artifact_metadata" in finalizer_source
    assert "_lock_existing_artifact" in finalizer_source
    before_canonical_lock = finalizer_source.split("_lock_existing_artifact", 1)[0]
    assert "with_for_update" not in before_canonical_lock


def test_review_gate_never_relocks_scan_after_artifact_lock():
    source = inspect.getsource(
        module.RemediationArtifactService._require_approvable_review
    )
    assert "db.query(Scan)" not in source
    assert "db.query(ScanFix)" in source


def test_scan_fix_writer_locks_scan_before_reading_occurrences():
    from src.services.scan_fix_service import lock_scan_review_graph, persist_scan_fixes

    source = inspect.getsource(persist_scan_fixes)
    assert "lock_scan_review_graph" in source
    lock_source = inspect.getsource(lock_scan_review_graph)
    scan_lock = lock_source.index("db.query(Scan)")
    occurrence_read = lock_source.index("db.query(ScanFix)")
    assert scan_lock < occurrence_read
    assert "with_for_update" in lock_source[scan_lock:occurrence_read]


def test_review_mutations_and_artifact_consumption_share_public_lock_graph():
    from src.api import review_routes
    from src.services.scan_fix_service import lock_scan_review_graph

    helper_source = inspect.getsource(lock_scan_review_graph)
    scan_lock = helper_source.index("db.query(Scan)")
    artifact_discovery = helper_source.index("db.query(RemediationArtifact)")
    cloud_lock = helper_source.index("db.query(CloudFile)")
    artifact_lock = helper_source.rindex("db.query(RemediationArtifact)")
    fix_lock = helper_source.index("db.query(ScanFix)")
    assert scan_lock < artifact_discovery < cloud_lock < artifact_lock < fix_lock
    for callable_ in (review_routes.review_fix, review_routes.batch_review):
        assert "lock_scan_review_graph" in inspect.getsource(callable_)
    for callable_ in (
        module.RemediationArtifactService._lock_mutable_graph,
        module.RemediationArtifactService.open_verified,
    ):
        source = inspect.getsource(callable_)
        assert "lock_scan_review_graph" not in source
        assert "_lock_existing_artifact" in source


@pytest.mark.asyncio
async def test_stale_approval_reaches_no_upload_sink(monkeypatch, tmp_path):
    from src.jobs import upload_job

    service = module.RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024,
        retention_days=30,
        staging_grace_seconds=60,
    )
    artifact = _artifact(
        lifecycle_status="available",
        review_status="approved",
        approval_checksum="a" * 64,
        publication_token=None,
        publication_heartbeat_at=None,
        published_at=datetime.now(timezone.utc),
        approved_by_ref="reviewer@example.test",
        approved_at=datetime.now(timezone.utc),
    )
    scan = SimpleNamespace(
        id=SCAN_ID,
        department_id=DEPARTMENT_ID,
        scan_type="WORD",
        status=models.ScanStatus.COMPLETED,
        remediation_outcome=models.RemediationOutcome.COMPLETED.value,
    )
    cloud = SimpleNamespace(
        id=CLOUD_FILE_ID,
        current_remediation_artifact_id=ARTIFACT_ID,
        writeback_status="approved",
        has_remediated_version=True,
        remediation_origin="manual",
    )
    service._lock_existing_artifact = MagicMock(
        return_value=(SimpleNamespace(id=DEPARTMENT_ID), scan, cloud, None, artifact)
    )
    db = MagicMock()
    db.get.return_value = artifact
    query = db.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.populate_existing.return_value = query
    query.all.return_value = [SimpleNamespace(review_status="pending")]
    sink = AsyncMock()
    monkeypatch.setattr(
        upload_job.RemediationArtifactService, "from_settings", lambda: service
    )
    monkeypatch.setattr(upload_job, "_process_upload_path", sink)

    result = await upload_job.process_upload_job(
        {
            "artifact_id": ARTIFACT_ID,
            "department_id": DEPARTMENT_ID,
            "cloud_file_id": CLOUD_FILE_ID,
            "provider": "canvas",
            "artifact_checksum": "a" * 64,
        },
        db,
    )

    assert result == {
        "success": False,
        "uploaded": False,
        "error": "managed_artifact_unavailable",
    }
    sink.assert_not_awaited()
    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None


@pytest.mark.integration
def test_postgres_scan_lock_serializes_fix_writer_with_approval_gate():
    from src.education.remediation.base import (
        FixedIssue,
        IssueCategory,
        IssueSeverity,
    )
    from src.services.scan_fix_service import persist_scan_fixes

    database_url = os.getenv("TEST_TASK8_POSTGRES_URL", "")
    if not database_url:
        pytest.skip("set TEST_TASK8_POSTGRES_URL for PostgreSQL concurrency")
    assert make_url(database_url).get_backend_name() == "postgresql"
    schema = "task8_review_concurrency"
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    metadata = MetaData()
    Table(
        "scans",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("department_id", String(36)),
        Column("current_remediation_artifact_id", String(36)),
    )
    Table("users", metadata, Column("id", String(36), primary_key=True))
    models.ScanFix.__table__.to_metadata(metadata)
    Table(
        "remediation_artifacts",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("scan_id", String(36), nullable=False),
        Column("cloud_file_id", String(36)),
        Column("review_status", String(20)),
        Column("written_back_at", String),
        Column("approval_checksum", String(64)),
        Column("approved_by_id", String(36)),
        Column("approved_by_ref", String(255)),
        Column("approved_at", String),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(metadata.tables["scans"].insert().values(id="scan-1"))

    started = threading.Event()
    finished = threading.Event()
    failures: queue.Queue[BaseException] = queue.Queue()
    fix = FixedIssue(
        issue_id="rule-1",
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.HIGH,
        description="serialized occurrence",
        location="page 1",
        fixed_content="fixed",
        fix_method="rule",
        page_number=1,
    )

    def writer() -> None:
        try:
            with Session(engine) as session:
                started.set()
                persist_scan_fixes(session, "scan-1", [fix])
                session.commit()
        except BaseException as exc:
            failures.put(exc)
        finally:
            finished.set()

    try:
        with Session(engine) as gate:
            gate.execute(
                select(models.Scan.id)
                .where(models.Scan.id == "scan-1")
                .with_for_update()
            ).scalar_one()
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            assert started.wait(2)
            time.sleep(0.2)
            assert not finished.is_set()
            gate.commit()
            assert finished.wait(2)
            thread.join(timeout=2)
        assert failures.empty(), list(failures.queue)
        with Session(engine) as session:
            assert session.query(models.ScanFix).count() == 1
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


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
