"""
Microsoft 365 Integration API Routes

Provides endpoints for:
- OAuth 2.0 connection flow
- OneDrive/SharePoint file listing
- File scanning with existing processors
- Auto-remediation with upload back to OneDrive
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import logging
import os
import uuid
import re
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
import httpx
from sqlalchemy.exc import SQLAlchemyError
from ..integrations.cloud_base import (
    CloudIntegrationError,
    CloudNotFoundError,
    CloudAuthError,
    CloudRateLimitError,
)

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    CloudOAuthCredentials,
    CloudFile,
    CloudJobQueue,
    CloudProvider,
    CloudJobType,
    CloudWebhookSubscription,
)
from ..api.auth_routes import get_current_api_key
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..middleware.quota import require_feature
from ..integrations.microsoft_365.onedrive import OneDriveIntegration
from ..integrations.microsoft_365.microsoft_oauth import MicrosoftOAuthService
from ..config.settings import get_settings
from ..services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)
from ..services.job_enqueue_service import enqueue_cloud_job

# Alias for test compatibility
get_db = get_db_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/microsoft", tags=["microsoft-365"])
settings = get_settings()


# ==================== Request/Response Models ====================


class MicrosoftConnectRequest(BaseModel):
    """Request to initiate Microsoft OAuth connection."""

    redirect_uri: str = Field(..., description="URI to redirect after OAuth")
    scopes: Optional[List[str]] = Field(
        default=None, description="OAuth scopes to request (defaults to Files + User)"
    )


class MicrosoftConnectResponse(BaseModel):
    """Response with OAuth authorization URL."""

    auth_url: str
    state: str


class MicrosoftCallbackRequest(BaseModel):
    """Request to complete OAuth callback."""

    code: str = Field(..., description="Authorization code from Microsoft")
    state: str = Field(..., description="State parameter for verification")
    redirect_uri: str = Field(..., description="Same redirect_uri used in connect")


class MicrosoftCredentialResponse(BaseModel):
    """Response with connected credential info."""

    id: str
    provider: str
    provider_email: Optional[str]
    provider_name: Optional[str]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime


class DriveResponse(BaseModel):
    """Response for a OneDrive/SharePoint drive."""

    id: str
    name: str
    drive_type: Optional[str]
    web_url: Optional[str]


class SiteResponse(BaseModel):
    """Response for a SharePoint site."""

    id: str
    name: str
    display_name: Optional[str]
    web_url: Optional[str]


class MicrosoftFileResponse(BaseModel):
    """Response for a OneDrive file."""

    id: str
    provider_file_id: str
    file_name: str
    file_type: str
    mime_type: Optional[str]
    file_size_bytes: Optional[int]
    web_view_link: Optional[str]
    last_scanned_at: Optional[datetime]
    last_compliance_score: Optional[float]
    needs_rescan: bool


class MicrosoftFileListResponse(BaseModel):
    """Response for file listing."""

    files: List[MicrosoftFileResponse]
    next_page_token: Optional[str]
    total_count: int


class ScanFileRequest(BaseModel):
    """Request to scan a specific file."""

    file_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Cloud file ID (not provider file ID)",
    )


class ScanFolderRequest(BaseModel):
    """Request to scan all files in a folder."""

    folder_id: str = Field(
        ..., min_length=1, max_length=255, description="OneDrive/SharePoint folder ID"
    )
    drive_id: Optional[str] = Field(None, description="Specific drive ID (optional)")
    site_id: Optional[str] = Field(None, description="SharePoint site ID (optional)")


class RemediateFileRequest(BaseModel):
    """Request to remediate and re-upload a file."""

    file_id: str = Field(..., min_length=1, max_length=255, description="Cloud file ID")
    upload_as_new: bool = Field(
        default=False, description="Upload as new file instead of replacing"
    )


class ScanResultResponse(BaseModel):
    """Response with scan results."""

    file_id: str
    job_id: Optional[str] = None
    scan_id: Optional[str]
    compliance_score: Optional[float]
    issues_found: int
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response for job status."""

    job_id: str
    status: str
    progress: int
    progress_message: Optional[str]
    result_data: Optional[Dict[str, Any]]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


# ==================== Helper Functions ====================


def get_token_manager() -> OAuthTokenManager:
    """Get OAuth token manager instance."""
    encryption_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not encryption_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token encryption key not configured",
        )
    return OAuthTokenManager(encryption_key)


