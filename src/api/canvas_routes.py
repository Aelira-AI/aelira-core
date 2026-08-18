"""
Canvas LMS REST API Routes

Handles OAuth 2.0 authentication and file operations with Canvas LMS.

SECURITY:
- All endpoints (except OAuth callback) require API key authentication
- Users can only access their own department's data
"""

import logging
import os
import secrets
import json
import base64
from urllib.parse import quote
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
    CloudJobQueue,
    CloudJobType,
    CloudJobStatus,
)
from ..integrations.canvas import (
    CanvasOAuthService,
    CanvasAPIClient,
    CanvasFileInfo,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..auth import verify_department_access
from ..auth.canvas_permissions import (
    require_canvas_staff,
    require_lti_account_access,
    require_lti_course_access,
)
from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..middleware.quota import require_feature

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
    upload_back: bool = Field(True, description="Upload remediated file back to Canvas")
    use_ai: bool = Field(True, description="Use AI for fix generation")


class CanvasRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str


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
    require_lti_account_access(principal)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

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

    # Encode CSRF token + context into state (Canvas only returns code + state)
    state_data = {
        "csrf": secrets.token_urlsafe(32),
        "canvas_instance_url": request.canvas_instance_url,
        "department_id": dept_id,
    }
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()

    # Generate authorization URL
    auth_url = oauth_service.get_authorization_url(
        canvas_instance_url=request.canvas_instance_url,
        state=state,
    )

    logger.info(
        f"Initiated Canvas OAuth for department {dept_id} at {request.canvas_instance_url}"
    )

    return {
        "authorization_url": auth_url,
        "state": state,
    }


@router.get("/oauth/callback")
async def canvas_oauth_callback(
    code: Optional[str] = Query(None, description="Authorization code from Canvas"),
    state: str = Query(..., description="State token encoding CSRF + context"),
    error: Optional[str] = Query(None, description="Error code, if Canvas refused"),
    error_description: Optional[str] = Query(
        None, description="Human-readable reason, if Canvas refused"
    ),
    db: Session = Depends(get_db_dependency),
) -> Any:
    """
    Handle Canvas OAuth callback.

    Decodes canvas_instance_url and department_id from the state parameter,
    then exchanges the authorization code for an access token.

    Canvas answers a refused authorisation on this same URL, with an error
    instead of a code. That is a configuration problem the person connecting
    can fix, so it is reported to them in the dashboard rather than as a
    validation failure about a missing query parameter.
    """
    dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")

    if error:
        reason = error_description or error
        logger.warning(f"Canvas refused the OAuth authorisation: {error} - {reason}")
        return RedirectResponse(
            url=(
                f"{dashboard_url}/integrations?canvas=error"
                f"&message={quote(reason[:200])}"
            ),
        )

    if not code:
        return RedirectResponse(
            url=(
                f"{dashboard_url}/integrations?canvas=error"
                f"&message={quote('Canvas returned no authorisation code.')}"
            ),
        )

    # Decode state to extract context (add padding if stripped by Canvas)
    try:
        padded_state = state + "=" * (-len(state) % 4)
        state_data = json.loads(base64.urlsafe_b64decode(padded_state))
        canvas_instance_url = state_data["canvas_instance_url"]
        department_id = state_data["department_id"]
    except (json.JSONDecodeError, KeyError, Exception) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid OAuth state parameter: {e}",
        )

    # Rewrite localhost for server-side calls inside Docker

    server_canvas_url = canvas_instance_url
    if os.getenv("ENV") == "development" and "localhost" in canvas_instance_url:
        server_canvas_url = canvas_instance_url.replace(
            "localhost", "host.docker.internal"
        )

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
            url=f"{dashboard_url}/integrations?canvas=connected&email={user_info.email}",
        )

    except Exception as e:
        logger.error(f"Canvas OAuth callback failed: {e}", exc_info=True)
        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?canvas=error&message={str(e)[:100]}",
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
    require_lti_account_access(principal)
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


