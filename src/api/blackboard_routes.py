"""
Blackboard Learn REST API Routes

FastAPI routes for Blackboard Learn OAuth 2.0 and file operations.

SECURITY:
- All endpoints (except OAuth callback) require API key authentication
- Users can only access their own department's data

Endpoints:
- POST /blackboard/connect - Initiate OAuth flow
- GET /blackboard/oauth/callback - Handle OAuth callback
- DELETE /blackboard/disconnect - Revoke connection
- GET /blackboard/courses - List user's courses
- GET /blackboard/files - Browse course files
- POST /blackboard/remediate - Queue remediation job
"""

import logging
import secrets
from typing import Dict, Any, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudFile,
    CloudJobType,
    CloudProvider,
    APIKey,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.blackboard import (
    BlackboardOAuthService,
    BlackboardAPIClient,
)
from ..auth import get_required_api_key, verify_department_access
from ..services.job_enqueue_service import enqueue_cloud_job
from ..utils.security import require_persisted_blackboard_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/blackboard", tags=["blackboard"])


# ============================================================================
# Request/Response Models
# ============================================================================


class BlackboardConnectRequest(BaseModel):
    """Request to initiate Blackboard OAuth flow"""

    blackboard_instance_url: str
    department_id: str
    redirect_uri: Optional[str] = None


class BlackboardRemediateRequest(BaseModel):
    """Request to remediate a Blackboard file"""

    course_id: str
    content_id: str
    department_id: str
    upload_as_new: bool = True
    use_ai: bool = True


# ============================================================================
# OAuth Flow
# ============================================================================


@router.post("/connect")
async def connect_blackboard(
    request: BlackboardConnectRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> Dict[str, str]:
    """
    Initiate Blackboard OAuth 2.0 flow.

    REQUIRES API KEY

    Returns authorization URL to redirect user to.
    """
    _, user_id, auth_department_id = api_key_info
    verify_department_access(request.department_id, auth_department_id)
    oauth_service = BlackboardOAuthService()

    if not oauth_service.is_configured():
        raise HTTPException(
            status_code=500,
            detail="Blackboard OAuth not configured. Please set BLACKBOARD_OAUTH_CLIENT_ID and BLACKBOARD_OAUTH_CLIENT_SECRET.",
        )

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)

    # Default redirect URI
    redirect_uri = (
        request.redirect_uri or f"{request.blackboard_instance_url}/oauth/callback"
    )

    # Generate authorization URL
    auth_url = oauth_service.get_authorization_url(
        blackboard_instance_url=request.blackboard_instance_url,
        redirect_uri=redirect_uri,
        state=state,
    )

    logger.info(
        f"Initiated Blackboard OAuth for department {request.department_id} at {request.blackboard_instance_url}"
    )

    return {
        "authorization_url": auth_url,
        "state": state,
    }


