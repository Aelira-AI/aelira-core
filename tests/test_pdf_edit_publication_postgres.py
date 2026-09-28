"""Disposable PostgreSQL proof for reviewed PDF edit publication fences."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
from pathlib import Path
import threading
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pikepdf
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from conftest import queue_race_engine
from src.db.models import (
    Base,
    CloudFile,
    CloudOAuthCredentials,
    Department,
    RemediationArtifact,
    Scan,
    ScanFix,
    ScanStatus,
    ScanType,
)
from src.education.pdf_review_candidate import (
    create_pdf_edit_candidate,
    inspect_pdf_edit_targets,
)
from src.services.remediation_artifact_service import (
    ArtifactEditConflict,
    ArtifactPublicationRetryable,
    RemediationArtifactService,
)
from test_pdf_review_candidate import table_pdf
from test_reading_order_snapshot import make_pdf

pytestmark = pytest.mark.integration


def _sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def publication(tmp_path):
    engine = queue_race_engine("TEST_MIGRATION_DATABASE_URL")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    source = make_pdf()
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(source)
    department_id, scan_id = str(uuid4()), str(uuid4())
    with sessions() as db:
        db.add(
            Department(
                id=department_id,
                name="PDF edit test",
                institution="Test",
                contact_email=f"{uuid4()}@example.test",
            )
        )
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                scan_type=ScanType.PDF,
                status=ScanStatus.COMPLETED,
                remediation_outcome="completed",
                file_name="source.pdf",
                file_size_bytes=len(source),
                file_hash=_sha(source),
                storage_path=str(source_path),
            )
        )
        db.commit()
    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=50 * 1024 * 1024,
        retention_days=3,
        staging_grace_seconds=60,
    )
    yield sessions, service, department_id, scan_id, source, tmp_path
    engine.dispose()


def _state(
    db,
    service,
    department_id,
    scan_id,
    source_kind="original",
    cloud_file_id=None,
    provider="local",
):
    result = service.capture_edit_precondition(
        db,
        department_id=department_id,
        scan_id=scan_id,
        cloud_file_id=cloud_file_id,
        provider=provider,
        source_kind=source_kind,
        scope_kind="department",
        course_id=None,
    )
    db.rollback()
    return result


def _candidate(source, *, level=2):
    target = next(
        item
        for item in inspect_pdf_edit_targets(source, _sha(source))
        if item["can_set_heading"]
    )
    operation = {"kind": "heading", "target_id": target["target_id"], "level": level}
    return operation, create_pdf_edit_candidate(source, _sha(source), operation)


def _publish(
    sessions,
    service,
    department_id,
    scan_id,
    precondition,
    operation,
    candidate,
    tmp_path,
    *,
    cloud_file_id=None,
    provider="local",
):
    path = tmp_path / f"candidate-{uuid4()}.pdf"
    path.write_bytes(candidate.content)
    with sessions() as db, path.open("rb") as stream:
        return service.claim_and_publish_stream(
            db,
            source_stream=stream,
            claimed_size_bytes=len(candidate.content),
            claimed_sha256=candidate.sha256,
            claimed_mime_type="application/pdf",
            claimed_filename="review-edit.pdf",
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
            remediation_job_id=None,
            created_by_id=None,
            provider=provider,
            scan_type=ScanType.PDF,
            filename="review-edit.pdf",
            provider_result=None,
            edit_precondition=precondition,
            edit_provenance={
                "source_kind": precondition["source_kind"],
                "source_sha256": precondition["source_sha256"],
                "operation": operation,
            },
        )


def test_migration_upgrade_downgrade_upgrade_restores_paired_fields():
    engine = queue_race_engine("TEST_MIGRATION_DATABASE_URL")
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260928_pdf_edit_pub")
    assert revision.down_revision == "20260917_unknown_confidence"
    schema = f"pdf_edit_migration_{uuid4().hex}"
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        connection.exec_driver_sql(
            "CREATE TABLE remediation_artifacts (id text PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO remediation_artifacts (id) VALUES ('legacy')"
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        columns = {
            item["name"]
            for item in inspect(connection).get_columns("remediation_artifacts")
        }
        assert {"edit_precondition", "edit_provenance"} <= columns
        assert {"edit_precondition", "edit_provenance"} <= set(
            RemediationArtifact.__table__.columns.keys()
        )
        assert (
            connection.exec_driver_sql(
                "SELECT id FROM remediation_artifacts WHERE id='legacy' "
                "AND edit_precondition IS NULL AND edit_provenance IS NULL"
            ).scalar_one()
            == "legacy"
        )
        nested = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "UPDATE remediation_artifacts SET edit_precondition='{}'::json "
                "WHERE id='legacy'"
            )
        nested.rollback()
        module.downgrade()
        assert {
            item["name"]
            for item in inspect(connection).get_columns("remediation_artifacts")
        } == {"id"}
        assert (
            connection.exec_driver_sql(
                "SELECT id FROM remediation_artifacts WHERE id='legacy'"
            ).scalar_one()
            == "legacy"
        )
        module.upgrade()
        assert {"edit_precondition", "edit_provenance"} <= {
            item["name"]
            for item in inspect(connection).get_columns("remediation_artifacts")
        }
        assert (
            connection.exec_driver_sql(
                "SELECT id FROM remediation_artifacts WHERE id='legacy' "
                "AND edit_precondition IS NULL AND edit_provenance IS NULL"
            ).scalar_one()
            == "legacy"
        )
    engine.dispose()


def test_real_saved_table_candidate_is_pending_and_verified(publication):
    sessions, service, department_id, scan_id, _, tmp_path = publication
    source = table_pdf(tmp_path)
    with sessions() as db:
        scan = db.get(Scan, scan_id)
        Path(scan.storage_path).write_bytes(source)
        scan.file_hash, scan.file_size_bytes = _sha(source), len(source)
        db.commit()
    with sessions() as db:
        precondition = _state(db, service, department_id, scan_id)
    target = next(
        item
        for item in inspect_pdf_edit_targets(source, _sha(source))
        if item["role"] == "Table"
    )
    operation = {"kind": "table_column_headers", "target_id": target["target_id"]}
    candidate = create_pdf_edit_candidate(source, _sha(source), operation)
    result = _publish(
        sessions,
        service,
        department_id,
        scan_id,
        precondition,
        operation,
        candidate,
        tmp_path,
    )
    with sessions() as db:
        artifact = db.get(RemediationArtifact, result.artifact_id)
        scan = db.get(Scan, scan_id)
        assert scan.current_remediation_artifact_id == artifact.id
        assert artifact.review_status == "pending"
        assert artifact.approval_checksum is None
        assert artifact.approval_review_digest is None
        assert artifact.approved_by_ref is None
        assert artifact.written_back_at is None
        assert artifact.edit_precondition == precondition
        assert artifact.edit_provenance["operation"] == operation
        with service.open_verified(
            db,
            artifact,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=None,
        ) as stream:
            saved = stream.read()
    assert _sha(saved) == candidate.sha256
    with pikepdf.open(io.BytesIO(saved)) as pdf:
        first = pdf.Root.StructTreeRoot.K[0].K[1].K[0]
        assert all(cell.S == pikepdf.Name.TH for cell in first.K)
        assert all(cell.A.Scope == pikepdf.Name.Column for cell in first.K)


def test_cloud_replacement_resets_writeback_without_inheriting_approval(publication):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    credential_id, cloud_id = str(uuid4()), str(uuid4())
    with sessions() as db:
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider="google",
                access_token="test",
                refresh_token="test",
                token_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.add(
            CloudFile(
                id=cloud_id,
                department_id=department_id,
                credential_id=credential_id,
                provider="google",
                provider_file_id=f"file-{uuid4()}",
                file_name="source.pdf",
                file_type="pdf",
                last_scan_id=scan_id,
                writeback_status="pending_review",
            )
        )
        db.commit()
        first_state = _state(
            db,
            service,
            department_id,
            scan_id,
            cloud_file_id=cloud_id,
            provider="google",
        )
    first_operation, first_candidate = _candidate(source)
    predecessor_id = _publish(
        sessions,
        service,
        department_id,
        scan_id,
        first_state,
        first_operation,
        first_candidate,
        tmp_path,
        cloud_file_id=cloud_id,
        provider="google",
    ).artifact_id
    with sessions() as db:
        predecessor = db.get(RemediationArtifact, predecessor_id)
        predecessor.review_status = "approved"
        predecessor.approval_checksum = predecessor.sha256
        predecessor.approved_by_ref = "test-reviewer"
        predecessor.approved_at = datetime.now(timezone.utc)
        predecessor.written_back_at = datetime.now(timezone.utc)
        cloud = db.get(CloudFile, cloud_id)
        cloud.writeback_status = "written_back"
        cloud.writeback_at = datetime.now(timezone.utc)
        db.commit()
        saved_state = _state(
            db,
            service,
            department_id,
            scan_id,
            "saved",
            cloud_file_id=cloud_id,
            provider="google",
        )
    operation, candidate = _candidate(first_candidate.content, level=3)
    result = _publish(
        sessions,
        service,
        department_id,
        scan_id,
        saved_state,
        operation,
        candidate,
        tmp_path,
        cloud_file_id=cloud_id,
        provider="google",
    )
    with sessions() as db:
        cloud, scan = db.get(CloudFile, cloud_id), db.get(Scan, scan_id)
        predecessor = db.get(RemediationArtifact, predecessor_id)
        artifact = db.get(RemediationArtifact, result.artifact_id)
        assert cloud.current_remediation_artifact_id == artifact.id
        assert cloud.writeback_status == "pending_review"
        assert scan.current_remediation_artifact_id is None
        assert predecessor.review_status == "approved"
        assert predecessor.written_back_at is not None
        assert artifact.review_status == "pending"
        assert artifact.approval_checksum is None
        assert artifact.written_back_at is None


def test_course_change_between_claim_and_finalize_preserves_pointer(publication):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    credential_id, cloud_id = str(uuid4()), str(uuid4())
    with sessions() as db:
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider="canvas",
                access_token="test",
                refresh_token="test",
                token_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.add(
            CloudFile(
                id=cloud_id,
                department_id=department_id,
                credential_id=credential_id,
                provider="canvas",
                provider_parent_id="course-a",
                provider_file_id=f"file-{uuid4()}",
                file_name="source.pdf",
                file_type="pdf",
                last_scan_id=scan_id,
            )
        )
        db.commit()
        precondition = service.capture_edit_precondition(
            db,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_id,
            provider="canvas",
            source_kind="original",
            scope_kind="course",
            course_id="course-a",
        )
        db.rollback()
    operation, candidate = _candidate(source)
    path = tmp_path / "course-staged.pdf"
    path.write_bytes(candidate.content)
    with sessions() as db, path.open("rb") as stream:
        prepared = service._prepare(
            stream.fileno(),
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_id,
            remediation_job_id=None,
            created_by_id=None,
            provider="canvas",
            scan_type=ScanType.PDF,
            filename="review-edit.pdf",
            provider_result=None,
            edit_precondition=precondition,
            edit_provenance={
                "source_kind": "original",
                "source_sha256": _sha(source),
                "operation": operation,
            },
        )
        claim = service.claim(db, prepared)
        assert claim.owned
        service.publish_claimed(
            db,
            claim.artifact,
            publication_token=claim.publication_token,
            source_path=path,
            trusted_temp_root=tmp_path,
        )
    with sessions() as db:
        db.get(CloudFile, cloud_id).provider_parent_id = "course-b"
        db.commit()
    with sessions() as db:
        with pytest.raises(ArtifactEditConflict):
            service.finalize(
                db,
                artifact_id=claim.artifact.id,
                publication_token=claim.publication_token,
            )
        db.rollback()
        assert db.get(CloudFile, cloud_id).current_remediation_artifact_id is None


@pytest.mark.parametrize("existing", [False, True], ids=["absent", "existing"])
def test_two_real_publishers_cannot_replace_the_same_predecessor(publication, existing):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    with sessions() as db:
        precondition = _state(db, service, department_id, scan_id)
    predecessor_id = None
    if existing:
        operation, candidate = _candidate(source, level=4)
        predecessor_id = _publish(
            sessions,
            service,
            department_id,
            scan_id,
            precondition,
            operation,
            candidate,
            tmp_path,
        ).artifact_id
        with sessions() as db:
            precondition = _state(db, service, department_id, scan_id)
        assert precondition["expected_artifact_id"] == predecessor_id
    proposals = [_candidate(source, level=2), _candidate(source, level=3)]
    barrier = threading.Barrier(2)
    original_finalize = service.finalize

    def synchronized_finalize(*args, **kwargs):
        barrier.wait(timeout=15)
        return original_finalize(*args, **kwargs)

    service.finalize = synchronized_finalize

    def attempt(proposal):
        operation, candidate = proposal
        try:
            return _publish(
                sessions,
                service,
                department_id,
                scan_id,
                precondition,
                operation,
                candidate,
                tmp_path,
            )
        except ArtifactEditConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, proposals))
    assert sum(isinstance(item, ArtifactEditConflict) for item in outcomes) == 1
    winner = next(item for item in outcomes if not isinstance(item, Exception))
    with sessions() as db:
        scan = db.get(Scan, scan_id)
        assert scan.current_remediation_artifact_id == winner.artifact_id
        rows = (
            db.query(RemediationArtifact)
            .filter(RemediationArtifact.scan_id == scan_id)
            .all()
        )
        assert len(rows) == (2 if existing else 1)
        assert (
            db.get(RemediationArtifact, winner.artifact_id).lifecycle_status
            == "available"
        )
        if existing:
            assert (
                db.get(RemediationArtifact, predecessor_id).lifecycle_status
                == "superseded"
            )


def test_storage_failure_after_claim_keeps_predecessor_and_cleans_claim(
    publication,
    monkeypatch,
):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    with sessions() as db:
        original = _state(db, service, department_id, scan_id)
    first_operation, first_candidate = _candidate(source)
    predecessor_id = _publish(
        sessions,
        service,
        department_id,
        scan_id,
        original,
        first_operation,
        first_candidate,
        tmp_path,
    ).artifact_id
    with sessions() as db:
        saved = _state(db, service, department_id, scan_id, "saved")
    operation, candidate = _candidate(first_candidate.content, level=3)

    def fail_storage(*args, **kwargs):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(service, "_publish_fd", fail_storage)
    with pytest.raises(ArtifactPublicationRetryable) as failure:
        _publish(
            sessions,
            service,
            department_id,
            scan_id,
            saved,
            operation,
            candidate,
            tmp_path,
        )
    assert failure.value.result.cleanup_complete
    with sessions() as db:
        assert db.get(Scan, scan_id).current_remediation_artifact_id == predecessor_id
        rows = (
            db.query(RemediationArtifact)
            .filter(RemediationArtifact.scan_id == scan_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == predecessor_id


@pytest.mark.parametrize("change", ["review", "source", "json_null"])
def test_finalize_rechecks_persisted_state_after_staging(publication, change):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    with sessions() as db:
        db.add(
            ScanFix(
                scan_id=scan_id,
                issue_id="issue",
                occurrence_key=f"fix-{uuid4()}",
                category="structure",
                severity="medium",
                description="Synthetic",
                fixed_content="same content",
                fix_method="rule",
                review_status="pending",
            )
        )
        db.commit()
        precondition = _state(db, service, department_id, scan_id)
    operation, candidate = _candidate(source)
    path = tmp_path / "staged.pdf"
    path.write_bytes(candidate.content)
    with sessions() as db, path.open("rb") as stream:
        prepared = service._prepare(
            stream.fileno(),
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=None,
            remediation_job_id=None,
            created_by_id=None,
            provider="local",
            scan_type=ScanType.PDF,
            filename="review-edit.pdf",
            provider_result=None,
            edit_precondition=precondition,
            edit_provenance={
                "source_kind": "original",
                "source_sha256": _sha(source),
                "operation": operation,
            },
        )
        claim = service.claim(db, prepared)
        assert claim.owned
        service.publish_claimed(
            db,
            claim.artifact,
            publication_token=claim.publication_token,
            source_path=path,
            trusted_temp_root=tmp_path,
        )
    with sessions() as db:
        if change == "review":
            fix = db.query(ScanFix).filter(ScanFix.scan_id == scan_id).one()
            fix.review_status = "rejected"
            fix.review_notes = "Reviewer changed decision"
        elif change == "source":
            db.get(Scan, scan_id).file_hash = "b" * 64
        else:
            db.execute(
                text(
                    "UPDATE remediation_artifacts SET edit_precondition = 'null'::json WHERE id = :id"
                ),
                {"id": claim.artifact.id},
            )
        db.commit()
    with sessions() as db:
        with pytest.raises((ArtifactEditConflict,)):
            service.finalize(
                db,
                artifact_id=claim.artifact.id,
                publication_token=claim.publication_token,
            )
        db.rollback()
        assert db.get(Scan, scan_id).current_remediation_artifact_id is None


@pytest.mark.parametrize("change", ["expiry", "cleanup", "approval"])
def test_saved_predecessor_change_after_claim_preserves_current(publication, change):
    sessions, service, department_id, scan_id, source, tmp_path = publication
    with sessions() as db:
        original = _state(db, service, department_id, scan_id)
    operation, candidate = _candidate(source)
    predecessor_id = _publish(
        sessions,
        service,
        department_id,
        scan_id,
        original,
        operation,
        candidate,
        tmp_path,
    ).artifact_id
    with sessions() as db:
        saved = _state(db, service, department_id, scan_id, "saved")
    second_operation, second_candidate = _candidate(candidate.content, level=3)
    path = tmp_path / "second-staged.pdf"
    path.write_bytes(second_candidate.content)
    with sessions() as db, path.open("rb") as stream:
        prepared = service._prepare(
            stream.fileno(),
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=None,
            remediation_job_id=None,
            created_by_id=None,
            provider="local",
            scan_type=ScanType.PDF,
            filename="review-edit.pdf",
            provider_result=None,
            edit_precondition=saved,
            edit_provenance={
                "source_kind": "saved",
                "source_sha256": saved["source_sha256"],
                "operation": second_operation,
            },
        )
        claim = service.claim(db, prepared)
        assert claim.owned
        service.publish_claimed(
            db,
            claim.artifact,
            publication_token=claim.publication_token,
            source_path=path,
            trusted_temp_root=tmp_path,
        )
    with sessions() as db:
        predecessor = db.get(RemediationArtifact, predecessor_id)
        if change == "expiry":
            predecessor.created_at = datetime.now(timezone.utc) - timedelta(days=2)
            predecessor.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif change == "cleanup":
            predecessor.cleanup_claimed_at = datetime.now(timezone.utc)
            predecessor.cleanup_reason = "retention"
            predecessor.cleanup_owner = "test"
        else:
            predecessor.review_status = "approved"
            predecessor.approval_checksum = predecessor.sha256
            predecessor.approved_by_ref = "test-reviewer"
            predecessor.approved_at = datetime.now(timezone.utc)
        db.commit()
    with sessions() as db:
        with pytest.raises(ArtifactEditConflict):
            service.finalize(
                db,
                artifact_id=claim.artifact.id,
                publication_token=claim.publication_token,
            )
        db.rollback()
        assert db.get(Scan, scan_id).current_remediation_artifact_id == predecessor_id
