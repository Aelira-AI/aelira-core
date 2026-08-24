"""Remediation endpoints — auto-fix, code remediation, download, batch."""

import json
import hashlib
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from urllib.parse import quote

from ...ai.providers import get_provider_manager
from ...ai.lms_remediation_client import LMSRemediationClient
from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.database import get_db_dependency
from ...db.models import (
    CloudFile,
    CloudOAuthCredentials,
    RemediationArtifact,
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanStatus,
    ScanType,
)
from ...db.scan_service import ScanService
from ...education.image_alt_text import ImageAltTextGenerator
from ...middleware.quota import require_feature
from ...services.remediation_artifact_service import (
    ArtifactError,
    ArtifactAuthorizationError,
    ArtifactIntegrityError,
    ArtifactPublicationResult,
    RemediationArtifactService,
)
from ...services.scan_fix_service import (
    artifact_review_blockers as scan_fix_review_blockers,
    persist_scan_fixes,
)
from ...jobs.remediation_job import _partition_authoritative_document_issues
from ...utils.sanitization import sanitize_for_postgres
from ...utils.security import (
    PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
    require_persisted_brightspace_origin,
    require_persisted_canvas_origin,
)
from ._shared import (
    APPROVED_REVIEW_STATUSES,
    RemediationOptions,
)
from ._scope import authorize_scan_access

logger = logging.getLogger(__name__)
router = APIRouter()


