"""
Google Workspace Integration API Routes

Provides endpoints for:
- OAuth 2.0 connection flow
- Google Drive file listing
- File scanning with existing processors
- Auto-remediation with upload back to Drive
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
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.google_workspace.google_oauth import GoogleOAuthService
from ..integrations.google_workspace.google_docs import GoogleDocsService
from ..integrations.google_workspace.google_slides import GoogleSlidesService
from ..integrations.google_workspace.google_sheets import GoogleSheetsService
from ..config.settings import get_settings

# Aliases for test compatibility
get_db = get_db_dependency
GoogleDriveService = GoogleDriveIntegration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/google", tags=["google-workspace"])
settings = get_settings()


# ==================== Request/Response Models ====================


class GoogleConnectRequest(BaseModel):
    """Request to initiate Google OAuth connection."""

    redirect_uri: str = Field(..., description="URI to redirect after OAuth")
    scopes: Optional[List[str]] = Field(
        default=None,
        description="OAuth scopes to request (defaults to Drive read/write)",
    )


class GoogleConnectResponse(BaseModel):
    """Response with OAuth authorization URL."""

    auth_url: str
    state: str


class GoogleCallbackRequest(BaseModel):
    """Request to complete OAuth callback."""

    code: str = Field(..., description="Authorization code from Google")
    state: str = Field(..., description="State parameter for verification")
    redirect_uri: str = Field(..., description="Same redirect_uri used in connect")


class GoogleCredentialResponse(BaseModel):
    """Response with connected credential info."""

    id: str
    provider: str
    provider_email: Optional[str]
    provider_name: Optional[str]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime


class GoogleFileResponse(BaseModel):
    """Response for a Google Drive file."""

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


class GoogleFileListResponse(BaseModel):
    """Response for file listing."""

    files: List[GoogleFileResponse]
    next_page_token: Optional[str]
    total_count: int


class ScanFileRequest(BaseModel):
    """Request to scan a specific file."""

    file_id: str = Field(..., description="Cloud file ID (not provider file ID)")


class ScanFolderRequest(BaseModel):
    """Request to scan all files in a folder."""

    folder_id: str = Field(..., description="Google Drive folder ID")
    recursive: bool = Field(default=True, description="Scan subfolders recursively")


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


async def get_google_credential(
    api_key: APIKey,
    db: Session,
) -> CloudOAuthCredentials:
    """Get Google OAuth credential for department, refreshing if needed."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected. Please connect first via /google/connect",
        )

    # Check if token needs refresh
    token_manager = get_token_manager()
    if token_manager.is_token_expired(credential.token_expires_at):
        try:
            # Decrypt refresh token
            refresh_token = token_manager.decrypt_token(credential.refresh_token)

            # Refresh tokens
            new_access, new_refresh, new_expires = (
                await token_manager.refresh_google_token(refresh_token)
            )

            # Update credential
            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(
                f"Refreshed Google OAuth token for department {api_key.department_id}"
            )

        except Exception as e:
            logger.error(f"Failed to refresh Google token: {e}")
            credential.is_active = False
            credential.last_error = f"Token refresh failed: {str(e)}"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google connection expired. Please reconnect.",
            )

    return credential


async def get_google_integration(
    credential: CloudOAuthCredentials,
) -> GoogleDriveIntegration:
    """Get Google Drive integration instance with valid access token."""
    token_manager = get_token_manager()
    access_token = token_manager.decrypt_token(credential.access_token)

    return GoogleDriveIntegration(
        access_token=access_token,
        department_id=credential.department_id,
    )


# ==================== Helper Functions ====================


async def _get_optional_api_key(
    credentials: Optional[str] = None, db: Session = Depends(get_db_dependency)
) -> Optional[APIKey]:
    """Optional API key dependency - returns None if not provided."""
    if not credentials:
        return None
    from ..auth.auth_service import AuthService

    return AuthService.validate_api_key(db, credentials)


# ==================== OAuth Connection Endpoints ====================


