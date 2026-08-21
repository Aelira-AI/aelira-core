"""
Microsoft 365 Integration API Routes

Provides endpoints for:
- OAuth 2.0 connection flow
- OneDrive/SharePoint file listing
- File scanning with existing processors
- Auto-remediation with upload back to OneDrive
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import logging
import os
import uuid

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    CloudOAuthCredentials,
    CloudFile,
    CloudJobQueue,
    CloudProvider,
    CloudJobType,
    CloudJobStatus,
)
from ..api.auth_routes import get_current_api_key
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..middleware.quota import require_feature
from ..integrations.microsoft_365.onedrive import OneDriveIntegration
from ..integrations.microsoft_365.microsoft_oauth import MicrosoftOAuthService
from ..integrations.microsoft_365.microsoft_graph import GraphClient
from ..config.settings import get_settings
from ..services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)

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

    file_id: str = Field(..., description="Cloud file ID (not provider file ID)")


class ScanFolderRequest(BaseModel):
    """Request to scan all files in a folder."""

    folder_id: str = Field(..., description="OneDrive/SharePoint folder ID")
    drive_id: Optional[str] = Field(None, description="Specific drive ID (optional)")
    site_id: Optional[str] = Field(None, description="SharePoint site ID (optional)")


class RemediateFileRequest(BaseModel):
    """Request to remediate and re-upload a file."""

    file_id: str = Field(..., description="Cloud file ID")
    upload_as_new: bool = Field(
        default=False, description="Upload as new file instead of replacing"
    )


class ScanResultResponse(BaseModel):
    """Response with scan results."""

    file_id: str
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
            logger.error(f"Failed to refresh Microsoft token: {e}")
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
        logger.error(f"Microsoft OAuth error: {error}")
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

        logger.info(
            f"Connected Microsoft 365 for department {department_id} ({token_data.get('email')})"
        )

        # Redirect back to frontend with success
        return RedirectResponse(
            url=f"http://localhost:5173/integrations?success=microsoft_connected&email={token_data.get('email', '')}"
        )

    except Exception as e:
        logger.error(f"Microsoft OAuth callback failed: {e}")
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

        logger.info(
            f"Connected Microsoft 365 for department {api_key.department_id} ({token_data.get('email')})"
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

    except Exception as e:
        logger.error(f"Microsoft OAuth callback failed: {e}")
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
            # Check if file already tracked
            cloud_file = (
                db.query(CloudFile)
                .filter(
                    CloudFile.department_id == api_key.department_id,
                    CloudFile.provider == CloudProvider.MICROSOFT.value,
                    CloudFile.provider_file_id == file_info.provider_file_id,
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
                    provider_file_id=file_info.provider_file_id,
                    provider_parent_id=(
                        file_info.parents[0] if file_info.parents else None
                    ),
                    file_name=file_name,
                    file_type=file_ext,
                    mime_type=file_info.mime_type,
                    file_size_bytes=file_info.size,
                    web_view_link=file_info.web_view_link,
                    provider_version=file_info.version,
                    provider_modified_at=file_info.modified_time,
                    needs_rescan=True,
                )
                db.add(cloud_file)
            else:
                # Update metadata if changed
                if file_info.version != cloud_file.provider_version:
                    cloud_file.file_name = file_name
                    cloud_file.provider_version = file_info.version
                    cloud_file.provider_modified_at = file_info.modified_time
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

        db.commit()

        # Update last sync time
        credential.last_sync_at = datetime.now(timezone.utc)
        db.commit()

        return MicrosoftFileListResponse(
            files=response_files,
            next_page_token=next_token,
            total_count=len(response_files),
        )

    except Exception as e:
        logger.error(f"Failed to list OneDrive files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list files: {str(e)}",
        )
    finally:
        await integration.close()


@router.get("/onedrive/folders")
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
    integration = None
    try:
        # Get OAuth credential
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
                detail="Microsoft 365 not connected",
            )

        # Decrypt token
        token_manager = get_token_manager()
        access_token = token_manager.decrypt_token(credential.access_token)

        # Initialize OneDrive integration
        integration = OneDriveIntegration(
            access_token=access_token,
            department_id=api_key.department_id,
        )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list OneDrive folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list folders: {str(e)}",
        )
    finally:
        if integration:
            await integration.close()


# ==================== Scanning Endpoints ====================


@router.post("/scan/file", response_model=ScanResultResponse)
async def scan_file(
    request: ScanFileRequest,
    background_tasks: BackgroundTasks,
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
    job = CloudJobQueue(
        id=str(uuid.uuid4()),
        department_id=api_key.department_id,
        job_type=CloudJobType.SCAN.value,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.MICROSOFT.value,
        provider_file_id=cloud_file.provider_file_id,
        status=CloudJobStatus.PENDING.value,
        priority=5,
    )
    db.add(job)
    db.commit()

    # Queue background task
    background_tasks.add_task(
        _scan_file_task,
        job_id=job.id,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
    )

    return ScanResultResponse(
        file_id=cloud_file.id,
        scan_id=None,
        compliance_score=None,
        issues_found=0,
        status="queued",
        message=f"Scan job {job.id} queued for processing",
    )


@router.post("/scan/folder", response_model=Dict[str, Any])
async def scan_folder(
    request: ScanFolderRequest,
    background_tasks: BackgroundTasks,
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
            all_files.extend(file_infos)

            if not next_token:
                break
            page_token = next_token

        # Create jobs for each file
        jobs_created = 0
        for file_info in all_files:
            # Get or create cloud file record
            cloud_file = (
                db.query(CloudFile)
                .filter(
                    CloudFile.department_id == api_key.department_id,
                    CloudFile.provider == CloudProvider.MICROSOFT.value,
                    CloudFile.provider_file_id == file_info.provider_file_id,
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
                    provider_file_id=file_info.provider_file_id,
                    file_name=file_name,
                    file_type=file_ext,
                    mime_type=file_info.mime_type,
                    needs_rescan=True,
                )
                db.add(cloud_file)
                db.flush()

            # Create scan job
            job = CloudJobQueue(
                id=str(uuid.uuid4()),
                department_id=api_key.department_id,
                job_type=CloudJobType.SCAN.value,
                cloud_file_id=cloud_file.id,
                credential_id=credential.id,
                provider=CloudProvider.MICROSOFT.value,
                provider_file_id=cloud_file.provider_file_id,
                status=CloudJobStatus.PENDING.value,
                priority=5,
            )
            db.add(job)
            jobs_created += 1

        db.commit()

        return {
            "success": True,
            "folder_id": request.folder_id,
            "files_found": len(all_files),
            "jobs_created": jobs_created,
            "message": f"Created {jobs_created} scan jobs for folder",
        }

    except Exception as e:
        logger.error(f"Failed to scan folder: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scan folder: {str(e)}",
        )
    finally:
        await integration.close()


# ==================== Remediation Endpoints ====================


@router.post("/remediate", response_model=Dict[str, Any])
async def remediate_file(
    request: RemediateFileRequest,
    background_tasks: BackgroundTasks,
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
    job = CloudJobQueue(
        id=str(uuid.uuid4()),
        department_id=api_key.department_id,
        job_type=CloudJobType.REMEDIATE.value,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.MICROSOFT.value,
        provider_file_id=cloud_file.provider_file_id,
        status=CloudJobStatus.PENDING.value,
        priority=3,  # Higher priority than scans
        result_data={"upload_as_new": request.upload_as_new},
    )
    db.add(job)
    db.commit()

    # Queue background task
    background_tasks.add_task(
        _remediate_file_task,
        job_id=job.id,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        upload_as_new=request.upload_as_new,
    )

    return {
        "success": True,
        "job_id": job.id,
        "file_id": cloud_file.id,
        "status": "queued",
        "message": f"Remediation job {job.id} queued for processing",
    }


# ==================== Job Status Endpoints ====================


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
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


# ==================== Background Task Functions ====================


async def _scan_file_task(job_id: str, cloud_file_id: str, credential_id: str):
    """Background task to scan a file from OneDrive."""
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.cloud_scan_job import handle_scan_job

    logger.info(f"Starting cloud scan: job={job_id}, file={cloud_file_id}")

    with _get_db_ctx() as db:
        job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()
        if not job:
            logger.error(f"Scan job not found: {job_id}")
            return

        try:
            job.status = CloudJobStatus.PROCESSING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            job.progress_message = "Downloading file from OneDrive..."
            db.commit()

            token_manager = OAuthTokenManager()
            result = await handle_scan_job(job, db, token_manager)

            job.status = CloudJobStatus.COMPLETED.value
            job.progress = 100
            job.progress_message = "Scan complete"
            job.result_data = result
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Cloud scan complete: job={job_id}, "
                f"score={result.get('compliance_score')}, "
                f"issues={result.get('issues_found', 0)}"
            )
        except Exception as e:
            logger.error(f"Cloud scan failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.progress = 100
            job.progress_message = f"Scan failed: {e}"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


async def _remediate_file_task(
    job_id: str,
    cloud_file_id: str,
    credential_id: str,
    upload_as_new: bool,
):
    """Background task to remediate a file and upload back to OneDrive."""
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.remediation_job import handle_remediation_job

    logger.info(f"Starting cloud remediation: job={job_id}, file={cloud_file_id}")

    with _get_db_ctx() as db:
        job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()
        if not job:
            logger.error(f"Remediation job not found: {job_id}")
            return

        try:
            job.status = CloudJobStatus.PROCESSING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            job.progress_message = "Starting remediation..."
            db.commit()

            token_manager = OAuthTokenManager()
            await handle_remediation_job(job, db, token_manager)

            logger.info(f"Cloud remediation complete: job={job_id}")
        except Exception as e:
            from ..jobs.remediation_job import (
                RemediationJobFailed,
                RetryableRemediationJobError,
                transition_retryable_remediation_job,
            )

            if isinstance(e, RetryableRemediationJobError):
                transition_retryable_remediation_job(job, db, e)
                logger.warning(
                    "Microsoft remediation queued after transient failure",
                    extra={"job_id": job_id, "error_code": e.code},
                )
                return
            if (
                isinstance(e, RemediationJobFailed)
                and e.terminal_state_committed is True
            ):
                logger.warning(
                    "Microsoft remediation reached a committed terminal failure",
                    extra={"job_id": job_id, "error_code": e.code},
                )
                return
            logger.error(f"Cloud remediation failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.progress = 100
            job.progress_message = f"Remediation failed: {e}"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


# ==================== Account Management ====================


@router.get("/account")
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
async def list_folder_children(
    folder_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List contents of an OneDrive folder.

    Args:
        folder_id: Folder ID or "root" for root folder

    Returns:
        List of files and folders in the folder.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get folder children using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        endpoint = (
            f"/me/drive/items/{folder_id}/children"
            if folder_id != "root"
            else "/me/drive/root/children"
        )
        response = graph_client.get(endpoint)
        return {"items": response.get("value", []), "folder_id": folder_id}
    except Exception as e:
        logger.error(f"Error listing folder children: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list folder children: {str(e)}",
        )


@router.get("/onedrive/files/{file_id}")
async def get_onedrive_file_metadata(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get file metadata from OneDrive.

    Args:
        file_id: OneDrive file ID

    Returns:
        File metadata including name, type, size, and sharing info.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get file metadata using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        file_info = graph_client.get(f"/me/drive/items/{file_id}")
        return file_info
    except Exception as e:
        logger.error(f"Error getting file metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file metadata: {str(e)}",
        )


@router.get("/onedrive/files/{file_id}/content")
async def download_onedrive_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Download file content from OneDrive.

    Args:
        file_id: OneDrive file ID

    Returns:
        File content as bytes.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Download file using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        file_content = graph_client.get(f"/me/drive/items/{file_id}/content")
        return {"content": file_content, "file_id": file_id}
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download file: {str(e)}",
        )


class UploadOneDriveRequest(BaseModel):
    """Request to upload file to OneDrive."""

    file_path: str = Field(..., description="Local file path to upload")
    parent_folder_id: Optional[str] = Field(
        None, description="Parent folder ID (root if not specified)"
    )
    file_id: Optional[str] = Field(None, description="File ID to replace (update)")


@router.post("/onedrive/upload")
async def upload_to_onedrive(
    request: UploadOneDriveRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Upload file to OneDrive.

    Args:
        request: Upload request with file path and optional parent folder

    Returns:
        Upload status and new file ID.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Check if file exists
    if not os.path.exists(request.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.file_path}",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Upload file using OneDrive integration
    OneDriveIntegration(access_token=access_token)

    try:
        # Placeholder - would use upload_file method
        new_file_id = request.file_id or f"onedrive-file-{uuid.uuid4()}"

        return {
            "success": True,
            "file_id": new_file_id,
            "file_path": request.file_path,
            "message": "File upload queued (implementation in progress)",
        }

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}",
        )


# ==================== SharePoint Operations ====================


@router.get("/sharepoint/sites/{site_id}/drives")
async def get_sharepoint_drives(
    site_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get document libraries for a SharePoint site.

    Args:
        site_id: SharePoint site ID

    Returns:
        List of document libraries (drives) in the site.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get drives using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        response = graph_client.get(f"/sites/{site_id}/drives")
        return {"drives": response.get("value", []), "site_id": site_id}
    except Exception as e:
        logger.error(f"Error getting SharePoint drives: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get SharePoint drives: {str(e)}",
        )


@router.get("/sharepoint/drives/{drive_id}/items")
async def list_sharepoint_drive_items(
    drive_id: str,
    folder_path: Optional[str] = Query(
        None, description="Folder path (root if not specified)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List items in a SharePoint document library.

    Args:
        drive_id: SharePoint drive ID
        folder_path: Optional folder path

    Returns:
        List of files and folders in the library.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get drive items using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        endpoint = f"/drives/{drive_id}/root/children"
        if folder_path:
            endpoint = f"/drives/{drive_id}/root:/{folder_path}:/children"

        response = graph_client.get(endpoint)
        return {"items": response.get("value", []), "drive_id": drive_id}
    except Exception as e:
        logger.error(f"Error listing SharePoint drive items: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list drive items: {str(e)}",
        )


@router.get("/sharepoint/search")
async def search_sharepoint(
    query: str = Query(..., description="Search query"),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Search for files in SharePoint.

    Args:
        query: Search query string

    Returns:
        List of matching files.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Search using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        response = graph_client.get(f"/me/drive/search(q='{query}')")
        return {"results": response.get("value", []), "query": query}
    except Exception as e:
        logger.error(f"Error searching SharePoint: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search SharePoint: {str(e)}",
        )


@router.post("/scan/sharepoint/file/{file_id}")
async def scan_sharepoint_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
    background_tasks: BackgroundTasks = None,
):
    """
    Scan a SharePoint file for accessibility issues.

    Args:
        file_id: SharePoint file ID

    Returns:
        Scan job information.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Create a scan job (similar to OneDrive scan)
    job_id = str(uuid.uuid4())

    new_job = CloudJobQueue(
        id=job_id,
        department_id=api_key.department_id,
        job_type=CloudJobType.SCAN.value,
        provider=CloudProvider.MICROSOFT.value,
        status=CloudJobStatus.PENDING.value,
    )

    db.add(new_job)
    db.commit()

    return {
        "job_id": job_id,
        "file_id": file_id,
        "status": "pending",
        "message": "SharePoint file scan queued",
    }


