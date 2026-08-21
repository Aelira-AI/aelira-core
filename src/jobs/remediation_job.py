"""
Remediation Job Processor

Processes accessibility remediation jobs for scanned files.
Applies automated fixes to accessibility issues.
"""

import asyncio
import logging
import re
import tempfile
import shutil
import uuid
from typing import Any, Dict, List, NoReturn, Optional
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Scan,
    RemediationOutcome,
    ScanStatus,
    ScanType,
    ScanResult,
    ScanFix,
    ReviewAuditLog,
    MatterhornResult as MatterhornResultModel,
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
    CloudProvider,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration
from ..ai.lms_remediation_client import LMSRemediationClient
from ..education.remediation.base import (
    IssueCategory,
    classify_issue_category,
    merge_partitioned_manual_issues,
)
from ..utils.security import (
    PERSISTED_BLACKBOARD_ORIGIN_ERROR,
    PERSISTED_CANVAS_ORIGIN_ERROR,
    require_persisted_blackboard_origin,
    require_persisted_canvas_origin,
)
from ..services.remediation_artifact_service import (
    ArtifactInProgressError,
    ArtifactIntegrityError,
    ArtifactPublicationResult,
    ArtifactPublicationRetryable,
    RemediationArtifactService,
)
from .contracts import LostJobOwnership

logger = logging.getLogger(__name__)

_EXECUTION_CONTEXT_TEXT_FIELDS = {
    "policy_version",
    "policy_provider",
    "originating_route",
    "resource_id",
    "course_id",
}
_EXECUTION_CONTEXT_TEXT_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")
_ALLOWED_PURPOSES = ("remediation", "alt_text")

_LMS_PROVIDERS = {
    CloudProvider.CANVAS.value,
    CloudProvider.BLACKBOARD.value,
    CloudProvider.MOODLE.value,
    CloudProvider.BRIGHTSPACE.value,
}
_EXECUTABLE_QUEUED_LMS_PROVIDERS = {
    CloudProvider.CANVAS.value,
    CloudProvider.BLACKBOARD.value,
}
_JOB_FAILURE_CODES = {
    "invalid_job_scope",
    "unsupported_lms_remediation",
    "remediation_artifact_unavailable",
    "manual_required",
    "alt_text_manual_required",
    "policy_not_permitted",
    "download_failed",
    "remediation_failed",
}


class RemediationJobFailed(RuntimeError):
    """Sanitized worker failure consumed by queue state machines."""

    def __init__(self, code: str, terminal_state_committed: bool = False):
        self.code = code if code in _JOB_FAILURE_CODES else "remediation_failed"
        self.terminal_state_committed = terminal_state_committed is True
        super().__init__(self.code)


class RetryableRemediationJobError(RuntimeError):
    """Sanitized transient failure consumed by immediate queue retry paths."""

    _CODES = {
        "remediation_artifact_retryable",
        "remediation_completion_retryable",
    }

    def __init__(
        self,
        code: str,
        *,
        artifact_id: str | None = None,
        cleanup_complete: bool = True,
    ):
        self.code = code if code in self._CODES else "remediation_artifact_retryable"
        self.artifact_id = artifact_id if isinstance(artifact_id, str) else None
        self.cleanup_complete = cleanup_complete is True
        super().__init__(self.code)


class RemediationCompletionCommitFailed(RetryableRemediationJobError):
    """The sole completion commit rolled back and must be retried immediately."""

    def __init__(
        self, *, artifact_id: str | None = None, cleanup_complete: bool = True
    ) -> None:
        super().__init__(
            "remediation_completion_retryable",
            artifact_id=artifact_id,
            cleanup_complete=cleanup_complete,
        )


class RemediationJobHandledResult(dict):
    """Serializable result whose queue completion was committed by the handler."""

    handler_committed = True