async def get_microsoft_credential(
    api_key: APIKey,
    db: Session,
) -> CloudOAuthCredentials:
    """Get Microsoft OAuth credential for department, refreshing if needed."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Microsoft 365 not connected. Please connect first via /microsoft/connect",
        )

    # Check if token needs refresh
    token_manager = get_token_manager()
    if token_manager.is_token_expired(credential.token_expires_at):
        try:
            # Decrypt refresh token
            refresh_token = token_manager.decrypt_token(credential.refresh_token)

            # Refresh tokens
            new_access, new_refresh, new_expires = (
                await token_manager.refresh_microsoft_token(
                    refresh_token,
                    scopes=credential.scopes,
                )
            )

            # Update credential
            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(
                f"Refreshed Microsoft OAuth token for department {api_key.department_id}"
            )

        except Exception as e:
            logger.error("Failed to refresh Microsoft token: %s", type(e).__name__)
            credential.is_active = False
            credential.last_error = f"Token refresh failed: {str(e)}"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Microsoft connection expired. Please reconnect.",
            )

    return credential


async def get_microsoft_integration(
    credential: CloudOAuthCredentials,
    drive_id: Optional[str] = None,
    site_id: Optional[str] = None,
) -> OneDriveIntegration:
    """Get OneDrive integration instance with valid access token."""
    token_manager = get_token_manager()
    access_token = token_manager.decrypt_token(credential.access_token)

    return OneDriveIntegration(
        access_token=access_token,
        department_id=credential.department_id,
        drive_id=drive_id,
        site_id=site_id,
    )


def microsoft_errors(handler):
    """Bound provider/database failures and roll back incomplete local writes."""

    @wraps(handler)
    async def guarded(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            db = kwargs.get("db")
            if db is not None:
                db.rollback()
            logger.warning("Microsoft operation failed (%s)", type(exc).__name__)
            code = 503 if isinstance(exc, SQLAlchemyError) else 500
            if isinstance(exc, CloudNotFoundError):
                code = 404
            elif isinstance(exc, (CloudAuthError, CloudRateLimitError)):
                code = 503
            elif isinstance(exc, httpx.RequestError):
                code = 503
            elif isinstance(exc, CloudIntegrationError):
                code = 502
            elif isinstance(exc, httpx.HTTPStatusError):
                code = 404 if exc.response.status_code == 404 else 503
            raise HTTPException(code, "Microsoft operation unavailable") from None

    return guarded


async def microsoft_graph_get(api_key, db, endpoint):
    """Use the existing async Graph transport and always release its storage."""
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential)
    try:
        client = await integration._get_client()
        response = await client.get(f"{integration.GRAPH_API_BASE}{endpoint}")
        response.raise_for_status()
        return response.json()
    finally:
        await integration.close()


# ==================== OAuth Connection Endpoints ====================


@router.post("/connect", response_model=MicrosoftConnectResponse)
async def connect_microsoft(
    request: MicrosoftConnectRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Initiate Microsoft OAuth 2.0 connection.

    Returns an authorization URL that the user should visit to grant access.
    After authorization, Microsoft redirects to the specified redirect_uri with
    a code parameter that should be sent to /microsoft/callback.

    REQUIRES: cloud_integration feature
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Microsoft 365 Integration"
    )

    # Check if already connected
    existing = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Microsoft 365 already connected. Disconnect first to reconnect.",
        )

    token_manager = get_token_manager()

    # Server-side CSRF state bound to this department, one-time use, TTL'd.
    from ..auth.redis_rate_limiter import OAuthStateManager

    state = OAuthStateManager.create_state(
        metadata={"department_id": api_key.department_id, "provider": "microsoft"}
    )

    auth_url = token_manager.get_microsoft_auth_url(
        redirect_uri=request.redirect_uri,
        scopes=request.scopes,
        state=state,
    )

    logger.info(f"Generated Microsoft OAuth URL for department {api_key.department_id}")

    return MicrosoftConnectResponse(auth_url=auth_url, state=state)


@router.get("/callback")
async def microsoft_callback_get(
    code: str = Query(..., description="Authorization code from Microsoft"),
    state: str = Query(..., description="State parameter for verification"),
    error: Optional[str] = Query(default=None, description="Error from OAuth provider"),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Microsoft OAuth callback (GET redirect from Microsoft).

    This endpoint receives the OAuth redirect from Microsoft after user authorization.
    It exchanges the authorization code for tokens and stores the connection.
    """
    # Handle OAuth errors
    if error:
        logger.error("Microsoft OAuth authorization was denied")
        return RedirectResponse(
            url=f"http://localhost:5173/integrations?error=oauth_failed&message={error}"
        )

    # Verify + consume the server-side state (CSRF defence). department_id
    # comes ONLY from verified metadata, never the query string.
    from ..auth.redis_rate_limiter import OAuthStateManager

    is_valid, metadata = OAuthStateManager.verify_and_consume_state(state)
    department_id = (metadata or {}).get("department_id")
    if not is_valid or not department_id:
        logger.warning("Microsoft OAuth callback with invalid/expired state")
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=invalid_state"
        )

    token_manager = get_token_manager()

    try:
        # Exchange code for tokens
        token_data = await token_manager.exchange_microsoft_code(
            code=code,
            redirect_uri="http://localhost:8000/microsoft/callback",
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
        ).delete()

        # Create new credential
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=department_id,
            provider=CloudProvider.MICROSOFT.value,
            access_token=token_manager.encrypt_token(token_data["access_token"]),
            refresh_token=token_manager.encrypt_token(token_data["refresh_token"]),
            token_expires_at=token_data["expires_at"],
            provider_user_id=token_data.get("user_id"),
            provider_email=token_data.get("email"),
            provider_name=token_data.get("name"),
            scopes=token_data.get("scopes", []),
            is_active=True,
        )

        db.add(credential)
        db.commit()

        logger.info("Connected Microsoft 365 for department %s", department_id)

        # Redirect back to frontend with success
        return RedirectResponse(
            url=f"http://localhost:5173/integrations?success=microsoft_connected&email={token_data.get('email', '')}"
        )

    except Exception as e:
        logger.error("Microsoft OAuth callback failed: %s", type(e).__name__)
        return RedirectResponse(
            url=f"http://localhost:5173/integrations?error=exchange_failed&message={str(e)}"
        )