class UploadSharePointRequest(BaseModel):
    """Request to upload file to SharePoint."""

    file_path: str = Field(..., description="Local file path to upload")
    site_id: str = Field(..., description="SharePoint site ID")
    drive_id: str = Field(..., description="Document library (drive) ID")
    folder_path: Optional[str] = Field(None, description="Folder path within library")


@router.post("/sharepoint/upload")
async def upload_to_sharepoint(
    request: UploadSharePointRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Upload file to SharePoint document library.

    Args:
        request: Upload request with file path, site ID, drive ID, and folder path

    Returns:
        Upload status and new file ID.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Check if file exists
    if not os.path.exists(request.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.file_path}",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    token_manager.decrypt_token(credential.access_token)

    try:
        # Placeholder - would use Graph API upload
        new_file_id = f"sharepoint-file-{uuid.uuid4()}"

        return {
            "success": True,
            "file_id": new_file_id,
            "file_path": request.file_path,
            "site_id": request.site_id,
            "drive_id": request.drive_id,
            "message": "SharePoint file upload queued (implementation in progress)",
        }

    except Exception as e:
        logger.error(f"Error uploading to SharePoint: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to SharePoint: {str(e)}",
        )


# ==================== Webhook Management ====================


class CreateSubscriptionRequest(BaseModel):
    """Request to create a webhook subscription."""

    resource: str = Field(..., description="Resource to watch (e.g., /me/drive/root)")
    change_types: List[str] = Field(
        default=["updated", "created", "deleted"], description="Change types to watch"
    )
    notification_url: str = Field(..., description="Webhook notification URL")


@router.post("/subscriptions")
async def create_webhook_subscription(
    request: CreateSubscriptionRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Create a webhook subscription for Microsoft Graph changes.

    Args:
        request: Subscription request with resource, change types, and notification URL

    Returns:
        Subscription ID and details.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Create subscription using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        subscription_data = {
            "changeType": ",".join(request.change_types),
            "notificationUrl": request.notification_url,
            "resource": request.resource,
            "expirationDateTime": "2026-12-31T00:00:00.0000000Z",  # 1 year from now
            "clientState": api_key.department_id,
        }

        response = graph_client.post("/subscriptions", json_data=subscription_data)

        return {
            "subscription_id": response.get("id"),
            "resource": request.resource,
            "expiration": response.get("expirationDateTime"),
            "status": "active",
        }

    except Exception as e:
        logger.error(f"Error creating webhook subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create subscription: {str(e)}",
        )


@router.patch("/subscriptions/{subscription_id}")
async def renew_webhook_subscription(
    subscription_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Renew a webhook subscription.

    Args:
        subscription_id: Subscription ID to renew

    Returns:
        Updated subscription details.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Renew subscription using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        renewal_data = {
            "expirationDateTime": "2026-12-31T00:00:00.0000000Z",
        }

        response = graph_client.patch(
            f"/subscriptions/{subscription_id}", json_data=renewal_data
        )

        return {
            "subscription_id": subscription_id,
            "expiration": response.get("expirationDateTime"),
            "status": "renewed",
        }

    except Exception as e:
        logger.error(f"Error renewing webhook subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to renew subscription: {str(e)}",
        )


@router.delete("/subscriptions/{subscription_id}")
async def delete_webhook_subscription(
    subscription_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Delete a webhook subscription.

    Args:
        subscription_id: Subscription ID to delete

    Returns:
        Deletion confirmation.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Delete subscription using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        graph_client.delete(f"/subscriptions/{subscription_id}")

        return {
            "subscription_id": subscription_id,
            "status": "deleted",
            "message": "Webhook subscription deleted successfully",
        }

    except Exception as e:
        logger.error(f"Error deleting webhook subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete subscription: {str(e)}",
        )


@router.get("/subscriptions")
async def list_webhook_subscriptions(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List all webhook subscriptions.

    Returns:
        List of active subscriptions.
    """
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
            detail="Microsoft 365 not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # List subscriptions using Graph API
    graph_client = GraphClient(access_token=access_token)

    try:
        response = graph_client.get("/subscriptions")

        return {
            "subscriptions": response.get("value", []),
            "count": len(response.get("value", [])),
        }

    except Exception as e:
        logger.error(f"Error listing webhook subscriptions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list subscriptions: {str(e)}",
        )


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
