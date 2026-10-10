"""The upload handler releases read locks before its separate effect fence."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, update, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    Department,
    RemediationArtifact,
    Scan,
    ScanStatus,
    ScanType,
)
from src.jobs import remediation_job, upload_job
from src.jobs.contracts import JobSuccess, LostJobOwnership
from src.jobs.job_processor import ClaimedJob, JobProcessor
from src.services.remediation_artifact_service import RemediationArtifactService

pytestmark = pytest.mark.integration


class _LockingArtifactService:
    """Exercise real PostgreSQL row locks without depending on artifact storage."""

    @contextmanager
    def open_verified(self, db, artifact, **_authority):
        db.execute(
            select(CloudFile)
            .where(CloudFile.id == artifact.cloud_file_id)
            .with_for_update()
        ).scalar_one()
        with BytesIO(b"%PDF-1.7\nverified bytes\n%%EOF\n") as stream:
            yield stream

    def _lock_authority_order(self, db, **authority):
        db.execute(
            select(Scan).where(Scan.id == authority["scan_id"]).with_for_update()
        ).scalar_one()
        db.execute(
            select(CloudFile)
            .where(CloudFile.id == authority["cloud_file_id"])
            .with_for_update()
        ).scalar_one()

    def lock_current(self, db, *, artifact_id, department_id, cloud_file_id, provider):
        scan = db.execute(
            select(Scan)
            .join(RemediationArtifact, RemediationArtifact.scan_id == Scan.id)
            .where(RemediationArtifact.id == artifact_id)
            .with_for_update(of=Scan)
        ).scalar_one()
        cloud = db.execute(
            select(CloudFile).where(CloudFile.id == cloud_file_id).with_for_update()
        ).scalar_one()
        artifact = db.execute(
            select(RemediationArtifact)
            .where(RemediationArtifact.id == artifact_id)
            .with_for_update()
        ).scalar_one()
        assert cloud.current_remediation_artifact_id == artifact.id
        return None, scan, cloud, None, artifact

    def resolve_record(self, *_args, **_kwargs):
        return None

    def mark_written(self, db, *, artifact_id, provider_result):
        artifact = db.get(RemediationArtifact, artifact_id)
        artifact.written_back_at = datetime.now(timezone.utc)
        artifact.provider_result = {"writeback_result": provider_result}
        db.flush()
        return artifact


@pytest.fixture
def pg_upload_graph():
    from src.database_url import engine_url

    engine = create_engine(engine_url(os.environ["DATABASE_URL"]))
    try:
        if engine.dialect.name != "postgresql":
            pytest.skip("requires disposable PostgreSQL")
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except SQLAlchemyError:
        engine.dispose()
        if os.environ.get("CI") == "true":
            pytest.fail("required upload PostgreSQL database unavailable")
        pytest.skip("requires disposable PostgreSQL")

    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    department_id, credential_id, scan_id, cloud_id, artifact_id, job_id = (
        str(uuid4()) for _ in range(6)
    )
    now = datetime.now(timezone.utc)
    with sessions() as db:
        db.add(
            Department(
                id=department_id,
                name="Upload effect test",
                institution="Test",
                contact_email=f"{department_id}@example.test",
            )
        )
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider="google",
                access_token="test-token",
                refresh_token="test-refresh",
                token_expires_at=now + timedelta(days=1),
                is_active=True,
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
                document_source="cloud_file",
                document_id=cloud_id,
                file_hash="1" * 64,
            )
        )
        db.flush()
        cloud = CloudFile(
            id=cloud_id,
            department_id=department_id,
            credential_id=credential_id,
            provider="google",
            provider_file_id="remote-1",
            provider_parent_id="folder-1",
            provider_version="version-1",
            provider_modified_at=now,
            file_name="source.pdf",
            file_type="pdf",
            last_scan_id=scan_id,
            needs_rescan=False,
            writeback_status="approved",
        )
        db.add(cloud)
        db.flush()
        artifact = RemediationArtifact(
            id=artifact_id,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_id,
            provider="google",
            scan_type="PDF",
            storage_key=f"upload-effect/{artifact_id}.pdf",
            filename="improved.pdf",
            mime_type="application/pdf",
            size_bytes=31,
            sha256="a" * 64,
            lifecycle_status="available",
            review_status="approved",
            approval_checksum="a" * 64,
            approval_review_digest="b" * 64,
            approved_by_ref="session:reviewer",
            approved_at=now,
            expires_at=now + timedelta(days=1),
            provider_result={},
        )
        db.add(artifact)
        db.flush()
        cloud.current_remediation_artifact_id = artifact_id
        payload = {
            "artifact_id": artifact_id,
            "scan_id": scan_id,
            "cloud_file_id": cloud_id,
            "department_id": department_id,
            "credential_id": credential_id,
            "provider": "google",
            "create_new_version": True,
            **upload_job.upload_approval_snapshot(artifact),
            **upload_job.upload_source_snapshot(cloud, db.get(Scan, scan_id)),
        }
        job = CloudJobQueue(
            id=job_id,
            department_id=department_id,
            job_type="upload",
            cloud_file_id=cloud_id,
            credential_id=credential_id,
            provider="google",
            provider_file_id="remote-1",
            payload=payload,
            status="processing",
            claim_token=str(uuid4()),
            worker_id="upload-test-worker",
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=5),
        )
        db.add(job)
        db.commit()
        claim = ClaimedJob(
            job_id, "upload", payload, job.claim_token, job.worker_id, 1, 3
        )
    try:
        yield sessions, claim, {**payload, "id": job_id}, cloud_id, artifact_id
    finally:
        with sessions() as db:
            db.execute(
                update(CloudFile)
                .where(CloudFile.id == cloud_id)
                .values(current_remediation_artifact_id=None)
            )
            db.execute(delete(CloudJobQueue).where(CloudJobQueue.id == job_id))
            db.execute(
                delete(RemediationArtifact).where(RemediationArtifact.id == artifact_id)
            )
            db.execute(delete(CloudFile).where(CloudFile.id == cloud_id))
            db.execute(delete(Scan).where(Scan.id == scan_id))
            db.execute(
                delete(CloudOAuthCredentials).where(
                    CloudOAuthCredentials.id == credential_id
                )
            )
            db.execute(delete(Department).where(Department.id == department_id))
            db.commit()
        engine.dispose()


@pytest.mark.asyncio
async def test_verified_upload_releases_read_locks_and_records_writeback(
    pg_upload_graph, monkeypatch
):
    sessions, claim, job_data, cloud_id, artifact_id = pg_upload_graph
    service = _LockingArtifactService()
    monkeypatch.setattr(
        RemediationArtifactService, "from_settings", classmethod(lambda cls: service)
    )

    async def fresh_token(*_args, **_kwargs):
        return "valid-nonexpired-token"

    monkeypatch.setattr(upload_job.OAuthTokenManager, "refresh_if_expired", fresh_token)
    called = []

    async def upload(**_kwargs):
        called.append(True)
        return {
            "success": True,
            "uploaded": True,
            "new_file_id": "remote-fixed",
            "new_file_name": "source_remediated.pdf",
            "provider": "google",
        }

    monkeypatch.setattr(upload_job, "_upload_to_google", upload)
    processor = JobProcessor(session_factory=sessions)
    with sessions() as db:
        job = db.get(CloudJobQueue, claim.job_id)
        job._begin_external_effect = lambda: processor.begin_external_effect(claim)
        result = await asyncio.wait_for(
            upload_job.handle_upload_job(job, db, None),
            timeout=8,
        )
    assert result["success"] is True
    assert called == [True]
    assert processor._finish(claim, JobSuccess(result)) is True
    with sessions() as db:
        assert db.get(RemediationArtifact, artifact_id).written_back_at is not None
        cloud = db.get(CloudFile, cloud_id)
        assert cloud.writeback_status == "written_back"
        assert cloud.remediated_file_id == "remote-fixed"
        job = db.get(CloudJobQueue, claim.job_id)
        assert job.external_effect_state == "confirmed"
        assert job.status == "completed"
    with sessions() as db:
        cloud = db.get(CloudFile, cloud_id)
        with pytest.raises(ValueError, match="writeback_already_completed"):
            remediation_job._queue_upload_job(
                cloud_file_id=cloud_id,
                department_id=cloud.department_id,
                provider="google",
                db=db,
                artifact_id=artifact_id,
                remediation_job_id=None,
                scan_id=job_data["scan_id"],
                credential_id=cloud.credential_id,
                create_new_version=False,
                requested_by_ref="session:another-request",
            )
    assert called == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["needs_rescan", "provider_version", "provider_parent_id", "provider_file_id"],
)
async def test_source_change_after_enqueue_fails_at_effect_fence_without_upload(
    pg_upload_graph, monkeypatch, change
):
    sessions, claim, job_data, cloud_id, _artifact_id = pg_upload_graph
    service = _LockingArtifactService()
    monkeypatch.setattr(
        RemediationArtifactService, "from_settings", classmethod(lambda cls: service)
    )

    async def mutate_then_return_token(*_args, **_kwargs):
        with sessions() as changed_db:
            cloud = changed_db.get(CloudFile, cloud_id)
            setattr(cloud, change, True if change == "needs_rescan" else "changed")
            changed_db.commit()
        return "valid-nonexpired-token"

    monkeypatch.setattr(
        upload_job.OAuthTokenManager, "refresh_if_expired", mutate_then_return_token
    )
    from unittest.mock import AsyncMock

    upload_call = AsyncMock()
    monkeypatch.setattr(upload_job, "_upload_to_google", upload_call)
    processor = JobProcessor(session_factory=sessions)
    with sessions() as db:
        job = db.get(CloudJobQueue, claim.job_id)
        job._begin_external_effect = lambda: processor.begin_external_effect(claim)
        with pytest.raises(LostJobOwnership):
            await asyncio.wait_for(
                upload_job.handle_upload_job(job, db, None),
                timeout=8,
            )
    upload_call.assert_not_awaited()
    with sessions() as db:
        assert db.get(CloudJobQueue, claim.job_id).external_effect_state is None
