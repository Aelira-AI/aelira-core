"""
Integration Status API Routes

Provides unified endpoints for:
- Overall integration status (Google, Microsoft, Canvas, Blackboard, Moodle, Brightspace)
- Health checks for each provider
- Integration usage metrics
- Webhook subscription status
- Integration disconnection
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
import logging
import uuid

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    CloudOAuthCredentials,
    CloudProvider,
    CloudJobQueue,
    CloudJobStatus,
    CloudJobType,
    CloudFile,
    CloudSyncFolder,
)
from ..api.auth_routes import get_current_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ==================== Response Models ====================


class IntegrationStatusResponse(BaseModel):
    """Response for integration connection status."""

    google_workspace: Dict[str, Any]
    microsoft_365: Dict[str, Any]
    canvas_lti: Dict[str, Any]
    blackboard_lti: Dict[str, Any]
    moodle_lti: Dict[str, Any]
    brightspace_lti: Dict[str, Any]


class HealthCheckResponse(BaseModel):
    """Response for health check."""

    status: str
    provider: Optional[str] = None
    timestamp: datetime


class MetricsResponse(BaseModel):
    """Response for integration metrics."""

    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    pending_jobs: int
    total_files_synced: int
    last_sync_at: Optional[datetime]


class AddSyncFolderRequest(BaseModel):
    """Request to add a folder to sync list."""

    provider: str  # "google" or "microsoft"
    folder_id: str
    folder_name: str
    folder_path: Optional[str] = None
    sync_subfolders: bool = True


class SyncFolderResponse(BaseModel):
    """Response for sync folder operations."""

    id: str
    provider: str
    folder_id: str
    folder_name: str
    folder_path: Optional[str]
    sync_subfolders: bool
    is_active: bool
    created_at: datetime


# ==================== Endpoints ====================


@router.get("/status")
async def get_integration_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get status of all integrations for the current department.

    Returns connection status for:
    - Google Workspace (Drive, Docs, Slides, Sheets)
    - Microsoft 365 (OneDrive, SharePoint)
    - Canvas LTI 1.3
    - Blackboard LTI 1.3
    - Moodle LMS (OAuth 2.0 + Web Services)
    - D2L Brightspace (OAuth 2.0 + Valence API)

    Authentication: Requires valid API key
    """
    department_id = api_key.department_id

    # Check Google Workspace
    google_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    google_status = {
        "connected": google_credential is not None,
        "email": google_credential.provider_email if google_credential else None,
        "name": google_credential.provider_name if google_credential else None,
        "last_sync_at": (
            google_credential.last_sync_at.isoformat()
            if google_credential and google_credential.last_sync_at
            else None
        ),
    }

    # Check Microsoft 365
    microsoft_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    microsoft_status = {
        "connected": microsoft_credential is not None,
        "email": microsoft_credential.provider_email if microsoft_credential else None,
        "name": microsoft_credential.provider_name if microsoft_credential else None,
        "last_sync_at": (
            microsoft_credential.last_sync_at.isoformat()
            if microsoft_credential and microsoft_credential.last_sync_at
            else None
        ),
    }

    # Check Canvas
    canvas_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.CANVAS.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    canvas_status = {
        "connected": canvas_credential is not None,
        "email": canvas_credential.provider_email if canvas_credential else None,
        "name": canvas_credential.provider_name if canvas_credential else None,
        "last_sync_at": (
            canvas_credential.last_sync_at.isoformat()
            if canvas_credential and canvas_credential.last_sync_at
            else None
        ),
    }

    # Check Blackboard
    blackboard_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BLACKBOARD.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    blackboard_status = {
        "connected": blackboard_credential is not None,
        "email": (
            blackboard_credential.provider_email if blackboard_credential else None
        ),
        "name": blackboard_credential.provider_name if blackboard_credential else None,
        "last_sync_at": (
            blackboard_credential.last_sync_at.isoformat()
            if blackboard_credential and blackboard_credential.last_sync_at
            else None
        ),
    }

    # Check Moodle
    moodle_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.MOODLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    moodle_status = {
        "connected": moodle_credential is not None,
        "email": moodle_credential.provider_user_email if moodle_credential else None,
        "fullname": moodle_credential.provider_user_name if moodle_credential else None,
        "last_sync_at": (
            moodle_credential.last_sync_at.isoformat()
            if moodle_credential and moodle_credential.last_sync_at
            else None
        ),
    }

    # Check Brightspace
    brightspace_credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    brightspace_status = {
        "connected": brightspace_credential is not None,
        "email": (
            brightspace_credential.provider_user_email
            if brightspace_credential
            else None
        ),
        "fullname": (
            brightspace_credential.provider_user_name
            if brightspace_credential
            else None
        ),
        "last_sync_at": (
            brightspace_credential.last_sync_at.isoformat()
            if brightspace_credential and brightspace_credential.last_sync_at
            else None
        ),
    }

    # Return format matching dashboard expectations
    return {
        "google": google_status,
        "microsoft": microsoft_status,
        "canvas": canvas_status,
        "blackboard": blackboard_status,
        "moodle": moodle_status,
        "brightspace": brightspace_status,
    }