@contextmanager
def _bounded_pdf_claim_validation_file(
    source_stream,
    *,
    claimed_size_bytes: int,
    claimed_sha256: str,
):
    """Materialize exact claimed PDF bytes in one private bounded temp file."""
    if not isinstance(claimed_size_bytes, int) or claimed_size_bytes < 0:
        raise ValueError("PDF output claim has an invalid size")
    if not isinstance(claimed_sha256, str) or len(claimed_sha256) != 64:
        raise ValueError("PDF output claim has an invalid digest")

    with tempfile.TemporaryDirectory(prefix="aelira_pdf_verification_") as temp_dir:
        verification_path = Path(temp_dir) / "claimed-output.pdf"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(verification_path, flags, 0o600)
        digest = hashlib.sha256()
        remaining = claimed_size_bytes
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as destination:
                descriptor = -1
                while remaining:
                    chunk = source_stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise ValueError(
                            "PDF output claim ended before its claimed size"
                        )
                    destination.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                if source_stream.read(1):
                    raise ValueError("PDF output claim exceeds its claimed size")
                destination.flush()
                os.fsync(destination.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if digest.hexdigest() != claimed_sha256:
            raise ValueError("PDF output claim digest does not match its metadata")
        yield verification_path


def _managed_artifact_authority(
    db: Session,
    *,
    scan_id: str,
    artifact_id: str,
    principal: AuthenticatedPrincipal,
) -> tuple[Scan, CloudFile | None, RemediationArtifact]:
    """Resolve exact tenant/scan/current-artifact authority without disclosure."""
    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    authorized_cloud = authorize_scan_access(db, scan, principal)
    cloud_file = _resolve_bound_scan_cloud_file(db, scan, principal, authorized_cloud)
    artifact = (
        db.query(RemediationArtifact)
        .filter(
            RemediationArtifact.id == artifact_id,
            RemediationArtifact.scan_id == scan_id,
            RemediationArtifact.department_id == principal.department_id,
        )
        .one_or_none()
    )
    current = cloud_file is None or (
        artifact is not None
        and artifact.cloud_file_id == cloud_file.id
        and cloud_file.current_remediation_artifact_id == artifact.id
    )
    if artifact is None or not current:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return scan, cloud_file, artifact


def _artifact_review_blockers(
    db: Session, scan: Scan, artifact: RemediationArtifact
) -> list[str]:
    blockers: list[str] = []
    if artifact.review_status != "pending":
        blockers.append(f"review_{artifact.review_status}")
    if scan.status != ScanStatus.COMPLETED:
        blockers.append("scan_not_completed")
    if scan.remediation_outcome != RemediationOutcome.COMPLETED.value:
        blockers.append("verification_not_passed")
    fixes = db.query(ScanFix).filter(ScanFix.scan_id == scan.id).all()
    blockers.extend(scan_fix_review_blockers(fixes))
    return blockers


def _artifact_failure(exc: ArtifactError) -> HTTPException:
    status_code = 409 if isinstance(exc, ArtifactIntegrityError) else 400
    if isinstance(exc, ArtifactAuthorizationError):
        status_code = 404
    return HTTPException(status_code=status_code, detail="Artifact unavailable")


@router.get("/scans/{scan_id}/artifacts/{artifact_id}")
async def get_managed_artifact_metadata(
    scan_id: str,
    artifact_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    scan, cloud_file, artifact = _managed_artifact_authority(
        db, scan_id=scan_id, artifact_id=artifact_id, principal=principal
    )
    service = RemediationArtifactService.from_settings()
    try:
        service.resolve_record(
            db,
            artifact,
            department_id=principal.department_id,
            scan_id=scan_id,
            cloud_file_id=str(cloud_file.id) if cloud_file is not None else None,
        )
    except ArtifactError as exc:
        raise _artifact_failure(exc) from None
    blockers = _artifact_review_blockers(db, scan, artifact)
    return {
        "id": artifact.id,
        "scan_id": artifact.scan_id,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "expires_at": artifact.expires_at,
        "review_status": artifact.review_status,
        "lifecycle_status": artifact.lifecycle_status,
        "availability": "available",
        "approval_blockers": blockers,
        "can_approve": not blockers,
    }


@router.get("/scans/{scan_id}/artifacts/{artifact_id}/download")
async def download_managed_artifact(
    scan_id: str,
    artifact_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    _, cloud_file, artifact = _managed_artifact_authority(
        db, scan_id=scan_id, artifact_id=artifact_id, principal=principal
    )
    service = RemediationArtifactService.from_settings()
    context = service.open_verified(
        db,
        artifact,
        department_id=principal.department_id,
        scan_id=scan_id,
        cloud_file_id=str(cloud_file.id) if cloud_file is not None else None,
    )
    try:
        stream = context.__enter__()
    except ArtifactError as exc:
        raise _artifact_failure(exc) from None

    def descriptor_chunks():
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            context.__exit__(None, None, None)

    encoded = quote(str(artifact.filename), safe="")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Content-Length": str(artifact.size_bytes),
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        descriptor_chunks(), media_type=artifact.mime_type, headers=headers
    )


@router.post("/scans/{scan_id}/artifacts/{artifact_id}/approve")
async def approve_managed_artifact(
    scan_id: str,
    artifact_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    _managed_artifact_authority(
        db, scan_id=scan_id, artifact_id=artifact_id, principal=principal
    )
    service = RemediationArtifactService.from_settings()
    try:
        artifact = service.approve(
            db,
            artifact_id=artifact_id,
            approved_by_id=principal.user_id,
            approved_by_ref=f"{principal.auth_method}:{principal.user_id}",
        )
        db.commit()
    except ArtifactError as exc:
        db.rollback()
        raise _artifact_failure(exc) from None
    return {
        "id": artifact.id,
        "review_status": artifact.review_status,
        "sha256": artifact.sha256,
    }


@router.post("/scans/{scan_id}/artifacts/{artifact_id}/reject")
async def reject_managed_artifact(
    scan_id: str,
    artifact_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    _managed_artifact_authority(
        db, scan_id=scan_id, artifact_id=artifact_id, principal=principal
    )
    service = RemediationArtifactService.from_settings()
    try:
        artifact = service.reject(
            db,
            artifact_id=artifact_id,
            rejected_by_id=principal.user_id,
            rejected_by_ref=f"{principal.auth_method}:{principal.user_id}",
        )
        db.commit()
    except ArtifactError as exc:
        db.rollback()
        raise _artifact_failure(exc) from None
    return {
        "id": artifact.id,
        "review_status": artifact.review_status,
        "sha256": artifact.sha256,
    }


class _PurposeUsageTracker:
    """Transparent client wrapper that derives bounded, coherent usage metadata."""

    _TRACKED_METHODS = frozenset(
        {"generate_text_sync", "generate_code_sync", "analyze_image_sync"}
    )
    _KNOWN_PROVIDERS = frozenset(
        {"anthropic", "gemini", "local", "ollama", "openai", "xai"}
    )
    _LOCAL_PROVIDERS = frozenset({"ollama", "local"})
    _OUTCOME_PRECEDENCE = {
        "not_requested": -1,
        "allowed_not_used": 0,
        "denied_at_dispatch": 1,
        "attempted_failed": 2,
        "used": 3,
    }

    def __init__(
        self,
        client: Any,
        *,
        requested: bool,
        authoritative: bool,
        trusted_lms_metadata: bool = False,
    ):
        self.client = client
        self._authoritative = authoritative
        self._trusted_lms_metadata = trusted_lms_metadata
        self._bound_provider = self._provider_string(getattr(client, "provider", None))
        self.call_attempted = False
        self.ai_used = False
        self.external_ai_used = False
        self.provider_used: Optional[str] = None
        self.model_used: Optional[str] = None
        self.providers_attempted: tuple[str, ...] = ()
        self.outcome = (
            "not_requested"
            if not requested
            else (
                "denied_at_dispatch"
                if authoritative and client is None
                else "allowed_not_used"
            )
        )

    @property
    def provider(self) -> Any:
        return getattr(self.client, "provider", None)

    def bind_client(self, client: Any) -> None:
        """Attach an allowed client after the pre-dispatch tracker is created."""
        self.client = client
        self._bound_provider = self._provider_string(getattr(client, "provider", None))
        if self.outcome == "denied_at_dispatch":
            self.outcome = "allowed_not_used"

    @classmethod
    def _provider_string(cls, value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        provider = value.casefold()
        return provider if provider in cls._KNOWN_PROVIDERS else None

    @staticmethod
    def _bounded_model(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value or len(value) > 200:
            return None
        return value if value.isprintable() and "\x00" not in value else None

    def _record_identity(self, result: Optional[Dict[str, Any]] = None) -> None:
        # A purpose-bound LMS client is the authority for transport identity;
        # response dictionaries cannot relabel the provider or model.
        if self._trusted_lms_metadata:
            provider = self._bound_provider
            model = self._bounded_model(getattr(self.client, "model", None))
        else:
            provider = self._provider_string((result or {}).get("provider"))
            if provider is None:
                provider = self._provider_string(getattr(self.client, "provider", None))
            model = self._bounded_model((result or {}).get("model"))
        if provider is not None:
            self.provider_used = provider
        if model is not None:
            self.model_used = model

    def _attempt_is_external(self) -> bool:
        # Unknown identity is treated as external rather than understating data
        # egress. Only an allowlisted local provider proves locality.
        return self.provider_used not in self._LOCAL_PROVIDERS

    def _is_authoritative_no_call_denial(self, result: Dict[str, Any]) -> bool:
        return (
            self.provider_used is not None
            and self._provider_string(getattr(self.client, "provider", None))
            == self._bound_provider
            and result.get("success") is False
            and result.get("ai_used") is False
            and result.get("external_ai_used") is False
            and result.get("purpose_outcome") == "denied_at_dispatch"
        )

    def _promote_outcome(self, outcome: str) -> None:
        if self._OUTCOME_PRECEDENCE[outcome] > self._OUTCOME_PRECEDENCE[self.outcome]:
            self.outcome = outcome

    def observe_image_usage(self, metadata: Any) -> None:
        """Merge trusted, bounded generator transport metadata into this purpose."""
        if not isinstance(metadata, Mapping):
            return
        attempted = metadata.get("providers_attempted")
        safe_attempts = []
        if isinstance(attempted, (list, tuple)):
            for value in attempted[:8]:
                provider = self._provider_string(value)
                if provider is not None and provider not in safe_attempts:
                    safe_attempts.append(provider)
        self.providers_attempted = tuple(safe_attempts)
        if safe_attempts:
            self.call_attempted = True

        provider = self._provider_string(metadata.get("provider"))
        if provider is not None:
            self.provider_used = provider
        model = self._bounded_model(metadata.get("model"))
        if model is not None:
            self.model_used = model

        if type(metadata.get("external_ai_used")) is bool:
            self.external_ai_used = (
                self.external_ai_used or metadata["external_ai_used"]
            )
        if metadata.get("ai_used") is True:
            self.ai_used = True
        outcome = metadata.get("outcome")
        if isinstance(outcome, str) and outcome in self._OUTCOME_PRECEDENCE:
            self._promote_outcome(outcome)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.client, name)
        if not callable(target) or name not in self._TRACKED_METHODS:
            return target

        def tracked(*args: Any, **kwargs: Any) -> Any:
            previously_attempted = self.call_attempted
            self.call_attempted = True
            try:
                result = target(*args, **kwargs)
            except Exception:
                self._record_identity()
                self.external_ai_used = (
                    self.external_ai_used or self._attempt_is_external()
                )
                self._promote_outcome("attempted_failed")
                raise

            self._record_identity(result if isinstance(result, dict) else None)
            if (
                self._authoritative
                and self._trusted_lms_metadata
                and isinstance(result, dict)
                and self._is_authoritative_no_call_denial(result)
            ):
                self.call_attempted = previously_attempted
                self._promote_outcome("denied_at_dispatch")
            elif isinstance(result, dict) and result.get("success") is True:
                self.ai_used = True
                self.external_ai_used = (
                    self.external_ai_used or self._attempt_is_external()
                )
                self._promote_outcome("used")
            else:
                self.external_ai_used = (
                    self.external_ai_used or self._attempt_is_external()
                )
                self._promote_outcome("attempted_failed")
            return result

        return tracked


def _aggregate_purpose_usage(
    remediation: _PurposeUsageTracker, alt_text: _PurposeUsageTracker
) -> Dict[str, Any]:
    """Return aggregate audit fields without retaining prompts or content."""
    providers = {
        purpose: tracker.provider_used
        for purpose, tracker in (
            ("remediation", remediation),
            ("alt_text", alt_text),
        )
        if tracker.provider_used
    }
    providers_attempted = {
        purpose: tracker.providers_attempted
        for purpose, tracker in (
            ("remediation", remediation),
            ("alt_text", alt_text),
        )
        if tracker.providers_attempted
    }
    return {
        "remediation_ai_attempted": remediation.call_attempted,
        "alt_text_attempted": alt_text.call_attempted,
        "remediation_ai_used": remediation.ai_used,
        "alt_text_used": alt_text.ai_used,
        "remediation_external_ai_used": remediation.external_ai_used,
        "alt_text_external_ai_used": alt_text.external_ai_used,
        "external_ai_used": (remediation.external_ai_used or alt_text.external_ai_used),
        "providers": providers,
        "providers_attempted": providers_attempted,
        "purpose_outcomes": {
            "remediation": remediation.outcome,
            "alt_text": alt_text.outcome,
        },
    }


def _audit_terminal_remediation(
    *,
    db: Session,
    request: Request,
    user_id: str,
    department_id: str,
    scan_id: str,
    file_type: str,
    remediation_requested: bool,
    alt_text_requested: bool,
    remediation_tracker: _PurposeUsageTracker,
    alt_text_tracker: _PurposeUsageTracker,
    successful: bool,
    total_issues: int,
    fixed_count: int,
    manual_count: int,
    failed_count: int,
    skipped_count: int = 0,
    original_score: Optional[float] = None,
    remediated_score: Optional[float] = None,
    improvement: Optional[float] = None,
    duration_seconds: Optional[float] = None,
    error: str = "remediation_failed",
    artifact_id: Optional[str] = None,
    commit: bool = False,
) -> None:
    """Emit exactly one bounded aggregate audit for a terminal route outcome."""
    from ...security.audit_service import AuditService

    usage = _aggregate_purpose_usage(remediation_tracker, alt_text_tracker)
    common = {
        "user_id": user_id,
        "department_id": department_id,
        "scan_id": scan_id,
        "file_type": file_type,
        "use_ai": usage["remediation_ai_used"] or usage["alt_text_used"],
        "remediation_ai_requested": remediation_requested,
        "alt_text_requested": alt_text_requested,
        **usage,
        "total_issues": total_issues,
        "fixed_count": fixed_count,
        "manual_count": manual_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "request": request,
        "commit": commit,
        "artifact_id": artifact_id,
    }
    audit = AuditService(db)
    if successful:
        audit.log_remediation_complete(
            **common,
            original_score=original_score,
            remediated_score=remediated_score,
            improvement=improvement,
            duration_seconds=duration_seconds,
        )
    else:
        audit.log_remediation_failed(**common, error=error)


def _best_effort_terminal_dispatch_failure(
    *,
    db: Session,
    request: Request,
    user_id: str,
    department_id: str,
    scan_id: str,
    file_type: str,
    remediation_requested: bool,
    alt_text_requested: bool,
    remediation_tracker: _PurposeUsageTracker,
    alt_text_tracker: _PurposeUsageTracker,
    error_code: str,
    total_issues: int = 0,
    fixed_count: int = 0,
    manual_count: int = 0,
    failed_count: int = 0,
    skipped_count: int = 0,
) -> None:
    """Persist one sanitized dispatch failure without changing the HTTP outcome.

    These exits do not mutate remediation state, so their audit uses a separate
    best-effort transaction. Audit persistence is deliberately fail-open here:
    a logging outage must not convert an established 4xx response into a 500.
    """
    try:
        _audit_terminal_remediation(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=file_type,
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            successful=False,
            total_issues=total_issues,
            fixed_count=fixed_count,
            manual_count=manual_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            error=error_code,
            commit=True,
        )
    except Exception:
        logger.exception(
            "Best-effort terminal remediation dispatch audit could not be persisted",
            extra={"scan_id": scan_id, "error_code": error_code},
        )


def _sanitize_str(value: Optional[str]) -> Optional[str]:
    """Strip NUL (0x00) bytes that PostgreSQL text columns reject."""
    return sanitize_for_postgres(value)


def _get_bound_fallback_cloud_file(
    db: Session, scan_id: str, department_id: str
) -> CloudFile | None:
    """Return a non-LTI fallback CloudFile bound to the scan tenant."""
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.last_scan_id == scan_id,
            CloudFile.department_id == department_id,
        )
        .first()
    )
    if (
        not cloud_file
        or cloud_file.last_scan_id != scan_id
        or cloud_file.department_id != department_id
    ):
        return None
    return cloud_file


def _resolve_bound_scan_cloud_file(
    db: Session,
    scan,
    principal: AuthenticatedPrincipal,
    authorized_cloud_file: CloudFile | None,
) -> CloudFile | None:
    """Resolve the scan's unique tenant-bound CloudFile after authorization.

    ``authorize_scan_access`` returns the course-bound file only for scoped LTI
    principals. Other authorized principals still need the canonical scan link
    for LMS policy classification. Two rows are enough to detect ambiguity.
    """
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == principal.department_id,
            CloudFile.last_scan_id == scan.id,
        )
        .limit(2)
        .all()
    )

    if len(cloud_files) > 1:
        raise HTTPException(status_code=404, detail="Scan not found")

    cloud_file = cloud_files[0] if cloud_files else None
    if cloud_file is not None and (
        cloud_file.department_id != principal.department_id
        or cloud_file.last_scan_id != scan.id
    ):
        raise HTTPException(status_code=404, detail="Scan not found")

    course_scoped_lti = (
        principal.auth_method == "lti" and not principal.lti_account_wide
    )
    if course_scoped_lti:
        if authorized_cloud_file is None or cloud_file is None:
            raise HTTPException(status_code=404, detail="Scan not found")
        if str(authorized_cloud_file.id) != str(cloud_file.id):
            raise HTTPException(status_code=404, detail="Scan not found")
    elif authorized_cloud_file is not None and (
        cloud_file is None or str(authorized_cloud_file.id) != str(cloud_file.id)
    ):
        raise HTTPException(status_code=404, detail="Scan not found")

    return cloud_file


def _get_bound_cloud_credential(
    db: Session, cloud_file: CloudFile, department_id: str
) -> CloudOAuthCredentials | None:
    """Return the credential bound to a CloudFile's tenant and provider."""
    if not cloud_file.credential_id:
        return None
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.id == cloud_file.credential_id,
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == cloud_file.provider,
        )
        .first()
    )
    if (
        not credential
        or credential.id != cloud_file.credential_id
        or credential.department_id != department_id
        or credential.provider != cloud_file.provider
    ):
        return None
    return credential


