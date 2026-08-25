"""Observe-only durable reconciliation for uncertain Canvas file writebacks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

from sqlalchemy import or_, select

from src.db.models import (
    CloudFile,
    CloudJobType,
    CloudOAuthCredentials,
    ContentWritebackLog,
    RemediationArtifact,
)
from src.integrations.canvas.canvas_api import CanvasAPIClient
from src.jobs.contracts import JobFailure
from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job
from src.services.remediation_artifact_service import RemediationArtifactService
from src.utils.security import require_persisted_canvas_origin


@dataclass(frozen=True)
class CanvasObservation:
    outcome: str
    file_id: str | None = None
    version: str | None = None
    checksum: str | None = None


class CanvasReconciliationObserver:
    """Query Canvas and hash exact candidate bytes; it has no write methods."""

    def __init__(self, client: CanvasAPIClient) -> None:
        self.client = client

    async def observe_exact(
        self,
        *,
        course_id: str,
        source_file_id: str,
        candidate_file_id: str | None,
        expected_file_name: str,
        artifact_checksum: str,
        correlation_id: str,
    ) -> CanvasObservation:
        # Course-file listing cannot expose our correlation marker. Without the
        # exact provider id returned by upload, identical old bytes are not proof.
        if not source_file_id or not correlation_id or not candidate_file_id:
            return CanvasObservation(outcome="indeterminate")
        try:
            candidates = await self.client.list_course_files(
                course_id, search_term=expected_file_name
            )
            exact = [
                item
                for item in candidates
                if (
                    item.filename == expected_file_name
                    or item.display_name == expected_file_name
                )
                and (candidate_file_id is None or str(item.id) == candidate_file_id)
            ]
            if not exact:
                return CanvasObservation(outcome="indeterminate")
            if len(exact) != 1:
                return CanvasObservation(outcome="indeterminate")
            candidate = exact[0]
            temporary_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix="aelira-canvas-reconcile-", delete=False
                ) as temporary:
                    temporary_path = temporary.name
                download = await self.client.download_file(candidate.id, temporary_path)
                if not download.success:
                    return CanvasObservation(outcome="indeterminate")
                digest = hashlib.sha256()
                with Path(temporary_path).open("rb") as stream:
                    while chunk := stream.read(64 * 1024):
                        digest.update(chunk)
                checksum = digest.hexdigest()
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass
            if not hmac.compare_digest(checksum, artifact_checksum):
                return CanvasObservation(outcome="absent")
            return CanvasObservation(
                outcome="confirmed",
                file_id=str(candidate.id),
                version=candidate.updated_at.isoformat(),
                checksum=checksum,
            )
        except Exception:
            return CanvasObservation(outcome="indeterminate")

    async def close(self) -> None:
        await self.client.close()


class CanvasReconciliationService:
    """Lease ambiguity rows, observe exact Canvas state, and fence resolution."""

    def __init__(
        self,
        *,
        observer: Any | None = None,
        artifact_service: Any | None = None,
        max_attempts: int = 3,
        lease_seconds: int = 300,
        batch_size: int = 100,
    ) -> None:
        if min(max_attempts, lease_seconds, batch_size) < 1:
            raise ValueError("reconciliation bounds must be positive")
        self.observer = observer
        self.artifact_service = (
            artifact_service or RemediationArtifactService.from_settings()
        )
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.batch_size = batch_size

    @staticmethod
    def _clear_lease(log: ContentWritebackLog) -> None:
        log.reconciliation_lease_token = None
        log.reconciliation_leased_at = None
        log.reconciliation_lease_expires_at = None

    @staticmethod
    def _requires_html_manual_review(log: ContentWritebackLog) -> bool:
        """Identify content-update intents that cannot be proved by file hashes."""
        provider_result = log.provider_result
        return (
            type(provider_result) is dict
            and provider_result.get("kind") == "canvas_html"
            and log.artifact_id is None
            and log.artifact_checksum is None
        )

    @staticmethod
    def _lock_log(db: Any, log_id: str) -> ContentWritebackLog | None:
        """Lock one ambiguity row; small unit fakes retain a direct-get seam."""
        if not hasattr(db, "execute"):
            return db.get(ContentWritebackLog, log_id)
        return db.execute(
            select(ContentWritebackLog)
            .where(ContentWritebackLog.id == log_id)
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _validate_exact_graph(
        *,
        log: Any,
        cloud_file: Any,
        credential: Any,
        artifact: Any,
        department_id: str,
    ) -> tuple[dict[str, Any], str]:
        provider_result = log.provider_result
        if type(provider_result) is not dict:
            raise ValueError("reconciliation_scope_invalid")
        required = (
            "correlation_id",
            "credential_id",
            "canvas_origin",
            "course_id",
            "source_file_id",
            "expected_file_name",
            "artifact_checksum",
        )
        if any(
            not isinstance(provider_result.get(field), str)
            or not provider_result[field]
            for field in required
        ):
            raise ValueError("reconciliation_scope_invalid")
        candidate_file_id = provider_result.get("canvas_file_id")
        if candidate_file_id is not None and (
            not isinstance(candidate_file_id, str) or not candidate_file_id
        ):
            raise ValueError("reconciliation_scope_invalid")
        origin = require_persisted_canvas_origin(credential)
        if (
            cloud_file.department_id != department_id
            or cloud_file.provider != "canvas"
            or cloud_file.credential_id != credential.id
            or credential.department_id != department_id
            or credential.provider != "canvas"
            or credential.is_active is not True
            or provider_result["credential_id"] != credential.id
            or provider_result["canvas_origin"] != origin
            or provider_result["course_id"] != cloud_file.provider_parent_id
            or provider_result["source_file_id"] != cloud_file.provider_file_id
            or provider_result["correlation_id"] != log.correlation_id
            or artifact.id != log.artifact_id
            or artifact.sha256 != log.artifact_checksum
            or provider_result["artifact_checksum"] != artifact.sha256
        ):
            raise ValueError("reconciliation_scope_invalid")
        return provider_result, origin

    async def handle_job(
        self,
        db: Any,
        *,
        payload: dict[str, Any],
        department_id: str,
        token_manager: Any,
        assert_owned: Any = None,
    ) -> dict[str, Any] | JobFailure:
        log_id = payload.get("writeback_log_id")
        if not isinstance(log_id, str):
            return JobFailure.deterministic("reconciliation_scope_invalid")
        log = self._lock_log(db, log_id)
        if log is None or log.reconciliation_status != "reconciliation_required":
            return JobFailure.deterministic("reconciliation_scope_invalid")
        cloud_file = db.get(CloudFile, log.cloud_file_id)
        artifact = db.get(RemediationArtifact, log.artifact_id)
        if cloud_file is None or artifact is None:
            return JobFailure.deterministic("reconciliation_scope_invalid")
        credential = db.get(CloudOAuthCredentials, cloud_file.credential_id)
        if credential is None:
            return JobFailure.deterministic("reconciliation_scope_invalid")
        try:
            provider_result, origin = self._validate_exact_graph(
                log=log,
                cloud_file=cloud_file,
                credential=credential,
                artifact=artifact,
                department_id=department_id,
            )
        except ValueError:
            return JobFailure.deterministic("reconciliation_scope_invalid")

        now = datetime.now(timezone.utc)
        lease_token = str(uuid.uuid4())
        log.reconciliation_attempt_count = (
            int(log.reconciliation_attempt_count or 0) + 1
        )
        log.reconciliation_lease_token = lease_token
        log.reconciliation_leased_at = now
        log.reconciliation_lease_expires_at = now + timedelta(
            seconds=self.lease_seconds
        )
        log.reconciliation_last_error = None
        db.commit()

        owned_observer = self.observer is None
        observer = self.observer
        if observer is None:
            try:
                access_token = await token_manager.refresh_if_expired(credential, db)
                observer = CanvasReconciliationObserver(
                    CanvasAPIClient(
                        canvas_instance_url=origin,
                        access_token=access_token,
                        credential_id=credential.id,
                    )
                )
            except Exception:
                observer = None
        try:
            if observer is None:
                observation = CanvasObservation(outcome="indeterminate")
            else:
                observation = await observer.observe_exact(
                    course_id=provider_result["course_id"],
                    source_file_id=provider_result["source_file_id"],
                    candidate_file_id=provider_result.get("canvas_file_id"),
                    expected_file_name=provider_result["expected_file_name"],
                    artifact_checksum=provider_result["artifact_checksum"],
                    correlation_id=provider_result["correlation_id"],
                )
        finally:
            if owned_observer and observer is not None:
                await observer.close()

        if assert_owned is not None:
            await assert_owned()
        log = self._lock_log(db, log_id)
        if (
            log is None
            or log.reconciliation_status != "reconciliation_required"
            or log.reconciliation_lease_token != lease_token
        ):
            return JobFailure.retryable("reconciliation_lease_lost")
        cloud_file = db.get(CloudFile, log.cloud_file_id, populate_existing=True)
        artifact = db.get(RemediationArtifact, log.artifact_id, populate_existing=True)
        if cloud_file is None or artifact is None:
            return JobFailure.deterministic("reconciliation_scope_invalid")

        confirmed = (
            observation.outcome == "confirmed"
            and isinstance(observation.file_id, str)
            and isinstance(observation.version, str)
            and isinstance(observation.checksum, str)
            and hmac.compare_digest(observation.checksum, artifact.sha256)
        )
        resolved_at = datetime.now(timezone.utc)
        if confirmed:
            reconciliation_result = {
                "correlation_id": log.correlation_id,
                "canvas_file_id": observation.file_id,
                "canvas_version": observation.version,
                "artifact_checksum": observation.checksum,
                "reconciled": True,
            }
            self.artifact_service.mark_written(
                db,
                artifact_id=artifact.id,
                provider_result=reconciliation_result,
                now=resolved_at,
            )
            cloud_file.remediated_file_id = observation.file_id
            cloud_file.writeback_status = "written_back"
            cloud_file.writeback_at = resolved_at
            if cloud_file.remediated_compliance_score is not None:
                cloud_file.last_compliance_score = (
                    cloud_file.remediated_compliance_score
                )
            log.written_back_at = resolved_at
            log.canvas_revision = observation.version
            log.reconciliation_status = "reconciled"
            log.reconciliation_resolution = "confirmed"
            log.reconciliation_resolved_at = resolved_at
            log.reconciliation_last_error = None
            self._clear_lease(log)
            db.commit()
            return {"success": True, "resolution": "confirmed"}

        if observation.outcome == "absent":
            log.reconciliation_status = "failed_manual"
            log.reconciliation_resolution = "failed_manual"
            log.reconciliation_resolved_at = resolved_at
            log.reconciliation_last_error = "canvas_upload_absent"
            cloud_file.writeback_status = "reconciliation_failed"
            self._clear_lease(log)
            db.commit()
            return {
                "success": True,
                "resolution": "failed_manual",
                "retry_safe": True,
            }

        log.reconciliation_last_error = "canvas_state_indeterminate"
        self._clear_lease(log)
        if log.reconciliation_attempt_count >= self.max_attempts:
            log.reconciliation_status = "manual_required"
            log.reconciliation_resolution = "manual_required"
            log.reconciliation_resolved_at = resolved_at
            cloud_file.writeback_status = "reconciliation_failed"
            db.commit()
            return {"success": True, "resolution": "manual_required"}
        log.reconciliation_next_attempt_at = resolved_at + timedelta(
            seconds=min(3600, 30 * (2 ** (log.reconciliation_attempt_count - 1)))
        )
        db.commit()
        return JobFailure.retryable(
            "canvas_state_indeterminate",
            {"attempt": log.reconciliation_attempt_count},
        )

    def backfill(
        self, db: Any, *, now: datetime | None = None, limit: int | None = None
    ) -> int:
        """Enqueue a bounded SKIP LOCKED set of unresolved ambiguity rows."""
        now = now or datetime.now(timezone.utc)
        rows = (
            db.query(ContentWritebackLog)
            .filter(
                ContentWritebackLog.reconciliation_status == "reconciliation_required",
                or_(
                    ContentWritebackLog.reconciliation_next_attempt_at.is_(None),
                    ContentWritebackLog.reconciliation_next_attempt_at <= now,
                ),
                or_(
                    ContentWritebackLog.reconciliation_lease_expires_at.is_(None),
                    ContentWritebackLog.reconciliation_lease_expires_at <= now,
                ),
            )
            .order_by(ContentWritebackLog.created_at.asc())
            .limit(min(limit or self.batch_size, self.batch_size))
            .with_for_update(skip_locked=True)
            .all()
        )
        enqueued = 0
        for log in rows:
            cloud_file = db.get(CloudFile, log.cloud_file_id)
            if cloud_file is None or cloud_file.provider != "canvas":
                continue
            # Stored Canvas HTML has no immutable provider artifact that can be
            # observed and hash-proved after a crash. Never feed that intent to
            # the file reconciliation worker, which would otherwise fail and be
            # re-enqueued forever. Preserve the ambiguity for human resolution.
            if self._requires_html_manual_review(log):
                log.reconciliation_status = "manual_required"
                log.reconciliation_resolution = "manual_required"
                log.reconciliation_resolved_at = now
                log.reconciliation_last_error = "canvas_html_outcome_requires_review"
                cloud_file.writeback_status = "reconciliation_failed"
                self._clear_lease(log)
                continue
            try:
                enqueue_cloud_job(
                    db,
                    department_id=cloud_file.department_id,
                    job_type=CloudJobType.RECONCILE.value,
                    payload={"writeback_log_id": log.id},
                    dedupe_key=f"canvas-reconcile:{log.id}",
                    provider="canvas",
                    credential_id=cloud_file.credential_id,
                    cloud_file_id=cloud_file.id,
                    provider_file_id=cloud_file.provider_file_id,
                    scheduled_for=log.reconciliation_next_attempt_at or now,
                )
                enqueued += 1
            except JobEnqueueError:
                log.reconciliation_last_error = "reconciliation_enqueue_invalid"
        db.commit()
        return enqueued


__all__ = [
    "CanvasObservation",
    "CanvasReconciliationObserver",
    "CanvasReconciliationService",
]