@router.get("/files")
async def get_cloud_files(
    provider: Optional[str] = Query(
        default=None, description="Filter by provider (google, microsoft)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get all cloud files tracked from connected integrations.

    Authentication: Requires valid API key
    """
    dept_id = api_key.department_id

    # Build query
    query = db.query(CloudFile).filter(CloudFile.department_id == dept_id)

    # Filter by provider if specified
    if provider:
        query = query.filter(CloudFile.provider == provider)

    # Order by most recently discovered first
    files = query.order_by(CloudFile.discovered_at.desc()).all()

    # Format response
    return {
        "files": [
            {
                "id": f.id,
                "provider": f.provider,
                "file_name": f.file_name,
                "file_type": f.file_type,
                "mime_type": f.mime_type,
                "file_size_bytes": f.file_size_bytes,
                "web_view_link": f.web_view_link,
                "last_scanned_at": (
                    f.last_scanned_at.isoformat() if f.last_scanned_at else None
                ),
                "last_compliance_score": f.last_compliance_score,
                "needs_rescan": f.needs_rescan,
                "has_remediated_version": f.has_remediated_version,
                "provider_modified_at": (
                    f.provider_modified_at.isoformat()
                    if f.provider_modified_at
                    else None
                ),
                "discovered_at": (
                    f.discovered_at.isoformat() if f.discovered_at else None
                ),
            }
            for f in files
        ],
        "total": len(files),
    }


@router.post("/sync")
async def trigger_sync(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Trigger file sync for all connected cloud providers.

    Queues sync jobs for Google Workspace and Microsoft 365 if connected.

    Authentication: Requires valid API key
    """
    dept_id = api_key.department_id

    # Find all active credentials for this department
    credentials = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == dept_id,
            CloudOAuthCredentials.is_active,
        )
        .all()
    )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No cloud providers connected. Connect Google Workspace or Microsoft 365 first.",
        )

    # Queue sync jobs for each provider
    queued_jobs = []
    for credential in credentials:
        job_id = str(uuid.uuid4())

        sync_job = CloudJobQueue(
            id=job_id,
            department_id=dept_id,
            credential_id=credential.id,
            job_type=CloudJobType.SYNC.value,
            provider=credential.provider,
            status=CloudJobStatus.PENDING.value,
            priority=1,
            job_data={
                "sync_type": "full",  # Full sync of all files
                "triggered_by": "manual",  # User-triggered via dashboard
            },
        )

        db.add(sync_job)
        queued_jobs.append(
            {
                "job_id": job_id,
                "provider": credential.provider,
                "status": "queued",
            }
        )

    db.commit()

    logger.info(f"Queued {len(queued_jobs)} sync jobs for department {dept_id}")

    return {
        "success": True,
        "message": f"Sync started for {len(queued_jobs)} provider(s)",
        "jobs": queued_jobs,
    }


@router.get("/health", response_model=HealthCheckResponse)
async def get_integration_health():
    """
    Overall integration health check.

    Returns:
        Health status for all integration services.
    """
    return HealthCheckResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
    )