class RemediationProcessingResult(dict):
    """Worker result with a non-serializable in-memory publication claim."""

    def __init__(
        self,
        *args: Any,
        artifact_publication: ArtifactPublicationResult | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.artifact_publication = artifact_publication


def transition_retryable_remediation_job(
    job: Any, db: Session, failure: RetryableRemediationJobError
) -> None:
    """Rollback, then durably requeue or exhaust one transient remediation job."""
    db.rollback()
    try:
        db.refresh(job)
    except Exception:
        pass

    if getattr(job, "status", None) in {
        CloudJobStatus.COMPLETED.value,
        CloudJobStatus.FAILED.value,
    }:
        return

    prior_result = job.result_data if isinstance(job.result_data, dict) else {}
    safe_result: dict[str, Any] = {}
    if isinstance(prior_result.get("scan_id"), str):
        safe_result["scan_id"] = prior_result["scan_id"]
    if failure.artifact_id is not None and not failure.cleanup_complete:
        safe_result.update(
            {
                "artifact_id": failure.artifact_id,
                "publication_cleanup_pending": True,
            }
        )

    retry_count = int(getattr(job, "retry_count", 0) or 0) + 1
    max_retries = int(getattr(job, "max_retries", 3) or 3)
    job.retry_count = retry_count
    job.error_message = failure.code
    job.result_data = safe_result
    if retry_count >= max_retries:
        job.status = CloudJobStatus.FAILED.value
        job.progress = 100
        job.progress_message = "Remediation failed after retry limit"
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = CloudJobStatus.PENDING.value
        job.progress = 0
        job.progress_message = "Remediation queued for retry"
        job.completed_at = None
    db.commit()


_SAFE_RESULT_FIELDS = {
    "success",
    "fixed_count",
    "manual_count",
    "failed_count",
    "skipped_count",
    "total_issues",
    "compliance_improvement",
    "upload_job_id",
    "scan_id",
    "artifact_id",
    "artifact_mime_type",
    "artifact_size_bytes",
    "artifact_sha256",
    "artifact_expires_at",
    "artifact_review_status",
    "artifact_required",
}


def _set_remediation_outcome(scan: Scan, outcome: RemediationOutcome | str) -> None:
    """Persist a bounded semantic outcome on its mapped column."""
    scan.remediation_outcome = RemediationOutcome(outcome).value


def _failure_outcome(code: str) -> RemediationOutcome:
    if code in {"manual_required", "alt_text_manual_required"}:
        return RemediationOutcome.MANUAL_REQUIRED
    if code == "remediation_artifact_unavailable":
        return RemediationOutcome.ARTIFACT_UNAVAILABLE
    return RemediationOutcome.REMEDIATION_FAILED


def _safe_failure_result(
    code: str, result: Dict[str, Any] | None, scan: Scan | None
) -> Dict[str, Any]:
    source = result if isinstance(result, dict) else {}
    safe: Dict[str, Any] = {"success": False, "error": code}
    for field in (
        "fixed_count",
        "manual_count",
        "failed_count",
        "skipped_count",
        "total_issues",
    ):
        value = source.get(field, 0)
        safe[field] = value if type(value) is int and value >= 0 else 0
    scan_id = source.get("scan_id")
    if not isinstance(scan_id, str) and scan is not None:
        scan_id = str(scan.id)
    if isinstance(scan_id, str):
        safe["scan_id"] = scan_id
    safe["artifact_id"] = None
    return safe


def _fence_claim_for_handler_commit(job: Any, db: Session) -> None:
    """Lock and verify durable ownership before a handler-owned commit."""
    token = getattr(job, "claim_token", None)
    worker_id = getattr(job, "worker_id", None)
    if not token or not worker_id:
        return
    owner = db.execute(
        select(CloudJobQueue.id)
        .where(
            CloudJobQueue.id == job.id,
            CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
            CloudJobQueue.claim_token == token,
            CloudJobQueue.worker_id == worker_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if owner is None:
        raise RetryableRemediationJobError("job_ownership_lost")


def _clear_claim_for_terminal(job: Any) -> None:
    if getattr(job, "claim_token", None) is None:
        return
    for field in (
        "claim_token",
        "worker_id",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
    ):
        setattr(job, field, None)


async def _commit_terminal_failure(
    job: Any,
    db: Session,
    code: str,
    *,
    scan: Scan | None,
    result: Dict[str, Any] | None = None,
    commit_job: bool = True,
    rollback_scan_state: Dict[str, Any] | None = None,
) -> NoReturn:
    """Atomically persist bounded terminal scan/job failure state, then signal."""
    failure = RemediationJobFailed(code)
    code = failure.code
    if not commit_job:
        raise failure

    assert_owned = getattr(job, "_assert_owned", None)
    if assert_owned is not None:
        await assert_owned()
    _fence_claim_for_handler_commit(job, db)
    prior_job_state = {
        field: getattr(job, field, None)
        for field in (
            "status",
            "progress",
            "progress_message",
            "result_data",
            "completed_at",
            "error_message",
            "claim_token",
            "worker_id",
            "claimed_at",
            "heartbeat_at",
            "lease_expires_at",
        )
    }
    prior_scan_state = rollback_scan_state
    if scan is not None:
        if prior_scan_state is None:
            prior_scan_state = {
                field: getattr(scan, field, None)
                for field in ("status", "remediation_outcome", "completed_at")
            }
        valid_failure_outcomes = {
            RemediationOutcome.MANUAL_REQUIRED.value,
            RemediationOutcome.ARTIFACT_UNAVAILABLE.value,
            RemediationOutcome.REMEDIATION_FAILED.value,
        }
        terminal_scan_staged = (
            scan.status == ScanStatus.FAILED
            and scan.remediation_outcome in valid_failure_outcomes
        )
        if not terminal_scan_staged:
            scan.status = ScanStatus.FAILED
            _set_remediation_outcome(scan, _failure_outcome(code))
            scan.completed_at = datetime.now(timezone.utc)
        elif scan.completed_at is None:
            scan.completed_at = datetime.now(timezone.utc)

    safe_result = _safe_failure_result(code, result, scan)
    job.status = CloudJobStatus.FAILED.value
    job.progress = 100
    job.progress_message = "Remediation failed"
    job.error_message = code
    job.result_data = safe_result
    job.completed_at = datetime.now(timezone.utc)
    _clear_claim_for_terminal(job)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        for field, value in prior_job_state.items():
            setattr(job, field, value)
        if scan is not None and prior_scan_state is not None:
            for field, value in prior_scan_state.items():
                setattr(scan, field, value)
        raise RemediationJobFailed(code, terminal_state_committed=False) from exc
    raise RemediationJobFailed(code, terminal_state_committed=True)


def sanitize_execution_context(value: Any) -> Dict[str, Any]:
    """Return the small, non-sensitive request-intent schema stored on jobs."""
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {}
    for field in ("ai_requested", "alt_text_requested", "upload_back"):
        if type(value.get(field)) is bool:
            result[field] = value[field]
    purposes = value.get("requested_purposes")
    if isinstance(purposes, list):
        requested = {
            purpose
            for purpose in purposes[:16]
            if isinstance(purpose, str) and purpose in _ALLOWED_PURPOSES
        }
        result["requested_purposes"] = [
            purpose for purpose in _ALLOWED_PURPOSES if purpose in requested
        ]
    for field in _EXECUTION_CONTEXT_TEXT_FIELDS:
        candidate = value.get(field)
        if (
            isinstance(candidate, str)
            and candidate
            and _EXECUTION_CONTEXT_TEXT_RE.fullmatch(candidate[:255])
        ):
            result[field] = candidate[:255]
    return result


def _partition_authoritative_document_issues(
    issues: List[Dict[str, Any]],
    *,
    partition_visual: bool = True,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Keep embedded-image work out of the one-client document remediator."""
    automatic: List[Dict[str, Any]] = []
    manual: List[Dict[str, Any]] = []
    for issue in issues:
        classification = classify_issue_category(issue, authoritative=True)
        target = (
            manual
            if (
                (
                    partition_visual
                    and classification.category
                    in {IssueCategory.ALT_TEXT, IssueCategory.CHART}
                )
                or classification.manual_reason is not None
            )
            else automatic
        )
        target.append(issue)
    return automatic, manual


async def process_remediation_job(
    job_data: Dict[str, Any],
    db: Session,
    *,
    ai_client: Any = None,
    alt_text_client: Any = None,
    lms_policy_authoritative: bool = False,
    credential: CloudOAuthCredentials | None = None,
    token_manager: OAuthTokenManager | None = None,
    defer_final_commit: bool = False,
    assert_owned: Any = None,
) -> Dict[str, Any]:
    """
    Process a remediation job.

    Downloads the scanned file, applies automated fixes, and generates
    a remediated output file.

    Args:
        job_data: Job data including:
            - scan_id: Scan ID to remediate
            - cloud_file_id: Cloud file ID (optional)
            - file_path: Path to file to remediate (optional)
            - department_id: Department ID
            - upload_to_cloud: Ignored; automatic upload is disabled
            - provider: Cloud provider (google/microsoft)
        db: Database session

    Returns:
        Dict with:
            - success: bool
            - fixed_count: int (number of issues fixed)
            - manual_count: int (issues needing manual review)
            - failed_count: int (fixes that failed)
            - artifact_id and verified artifact metadata when fixes are published
            - upload_job_id: always None; automatic upload is disabled
            - error: str (if failed)
    """
    scan_id = job_data.get("scan_id")
    cloud_file_id = job_data.get("cloud_file_id")
    file_path = job_data.get("file_path")
    department_id = job_data.get("department_id")
    remediation_job_id = job_data.get("job_id")
    created_by_id = job_data.get("actor_id")
    temp_file_path = None
    artifact_temp_dir = None
    artifact_service = None
    artifact_id = None
    artifact_publication: ArtifactPublicationResult | None = None

    try:
        logger.info(
            f"Processing remediation job for scan {scan_id}, department {department_id}"
        )

        # 1. Fetch scan from database
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            return {"success": False, "error": f"Scan not found: {scan_id}"}

        # 2. Get ScanResult with detailed issues
        scan_result = db.query(ScanResult).filter(ScanResult.scan_id == scan_id).first()
        if not scan_result:
            return {
                "success": False,
                "error": "Scan results not found",
                "scan_id": scan_id,
            }
        if not scan_result.issues:
            original_status = scan.status
            original_outcome = scan.remediation_outcome
            original_completed_at = scan.completed_at
            try:
                scan.status = ScanStatus.COMPLETED
                _set_remediation_outcome(scan, RemediationOutcome.NO_OP)
                scan.completed_at = datetime.now(timezone.utc)
                if not defer_final_commit:
                    if assert_owned is not None:
                        await assert_owned()
                    db.commit()
            except Exception:
                db.rollback()
                scan.status = original_status
                scan.remediation_outcome = original_outcome
                scan.completed_at = original_completed_at
                raise
            return {
                "success": True,
                "fixed_count": 0,
                "manual_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "scan_id": scan_id,
                "artifact_required": False,
            }

        # 3. Determine file path (cloud, manual upload, or explicit path)
        if cloud_file_id:
            # Download from cloud storage (Google Drive/OneDrive)
            download_result = await _download_cloud_file(
                cloud_file_id,
                department_id,
                db,
                credential=credential,
                token_manager=token_manager,
                require_exact_credential=lms_policy_authoritative,
            )
            if not download_result.get("success"):
                return {
                    "success": False,
                    "error": f"Failed to download cloud file: {download_result.get('error')}",
                }
            file_path = download_result.get("local_path")
            temp_file_path = file_path  # Mark for cleanup
        elif not file_path and scan.storage_path:
            # Use manually uploaded file from persistent storage
            file_path = scan.storage_path
            logger.info(f"Using manually uploaded file from storage: {file_path}")

        # 4. Validate file exists
        if not file_path or not Path(file_path).exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        # Managed artifacts supersede caller-visible backup paths.

        # 6. Parse issues into RemediationIssue objects
        issues = scan_result.issues or []
        embedded_alt_manual = []
        if lms_policy_authoritative and scan.scan_type not in (
            "IMAGE",
            "image",
            ScanType.IMAGE,
        ):
            issues, embedded_alt_manual = _partition_authoritative_document_issues(
                issues, partition_visual=alt_text_client is None
            )
        logger.info(
            "Processing remediation issues",
            extra={
                "scan_id": scan_id,
                "automatic_count": len(issues),
                "manual_embedded_alt_count": len(embedded_alt_manual),
            },
        )

        # 7. Select and instantiate remediator. LMS jobs may only use clients
        # bound from current policy by the handler; ordinary jobs retain their
        # historical mechanical behavior.
        if lms_policy_authoritative and scan.scan_type in (
            "IMAGE",
            "image",
            ScanType.IMAGE,
        ):
            # A standalone queued IMAGE remediation produces alt-text metadata,
            # not a remediated file. Task 16B1 must not fake a file artifact.
            return {
                "success": False,
                "error": "remediation_artifact_unavailable",
                "manual_count": 1,
                "scan_id": scan_id,
            }

        effective_ai_client = ai_client
        effective_alt_text_client = alt_text_client
        effective_use_ai = (
            effective_ai_client is not None if lms_policy_authoritative else True
        )
        remediator = _get_remediator_for_scan_type(
            scan_type=scan.scan_type,
            file_path=file_path,
            issues=issues,
            use_ai=effective_use_ai,
            ai_client=effective_ai_client,
            alt_text_client=effective_alt_text_client,
            allow_legacy_nested_ai=not lms_policy_authoritative,
            allow_embedded_alt=(
                effective_alt_text_client is not None or not lms_policy_authoritative
            ),
        )

        if not remediator:
            return {
                "success": False,
                "error": f"No remediator available for scan type: {scan.scan_type}",
            }

        # 8. Run remediation
        logger.info(f"Starting remediation with {remediator.__class__.__name__}")
        remediation_result = await asyncio.to_thread(remediator.remediate)
        if remediation_result.success is not True:
            return {
                "success": False,
                "error": "remediation_failed",
                "scan_id": scan_id,
            }
        if not hasattr(remediation_result, "total_issues"):
            remediation_result.total_issues = sum(
                int(getattr(remediation_result, field, 0) or 0)
                for field in (
                    "fixed_count",
                    "manual_count",
                    "failed_count",
                    "skipped_count",
                )
            )
        if embedded_alt_manual:
            merge_partitioned_manual_issues(
                remediation_result,
                embedded_alt_manual,
                reason="alt_text_client_unavailable",
                purpose="manual_review",
            )

        # Manual or failed work remains manual; never publish a partial output as
        # the authoritative remediation artifact.
        if remediation_result.manual_count > 0 or remediation_result.failed_count > 0:
            scan.status = ScanStatus.FAILED
            _set_remediation_outcome(scan, RemediationOutcome.MANUAL_REQUIRED)
            scan.completed_at = datetime.now(timezone.utc)
            if not defer_final_commit:
                if assert_owned is not None:
                    await assert_owned()
                db.commit()
            return {
                "success": False,
                "error": "manual_required",
                "fixed_count": 0,
                "manual_count": remediation_result.manual_count,
                "failed_count": remediation_result.failed_count,
                "skipped_count": remediation_result.skipped_count,
                "total_issues": remediation_result.total_issues,
                "scan_id": scan_id,
            }

        artifact = None
        if remediation_result.fixed_count > 0:
            output_file = getattr(remediation_result, "output_file", None)
            cloud_file = (
                db.query(CloudFile).filter(CloudFile.id == cloud_file_id).first()
                if cloud_file_id
                else None
            )
            if (
                not output_file
                or not Path(output_file).is_file()
                or getattr(remediation_result, "verification_passed", None) is not True
                or cloud_file is None
                or remediation_job_id is None
            ):
                scan.status = ScanStatus.FAILED
                _set_remediation_outcome(scan, RemediationOutcome.ARTIFACT_UNAVAILABLE)
                scan.completed_at = datetime.now(timezone.utc)
                if not defer_final_commit:
                    if assert_owned is not None:
                        await assert_owned()
                    db.commit()
                return {
                    "success": False,
                    "error": "remediation_artifact_unavailable",
                    "fixed_count": remediation_result.fixed_count,
                    "manual_count": remediation_result.manual_count,
                    "failed_count": remediation_result.failed_count,
                    "skipped_count": remediation_result.skipped_count,
                    "total_issues": remediation_result.total_issues,
                    "scan_id": scan_id,
                }

            artifact_temp_dir = tempfile.mkdtemp(prefix="aelira_remediation_artifact_")
            artifact_source = Path(artifact_temp_dir) / Path(output_file).name
            try:
                await asyncio.to_thread(shutil.copyfile, output_file, artifact_source)
            except OSError as exc:
                raise RetryableRemediationJobError(
                    "remediation_artifact_retryable"
                ) from exc
            artifact_service = RemediationArtifactService.from_settings()
            published = artifact_service.claim_and_publish(
                db,
                source_path=artifact_source,
                trusted_temp_root=artifact_temp_dir,
                department_id=str(department_id),
                scan_id=str(scan_id),
                cloud_file_id=str(cloud_file.id),
                remediation_job_id=str(remediation_job_id),
                created_by_id=created_by_id,
                provider=str(cloud_file.provider),
                scan_type=scan.scan_type,
                filename=artifact_source.name,
                provider_result={"verification_passed": True},
                commit=False,
            )
            artifact_publication = (
                published
                if isinstance(published, ArtifactPublicationResult)
                else ArtifactPublicationResult(
                    artifact=published,
                    artifact_id=str(published.id),
                    publication_token=getattr(published, "publication_token", None),
                )
            )
            artifact = artifact_publication.artifact
            artifact_id = str(artifact.id)

        # Automatic upload is deliberately disabled until Task 16 provides a
        # durable artifact and approval record.
        upload_job_id = None

        # 11. Update scan record with remediation results. Authoritative LMS
        # output is ephemeral until Task 16, so never persist a deleted path.
        scan.completed_at = datetime.now(timezone.utc)
        scan.status = ScanStatus.COMPLETED
        _set_remediation_outcome(
            scan,
            (
                RemediationOutcome.COMPLETED
                if remediation_result.fixed_count > 0
                else RemediationOutcome.NO_OP
            ),
        )

        # Store remediation metadata
        metadata = dict(scan.metadata or {})
        metadata["remediation"] = {
            "fixed_count": remediation_result.fixed_count,
            "manual_count": remediation_result.manual_count,
            "failed_count": remediation_result.failed_count,
            "artifact_id": artifact_id,
            "artifact_persisted": artifact is not None,
            "compliance_improvement": remediation_result.improvement,
            "remediated_at": datetime.now(timezone.utc).isoformat(),
        }
        scan.metadata = metadata

        # Persist individual fixes to scan_fixes table (delete existing for idempotency on retry)
        db.query(ScanFix).filter(ScanFix.scan_id == scan_id).delete()
        for fix in remediation_result.fixed_issues:
            scan_fix = ScanFix(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                issue_id=fix.issue_id,
                category=(
                    fix.category.value
                    if hasattr(fix.category, "value")
                    else fix.category
                ),
                severity=(
                    fix.severity.value
                    if hasattr(fix.severity, "value")
                    else fix.severity
                ),
                description=fix.description,
                location=fix.location,
                original_content=fix.original_content,
                fixed_content=fix.fixed_content,
                fix_method=fix.fix_method,
                model_used=fix.model_used,
                confidence=fix.confidence,
                needs_review=fix.needs_review,
                review_status="auto_approved" if not fix.needs_review else "pending",
                wcag_criteria=fix.wcag_criteria,
                page_number=fix.page_number,
            )
            db.add(scan_fix)

        # Log remediation completion to audit trail
        auto_approved = sum(
            1 for f in remediation_result.fixed_issues if not f.needs_review
        )
        db.add(
            ReviewAuditLog(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                action="remediation_complete",
                details={
                    "total_fixes": len(remediation_result.fixed_issues),
                    "auto_approved": auto_approved,
                    "needs_review": len(remediation_result.fixed_issues)
                    - auto_approved,
                    "manual_issues": remediation_result.manual_count,
                    "failed_issues": remediation_result.failed_count,
                    "artifact_id": artifact_id,
                },
            )
        )

        # Run post-remediation Matterhorn validation (PDF only)
        if scan.scan_type in ("PDF", "pdf") and remediation_result.output_file:
            try:
                from ..education.validation.matterhorn import MatterhornValidator

                validator = MatterhornValidator()
                matterhorn = await asyncio.to_thread(
                    validator.validate, remediation_result.output_file
                )

                for cp in matterhorn.checkpoints:
                    db.add(
                        MatterhornResultModel(
                            id=str(uuid.uuid4()),
                            scan_id=scan_id,
                            checkpoint_id=cp.id,
                            checkpoint_name=cp.name,
                            status=cp.status.value,
                            severity=cp.severity,
                            details=cp.details,
                            page_number=cp.page_number,
                        )
                    )

                db.add(
                    ReviewAuditLog(
                        id=str(uuid.uuid4()),
                        scan_id=scan_id,
                        action="matterhorn_validation",
                        details={
                            "total": matterhorn.total,
                            "passed": matterhorn.passed,
                            "failed": matterhorn.failed,
                            "warnings": matterhorn.warnings,
                            "compliance_level": matterhorn.compliance_level,
                        },
                    )
                )

                logger.info(
                    "Matterhorn validation complete",
                    extra={
                        "scan_id": scan_id,
                        "passed": matterhorn.passed,
                        "failed": matterhorn.failed,
                        "compliance": matterhorn.compliance_level,
                    },
                )
            except ImportError:
                logger.warning(
                    "pikepdf not installed - skipping Matterhorn validation",
                    extra={"scan_id": scan_id},
                )
            except Exception as exc:
                logger.error(
                    "Matterhorn validation failed",
                    extra={"scan_id": scan_id, "error_type": type(exc).__name__},
                )

        if not defer_final_commit:
            if assert_owned is not None:
                await assert_owned()
            db.commit()

        # Queued processing cannot notify here: the caller still has to commit
        # the CloudJobQueue completion state. A transactional outbox/caller-side
        # notification is intentionally deferred to Task 17.

        response = {
            "success": True,
            "fixed_count": remediation_result.fixed_count,
            "manual_count": remediation_result.manual_count,
            "failed_count": remediation_result.failed_count,
            "skipped_count": remediation_result.skipped_count,
            "total_issues": remediation_result.total_issues,
            "compliance_improvement": remediation_result.improvement,
            "upload_job_id": upload_job_id,
            "scan_id": scan_id,
        }
        if artifact is not None:
            response.update(
                {
                    "artifact_id": str(artifact.id),
                    "artifact_mime_type": artifact.mime_type,
                    "artifact_size_bytes": artifact.size_bytes,
                    "artifact_sha256": artifact.sha256,
                    "artifact_expires_at": artifact.expires_at.isoformat(),
                    "artifact_review_status": artifact.review_status,
                }
            )
        else:
            response["artifact_required"] = False
        return RemediationProcessingResult(
            response, artifact_publication=artifact_publication
        )

    except LostJobOwnership:
        db.rollback()
        raise

    except ArtifactInProgressError as exc:
        db.rollback()
        raise RetryableRemediationJobError("remediation_artifact_retryable") from exc

    except ArtifactPublicationRetryable as exc:
        db.rollback()
        raise RetryableRemediationJobError(
            "remediation_artifact_retryable",
            artifact_id=exc.result.artifact_id,
            cleanup_complete=exc.result.cleanup_complete,
        ) from exc

    except ArtifactIntegrityError as exc:
        db.rollback()
        raise RetryableRemediationJobError(
            "remediation_artifact_retryable",
            artifact_id=artifact_id,
            cleanup_complete=False,
        ) from exc

    except RetryableRemediationJobError:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        if artifact_publication is not None and artifact_service is not None:
            cleanup_complete = False
            try:
                artifact_service.abort_staging(
                    db,
                    artifact_id=artifact_publication.artifact_id,
                    publication_token=artifact_publication.publication_token,
                )
                cleanup_complete = True
            except Exception:
                db.rollback()
                logger.warning(
                    "Failed to clean aborted remediation artifact",
                    extra={"scan_id": scan_id},
                )
            raise RetryableRemediationJobError(
                "remediation_artifact_retryable",
                artifact_id=artifact_publication.artifact_id,
                cleanup_complete=cleanup_complete,
            ) from exc
        logger.error(
            "Error processing remediation job",
            extra={"scan_id": scan_id, "error_type": type(exc).__name__},
        )
        return {
            "success": False,
            "error": "remediation_failed",
            "scan_id": scan_id,
        }

    finally:
        # Cleanup temp file and directory if downloaded from cloud
        if temp_file_path:
            try:
                Path(temp_file_path).unlink(missing_ok=True)
                # Also remove the parent temp directory
                parent = Path(temp_file_path).parent
                if parent.name.startswith("aelira_remediation_"):
                    shutil.rmtree(parent, ignore_errors=True)
            except Exception as exc:
                logger.warning(
                    "Failed to cleanup remediation temp file",
                    extra={"scan_id": scan_id, "error_type": type(exc).__name__},
                )
        if artifact_temp_dir:
            shutil.rmtree(artifact_temp_dir, ignore_errors=True)


async def _download_cloud_file(
    cloud_file_id: str,
    department_id: str,
    db: Session,
    *,
    credential: CloudOAuthCredentials | None = None,
    token_manager: OAuthTokenManager | None = None,
    require_exact_credential: bool = False,
) -> Dict[str, Any]:
    """
    Download file from Google Drive or OneDrive to temp directory.

    Args:
        cloud_file_id: Cloud file ID
        department_id: Department ID
        db: Database session

    Returns:
        Dict with success, local_path, error
    """
    try:
        # Get cloud file record
        cloud_file = db.query(CloudFile).filter(CloudFile.id == cloud_file_id).first()
        if not cloud_file:
            return {"success": False, "error": f"Cloud file not found: {cloud_file_id}"}

        # Authoritative queued jobs supply one exact credential. Re-read that
        # same ID immediately before token work; never substitute another
        # active credential for the tenant/provider.
        if credential is not None:
            current_credential = db.get(
                CloudOAuthCredentials,
                credential.id,
                populate_existing=True,
            )
            if (
                current_credential is None
                or current_credential.id != credential.id
                or current_credential.is_active is not True
                or current_credential.department_id != department_id
                or current_credential.provider != cloud_file.provider
                or cloud_file.credential_id != current_credential.id
            ):
                return {"success": False, "error": "invalid_job_scope"}
            credential = current_credential
        elif require_exact_credential:
            return {"success": False, "error": "invalid_job_scope"}
        else:
            # Historical non-authoritative cloud remediation behavior.
            credential = (
                db.query(CloudOAuthCredentials)
                .filter(
                    CloudOAuthCredentials.department_id == department_id,
                    CloudOAuthCredentials.provider == cloud_file.provider,
                    CloudOAuthCredentials.is_active,
                )
                .first()
            )

        if not credential:
            return {
                "success": False,
                "error": "oauth_credentials_unavailable",
            }

        # Refresh token if needed (with distributed lock to prevent races)
        blackboard_instance_url = None
        if credential.provider == CloudProvider.CANVAS.value:
            require_persisted_canvas_origin(credential)
        elif credential.provider == CloudProvider.BLACKBOARD.value:
            try:
                blackboard_instance_url = require_persisted_blackboard_origin(
                    credential
                )
            except ValueError:
                return {
                    "success": False,
                    "error": PERSISTED_BLACKBOARD_ORIGIN_ERROR,
                }
        token_manager = token_manager or OAuthTokenManager()
        access_token = await token_manager.refresh_if_expired(credential, db)

        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="aelira_remediation_")
        local_path = Path(temp_dir) / (cloud_file.file_name or "file")

        # Download file
        if credential.provider == CloudProvider.GOOGLE.value:
            integration = GoogleDriveIntegration(
                access_token=access_token,
                department_id=department_id,
            )
            try:
                result = await integration.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await integration.close()

        elif credential.provider == CloudProvider.MICROSOFT.value:
            integration = OneDriveIntegration(
                access_token=access_token,
                department_id=department_id,
            )
            try:
                result = await integration.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await integration.close()

        elif credential.provider == CloudProvider.CANVAS.value:
            from ..integrations.canvas import CanvasAPIClient

            try:
                canvas_instance_url = require_persisted_canvas_origin(credential)
            except ValueError:
                return {
                    "success": False,
                    "error": PERSISTED_CANVAS_ORIGIN_ERROR,
                }

            api_client = CanvasAPIClient(
                canvas_instance_url=canvas_instance_url,
                access_token=access_token,
                credential_id=credential.id,
            )
            try:
                result = await api_client.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await api_client.close()

        elif credential.provider == CloudProvider.BLACKBOARD.value:
            from ..integrations.blackboard import BlackboardAPIClient

            assert blackboard_instance_url is not None

            # Get course_id from cloud file metadata
            course_id = cloud_file.metadata.get("course_id")
            if not course_id:
                return {
                    "success": False,
                    "error": "Blackboard course ID not found in file metadata",
                }

            api_client = BlackboardAPIClient(
                blackboard_instance_url=blackboard_instance_url,
                access_token=access_token,
                credential_id=credential.id,
            )
            try:
                result = await api_client.download_file(
                    course_id=course_id,
                    content_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await api_client.close()

        else:
            return {
                "success": False,
                "error": f"Unsupported provider for file download: {credential.provider}",
            }

    except Exception as exc:
        logger.error(
            "Error downloading cloud file",
            extra={"cloud_file_id": cloud_file_id, "error_type": type(exc).__name__},
        )
        return {"success": False, "error": "download_failed"}


def _create_backup(file_path: str) -> str:
    """
    Create backup copy in backups/ directory.

    Args:
        file_path: Path to file to backup

    Returns:
        Path to backup file
    """
    file_path_obj = Path(file_path)
    backup_dir = file_path_obj.parent / "backups"
    backup_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path_obj.stem}_backup_{timestamp}{file_path_obj.suffix}"
    backup_path = backup_dir / backup_name

    shutil.copy2(file_path, backup_path)

    logger.info(f"Created backup: {backup_path}")
    return str(backup_path)


def _get_remediator_for_scan_type(
    scan_type: str,
    file_path: str,
    issues: List[Dict[str, Any]],
    use_ai: bool,
    ai_client: Any = None,
    alt_text_client: Any = None,
    allow_legacy_nested_ai: bool = True,
    allow_embedded_alt: bool = True,
) -> Optional[Any]:
    """
    Instantiate appropriate remediator based on scan type.

    Args:
        scan_type: Type of scan (PDF, WORD, POWERPOINT, EXCEL)
        file_path: Path to file
        issues: List of issues from scan
        use_ai: Whether to use AI for fix generation

    Returns:
        Remediator instance or None
    """
    try:
        from ..education.remediation.base import RemediationConfig

        config = RemediationConfig(
            use_ai=use_ai,
            allow_legacy_nested_ai=allow_legacy_nested_ai,
            fix_alt_text=allow_embedded_alt,
        )

        # Map scan types to remediator classes
        if scan_type in ("PDF", "pdf"):
            from ..education.remediation.pdf_remediator import PdfRemediator

            return PdfRemediator(
                file_path=file_path,
                issues=issues,
                config=config,
                ai_client=ai_client,
                alt_text_client=alt_text_client,
            )

        elif scan_type in ("WORD", "word", "DOCX", "docx"):
            from ..education.remediation.docx_remediator import DocxRemediator

            return DocxRemediator(
                file_path=file_path,
                issues=issues,
                config=config,
                ai_client=ai_client,
                alt_text_client=alt_text_client,
            )

        elif scan_type in ("POWERPOINT", "powerpoint", "PPTX", "pptx"):
            from ..education.remediation.pptx_remediator import PptxRemediator

            return PptxRemediator(
                file_path=file_path,
                issues=issues,
                config=config,
                ai_client=ai_client,
                alt_text_client=alt_text_client,
            )

        elif scan_type in ("EXCEL", "excel", "XLSX", "xlsx"):
            from ..education.remediation.xlsx_remediator import XlsxRemediator

            return XlsxRemediator(
                file_path=file_path,
                issues=issues,
                config=config,
                ai_client=ai_client,
                alt_text_client=alt_text_client,
            )

        else:
            logger.warning(f"No remediator available for scan type: {scan_type}")
            return None

    except ImportError as exc:
        logger.error(
            "Failed to import remediator",
            extra={"scan_type": scan_type, "error_type": type(exc).__name__},
        )
        return None


async def _send_remediation_notification(
    scan: Scan,
    result: Any,  # RemediationResult
    department_id: str,
    db: Session,
):
    """
    Send email notification based on remediation result.

    Args:
        scan: Scan record
        result: RemediationResult
        department_id: Department ID
        db: Database session
    """
    try:
        from ..services.alert_service import AlertService

        alert_service = AlertService()

        # Get department to find contact email
        from ..db.models import Department

        department = db.query(Department).filter(Department.id == department_id).first()
        if not department:
            logger.warning(f"Department {department_id} not found for notifications")
            return

        to_emails = [str(department.contact_email)]

        # Determine notification type based on result
        if result.failed_count == 0 and result.fixed_count > 0:
            # Full success
            await alert_service.send_scan_complete_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                issues_found=result.manual_count,  # Issues still needing manual work
                compliance_score=result.remediated_compliance_score or 0.0,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(f"Sent success notification for scan {scan.id}")

        elif result.fixed_count > 0 and result.failed_count > 0:
            # Partial success
            await alert_service.send_remediation_partial_success_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                fixed_count=result.fixed_count,
                failed_count=result.failed_count,
                manual_count=result.manual_count,
                fixed_issues=[
                    {"description": fi.description} for fi in result.fixed_issues
                ],
                failed_issues=result.failed_issues,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(
                f"Sent partial success notification for scan {scan.id} "
                f"(fixed={result.fixed_count}, failed={result.failed_count})"
            )

        elif result.failed_count > 0 and result.fixed_count == 0:
            # Complete failure
            if result.failed_issues:
                error_message = result.failed_issues[0].get(
                    "error", "Automatic remediation was unable to fix this document."
                )
            else:
                error_message = (
                    "Automatic remediation was unable to fix any issues "
                    "in this document."
                )
            await alert_service.send_remediation_failure_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                error_message=error_message,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(
                f"Sent failure notification for scan {scan.id} "
                f"(failed={result.failed_count})"
            )

    except Exception as exc:
        logger.error(
            "Error sending remediation notification",
            extra={"scan_id": scan.id, "error_type": type(exc).__name__},
        )
        # Don't raise - email failure shouldn't break remediation


async def handle_remediation_job(
    job: Any,  # CloudJobQueue
    db: Session,
    token_manager: Any,  # OAuthTokenManager
) -> Dict[str, Any]:
    """
    Job handler for remediation jobs (matches JobProcessor signature).

    Builds job_data from CloudJobQueue columns since the model has no
    job_data column — the needed fields are spread across the job record.

    Args:
        job: CloudJobQueue instance
        db: Database session
        token_manager: OAuth token manager (not used, but required by signature)

    Returns:
        Remediation results
    """
    cloud_file = db.get(CloudFile, job.cloud_file_id) if job.cloud_file_id else None
    credential = (
        db.get(
            CloudOAuthCredentials,
            job.credential_id,
            populate_existing=True,
        )
        if job.credential_id
        else None
    )
    explicit_scan_id = None
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    legacy_input = job.result_data if isinstance(job.result_data, dict) else {}
    candidate = payload.get("scan_id") or legacy_input.get("scan_id")
    if isinstance(candidate, str) and candidate.strip():
        explicit_scan_id = candidate
    scan_id = explicit_scan_id or (
        cloud_file.last_scan_id if cloud_file is not None else None
    )
    scan = db.get(Scan, scan_id) if scan_id else None

    authoritative_scan = (
        scan
        if scan is not None
        and job.department_id is not None
        and scan.department_id == job.department_id
        and scan_id is not None
        and str(scan.id) == str(scan_id)
        else None
    )
    safe_job_scope = (
        getattr(job, "id", None) is not None and job.department_id is not None
    )

    if (
        cloud_file is None
        or credential is None
        or scan is None
        or job.department_id is None
        or cloud_file.department_id is None
        or credential.department_id is None
        or scan.department_id is None
        or cloud_file.credential_id is None
        or cloud_file.provider is None
        or credential.provider is None
        or job.provider is None
        or credential.is_active is not True
        or cloud_file.department_id != job.department_id
        or credential.department_id != job.department_id
        or scan.department_id != job.department_id
        or cloud_file.last_scan_id is None
        or str(cloud_file.last_scan_id) != str(scan.id)
        or cloud_file.credential_id != credential.id
        or cloud_file.provider != credential.provider
        or job.provider != credential.provider
    ):
        await _commit_terminal_failure(
            job,
            db,
            "invalid_job_scope",
            scan=authoritative_scan,
            commit_job=safe_job_scope,
        )

    retry_state = job.result_data if isinstance(job.result_data, dict) else {}
    retained_artifact_id = retry_state.get("artifact_id")
    if retry_state.get("publication_cleanup_pending") is True and isinstance(
        retained_artifact_id, str
    ):
        try:
            RemediationArtifactService.from_settings().abort_staging_for_job(
                db,
                artifact_id=retained_artifact_id,
                remediation_job_id=str(job.id),
            )
        except Exception as exc:
            db.rollback()
            raise RetryableRemediationJobError(
                "remediation_artifact_retryable",
                artifact_id=retained_artifact_id,
                cleanup_complete=False,
            ) from exc
        job.result_data = {}
        if isinstance(retry_state.get("scan_id"), str):
            job.result_data["scan_id"] = retry_state["scan_id"]

    provider = credential.provider
    if provider in _LMS_PROVIDERS and provider not in _EXECUTABLE_QUEUED_LMS_PROVIDERS:
        await _commit_terminal_failure(
            job, db, "unsupported_lms_remediation", scan=authoritative_scan
        )
    if provider in _LMS_PROVIDERS and scan.scan_type in (
        "IMAGE",
        "image",
        ScanType.IMAGE,
    ):
        # Standalone IMAGE work has no remediated file bytes. Keep the stable
        # unsupported/manual outcome rather than inventing an artifact.
        await _commit_terminal_failure(
            job, db, "remediation_artifact_unavailable", scan=authoritative_scan
        )

    context = sanitize_execution_context(getattr(job, "execution_context", {}))
    requested = set(context.get("requested_purposes", []))
    if not context.get("ai_requested"):
        requested.discard("remediation")
    if not context.get("alt_text_requested"):
        requested.discard("alt_text")

    remediation_client = None
    alt_text_client = None
    if provider in _LMS_PROVIDERS:
        binding = {
            "department_id": job.department_id,
            "job_id": str(job.id),
            "scan_id": str(scan.id),
            "cloud_file_id": str(cloud_file.id),
        }
        if "remediation" in requested:
            remediation_client = LMSRemediationClient.bind_if_allowed(
                purpose="remediation", **binding
            )
            if remediation_client is None:
                await _commit_terminal_failure(
                    job, db, "policy_not_permitted", scan=authoritative_scan
                )
        if "alt_text" in requested:
            alt_text_client = LMSRemediationClient.bind_if_allowed(
                purpose="alt_text", **binding
            )
            if alt_text_client is None:
                await _commit_terminal_failure(
                    job, db, "policy_not_permitted", scan=authoritative_scan
                )

    job_data = {
        "job_id": str(job.id),
        "cloud_file_id": job.cloud_file_id,
        "department_id": job.department_id,
        "provider": provider,
        "upload_to_cloud": False,
        "scan_id": scan_id,
    }
    pre_process_scan_state = {
        field: getattr(scan, field, None)
        for field in ("status", "remediation_outcome", "completed_at")
    }
    result = await process_remediation_job(
        job_data,
        db,
        ai_client=remediation_client,
        alt_text_client=alt_text_client,
        lms_policy_authoritative=provider in _LMS_PROVIDERS,
        credential=credential,
        token_manager=token_manager,
        defer_final_commit=True,
        assert_owned=getattr(job, "_assert_owned", None),
    )
    if result.get("success") is not True:
        error = result.get("error")
        code = error if isinstance(error, str) else "remediation_failed"
        await _commit_terminal_failure(
            job,
            db,
            code,
            scan=authoritative_scan,
            result=result,
            rollback_scan_state=pre_process_scan_state,
        )

    safe_result = RemediationJobHandledResult(
        (key, value) for key, value in result.items() if key in _SAFE_RESULT_FIELDS
    )
    artifact_publication = getattr(result, "artifact_publication", None)
    assert_owned = getattr(job, "_assert_owned", None)
    if assert_owned is not None:
        await assert_owned()
    _fence_claim_for_handler_commit(job, db)
    prior_job_state = {
        field: getattr(job, field, None)
        for field in (
            "status",
            "progress",
            "progress_message",
            "result_data",
            "completed_at",
            "error_message",
            "claim_token",
            "worker_id",
            "claimed_at",
            "heartbeat_at",
            "lease_expires_at",
        )
    }
    job.status = CloudJobStatus.COMPLETED.value
    job.progress = 100
    job.progress_message = "Remediation complete"
    job.result_data = dict(safe_result)
    job.completed_at = datetime.now(timezone.utc)
    job.error_message = None
    _clear_claim_for_terminal(job)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        for field, value in prior_job_state.items():
            setattr(job, field, value)
        artifact_id = safe_result.get("artifact_id")
        cleanup_complete = artifact_id is None
        if (
            isinstance(artifact_id, str)
            and isinstance(artifact_publication, ArtifactPublicationResult)
            and isinstance(artifact_publication.publication_token, str)
        ):
            try:
                RemediationArtifactService.from_settings().abort_staging(
                    db,
                    artifact_id=artifact_id,
                    publication_token=artifact_publication.publication_token,
                )
                cleanup_complete = True
            except Exception:
                db.rollback()
                logger.warning(
                    "Failed to clean remediation artifact after completion rollback",
                    extra={"job_id": str(job.id)},
                )
        raise RemediationCompletionCommitFailed(
            artifact_id=artifact_id if isinstance(artifact_id, str) else None,
            cleanup_complete=cleanup_complete,
        ) from exc
    return safe_result


__all__ = [
    "process_remediation_job",
    "handle_remediation_job",
    "sanitize_execution_context",
    "RetryableRemediationJobError",
    "RemediationCompletionCommitFailed",
    "RemediationJobHandledResult",
    "RemediationProcessingResult",
    "transition_retryable_remediation_job",
]