@router.post("/callback", response_model=MicrosoftCredentialResponse)
async def microsoft_callback(
    request: MicrosoftCallbackRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Microsoft OAuth callback.

    Exchange the authorization code for tokens and store the connection.
    """
    # Verify state contains correct department ID
    if not request.state.startswith(api_key.department_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state parameter"
        )

    token_manager = get_token_manager()

    try:
        # Exchange code for tokens (also returns user info)
        token_data = await token_manager.exchange_microsoft_code(
            code=request.code,
            redirect_uri=request.redirect_uri,
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
        ).delete()

        # Create new credential
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=api_key.department_id,
            provider=CloudProvider.MICROSOFT.value,
            access_token=token_manager.encrypt_token(token_data["access_token"]),
            refresh_token=token_manager.encrypt_token(token_data["refresh_token"]),
            token_expires_at=token_data["expires_at"],
            provider_user_id=token_data.get("user_id"),
            provider_email=token_data.get("email"),
            provider_name=token_data.get("name"),
            scopes=token_data.get("scopes", []),
            is_active=True,
        )

        db.add(credential)
        db.commit()
        db.refresh(credential)

        logger.info("Connected Microsoft 365 for department %s", api_key.department_id)

        return MicrosoftCredentialResponse(
            id=credential.id,
            provider=credential.provider,
            provider_email=credential.provider_email,
            provider_name=credential.provider_name,
            is_active=credential.is_active,
            last_sync_at=credential.last_sync_at,
            created_at=credential.created_at,
        )

    except Exception as e:
        logger.error("Microsoft OAuth callback failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth callback failed: {str(e)}",
        )


@router.delete("/disconnect")
async def disconnect_microsoft(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Disconnect Microsoft 365 integration.

    Removes stored credentials. Note: Microsoft tokens can only be
    fully revoked through user-initiated logout.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Microsoft 365 not connected"
        )

    # Managed artifacts use RESTRICT parents and must be handled explicitly.
    try:
        RemediationArtifactService.from_settings().delete_for_credential(
            db,
            department_id=api_key.department_id,
            credential_id=credential.id,
        )
    except ArtifactAuthorizationError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="artifact_cleanup_required"
        ) from None

    try:
        db.query(CloudJobQueue).filter(
            CloudJobQueue.credential_id == credential.id
        ).delete()
        db.query(CloudFile).filter(CloudFile.credential_id == credential.id).delete()
        db.delete(credential)
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(f"Disconnected Microsoft 365 for department {api_key.department_id}")

    return {"success": True, "message": "Microsoft 365 disconnected"}


@router.get("/status", response_model=MicrosoftCredentialResponse)
async def microsoft_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get Microsoft 365 connection status.

    REQUIRES: cloud_integration feature
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Microsoft 365 Integration"
    )

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Microsoft 365 not connected"
        )

    return MicrosoftCredentialResponse(
        id=credential.id,
        provider=credential.provider,
        provider_email=credential.provider_email,
        provider_name=credential.provider_name,
        is_active=credential.is_active,
        last_sync_at=credential.last_sync_at,
        created_at=credential.created_at,
    )


# ==================== Drive/Site Listing Endpoints ====================


@router.get("/drives", response_model=List[DriveResponse])
@microsoft_errors
async def list_drives(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List available OneDrive and SharePoint drives.

    REQUIRES: cloud_integration feature
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Microsoft 365 Integration"
    )

    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential)

    try:
        drives = await integration.get_drives()
        return [
            DriveResponse(
                id=drive.id,
                name=drive.name,
                drive_type=drive.drive_type,
                web_url=drive.web_url,
            )
            for drive in drives
        ]
    finally:
        await integration.close()


@router.get("/sites", response_model=List[SiteResponse])
@microsoft_errors
async def list_sites(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List accessible SharePoint sites.
    """
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential)

    try:
        sites = await integration.get_sites()
        return [
            SiteResponse(
                id=site.id,
                name=site.name,
                display_name=site.display_name,
                web_url=site.web_url,
            )
            for site in sites
        ]
    finally:
        await integration.close()


