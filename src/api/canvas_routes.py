"""
Canvas LMS REST API Routes

Handles OAuth 2.0 authentication and file operations with Canvas LMS.

SECURITY:
- All endpoints (except OAuth callback) require API key authentication
- Users can only access their own department's data
"""

import logging
import os
from urllib.parse import quote
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
    CloudJobType,
)
from ..integrations.canvas import (
    CanvasOAuthService,
    CanvasAPIClient,
    CanvasFileInfo,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..auth import verify_department_access
from ..auth.redis_rate_limiter import OAuthStateManager, OAuthStateStorageError
from ..auth.canvas_permissions import (
    require_canvas_account_management,
    require_canvas_staff,
    require_lti_account_access,
    require_lti_course_access,
)
from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..middleware.quota import require_feature
from ..ai.lms_remediation_client import LMSRemediationClient
from ..jobs.remediation_job import sanitize_execution_context
from ..services.job_enqueue_service import enqueue_cloud_job
from ..services.canvas_identity_service import (
    add_or_get_canvas_cloud_file,
    invalidate_canvas_derived_state,
    load_canvas_file,
)
from ..utils.security import (
    require_canvas_oauth_allowed_origin,
    require_persisted_canvas_origin,
    resolve_canvas_network_origin,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas", tags=["canvas"])

# =============================================================================
# Request/Response Models
# =============================================================================


class CanvasConnectRequest(BaseModel):
    """Request to initiate Canvas OAuth connection"""

    canvas_instance_url: str = Field(
        ..., description="Canvas instance URL (e.g., https://canvas.university.edu)"
    )
    department_id: Optional[str] = None


class CanvasConnectionStatus(BaseModel):
    """Canvas connection status response"""

    connected: bool
    canvas_instance_url: Optional[str] = None
    user_email: Optional[str] = None
    connected_at: Optional[datetime] = None
    credential_id: Optional[str] = None


class CanvasRemediateRequest(BaseModel):
    """Request to remediate a Canvas file"""

    file_id: str = Field(..., description="Canvas file ID")
    course_id: str = Field(..., description="Canvas course ID")
    department_id: Optional[str] = None
    upload_back: bool = Field(
        False, description="Automatic Canvas writeback is not available"
    )
    use_ai: bool = Field(False, description="Use policy-authorized AI for fixes")
    generate_alt_text: bool = Field(
        False, description="Use policy-authorized vision for image alt text"
    )


class CanvasRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str
    error_code: Optional[str] = None


# =============================================================================
# OAuth Flow
# =============================================================================


@router.post("/connect")
async def connect_canvas(
    request: CanvasConnectRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, str]:
    """
    Initiate Canvas OAuth 2.0 flow.

    REQUIRES API KEY
    REQUIRES: lms_integration feature (department tier or higher)

    Returns authorization URL to redirect user to.
    """
    require_canvas_account_management(principal)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    try:
        canvas_instance_url = require_canvas_oauth_allowed_origin(
            request.canvas_instance_url
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_canvas_origin") from exc

    # Check feature access - Canvas integration requires lms_integration feature
    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )

    oauth_service = CanvasOAuthService()

    if not oauth_service.is_configured():
        raise HTTPException(
            status_code=500,
            detail="Canvas OAuth not configured. Please set CANVAS_OAUTH_CLIENT_ID and CANVAS_OAUTH_CLIENT_SECRET.",
        )

    allow_memory_fallback = os.getenv("ENV", "development").lower() in {
        "development",
        "test",
    }
    try:
        state = OAuthStateManager.create_state(
            metadata={
                "provider": "canvas",
                "department_id": dept_id,
                "canvas_instance_url": canvas_instance_url,
                "initiating_user_id": principal.user_id,
            },
            allow_memory_fallback=allow_memory_fallback,
        )
    except OAuthStateStorageError as exc:
        raise HTTPException(
            status_code=503, detail="OAuth state storage is unavailable"
        ) from exc

    # Generate authorization URL
    auth_url = oauth_service.get_authorization_url(
        canvas_instance_url=canvas_instance_url,
        state=state,
    )

    logger.info(
        f"Initiated Canvas OAuth for department {dept_id} at {canvas_instance_url}"
    )

    return {
        "authorization_url": auth_url,
        "state": state,
    }


@router.get("/oauth/callback")
async def canvas_oauth_callback(
    code: Optional[str] = Query(None, description="Authorization code from Canvas"),
    state: str = Query(..., description="Opaque one-time OAuth state token"),
    error: Optional[str] = Query(None, description="Error code, if Canvas refused"),
    error_description: Optional[str] = Query(
        None, description="Human-readable reason, if Canvas refused"
    ),
    db: Session = Depends(get_db_dependency),
) -> Any:
    """
    Handle Canvas OAuth callback.

    Verifies and consumes server-side state before reading callback results,
    then exchanges the authorization code using only trusted state metadata.

    Canvas answers a refused authorisation on this same URL, with an error
    instead of a code. That is a configuration problem the person connecting
    can fix, so it is reported to them in the dashboard rather than as a
    validation failure about a missing query parameter.
    """
    dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")

    allow_memory_fallback = os.getenv("ENV", "development").lower() in {
        "development",
        "test",
    }
    is_valid, state_metadata = OAuthStateManager.verify_and_consume_state(
        state, allow_memory_fallback=allow_memory_fallback
    )
    if not is_valid or not isinstance(state_metadata, dict):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    canvas_instance_url = state_metadata.get("canvas_instance_url")
    department_id = state_metadata.get("department_id")
    initiating_user_id = state_metadata.get("initiating_user_id")
    if (
        state_metadata.get("provider") != "canvas"
        or not isinstance(canvas_instance_url, str)
        or not canvas_instance_url
        or not isinstance(department_id, str)
        or not department_id
        or not isinstance(initiating_user_id, str)
        or not initiating_user_id
    ):
        raise HTTPException(status_code=400, detail="Invalid OAuth state metadata")

    try:
        canvas_instance_url = require_canvas_oauth_allowed_origin(canvas_instance_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid OAuth state metadata"
        ) from exc

    if error:
        logger.warning("Canvas refused the OAuth authorisation")
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?canvas=error&code=oauth_refused",
        )

    if not code:
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?canvas=error&code=missing_code",
        )

    # Browser-facing OAuth state keeps the persisted localhost origin, while
    # server-side token/API calls use the centralized development mapping.
    server_canvas_url = resolve_canvas_network_origin(canvas_instance_url)

    oauth_service = CanvasOAuthService()
    token_manager = OAuthTokenManager()

    try:
        # Exchange code for token
        credential = await oauth_service.exchange_code_for_token(
            canvas_instance_url=server_canvas_url,
            authorization_code=code,
        )

        # Get user info (use server URL for API calls from Docker)
        api_client = CanvasAPIClient(
            canvas_instance_url=server_canvas_url,
            access_token=credential.access_token,
        )
        try:
            user_info = await api_client.get_current_user()
        finally:
            await api_client.close()

        # Store credentials in database
        # Encrypt tokens
        encrypted_access = token_manager.encrypt_token(credential.access_token)
        encrypted_refresh = (
            token_manager.encrypt_token(credential.refresh_token)
            if credential.refresh_token
            else None
        )

        # Check for existing credential
        existing = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
            )
            .first()
        )

        if existing:
            # Update existing
            existing.access_token = encrypted_access
            existing.refresh_token = encrypted_refresh
            existing.token_expires_at = credential.expires_at
            existing.scopes = credential.scope
            existing.is_active = True
            existing.provider_metadata = {
                "canvas_instance_url": canvas_instance_url,
                "user_id": credential.user_id,
                "user_email": user_info.email,
                "user_name": user_info.name,
            }
            db_credential = existing
        else:
            # Create new
            db_credential = CloudOAuthCredentials(
                department_id=department_id,
                provider=CloudProvider.CANVAS.value,
                access_token=encrypted_access,
                refresh_token=encrypted_refresh,
                token_expires_at=credential.expires_at,
                scopes=credential.scope,
                is_active=True,
                provider_metadata={
                    "canvas_instance_url": canvas_instance_url,
                    "user_id": credential.user_id,
                    "user_email": user_info.email,
                    "user_name": user_info.name,
                },
            )
            db.add(db_credential)

        db.commit()
        db.refresh(db_credential)

        logger.info(
            f"Canvas OAuth successful for department {department_id}: {user_info.email}"
        )

        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
        return RedirectResponse(
            url=(
                f"{dashboard_url}/integrations?canvas=connected"
                f"&email={quote(user_info.email or '', safe='')}"
            ),
        )

    except Exception:
        logger.error("Canvas OAuth callback failed")
        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?canvas=error&code=callback_failed",
        )