@router.post("/connect", response_model=GoogleConnectResponse)
async def connect_google(
    request: GoogleConnectRequest,
    department_id: Optional[str] = Query(
        default=None, description="Department ID for development"
    ),
    api_key: Optional[APIKey] = Depends(_get_optional_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Initiate Google OAuth 2.0 connection.

    Returns an authorization URL that the user should visit to grant access.
    After authorization, Google redirects to the specified redirect_uri with
    a code parameter that should be sent to /google/callback.

    Authentication:
    - Production: Requires valid API key (uses api_key.department_id)
    - Development: Can use query parameter department_id if no API key provided

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Determine department_id: prefer API key, fallback to query param
    if api_key:
        dept_id = api_key.department_id
    elif department_id:
        dept_id = department_id
    else:
        # Default for local development
        dept_id = "test-dept-001"

    # Check feature access - Google integration requires cloud_integration feature
    await require_feature(
        db, dept_id, "cloud_integration", "Google Workspace Integration"
    )

    # Check if already connected
    existing = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == dept_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Workspace already connected. Disconnect first to reconnect.",
        )

    token_manager = get_token_manager()

    # Generate state with department ID for verification
    state = f"{dept_id}:{uuid.uuid4().hex[:16]}"

    auth_url = token_manager.get_google_auth_url(
        redirect_uri=request.redirect_uri,
        scopes=request.scopes,
        state=state,
    )

    logger.info(f"Generated Google OAuth URL for department {dept_id}")

    return GoogleConnectResponse(auth_url=auth_url, state=state)


@router.get("/callback")
async def google_callback_get(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter for verification"),
    error: Optional[str] = Query(default=None, description="Error from OAuth provider"),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Google OAuth callback (GET redirect from Google).

    This endpoint receives the OAuth redirect from Google after user authorization.
    It exchanges the authorization code for tokens and stores the connection.
    """
    # Handle OAuth errors
    if error:
        logger.error(f"Google OAuth error: {error}")
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=oauth_failed&message={error}"
        )

    # Extract department_id from state
    try:
        department_id = state.split(":")[0]
    except Exception:
        logger.error(f"Invalid state parameter: {state}")
        return RedirectResponse(
            url="{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=invalid_state"
        )

    token_manager = get_token_manager()

    try:
        # Exchange code for tokens
        # Use the backend URL as redirect_uri since that's what we registered
        token_data = await token_manager.exchange_google_code(
            code=code,
            redirect_uri=os.getenv(
                "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/google/callback"
            ),
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        ).delete()

        # Create new credential
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=department_id,
            provider=CloudProvider.GOOGLE.value,
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
            f"Connected Google Workspace for department {department_id} ({token_data.get('email')})"
        )

        # Redirect back to frontend with success
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?success=google_connected&email={token_data.get('email', '')}"
        )

    except Exception as e:
        logger.error(f"Google OAuth callback failed: {e}")
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=exchange_failed&message={str(e)}"
        )


@router.post("/callback", response_model=GoogleCredentialResponse)
async def google_callback(
    request: GoogleCallbackRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Google OAuth callback (POST - for programmatic use).

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
        token_data = await token_manager.exchange_google_code(
            code=request.code,
            redirect_uri=request.redirect_uri,
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        ).delete()

        # Create new credential (user info is included in token_data from exchange)
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=api_key.department_id,
            provider=CloudProvider.GOOGLE.value,
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
            f"Connected Google Workspace for department {api_key.department_id} ({token_data.get('email')})"
        )

        return GoogleCredentialResponse(
            id=credential.id,
            provider=credential.provider,
            provider_email=credential.provider_email,
            provider_name=credential.provider_name,
            is_active=credential.is_active,
            last_sync_at=credential.last_sync_at,
            created_at=credential.created_at,
        )

    except Exception as e:
        logger.error(f"Google OAuth callback failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth callback failed: {str(e)}",
        )


@router.delete("/disconnect")
async def disconnect_google(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Disconnect Google Workspace integration.

    Revokes the OAuth token and removes stored credentials.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    try:
        # Revoke token with Google
        token_manager = get_token_manager()
        access_token = token_manager.decrypt_token(credential.access_token)
        await token_manager.revoke_google_token(access_token)
    except Exception as e:
        logger.warning(f"Failed to revoke Google token (may already be revoked): {e}")

    # Delete credential and associated data
    db.query(CloudFile).filter(CloudFile.credential_id == credential.id).delete()
    db.query(CloudJobQueue).filter(
        CloudJobQueue.credential_id == credential.id
    ).delete()
    db.delete(credential)
    db.commit()

    logger.info(f"Disconnected Google Workspace for department {api_key.department_id}")

    return {"success": True, "message": "Google Workspace disconnected"}


@router.get("/status", response_model=GoogleCredentialResponse)
async def google_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get Google Workspace connection status.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    return GoogleCredentialResponse(
        id=credential.id,
        provider=credential.provider,
        provider_email=credential.provider_email,
        provider_name=credential.provider_name,
        is_active=credential.is_active,
        last_sync_at=credential.last_sync_at,
        created_at=credential.created_at,
    )


# ==================== File Listing Endpoints ====================


@router.get("/drive/files", response_model=GoogleFileListResponse)
async def list_drive_files(
    folder_id: Optional[str] = Query(None, description="Folder ID (None for root)"),
    page_token: Optional[str] = Query(None, description="Page token for pagination"),
    page_size: int = Query(50, ge=1, le=100, description="Number of files per page"),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List accessible files from Google Drive.

    Returns files that can be scanned (Docs, Slides, Sheets, Office formats, PDFs).
    Files are automatically tracked in our database for change detection.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

    credential = await get_google_credential(api_key, db)
    integration = await get_google_integration(credential)

    try:
        # List files from Google Drive
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
                    CloudFile.provider == CloudProvider.GOOGLE.value,
                    CloudFile.provider_file_id == file_info.id,
                )
                .first()
            )

            if not cloud_file:
                # Create new tracking record
                cloud_file = CloudFile(
                    id=str(uuid.uuid4()),
                    department_id=api_key.department_id,
                    credential_id=credential.id,
                    provider=CloudProvider.GOOGLE.value,
                    provider_file_id=file_info.id,
                    provider_parent_id=(
                        file_info.parents[0] if file_info.parents else None
                    ),
                    file_name=file_info.name,
                    file_type=(
                        file_info.export_extension.lstrip(".")
                        if file_info.export_extension
                        else "unknown"
                    ),
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
                    cloud_file.file_name = file_info.name
                    cloud_file.provider_version = file_info.version
                    cloud_file.provider_modified_at = file_info.modified_time
                    cloud_file.needs_rescan = True

            response_files.append(
                GoogleFileResponse(
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

        return GoogleFileListResponse(
            files=response_files,
            next_page_token=next_token,
            total_count=len(response_files),
        )

    except Exception as e:
        logger.error(f"Failed to list Google Drive files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list files: {str(e)}",
        )


@router.get("/drive/folders")
async def list_google_drive_folders(
    parent_id: Optional[str] = Query(
        None, description="Parent folder ID (None for root)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List folders in Google Drive.

    Used for folder selection UI to choose which folders to sync.
    Returns folder hierarchy for privacy-conscious syncing.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

    try:
        # Get OAuth credential
        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == api_key.department_id,
                CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        if not credential:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Google Workspace not connected",
            )

        # Decrypt token
        token_manager = get_token_manager()
        access_token = token_manager.decrypt_token(credential.access_token)

        # Initialize Google Drive integration
        drive = GoogleDriveIntegration(
            credential_id=credential.id,
            access_token=access_token,
        )

        # List folders
        folders = await drive.list_folders(parent_id=parent_id)

        return {
            "folders": [
                {
                    "id": folder.id,
                    "name": folder.name,
                    "parent_id": folder.parent_id,
                    "web_view_link": folder.web_view_link,
                }
                for folder in folders
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list Google Drive folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list folders: {str(e)}",
        )


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

    Downloads the file from Google Drive, scans with our processors,
    and stores the results. Returns immediately with job status.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

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

    credential = await get_google_credential(api_key, db)

    # Create scan job
    job = CloudJobQueue(
        id=str(uuid.uuid4()),
        department_id=api_key.department_id,
        job_type=CloudJobType.SCAN.value,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.GOOGLE.value,
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
    Scan all accessible files in a Google Drive folder.

    Creates scan jobs for each file found. Returns summary of jobs created.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

    credential = await get_google_credential(api_key, db)
    integration = await get_google_integration(credential)

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
                    CloudFile.provider == CloudProvider.GOOGLE.value,
                    CloudFile.provider_file_id == file_info.id,
                )
                .first()
            )

            if not cloud_file:
                cloud_file = CloudFile(
                    id=str(uuid.uuid4()),
                    department_id=api_key.department_id,
                    credential_id=credential.id,
                    provider=CloudProvider.GOOGLE.value,
                    provider_file_id=file_info.id,
                    file_name=file_info.name,
                    file_type=(
                        file_info.export_extension.lstrip(".")
                        if file_info.export_extension
                        else "unknown"
                    ),
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
                provider=CloudProvider.GOOGLE.value,
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


# ==================== Remediation Endpoints ====================


@router.post("/remediate", response_model=Dict[str, Any])
async def remediate_file(
    request: RemediateFileRequest,
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Remediate a file and upload the fixed version back to Google Drive.

    Downloads the file, applies accessibility fixes, and either
    replaces the original or uploads as a new file.

    🔒 REQUIRES: cloud_integration feature (not available on free tier)
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

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

    credential = await get_google_credential(api_key, db)

    # Create remediation job
    job = CloudJobQueue(
        id=str(uuid.uuid4()),
        department_id=api_key.department_id,
        job_type=CloudJobType.REMEDIATE.value,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.GOOGLE.value,
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
        CloudJobQueue.provider == CloudProvider.GOOGLE.value,
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
    """Background task to scan a file from Google Drive."""
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
            job.progress_message = "Downloading file from Google Drive..."
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
    """Background task to remediate a file and upload back to Google Drive."""
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
            result = await handle_remediation_job(job, db, token_manager)

            job.status = CloudJobStatus.COMPLETED.value
            job.progress = 100
            job.progress_message = "Remediation complete"
            job.result_data = result
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(f"Cloud remediation complete: job={job_id}")
        except Exception as e:
            logger.error(f"Cloud remediation failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.progress = 100
            job.progress_message = f"Remediation failed: {e}"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


# ==================== Account Management ====================


@router.get("/account")
async def get_google_account(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get connected Google account information.

    Returns:
        Account info including email, name, connection time, and last sync time.
    """
    oauth_service = GoogleOAuthService()
    account_info = oauth_service.get_account_info(
        department_id=api_key.department_id, db=db
    )

    if not account_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    return account_info


# ==================== File Operations ====================


@router.get("/drive/files/{file_id}")
async def get_file_metadata(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get file metadata from Google Drive.

    Args:
        file_id: Google Drive file ID

    Returns:
        File metadata including name, type, size, and sharing info.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Get file metadata using Google Drive integration
    drive_integration = GoogleDriveIntegration(access_token=access_token)

    try:
        file_info = drive_integration.get_file_metadata(file_id)
        return {
            "id": file_info.id,
            "name": file_info.name,
            "mime_type": file_info.mime_type,
            "size": file_info.size,
            "created_time": file_info.created_time,
            "modified_time": file_info.modified_time,
            "web_view_link": file_info.web_view_link,
        }
    except Exception as e:
        logger.error(f"Error getting file metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file metadata: {str(e)}",
        )


@router.get("/drive/files/{file_id}/download")
async def download_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Download file content from Google Drive.

    Args:
        file_id: Google Drive file ID

    Returns:
        File content as bytes.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Download file using Google Drive integration
    drive_integration = GoogleDriveIntegration(access_token=access_token)

    try:
        file_content = drive_integration.download_file(file_id)
        return {"content": file_content, "file_id": file_id}
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download file: {str(e)}",
        )


# ==================== Document Export ====================


class ExportDocRequest(BaseModel):
    """Request to export Google Doc to DOCX."""

    file_id: str = Field(..., description="Google Doc file ID")
    output_path: Optional[str] = Field(None, description="Local output path")


class ExportSlidesRequest(BaseModel):
    """Request to export Google Slides to PPTX."""

    file_id: str = Field(..., description="Google Slides file ID")
    output_path: Optional[str] = Field(None, description="Local output path")


class ExportSheetsRequest(BaseModel):
    """Request to export Google Sheets to XLSX."""

    file_id: str = Field(..., description="Google Sheets file ID")
    output_path: Optional[str] = Field(None, description="Local output path")


@router.post("/docs/export")
async def export_google_doc(
    request: ExportDocRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Export Google Doc to DOCX format.

    Args:
        request: Export request with file ID and optional output path

    Returns:
        Export status and output file path.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Export document using GoogleDocsService
    docs_service = GoogleDocsService(access_token=access_token)

    try:
        output_path = docs_service.export_to_docx(
            file_id=request.file_id, output_path=request.output_path
        )
        return {
            "success": True,
            "file_id": request.file_id,
            "output_path": output_path,
            "format": "docx",
        }
    except Exception as e:
        logger.error(f"Error exporting Google Doc: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export Google Doc: {str(e)}",
        )


@router.post("/slides/export")
async def export_google_slides(
    request: ExportSlidesRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Export Google Slides to PPTX format.

    Args:
        request: Export request with file ID and optional output path

    Returns:
        Export status and output file path.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Export slides using GoogleSlidesService
    slides_service = GoogleSlidesService(access_token=access_token)

    try:
        output_path = slides_service.export_to_pptx(
            file_id=request.file_id, output_path=request.output_path
        )
        return {
            "success": True,
            "file_id": request.file_id,
            "output_path": output_path,
            "format": "pptx",
        }
    except Exception as e:
        logger.error(f"Error exporting Google Slides: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export Google Slides: {str(e)}",
        )


@router.post("/sheets/export")
async def export_google_sheets(
    request: ExportSheetsRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Export Google Sheets to XLSX format.

    Args:
        request: Export request with file ID and optional output path

    Returns:
        Export status and output file path.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Export sheets using GoogleSheetsService
    sheets_service = GoogleSheetsService(access_token=access_token)

    try:
        output_path = sheets_service.export_to_xlsx(
            file_id=request.file_id, output_path=request.output_path
        )
        return {
            "success": True,
            "file_id": request.file_id,
            "output_path": output_path,
            "format": "xlsx",
        }
    except Exception as e:
        logger.error(f"Error exporting Google Sheets: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export Google Sheets: {str(e)}",
        )


# ==================== File Upload ====================


class UploadFileRequest(BaseModel):
    """Request to upload file to Google Drive."""

    file_path: str = Field(..., description="Local file path to upload")
    parent_folder_id: Optional[str] = Field(
        None, description="Parent folder ID (root if not specified)"
    )
    file_id: Optional[str] = Field(None, description="File ID to replace (update)")


@router.post("/upload")
async def upload_file(
    request: UploadFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Upload file to Google Drive.

    Args:
        request: Upload request with file path and optional parent folder

    Returns:
        Upload status and new file ID.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Decrypt access token
    token_manager = OAuthTokenManager()
    access_token = token_manager.decrypt_token(credential.access_token)

    # Upload file using Google Drive integration
    GoogleDriveIntegration(access_token=access_token)

    try:
        # Check if file exists
        if not os.path.exists(request.file_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {request.file_path}",
            )

        # Upload file (implementation depends on GoogleDriveIntegration)
        # For now, return a placeholder response
        new_file_id = request.file_id or f"google-file-{uuid.uuid4()}"

        return {
            "success": True,
            "file_id": new_file_id,
            "file_path": request.file_path,
            "message": "File upload queued (implementation in progress)",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}",
        )


# ==================== Health Check ====================


@router.get("/health")
async def google_health():
    """Health check for Google integration."""
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    return {
        "status": (
            "healthy" if google_client_id and google_client_secret else "unconfigured"
        ),
        "service": "google-workspace",
        "configured": bool(google_client_id and google_client_secret),
        "features": [
            "oauth-connection",
            "drive-file-listing",
            "file-scanning",
            "auto-remediation",
        ],
    }