# ==================== File Listing Endpoints ====================


@router.get("/onedrive/files", response_model=MicrosoftFileListResponse)
@microsoft_errors
async def list_onedrive_files(
    folder_id: Optional[str] = Query(None, description="Folder ID (None for root)"),
    drive_id: Optional[str] = Query(None, description="Specific drive ID"),
    site_id: Optional[str] = Query(None, description="SharePoint site ID"),
    page_token: Optional[str] = Query(None, description="Page token for pagination"),
    page_size: int = Query(50, ge=1, le=100, description="Number of files per page"),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List accessible files from OneDrive or SharePoint.

    Returns files that can be scanned (Word, PowerPoint, Excel, PDFs).
    Files are automatically tracked in our database for change detection.
    """
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential, drive_id, site_id)

    try:
        # List files from OneDrive/SharePoint
        file_infos, next_token = await integration.list_files(
            folder_id=folder_id,
            page_token=page_token,
            page_size=page_size,
        )

        # Sync files to our database
        response_files = []
        for file_info in file_infos:
            if file_info.is_folder:
                continue
            # Check if file already tracked
            cloud_file = (
                db.query(CloudFile)
                .filter(
                    CloudFile.department_id == api_key.department_id,
                    CloudFile.provider == CloudProvider.MICROSOFT.value,
                    CloudFile.provider_file_id == file_info.id,
                )
                .first()
            )

            # Determine file type from name
            file_name = file_info.name or ""
            file_ext = (
                file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "unknown"
            )

            if not cloud_file:
                # Create new tracking record
                cloud_file = CloudFile(
                    id=str(uuid.uuid4()),
                    department_id=api_key.department_id,
                    credential_id=credential.id,
                    provider=CloudProvider.MICROSOFT.value,
                    provider_file_id=file_info.id,
                    provider_parent_id=file_info.parent_id,
                    file_name=file_name,
                    file_type=file_ext,
                    mime_type=file_info.mime_type,
                    file_size_bytes=file_info.size_bytes,
                    web_view_link=file_info.web_view_link,
                    provider_version=file_info.version,
                    provider_modified_at=file_info.modified_at,
                    needs_rescan=True,
                )
                db.add(cloud_file)
            else:
                # Update metadata if changed
                if file_info.version != cloud_file.provider_version:
                    cloud_file.file_name = file_name
                    cloud_file.provider_version = file_info.version
                    cloud_file.provider_modified_at = file_info.modified_at
                    cloud_file.needs_rescan = True

            response_files.append(
                MicrosoftFileResponse(
                    id=cloud_file.id,
                    provider_file_id=cloud_file.provider_file_id,
                    file_name=cloud_file.file_name,
                    file_type=cloud_file.file_type,
                    mime_type=cloud_file.mime_type,
                    file_size_bytes=cloud_file.file_size_bytes,
                    web_view_link=cloud_file.web_view_link,
                    last_scanned_at=cloud_file.last_scanned_at,
                    last_compliance_score=cloud_file.last_compliance_score,
                    needs_rescan=cloud_file.needs_rescan,
                )
            )

        credential.last_sync_at = datetime.now(timezone.utc)
        db.commit()

        return MicrosoftFileListResponse(
            files=response_files,
            next_page_token=next_token,
            total_count=len(response_files),
        )

    finally:
        await integration.close()


@router.get("/onedrive/folders")
@microsoft_errors
async def list_onedrive_folders(
    parent_id: Optional[str] = Query(
        None, description="Parent folder ID (None for root)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List folders in Microsoft OneDrive/SharePoint.

    Used for folder selection UI to choose which folders to sync.
    Returns folder hierarchy for privacy-conscious syncing.
    """
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential)
    try:
        # List folders
        folders = await integration.list_folders(parent_folder_id=parent_id)

        return {
            "folders": [
                {
                    "id": folder.id,
                    "name": folder.name,
                    "parent_id": folder.parent_id,
                    "web_view_link": folder.web_view_link,
                    "file_count": folder.file_count,
                }
                for folder in folders
            ]
        }

    finally:
        await integration.close()


