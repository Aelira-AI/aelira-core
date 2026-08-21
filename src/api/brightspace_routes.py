"""
D2L Brightspace REST API Routes

Handles OAuth 2.0 authentication and file operations with Brightspace LMS.

Market Impact: +15% US, +10% Australia (community colleges)
"""

import asyncio
import functools
import importlib
import os
import logging
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple, Literal
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
    CloudJobQueue,
    CloudJobType,
    CloudJobStatus,
    APIKey,
    Scan,
)
from ..integrations.brightspace import (
    get_brightspace_authorization_url,
    exchange_brightspace_code_for_token,
    BrightspaceAPIClient,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..api.auth_routes import get_current_api_key
from ..auth import verify_department_access
from ..auth.canvas_permissions import (
    require_account_management,
    require_lti_course_access,
)
from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..auth.redis_rate_limiter import OAuthStateManager
from ..ai.lms_remediation_client import LMSRemediationClient
from ..services.remediation_artifact_service import (
    ArtifactPublicationResult,
    RemediationArtifactService,
)
from ..utils.security import (
    PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
    require_brightspace_oauth_allowed_origin,
    require_persisted_brightspace_origin,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brightspace", tags=["brightspace"])

BRIGHTSPACE_ITEM_SIZE_LIMIT_BYTES = 25 * 1024 * 1024
BRIGHTSPACE_BATCH_SIZE_LIMIT_BYTES = 100 * 1024 * 1024
BRIGHTSPACE_WORKER_MAX_GLOBAL = 4
BRIGHTSPACE_WORKER_MAX_PER_DEPARTMENT = 1
_BRIGHTSPACE_WORKER_EXECUTOR = ThreadPoolExecutor(
    max_workers=BRIGHTSPACE_WORKER_MAX_GLOBAL,
    thread_name_prefix="brightspace-remediation",
)
_BRIGHTSPACE_WORKER_GLOBAL_SLOTS = asyncio.Semaphore(BRIGHTSPACE_WORKER_MAX_GLOBAL)
_BRIGHTSPACE_WORKER_DEPARTMENT_SLOTS: Dict[str, asyncio.Semaphore] = {}
BRIGHTSPACE_DOWNLOAD_EXTENSIONS = frozenset(
    {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "webp",
        "svg",
        "tiff",
        "docx",
        "doc",
        "pptx",
        "ppt",
        "xlsx",
        "xls",
        "pdf",
        "mp4",
        "mp3",
        "wav",
        "avi",
        "mov",
        "webm",
    }
)

# =============================================================================
# Request/Response Models
# =============================================================================


class BrightspaceConnectRequest(BaseModel):
    """Request to initiate Brightspace OAuth connection"""

    brightspace_instance_url: str = Field(
        ...,
        description="Brightspace instance URL (e.g., https://university.brightspace.com)",
    )
    department_id: Optional[str] = None
    redirect_uri: Optional[str] = Field(
        None,
        description="OAuth callback URL (optional, defaults to /brightspace/callback)",
    )


class BrightspaceConnectionStatus(BaseModel):
    """Brightspace connection status response"""

    connected: bool
    brightspace_instance_url: Optional[str] = None
    user_email: Optional[str] = None
    user_fullname: Optional[str] = None
    connected_at: Optional[datetime] = None
    credential_id: Optional[str] = None


class BrightspaceRemediateRequest(BaseModel):
    """Deprecated queue request retained only for a stable fail-closed response."""

    model_config = ConfigDict(extra="forbid")

    file_url: str = Field(..., description="Brightspace file URL")
    org_unit_id: StrictInt = Field(
        ..., gt=0, description="Brightspace course ID (OrgUnitId)"
    )
    department_id: str
    upload_back: StrictBool = False
    use_ai: StrictBool = False
    generate_alt_text: StrictBool = False


class BrightspaceContentRemediateRequest(BaseModel):
    """Explicit, purpose-separated intent for one tracked content item."""

    model_config = ConfigDict(extra="forbid")

    use_ai: StrictBool = False
    generate_alt_text: StrictBool = False


class BrightspaceBatchRemediateRequest(BrightspaceContentRemediateRequest):
    """Bounded explicit course batch; implicit course-wide mutation is forbidden."""

    org_unit_id: StrictInt = Field(gt=0)
    cloud_file_ids: List[str] = Field(min_length=1, max_length=20)

    @field_validator("cloud_file_ids")
    @classmethod
    def validate_cloud_file_ids(cls, values: List[str]) -> List[str]:
        import re

        if any(
            type(value) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
            for value in values
        ):
            raise ValueError("invalid cloud file id")
        return list(dict.fromkeys(values))


class RemediationOutcome(BaseModel):
    """Sanitized terminal state returned by synchronous Brightspace remediation."""

    cloud_file_id: str
    status: Literal["completed", "manual_required", "no_op", "failed"]
    fixed_count: int = Field(default=0, ge=0)
    manual_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    has_remediated_version: bool = False
    artifact_id: Optional[str] = None
    artifact_mime_type: Optional[str] = None
    artifact_size_bytes: Optional[int] = Field(default=None, ge=0)
    artifact_sha256: Optional[str] = None
    artifact_expires_at: Optional[datetime] = None
    artifact_review_status: Optional[str] = None
    ai_used: bool = False
    external_ai_used: bool = False
    providers: List[str] = Field(default_factory=list, max_length=2)
    purpose_decisions: Dict[str, str] = Field(default_factory=dict)
    error_code: Optional[str] = None


class BrightspaceBatchRemediateResponse(BaseModel):
    status: Literal["completed"] = "completed"
    requested_count: int
    completed_count: int
    manual_count: int
    failed_count: int
    fixed_count: int
    results: List[RemediationOutcome]


class BrightspaceRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str


class BrightspaceContentScanRequest(BaseModel):
    """Request to scan Brightspace course content"""

    org_unit_id: StrictInt = Field(
        ..., gt=0, description="Brightspace OrgUnit (course) ID"
    )
    scan_types: str = Field("both", description="files, html, or both")
    module_id: Optional[StrictInt] = Field(
        None, gt=0, description="Optional: scan only this module"
    )


class BrightspaceContentScanResponse(BaseModel):
    """Response from content scan request"""

    total_items: int
    jobs_queued: int
    skipped: int


# =============================================================================
# Helpers
# =============================================================================


async def _run_brightspace_worker(
    department_id: str, worker: Any, /, *args: Any, **kwargs: Any
) -> Any:
    """Run one bounded worker and await its real completion on cancellation.

    Remediators have no cooperative cancellation, so this makes no timeout
    claim. Count/byte budgets bound admission and provider calls retain their
    finite internal timeouts.
    """
    department_slot = _BRIGHTSPACE_WORKER_DEPARTMENT_SLOTS.setdefault(
        department_id,
        asyncio.Semaphore(BRIGHTSPACE_WORKER_MAX_PER_DEPARTMENT),
    )
    async with _BRIGHTSPACE_WORKER_GLOBAL_SLOTS, department_slot:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            _BRIGHTSPACE_WORKER_EXECUTOR,
            functools.partial(worker, *args, **kwargs),
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await asyncio.shield(future)
            raise


def _get_credential(db: Session, dept_id: str) -> CloudOAuthCredentials:
    """Get Brightspace OAuth credential for department."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
            CloudOAuthCredentials.department_id == dept_id,
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=404, detail="Brightspace not connected")
    return credential


async def _ensure_valid_token(credential: CloudOAuthCredentials, db: Session) -> str:
    """Refresh under the shared lock, then reload the exact active credential."""
    try:
        expected_origin = require_persisted_brightspace_origin(credential)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR
        ) from exc

    expected_id = credential.id
    expected_department = credential.department_id
    token_manager = OAuthTokenManager()
    try:
        await token_manager.refresh_if_expired(credential, db)
    except Exception as exc:
        logger.error("Failed to refresh Brightspace token: %s", type(exc).__name__)
        raise HTTPException(
            status_code=409,
            detail="Brightspace token expired and refresh failed. Please reconnect.",
        ) from exc

    fresh = db.get(CloudOAuthCredentials, expected_id, populate_existing=True)
    if (
        fresh is None
        or fresh.id != expected_id
        or fresh.department_id != expected_department
        or fresh.provider != CloudProvider.BRIGHTSPACE.value
        or fresh.is_active is not True
    ):
        raise HTTPException(
            status_code=409,
            detail="Brightspace credential is no longer active. Please reconnect.",
        )
    try:
        fresh_origin = require_persisted_brightspace_origin(fresh)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR
        ) from exc
    if fresh_origin != expected_origin:
        raise HTTPException(status_code=409, detail=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR)
    return token_manager.decrypt_token(fresh.access_token)


# =============================================================================
# OAuth Flow
# =============================================================================


@router.post("/connect")
async def connect_brightspace(
    request: BrightspaceConnectRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, str]:
    """
    Initiate Brightspace OAuth 2.0 flow.

    Requires account-management authorization before OAuth state creation.
    Returns authorization URL to redirect user to.
    """
    require_account_management(principal)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    try:
        instance_origin = require_brightspace_oauth_allowed_origin(
            request.brightspace_instance_url
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Generate secure CSRF state token with metadata (stored server-side with TTL)
    state = OAuthStateManager.create_state(
        metadata={
            "department_id": dept_id,
            "brightspace_instance_url": instance_origin,
            "provider": "brightspace",
        }
    )

    # Generate redirect URI
    redirect_uri = (
        request.redirect_uri
        or f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/brightspace/callback"
    )

    try:
        # Generate authorization URL
        auth_url = get_brightspace_authorization_url(
            brightspace_instance_url=instance_origin,
            redirect_uri=redirect_uri,
            state=state,
        )

        logger.info(
            f"Initiated Brightspace OAuth for department {request.department_id} at {instance_origin}"
        )

        return {
            "authorization_url": auth_url,
            "state": state,
        }

    except ValueError as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/callback")
async def brightspace_oauth_callback(
    code: str = Query(..., description="Authorization code from Brightspace"),
    state: str = Query(..., description="CSRF state token"),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """
    Handle Brightspace OAuth callback.

    Verifies state token, exchanges authorization code for access token, and stores credentials.
    """
    # Verify and consume state token (one-time use, expires after 10 minutes)
    is_valid, metadata = OAuthStateManager.verify_and_consume_state(state)
    if not is_valid or not metadata:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please restart the connection flow.",
        )

    # Extract metadata from verified state
    department_id = metadata.get("department_id")
    brightspace_instance_url = metadata.get("brightspace_instance_url")

    if not department_id or not brightspace_instance_url:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state metadata. Please restart the connection flow.",
        )

    try:
        brightspace_instance_url = require_brightspace_oauth_allowed_origin(
            brightspace_instance_url
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid OAuth state metadata"
        ) from exc

    token_manager = OAuthTokenManager()

    try:
        # Generate redirect URI (must match the one used in /connect)
        redirect_uri = (
            f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/brightspace/callback"
        )

        # Exchange code for token
        access_token, refresh_token, expires_at = (
            await exchange_brightspace_code_for_token(
                brightspace_instance_url=brightspace_instance_url,
                authorization_code=code,
                redirect_uri=redirect_uri,
            )
        )

        # Get user info
        api_client = BrightspaceAPIClient(
            brightspace_instance_url=brightspace_instance_url,
            access_token=access_token,
        )

        try:
            user_info = await api_client.get_whoami()

            # Check if credential already exists
            existing = (
                db.query(CloudOAuthCredentials)
                .filter(
                    CloudOAuthCredentials.department_id == department_id,
                    CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
                    CloudOAuthCredentials.provider_user_id == user_info.Identifier,
                )
                .first()
            )

            if existing:
                # Update existing credential
                existing.access_token = token_manager.encrypt_token(access_token)
                if refresh_token:
                    existing.refresh_token = token_manager.encrypt_token(refresh_token)
                existing.token_expires_at = expires_at
                existing.provider_metadata = {
                    "brightspace_instance_url": brightspace_instance_url,
                    "user_email": user_info.UniqueName,
                    "user_name": f"{user_info.FirstName} {user_info.LastName}",
                }
                existing.provider_email = user_info.UniqueName
                existing.provider_name = f"{user_info.FirstName} {user_info.LastName}"
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()

                logger.info(
                    f"Updated existing Brightspace credential for user {user_info.UniqueName}"
                )
            else:
                # Create new credential
                credential = CloudOAuthCredentials(
                    id=str(uuid.uuid4()),
                    department_id=department_id,
                    provider=CloudProvider.BRIGHTSPACE.value,
                    provider_user_id=user_info.Identifier,
                    provider_email=user_info.UniqueName,
                    provider_name=f"{user_info.FirstName} {user_info.LastName}",
                    provider_metadata={
                        "brightspace_instance_url": brightspace_instance_url,
                        "user_email": user_info.UniqueName,
                        "user_name": f"{user_info.FirstName} {user_info.LastName}",
                    },
                    access_token=token_manager.encrypt_token(access_token),
                    refresh_token=(
                        token_manager.encrypt_token(refresh_token)
                        if refresh_token
                        else None
                    ),
                    token_expires_at=expires_at,
                    scopes="core:*:* content:*:*",
                )

                db.add(credential)
                db.commit()

                logger.info(
                    f"Created new Brightspace credential for user {user_info.UniqueName}"
                )

            dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.example.com")
            return RedirectResponse(
                url=f"{dashboard_url}/integrations?brightspace=connected&email={user_info.UniqueName}",
            )

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Brightspace OAuth callback failed: {e}")
        dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.example.com")
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?brightspace=error&message={str(e)[:100]}",
        )


# =============================================================================
# Connection Status
# =============================================================================


@router.get("/status")
async def get_brightspace_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceConnectionStatus:
    """Get Brightspace connection status for a department.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        return BrightspaceConnectionStatus(connected=False)

    return BrightspaceConnectionStatus(
        connected=True,
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        user_email=credential.provider_metadata.get("user_email"),
        user_fullname=credential.provider_metadata.get("user_name"),
        connected_at=credential.created_at,
        credential_id=credential.id,
    )


# =============================================================================
# Course and Content Operations
# =============================================================================


@router.get("/courses")
async def list_brightspace_courses(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all Brightspace courses the user has access to.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    # Get courses
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        courses = await api_client.get_my_enrollments()

        return [
            {
                "OrgUnitId": course.OrgUnitId,
                "Name": course.Name,
                "Code": course.Code,
                "org_unit_id": course.OrgUnitId,
                "name": course.Name,
                "code": course.Code,
                "start_date": course.StartDate,
                "end_date": course.EndDate,
                "is_active": course.IsActive,
            }
            for course in courses
        ]

    finally:
        await api_client.close()


@router.get("/courses/{org_unit_id}/content")
async def list_brightspace_course_content(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all content modules in a Brightspace course.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    # Get course content
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        content_items = await api_client.get_course_content(org_unit_id)

        return [
            {
                "id": item.Id,
                "title": item.Title,
                "short_title": item.ShortTitle,
                "type": item.Type,
                "is_hidden": item.IsHidden,
                "is_locked": item.IsLocked,
            }
            for item in content_items
        ]

    finally:
        await api_client.close()


# =============================================================================
# Content Scanning & Remediation
# =============================================================================


@router.get("/courses/{org_unit_id}/files")
async def list_brightspace_course_files(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all scannable content items in a Brightspace course.

    Recursively walks the course content tree and returns files and HTML topics.

    Requires API key authentication.
    """
    department_id = api_key.department_id
    credential = _get_credential(db, department_id)

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        scannables = await api_client.get_course_content_recursive(org_unit_id)

        return [
            {
                "topic_id": item.topic_id,
                "org_unit_id": item.org_unit_id,
                "module_id": item.module_id,
                "title": item.title,
                "content_type": item.content_type,
                "url": item.url,
                "module_path": item.module_path,
            }
            for item in scannables
        ]

    finally:
        await api_client.close()


async def _brightspace_scan_file_task(
    job_id: str, cloud_file_id: str, credential_id: str
):
    """Background task to scan and auto-remediate a file from Brightspace LMS."""
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.cloud_scan_job import handle_scan_job

    logger.info(f"Starting Brightspace scan: job={job_id}, file={cloud_file_id}")

    with _get_db_ctx() as db:
        job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()
        if not job:
            logger.error(f"Scan job not found: {job_id}")
            return

        try:
            job.status = CloudJobStatus.PROCESSING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            db.commit()

            token_manager = OAuthTokenManager()
            result = await handle_scan_job(job, db, token_manager)

            job.status = CloudJobStatus.COMPLETED.value
            job.progress = 100
            job.result_data = result
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Brightspace scan complete: job={job_id}, "
                f"score={result.get('compliance_score')}"
            )

            # Store content_body reference for later remediation
            # (remediation is triggered separately by the user)

        except Exception as e:
            logger.error(f"Brightspace scan failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.error_message = str(e)[:500]
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


def _convert_axe_issues(axe_violations: list) -> list:
    """Convert axe-core violations to the format remediators expect."""
    from ..education.remediation.category_mapper import (
        wcag_criterion_to_category,
        impact_to_severity,
    )
    import re

    converted = []
    for violation in axe_violations:
        # Extract WCAG criterion from tags (e.g., "wcag131" → "1.3.1")
        category = "other"
        wcag = None
        for tag in violation.get("tags", []):
            match = re.match(r"wcag(\d)(\d)(\d+)", tag)
            if match:
                wcag = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
                category = wcag_criterion_to_category(wcag)
                break

        # Map axe rule IDs to categories as fallback
        axe_id = violation.get("id", "")
        _axe_id_to_category = {
            "empty-heading": "heading",
            "heading-order": "heading",
            "image-alt": "alt_text",
            "input-image-alt": "alt_text",
            "role-img-alt": "alt_text",
            "aria-required-children": "aria",
            "aria-required-parent": "aria",
            "aria-roles": "aria",
            "aria-valid-attr": "aria",
            "nested-interactive": "aria",
            "color-contrast": "contrast",
            "html-has-lang": "language",
            "html-lang-valid": "language",
            "label": "form",
            "link-name": "link",
            "list": "list",
            "listitem": "list",
            "table-fake-caption": "table",
            "td-headers-attr": "table",
            "document-title": "title",
        }
        if category == "other":
            category = _axe_id_to_category.get(axe_id, "other")

        severity = impact_to_severity(violation.get("impact", "moderate"))

        for node in violation.get("nodes", []):
            converted.append(
                {
                    "id": axe_id,
                    "category": category,
                    "type": category,
                    "severity": severity,
                    "description": violation.get("description", ""),
                    "message": violation.get("help", ""),
                    "element": node.get("html", ""),
                    "element_type": (
                        node.get("html", "").split("<")[1].split(" ")[0].split(">")[0]
                        if "<" in node.get("html", "")
                        else ""
                    ),
                    "location": ", ".join(node.get("target", [])),
                    "wcag_criteria": wcag,
                    "fix_suggestion": node.get("failureSummary", ""),
                    "original_content": node.get("html", ""),
                    "metadata": {
                        "element_xpath": ", ".join(node.get("target", [])),
                        "html": node.get("html", ""),
                        "axe_id": axe_id,
                        "impact": violation.get("impact", ""),
                    },
                }
            )
    return converted


async def _client_for_fresh_credential(
    db: Session,
    *,
    credential_id: str,
    department_id: str,
) -> Tuple[CloudOAuthCredentials, BrightspaceAPIClient]:
    """Reload and validate the exact active credential before any token use."""
    credential = db.get(CloudOAuthCredentials, credential_id, populate_existing=True)
    if (
        credential is None
        or credential.id != credential_id
        or credential.department_id != department_id
        or credential.provider != CloudProvider.BRIGHTSPACE.value
        or credential.is_active is not True
    ):
        raise HTTPException(status_code=404, detail="Content item not found")
    metadata = credential.provider_metadata
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=404, detail="Content item not found")
    try:
        instance_url = require_persisted_brightspace_origin(metadata)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR
        ) from exc
    access_token = await _ensure_valid_token(credential, db)
    return credential, BrightspaceAPIClient(
        brightspace_instance_url=instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )


def _validate_brightspace_file_scope(
    cloud_file: CloudFile,
    *,
    department_id: str,
    org_unit_id: int,
) -> bool:
    """Defensively validate every trusted relation used by remediation."""
    metadata = cloud_file.provider_metadata
    return bool(
        cloud_file.id
        and cloud_file.department_id == department_id
        and cloud_file.provider == CloudProvider.BRIGHTSPACE.value
        and cloud_file.credential_id
        and cloud_file.provider_parent_id == str(org_unit_id)
        and isinstance(metadata, dict)
        and type(metadata.get("org_unit_id")) is int
        and metadata["org_unit_id"] == org_unit_id
        and isinstance(cloud_file.provider_file_id, str)
        and cloud_file.provider_file_id.isdigit()
    )


class _PurposeUsageTracker:
    """Track bounded transport facts for one injected Brightspace AI purpose."""

    _TRACKED_METHODS = frozenset(
        {"generate_text_sync", "generate_code_sync", "analyze_image_sync"}
    )
    _KNOWN_PROVIDERS = frozenset(
        {"anthropic", "gemini", "local", "ollama", "openai", "xai"}
    )
    _LOCAL_PROVIDERS = frozenset({"local", "ollama"})
    _OUTCOME_PRECEDENCE = {
        "allowed_not_used": 0,
        "denied_at_dispatch": 1,
        "attempted_failed": 2,
        "used": 3,
    }

    def __init__(self, client: Any, *, purpose: str):
        self.client = client
        self.purpose = purpose
        self.outcome = "allowed_not_used"
        self.ai_used = False
        self.external_ai_used = False
        self.provider_used: Optional[str] = None

    @property
    def provider(self) -> Any:
        return getattr(self.client, "provider", None)

    @classmethod
    def _safe_provider(cls, value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        provider = value.casefold()
        return provider if provider in cls._KNOWN_PROVIDERS else None

    def _record_provider(self) -> None:
        provider = self._safe_provider(getattr(self.client, "provider", None))
        if provider is not None:
            self.provider_used = provider

    def _promote_outcome(self, outcome: str) -> None:
        if self._OUTCOME_PRECEDENCE[outcome] > self._OUTCOME_PRECEDENCE[self.outcome]:
            self.outcome = outcome

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.client, name)
        if name not in self._TRACKED_METHODS or not callable(target):
            return target

        def tracked(*args: Any, **kwargs: Any) -> Any:
            self._record_provider()
            try:
                result = target(*args, **kwargs)
            except Exception:
                self.external_ai_used = self.provider_used not in self._LOCAL_PROVIDERS
                self._promote_outcome("attempted_failed")
                raise

            if (
                isinstance(result, dict)
                and result.get("success") is False
                and result.get("ai_used") is False
                and result.get("external_ai_used") is False
                and result.get("purpose_outcome") == "denied_at_dispatch"
            ):
                self._promote_outcome("denied_at_dispatch")
                return result
            if isinstance(result, dict) and result.get("success") is True:
                self.ai_used = True
                self.external_ai_used = self.provider_used not in self._LOCAL_PROVIDERS
                self._promote_outcome("used")
            else:
                self.external_ai_used = self.provider_used not in self._LOCAL_PROVIDERS
                self._promote_outcome("attempted_failed")
            return result

        return tracked


def _usage_fields(
    remediation_client: Any,
    alt_text_client: Any,
    decisions: Dict[str, str],
) -> Dict[str, Any]:
    """Derive the response's complete usage statement from both purposes."""
    trackers = [
        tracker
        for tracker in (remediation_client, alt_text_client)
        if isinstance(tracker, _PurposeUsageTracker)
    ]
    purpose_decisions = dict(decisions)
    for tracker in trackers:
        purpose_decisions[tracker.purpose] = tracker.outcome
    providers = list(
        dict.fromkeys(
            tracker.provider_used
            for tracker in trackers
            if tracker.provider_used is not None
        )
    )[:2]
    return {
        "ai_used": any(tracker.ai_used for tracker in trackers),
        "external_ai_used": any(tracker.external_ai_used for tracker in trackers),
        "providers": providers,
        "purpose_decisions": purpose_decisions,
    }


async def _authorize_brightspace_files(
    *,
    db: Session,
    principal: AuthenticatedPrincipal,
    org_unit_id: int,
    cloud_file_ids: List[str],
) -> List[CloudFile]:
    """Resolve the entire graph and prove topic membership before policy checks."""
    requested_ids = list(dict.fromkeys(cloud_file_ids))
    rows = (
        db.query(CloudFile)
        .filter(
            CloudFile.id.in_(requested_ids),
            CloudFile.department_id == principal.department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .all()
    )
    if (
        len(rows) != len(requested_ids)
        or {str(row.id) for row in rows} != set(requested_ids)
        or any(
            not _validate_brightspace_file_scope(
                row,
                department_id=principal.department_id,
                org_unit_id=org_unit_id,
            )
            for row in rows
        )
        or len({row.credential_id for row in rows}) != 1
    ):
        raise HTTPException(status_code=404, detail="Content item not found")

    try:
        require_lti_course_access(principal, str(org_unit_id))
    except HTTPException:
        raise HTTPException(status_code=404, detail="Content item not found") from None

    credential_id = rows[0].credential_id
    _, inventory_client = await _client_for_fresh_credential(
        db,
        credential_id=credential_id,
        department_id=principal.department_id,
    )
    try:
        inventory = await inventory_client.get_course_content_recursive(org_unit_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found") from None
    finally:
        await inventory_client.close()

    inventory_topic_ids = {
        str(item.topic_id)
        for item in inventory
        if getattr(item, "org_unit_id", None) == org_unit_id
    }
    if any(row.provider_file_id not in inventory_topic_ids for row in rows):
        raise HTTPException(status_code=404, detail="Content item not found")

    by_id = {str(row.id): row for row in rows}
    return [by_id[value] for value in requested_ids]


def _bind_brightspace_clients(
    *,
    principal: AuthenticatedPrincipal,
    cloud_file: CloudFile,
    intent: BrightspaceContentRemediateRequest,
) -> Tuple[Any, Any, Dict[str, str]]:
    """Independently bind only explicitly requested current policy clients."""
    remediation_client = None
    alt_text_client = None
    decisions = {"remediation": "not_requested", "alt_text": "not_requested"}
    binding = {
        "department_id": principal.department_id,
        "actor_id": principal.user_id,
        "cloud_file_id": str(cloud_file.id),
    }
    if intent.use_ai is True:
        remediation_client = LMSRemediationClient.bind_if_allowed(
            purpose="remediation", **binding
        )
        if remediation_client is None:
            raise HTTPException(
                status_code=403, detail="LMS AI remediation is not permitted"
            )
        remediation_client = _PurposeUsageTracker(
            remediation_client, purpose="remediation"
        )
        decisions["remediation"] = "allowed_not_used"
    if intent.generate_alt_text is True:
        alt_text_client = LMSRemediationClient.bind_if_allowed(
            purpose="alt_text", **binding
        )
        decisions["alt_text"] = (
            "allowed_not_used" if alt_text_client is not None else "manual_required"
        )
        if alt_text_client is not None:
            alt_text_client = _PurposeUsageTracker(alt_text_client, purpose="alt_text")
    return remediation_client, alt_text_client, decisions


def _bounded_count(value: object) -> int:
    return value if type(value) is int and 0 <= value <= 1_000_000 else 0


def _brightspace_file_extension(cloud_file: CloudFile) -> str:
    metadata = cloud_file.provider_metadata
    candidates = []
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("file_name"), metadata.get("url")])
    candidates.append(getattr(cloud_file, "file_name", ""))
    fallback = ""
    for source_name in candidates:
        if not isinstance(source_name, str):
            continue
        ext = source_name.rsplit(".", 1)[-1].lower().split("?", 1)[0]
        fallback = fallback or ext
        if ext in BRIGHTSPACE_DOWNLOAD_EXTENSIONS or ext in {"html", "htm"}:
            return ext
    return fallback


def _is_inline_html_content(cloud_file: CloudFile, ext: str) -> bool:
    if ext in {"html", "htm"}:
        return True
    metadata = cloud_file.provider_metadata
    if isinstance(metadata, dict) and metadata.get("topic_type") == "file":
        return False
    return ext not in BRIGHTSPACE_DOWNLOAD_EXTENSIONS and isinstance(
        getattr(cloud_file, "content_body", None), str
    )


def _preflight_brightspace_file_sizes(
    cloud_files: List[CloudFile],
) -> Dict[str, str]:
    """Return item-level manual outcomes after enforcing the aggregate cap."""
    manual: Dict[str, str] = {}
    aggregate_size = 0
    for cloud_file in cloud_files:
        file_id = str(cloud_file.id)
        ext = _brightspace_file_extension(cloud_file)
        content_body = getattr(cloud_file, "content_body", None)
        if _is_inline_html_content(cloud_file, ext):
            if not isinstance(content_body, str) or not content_body:
                manual[file_id] = "content_size_unknown"
                continue
            trusted_size = len(content_body.encode("utf-8"))
        else:
            trusted_size = getattr(cloud_file, "file_size_bytes", None)
            if type(trusted_size) is not int or trusted_size <= 0:
                manual[file_id] = "content_size_unknown"
                continue
        aggregate_size += trusted_size
        if trusted_size > BRIGHTSPACE_ITEM_SIZE_LIMIT_BYTES:
            manual[file_id] = "content_too_large"
            continue

    if aggregate_size > BRIGHTSPACE_BATCH_SIZE_LIMIT_BYTES:
        raise HTTPException(
            status_code=413, detail="brightspace_batch_size_limit_exceeded"
        )
    return manual


@dataclass(frozen=True)
class _WorkerRemediationResult:
    result: Any
    remediated_text: Optional[str] = None
    remediated_bytes: Optional[bytes] = None


def _run_remediator_worker(
    *,
    ext: str,
    raw_issues: List[Dict[str, Any]],
    config: Any,
    remediation_client: Any,
    alt_text_client: Any = None,
    source_text: Optional[str] = None,
    source_bytes: Optional[bytes] = None,
) -> _WorkerRemediationResult:
    """Construct, run, verify, and read one remediator without ORM state."""
    remediator_map = {
        "docx": ("..education.remediation.docx_remediator", "DocxRemediator"),
        "doc": ("..education.remediation.docx_remediator", "DocxRemediator"),
        "pptx": ("..education.remediation.pptx_remediator", "PptxRemediator"),
        "ppt": ("..education.remediation.pptx_remediator", "PptxRemediator"),
        "xlsx": ("..education.remediation.xlsx_remediator", "XlsxRemediator"),
        "xls": ("..education.remediation.xlsx_remediator", "XlsxRemediator"),
        "pdf": ("..education.remediation.pdf_remediator", "PdfRemediator"),
        "mp4": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
        "mp3": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
        "wav": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
        "avi": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
        "mov": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
        "webm": (
            "..education.remediation.multimedia_remediator",
            "MultimediaRemediator",
        ),
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, f"source.{ext}")
        if source_text is not None:
            with open(file_path, "w", encoding="utf-8") as source:
                source.write(source_text)
            from ..education.remediation.html_remediator import HtmlRemediator

            remediator = HtmlRemediator(
                file_path, raw_issues, config, remediation_client
            )
        elif source_bytes is not None and ext in remediator_map:
            with open(file_path, "wb") as source:
                source.write(source_bytes)
            module_path, class_name = remediator_map[ext]
            remediator_class = getattr(
                importlib.import_module(module_path, package="src.api"), class_name
            )
            try:
                remediator = remediator_class(
                    file_path,
                    raw_issues,
                    config,
                    remediation_client,
                    alt_text_client=alt_text_client,
                )
            except TypeError:
                remediator = remediator_class(
                    file_path, raw_issues, config, remediation_client
                )
        else:
            raise ValueError("unsupported_remediation_worker_input")

        result = remediator.remediate()
        output_path = getattr(result, "output_file", None)
        complete = (
            getattr(result, "success", None) is True
            and _bounded_count(getattr(result, "fixed_count", 0)) > 0
            and _bounded_count(getattr(result, "manual_count", 0)) == 0
            and _bounded_count(getattr(result, "failed_count", 0)) == 0
            and getattr(result, "verification_passed", None) is True
        )
        if not complete or not output_path or not os.path.isfile(output_path):
            return _WorkerRemediationResult(result=result)
        if source_text is not None:
            with open(output_path, "r", encoding="utf-8") as output:
                return _WorkerRemediationResult(
                    result=result, remediated_text=output.read()
                )
        with open(output_path, "rb") as output:
            return _WorkerRemediationResult(
                result=result, remediated_bytes=output.read()
            )


def _persist_remediated_bytes(path: str, content: bytes) -> bool:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as output:
        output.write(content)
    return os.path.isfile(path)


async def _remediate_file_impl(
    cloud_file: CloudFile,
    db: Session,
    *,
    remediation_client: Any = None,
    alt_text_client: Any = None,
    api_client: BrightspaceAPIClient,
    purpose_decisions: Optional[Dict[str, str]] = None,
) -> RemediationOutcome:
    """Synchronously remediate using injected purpose-bound clients only."""
    import html

    from ..db.models import ScanResult
    from ..education.remediation.base import RemediationConfig

    decisions = dict(
        purpose_decisions
        or {"remediation": "not_requested", "alt_text": "not_requested"}
    )
    scan_result = (
        db.query(ScanResult)
        .filter(ScanResult.scan_id == cloud_file.last_scan_id)
        .first()
    )
    raw_issues = scan_result.issues if scan_result is not None else None
    if not isinstance(raw_issues, list) or not raw_issues:
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="no_op",
            purpose_decisions=decisions,
        )

    metadata = cloud_file.provider_metadata
    if not isinstance(metadata, dict):
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="failed",
            failed_count=len(raw_issues),
            purpose_decisions=decisions,
            error_code="invalid_content_scope",
        )
    size_errors = _preflight_brightspace_file_sizes([cloud_file])
    if str(cloud_file.id) in size_errors:
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="manual_required",
            manual_count=len(raw_issues),
            purpose_decisions=decisions,
            error_code=size_errors[str(cloud_file.id)],
        )
    ext = _brightspace_file_extension(cloud_file)
    config = RemediationConfig(
        use_ai=remediation_client is not None,
        allow_legacy_nested_ai=False,
        verify_fixes=True,
        create_backup=False,
        fix_alt_text=alt_text_client is not None,
    )
    result = None
    durable_output = False
    artifact = None
    artifact_publication = None

    if _is_inline_html_content(cloud_file, ext):
        if not isinstance(cloud_file.content_body, str) or not cloud_file.content_body:
            return RemediationOutcome(
                cloud_file_id=str(cloud_file.id),
                status="manual_required",
                manual_count=len(raw_issues),
                purpose_decisions=decisions,
                error_code="manual_required",
            )
        from ..education.canvas_content_scanner import (
            _sanitize_html,
            _unwrap_html_fragment,
            _wrap_html_fragment,
        )

        worker_result = await _run_brightspace_worker(
            str(getattr(cloud_file, "department_id", "unknown")),
            _run_remediator_worker,
            ext="html",
            raw_issues=_convert_axe_issues(raw_issues),
            config=config,
            remediation_client=remediation_client,
            source_text=_wrap_html_fragment(cloud_file.content_body),
        )
        result = worker_result.result
        if worker_result.remediated_text is not None:
            cloud_file.remediated_body = _sanitize_html(
                _unwrap_html_fragment(worker_result.remediated_text)
            )
            durable_output = bool(cloud_file.remediated_body)
    elif ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff"}:
        if alt_text_client is None:
            return RemediationOutcome(
                cloud_file_id=str(cloud_file.id),
                status="manual_required",
                manual_count=len(raw_issues),
                purpose_decisions=decisions,
                error_code="alt_text_manual_required",
            )
        from ..education.image_alt_text import ImageAltTextGenerator

        temp_path = None
        try:
            file_bytes, _ = await api_client.get_topic_file(
                metadata["org_unit_id"], int(cloud_file.provider_file_id)
            )
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(file_bytes)
                temp_path = tmp.name
            image_result = await ImageAltTextGenerator(
                lms_client=alt_text_client, allow_legacy_transport=False
            ).analyze_image_comprehensive(
                image_path=temp_path,
                context="Educational course content",
            )
            description = image_result.get("description")
            alt_text = (
                description.get("alt_text") if isinstance(description, dict) else None
            )
            if not isinstance(alt_text, str) or not alt_text.strip():
                return RemediationOutcome(
                    cloud_file_id=str(cloud_file.id),
                    status="manual_required",
                    manual_count=len(raw_issues),
                    purpose_decisions={**decisions, "alt_text": "attempted_failed"},
                    error_code="alt_text_manual_required",
                    ai_used=True,
                    external_ai_used=getattr(alt_text_client, "provider", None)
                    != "ollama",
                    providers=[getattr(alt_text_client, "provider", "unknown")][:1],
                )
            if len(raw_issues) != 1:
                return RemediationOutcome(
                    cloud_file_id=str(cloud_file.id),
                    status="manual_required",
                    manual_count=len(raw_issues),
                    purpose_decisions=decisions,
                    error_code="manual_required",
                )
            cloud_file.remediated_body = (
                f'<img src="" alt="{html.escape(alt_text.strip(), quote=True)}" />'
            )
            durable_output = True
            result = type(
                "ImageResult",
                (),
                {
                    "success": True,
                    "fixed_count": 1,
                    "manual_count": 0,
                    "failed_count": 0,
                    "verification_passed": True,
                },
            )()
            decisions["alt_text"] = "used"
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    elif ext in {
        "docx",
        "doc",
        "pptx",
        "ppt",
        "xlsx",
        "xls",
        "pdf",
        "mp4",
        "mp3",
        "wav",
        "avi",
        "mov",
        "webm",
    }:
        file_bytes, _ = await api_client.get_topic_file(
            metadata["org_unit_id"], int(cloud_file.provider_file_id)
        )
        worker_result = await _run_brightspace_worker(
            str(getattr(cloud_file, "department_id", "unknown")),
            _run_remediator_worker,
            ext=ext,
            raw_issues=raw_issues,
            config=config,
            remediation_client=remediation_client,
            alt_text_client=alt_text_client,
            source_bytes=file_bytes,
        )
        result = worker_result.result
        complete = (
            getattr(result, "success", None) is True
            and _bounded_count(getattr(result, "fixed_count", 0)) > 0
            and _bounded_count(getattr(result, "manual_count", 0)) == 0
            and _bounded_count(getattr(result, "failed_count", 0)) == 0
            and getattr(result, "verification_passed", None) is True
        )
        if (
            complete
            and worker_result.remediated_bytes is not None
            and ext
            in {
                "docx",
                "pptx",
                "xlsx",
                "pdf",
            }
        ):
            with tempfile.TemporaryDirectory(
                prefix="aelira_brightspace_artifact_"
            ) as artifact_temp_dir:
                artifact_path = os.path.join(artifact_temp_dir, f"remediated.{ext}")
                durable_output = await _run_brightspace_worker(
                    str(getattr(cloud_file, "department_id", "unknown")),
                    _persist_remediated_bytes,
                    artifact_path,
                    worker_result.remediated_bytes,
                )
                if durable_output:
                    artifact = (
                        RemediationArtifactService.from_settings().claim_and_publish(
                            db,
                            source_path=artifact_path,
                            trusted_temp_root=artifact_temp_dir,
                            department_id=str(cloud_file.department_id),
                            scan_id=str(cloud_file.last_scan_id),
                            cloud_file_id=str(cloud_file.id),
                            remediation_job_id=None,
                            created_by_id=None,
                            provider=CloudProvider.BRIGHTSPACE.value,
                            scan_type={
                                "docx": "WORD",
                                "pptx": "POWERPOINT",
                                "xlsx": "EXCEL",
                                "pdf": "PDF",
                            }[ext],
                            filename=f"remediated.{ext}",
                            provider_result={"verification_passed": True},
                            commit=False,
                        )
                    )
                    if isinstance(artifact, ArtifactPublicationResult):
                        artifact_publication = artifact
                    durable_output = artifact.lifecycle_status == "available"
    else:
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="manual_required",
            manual_count=len(raw_issues),
            purpose_decisions=decisions,
            error_code="manual_required",
        )

    if result is None:
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="failed",
            failed_count=len(raw_issues),
            purpose_decisions=decisions,
            error_code="remediation_failed",
        )
    fixed = _bounded_count(getattr(result, "fixed_count", 0))
    manual = _bounded_count(getattr(result, "manual_count", 0))
    failed = _bounded_count(getattr(result, "failed_count", 0))
    cloud_file.has_remediated_version = bool(durable_output and fixed > 0)
    cloud_file.remediated_issues_fixed = fixed
    cloud_file.remediated_issues_remaining = manual + failed
    cloud_file.writeback_status = (
        "pending_review" if cloud_file.has_remediated_version else None
    )
    if cloud_file.has_remediated_version is True:
        try:
            db.commit()
        except Exception:
            db.rollback()
            if artifact_publication is not None:
                try:
                    RemediationArtifactService.from_settings().abort_staging(
                        db,
                        artifact_id=artifact_publication.artifact_id,
                        publication_token=artifact_publication.publication_token,
                    )
                except Exception:
                    db.rollback()
                    logger.warning(
                        "Failed to clean aborted Brightspace artifact",
                    )
            return RemediationOutcome(
                cloud_file_id=str(cloud_file.id),
                status="failed",
                failed_count=max(1, fixed),
                purpose_decisions=decisions,
                error_code="remediation_failed",
            )
    if cloud_file.has_remediated_version is True:
        status = "completed"
    elif getattr(result, "success", None) is not True or failed > 0:
        status = "failed"
    else:
        status = "manual_required"
    return RemediationOutcome(
        cloud_file_id=str(cloud_file.id),
        status=status,
        fixed_count=fixed if cloud_file.has_remediated_version else 0,
        manual_count=(
            manual + fixed if not cloud_file.has_remediated_version else manual
        ),
        failed_count=failed,
        has_remediated_version=bool(cloud_file.has_remediated_version),
        artifact_id=str(artifact.id) if artifact is not None else None,
        artifact_mime_type=artifact.mime_type if artifact is not None else None,
        artifact_size_bytes=artifact.size_bytes if artifact is not None else None,
        artifact_sha256=artifact.sha256 if artifact is not None else None,
        artifact_expires_at=artifact.expires_at if artifact is not None else None,
        artifact_review_status=artifact.review_status if artifact is not None else None,
        ai_used=decisions.get("alt_text") == "used",
        external_ai_used=(
            decisions.get("alt_text") == "used"
            and getattr(alt_text_client, "provider", None) != "ollama"
        ),
        providers=(
            [getattr(alt_text_client, "provider")]
            if decisions.get("alt_text") == "used"
            and isinstance(getattr(alt_text_client, "provider", None), str)
            else []
        ),
        purpose_decisions=decisions,
        error_code=None if status == "completed" else "manual_required",
    )