# ==================== Auto-Remediation Helpers ====================


def _infer_category(issue: dict) -> str:
    """Infer accessibility category from issue fields.

    Checks explicit category/type first, then falls back to keyword
    matching on description/message and WCAG rule numbers.
    """
    from ...education.math_contracts import math_issue_type_from

    math_issue_type = math_issue_type_from(issue)
    if math_issue_type:
        return "structure"

    # Use explicit category/type/issue_type if present
    explicit = issue.get("category") or issue.get("type") or issue.get("issue_type")
    if explicit:
        # Map LaTeX-specific issue_type values to categories
        issue_type_map = {
            # LaTeX/general issue types
            "missing_title": "title",
            "missing_author": "title",
            "title_not_displayed": "title",
            "missing_lang": "language",
            "missing_language": "language",
            "missing_alt_text": "alt_text",
            "missing_figure_caption": "alt_text",
            "missing_table_caption": "table",
            "missing_table_structure": "table",
            "complex_table_no_header": "table",
            "equation_no_label": "aria",
            "color_only_emphasis": "color",
            "low_contrast_potential": "contrast",
            "low_color_contrast": "contrast",
            "unlabeled_hyperlink": "link",
            "links_missing_alt": "link",
            "vague_link_text": "link",
            "missing_list_structure": "list",
            # PDF scanner issue types
            "reading_order_mismatch": "reading_order",
            "unlabeled_form_fields": "form",
            "missing_tab_order": "form",
            "missing_structure_tree": "structure",
            "empty_structure_tree": "structure",
            "not_marked_tagged": "structure",
            "missing_content_marking": "structure",
            "empty_parent_tree": "structure",
            "missing_document_root": "structure",
            "missing_pdfua_identifier": "structure",
            "missing_bookmarks": "navigation",
            "missing_tounicode": "structure",
            "missing_role_map": "structure",
            "incomplete_role_map": "structure",
        }
        if explicit in issue_type_map:
            return issue_type_map[explicit]
        return explicit

    rule = (
        issue.get("rule")
        or issue.get("wcag_criteria")
        or issue.get("wcag_criterion")
        or ""
    ).lower()
    msg = (
        issue.get("message") or issue.get("description") or issue.get("title") or ""
    ).lower()

    # Keyword matching on description/message
    if "heading" in msg or "h1" in msg:
        return "heading"
    if "alt" in msg and ("text" in msg or "image" in msg):
        return "alt_text"
    if "contrast" in msg or "color" in msg:
        return "contrast"
    if "table" in msg:
        return "table"
    if "link" in msg or "url" in msg or "hyperlink" in msg:
        return "link"
    if "language" in msg or "lang" in msg:
        return "language"
    if "keyboard" in msg or "focus" in msg:
        return "keyboard"
    if "form" in msg or "label" in msg:
        return "form"
    if "title" in msg or "author" in msg:
        return "title"
    if "structure tree" in msg or "untagged" in msg or "tagged" in msg:
        return "structure"
    if "bookmark" in msg or "outline" in msg:
        return "navigation"
    if "list" in msg and ("fake" in msg or "bullet" in msg):
        return "list"
    if "equation" in msg:
        return "aria"

    # Fall back to WCAG rule number
    if "1.1" in rule:
        return "alt_text"
    if "1.3" in rule:
        return "structure"
    if "1.4" in rule:
        return "contrast"
    if "2.1" in rule:
        return "keyboard"
    if "2.4" in rule:
        return "navigation"
    if "3.1" in rule:
        return "language"
    if "4.1" in rule:
        return "aria"

    return "other"


class _InvalidScanResultError(ValueError):
    """Persisted scan issues do not have a safe remediation shape."""


class _InvalidProviderResponseError(ValueError):
    """An image provider result does not have the documented safe shape."""


def _copy_validated_remediation_issues(issues: Any) -> list:
    """Return an isolated copy of persisted issues with a safe mapping shape."""
    if not isinstance(issues, list):
        raise _InvalidScanResultError("invalid_scan_result")

    string_fields = {
        "category",
        "type",
        "issue_type",
        "rule",
        "wcag_criteria",
        "wcag_criterion",
        "message",
        "description",
        "title",
    }
    for raw_issue in issues:
        if not isinstance(raw_issue, dict):
            raise _InvalidScanResultError("invalid_scan_result")
        if not isinstance(raw_issue.get("metadata", {}), dict):
            raise _InvalidScanResultError("invalid_scan_result")
        raw_nodes = raw_issue.get("nodes", [])
        if not isinstance(raw_nodes, list) or any(
            not isinstance(node, dict) for node in raw_nodes
        ):
            raise _InvalidScanResultError("invalid_scan_result")
        if any(
            field in raw_issue
            and raw_issue[field] is not None
            and not isinstance(raw_issue[field], str)
            for field in string_fields
        ):
            raise _InvalidScanResultError("invalid_scan_result")

    return deepcopy(issues)


def _extract_validated_remediation_issues(container: Any) -> list:
    """Accept only the persisted list or documented ``details`` wrapper."""
    if isinstance(container, list):
        issues = container
    elif isinstance(container, dict) and "details" in container:
        issues = container["details"]
    else:
        raise _InvalidScanResultError("invalid_scan_result")
    return _copy_validated_remediation_issues(issues)


def _extract_validated_image_analysis(analysis: Any) -> tuple[str, bool]:
    """Extract bounded image output while rejecting ambiguous provider shapes."""
    if not isinstance(analysis, dict):
        raise _InvalidProviderResponseError("invalid_provider_response")
    type_detection = analysis.get("type_detection")
    if not isinstance(type_detection, dict):
        raise _InvalidProviderResponseError("invalid_provider_response")

    is_decorative = type_detection.get("is_decorative")
    if type(is_decorative) is not bool:
        raise _InvalidProviderResponseError("invalid_provider_response")
    description = analysis.get("description")
    if is_decorative and description is None:
        return "", True
    if not isinstance(description, dict):
        raise _InvalidProviderResponseError("invalid_provider_response")
    raw_alt_text = description.get("alt_text", "")
    if not isinstance(raw_alt_text, str):
        raise _InvalidProviderResponseError("invalid_provider_response")
    if is_decorative:
        return "", True
    return raw_alt_text.strip(), False


def _normalize_issues_for_remediation(issues: list) -> list:
    """Normalize raw scanner issues into the format the remediator expects.

    The remediator's ``can_auto_fix()`` and ``apply_fix()`` rely on a rich
    ``metadata`` dict (with ``generated_alt_text``, ``page_number``,
    ``issue_type``, ``paragraph_index``, etc.).  Scanner output stores these
    at the top level of the issue dict; this function copies them into the
    ``metadata`` sub-dict so the remediator can find them.

    Normalizes raw scan issues into the remediator input shape.
    """
    normalized = []
    for i, issue in enumerate(_copy_validated_remediation_issues(issues)):
        category = _infer_category(issue)
        category_lower = category.lower()

        metadata = issue["metadata"] if "metadata" in issue else {}
        # Copy scanner top-level fields into metadata
        metadata.setdefault("page_number", issue.get("page_number", 1))
        metadata.setdefault("issue_type", issue.get("issue_type"))
        metadata.setdefault("rule", issue.get("rule"))
        metadata.setdefault("element", issue.get("element"))
        metadata.setdefault("text", issue.get("text", ""))
        metadata.setdefault("bbox", issue.get("bbox"))
        metadata.setdefault("image_index", issue.get("image_index", 0))
        metadata.setdefault("image_xref", issue.get("image_xref"))
        metadata.setdefault("occurrence_ordinal", issue.get("occurrence_ordinal"))
        metadata.setdefault("occurrence_id", issue.get("occurrence_id"))
        metadata.setdefault(
            "generated_alt_text",
            issue.get("generated_alt_text") or issue.get("alt_text"),
        )
        # Paragraph location (DOCX fixes)
        metadata.setdefault("paragraph_index", issue.get("paragraph_index"))
        metadata.setdefault("paragraph_indices", issue.get("paragraph_indices"))
        # Heading-specific
        metadata.setdefault(
            "suggested_level",
            issue.get("suggested_level") or issue.get("expected_level", 1),
        )
        metadata.setdefault("current_level", issue.get("current_level"))
        metadata.setdefault("expected_level", issue.get("expected_level"))
        # List-specific
        metadata.setdefault(
            "is_fake_list",
            category_lower == "list" or "fake" in str(issue.get("title", "")).lower(),
        )
        # Table-specific
        metadata.setdefault("has_data_rows", True)
        metadata.setdefault("table_index", issue.get("table_index"))
        # Link text
        metadata.setdefault("link_text", issue.get("link_text"))
        metadata.setdefault("link_url", issue.get("link_url"))
        # Title-specific
        metadata.setdefault("suggested_title", issue.get("suggested_title"))
        metadata.setdefault("existing_title", issue.get("existing_title"))
        # PPTX-specific: map scanner fields to remediator expectations
        # The PowerPointProcessor's computed `.issues` field already exposes
        # 0-based slide_index at the top level; only fall back to the legacy
        # 1-based slide_number / slide keys if the 0-based value is absent.
        # Without this branch, metadata.slide_index stayed None and every
        # PPTX alt-text fix bailed at "No slide index for alt text fix".
        if "slide_index" in issue and issue.get("slide_index") is not None:
            metadata.setdefault("slide_index", int(issue["slide_index"]))
        else:
            slide_num = issue.get("slide_number") or issue.get("slide")
            if slide_num is not None:
                metadata.setdefault("slide_index", int(slide_num) - 1)
        metadata.setdefault("shape_id", issue.get("shape_id"))
        metadata.setdefault("shape_name", issue.get("shape_name", ""))
        metadata.setdefault(
            "suggested_alt_text",
            issue.get("suggested_alt_text"),
        )

        suggested_fix = issue.get("suggested_fix") or issue.get("fix_suggestion") or ""

        normalized.append(
            {
                "id": issue.get("id", f"issue-{i}"),
                "category": category,
                "type": category,
                "severity": issue.get("severity", "medium"),
                "description": issue.get("description")
                or issue.get("message", "Accessibility issue"),
                "message": issue.get("message") or issue.get("description", ""),
                "location": issue.get("location", "Unknown"),
                "fix_suggestion": suggested_fix,
                "recommendation": issue.get("recommendation", ""),
                "metadata": metadata,
            }
        )
    return normalized