@router.delete("/disconnect")
async def disconnect_canvas(
    department_id: str = Query(..., description="Department ID"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, str]:
    """
    Disconnect Canvas integration.

    REQUIRES API KEY

    Revokes OAuth tokens and removes credentials.
    """
    require_canvas_account_management(principal)
    verify_department_access(department_id, principal.department_id)
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(status_code=404, detail="Canvas not connected")

    # Mark as inactive (soft delete)
    credential.is_active = False
    db.commit()

    logger.info(f"Disconnected Canvas for department {department_id}")

    return {"success": "true", "message": "Canvas disconnected"}


@router.get("/status")
async def canvas_connection_status(
    department_id: Optional[str] = Query(default=None, description="Department ID"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CanvasConnectionStatus:
    """
    Check Canvas connection status for a department.

    REQUIRES API KEY
    """
    require_lti_account_access(principal)
    dept_id = department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == dept_id,
            CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        return CanvasConnectionStatus(connected=False)

    # provider_metadata is nullable, so a credential written by an older
    # path can have none. Reading through it directly turned that into a
    # 500 on a plain status check.
    metadata = credential.provider_metadata or {}
    return CanvasConnectionStatus(
        connected=True,
        canvas_instance_url=metadata.get("canvas_instance_url"),
        user_email=metadata.get("user_email"),
        connected_at=credential.created_at,
        credential_id=credential.id,
    )


# =============================================================================
# File Browsing
# =============================================================================


@router.get("/courses")
async def list_canvas_courses(
    department_id: Optional[str] = Query(default=None, description="Department ID"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> List[Dict[str, Any]]:
    """
    List Canvas courses for connected user.

    REQUIRES API KEY
    """
    require_canvas_staff(principal)
    dept_id = department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    credential, api_client = await _get_canvas_client(dept_id, db)

    try:
        courses = await api_client.list_courses(enrollment_state="active")
        if principal.auth_method == "lti" and not principal.lti_account_wide:
            courses = [
                course
                for course in courses
                if str(course.id) == principal.lti_course_id
            ]

        return [
            {
                "id": course.id,
                "name": course.name,
                "course_code": course.course_code,
                "workflow_state": course.workflow_state,
                "start_at": course.start_at.isoformat() if course.start_at else None,
                "end_at": course.end_at.isoformat() if course.end_at else None,
            }
            for course in courses
        ]
    finally:
        await api_client.close()


@router.get("/courses/{course_id}/files")
async def list_canvas_course_files(
    course_id: str,
    department_id: Optional[str] = Query(default=None, description="Department ID"),
    search_term: Optional[str] = Query(None, description="Search query"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> List[Dict[str, Any]]:
    """
    List files in a Canvas course.

    REQUIRES API KEY
    """
    require_lti_course_access(principal, course_id)
    dept_id = department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    credential, api_client = await _get_canvas_client(dept_id, db)

    try:
        files = await api_client.list_course_files(
            course_id=course_id,
            search_term=search_term,
        )

        return [_format_file_info(file) for file in files]
    finally:
        await api_client.close()


@router.get("/courses/{course_id}/folders")
async def list_canvas_course_folders(
    course_id: str,
    department_id: str = Query(..., description="Department ID"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> List[Dict[str, Any]]:
    """
    List folders in a Canvas course.

    REQUIRES API KEY
    REQUIRES: lms_integration feature (department tier or higher)
    """
    require_lti_course_access(principal, course_id)
    verify_department_access(department_id, principal.department_id)

    # Check feature access - Canvas integration requires lms_integration feature
    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )
    credential, api_client = await _get_canvas_client(department_id, db)

    try:
        folders = await api_client.list_course_folders(course_id=course_id)

        return [
            {
                "id": folder.id,
                "name": folder.name,
                "full_name": folder.full_name,
                "parent_folder_id": folder.parent_folder_id,
                "files_count": folder.files_count,
                "folders_count": folder.folders_count,
                "locked": folder.locked,
                "hidden": folder.hidden,
            }
            for folder in folders
        ]
    finally:
        await api_client.close()


# =============================================================================
# Remediation
# =============================================================================


def _rollback_canvas_remediation_enqueue(db: Session) -> None:
    try:
        db.rollback()
    except Exception as exc:
        logger.error(
            "Canvas remediation enqueue rollback failed",
            extra={"exception_type": type(exc).__name__[:64]},
        )


@router.post("/remediate")
async def remediate_canvas_file(
    request: CanvasRemediateRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CanvasRemediateResponse:
    """
    Queue remediation job for a Canvas file.

    REQUIRES API KEY
    REQUIRES: lms_integration feature (department tier or higher)

    Downloads file, scans, remediates, and optionally uploads back.
    """
    require_lti_course_access(principal, request.course_id)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    # Check feature access - Canvas integration requires lms_integration feature
    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )
    import uuid

    try:
        # Get Canvas credentials
        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == dept_id,
                CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        if not credential:
            return CanvasRemediateResponse(
                success=False,
                message="Canvas not connected. Please connect your Canvas account first.",
            )

        # Resolve the requested ID through the course-scoped endpoint. The
        # account-wide /files/{id} endpoint does not prove course membership.
        _, api_client = await _get_canvas_client(dept_id, db)
        try:
            canvas_files = await api_client.list_course_files(request.course_id)
            file_info = next(
                (item for item in canvas_files if str(item.id) == str(request.file_id)),
                None,
            )
            if file_info is None:
                raise HTTPException(status_code=404, detail="Canvas file not found")
        finally:
            await api_client.close()

        provider_modified_at = getattr(file_info, "updated_at", None)
        provider_version = (
            provider_modified_at.isoformat() if provider_modified_at else None
        )

        if request.upload_back is True:
            raise HTTPException(
                status_code=400, detail="automatic_canvas_writeback_unsupported"
            )

        # Check policy only after course-file authorization to avoid exposing
        # policy state for resources the caller cannot see.
        policy_provider = None
        ai_requested = request.use_ai is True
        alt_text_requested = getattr(request, "generate_alt_text", False) is True
        for purpose, requested in (
            ("remediation", ai_requested),
            ("alt_text", alt_text_requested),
        ):
            if not requested:
                continue
            policy_client = LMSRemediationClient.bind_if_allowed(
                department_id=dept_id,
                purpose=purpose,
                actor_id=principal.user_id,
            )
            if policy_client is None:
                raise HTTPException(
                    status_code=403,
                    detail=f"LMS AI {purpose} is not permitted",
                )
            policy_provider = policy_client.provider

        # Get or create CloudFile record
        cloud_file = load_canvas_file(
            db,
            department_id=dept_id,
            course_id=request.course_id,
            provider_file_id=request.file_id,
        )
        if cloud_file:
            if cloud_file.provider_version != provider_version:
                invalidate_canvas_derived_state(cloud_file)
            cloud_file.provider_modified_at = provider_modified_at
            cloud_file.provider_version = provider_version
            cloud_file.content_source = "file"
        else:
            cloud_file = CloudFile(
                id=str(uuid.uuid4()),
                department_id=dept_id,
                credential_id=credential.id,
                provider=CloudProvider.CANVAS.value,
                provider_file_id=request.file_id,
                file_name=f"canvas_file_{request.file_id}",
                file_type="unknown",
                mime_type="unknown",
                file_size_bytes=0,
                provider_modified_at=provider_modified_at,
                provider_version=provider_version,
                provider_parent_id=request.course_id,
                content_source="file",
            )
            cloud_file = add_or_get_canvas_cloud_file(
                db,
                cloud_file,
                lambda: load_canvas_file(
                    db,
                    department_id=dept_id,
                    course_id=request.course_id,
                    provider_file_id=request.file_id,
                ),
            )

        db.flush()
        scan_payload = {
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.CANVAS.value,
            "provider_file_id": str(request.file_id),
            "course_id": str(request.course_id),
        }
        scan_job = enqueue_cloud_job(
            db,
            department_id=dept_id,
            job_type=CloudJobType.SCAN.value,
            payload=scan_payload,
            dedupe_key=(
                f"scan:canvas:{request.course_id}:file:{request.file_id}:"
                f"{provider_version or 'current'}"
            ),
            provider=CloudProvider.CANVAS.value,
            priority=1,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
            execution_context=sanitize_execution_context(
                {
                    "originating_route": "/canvas/remediate",
                    "resource_id": str(request.file_id),
                    "course_id": str(request.course_id),
                }
            ),
        )

        remediation_payload = {
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.CANVAS.value,
            "provider_file_id": str(request.file_id),
            "course_id": str(request.course_id),
            "scan_job_id": scan_job.id,
            "ai_requested": ai_requested,
            "alt_text_requested": alt_text_requested,
            "upload_back": False,
        }
        remediation_job = enqueue_cloud_job(
            db,
            department_id=dept_id,
            job_type=CloudJobType.REMEDIATE.value,
            payload=remediation_payload,
            dedupe_key=(
                f"remediate:canvas:{request.course_id}:file:{request.file_id}:"
                f"version={provider_version or 'current'}:"
                f"ai={str(ai_requested).lower()}:"
                f"alt={str(alt_text_requested).lower()}"
            ),
            depends_on_job_id=scan_job.id,
            provider=CloudProvider.CANVAS.value,
            priority=2,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
            execution_context=sanitize_execution_context(
                {
                    "ai_requested": ai_requested,
                    "alt_text_requested": alt_text_requested,
                    "requested_purposes": [
                        purpose
                        for purpose, requested in (
                            ("remediation", ai_requested),
                            ("alt_text", alt_text_requested),
                        )
                        if requested
                    ],
                    "policy_version": "1",
                    "policy_provider": policy_provider,
                    "originating_route": "/canvas/remediate",
                    "resource_id": str(request.file_id),
                    "course_id": str(request.course_id),
                }
            ),
        )

        db.commit()

        logger.info(
            f"Queued Canvas remediation for file {request.file_id}: "
            f"scan={scan_job.id}, remediate={remediation_job.id}"
        )

        return CanvasRemediateResponse(
            success=True,
            scan_id=None,  # Will be created by scan job
            job_id=remediation_job.id,
            message=(
                "Remediation started. The file is downloaded, scanned, and "
                "remediated; the remediated copy is not written back to "
                "Canvas."
            ),
        )

    except HTTPException:
        _rollback_canvas_remediation_enqueue(db)
        raise
    except Exception as exc:
        _rollback_canvas_remediation_enqueue(db)
        logger.error(
            "Canvas remediation enqueue failed",
            extra={
                "operation": "canvas_remediation_enqueue",
                "exception_type": type(exc).__name__[:64],
            },
        )
        return CanvasRemediateResponse(
            success=False,
            message="Unable to queue remediation. Please try again later.",
            error_code="remediation_queue_unavailable",
        )


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_canvas_client(
    department_id: str, db: Session
) -> tuple[CloudOAuthCredentials, CanvasAPIClient]:
    """
    Get Canvas API client with token refresh if needed.

    Returns:
        Tuple of (credential, api_client)
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Canvas not connected. Please connect your Canvas account first.",
        )

    try:
        canvas_instance_url = require_persisted_canvas_origin(credential)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Canvas connection is no longer authorized. Please reconnect your Canvas account.",
        ) from exc

    token_manager = OAuthTokenManager()

    # Refresh token if expired
    if token_manager.is_token_expired(credential.token_expires_at):
        oauth_service = CanvasOAuthService()
        refresh_token = token_manager.decrypt_token(credential.refresh_token)
        canvas_network_origin = resolve_canvas_network_origin(canvas_instance_url)

        try:
            new_access, new_refresh, new_expires = (
                await oauth_service.refresh_access_token(
                    canvas_instance_url=canvas_network_origin,
                    refresh_token=refresh_token,
                )
            )

            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(f"Refreshed Canvas token for department {department_id}")
        except Exception as exc:
            logger.error(
                "Failed to refresh Canvas token",
                extra={
                    "department_id": department_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=409,
                detail="Canvas token expired and refresh failed. Please reconnect your Canvas account.",
            )

    # Decrypt token and create client
    access_token = token_manager.decrypt_token(credential.access_token)

    # CanvasAPIClient centralizes the development network-origin mapping.
    api_client = CanvasAPIClient(
        canvas_instance_url=canvas_instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )

    return credential, api_client


def _format_file_info(file: CanvasFileInfo) -> Dict[str, Any]:
    """Format CanvasFileInfo for API response"""
    return {
        "id": file.id,
        "display_name": file.display_name,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file.size,
        "url": file.url,
        "folder_id": file.folder_id,
        "thumbnail_url": file.thumbnail_url,
        "locked": file.locked,
        "hidden": file.hidden,
        "created_at": file.created_at.isoformat() if file.created_at else None,
        "updated_at": file.updated_at.isoformat() if file.updated_at else None,
    }