async def _remediate_file(
    cloud_file: CloudFile,
    db: Session,
    *,
    remediation_client: Any = None,
    alt_text_client: Any = None,
    api_client: BrightspaceAPIClient,
    purpose_decisions: Optional[Dict[str, str]] = None,
) -> RemediationOutcome:
    """Run remediation and overlay usage facts observed by both trackers."""
    decisions = dict(
        purpose_decisions
        or {"remediation": "not_requested", "alt_text": "not_requested"}
    )
    outcome = await _remediate_file_impl(
        cloud_file,
        db,
        remediation_client=remediation_client,
        alt_text_client=alt_text_client,
        api_client=api_client,
        purpose_decisions=decisions,
    )
    return outcome.model_copy(
        update=_usage_fields(remediation_client, alt_text_client, decisions)
    )


@router.post("/content/scan")
async def scan_brightspace_content(
    request: BrightspaceContentScanRequest,
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceContentScanResponse:
    """Queue scan jobs for Brightspace course content.

    Recursively discovers content items and creates scan jobs for each.
    Filter by scan_types: 'files', 'html', or 'both' (default).

    Requires API key authentication.
    """
    department_id = api_key.department_id
    credential = _get_credential(db, department_id)

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        scannables = await api_client.get_course_content_recursive(request.org_unit_id)
    finally:
        await api_client.close()

    # Filter by scan_types
    if request.scan_types == "files":
        scannables = [s for s in scannables if s.content_type == "file"]
    elif request.scan_types == "html":
        scannables = [s for s in scannables if s.content_type == "html"]
    # "both" keeps all

    # Filter by module_id if specified
    if request.module_id is not None:
        scannables = [s for s in scannables if s.module_id == request.module_id]

    total_items = len(scannables)
    jobs_queued = 0
    skipped = 0

    for item in scannables:
        # Find or create CloudFile record
        cloud_file = (
            db.query(CloudFile)
            .filter(
                CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
                CloudFile.provider_file_id == str(item.topic_id),
                CloudFile.department_id == department_id,
            )
            .first()
        )

        if not cloud_file:
            cloud_file = CloudFile(
                id=str(uuid.uuid4()),
                department_id=department_id,
                credential_id=credential.id,
                provider=CloudProvider.BRIGHTSPACE.value,
                provider_file_id=str(item.topic_id),
                provider_parent_id=str(request.org_unit_id),
                file_name=item.file_name or item.title,
                file_type=item.content_type,
                mime_type="text/html" if item.content_type == "html" else "unknown",
                file_size_bytes=(
                    item.file_size
                    if type(item.file_size) is int and item.file_size > 0
                    else None
                ),
                provider_metadata={
                    "org_unit_id": request.org_unit_id,
                    "module_id": item.module_id,
                    "topic_type": item.content_type,
                    "file_name": item.file_name,
                    "module_path": item.module_path,
                    "url": item.url,
                },
            )
            db.add(cloud_file)
            db.flush()  # Get the ID assigned
        else:
            # Update module_path if missing from earlier scans
            metadata = cloud_file.provider_metadata or {}
            if item.file_name:
                cloud_file.file_name = item.file_name
                metadata["file_name"] = item.file_name
            if not metadata.get("module_path"):
                metadata["module_path"] = item.module_path
                metadata["url"] = item.url
            cloud_file.provider_metadata = metadata
            cloud_file.file_size_bytes = (
                item.file_size
                if type(item.file_size) is int and item.file_size > 0
                else None
            )

        # Check if there's already a pending/processing scan job for this file
        existing_job = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.cloud_file_id == cloud_file.id,
                CloudJobQueue.job_type == CloudJobType.SCAN.value,
                CloudJobQueue.status.in_(
                    [
                        CloudJobStatus.PENDING.value,
                        CloudJobStatus.PROCESSING.value,
                    ]
                ),
            )
            .first()
        )

        if existing_job:
            skipped += 1
            continue

        # Create scan job
        job_id = str(uuid.uuid4())
        scan_job = CloudJobQueue(
            id=job_id,
            department_id=department_id,
            job_type=CloudJobType.SCAN.value,
            provider=CloudProvider.BRIGHTSPACE.value,
            status=CloudJobStatus.PENDING.value,
            priority=1,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(scan_job)
        jobs_queued += 1

        # Queue background task to execute the scan
        background_tasks.add_task(
            _brightspace_scan_file_task,
            job_id=job_id,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )

    db.commit()

    logger.info(
        f"Queued {jobs_queued} Brightspace scan jobs for org_unit {request.org_unit_id} "
        f"(department {department_id}, {skipped} skipped)"
    )

    return BrightspaceContentScanResponse(
        total_items=total_items,
        jobs_queued=jobs_queued,
        skipped=skipped,
    )


@router.get("/content/courses/{org_unit_id}/status")
async def get_brightspace_content_status(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Get scan status for all content items in a Brightspace course.

    Returns compliance scores and scan status for each tracked content item.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    # Query all CloudFile records for this course
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.department_id == department_id,
            CloudFile.provider_parent_id == str(org_unit_id),
        )
        .all()
    )

    items = []
    scanned_count = 0
    total_score = 0.0

    from ..db.models import ScanResult

    for cf in cloud_files:
        # Get the latest scan for this file
        latest_scan = (
            db.query(Scan).filter(Scan.id == cf.last_scan_id).first()
            if cf.last_scan_id
            else None
        )
        # Get issue counts from scan result
        scan_result = (
            db.query(ScanResult).filter(ScanResult.scan_id == cf.last_scan_id).first()
            if cf.last_scan_id
            else None
        )
        issue_count = (
            (
                (scan_result.critical_issues or 0)
                + (scan_result.high_issues or 0)
                + (scan_result.medium_issues or 0)
                + (scan_result.low_issues or 0)
            )
            if scan_result
            else 0
        )

        score = cf.last_compliance_score
        if score is not None:
            scanned_count += 1
            total_score += score

        metadata = cf.provider_metadata or {}

        # Derive a meaningful content type from URL extension or mime_type
        content_type = cf.file_type
        url = metadata.get("url", "")
        if url and content_type == "file":
            ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
            _ext_to_type = {
                "html": "HTML",
                "htm": "HTML",
                "pdf": "PDF",
                "docx": "Word",
                "doc": "Word",
                "pptx": "PowerPoint",
                "ppt": "PowerPoint",
                "xlsx": "Excel",
                "xls": "Excel",
                "jpg": "Image",
                "jpeg": "Image",
                "png": "Image",
                "gif": "Image",
                "mp4": "Video",
                "mp3": "Audio",
                "wav": "Audio",
                "mov": "Video",
            }
            content_type = _ext_to_type.get(ext, content_type)

        items.append(
            {
                "cloud_file_id": cf.id,
                "provider_file_id": cf.provider_file_id,
                "title": cf.file_name,
                "file_name": cf.file_name,
                "content_type": content_type,
                "file_type": cf.file_type,
                "compliance_score": score,
                "issue_count": issue_count,
                "module_path": metadata.get("module_path", ""),
                "writeback_status": cf.writeback_status,
                "last_scanned_at": (
                    cf.last_scanned_at.isoformat() if cf.last_scanned_at else None
                ),
                "scan_status": latest_scan.status.value if latest_scan else None,
                "has_remediated_version": cf.has_remediated_version,
                "needs_rescan": cf.needs_rescan,
            }
        )

    average_compliance = (
        round(total_score / scanned_count, 1) if scanned_count > 0 else None
    )

    return {
        "org_unit_id": org_unit_id,
        "total_items": len(cloud_files),
        "scanned_items": scanned_count,
        "average_compliance": average_compliance,
        "items": items,
    }