def _scanfix_to_issue_dict(fix) -> dict:
    """Convert a ScanFix ORM record to the dict format expected by HtmlRemediator.

    The BaseRemediator._normalize_issues() method consumes dicts with keys like
    ``id``, ``category``, ``severity``, ``description``, ``location``,
    ``original_content``, and ``fix_suggestion``.  The ``fixed_content`` from
    the review queue is passed as ``fix_suggestion`` so that the remediator
    uses the already-approved content rather than generating a new fix.
    """
    return {
        "id": fix.issue_id or fix.id,
        "category": fix.category or "other",
        "severity": fix.severity or "medium",
        "description": fix.description or "",
        "location": fix.location,
        "original_content": fix.original_content,
        "fix_suggestion": fix.fixed_content,
        "fixed_content": fix.fixed_content,
        "wcag_criteria": fix.wcag_criteria,
        "metadata": {},
    }


def _map_category_string(category_str: str):
    """Map a category string to IssueCategory enum value.

    Uses the same mapping as BaseRemediator._map_category().
    """
    from ...education.remediation.base import IssueCategory

    category_map = {
        "alt_text": IssueCategory.ALT_TEXT,
        "alternative_text": IssueCategory.ALT_TEXT,
        "image": IssueCategory.ALT_TEXT,
        "heading": IssueCategory.HEADING,
        "heading_structure": IssueCategory.HEADING,
        "contrast": IssueCategory.CONTRAST,
        "color_contrast": IssueCategory.CONTRAST,
        "table": IssueCategory.TABLE,
        "table_header": IssueCategory.TABLE,
        "link": IssueCategory.LINK,
        "hyperlink": IssueCategory.LINK,
        "list": IssueCategory.LIST,
        "list_structure": IssueCategory.LIST,
        "language": IssueCategory.LANGUAGE,
        "reading_order": IssueCategory.READING_ORDER,
        "form": IssueCategory.FORM,
        "aria": IssueCategory.ARIA,
        "navigation": IssueCategory.NAVIGATION,
        "structure": IssueCategory.STRUCTURE,
        "color": IssueCategory.COLOR,
        "chart": IssueCategory.CHART,
        "sheet": IssueCategory.SHEET,
        "title": IssueCategory.TITLE,
        "other": IssueCategory.OTHER,
    }

    normalized = category_str.lower().strip().replace(" ", "_").replace("-", "_")
    return category_map.get(normalized, IssueCategory.OTHER)


def _map_severity_string(severity_str: str):
    """Map a severity string to IssueSeverity enum value.

    Uses the same mapping as BaseRemediator._map_severity().
    """
    from ...education.remediation.base import IssueSeverity

    severity_map = {
        "critical": IssueSeverity.CRITICAL,
        "high": IssueSeverity.HIGH,
        "medium": IssueSeverity.MEDIUM,
        "low": IssueSeverity.LOW,
        "error": IssueSeverity.HIGH,
        "warning": IssueSeverity.MEDIUM,
        "info": IssueSeverity.LOW,
    }

    normalized = severity_str.lower().strip()
    return severity_map.get(normalized, IssueSeverity.MEDIUM)


# ==================== Auto-Remediation Endpoints ====================


def _effective_remediation_use_ai(
    options: Optional[RemediationOptions],
    query_use_ai: Optional[bool],
    *,
    lms_backed: bool,
) -> bool:
    """Resolve AI intent without promoting a Pydantic model default to consent.

    An explicitly supplied body field takes precedence over the deprecated
    query parameter. LMS-backed scans default to mechanical remediation unless
    either request location explicitly asks for AI. Non-LMS scans retain the
    historical default.
    """
    if options is not None and "use_ai" in options.model_fields_set:
        return bool(options.use_ai)
    if query_use_ai is not None:
        return bool(query_use_ai)
    return not lms_backed


def _effective_generate_alt_text(
    options: Optional[RemediationOptions], *, lms_backed: bool
) -> bool:
    """Resolve a separate, explicit alt-text intent for LMS documents."""
    if options is not None and "generate_alt_text" in options.model_fields_set:
        return bool(options.generate_alt_text)
    return not lms_backed