@router.get("/oauth/callback")
async def blackboard_oauth_callback(
    code: str = Query(..., description="Authorization code from Blackboard"),
    state: str = Query(..., description="CSRF state token"),
    blackboard_instance_url: str = Query(
        ..., description="Blackboard instance URL (passed via state)"
    ),
    department_id: str = Query(..., description="Department ID (passed via state)"),
    redirect_uri: str = Query(..., description="Redirect URI used in authorization"),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """
    Handle Blackboard OAuth callback.

    Exchanges authorization code for access token and stores credentials.
    """
    oauth_service = BlackboardOAuthService()
    token_manager = OAuthTokenManager()

    try:
        # Exchange code for token
        credential = await oauth_service.exchange_code_for_token(
            blackboard_instance_url=blackboard_instance_url,
            authorization_code=code,
            redirect_uri=redirect_uri,
        )

        # Get user info
        api_client = BlackboardAPIClient(
            blackboard_instance_url=blackboard_instance_url,
            access_token=credential.access_token,
        )

        try:
            user_info = await api_client.get_current_user()
        finally:
            await api_client.close()

        # Check for existing credential
        existing_credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == CloudProvider.BLACKBOARD.value,
            )
            .first()
        )

        if existing_credential:
            # Update existing credential
            existing_credential.access_token = token_manager.encrypt_token(
                credential.access_token
            )
            if credential.refresh_token:
                existing_credential.refresh_token = token_manager.encrypt_token(
                    credential.refresh_token
                )
            existing_credential.token_expires_at = credential.expires_at
            existing_credential.provider_user_id = user_info.id
            existing_credential.provider_email = user_info.email
            existing_credential.provider_name = f"{user_info.name.get('given', '')} {user_info.name.get('family', '')}".strip()
            existing_credential.is_active = True
            existing_credential.provider_metadata = {
                "blackboard_instance_url": blackboard_instance_url
            }
            db.commit()

            logger.info(
                f"Updated Blackboard credentials for department {department_id}"
            )

        else:
            # Create new credential
            new_credential = CloudOAuthCredentials(
                department_id=department_id,
                provider=CloudProvider.BLACKBOARD.value,
                access_token=token_manager.encrypt_token(credential.access_token),
                refresh_token=(
                    token_manager.encrypt_token(credential.refresh_token)
                    if credential.refresh_token
                    else None
                ),
                token_expires_at=credential.expires_at,
                provider_user_id=user_info.id,
                provider_email=user_info.email,
                provider_name=f"{user_info.name.get('given', '')} {user_info.name.get('family', '')}".strip(),
                scopes=credential.scope.split() if credential.scope else None,
                is_active=True,
                provider_metadata={"blackboard_instance_url": blackboard_instance_url},
            )
            db.add(new_credential)
            db.commit()

            logger.info(
                f"Created new Blackboard credentials for department {department_id}"
            )

        return {
            "success": True,
            "message": "Blackboard account connected successfully",
            "user": {
                "id": user_info.id,
                "name": f"{user_info.name.get('given', '')} {user_info.name.get('family', '')}".strip(),
                "email": user_info.email,
            },
        }

    except Exception as e:
        logger.error(f"Blackboard OAuth callback failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete Blackboard OAuth: {str(e)}",
        )


@router.delete("/disconnect")
async def disconnect_blackboard(
    department_id: str = Query(...),
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> Dict[str, Any]:
    """
    Disconnect Blackboard integration.

    REQUIRES API KEY

    Deactivates the stored credentials.
    """
    _, user_id, auth_department_id = api_key_info
    verify_department_access(department_id, auth_department_id)
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BLACKBOARD.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="No active Blackboard connection found",
        )

    credential.is_active = False
    db.commit()

    logger.info(f"Disconnected Blackboard for department {department_id}")

    return {"success": True, "message": "Blackboard disconnected successfully"}


# ============================================================================
# File Operations
# ============================================================================