@router.post("/remediate")
async def remediate_brightspace_content(
    request: BrightspaceRemediateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> None:
    """Reject the non-executable legacy queued remediation contract."""
    raise HTTPException(
        status_code=501,
        detail="brightspace_queued_remediation_unsupported",
    )


# =============================================================================
# Disconnect
# =============================================================================


@router.delete("/disconnect")
async def disconnect_brightspace(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """Disconnect Brightspace integration for the principal's department.

    Requires account-management authorization.
    """
    require_account_management(principal)
    department_id = principal.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Delete credential
    db.delete(credential)
    db.commit()

    logger.info(f"Disconnected Brightspace for department {department_id}")

    return {"message": "Brightspace disconnected successfully"}


# =============================================================================
# Content Review & Writeback
# =============================================================================


def _get_cloud_file_or_404(
    db: Session, cloud_file_id: str, department_id: str
) -> CloudFile:
    """Get a CloudFile by ID and department, or raise 404."""
    cf = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == cloud_file_id,
            CloudFile.department_id == department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )
    if not cf:
        raise HTTPException(status_code=404, detail="Content item not found")
    return cf


@router.post("/content/{cloud_file_id}/remediate", response_model=RemediationOutcome)
async def remediate_content(
    cloud_file_id: str,
    request: Optional[BrightspaceContentRemediateRequest] = Body(default=None),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> RemediationOutcome:
    """Authorize, bind current policy, and synchronously remediate one item."""
    candidate = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == cloud_file_id,
            CloudFile.department_id == principal.department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )
    if candidate is None or not isinstance(candidate.provider_parent_id, str):
        raise HTTPException(status_code=404, detail="Content item not found")
    try:
        org_unit_id = int(candidate.provider_parent_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Content item not found") from None

    cloud_file = (
        await _authorize_brightspace_files(
            db=db,
            principal=principal,
            org_unit_id=org_unit_id,
            cloud_file_ids=[cloud_file_id],
        )
    )[0]
    size_errors = _preflight_brightspace_file_sizes([cloud_file])
    if str(cloud_file.id) in size_errors:
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="manual_required",
            manual_count=1,
            error_code=size_errors[str(cloud_file.id)],
        )
    intent = request or BrightspaceContentRemediateRequest()
    remediation_client, alt_text_client, decisions = _bind_brightspace_clients(
        principal=principal,
        cloud_file=cloud_file,
        intent=intent,
    )
    _, api_client = await _client_for_fresh_credential(
        db,
        credential_id=cloud_file.credential_id,
        department_id=principal.department_id,
    )
    try:
        return await _remediate_file(
            cloud_file,
            db,
            remediation_client=remediation_client,
            alt_text_client=alt_text_client,
            api_client=api_client,
            purpose_decisions=decisions,
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Brightspace remediation failed",
            extra={
                "cloud_file_id": str(cloud_file.id)[:64],
                "error_type": type(exc).__name__[:64],
            },
        )
        return RemediationOutcome(
            cloud_file_id=str(cloud_file.id),
            status="failed",
            failed_count=1,
            error_code="remediation_failed",
            **_usage_fields(remediation_client, alt_text_client, decisions),
        )
    finally:
        await api_client.close()


@router.post(
    "/content/batch-remediate", response_model=BrightspaceBatchRemediateResponse
)
async def batch_remediate_content(
    request: BrightspaceBatchRemediateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceBatchRemediateResponse:
    """Authorize the complete bounded set, then process each terminally in order."""
    requested_ids = list(dict.fromkeys(request.cloud_file_ids))
    cloud_files = await _authorize_brightspace_files(
        db=db,
        principal=principal,
        org_unit_id=request.org_unit_id,
        cloud_file_ids=requested_ids,
    )
    size_errors = _preflight_brightspace_file_sizes(cloud_files)

    results: List[RemediationOutcome] = []
    for cloud_file in cloud_files:
        size_error = size_errors.get(str(cloud_file.id))
        if size_error is not None:
            results.append(
                RemediationOutcome(
                    cloud_file_id=str(cloud_file.id),
                    status="manual_required",
                    manual_count=1,
                    error_code=size_error,
                )
            )
            continue

        decisions = {"remediation": "not_requested", "alt_text": "not_requested"}
        remediation_client = None
        alt_text_client = None
        api_client = None
        try:
            remediation_client, alt_text_client, decisions = _bind_brightspace_clients(
                principal=principal,
                cloud_file=cloud_file,
                intent=request,
            )
            _, api_client = await _client_for_fresh_credential(
                db,
                credential_id=cloud_file.credential_id,
                department_id=principal.department_id,
            )
            outcome = await _remediate_file(
                cloud_file,
                db,
                remediation_client=remediation_client,
                alt_text_client=alt_text_client,
                api_client=api_client,
                purpose_decisions=decisions,
            )
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 403:
                if request.use_ai is True:
                    decisions["remediation"] = "denied_at_dispatch"
                outcome = RemediationOutcome(
                    cloud_file_id=str(cloud_file.id),
                    status="failed",
                    failed_count=1,
                    error_code="policy_not_permitted",
                    **_usage_fields(remediation_client, alt_text_client, decisions),
                )
            else:
                raise
        except Exception as exc:
            db.rollback()
            logger.error(
                "Brightspace batch item failed",
                extra={
                    "cloud_file_id": str(cloud_file.id)[:64],
                    "error_type": type(exc).__name__[:64],
                },
            )
            outcome = RemediationOutcome(
                cloud_file_id=str(cloud_file.id),
                status="failed",
                failed_count=1,
                error_code="remediation_failed",
                **_usage_fields(remediation_client, alt_text_client, decisions),
            )
        finally:
            if api_client is not None:
                await api_client.close()
        results.append(outcome)

    return BrightspaceBatchRemediateResponse(
        requested_count=len(requested_ids),
        completed_count=sum(item.status == "completed" for item in results),
        manual_count=sum(item.manual_count for item in results),
        failed_count=sum(item.status == "failed" for item in results),
        fixed_count=sum(item.fixed_count for item in results),
        results=results,
    )


@router.get("/content/{cloud_file_id}/diff")
async def get_content_diff(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Get original vs remediated content for review."""
    from ..db.models import ScanResult

    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    issues_fixed = 0
    issues_remaining = 0
    remediated = cf.has_remediated_version or cf.remediated_body
    if remediated and cf.remediated_issues_fixed is not None:
        # Authoritative counts from the remediator
        issues_fixed = cf.remediated_issues_fixed
        issues_remaining = cf.remediated_issues_remaining or 0
    elif cf.last_scan_id:
        scan_result = (
            db.query(ScanResult).filter(ScanResult.scan_id == cf.last_scan_id).first()
        )
        if scan_result and scan_result.issues:
            total_issues = len(scan_result.issues)
            if (
                remediated
                and cf.last_compliance_score is not None
                and cf.last_compliance_score >= 100
            ):
                # Legacy row remediated before counts were persisted, but score says fully fixed
                issues_fixed = total_issues
            else:
                issues_remaining = total_issues

    return {
        "cloud_file_id": cf.id,
        "content_type": cf.file_type,
        "title": cf.file_name,
        "original_html": cf.content_body or "",
        "remediated_html": cf.remediated_body or "",
        "issues_fixed": issues_fixed,
        "issues_remaining": issues_remaining,
    }


@router.post("/content/{cloud_file_id}/approve")
async def approve_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Approve remediated content for write-back."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)
    if not cf.remediated_body:
        raise HTTPException(status_code=400, detail="No remediated content to approve")
    cf.writeback_status = "approved"
    db.commit()
    return {"success": True, "message": "Content approved"}


@router.post("/content/{cloud_file_id}/reject")
async def reject_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Reject remediated content."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)
    cf.writeback_status = "rejected"
    db.commit()
    return {"success": True, "message": "Content rejected"}


@router.post("/content/batch-approve")
async def batch_approve_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Approve multiple content items at once."""
    cloud_file_ids = request.get("cloud_file_ids", [])
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.id.in_(cloud_file_ids),
            CloudFile.department_id == api_key.department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .all()
    )
    approved = 0
    for cf in cloud_files:
        if cf.remediated_body:
            cf.writeback_status = "approved"
            approved += 1
    db.commit()
    return {"approved_count": approved}


async def _writeback_single(api_client, cf: CloudFile, org_unit_id, topic_id, db=None):
    """Write a single remediated file back to Brightspace.

    Saves the current Brightspace content before overwriting so it can be rolled back.
    """
    metadata = cf.provider_metadata or {}
    url = metadata.get("url", "")
    ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""

    if ext in ("html", "htm") or (
        cf.content_body
        and ext
        not in (
            "jpg",
            "jpeg",
            "png",
            "gif",
            "bmp",
            "webp",
            "svg",
            "docx",
            "doc",
            "pptx",
            "ppt",
            "xlsx",
            "xls",
            "pdf",
            "mp4",
            "mp3",
            "wav",
            "avi",
            "mov",
            "webm",
        )
    ):
        # Save current file content as restore point before overwriting
        try:
            file_bytes, _ = await api_client.get_topic_file(int(org_unit_id), topic_id)
            cf.content_body = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            pass  # Keep existing content_body as fallback

        # Upload remediated HTML as replacement file, preserving original name
        original_url = (cf.provider_metadata or {}).get("url", "")
        if original_url and "." in original_url:
            filename = original_url.rsplit("/", 1)[-1]
        else:
            filename = f"{cf.file_name or 'content'}.html"
        remediated_bytes = cf.remediated_body.encode("utf-8")
        await api_client.replace_topic_file(
            org_unit_id, topic_id, remediated_bytes, filename
        )
    elif ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"):
        # Image: update topic description with alt text
        await api_client.update_topic_html(org_unit_id, topic_id, cf.remediated_body)
    elif ext in ("mp4", "mp3", "wav", "avi", "mov", "webm", "ogg"):
        # Multimedia: DON'T replace the video/audio file with a caption file.
        # Instead update the topic description with generated captions/transcript info.
        if cf.remediated_body:
            await api_client.update_topic_html(
                org_unit_id, topic_id, cf.remediated_body
            )
    elif cf.remediated_file_id and os.path.exists(cf.remediated_file_id):
        # Save original file before overwriting
        try:
            original_bytes, _ = await api_client.get_topic_file(
                int(org_unit_id), topic_id
            )
            backup_dir = f"/app/uploads/remediated/{cf.id}"
            os.makedirs(backup_dir, exist_ok=True)
            original_path = os.path.join(backup_dir, f"original.{ext}")
            with open(original_path, "wb") as bf:
                bf.write(original_bytes)
            meta = cf.provider_metadata or {}
            meta["original_file_path"] = original_path
            cf.provider_metadata = meta
        except Exception as backup_err:
            logger.warning(f"Failed to backup original file for {cf.id}: {backup_err}")

        # Document: upload the remediated file with original name
        with open(cf.remediated_file_id, "rb") as f:
            file_bytes = f.read()
        original_url = (cf.provider_metadata or {}).get("url", "")
        if original_url and "." in original_url:
            filename = original_url.rsplit("/", 1)[-1]
        else:
            filename = f"{cf.file_name or 'file'}.{ext}"
        await api_client.replace_topic_file(org_unit_id, topic_id, file_bytes, filename)
    else:
        if cf.remediated_body:
            await api_client.update_topic_html(
                org_unit_id, topic_id, cf.remediated_body
            )
        else:
            raise Exception("No remediated content available for write-back")


@router.post("/content/{cloud_file_id}/writeback")
async def writeback_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Write approved remediated content back to Brightspace."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    if cf.writeback_status != "approved":
        raise HTTPException(
            status_code=400, detail="Content must be approved before write-back"
        )
    if not cf.remediated_body:
        raise HTTPException(
            status_code=400, detail="No remediated content to write back"
        )

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)
    metadata = cf.provider_metadata or {}
    org_unit_id = metadata.get("org_unit_id")
    topic_id = int(cf.provider_file_id)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        await _writeback_single(api_client, cf, org_unit_id, topic_id)
        cf.writeback_status = "written_back"
        db.commit()
        return {"success": True, "message": "Content written back to Brightspace"}
    except Exception as e:
        logger.error(f"Writeback failed for {cloud_file_id}: {e}")
        cf.writeback_status = "write_failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Write-back failed: {str(e)}")
    finally:
        await api_client.close()