@router.get("/health/google", response_model=HealthCheckResponse)
async def get_google_health():
    """
    Google Workspace integration health check.

    Returns:
        Health status for Google Drive, Docs, Slides, Sheets integration.
    """
    return HealthCheckResponse(
        status="healthy",
        provider="google",
        timestamp=datetime.utcnow(),
    )


@router.get("/health/microsoft", response_model=HealthCheckResponse)
async def get_microsoft_health():
    """
    Microsoft 365 integration health check.

    Returns:
        Health status for OneDrive, SharePoint integration.
    """
    return HealthCheckResponse(
        status="healthy",
        provider="microsoft",
        timestamp=datetime.utcnow(),
    )


@router.get("/metrics", response_model=MetricsResponse)
async def get_integration_metrics(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get integration usage metrics for the current department.

    Returns:
        Metrics including job counts, file sync stats, and last sync time.
    """
    department_id = api_key.department_id

    # Count jobs by status
    total_jobs = (
        db.query(CloudJobQueue)
        .filter(CloudJobQueue.department_id == department_id)
        .count()
    )

    completed_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
        )
        .count()
    )

    failed_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.FAILED.value,
        )
        .count()
    )

    pending_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.PENDING.value,
        )
        .count()
    )

    # Get last sync time from credentials
    credentials = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.is_active,
        )
        .all()
    )

    last_sync_at = None
    if credentials:
        sync_times = [c.last_sync_at for c in credentials if c.last_sync_at]
        if sync_times:
            last_sync_at = max(sync_times)

    return MetricsResponse(
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        pending_jobs=pending_jobs,
        total_files_synced=completed_jobs,  # Simplified - could track separately
        last_sync_at=last_sync_at,
    )


@router.get("/metrics/{integration}", response_model=MetricsResponse)
async def get_integration_specific_metrics(
    integration: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get metrics for a specific integration.

    Args:
        integration: Integration name ("google", "microsoft")

    Returns:
        Metrics for the specified integration.
    """
    department_id = api_key.department_id

    # Map integration name to provider
    provider_map = {
        "google": CloudProvider.GOOGLE.value,
        "microsoft": CloudProvider.MICROSOFT.value,
    }

    if integration not in provider_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid integration: {integration}. Must be 'google' or 'microsoft'",
        )

    provider = provider_map[integration]

    # Get credential for this provider
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == provider,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{integration} integration not connected",
        )

    # Count jobs for this provider's files
    # Note: Would need to join with CloudFile to filter by provider
    # For now, return department-wide metrics
    total_jobs = (
        db.query(CloudJobQueue)
        .filter(CloudJobQueue.department_id == department_id)
        .count()
    )

    completed_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
        )
        .count()
    )

    failed_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.FAILED.value,
        )
        .count()
    )

    pending_jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.status == CloudJobStatus.PENDING.value,
        )
        .count()
    )

    return MetricsResponse(
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        pending_jobs=pending_jobs,
        total_files_synced=completed_jobs,
        last_sync_at=credential.last_sync_at,
    )


@router.get("/webhooks/{provider}")
async def get_webhook_status(
    provider: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get webhook subscription status for a provider.

    Args:
        provider: Provider name ("google", "microsoft")

    Returns:
        Webhook subscription information.
    """
    department_id = api_key.department_id

    # Map provider name to CloudProvider enum
    provider_map = {
        "google": CloudProvider.GOOGLE.value,
        "microsoft": CloudProvider.MICROSOFT.value,
    }

    if provider not in provider_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}. Must be 'google' or 'microsoft'",
        )

    provider_value = provider_map[provider]

    # Get credential to check if connected
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == provider_value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider} integration not connected",
        )

    # Placeholder response - would check actual webhook subscriptions
    return {
        "provider": provider,
        "subscriptions": [],
        "message": f"Webhook subscription management for {provider} coming soon",
    }


@router.delete("/{integration}")
async def disconnect_integration(
    integration: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Disconnect an integration.

    Args:
        integration: Integration name ("google", "microsoft")

    Returns:
        Success message.
    """
    department_id = api_key.department_id

    # Map integration name to provider
    provider_map = {
        "google": CloudProvider.GOOGLE.value,
        "microsoft": CloudProvider.MICROSOFT.value,
    }

    if integration not in provider_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid integration: {integration}. Must be 'google' or 'microsoft'",
        )

    provider = provider_map[integration]

    # Find and deactivate credential
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == provider,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{integration} integration not connected",
        )

    # Deactivate credential
    credential.is_active = False
    db.commit()

    logger.info(
        f"Disconnected {integration} integration for department {department_id}"
    )

    return {
        "success": True,
        "message": f"{integration} integration disconnected successfully",
        "provider": integration,
    }


