"""
Moodle LMS REST API Routes

Handles OAuth 2.0 authentication and file operations with Moodle LMS.

Market Impact: +20% US, +60% Australia (world's most-used LMS)
"""

import os
import logging
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    APIKey,
)
from ..integrations.moodle import (
    get_moodle_authorization_url,
    exchange_moodle_code_for_token,
    get_moodle_webservice_token,
    MoodleAPIClient,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..api.auth_routes import get_current_api_key
from ..auth.redis_rate_limiter import OAuthStateManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/moodle", tags=["moodle"])

# =============================================================================
# Request/Response Models
# =============================================================================


class MoodleConnectRequest(BaseModel):
    """Request to initiate Moodle OAuth connection"""

    moodle_instance_url: str = Field(
        ..., description="Moodle instance URL (e.g., https://moodle.university.edu)"
    )
    department_id: str = Field(..., description="Department ID")
    redirect_uri: Optional[str] = Field(
        None, description="OAuth callback URL (optional, defaults to /moodle/callback)"
    )


class MoodleConnectionStatus(BaseModel):
    """Moodle connection status response"""

    connected: bool
    moodle_instance_url: Optional[str] = None
    user_email: Optional[str] = None
    user_fullname: Optional[str] = None
    connected_at: Optional[datetime] = None
    credential_id: Optional[str] = None


class MoodleRemediateRequest(BaseModel):
    """Request to remediate a Moodle file"""

    file_url: str = Field(..., description="Moodle file URL")
    course_id: str = Field(..., description="Moodle course ID")
    department_id: str
    upload_back: bool = Field(True, description="Upload remediated file back to Moodle")
    use_ai: bool = Field(True, description="Use AI for fix generation")


class MoodleRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str


# =============================================================================
# OAuth Flow
# =============================================================================


@router.post("/connect")
async def connect_moodle(
    request: MoodleConnectRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """
    Initiate Moodle OAuth 2.0 flow.

    Requires API key authentication to prevent unauthorized OAuth initiations.
    Returns authorization URL to redirect user to.
    """
    # Verify the API key's department matches the request
    if api_key.department_id != request.department_id:
        raise HTTPException(
            status_code=403,
            detail="API key department does not match requested department",
        )

    # Generate secure CSRF state token with metadata (stored server-side with TTL)
    state = OAuthStateManager.create_state(
        metadata={
            "department_id": request.department_id,
            "moodle_instance_url": request.moodle_instance_url,
            "provider": "moodle",
        }
    )

    # Generate redirect URI
    redirect_uri = (
        request.redirect_uri
        or f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/moodle/callback"
    )

    try:
        # Generate authorization URL
        auth_url = get_moodle_authorization_url(
            moodle_instance_url=request.moodle_instance_url,
            redirect_uri=redirect_uri,
            state=state,
        )

        logger.info(
            f"Initiated Moodle OAuth for department {request.department_id} at {request.moodle_instance_url}"
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
async def moodle_oauth_callback(
    code: str = Query(..., description="Authorization code from Moodle"),
    state: str = Query(..., description="CSRF state token"),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """
    Handle Moodle OAuth callback.

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
    moodle_instance_url = metadata.get("moodle_instance_url")

    if not department_id or not moodle_instance_url:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state metadata. Please restart the connection flow.",
        )

    token_manager = OAuthTokenManager()

    try:
        # Generate redirect URI (must match the one used in /connect)
        redirect_uri = (
            f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/moodle/callback"
        )

        # Exchange code for token
        access_token, refresh_token, expires_at = await exchange_moodle_code_for_token(
            moodle_instance_url=moodle_instance_url,
            authorization_code=code,
            redirect_uri=redirect_uri,
        )

        # Get web service token (Moodle-specific step)
        ws_token = await get_moodle_webservice_token(
            moodle_instance_url=moodle_instance_url,
            oauth_access_token=access_token,
        )

        # Get user info using the web service token
        api_client = MoodleAPIClient(
            moodle_instance_url=moodle_instance_url,
            access_token=ws_token,
        )

        try:
            user_info = await api_client.get_site_info()

            # Check if credential already exists
            existing = (
                db.query(CloudOAuthCredentials)
                .filter(
                    CloudOAuthCredentials.department_id == department_id,
                    CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
                    CloudOAuthCredentials.provider_user_id == user_info.id,
                )
                .first()
            )

            if existing:
                # Update existing credential
                existing.access_token = token_manager.encrypt_token(ws_token)
                if refresh_token:
                    existing.refresh_token = token_manager.encrypt_token(refresh_token)
                existing.token_expires_at = expires_at
                existing.provider_instance_url = moodle_instance_url
                existing.provider_user_email = user_info.email
                existing.provider_user_name = user_info.fullname
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()

                credential_id = existing.id
                logger.info(
                    f"Updated existing Moodle credential for user {user_info.email}"
                )
            else:
                # Create new credential
                credential = CloudOAuthCredentials(
                    id=str(uuid.uuid4()),
                    department_id=department_id,
                    provider=CloudProvider.MOODLE.value,
                    provider_instance_url=moodle_instance_url,
                    provider_user_id=user_info.id,
                    provider_user_email=user_info.email,
                    provider_user_name=user_info.fullname,
                    access_token=token_manager.encrypt_token(ws_token),
                    refresh_token=(
                        token_manager.encrypt_token(refresh_token)
                        if refresh_token
                        else None
                    ),
                    token_expires_at=expires_at,
                    scopes="webservice",  # Moodle web service access
                )

                db.add(credential)
                db.commit()

                credential_id = credential.id
                logger.info(f"Created new Moodle credential for user {user_info.email}")

            return {
                "success": True,
                "message": f"Successfully connected to Moodle as {user_info.fullname}",
                "credential_id": credential_id,
                "user": {
                    "id": user_info.id,
                    "email": user_info.email,
                    "fullname": user_info.fullname,
                },
            }

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Moodle OAuth callback failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete Moodle authentication: {str(e)}",
        )


# =============================================================================
# Connection Status
# =============================================================================


@router.get("/status")
async def get_moodle_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> MoodleConnectionStatus:
    """Get Moodle connection status for a department.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
        )
        .first()
    )

    if not credential:
        return MoodleConnectionStatus(connected=False)

    return MoodleConnectionStatus(
        connected=True,
        moodle_instance_url=credential.provider_instance_url,
        user_email=credential.provider_user_email,
        user_fullname=credential.provider_user_name,
        connected_at=credential.created_at,
        credential_id=credential.id,
    )


# =============================================================================
# Course and File Operations
# =============================================================================


@router.get("/courses")
async def list_moodle_courses(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all Moodle courses the user has access to.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Moodle not connected for this department",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get courses
    api_client = MoodleAPIClient(
        moodle_instance_url=credential.provider_instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        courses = await api_client.get_enrolled_courses()

        return [
            {
                "id": course.id,
                "fullname": course.fullname,
                "shortname": course.shortname,
                "category_id": course.categoryid,
                "summary": course.summary,
                "format": course.format,
                "visible": course.visible,
            }
            for course in courses
        ]

    finally:
        await api_client.close()


@router.get("/courses/{course_id}/files")
async def list_moodle_course_files(
    course_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all files in a Moodle course.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Moodle not connected for this department",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get course files
    api_client = MoodleAPIClient(
        moodle_instance_url=credential.provider_instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        files = await api_client.get_course_files(course_id)

        return [
            {
                "filename": file.filename,
                "filepath": file.filepath,
                "filesize": file.filesize,
                "fileurl": file.fileurl,
                "mimetype": file.mimetype,
                "timemodified": file.timemodified,
                "author": file.author,
            }
            for file in files
        ]

    finally:
        await api_client.close()


# =============================================================================
# Disconnect
# =============================================================================


@router.delete("/disconnect")
async def disconnect_moodle(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """Disconnect Moodle integration for a department.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Moodle not connected for this department",
        )

    # Delete credential
    db.delete(credential)
    db.commit()

    logger.info(f"Disconnected Moodle for department {department_id}")

    return {"message": "Moodle disconnected successfully"}
