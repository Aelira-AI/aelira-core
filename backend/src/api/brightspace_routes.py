"""
D2L Brightspace REST API Routes

Handles OAuth 2.0 authentication and file operations with Brightspace LMS.

Market Impact: +15% US, +10% Australia (community colleges)
"""

import os
import logging
import secrets
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
from ..integrations.brightspace import (
    get_brightspace_authorization_url,
    exchange_brightspace_code_for_token,
    BrightspaceAPIClient,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..api.auth_routes import get_current_api_key
from ..auth.redis_rate_limiter import OAuthStateManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brightspace", tags=["brightspace"])

# =============================================================================
# Request/Response Models
# =============================================================================


class BrightspaceConnectRequest(BaseModel):
    """Request to initiate Brightspace OAuth connection"""

    brightspace_instance_url: str = Field(
        ...,
        description="Brightspace instance URL (e.g., https://university.brightspace.com)",
    )
    department_id: str = Field(..., description="Department ID")
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
    """Request to remediate a Brightspace file"""

    file_url: str = Field(..., description="Brightspace file URL")
    org_unit_id: int = Field(..., description="Brightspace course ID (OrgUnitId)")
    department_id: str
    upload_back: bool = Field(
        True, description="Upload remediated file back to Brightspace"
    )
    use_ai: bool = Field(True, description="Use AI for fix generation")


class BrightspaceRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str


# =============================================================================
# OAuth Flow
# =============================================================================


@router.post("/connect")
async def connect_brightspace(
    request: BrightspaceConnectRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """
    Initiate Brightspace OAuth 2.0 flow.

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
            "brightspace_instance_url": request.brightspace_instance_url,
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
            brightspace_instance_url=request.brightspace_instance_url,
            redirect_uri=redirect_uri,
            state=state,
        )

        logger.info(
            f"Initiated Brightspace OAuth for department {request.department_id} at {request.brightspace_instance_url}"
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
                existing.provider_instance_url = brightspace_instance_url
                existing.provider_user_email = (
                    user_info.UniqueName
                )  # Use username as email
                existing.provider_user_name = (
                    f"{user_info.FirstName} {user_info.LastName}"
                )
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()

                credential_id = existing.id
                logger.info(
                    f"Updated existing Brightspace credential for user {user_info.UniqueName}"
                )
            else:
                # Create new credential
                credential = CloudOAuthCredentials(
                    id=str(uuid.uuid4()),
                    department_id=department_id,
                    provider=CloudProvider.BRIGHTSPACE.value,
                    provider_instance_url=brightspace_instance_url,
                    provider_user_id=user_info.Identifier,
                    provider_user_email=user_info.UniqueName,
                    provider_user_name=f"{user_info.FirstName} {user_info.LastName}",
                    access_token=token_manager.encrypt_token(access_token),
                    refresh_token=(
                        token_manager.encrypt_token(refresh_token)
                        if refresh_token
                        else None
                    ),
                    token_expires_at=expires_at,
                    scopes="core:*:*",  # Full API access
                )

                db.add(credential)
                db.commit()

                credential_id = credential.id
                logger.info(
                    f"Created new Brightspace credential for user {user_info.UniqueName}"
                )

            return {
                "success": True,
                "message": f"Successfully connected to Brightspace as {user_info.FirstName} {user_info.LastName}",
                "credential_id": credential_id,
                "user": {
                    "id": user_info.Identifier,
                    "username": user_info.UniqueName,
                    "first_name": user_info.FirstName,
                    "last_name": user_info.LastName,
                },
            }

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Brightspace OAuth callback failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete Brightspace authentication: {str(e)}",
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
        brightspace_instance_url=credential.provider_instance_url,
        user_email=credential.provider_user_email,
        user_fullname=credential.provider_user_name,
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

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get courses
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_instance_url,
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        courses = await api_client.get_my_enrollments()

        return [
            {
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

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get course content
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_instance_url,
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
# Disconnect
# =============================================================================


@router.delete("/disconnect")
async def disconnect_brightspace(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """Disconnect Brightspace integration for a department.

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

    # Delete credential
    db.delete(credential)
    db.commit()

    logger.info(f"Disconnected Brightspace for department {department_id}")

    return {"message": "Brightspace disconnected successfully"}
