"""Real PostgreSQL journey from reviewed artifact to one provider upload."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, update

from test_durable_job_processor_postgres import processor
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
    RemediationArtifact,
    RemediationOutcome,
    Scan,
    ScanStatus,
    ScanType,
    User,
)
from src.jobs.registry import JobRegistry, adapt_legacy_handler
from src.jobs import upload_job
from src.services.remediation_artifact_service import RemediationArtifactService

pytest_plugins = ("test_durable_job_processor_postgres",)
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("has_producing_job", [True, False])
async def test_reviewed_upload_progresses_after_artifact_locks_release(
    pg_sessions, tmp_path, has_producing_job
):
    """The upload handler must not self-deadlock against its marker session."""
    from src.education.remediation.base import FixedIssue, IssueCategory, IssueSeverity
    from src.jobs.remediation_job import _queue_upload_job
    from src.services.scan_fix_service import build_output_membership, build_scan_fix

    factory, department_id = pg_sessions
    now = datetime.now(timezone.utc)
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "user",
            "scan",
            "credential",
            "cloud_file",
            "remediation_job",
            "upload_job",
            "artifact",
        )
    }
    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024 * 1024,
        retention_days=30,
        staging_grace_seconds=3600,
    )
    storage_key = f"{department_id}/{ids['scan']}/{ids['artifact']}/{uuid.uuid4()}.docx"
    artifact_path = service.root / storage_key
    artifact_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(artifact_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    artifact_bytes = artifact_path.read_bytes()
    checksum = hashlib.sha256(artifact_bytes).hexdigest()

    with factory() as db:
        db.add(
            User(
                id=ids["user"],
                email=f"{ids['user']}@example.test",
                department_id=department_id,
            )
        )
        db.flush()
        db.add(
            Scan(
                id=ids["scan"],
                scan_type=ScanType.WORD,
                file_hash="d" * 64,
                file_name="source.docx",
                user_id=ids["user"],
                department_id=department_id,
                status=ScanStatus.COMPLETED,
                document_source="cloud_file",
                document_id=ids["cloud_file"],
                remediation_outcome=RemediationOutcome.COMPLETED.value,
            )
        )
        db.add(
            CloudOAuthCredentials(
                id=ids["credential"],
                department_id=department_id,
                provider="google",
                access_token="unexpired-test-token",
                refresh_token="test-refresh-token",
                token_expires_at=now + timedelta(hours=1),
            )
        )
        db.flush()
        cloud_file = CloudFile(
            id=ids["cloud_file"],
            department_id=department_id,
            credential_id=ids["credential"],
            provider="google",
            provider_file_id=f"provider-{ids['cloud_file']}",
            provider_parent_id="provider-parent",
            file_name="source.docx",
            file_type="docx",
            last_scan_id=ids["scan"],
            needs_rescan=False,
            provider_version="version-1",
            provider_modified_at=now,
        )
        db.add(cloud_file)
        db.flush()
        db.add(
            CloudJobQueue(
                id=ids["remediation_job"],
                department_id=department_id,
                job_type="remediate",
                cloud_file_id=ids["cloud_file"],
                credential_id=ids["credential"],
                provider="google",
                payload={"scan_id": ids["scan"]},
                execution_context={"scan_id": ids["scan"]},
                status=CloudJobStatus.COMPLETED.value,
                completed_at=now,
            )
        )
        db.flush()
        db.add(
            RemediationArtifact(
                id=ids["artifact"],
                department_id=department_id,
                scan_id=ids["scan"],
                cloud_file_id=ids["cloud_file"],
                remediation_job_id=(
                    ids["remediation_job"] if has_producing_job else None
                ),
                created_by_id=ids["user"],
                provider="google",
                scan_type="WORD",
                storage_key=storage_key,
                filename="verified-output.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=len(artifact_bytes),
                sha256=checksum,
                lifecycle_status="available",
                published_at=now,
                expires_at=now + timedelta(days=1),
            )
        )
        db.flush()
        cloud_file.current_remediation_artifact_id = ids["artifact"]
        applied_fix = build_scan_fix(
            ids["scan"],
            FixedIssue(
                issue_id="language-1",
                category=IssueCategory.LANGUAGE,
                severity=IssueSeverity.LOW,
                description="Missing language",
                fixed_content="en-AU",
                fix_method="rule",
                confidence=1.0,
            ),
        )
        db.add(applied_fix)
        artifact = db.get(RemediationArtifact, ids["artifact"])
        artifact.provider_result = {
            "reviewed_output_membership": build_output_membership(
                checksum, "d" * 64, [applied_fix]
            )
        }
        db.flush()
        service.approve(
            db,
            artifact_id=ids["artifact"],
            approved_by_id=ids["user"],
            approved_by_ref=f"session:{ids['user']}",
        )
        with patch.object(
            RemediationArtifactService, "from_settings", return_value=service
        ):
            ids["upload_job"] = _queue_upload_job(
                db=db,
                department_id=department_id,
                cloud_file_id=ids["cloud_file"],
                credential_id=ids["credential"],
                provider="google",
                artifact_id=ids["artifact"],
                remediation_job_id=(
                    ids["remediation_job"] if has_producing_job else None
                ),
                scan_id=ids["scan"],
                create_new_version=True,
                requested_by_ref=f"session:{ids['user']}",
            )
        db.commit()

    try:
        registry = JobRegistry()
        registry.register("upload", adapt_legacy_handler(upload_job.handle_upload_job))
        worker = processor(
            factory, f"reviewed-upload-{uuid.uuid4()}", registry, batch_size=1
        )
        claim = worker.claim_batch()[0]
        marker_completed = asyncio.Event()

        original_begin_external_effect = worker.begin_external_effect

        async def begin_external_effect(current_claim):
            token = await original_begin_external_effect(current_claim)
            marker_completed.set()
            return token

        worker.begin_external_effect = begin_external_effect

        remote_upload = AsyncMock(
            return_value={
                "success": True,
                "uploaded": True,
                "new_file_id": "remote-remediated-id",
                "new_file_name": "source_remediated.docx",
                "provider": "google",
            }
        )
        with (
            patch.object(
                upload_job.RemediationArtifactService,
                "from_settings",
                return_value=service,
            ),
            patch.object(
                upload_job.OAuthTokenManager,
                "refresh_if_expired",
                new=AsyncMock(return_value="unexpired-test-token"),
            ),
            patch.object(upload_job, "_upload_to_google", remote_upload),
        ):
            completed = await asyncio.wait_for(worker.process_claim(claim), timeout=2)

        assert marker_completed.is_set()
        remote_upload.assert_awaited_once()
        assert completed is True
        with factory() as db:
            completed = db.get(CloudJobQueue, ids["upload_job"])
            cloud_file = db.get(CloudFile, ids["cloud_file"])
            artifact = db.get(RemediationArtifact, ids["artifact"])
            assert completed is not None
            assert completed.status == CloudJobStatus.COMPLETED.value
            assert completed.claim_token is None
            assert completed.external_effect_state == "confirmed"
            assert isinstance(completed.external_effect_token, str)
            assert cloud_file is not None
            assert cloud_file.remediated_file_id == "remote-remediated-id"
            assert cloud_file.writeback_status == "written_back"
            assert artifact is not None
            assert artifact.written_back_at is not None
            assert (
                artifact.provider_result["writeback_result"]["external_effect_token"]
                == completed.external_effect_token
            )
            with patch.object(
                RemediationArtifactService, "from_settings", return_value=service
            ):
                repeated = _queue_upload_job(
                    db=db,
                    department_id=department_id,
                    cloud_file_id=ids["cloud_file"],
                    credential_id=ids["credential"],
                    provider="google",
                    artifact_id=ids["artifact"],
                    remediation_job_id=(
                        ids["remediation_job"] if has_producing_job else None
                    ),
                    scan_id=ids["scan"],
                    create_new_version=True,
                    requested_by_ref=f"session:{ids['user']}",
                )
            assert repeated == ids["upload_job"]
            assert (
                db.query(CloudJobQueue)
                .filter(
                    CloudJobQueue.cloud_file_id == ids["cloud_file"],
                    CloudJobQueue.job_type == "upload",
                )
                .count()
                == 1
            )
    finally:
        with factory() as db:
            db.execute(
                update(CloudFile)
                .where(CloudFile.id == ids["cloud_file"])
                .values(current_remediation_artifact_id=None)
            )
            db.execute(
                delete(RemediationArtifact).where(
                    RemediationArtifact.id == ids["artifact"]
                )
            )
            db.execute(
                delete(CloudJobQueue).where(
                    CloudJobQueue.id.in_((ids["upload_job"], ids["remediation_job"]))
                )
            )
            db.execute(delete(CloudFile).where(CloudFile.id == ids["cloud_file"]))
            db.execute(
                delete(CloudOAuthCredentials).where(
                    CloudOAuthCredentials.id == ids["credential"]
                )
            )
            db.execute(delete(Scan).where(Scan.id == ids["scan"]))
            db.execute(delete(User).where(User.id == ids["user"]))
            db.commit()