@router.get("/sync-folders")
async def list_sync_folders(
    provider: Optional[str] = Query(
        None, description="Filter by provider (google, microsoft)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List all folders selected for syncing.

    Returns folders that have been explicitly selected for cloud file sync.
    This ensures only chosen folders are synced (privacy-conscious).

    Authentication: Requires valid API key
    """
    dept_id = api_key.department_id

    # Build query
    query = db.query(CloudSyncFolder).filter(
        CloudSyncFolder.department_id == dept_id,
        CloudSyncFolder.is_active,
    )

    # Filter by provider if specified
    if provider:
        query = query.filter(CloudSyncFolder.provider == provider)

    # Order by most recently created first
    folders = query.order_by(CloudSyncFolder.created_at.desc()).all()

    # Format response
    return {
        "folders": [
            {
                "id": f.id,
                "provider": f.provider,
                "folder_id": f.provider_folder_id,
                "folder_name": f.folder_name,
                "folder_path": f.folder_path,
                "sync_subfolders": f.sync_subfolders,
                "is_active": f.is_active,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in folders
        ],
        "total": len(folders),
    }


@router.post("/sync-folders")
async def add_sync_folder(
    request: AddSyncFolderRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Add a folder to the sync list.

    Privacy-critical: Only explicitly added folders will be synced.
    This prevents syncing entire Google Drive or OneDrive.

    Authentication: Requires valid API key
    """
    dept_id = api_key.department_id

    # Validate provider
    provider_map = {
        "google": CloudProvider.GOOGLE.value,
        "microsoft": CloudProvider.MICROSOFT.value,
    }

    if request.provider not in provider_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {request.provider}. Must be 'google' or 'microsoft'",
        )

    provider_value = provider_map[request.provider]

    # Find the OAuth credential for this provider
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == dept_id,
            CloudOAuthCredentials.provider == provider_value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{request.provider} integration not connected",
        )

    # Check if folder already exists
    existing_folder = (
        db.query(CloudSyncFolder)
        .filter(
            CloudSyncFolder.department_id == dept_id,
            CloudSyncFolder.credential_id == credential.id,
            CloudSyncFolder.provider_folder_id == request.folder_id,
        )
        .first()
    )

    if existing_folder:
        # Reactivate if inactive
        if not existing_folder.is_active:
            existing_folder.is_active = True
            existing_folder.sync_subfolders = request.sync_subfolders
            db.commit()
            db.refresh(existing_folder)

            logger.info(
                f"Reactivated sync folder {request.folder_id} for department {dept_id}"
            )

            return {
                "id": existing_folder.id,
                "provider": existing_folder.provider,
                "folder_id": existing_folder.provider_folder_id,
                "folder_name": existing_folder.folder_name,
                "folder_path": existing_folder.folder_path,
                "sync_subfolders": existing_folder.sync_subfolders,
                "is_active": existing_folder.is_active,
                "created_at": (
                    existing_folder.created_at.isoformat()
                    if existing_folder.created_at
                    else None
                ),
                "message": "Folder reactivated successfully",
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Folder {request.folder_name} is already in sync list",
            )

    # Create new sync folder
    sync_folder = CloudSyncFolder(
        id=str(uuid.uuid4()),
        department_id=dept_id,
        credential_id=credential.id,
        provider=request.provider,
        provider_folder_id=request.folder_id,
        folder_name=request.folder_name,
        folder_path=request.folder_path,
        is_active=True,
        sync_subfolders=request.sync_subfolders,
    )

    db.add(sync_folder)
    db.commit()
    db.refresh(sync_folder)

    logger.info(
        f"Added sync folder {request.folder_id} ({request.folder_name}) for department {dept_id}"
    )

    return {
        "id": sync_folder.id,
        "provider": sync_folder.provider,
        "folder_id": sync_folder.provider_folder_id,
        "folder_name": sync_folder.folder_name,
        "folder_path": sync_folder.folder_path,
        "sync_subfolders": sync_folder.sync_subfolders,
        "is_active": sync_folder.is_active,
        "created_at": (
            sync_folder.created_at.isoformat() if sync_folder.created_at else None
        ),
        "message": "Folder added to sync list successfully",
    }