# ==================== Scanning Endpoints ====================


@router.post("/scan/file", response_model=ScanResultResponse)
@microsoft_errors
async def scan_file(
    request: ScanFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Scan a single file for accessibility issues.

    Downloads the file from OneDrive, scans with our processors,
    and stores the results. Returns immediately with job status.
    """
    # Get cloud file record
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == request.file_id,
            CloudFile.provider == CloudProvider.MICROSOFT.value,
            CloudFile.department_id == api_key.department_id,
        )
        .first()
    )

    if not cloud_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        )

    credential = await get_microsoft_credential(api_key, db)

    # Create scan job
    job = enqueue_cloud_job(
        db,
        department_id=api_key.department_id,
        job_type=CloudJobType.SCAN.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.MICROSOFT.value,
            "provider_file_id": cloud_file.provider_file_id,
        },
        dedupe_key=f"scan:microsoft:{cloud_file.id}:{cloud_file.provider_version or 'current'}",
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.MICROSOFT.value,
        provider_file_id=cloud_file.provider_file_id,
        priority=5,
    )
    db.commit()

    return ScanResultResponse(
        file_id=cloud_file.id,
        job_id=job.id,
        scan_id=None,
        compliance_score=None,
        issues_found=0,
        status="queued",
        message=f"Scan job {job.id} queued for processing",
    )


@router.post("/scan/folder", response_model=Dict[str, Any])
@microsoft_errors
async def scan_folder(
    request: ScanFolderRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Scan all accessible files in a OneDrive/SharePoint folder.

    Creates scan jobs for each file found. Returns summary of jobs created.
    """
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(
        credential, request.drive_id, request.site_id
    )

    try:
        # List all files in folder
        all_files = []
        page_token = None

        while True:
            file_infos, next_token = await integration.list_files(
                folder_id=request.folder_id,
                page_token=page_token,
                page_size=100,
            )
            all_files.extend(info for info in file_infos if not info.is_folder)

            if not next_token:
                break
            page_token = next_token

        # Create jobs for each file
        jobs_created = 0
        job_ids = []
        for file_info in all_files:
            # Get or create cloud file record
            cloud_file = (
                db.query(CloudFile)
                .filter(
                    CloudFile.department_id == api_key.department_id,
                    CloudFile.provider == CloudProvider.MICROSOFT.value,
                    CloudFile.provider_file_id == file_info.id,
                )
                .first()
            )

            file_name = file_info.name or ""
            file_ext = (
                file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "unknown"
            )

            if not cloud_file:
                cloud_file = CloudFile(
                    id=str(uuid.uuid4()),
                    department_id=api_key.department_id,
                    credential_id=credential.id,
                    provider=CloudProvider.MICROSOFT.value,
                    provider_file_id=file_info.id,
                    file_name=file_name,
                    file_type=file_ext,
                    mime_type=file_info.mime_type,
                    needs_rescan=True,
                )
                db.add(cloud_file)
                db.flush()

            # Create scan job
            job = enqueue_cloud_job(
                db,
                department_id=api_key.department_id,
                job_type=CloudJobType.SCAN.value,
                payload={
                    "cloud_file_id": cloud_file.id,
                    "credential_id": credential.id,
                    "provider": CloudProvider.MICROSOFT.value,
                    "provider_file_id": cloud_file.provider_file_id,
                },
                dedupe_key=f"scan:microsoft:{cloud_file.id}:{cloud_file.provider_version or 'current'}",
                cloud_file_id=cloud_file.id,
                credential_id=credential.id,
                provider=CloudProvider.MICROSOFT.value,
                provider_file_id=cloud_file.provider_file_id,
                priority=5,
            )
            jobs_created += 1
            job_ids.append(job.id)

        db.commit()

        return {
            "success": True,
            "folder_id": request.folder_id,
            "files_found": len(all_files),
            "jobs_created": jobs_created,
            "job_ids": job_ids,
            "message": f"Created {jobs_created} scan jobs for folder",
        }

    finally:
        await integration.close()


# ==================== Remediation Endpoints ====================


@router.post("/remediate", response_model=Dict[str, Any])
@microsoft_errors
async def remediate_file(
    request: RemediateFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Remediate a file and upload the fixed version back to OneDrive.

    Downloads the file, applies accessibility fixes, and either
    replaces the original or uploads as a new file.
    """
    # Get cloud file record
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == request.file_id,
            CloudFile.provider == CloudProvider.MICROSOFT.value,
            CloudFile.department_id == api_key.department_id,
        )
        .first()
    )

    if not cloud_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        )

    if not cloud_file.last_scan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File has not been scanned yet. Scan first before remediation.",
        )

    credential = await get_microsoft_credential(api_key, db)

    # Create remediation job
    job = enqueue_cloud_job(
        db,
        department_id=api_key.department_id,
        job_type=CloudJobType.REMEDIATE.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.MICROSOFT.value,
            "provider_file_id": cloud_file.provider_file_id,
            "scan_id": cloud_file.last_scan_id,
            "upload_as_new": request.upload_as_new,
        },
        dedupe_key=(
            f"remediate:microsoft:{cloud_file.id}:{cloud_file.last_scan_id}:"
            f"upload-new={str(request.upload_as_new).lower()}"
        ),
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.MICROSOFT.value,
        provider_file_id=cloud_file.provider_file_id,
        priority=3,  # Higher priority than scans
    )
    db.commit()

    return {
        "success": True,
        "job_id": job.id,
        "file_id": cloud_file.id,
        "status": "queued",
        "message": f"Remediation job {job.id} queued for processing",
    }


# ==================== Job Status Endpoints ====================


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
@microsoft_errors
async def get_job_status(
    job_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get the status of a cloud job.
    """
    job = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.id == job_id,
            CloudJobQueue.provider == CloudProvider.MICROSOFT.value,
            CloudJobQueue.department_id == api_key.department_id,
        )
        .first()
    )

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        progress_message=job.progress_message,
        result_data=job.result_data,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/jobs", response_model=List[JobStatusResponse])