@router.post("/remediate/batch")
async def batch_remediate(
    scan_ids: List[str],
    background_tasks: BackgroundTasks,
    use_ai: bool = True,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """Validate and queue a batch without allowing partial authorization."""
    return await _batch_remediate_impl(
        scan_ids=scan_ids,
        use_ai=use_ai,
        background_tasks=background_tasks,
        db=db,
        principal=principal,
    )


@router.post("/remediate/{scan_id}")
async def remediate_scan(
    scan_id: str,
    request: Request,
    options: Optional[RemediationOptions] = None,
    use_ai: Optional[bool] = None,  # None preserves legacy only for non-LMS scans
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Auto-remediate accessibility issues for a completed scan.

    Automatically fix accessibility issues in the scanned document.
    REQUIRES API KEY IN PRODUCTION.

    This endpoint triggers the auto-remediation engine to fix as many
    accessibility issues as possible in the scanned document.

    Args:
        scan_id: The scan ID to remediate
        options: Remediation options (use_ai, latex_formats, multimedia_format)
        use_ai: Whether to use AI for generating fixes (default: True, deprecated)

    Returns:
        Remediation result with fixed/manual counts and managed artifact metadata.
        Filesystem paths and storage keys are never returned.
    """
    from ...education.remediation import (
        RemediationConfig,
        DocxRemediator,
        PptxRemediator,
        PdfRemediator,
        XlsxRemediator,
        LatexRemediator,
        MultimediaRemediator,
    )
    from ...education.remediation.base import (
        OutputFormat,
        merge_partitioned_manual_issues,
    )

    _, user_id, department_id = principal.as_legacy_tuple()

    # Get the scan
    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorized_cloud_file = authorize_scan_access(db, scan, principal)
    resolved_cloud_file = _resolve_bound_scan_cloud_file(
        db, scan, principal, authorized_cloud_file
    )

    lms_providers = {
        "canvas",
        "blackboard",
        "moodle",
        "brightspace",
    }
    lms_cloud_file = (
        resolved_cloud_file
        if resolved_cloud_file and resolved_cloud_file.provider in lms_providers
        else None
    )
    effective_use_ai = _effective_remediation_use_ai(
        options,
        use_ai,
        lms_backed=lms_cloud_file is not None,
    )
    effective_alt_text = _effective_generate_alt_text(
        options, lms_backed=lms_cloud_file is not None
    )
    scan_type_value = getattr(scan.scan_type, "value", scan.scan_type)
    is_image_scan = scan_type_value == ScanType.IMAGE.value
    remediation_requested = effective_use_ai and not is_image_scan
    alt_text_requested = effective_alt_text or (effective_use_ai and is_image_scan)
    remediation_client = None
    alt_text_client = None
    remediation_tracker = _PurposeUsageTracker(
        None,
        requested=remediation_requested,
        authoritative=lms_cloud_file is not None,
        trusted_lms_metadata=lms_cloud_file is not None,
    )
    alt_text_tracker = _PurposeUsageTracker(
        None,
        requested=alt_text_requested,
        authoritative=lms_cloud_file is not None,
        trusted_lms_metadata=lms_cloud_file is not None,
    )

    if lms_cloud_file and lms_cloud_file.provider in {"blackboard", "moodle"}:
        _best_effort_terminal_dispatch_failure(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type_value,
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            error_code="unsupported_lms_provider",
        )
        raise HTTPException(
            status_code=400,
            detail="LMS file remediation is not supported for this provider",
        )

    issues = []
    if scan.result:
        try:
            issues = _extract_validated_remediation_issues(scan.result.issues)
        except _InvalidScanResultError:
            db.rollback()
            try:
                _audit_terminal_remediation(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type_value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    successful=False,
                    total_issues=0,
                    fixed_count=0,
                    manual_count=0,
                    failed_count=0,
                    error="invalid_scan_result",
                    commit=True,
                )
            except Exception:
                logger.exception(
                    "Best-effort invalid scan result audit could not be persisted",
                    extra={"scan_id": scan_id},
                )
            raise HTTPException(
                status_code=500, detail="Remediation failed. Please try again."
            )

    if lms_cloud_file:
        binding = {
            "department_id": principal.department_id,
            "actor_id": principal.user_id,
            "scan_id": str(scan.id),
            "cloud_file_id": str(lms_cloud_file.id),
        }

        if remediation_requested:
            remediation_client = LMSRemediationClient.bind_if_allowed(
                purpose="remediation", **binding
            )
            if remediation_client is None:
                _best_effort_terminal_dispatch_failure(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type_value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    error_code="policy_not_permitted",
                )
                raise HTTPException(
                    status_code=403, detail="LMS AI remediation is not permitted"
                )
            remediation_tracker.bind_client(remediation_client)
        if alt_text_requested:
            alt_text_client = LMSRemediationClient.bind_if_allowed(
                purpose="alt_text", **binding
            )
            if alt_text_client is None:
                _best_effort_terminal_dispatch_failure(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type_value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    error_code="policy_not_permitted",
                )
                raise HTTPException(
                    status_code=403, detail="LMS AI alt_text is not permitted"
                )
            alt_text_tracker.bind_client(alt_text_client)

    if not scan.result:
        _best_effort_terminal_dispatch_failure(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type_value,
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            error_code="missing_scan_result",
        )
        raise HTTPException(status_code=400, detail="Scan has no results to remediate")

    if not issues:
        original_status = scan.status
        original_outcome = scan.remediation_outcome
        original_completed_at = scan.completed_at
        commit_attempted = False
        try:
            scan.status = ScanStatus.COMPLETED
            scan.remediation_outcome = RemediationOutcome.NO_OP.value
            scan.completed_at = datetime.now(timezone.utc)
            _audit_terminal_remediation(
                db=db,
                request=request,
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type_value,
                remediation_requested=remediation_requested,
                alt_text_requested=alt_text_requested,
                remediation_tracker=remediation_tracker,
                alt_text_tracker=alt_text_tracker,
                successful=True,
                total_issues=0,
                fixed_count=0,
                manual_count=0,
                failed_count=0,
                skipped_count=0,
                commit=False,
            )
            commit_attempted = True
            db.commit()
        except Exception:
            db.rollback()
            scan.status = original_status
            scan.remediation_outcome = original_outcome
            scan.completed_at = original_completed_at
            if commit_attempted:
                try:
                    _audit_terminal_remediation(
                        db=db,
                        request=request,
                        user_id=user_id,
                        department_id=department_id,
                        scan_id=scan_id,
                        file_type=scan_type_value,
                        remediation_requested=remediation_requested,
                        alt_text_requested=alt_text_requested,
                        remediation_tracker=remediation_tracker,
                        alt_text_tracker=alt_text_tracker,
                        successful=False,
                        total_issues=0,
                        fixed_count=0,
                        manual_count=0,
                        failed_count=0,
                        error="remediation_exception",
                        commit=True,
                    )
                except Exception:
                    logger.exception(
                        "Best-effort no-op commit failure audit could not be persisted",
                        extra={"scan_id": scan_id},
                    )
            raise HTTPException(
                status_code=500, detail="Remediation failed. Please try again."
            )
        return {
            "success": True,
            "message": "No issues to remediate",
            "fixed_count": 0,
            "manual_count": 0,
            "failed_count": 0,
            "artifact_required": False,
        }

    # Check if we have a stored file path (from manual upload)
    file_path = scan.storage_path

    # Fall back to scan.result.file_path for backward compatibility
    if not file_path and hasattr(scan.result, "file_path"):
        file_path = scan.result.file_path

    # Fall back: re-download from cloud provider if this was a cloud scan
    if not file_path or not os.path.exists(file_path):
        from ...db.models import CloudProvider
        from ...integrations.oauth_token_manager import OAuthTokenManager

        cloud_file = resolved_cloud_file
        if cloud_file and cloud_file.credential_id:
            credential = _get_bound_cloud_credential(
                db, cloud_file, principal.department_id
            )
            if credential:
                brightspace_url = None
                if credential.provider == CloudProvider.BRIGHTSPACE.value:
                    try:
                        brightspace_url = require_persisted_brightspace_origin(
                            credential
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=409,
                            detail=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
                        ) from exc
                try:
                    canvas_url = None
                    if credential.provider == CloudProvider.CANVAS.value:
                        canvas_url = require_persisted_canvas_origin(credential)
                    token_manager = OAuthTokenManager()
                    access_token = await token_manager.refresh_if_expired(
                        credential, db
                    )

                    # Determine provider and download
                    temp_dir = tempfile.mkdtemp()
                    local_path = os.path.join(
                        temp_dir, f"{cloud_file.file_name or 'file'}"
                    )

                    if credential.provider == CloudProvider.CANVAS.value:
                        from ...integrations.canvas.canvas_api import CanvasAPIClient

                        assert canvas_url is not None
                        client = CanvasAPIClient(
                            canvas_instance_url=canvas_url, access_token=access_token
                        )
                        try:
                            dl_result = await client.download_file(
                                file_id=cloud_file.provider_file_id,
                                local_path=local_path,
                            )
                            if dl_result.success:
                                file_path = dl_result.local_path
                                # Update scan storage_path so future remediations don't re-download
                                scan.storage_path = file_path
                                db.commit()
                                logger.info(
                                    f"Re-downloaded Canvas file for remediation: {cloud_file.file_name}"
                                )
                        finally:
                            await client.close()

                    elif credential.provider == CloudProvider.GOOGLE.value:
                        from ...integrations.google_workspace.google_drive import (
                            GoogleDriveIntegration,
                        )

                        drive = GoogleDriveIntegration(
                            credential_id=credential.id, access_token=access_token
                        )
                        try:
                            dl_result = await drive.download_file(
                                file_id=cloud_file.provider_file_id,
                                local_path=local_path,
                            )
                            if dl_result.success:
                                file_path = dl_result.local_path
                                scan.storage_path = file_path
                                db.commit()
                                logger.info(
                                    f"Re-downloaded Google Drive file for remediation: {cloud_file.file_name}"
                                )
                        finally:
                            await drive.close()

                    elif credential.provider == CloudProvider.BRIGHTSPACE.value:
                        from ...integrations.brightspace.brightspace_api import (
                            BrightspaceAPIClient,
                        )

                        metadata = cloud_file.provider_metadata or {}
                        org_unit_id = metadata.get("org_unit_id")
                        topic_id = int(cloud_file.provider_file_id)

                        # Add proper extension from URL
                        url = metadata.get("url", "")
                        if "." in url:
                            url_ext = url.rsplit(".", 1)[-1].lower()
                            local_path = os.path.join(
                                temp_dir, f"{cloud_file.file_name or 'file'}.{url_ext}"
                            )

                        bs_client = BrightspaceAPIClient(
                            brightspace_instance_url=brightspace_url,
                            access_token=access_token,
                        )
                        try:
                            file_bytes, content_type = await bs_client.get_topic_file(
                                int(org_unit_id), topic_id
                            )
                            with open(local_path, "wb") as f:
                                f.write(file_bytes)
                            file_path = local_path
                            scan.storage_path = file_path
                            db.commit()
                            logger.info(
                                f"Re-downloaded Brightspace file for remediation: {cloud_file.file_name}"
                            )
                        finally:
                            await bs_client.close()

                except Exception as e:
                    logger.error(
                        f"Failed to re-download cloud file for remediation: {e}"
                    )

    if not file_path or not os.path.exists(file_path):
        _best_effort_terminal_dispatch_failure(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type_value,
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            error_code="source_file_unavailable",
            total_issues=len(issues),
        )
        raise HTTPException(
            status_code=400,
            detail="Original file not available for remediation. Please re-upload and scan.",
        )

    # Determine document type and get appropriate remediator
    scan_type = scan.scan_type

    # Map scan type to remediator
    remediator_map = {
        ScanType.WORD: DocxRemediator,
        ScanType.EXCEL: XlsxRemediator,
        ScanType.PDF: PdfRemediator,
        ScanType.POWERPOINT: PptxRemediator,
        ScanType.LATEX: LatexRemediator,
        ScanType.MULTIMEDIA: MultimediaRemediator,
        ScanType.VIDEO: MultimediaRemediator,
    }

    # Special case: LaTeX scan with a PDF file should use PdfRemediator
    # (This happens when a PDF is uploaded to the LaTeX scanner for math-aware scanning)
    file_ext = Path(file_path).suffix.lower()
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        logger.info(f"LaTeX scan with PDF file - using PdfRemediator for {file_path}")
        RemediatorClass = PdfRemediator
    else:
        RemediatorClass = remediator_map.get(scan_type)

    # Special case: IMAGE scan — generate alt text via AI
    if scan_type == ScanType.IMAGE:
        generator = ImageAltTextGenerator(
            lms_client=(alt_text_tracker if alt_text_client is not None else None),
            allow_legacy_transport=lms_cloud_file is None,
        )
        if lms_cloud_file and alt_text_client is None:
            _best_effort_terminal_dispatch_failure(
                db=db,
                request=request,
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type_value,
                remediation_requested=remediation_requested,
                alt_text_requested=alt_text_requested,
                remediation_tracker=remediation_tracker,
                alt_text_tracker=alt_text_tracker,
                error_code="alt_text_not_requested",
                total_issues=len(issues),
            )
            raise HTTPException(
                status_code=400,
                detail="Image alt text requires an allowed LMS AI request",
            )
        original_status = scan.status
        original_outcome = scan.remediation_outcome
        try:
            try:
                analysis = await generator.analyze_image_comprehensive(
                    image_path=file_path,
                    context=f"Educational course content: {scan.file_name}",
                )
            finally:
                alt_text_tracker.observe_image_usage(
                    getattr(generator, "usage_metadata", None)
                )
            alt_text, is_decorative = _extract_validated_image_analysis(analysis)
        except Exception as error:
            db.rollback()
            scan.status = original_status
            scan.remediation_outcome = original_outcome
            try:
                _audit_terminal_remediation(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type.value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    successful=False,
                    total_issues=1,
                    fixed_count=0,
                    manual_count=0,
                    failed_count=1,
                    error=(
                        "invalid_provider_response"
                        if isinstance(error, _InvalidProviderResponseError)
                        else "remediation_exception"
                    ),
                    commit=True,
                )
            except Exception:
                logger.exception(
                    "Best-effort image remediation failure audit could not be persisted",
                    extra={"scan_id": scan_id},
                )
            raise HTTPException(
                status_code=500, detail="Remediation failed. Please try again."
            )

        success = bool(alt_text) or is_decorative

        try:
            scan.status = ScanStatus.COMPLETED if success else ScanStatus.FAILED
            scan.remediation_outcome = (
                RemediationOutcome.COMPLETED.value
                if success
                else RemediationOutcome.MANUAL_REQUIRED.value
            )
            _audit_terminal_remediation(
                db=db,
                request=request,
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type.value,
                remediation_requested=remediation_requested,
                alt_text_requested=alt_text_requested,
                remediation_tracker=remediation_tracker,
                alt_text_tracker=alt_text_tracker,
                successful=success,
                total_issues=1,
                fixed_count=1 if success else 0,
                manual_count=0 if success else 1,
                failed_count=0,
            )
            db.commit()
        except Exception:
            db.rollback()
            scan.status = original_status
            scan.remediation_outcome = original_outcome
            try:
                _audit_terminal_remediation(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type.value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    successful=False,
                    total_issues=1,
                    fixed_count=0,
                    manual_count=0,
                    failed_count=1,
                    error="remediation_exception",
                    commit=True,
                )
            except Exception:
                logger.exception(
                    "Best-effort image remediation failure audit could not be persisted",
                    extra={"scan_id": scan_id},
                )
            raise HTTPException(
                status_code=500, detail="Remediation failed. Please try again."
            )

        return {
            "success": success,
            "message": (
                "Image alt text generated"
                if alt_text
                else (
                    "Image classified as decorative"
                    if is_decorative
                    else "manual_required"
                )
            ),
            "fixed_count": 1 if success else 0,
            "manual_count": 0 if success else 1,
            "remediated_alt_text": alt_text,
            "is_decorative": is_decorative,
        }

    if not RemediatorClass:
        _best_effort_terminal_dispatch_failure(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type_value,
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            error_code="unsupported_scan_type",
            total_issues=len(issues),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Remediation not supported for scan type: {scan_type}",
        )

    original_status = scan.status
    original_outcome = scan.remediation_outcome
    original_cloud_remediated = (
        resolved_cloud_file.has_remediated_version if resolved_cloud_file else None
    )
    original_remediation_origin = (
        getattr(resolved_cloud_file, "remediation_origin", None)
        if resolved_cloud_file
        else None
    )
    artifact = None
    artifact_publication = None
    artifact_service = None
    artifact_temp_dir = None
    result = None

    try:
        # Configuration, partitioning, and persisted-input normalization are
        # fallible and therefore belong inside the audited transaction funnel.
        config = RemediationConfig(
            use_ai=(
                remediation_client is not None if lms_cloud_file else effective_use_ai
            ),
            allow_legacy_nested_ai=lms_cloud_file is None,
            fix_alt_text=(
                alt_text_client is not None if lms_cloud_file else effective_alt_text
            ),
            verify_fixes=True,
            create_backup=True,
            output_directory=str(Path(file_path).parent),
        )

        if scan_type == ScanType.LATEX:
            latex_formats = options.latex_formats if options else ["tex", "pdf", "html"]
            config.latex_output_formats = [
                OutputFormat(fmt)
                for fmt in latex_formats
                if fmt in ["tex", "pdf", "html"]
            ]

        if scan_type == ScanType.MULTIMEDIA and options:
            config.multimedia_output_format = OutputFormat(options.multimedia_format)
            config.include_original_in_zip = options.include_original_in_zip

        issues = _copy_validated_remediation_issues(issues)
        embedded_alt_manual = []
        if lms_cloud_file and scan_type != ScanType.IMAGE:
            issues, embedded_alt_manual = _partition_authoritative_document_issues(
                issues, partition_visual=alt_text_client is None
            )

        normalized_issues = _normalize_issues_for_remediation(issues)
        logger.info(
            f"Normalized {len(normalized_issues)} issues for remediation (scan {scan_id})"
        )

        # LMS-backed scans receive only their purpose-bound current-policy
        # client. Ordinary uploads retain the historical provider manager.
        if lms_cloud_file:
            ai_client = remediation_tracker if remediation_client is not None else None
            tracked_alt_text_client = (
                alt_text_tracker if alt_text_client is not None else None
            )
        else:
            manager = get_provider_manager()
            remediation_tracker.bind_client(manager)
            alt_text_tracker.bind_client(manager)
            ai_client = remediation_tracker
            # The same legacy manager remains underneath both wrappers, while
            # document remediators report the purpose that actually invoked it.
            tracked_alt_text_client = alt_text_tracker if alt_text_requested else None

        # Create remediator and run remediation
        remediator_kwargs = {
            "file_path": file_path,
            "issues": normalized_issues,
            "config": config,
            "ai_client": ai_client,
        }
        if RemediatorClass in {
            DocxRemediator,
            PptxRemediator,
            PdfRemediator,
            XlsxRemediator,
        }:
            remediator_kwargs["alt_text_client"] = tracked_alt_text_client
        remediator = RemediatorClass(**remediator_kwargs)

        result = remediator.remediate()
        pdf_claim_required = RemediatorClass is PdfRemediator
        if embedded_alt_manual:
            merge_partitioned_manual_issues(
                result,
                embedded_alt_manual,
                reason="alt_text_client_unavailable",
                purpose="manual_review",
            )

        successful_complete_result = (
            result.success is True
            and result.manual_count == 0
            and result.failed_count == 0
        )
        if (
            pdf_claim_required
            and successful_complete_result
            and result.has_output_claim() is not True
        ):
            scan.status = ScanStatus.FAILED
            scan.remediation_outcome = RemediationOutcome.ARTIFACT_UNAVAILABLE.value
            _audit_terminal_remediation(
                db=db,
                request=request,
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type_value,
                remediation_requested=remediation_requested,
                alt_text_requested=alt_text_requested,
                remediation_tracker=remediation_tracker,
                alt_text_tracker=alt_text_tracker,
                successful=False,
                total_issues=result.total_issues,
                fixed_count=0,
                manual_count=result.fixed_count,
                failed_count=result.failed_count,
                error="remediation_artifact_unavailable",
                commit=False,
            )
            db.commit()
            return {
                "success": False,
                "scan_id": scan_id,
                "error": "remediation_artifact_unavailable",
                "fixed_count": 0,
                "manual_count": result.fixed_count,
                "failed_count": result.failed_count,
                "artifact_id": None,
            }

        if successful_complete_result and result.fixed_count > 0:
            if pdf_claim_required:
                output_path = None
                output_available = result.has_output_claim()
            else:
                output_path = getattr(result, "output_file", None)
                output_available = bool(output_path and Path(output_path).is_file())
            if (
                not output_available
                or getattr(result, "verification_passed", None) is not True
            ):
                scan.status = ScanStatus.FAILED
                scan.remediation_outcome = RemediationOutcome.ARTIFACT_UNAVAILABLE.value
                _audit_terminal_remediation(
                    db=db,
                    request=request,
                    user_id=user_id,
                    department_id=department_id,
                    scan_id=scan_id,
                    file_type=scan_type_value,
                    remediation_requested=remediation_requested,
                    alt_text_requested=alt_text_requested,
                    remediation_tracker=remediation_tracker,
                    alt_text_tracker=alt_text_tracker,
                    successful=False,
                    total_issues=result.total_issues,
                    fixed_count=0,
                    manual_count=result.fixed_count,
                    failed_count=result.failed_count,
                    error="remediation_artifact_unavailable",
                    commit=False,
                )
                db.commit()
                return {
                    "success": False,
                    "scan_id": scan_id,
                    "error": "remediation_artifact_unavailable",
                    "fixed_count": 0,
                    "manual_count": result.fixed_count,
                    "failed_count": result.failed_count,
                    "artifact_id": None,
                }
            artifact_service = RemediationArtifactService.from_settings()
            publication_kwargs = {
                "department_id": str(department_id),
                "scan_id": str(scan.id),
                "cloud_file_id": (
                    str(resolved_cloud_file.id)
                    if resolved_cloud_file is not None
                    else None
                ),
                "remediation_job_id": None,
                "created_by_id": str(user_id) if user_id else None,
                "provider": (
                    str(resolved_cloud_file.provider)
                    if resolved_cloud_file is not None
                    else "local"
                ),
                "scan_type": scan.scan_type,
                "provider_result": {"verification_passed": True},
                "commit": False,
            }
            if pdf_claim_required:
                claim_metadata = result.output_claim_metadata()
                with result.open_output_stream() as source_stream:
                    published = artifact_service.claim_and_publish_stream(
                        db,
                        source_stream=source_stream,
                        filename=claim_metadata["filename"],
                        claimed_size_bytes=claim_metadata["size_bytes"],
                        claimed_sha256=claim_metadata["sha256"],
                        claimed_mime_type=claim_metadata["mime_type"],
                        claimed_filename=claim_metadata["filename"],
                        **publication_kwargs,
                    )
            else:
                artifact_temp_dir = tempfile.mkdtemp(prefix="aelira_direct_artifact_")
                artifact_source = Path(artifact_temp_dir) / Path(output_path).name
                shutil.copyfile(output_path, artifact_source)
                published = artifact_service.claim_and_publish(
                    db,
                    source_path=artifact_source,
                    trusted_temp_root=artifact_temp_dir,
                    filename=artifact_source.name,
                    **publication_kwargs,
                )
            if isinstance(published, ArtifactPublicationResult):
                artifact_publication = published
                artifact = published.artifact
            else:
                # Preserve compatibility with test doubles and older service
                # adapters; the production service always returns the typed claim.
                artifact = published
            if artifact_temp_dir:
                shutil.rmtree(artifact_temp_dir, ignore_errors=True)
                artifact_temp_dir = None

        # Direct and queued flows share one canonical, idempotent writer.
        persist_scan_fixes(db, scan_id, result.fixed_issues)

        import uuid as _uuid

        # Run Matterhorn only for a complete result with eligible output bytes.
        try:
            from ...education.validation.matterhorn import MatterhornValidator
            from ...db import models as _dbm

            if (
                pdf_claim_required
                and successful_complete_result
                and result.has_output_claim()
            ):
                claim_metadata = result.output_claim_metadata()
                with result.open_output_stream() as output_stream:
                    with _bounded_pdf_claim_validation_file(
                        output_stream,
                        claimed_size_bytes=claim_metadata["size_bytes"],
                        claimed_sha256=claim_metadata["sha256"],
                    ) as validation_path:
                        validator = MatterhornValidator()
                        mh_result = validator.validate(validation_path)
            elif successful_complete_result and not pdf_claim_required:
                output_path = result.output_file
                if (
                    output_path
                    and os.path.exists(output_path)
                    and output_path.endswith(".pdf")
                ):
                    validator = MatterhornValidator()
                    mh_result = validator.validate(output_path)
                else:
                    mh_result = None
            else:
                mh_result = None
            if mh_result is not None:
                for cp in mh_result.checkpoints:
                    db.add(
                        _dbm.MatterhornResult(
                            id=str(_uuid.uuid4()),
                            scan_id=scan_id,
                            checkpoint_id=cp.id,
                            checkpoint_name=_sanitize_str(cp.name),
                            status=cp.status.value,
                            severity=cp.severity,
                            details=_sanitize_str(cp.details),
                            page_number=cp.page_number,
                        )
                    )
                logger.info(
                    f"Matterhorn validation complete for {scan_id}: "
                    f"{mh_result.passed}/{mh_result.total} passed"
                )
        except Exception as mh_err:
            logger.warning(f"Matterhorn validation skipped for {scan_id}: {mh_err}")

        terminal_success = successful_complete_result
        if result.success is not True or result.failed_count > 0:
            scan.status = ScanStatus.FAILED
            scan.remediation_outcome = RemediationOutcome.REMEDIATION_FAILED.value
        elif result.manual_count > 0:
            scan.status = ScanStatus.FAILED
            scan.remediation_outcome = RemediationOutcome.MANUAL_REQUIRED.value
        elif result.fixed_count > 0:
            scan.status = ScanStatus.COMPLETED
            scan.remediation_outcome = RemediationOutcome.COMPLETED.value
        else:
            scan.status = ScanStatus.COMPLETED
            scan.remediation_outcome = RemediationOutcome.NO_OP.value

        # Fully materialize every response field before recording success or
        # committing. Enum/property/list failures must still roll back through
        # the single audited failure path.
        response_payload = {
            "success": terminal_success if pdf_claim_required else result.success,
            "scan_id": scan_id,
            "artifact_id": str(artifact.id) if artifact is not None else None,
            "artifact_mime_type": artifact.mime_type if artifact is not None else None,
            "artifact_size_bytes": (
                artifact.size_bytes if artifact is not None else None
            ),
            "artifact_sha256": artifact.sha256 if artifact is not None else None,
            "artifact_expires_at": (
                artifact.expires_at.isoformat() if artifact is not None else None
            ),
            "artifact_review_status": (
                artifact.review_status if artifact is not None else None
            ),
            "total_issues": result.total_issues,
            "fixed_count": result.fixed_count,
            "manual_count": result.manual_count,
            "failed_count": result.failed_count,
            "skipped_count": getattr(result, "skipped_count", 0),
            "original_score": result.original_compliance_score,
            "remediated_score": result.remediated_compliance_score,
            "improvement": result.improvement,
            "duration_seconds": result.duration_seconds,
            "fixed_issues": [
                {
                    "id": fix.issue_id,
                    "category": fix.category.value,
                    "severity": fix.severity.value,
                    "description": fix.description,
                    "fix_method": fix.fix_method,
                }
                for fix in result.fixed_issues
            ],
            "manual_issues": [
                {
                    "id": manual.issue_id,
                    "category": manual.category.value,
                    "severity": manual.severity.value,
                    "description": manual.description,
                    "reason": manual.reason,
                    "recommendation": manual.recommendation,
                }
                for manual in result.manual_issues
            ],
            "warnings": result.warnings,
        }
        # Match Starlette's strict JSON response constraints while rollback and
        # a single failure audit are still possible.
        json.dumps(
            response_payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

        _audit_terminal_remediation(
            db=db,
            request=request,
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type.value if scan_type else "unknown",
            remediation_requested=remediation_requested,
            alt_text_requested=alt_text_requested,
            remediation_tracker=remediation_tracker,
            alt_text_tracker=alt_text_tracker,
            successful=terminal_success,
            total_issues=result.total_issues,
            fixed_count=result.fixed_count,
            manual_count=result.manual_count,
            failed_count=result.failed_count,
            skipped_count=getattr(result, "skipped_count", 0),
            original_score=result.original_compliance_score,
            remediated_score=result.remediated_compliance_score,
            improvement=result.improvement,
            duration_seconds=result.duration_seconds,
            artifact_id=str(artifact.id) if artifact is not None else None,
            error=(
                "manual_review_required"
                if result.success is True and result.manual_count > 0
                else "remediation_failed"
            ),
        )

        # The managed artifact finalization already set the CloudFile pointer and
        # version flag under the same pending caller transaction.

        # The commit is the final fallible operation guarded by this handler.
        db.commit()

    except Exception as e:
        db.rollback()
        if artifact_publication is not None and artifact_service is not None:
            try:
                artifact_service.abort_staging(
                    db,
                    artifact_id=artifact_publication.artifact_id,
                    publication_token=artifact_publication.publication_token,
                )
            except Exception:
                db.rollback()
                logger.warning(
                    "Failed to clean aborted direct remediation artifact",
                    extra={"scan_id": scan_id},
                )
        if artifact_temp_dir:
            shutil.rmtree(artifact_temp_dir, ignore_errors=True)
        scan.status = original_status
        scan.remediation_outcome = original_outcome
        if resolved_cloud_file is not None:
            try:
                resolved_cloud_file.has_remediated_version = original_cloud_remediated
                resolved_cloud_file.remediation_origin = original_remediation_origin
            except Exception:
                logger.warning(
                    "Failed to restore in-memory CloudFile remediation status",
                    extra={"scan_id": scan_id},
                )
        logger.error(f"Remediation failed for scan {scan_id}: {e}", exc_info=True)
        # The failed transaction (including any success audit row) is gone.
        # Record one sanitized terminal failure in a fresh best-effort commit.
        try:
            failed_result = locals().get("result")
            _audit_terminal_remediation(
                db=db,
                request=request,
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type.value if scan_type else "unknown",
                remediation_requested=remediation_requested,
                alt_text_requested=alt_text_requested,
                remediation_tracker=remediation_tracker,
                alt_text_tracker=alt_text_tracker,
                successful=False,
                total_issues=getattr(failed_result, "total_issues", 0),
                fixed_count=getattr(failed_result, "fixed_count", 0),
                manual_count=getattr(failed_result, "manual_count", 0),
                failed_count=getattr(failed_result, "failed_count", 0),
                skipped_count=getattr(failed_result, "skipped_count", 0),
                error=(
                    "invalid_scan_result"
                    if isinstance(e, _InvalidScanResultError)
                    else "remediation_exception"
                ),
                commit=True,
            )
        except Exception:
            logger.exception(
                "Best-effort remediation failure audit could not be persisted",
                extra={"scan_id": scan_id},
            )
        raise HTTPException(
            status_code=500, detail="Remediation failed. Please try again."
        )

    finally:
        if result is not None:
            close_output_claim = getattr(result, "close_output_claim", None)
            if callable(close_output_claim):
                try:
                    close_output_claim()
                except Exception:
                    logger.warning(
                        "Failed to close direct remediation output claim",
                        extra={"scan_id": scan_id},
                    )

    # Once commit succeeds, returning the already-built value is intentionally
    # outside the rollback/failure-audit handler. Framework serialization cannot
    # produce a second contradictory terminal audit.
    return response_payload


# ==================== Code Remediation Endpoint ====================


@router.post("/code/remediate/{scan_id}")
def remediate_code_scan(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Remediate a code (HTML) scan using approved ScanFix records.

    This endpoint loads fixes that have been reviewed and approved (or
    auto-approved) for a code scan, converts them to the format expected
    by HtmlRemediator, runs the remediator, and returns a structured result.

    Only HTML files can be auto-remediated; CSS and JS files are not supported.
    """
    from ...education.remediation.html_remediator import HtmlRemediator
    from ...education.remediation.base import RemediationConfig

    _, user_id, department_id = principal.as_legacy_tuple()

    # 1. Verify scan exists and belongs to user's department
    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    # Must be a code scan
    scan_type_value = (
        scan.scan_type.value
        if hasattr(scan.scan_type, "value")
        else str(scan.scan_type)
    )
    if scan_type_value.upper() not in ("CODE",):
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint only supports code scans. Scan type is: {scan_type_value}",
        )

    # 2. Verify the file is HTML
    file_path = scan.storage_path
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=400,
            detail="Original file not available for remediation. Please re-upload and scan.",
        )

    file_ext = Path(file_path).suffix.lower()
    if file_ext not in (".html", ".htm"):
        raise HTTPException(
            status_code=400,
            detail=f"Only HTML files can be auto-remediated. File is: {file_ext}",
        )

    # 3. Query approved ScanFix records
    approved_fixes = (
        db.query(ScanFix)
        .filter(
            ScanFix.scan_id == scan_id,
            ScanFix.review_status.in_(list(APPROVED_REVIEW_STATUSES)),
        )
        .all()
    )

    if not approved_fixes:
        raise HTTPException(
            status_code=400,
            detail="No approved fixes to apply. Review and approve fixes first.",
        )

    # 4. Convert ScanFix -> issue dicts for HtmlRemediator
    issue_dicts = [_scanfix_to_issue_dict(fix) for fix in approved_fixes]

    logger.info(
        f"Code remediation for scan {scan_id}: {len(issue_dicts)} approved fixes",
        extra={"user_id": user_id, "department_id": department_id},
    )

    # 5. Initialize HtmlRemediator
    config = RemediationConfig(
        use_ai=False,  # We already have approved fixes; no AI needed
        verify_fixes=True,
        create_backup=True,
        output_directory=str(Path(file_path).parent),
    )

    try:
        remediator = HtmlRemediator(
            file_path=file_path,
            issues=issue_dicts,
            config=config,
            ai_client=None,
        )

        # 6. Run remediation
        result = remediator.remediate()

        # 7. Update ScanFix records — mark applied ones
        applied_issue_ids = {f.issue_id for f in result.fixed_issues}
        failed_issue_ids = {f.get("issue_id") for f in result.failed_issues}
        now = datetime.now(timezone.utc)

        for fix in approved_fixes:
            fix_issue_id = fix.issue_id or fix.id
            if fix_issue_id in applied_issue_ids:
                fix.review_status = "applied"
                fix.updated_at = now
            elif fix_issue_id in failed_issue_ids:
                fix.review_status = "apply_failed"
                fix.updated_at = now

        # 8. Commit
        db.commit()

        logger.info(
            f"Code remediation complete for scan {scan_id}: "
            f"{result.fixed_count} fixed, {result.manual_count} manual, "
            f"{result.failed_count} failed",
            extra={"user_id": user_id, "department_id": department_id},
        )

        # 9. Return structured result
        return {
            "success": result.success,
            "scan_id": scan_id,
            "fixes_applied": result.fixed_count,
            "fixes_failed": result.failed_count,
            "manual_fixes": result.manual_count,
            "output_file": result.output_file,
            "original_score": result.original_compliance_score,
            "remediated_score": result.remediated_compliance_score,
            "fixed_issues": [
                {
                    "id": f.issue_id,
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "description": f.description,
                    "fix_method": f.fix_method,
                }
                for f in result.fixed_issues
            ],
            "manual_issues": [
                {
                    "id": m.issue_id,
                    "category": m.category.value,
                    "severity": m.severity.value,
                    "description": m.description,
                    "reason": m.reason,
                }
                for m in result.manual_issues
            ],
            "warnings": result.warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Code remediation failed for scan {scan_id}: {e}",
            exc_info=True,
            extra={"user_id": user_id, "department_id": department_id},
        )
        raise HTTPException(
            status_code=500, detail="Code remediation failed. Please try again."
        )