async def _canvas_scan_then_remediate_task(
    scan_job_id: str, remediation_job_id: str
) -> None:
    """Run the queued scan, then the queued remediation, in that order.

    Job rows are records, not work: nothing polls the queue, so whoever
    creates a row has to run it. The scan runs first because remediation
    reads the scan's stored issues, and a failed scan fails its remediation
    rather than leaving it pending forever.
    """
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.cloud_scan_job import handle_scan_job
    from ..jobs.remediation_job import handle_remediation_job

    token_manager = OAuthTokenManager()

    with _get_db_ctx() as db:
        scan_job = (
            db.query(CloudJobQueue).filter(CloudJobQueue.id == scan_job_id).first()
        )
        remediation_job = (
            db.query(CloudJobQueue)
            .filter(CloudJobQueue.id == remediation_job_id)
            .first()
        )
        if not scan_job or not remediation_job:
            logger.error(
                f"Canvas remediation jobs not found: scan={scan_job_id}, "
                f"remediate={remediation_job_id}"
            )
            return

        try:
            scan_job.status = CloudJobStatus.PROCESSING.value
            scan_job.started_at = datetime.now(timezone.utc)
            scan_job.progress = 10
            scan_job.progress_message = "Downloading file from Canvas..."
            db.commit()

            scan_result = await handle_scan_job(scan_job, db, token_manager)

            scan_job.status = CloudJobStatus.COMPLETED.value
            scan_job.progress = 100
            scan_job.progress_message = "Scan complete"
            scan_job.result_data = scan_result
            scan_job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:
            logger.error(f"Canvas scan failed: job={scan_job_id}, error={e}")
            now = datetime.now(timezone.utc)
            scan_job.status = CloudJobStatus.FAILED.value
            scan_job.progress = 100
            scan_job.progress_message = f"Scan failed: {e}"
            scan_job.error_message = str(e)
            scan_job.completed_at = now
            remediation_job.status = CloudJobStatus.FAILED.value
            remediation_job.progress = 100
            remediation_job.progress_message = "Not run: the scan it depends on failed"
            remediation_job.error_message = str(e)
            remediation_job.completed_at = now
            db.commit()
            return

        try:
            # handle_remediation_job reads the scan id off the remediation
            # job's own result_data, so the completed scan's id goes there.
            remediation_job.status = CloudJobStatus.PROCESSING.value
            remediation_job.started_at = datetime.now(timezone.utc)
            remediation_job.progress = 10
            remediation_job.progress_message = "Remediating..."
            remediation_job.result_data = {
                "scan_id": (scan_result or {}).get("scan_id")
            }
            db.commit()

            result = await handle_remediation_job(remediation_job, db, token_manager)

            remediation_job.status = CloudJobStatus.COMPLETED.value
            remediation_job.progress = 100
            remediation_job.progress_message = "Remediation complete"
            remediation_job.result_data = result
            remediation_job.completed_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Canvas remediation complete: job={remediation_job_id}, "
                f"fixed={result.get('fixed_count')}"
            )
        except Exception as e:
            logger.error(
                f"Canvas remediation failed: job={remediation_job_id}, error={e}"
            )
            remediation_job.status = CloudJobStatus.FAILED.value
            remediation_job.progress = 100
            remediation_job.progress_message = f"Remediation failed: {e}"
            remediation_job.error_message = str(e)
            remediation_job.completed_at = datetime.now(timezone.utc)
            db.commit()


@router.post("/remediate")
async def remediate_canvas_file(
    request: CanvasRemediateRequest,
    background_tasks: BackgroundTasks,
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
            if not any(
                str(file_info.id) == str(request.file_id) for file_info in canvas_files
            ):
                raise HTTPException(status_code=404, detail="Canvas file not found")
        finally:
            await api_client.close()

        # Get or create CloudFile record
        cloud_file = (
            db.query(CloudFile)
            .filter(
                CloudFile.provider == CloudProvider.CANVAS.value,
                CloudFile.provider_file_id == request.file_id,
                CloudFile.provider_parent_id == request.course_id,
                CloudFile.department_id == dept_id,
            )
            .first()
        )
        if not cloud_file:
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
                provider_parent_id=request.course_id,
            )
            db.add(cloud_file)

        # Create scan job
        scan_job_id = str(uuid.uuid4())
        scan_job = CloudJobQueue(
            id=scan_job_id,
            department_id=dept_id,
            job_type=CloudJobType.SCAN.value,
            provider=CloudProvider.CANVAS.value,
            status=CloudJobStatus.PENDING.value,
            priority=1,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(scan_job)

        # Create remediation job (will execute after scan completes)
        remediation_job_id = str(uuid.uuid4())
        remediation_job = CloudJobQueue(
            id=remediation_job_id,
            department_id=dept_id,
            job_type=CloudJobType.REMEDIATE.value,
            provider=CloudProvider.CANVAS.value,
            status=CloudJobStatus.PENDING.value,
            priority=2,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(remediation_job)

        db.commit()

        # The queue has no processor: fire the work that satisfies the rows
        # we just wrote, or this endpoint reports success for nothing.
        background_tasks.add_task(
            _canvas_scan_then_remediate_task, scan_job_id, remediation_job_id
        )

        logger.info(
            f"Started Canvas remediation for file {request.file_id}: scan={scan_job_id}, remediate={remediation_job_id}"
        )

        return CanvasRemediateResponse(
            success=True,
            scan_id=None,  # Will be created by scan job
            job_id=remediation_job_id,
            message=(
                "Remediation started. The file is downloaded, scanned, and "
                "remediated; the remediated copy is not written back to "
                "Canvas."
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue Canvas remediation: {e}", exc_info=True)
        return CanvasRemediateResponse(
            success=False,
            message=f"Failed to queue remediation: {str(e)}",
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

    token_manager = OAuthTokenManager()

    # Refresh token if expired
    if token_manager.is_token_expired(credential.token_expires_at):
        oauth_service = CanvasOAuthService()
        refresh_token = token_manager.decrypt_token(credential.refresh_token)
        canvas_instance_url = credential.provider_metadata.get(
            "canvas_instance_url", ""
        )
        if os.getenv("ENV") == "development" and "localhost" in canvas_instance_url:
            canvas_instance_url = canvas_instance_url.replace(
                "localhost", "host.docker.internal"
            )

        try:
            new_access, new_refresh, new_expires = (
                await oauth_service.refresh_access_token(
                    canvas_instance_url=canvas_instance_url,
                    refresh_token=refresh_token,
                )
            )

            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(f"Refreshed Canvas token for department {department_id}")
        except Exception as e:
            logger.error(f"Failed to refresh Canvas token: {e}")
            raise HTTPException(
                status_code=409,
                detail="Canvas token expired and refresh failed. Please reconnect your Canvas account.",
            )

    # Decrypt token and create client
    access_token = token_manager.decrypt_token(credential.access_token)
    canvas_instance_url = credential.provider_metadata.get("canvas_instance_url", "")

    # Rewrite localhost for Docker networking (dev only)
    if os.getenv("ENV") == "development" and "localhost" in canvas_instance_url:
        canvas_instance_url = canvas_instance_url.replace(
            "localhost", "host.docker.internal"
        )

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