@router.post("/content/batch-writeback")
async def batch_writeback_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Write back all approved content items for a course."""
    org_unit_id = request.get("org_unit_id")

    approved_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.provider_parent_id == str(org_unit_id),
            CloudFile.department_id == api_key.department_id,
            CloudFile.writeback_status == "approved",
            CloudFile.remediated_body.isnot(None),
        )
        .all()
    )

    if not approved_files:
        return {"written_count": 0, "failed_count": 0, "stale_count": 0}

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    written = 0
    failed = 0
    try:
        for cf in approved_files:
            try:
                topic_id = int(cf.provider_file_id)
                file_org_unit = (cf.provider_metadata or {}).get(
                    "org_unit_id", org_unit_id
                )
                await _writeback_single(api_client, cf, file_org_unit, topic_id)
                cf.writeback_status = "written_back"
                written += 1
            except Exception as e:
                logger.error(f"Writeback failed for {cf.id}: {e}")
                cf.writeback_status = "write_failed"
                failed += 1
        db.commit()
    finally:
        await api_client.close()

    return {"written_count": written, "failed_count": failed, "stale_count": 0}


@router.post("/content/{cloud_file_id}/rollback")
async def rollback_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Roll back a written-back item to its original content in Brightspace."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    if cf.writeback_status != "written_back":
        raise HTTPException(
            status_code=400, detail="Only written-back items can be rolled back"
        )
    if not cf.content_body:
        raise HTTPException(
            status_code=400, detail="Original content not available for rollback"
        )

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)
    metadata = cf.provider_metadata or {}
    org_unit_id = metadata.get("org_unit_id")
    topic_id = int(cf.provider_file_id)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        # Determine file type for proper rollback method
        metadata = cf.provider_metadata or {}
        url = metadata.get("url", "")
        ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
        original_file_path = metadata.get("original_file_path")

        # Derive original filename from URL
        original_filename = (
            url.rsplit("/", 1)[-1] if "/" in url else f"{cf.file_name or 'file'}.{ext}"
        )

        if ext in ("html", "htm") and cf.content_body:
            # Upload original HTML file back
            filename = original_filename
            original_bytes = cf.content_body.encode("utf-8")
            await api_client.replace_topic_file(
                org_unit_id, topic_id, original_bytes, filename
            )
        elif original_file_path and os.path.exists(original_file_path):
            # Upload original document/media file back
            with open(original_file_path, "rb") as f:
                original_bytes = f.read()
            await api_client.replace_topic_file(
                org_unit_id, topic_id, original_bytes, original_filename
            )
        elif cf.content_body:
            # Fallback: update description
            await api_client.update_topic_html(org_unit_id, topic_id, cf.content_body)
        else:
            raise Exception("No original content available for rollback")

        cf.writeback_status = "rolled_back"
        db.commit()
        logger.info(f"Rolled back content for {cloud_file_id}")
        return {"success": True, "message": "Content rolled back to original"}
    except Exception as e:
        logger.error(f"Rollback failed for {cloud_file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Rollback failed: {str(e)}")
    finally:
        await api_client.close()


@router.post("/content/batch-rollback")
async def batch_rollback_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Roll back all written-back items for a course to their originals."""
    org_unit_id = request.get("org_unit_id")

    written_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.provider_parent_id == str(org_unit_id),
            CloudFile.department_id == api_key.department_id,
            CloudFile.writeback_status == "written_back",
            CloudFile.content_body.isnot(None),
        )
        .all()
    )

    if not written_files:
        return {"rolled_back_count": 0, "failed_count": 0}

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    rolled_back = 0
    failed = 0
    try:
        for cf in written_files:
            try:
                topic_id = int(cf.provider_file_id)
                file_org_unit = (cf.provider_metadata or {}).get(
                    "org_unit_id", org_unit_id
                )
                cf_url = (cf.provider_metadata or {}).get("url", "")
                cf_ext = cf_url.rsplit(".", 1)[-1].lower() if "." in cf_url else ""
                cf_filename = (
                    cf_url.rsplit("/", 1)[-1]
                    if "/" in cf_url
                    else f"{cf.file_name or 'file'}.{cf_ext}"
                )
                if cf_ext in ("html", "htm"):
                    original_bytes = cf.content_body.encode("utf-8")
                    await api_client.replace_topic_file(
                        file_org_unit, topic_id, original_bytes, cf_filename
                    )
                else:
                    await api_client.update_topic_html(
                        file_org_unit, topic_id, cf.content_body
                    )
                cf.writeback_status = "rolled_back"
                rolled_back += 1
            except Exception as e:
                logger.error(f"Rollback failed for {cf.id}: {e}")
                failed += 1
        db.commit()
    finally:
        await api_client.close()

    return {"rolled_back_count": rolled_back, "failed_count": failed}