async def _get_blackboard_client(
    department_id: str, db: Session
) -> tuple[CloudOAuthCredentials, BlackboardAPIClient]:
    """Helper to get authenticated Blackboard API client"""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BLACKBOARD.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="No active Blackboard connection found. Please connect first.",
        )

    token_manager = OAuthTokenManager()

    # Refresh token if needed (5 minutes buffer)
    if token_manager.is_token_expired(credential.token_expires_at):
        oauth_service = BlackboardOAuthService()
        refresh_token = token_manager.decrypt_token(credential.refresh_token)
        blackboard_instance_url = credential.provider_metadata.get(
            "blackboard_instance_url"
        )

        new_access, new_refresh, new_expires = await oauth_service.refresh_access_token(
            blackboard_instance_url=blackboard_instance_url,
            refresh_token=refresh_token,
        )

        credential.access_token = token_manager.encrypt_token(new_access)
        if new_refresh:
            credential.refresh_token = token_manager.encrypt_token(new_refresh)
        credential.token_expires_at = new_expires
        db.commit()

    # Get decrypted access token
    access_token = token_manager.decrypt_token(credential.access_token)
    try:
        blackboard_instance_url = require_persisted_blackboard_origin(
            credential.provider_metadata
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    api_client = BlackboardAPIClient(
        blackboard_instance_url=blackboard_instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )

    return credential, api_client


@router.get("/courses")
async def list_blackboard_courses(
    department_id: str = Query(...),
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    List Blackboard courses for connected user.

    REQUIRES API KEY
    """
    _, user_id, auth_department_id = api_key_info
    verify_department_access(department_id, auth_department_id)
    credential, api_client = await _get_blackboard_client(department_id, db)

    try:
        courses = await api_client.list_courses()
        return {
            "success": True,
            "courses": [
                {
                    "id": course.id,
                    "course_id": course.course_id,
                    "name": course.name,
                    "description": course.description,
                    "is_available": course.is_available,
                }
                for course in courses
            ],
        }
    finally:
        await api_client.close()


@router.get("/files")
async def list_blackboard_files(
    course_id: str = Query(...),
    content_id: Optional[str] = Query(None),
    department_id: str = Query(...),
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    List files in a Blackboard course.

    REQUIRES API KEY
    """
    _, user_id, auth_department_id = api_key_info
    verify_department_access(department_id, auth_department_id)
    credential, api_client = await _get_blackboard_client(department_id, db)

    try:
        files = await api_client.list_course_content(
            course_id=course_id,
            content_id=content_id,
        )
        return {
            "success": True,
            "files": [
                {
                    "id": file.id,
                    "title": file.title,
                    "content_type": file.content_type,
                    "has_children": file.has_children,
                    "created_at": (
                        file.created_at.isoformat() if file.created_at else None
                    ),
                    "modified_at": (
                        file.modified_at.isoformat() if file.modified_at else None
                    ),
                }
                for file in files
            ],
        }
    finally:
        await api_client.close()


@router.post("/remediate")
async def remediate_blackboard_file(
    request: BlackboardRemediateRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Validate persisted Blackboard authority and enqueue a durable pipeline.

    REQUIRES API KEY

    The route performs no provider I/O. The durable worker owns all execution.
    """
    auth_department_id = api_key_info[2]
    verify_department_access(request.department_id, auth_department_id)
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == request.department_id,
            CloudOAuthCredentials.provider == CloudProvider.BLACKBOARD.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )
    if credential is None:
        raise HTTPException(
            status_code=404,
            detail="No active Blackboard connection found. Please connect first.",
        )
    try:
        require_persisted_blackboard_origin(credential.provider_metadata)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == request.department_id,
            CloudFile.provider == CloudProvider.BLACKBOARD.value,
            CloudFile.provider_file_id == request.content_id,
            CloudFile.provider_parent_id == request.course_id,
            CloudFile.credential_id == credential.id,
        )
        .first()
    )
    if cloud_file is None:
        raise HTTPException(status_code=404, detail="Blackboard file not found")

    version = cloud_file.provider_version or cloud_file.file_hash or "current"
    scan_job = enqueue_cloud_job(
        db,
        department_id=request.department_id,
        job_type=CloudJobType.SCAN.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.BLACKBOARD.value,
            "provider_file_id": request.content_id,
            "course_id": request.course_id,
        },
        dedupe_key=f"scan:blackboard:{request.course_id}:{request.content_id}:{version}",
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.BLACKBOARD.value,
        provider_file_id=request.content_id,
        priority=1,
    )
    remediation_job = enqueue_cloud_job(
        db,
        department_id=request.department_id,
        job_type=CloudJobType.REMEDIATE.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.BLACKBOARD.value,
            "provider_file_id": request.content_id,
            "course_id": request.course_id,
            "scan_job_id": scan_job.id,
            "upload_as_new": request.upload_as_new,
            "use_ai": request.use_ai,
        },
        dedupe_key=(
            f"remediate:blackboard:{request.course_id}:{request.content_id}:"
            f"version={version}:upload-new={str(request.upload_as_new).lower()}:"
            f"ai={str(request.use_ai).lower()}"
        ),
        depends_on_job_id=scan_job.id,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.BLACKBOARD.value,
        provider_file_id=request.content_id,
        priority=2,
    )
    db.commit()
    return {
        "success": True,
        "job_id": remediation_job.id,
        "scan_job_id": scan_job.id,
        "status": remediation_job.status,
        "progress": remediation_job.progress,
        "course_id": request.course_id,
        "content_id": request.content_id,
    }


__all__ = ["router"]