@router.get("/scans/{scan_id}/remediated")
async def download_remediated_file(
    scan_id: str,
    request: Request,
    format: Optional[str] = None,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Download the remediated document for a scan.

    For LaTeX scans, use ?format=tex|pdf|html to select format.
    For Multimedia scans, returns ZIP if created, otherwise caption file.

    REQUIRES API KEY IN PRODUCTION
    """
    from fastapi.responses import FileResponse
    from ...utils.file_storage import get_remediated_file_path

    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    file_path = scan.storage_path
    if not file_path:
        raise HTTPException(status_code=404, detail="Original file not found")

    # Get base remediated path
    remediated_base = get_remediated_file_path(file_path)
    remediated_dir = Path(remediated_base).parent
    base_stem = Path(file_path).stem + "_remediated"

    # Determine which file to return based on scan type and format request
    scan_type = scan.scan_type
    file_ext = Path(file_path).suffix.lower()

    # Special case: LaTeX scan with a PDF file - treat as PDF download
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        target_path = Path(remediated_base)
        if not target_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Remediated PDF not found. Run remediation first.",
            )

    elif scan_type == ScanType.LATEX:
        # LaTeX (.tex file): support tex, pdf, html formats
        format_ext = (format or "tex").lower()
        if format_ext not in ["tex", "pdf", "html"]:
            format_ext = "tex"

        target_path = remediated_dir / f"{base_stem}.{format_ext}"

        if not target_path.exists():
            # Try original .tex if requested format not available
            target_path = Path(remediated_base)
            if not target_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Remediated {format_ext.upper()} not found. Run remediation with latex_formats=['{format_ext}']",
                )

    elif scan_type == ScanType.MULTIMEDIA:
        # Multimedia: check for ZIP first, then individual files
        zip_path = remediated_dir / f"{Path(file_path).stem}_accessible.zip"
        vtt_path = remediated_dir / f"{Path(file_path).stem}.vtt"
        transcript_path = remediated_dir / f"{Path(file_path).stem}_transcript.txt"

        if format == "zip" and zip_path.exists():
            target_path = zip_path
        elif format == "vtt" and vtt_path.exists():
            target_path = vtt_path
        elif format == "transcript" and transcript_path.exists():
            target_path = transcript_path
        elif zip_path.exists():
            target_path = zip_path
        elif vtt_path.exists():
            target_path = vtt_path
        elif transcript_path.exists():
            target_path = transcript_path
        else:
            raise HTTPException(
                status_code=404,
                detail="No remediated files found. Run remediation first.",
            )
    else:
        # Standard document types
        target_path = Path(remediated_base)
        if not target_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Remediated file not found. Please run remediation first.",
            )

    # Determine MIME type
    mime_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".tex": "application/x-tex",
        ".html": "text/html",
        ".zip": "application/zip",
        ".vtt": "text/vtt",
        ".txt": "text/plain",
    }

    suffix = target_path.suffix.lower()
    media_type = mime_types.get(suffix, "application/octet-stream")

    # Log download event
    from ...security.audit_service import AuditService

    AuditService(db).log_remediation_download(
        user_id=user_id,
        department_id=department_id,
        scan_id=scan_id,
        file_type=scan_type.value if scan_type else "unknown",
        format=format or suffix.lstrip("."),
        request=request,
    )

    return FileResponse(
        path=str(target_path),
        filename=target_path.name,
        media_type=media_type,
    )


@router.get("/scans/{scan_id}/remediated/formats")
async def list_remediated_formats(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    List available remediated file formats for a scan.

    Returns which formats are available for download.
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    file_path = scan.storage_path
    if not file_path:
        return {"available_formats": [], "message": "No file stored"}

    from ...utils.file_storage import get_remediated_file_path

    remediated_base = get_remediated_file_path(file_path)
    remediated_dir = Path(remediated_base).parent
    base_stem = Path(file_path).stem
    remediated_stem = f"{base_stem}_remediated"

    available = []
    scan_type = scan.scan_type
    file_ext = Path(file_path).suffix.lower()

    # Special case: LaTeX scan with a PDF file - treat as standard PDF
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        path = Path(remediated_base)
        if path.exists():
            available.append(
                {
                    "format": "pdf",
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/education/scans/{scan_id}/remediated",
                }
            )

    elif scan_type == ScanType.LATEX:
        # LaTeX .tex file: Check for each LaTeX output format
        for ext in ["tex", "pdf", "html"]:
            path = remediated_dir / f"{remediated_stem}.{ext}"
            if path.exists():
                available.append(
                    {
                        "format": ext,
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "download_url": f"/education/scans/{scan_id}/remediated?format={ext}",
                    }
                )

    elif scan_type == ScanType.MULTIMEDIA:
        # Check for multimedia outputs
        zip_path = remediated_dir / f"{base_stem}_accessible.zip"
        vtt_path = remediated_dir / f"{base_stem}.vtt"
        transcript_path = remediated_dir / f"{base_stem}_transcript.txt"
        ad_path = remediated_dir / f"{base_stem}_audio_descriptions.txt"

        for path, fmt in [
            (zip_path, "zip"),
            (vtt_path, "vtt"),
            (transcript_path, "transcript"),
            (ad_path, "audio_descriptions"),
        ]:
            if path.exists():
                available.append(
                    {
                        "format": fmt,
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "download_url": f"/education/scans/{scan_id}/remediated?format={fmt}",
                    }
                )
    else:
        # Standard document
        path = Path(remediated_base)
        if path.exists():
            available.append(
                {
                    "format": path.suffix.lstrip("."),
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/education/scans/{scan_id}/remediated",
                }
            )

    return {
        "scan_id": scan_id,
        "scan_type": scan_type.value,
        "available_formats": available,
        "remediation_complete": len(available) > 0,
    }


async def _batch_remediate_impl(
    scan_ids: List[str],
    use_ai: bool,
    background_tasks: BackgroundTasks,
    db: Session,
    principal: AuthenticatedPrincipal,
):
    """
    Batch remediate multiple scans.

    Starts remediation for multiple scans in the background.
    Returns immediately with a batch ID to track progress.
    REQUIRES API KEY IN PRODUCTION.
    REQUIRES: bulk_api feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    if not scan_ids:
        raise HTTPException(status_code=400, detail="No scan IDs provided")

    if len(scan_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 scans per batch")

    batch_id = str(uuid.uuid4())

    # Resolve and authorize the complete request before feature checks, tasks,
    # writes, or external clients. This makes mixed-scope batches atomic.
    valid_scans = []
    for scan_id in scan_ids:
        scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        authorize_scan_access(db, scan, principal)
        if not scan.result:
            raise HTTPException(
                status_code=400, detail="Scan has no results to remediate"
            )
        valid_scans.append(scan_id)

    # Batch remediation requires bulk_api feature.
    await require_feature(db, department_id, "bulk_api", "Batch Remediation")

    # Queue batch remediation in background
    # For now, return the plan - actual background processing would be added
    return {
        "success": True,
        "batch_id": batch_id,
        "total_scans": len(valid_scans),
        "scans_queued": valid_scans,
        "message": "Batch remediation queued. Check individual scan statuses for progress.",
        "note": "Batch background processing coming soon. For now, remediate individually.",
    }


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    # Check if llava model is available
    generator = ImageAltTextGenerator(allow_legacy_transport=True)
    vision_health = generator.health_check()

    return {
        "status": "healthy",
        "service": "education-api-v2",
        "database": "enabled",
        "vision_model": vision_health.get("vision_model"),
        "vision_available": vision_health.get("vision_available", False),
        "features": [
            "pdf-ocr",
            "pdf-remediation",
            "powerpoint-scanning",
            "latex-mathml",
            "database-storage",
            "scan-history",
            "ollama-aria-labels",
            "image-alt-text",
            "code-scanning",
            "compliance-dashboard",
            "auto-remediation",
            "focus-order-analysis",
            "cvd-analysis",
        ],
    }