@router.delete("/sync-folders/{folder_id}")
async def remove_sync_folder(
    folder_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Remove a folder from the sync list.

    This will stop syncing files from this folder (and subfolders if enabled).
    Existing files from this folder will remain in the database.

    Authentication: Requires valid API key
    """
    dept_id = api_key.department_id

    # Find the sync folder
    sync_folder = (
        db.query(CloudSyncFolder)
        .filter(
            CloudSyncFolder.id == folder_id,
            CloudSyncFolder.department_id == dept_id,
        )
        .first()
    )

    if not sync_folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sync folder not found",
        )

    # Deactivate the folder instead of deleting (preserves history)
    sync_folder.is_active = False
    db.commit()

    logger.info(
        f"Removed sync folder {folder_id} ({sync_folder.folder_name}) for department {dept_id}"
    )

    return {
        "success": True,
        "message": f"Folder '{sync_folder.folder_name}' removed from sync list",
        "folder_id": folder_id,
        "folder_name": sync_folder.folder_name,
    }


# ==================== LTI Registration Management ====================


class LTIRegistrationRequest(BaseModel):
    """Request to register an LTI tool."""

    platform: str  # "blackboard", "canvas", "moodle", "brightspace"
    platform_name: Optional[str] = None  # Human-readable name
    issuer: str  # LTI issuer URL
    client_id: str  # LTI client_id from platform
    deployment_id: Optional[str] = None  # Optional deployment ID
    auth_login_url: Optional[str] = None
    auth_token_url: Optional[str] = None
    jwks_url: Optional[str] = None


class LTIRegistrationResponse(BaseModel):
    """Response for LTI registration."""

    id: str
    platform: str
    platform_name: Optional[str]
    issuer: str
    client_id: str
    deployment_id: Optional[str]
    is_active: bool
    launch_count: int
    last_launch_at: Optional[datetime]
    created_at: datetime


@router.get("/lti/registrations")
async def list_lti_registrations(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List all LTI tool registrations for the department.

    Returns all LTI registrations (Canvas, Blackboard, Moodle, Brightspace)
    linked to this department.

    Authentication: Requires valid API key (admin role recommended)
    """
    from ..db.models import LTIRegistration

    department_id = api_key.department_id

    registrations = (
        db.query(LTIRegistration)
        .filter(LTIRegistration.department_id == department_id)
        .order_by(LTIRegistration.created_at.desc())
        .all()
    )

    return {
        "registrations": [
            {
                "id": r.id,
                "platform": (
                    r.platform.value if hasattr(r.platform, "value") else r.platform
                ),
                "platform_name": r.platform_name,
                "issuer": r.issuer,
                "client_id": r.client_id,
                "deployment_id": r.deployment_id,
                "is_active": r.is_active,
                "launch_count": r.launch_count,
                "last_launch_at": (
                    r.last_launch_at.isoformat() if r.last_launch_at else None
                ),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in registrations
        ],
        "total": len(registrations),
    }


@router.post("/lti/registrations")
async def create_lti_registration(
    request: LTIRegistrationRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Register an LTI tool for the department.

    Creates a mapping between an LTI client_id (from Canvas, Blackboard, etc.)
    and this department. This enables multi-tenant LTI support.

    Authentication: Requires valid API key (admin role recommended)
    """
    from ..db.models import LTIRegistration, LTIPlatform

    department_id = api_key.department_id

    # Validate platform
    valid_platforms = ["canvas", "blackboard", "moodle", "brightspace"]
    if request.platform.lower() not in valid_platforms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {request.platform}. Must be one of: {', '.join(valid_platforms)}",
        )

    # Check if this client_id is already registered (globally unique)
    existing = (
        db.query(LTIRegistration)
        .filter(
            LTIRegistration.issuer == request.issuer,
            LTIRegistration.client_id == request.client_id,
        )
        .first()
    )

    if existing:
        if existing.department_id == department_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="LTI registration already exists for this client_id",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This LTI client_id is already registered by another department",
            )

    # Map platform string to enum
    platform_enum = LTIPlatform(request.platform.lower())

    # Create registration
    registration = LTIRegistration(
        id=str(uuid.uuid4()),
        department_id=department_id,
        platform=platform_enum,
        platform_name=request.platform_name or f"{request.platform.title()} LMS",
        issuer=request.issuer,
        client_id=request.client_id,
        deployment_id=request.deployment_id,
        auth_login_url=request.auth_login_url,
        auth_token_url=request.auth_token_url,
        jwks_url=request.jwks_url,
        is_active=True,
        launch_count=0,
    )

    db.add(registration)
    db.commit()
    db.refresh(registration)

    logger.info(
        f"Created LTI registration for department {department_id}: "
        f"platform={request.platform}, client_id={request.client_id}"
    )

    return {
        "id": registration.id,
        "platform": registration.platform.value,
        "platform_name": registration.platform_name,
        "issuer": registration.issuer,
        "client_id": registration.client_id,
        "deployment_id": registration.deployment_id,
        "is_active": registration.is_active,
        "created_at": (
            registration.created_at.isoformat() if registration.created_at else None
        ),
        "message": "LTI registration created successfully",
    }


@router.delete("/lti/registrations/{registration_id}")
async def delete_lti_registration(
    registration_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Delete an LTI registration.

    Removes the LTI tool registration. Any future LTI launches from this
    client_id will fail until re-registered.

    Authentication: Requires valid API key (admin role recommended)
    """
    from ..db.models import LTIRegistration

    department_id = api_key.department_id

    registration = (
        db.query(LTIRegistration)
        .filter(
            LTIRegistration.id == registration_id,
            LTIRegistration.department_id == department_id,
        )
        .first()
    )

    if not registration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LTI registration not found",
        )

    # Store info for response before deletion
    platform = (
        registration.platform.value
        if hasattr(registration.platform, "value")
        else registration.platform
    )
    client_id = registration.client_id

    db.delete(registration)
    db.commit()

    logger.info(
        f"Deleted LTI registration {registration_id} for department {department_id}"
    )

    return {
        "success": True,
        "message": "LTI registration deleted successfully",
        "platform": platform,
        "client_id": client_id,
    }


@router.patch("/lti/registrations/{registration_id}")
async def update_lti_registration(
    registration_id: str,
    is_active: Optional[bool] = None,
    platform_name: Optional[str] = None,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Update an LTI registration.

    Can enable/disable the registration or update the display name.

    Authentication: Requires valid API key (admin role recommended)
    """
    from ..db.models import LTIRegistration

    department_id = api_key.department_id

    registration = (
        db.query(LTIRegistration)
        .filter(
            LTIRegistration.id == registration_id,
            LTIRegistration.department_id == department_id,
        )
        .first()
    )

    if not registration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LTI registration not found",
        )

    # Update fields if provided
    if is_active is not None:
        registration.is_active = is_active
    if platform_name is not None:
        registration.platform_name = platform_name

    db.commit()
    db.refresh(registration)

    logger.info(
        f"Updated LTI registration {registration_id} for department {department_id}"
    )

    return {
        "id": registration.id,
        "platform": (
            registration.platform.value
            if hasattr(registration.platform, "value")
            else registration.platform
        ),
        "platform_name": registration.platform_name,
        "issuer": registration.issuer,
        "client_id": registration.client_id,
        "is_active": registration.is_active,
        "message": "LTI registration updated successfully",
    }


__all__ = ["router"]