@microsoft_errors
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List cloud jobs for the department.
    """
    query = db.query(CloudJobQueue).filter(
        CloudJobQueue.department_id == api_key.department_id,
        CloudJobQueue.provider == CloudProvider.MICROSOFT.value,
    )

    if status:
        query = query.filter(CloudJobQueue.status == status)

    jobs = query.order_by(CloudJobQueue.created_at.desc()).limit(limit).all()

    return [
        JobStatusResponse(
            job_id=job.id,
            status=job.status,
            progress=job.progress,
            progress_message=job.progress_message,
            result_data=job.result_data,
            error_message=job.error_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        for job in jobs
    ]


# ==================== Account Management ====================


@router.get("/account")
@microsoft_errors
async def get_microsoft_account(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get connected Microsoft account information.

    Returns:
        Account info including email, name, connection time, and last sync time.
    """
    oauth_service = MicrosoftOAuthService()
    account_info = oauth_service.get_account_info(
        department_id=api_key.department_id, db=db
    )

    if not account_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Microsoft 365 not connected",
        )

    return account_info


# ==================== OneDrive Operations ====================


@router.get("/onedrive/folders/{folder_id}/children")
@microsoft_errors
async def list_folder_children(
    folder_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    endpoint = (
        "/me/drive/root/children"
        if folder_id == "root"
        else f"/me/drive/items/{folder_id}/children"
    )
    data = await microsoft_graph_get(api_key, db, endpoint)
    return {"items": data.get("value", []), "folder_id": folder_id}


@router.get("/onedrive/files/{file_id}")
@microsoft_errors
async def get_onedrive_file_metadata(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    return await microsoft_graph_get(api_key, db, f"/me/drive/items/{file_id}")


@router.get("/onedrive/files/{file_id}/content")
@microsoft_errors
async def download_onedrive_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    credential = await get_microsoft_credential(api_key, db)
    integration = await get_microsoft_integration(credential)
    try:
        local_path = str(Path(integration._temp_dir) / "download")
        result = await integration.download_file(file_id, local_path)
        if not result.success:
            raise HTTPException(502, "Microsoft download unavailable")
        return Response(
            content=Path(local_path).read_bytes(),
            media_type=result.mime_type or "application/octet-stream",
        )
    finally:
        await integration.close()


class UploadOneDriveRequest(BaseModel):
    """Legacy local-path upload shape; managed artifact publication is supported separately."""

    file_path: str = Field(..., min_length=1)
    parent_folder_id: Optional[str] = None
    file_id: Optional[str] = None


@router.post("/onedrive/upload")
@microsoft_errors
async def upload_to_onedrive(
    request: UploadOneDriveRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    await get_microsoft_credential(api_key, db)
    raise HTTPException(
        501,
        "Local-path uploads are not supported; publish an approved managed artifact",
    )


@router.get("/sharepoint/sites/{site_id}/drives")
@microsoft_errors
async def get_sharepoint_drives(
    site_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    data = await microsoft_graph_get(api_key, db, f"/sites/{site_id}/drives")
    return {"drives": data.get("value", []), "site_id": site_id}


@router.get("/sharepoint/drives/{drive_id}/items")
@microsoft_errors
async def list_sharepoint_drive_items(
    drive_id: str,
    folder_path: Optional[str] = Query(None),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    endpoint = (
        f"/drives/{drive_id}/root/children"
        if not folder_path
        else f"/drives/{drive_id}/root:/{folder_path}:/children"
    )
    data = await microsoft_graph_get(api_key, db, endpoint)
    return {"items": data.get("value", []), "drive_id": drive_id}


@router.get("/sharepoint/search")
@microsoft_errors
async def search_sharepoint(
    query: str = Query(..., min_length=1, max_length=255),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    data = await microsoft_graph_get(
        api_key, db, f"/me/drive/search(q='{query.replace(chr(39), chr(39) * 2)}')"
    )
    return {"results": data.get("value", []), "query": query}


@router.post("/scan/sharepoint/file/{file_id}")
@microsoft_errors
async def scan_sharepoint_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    credential = await get_microsoft_credential(api_key, db)
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == api_key.department_id,
            CloudFile.provider == CloudProvider.MICROSOFT.value,
            CloudFile.credential_id == credential.id,
            CloudFile.provider_file_id == file_id,
        )
        .first()
    )
    if cloud_file is None:
        raise HTTPException(404, "File not found; list files before requesting a scan")
    new_job = enqueue_cloud_job(
        db,
        department_id=api_key.department_id,
        job_type=CloudJobType.SCAN.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": "microsoft",
            "provider_file_id": file_id,
        },
        dedupe_key=f"scan:microsoft:{cloud_file.id}:{cloud_file.provider_version or 'current'}",
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider="microsoft",
        provider_file_id=file_id,
    )
    db.commit()
    return {
        "job_id": new_job.id,
        "file_id": file_id,
        "status": new_job.status,
        "message": "SharePoint file scan queued",
    }


class UploadSharePointRequest(BaseModel):
    file_path: str = Field(..., min_length=1)
    site_id: str = Field(..., min_length=1)
    drive_id: str = Field(..., min_length=1)
    folder_path: Optional[str] = None


@router.post("/sharepoint/upload")
@microsoft_errors
async def upload_to_sharepoint(
    request: UploadSharePointRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    await get_microsoft_credential(api_key, db)
    raise HTTPException(
        501,
        "Local-path uploads are not supported; publish an approved managed artifact",
    )


class CreateSubscriptionRequest(BaseModel):
    """Drive-root notifications supported by the existing OneDrive adapter."""

    resource: str = Field(..., max_length=1024)
    change_types: List[str] = Field(default_factory=lambda: ["updated"])
    notification_url: str = Field(..., max_length=1024)

    @field_validator("resource")
    @classmethod
    def supported_resource(cls, value):
        if not re.fullmatch(
            r"/me/drive/root|/drives/[^/?#]+/root|/sites/[^/?#]+/drive/root", value
        ):
            raise ValueError("unsupported drive-root resource")
        return value

    @field_validator("change_types")
    @classmethod
    def supported_changes(cls, value):
        if value != ["updated"]:
            raise ValueError("drive-root subscriptions support updated notifications")
        return value

    @field_validator("notification_url")
    @classmethod
    def https_callback(cls, value):
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "notification URL must use HTTPS without credentials or fragment"
            )
        return value


def scoped_subscription(db, department_id, subscription_id):
    row = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.department_id == department_id,
            CloudWebhookSubscription.provider == "microsoft",
            CloudWebhookSubscription.subscription_id == subscription_id,
            CloudWebhookSubscription.is_active,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Subscription not found")
    return row


@router.post("/subscriptions")
@microsoft_errors
async def create_webhook_subscription(
    request: CreateSubscriptionRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    credential = await get_microsoft_credential(api_key, db)
    # Serialize initial intent creation within one credential.
    db.query(CloudOAuthCredentials).filter(
        CloudOAuthCredentials.id == credential.id
    ).with_for_update().one()
    existing = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.department_id == api_key.department_id,
            CloudWebhookSubscription.credential_id == credential.id,
            CloudWebhookSubscription.provider == "microsoft",
            CloudWebhookSubscription.resource_uri == request.resource,
            CloudWebhookSubscription.notification_url == request.notification_url,
            CloudWebhookSubscription.renewal_status.in_(
                ["requesting", "indeterminate", "created", "renewed"]
            ),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            409, "Subscription already exists or requires reconciliation"
        )
    intent_id = str(uuid.uuid4())
    row = CloudWebhookSubscription(
        id=intent_id,
        department_id=api_key.department_id,
        credential_id=credential.id,
        provider="microsoft",
        subscription_id=intent_id,
        provider_resource_id=request.resource,
        resource_uri=request.resource,
        notification_url=request.notification_url,
        expiration_time=datetime.now(timezone.utc),
        is_active=False,
        renewal_status="requesting",
        pending_renewal_channel_id=intent_id,
        pending_renewal_started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()  # Persist intent before a remote side effect; failures cannot imply remote rollback.
    drive_id = (
        request.resource.split("/")[2]
        if request.resource.startswith("/drives/")
        else None
    )
    site_id = (
        request.resource.split("/")[2]
        if request.resource.startswith("/sites/")
        else None
    )
    integration = await get_microsoft_integration(credential, drive_id, site_id)
    try:
        result = await integration.create_webhook(request.notification_url)
        row.subscription_id = result["subscription_id"]
        row.expiration_time = result["expiration_time"]
        row.is_active = True
        row.renewal_status = "created"
        row.pending_renewal_channel_id = None
        row.pending_renewal_started_at = None
        db.commit()
    except Exception:
        db.rollback()
        # The committed intent remains requesting: automatic retries are blocked.
        raise
    finally:
        await integration.close()
    return {
        "subscription_id": row.subscription_id,
        "resource": row.resource_uri,
        "expiration": row.expiration_time,
        "status": "active",
    }


def begin_subscription_mutation(db, row, operation):
    """Keep a durable manual-reconciliation boundary around provider mutations."""
    if row.renewal_status in {"requesting", "indeterminate"}:
        raise HTTPException(409, "Subscription requires reconciliation")
    row.renewal_status = "requesting"
    row.pending_renewal_channel_id = str(uuid.uuid4())
    row.pending_renewal_started_at = datetime.now(timezone.utc)
    row.renewal_result = {
        "provider": "microsoft",
        "operation": operation,
        "status": "requesting",
    }
    db.commit()


def complete_subscription_mutation(row, outcome):
    row.renewal_status = outcome
    row.pending_renewal_channel_id = None
    row.pending_renewal_started_at = None
    row.renewal_result = {"provider": "microsoft", "status": outcome}


@router.patch("/subscriptions/{subscription_id}")
@microsoft_errors
async def renew_webhook_subscription(
    subscription_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    # Refresh may commit: resolve credentials before taking the subscription lock.
    credential = await get_microsoft_credential(api_key, db)
    row = scoped_subscription(db, api_key.department_id, subscription_id)
    if credential.id != row.credential_id:
        raise HTTPException(409, "Subscription credential has changed")
    begin_subscription_mutation(db, row, "renew")
    integration = await get_microsoft_integration(credential)
    try:
        result = await integration.renew_webhook(subscription_id)
        row.expiration_time = result["expiration_time"]
        row.last_renewed_at = datetime.now(timezone.utc)
        complete_subscription_mutation(row, "renewed")
        db.commit()
        return {
            "subscription_id": subscription_id,
            "expiration": row.expiration_time,
            "status": "renewed",
        }
    finally:
        await integration.close()


@router.delete("/subscriptions/{subscription_id}")
@microsoft_errors
async def delete_webhook_subscription(
    subscription_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    # Refresh may commit: resolve credentials before taking the subscription lock.
    credential = await get_microsoft_credential(api_key, db)
    row = scoped_subscription(db, api_key.department_id, subscription_id)
    if credential.id != row.credential_id:
        raise HTTPException(409, "Subscription credential has changed")
    begin_subscription_mutation(db, row, "delete")
    integration = await get_microsoft_integration(credential)
    try:
        if not await integration.delete_webhook(subscription_id):
            raise HTTPException(502, "Microsoft subscription deletion unavailable")
        row.is_active = False
        complete_subscription_mutation(row, "deleted")
        db.commit()
        return {
            "subscription_id": subscription_id,
            "status": "deleted",
            "message": "Webhook subscription deleted successfully",
        }
    finally:
        await integration.close()


@router.get("/subscriptions")
@microsoft_errors
async def list_webhook_subscriptions(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    await get_microsoft_credential(api_key, db)
    rows = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.department_id == api_key.department_id,
            CloudWebhookSubscription.provider == "microsoft",
            CloudWebhookSubscription.is_active,
        )
        .all()
    )
    subscriptions = [
        {
            "id": row.subscription_id,
            "resource": row.resource_uri,
            "expirationDateTime": row.expiration_time,
        }
        for row in rows
    ]
    return {"subscriptions": subscriptions, "count": len(subscriptions)}


# ==================== Health Check ====================


@router.get("/health")
async def microsoft_health():
    """Health check for Microsoft integration."""
    microsoft_client_id = os.environ.get("MICROSOFT_CLIENT_ID")
    microsoft_client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET")

    return {
        "status": (
            "healthy"
            if microsoft_client_id and microsoft_client_secret
            else "unconfigured"
        ),
        "service": "microsoft-365",
        "configured": bool(microsoft_client_id and microsoft_client_secret),
        "features": [
            "oauth-connection",
            "onedrive-file-listing",
            "sharepoint-access",
            "file-scanning",
            "auto-remediation",
        ],
    }
