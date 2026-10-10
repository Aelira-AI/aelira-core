"""Completion survives real review-graph refreshes and deferred PostgreSQL commit."""

from datetime import datetime, timezone
import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.db.models import (
    CloudJobQueue,
    MatterhornResult,
    RemediationArtifact,
    ReviewAuditLog,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
    User,
)
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.pdf_recovery_plan import reviewed_pdf_recovery_receipt
from src.jobs import remediation_job
from src.jobs.job_processor import JobProcessor
from src.jobs.registry import JobRegistry, adapt_legacy_handler
from src.services.job_enqueue_service import enqueue_cloud_job
from src.services.remediation_artifact_service import RemediationArtifactService
from test_pdf_recovery_persistence_postgres import pg_recovery_scan as pg_recovery_scan
from test_remediation_subprocess_pdf_recovery import recovery as recovery

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "deferred", "queued", "commit_failure"])
async def test_completion_commits_presentation_with_actual_fixes_and_managed_artifact(
    pg_recovery_scan, recovery, tmp_path, monkeypatch, mode
):
    sessions, scan_id = pg_recovery_scan
    source, plan, _ = recovery
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(source)
    baseline = PDFProcessor(
        generate_alt_text=False, validate_alt_text=False, require_complete_scan=True
    ).process_pdf(str(path))
    service = RemediationArtifactService(
        root=tmp_path / "managed",
        max_bytes=5 * 1024 * 1024,
        retention_days=3,
        staging_grace_seconds=60,
    )
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService, "from_settings", lambda: service
    )
    notification = AsyncMock()
    monkeypatch.setattr(remediation_job, "_send_remediation_notification", notification)

    def unexpected_provider(_workspace_id):
        raise AssertionError("reviewed PDF recovery must not acquire a model provider")

    monkeypatch.setattr(
        remediation_job, "workspace_provider_runtime", unexpected_provider
    )
    old_completed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    user_id, job_id = str(uuid4()), str(uuid4())
    try:
        with sessions() as db:
            assert db.autoflush is False
            scan = db.get(Scan, scan_id)
            department_id = scan.department_id
            db.add(
                User(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    department_id=department_id,
                )
            )
            db.flush()
            scan.user_id = user_id
            scan.storage_path = str(path)
            scan.file_hash = hashlib.sha256(source).hexdigest()
            scan.remediation_outcome = "manual_required"
            scan.completed_at = old_completed_at
            scan.progress_message = "Remediation needs manual review"
            db.add(
                ScanResult(
                    scan_id=scan_id,
                    compliance_score=baseline.compliance_score,
                    issues=baseline.issues,
                )
            )
            enqueue_cloud_job(
                db,
                job_id=job_id,
                department_id=department_id,
                job_type="remediate",
                provider="local",
                dedupe_key=f"synthetic-completion:{job_id}",
                max_retries=0,
                payload={
                    "scan_id": scan_id,
                    "requested_by_id": user_id,
                    "options": {"use_ai": True, "generate_alt_text": False},
                },
            )
            db.commit()

        if mode in {"queued", "commit_failure"}:

            async def handler(job, db, token_manager):
                if mode == "commit_failure":
                    original_commit = db.commit
                    original_fence = remediation_job._fence_claim_for_handler_commit
                    final_commit = {"armed": False, "injected": False}

                    def arm_after_ownership_fence(claimed_job, session):
                        original_fence(claimed_job, session)
                        final_commit["armed"] = True

                    def fail_completion_only():
                        if final_commit["armed"] and not final_commit["injected"]:
                            final_commit["armed"] = False
                            final_commit["injected"] = True
                            raise RuntimeError("synthetic completion commit failure")
                        original_commit()

                    monkeypatch.setattr(
                        remediation_job,
                        "_fence_claim_for_handler_commit",
                        arm_after_ownership_fence,
                    )
                    monkeypatch.setattr(db, "commit", fail_completion_only)
                try:
                    return await remediation_job.handle_remediation_job(
                        job, db, token_manager, reviewed_pdf_recovery=plan
                    )
                finally:
                    if mode == "commit_failure":
                        assert final_commit["injected"] is True

            registry = JobRegistry()
            registry.register("remediate", adapt_legacy_handler(handler))
            processor = JobProcessor(
                worker_id=f"synthetic-completion-{job_id}",
                registry=registry,
                session_factory=sessions,
                batch_size=1,
                max_concurrency=1,
            )
            [claim] = processor.claim_batch(limit=1, job_id=job_id)
            assert await processor.process_claim(claim) is True
            notification.assert_not_awaited()
        else:
            with sessions() as db:
                result = await remediation_job.process_remediation_job(
                    {
                        "job_id": job_id,
                        "scan_id": scan_id,
                        "department_id": department_id,
                        "provider": "local",
                        "file_path": str(path),
                        "options": {"use_ai": True, "generate_alt_text": False},
                    },
                    db,
                    defer_final_commit=mode == "deferred",
                    reviewed_pdf_recovery=plan,
                )
                assert result["success"] is True
                assert result["reviewed_pdf_recovery"]["applied"] is True
                if mode == "deferred":
                    notification.assert_not_awaited()
                    db.commit()
                db.expire_all()
                assert db.get(Scan, scan_id).status == ScanStatus.COMPLETED

        # Fresh session proves durable fields, not stale in-memory values whose
        # dirty history may have been cleared by populate_existing(load_only()).
        with sessions() as db:
            scan = db.get(Scan, scan_id)
            job = db.get(CloudJobQueue, job_id)
            fixes = list(db.scalars(select(ScanFix).where(ScanFix.scan_id == scan_id)))
            artifacts = list(
                db.scalars(
                    select(RemediationArtifact).where(
                        RemediationArtifact.scan_id == scan_id
                    )
                )
            )
            if mode == "commit_failure":
                assert job.status == "failed"
                assert job.last_error_code == "remediation_completion_retryable"
                assert scan.status == ScanStatus.FAILED
                assert scan.remediation_outcome == "manual_required"
                assert scan.completed_at == old_completed_at
                assert scan.progress == 35
                assert scan.progress_message == "Remediation needs manual review"
                assert scan.error_message == "Previous attempt failed"
                assert scan.current_remediation_artifact_id is None
                assert not fixes and not artifacts
            else:
                if mode == "queued":
                    assert job.status == "completed"
                    assert job.claim_token is None and job.worker_id is None
                    assert job.result_data["human_review_required"] is True
                assert scan.status == ScanStatus.COMPLETED
                assert scan.remediation_outcome == "completed"
                assert scan.completed_at > old_completed_at
                assert scan.progress == 100
                assert scan.progress_message == "Remediation complete"
                assert scan.error_message is None
                assert fixes and all(
                    fix.needs_review and fix.review_status == "pending" for fix in fixes
                )
                assert len(artifacts) == 1
                artifact = artifacts[0]
                assert artifact.id == scan.current_remediation_artifact_id
                assert artifact.lifecycle_status == "available"
                assert artifact.review_status == "pending"
                receipt = (
                    job.result_data["reviewed_pdf_recovery"]
                    if mode == "queued"
                    else result["reviewed_pdf_recovery"]
                )
                assert receipt["applied"] is True
                assert receipt["independent_review_pending"] is True
                assert receipt["output_sha256"] == artifact.sha256
                assert all(
                    receipt[key] == value
                    for key, value in reviewed_pdf_recovery_receipt(plan).items()
                )
                membership = artifact.provider_result["reviewed_output_membership"]
                assert membership["source_sha256"] == hashlib.sha256(source).hexdigest()
                assert membership["output_sha256"] == artifact.sha256
                assert len(membership["fixes"]) == len(fixes)
                with service.open_verified(
                    db,
                    artifact,
                    department_id=department_id,
                    scan_id=scan_id,
                    cloud_file_id=None,
                ) as stream:
                    assert hashlib.sha256(stream.read()).hexdigest() == artifact.sha256
        assert path.read_bytes() == source
    finally:
        with sessions() as db:
            db.execute(
                update(Scan)
                .where(Scan.id == scan_id)
                .values(current_remediation_artifact_id=None, user_id=None)
            )
            for model in (
                MatterhornResult,
                ReviewAuditLog,
                ScanResult,
                RemediationArtifact,
            ):
                db.execute(delete(model).where(model.scan_id == scan_id))
            db.execute(delete(CloudJobQueue).where(CloudJobQueue.id == job_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()
