"""
Canvas LMS Scan Routes

Endpoints for scanning Canvas files for accessibility compliance:
- POST /canvas/scan — Queue scan for a single Canvas file
- POST /canvas/scan/bulk — Scan all files in a Canvas course
- GET /canvas/courses/{course_id}/scan-status — Database-only compliance summary
- POST /canvas/upload-remediated — Push remediated file back to Canvas

SECURITY:
- All endpoints require API key authentication
- All endpoints require lms_integration feature gate
- Users can only access their own department's data
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import verify_department_access
from ..auth.canvas_permissions import require_lti_course_access
from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..db.database import get_db_dependency
from ..db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudJobType,
    CloudProvider,
    Scan,
    ScanResult,
)
from ..integrations.canvas import CanvasAPIClient
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..middleware.quota import require_feature

# Import _get_canvas_client from the main canvas routes
from .canvas_routes import _get_canvas_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas", tags=["canvas"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CanvasScanRequest(BaseModel):
    """Request to scan a single Canvas file."""

    department_id: Optional[str] = None
    course_id: str = Field(..., description="Canvas course ID")
    file_id: str = Field(..., description="Canvas file ID")


class CanvasScanResponse(BaseModel):
    """Response after queuing a scan."""

    job_id: str
    status: str = "queued"
    cloud_file_id: str


class CanvasBulkScanRequest(BaseModel):
    """Request to scan all files in a Canvas course."""

    department_id: Optional[str] = None
    course_id: str = Field(..., description="Canvas course ID")


class BulkScanFileJob(BaseModel):
    """Individual file job in a bulk scan response."""

    file_id: str
    file_name: str
    job_id: str


class CanvasBulkScanResponse(BaseModel):
    """Response after queuing bulk scan."""

    jobs: List[BulkScanFileJob]
    total: int
    skipped: int


class FileScanStatus(BaseModel):
    """Scan status for a single file."""

    provider_file_id: str
    file_name: str
    file_type: str
    scan_id: Optional[str] = None
    compliance_score: Optional[float] = None
    issues_count: int = 0
    status: str = "pending"
    has_remediated_version: bool = False


class CourseScanStatusResponse(BaseModel):
    """Compliance summary for a course."""

    course_id: str
    total_files: int
    scanned_files: int
    average_compliance: Optional[float] = None
    files: List[FileScanStatus]


class CanvasUploadRemediatedRequest(BaseModel):
    """Request to upload a remediated file back to Canvas."""

    scan_id: str = Field(..., description="Scan ID of the remediated file")
    course_id: str = Field(..., description="Canvas course ID to upload to")


class CanvasUploadRemediatedResponse(BaseModel):
    """Response after uploading a remediated file."""

    success: bool
    canvas_file_url: Optional[str] = None
    file_name: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Helper: file type from filename
# =============================================================================


def _get_file_type(file_name: str) -> str:
    """Extract file type from filename extension."""
    if not file_name or "." not in file_name:
        return "unknown"
    return file_name.rsplit(".", 1)[-1].lower()


# =============================================================================
# Helper: Docker localhost rewrite
# =============================================================================


def _rewrite_localhost_for_docker(api_client: CanvasAPIClient) -> None:
    """
    Rewrite localhost URLs for Docker networking.

    When running in Docker, the Canvas instance URL stored in credentials
    may reference localhost, which doesn't work from inside the container.
    """
    if os.getenv("ENV") == "development" and "localhost" in api_client.canvas_url:
        api_client.canvas_url = api_client.canvas_url.replace(
            "localhost", "host.docker.internal"
        )
        api_client.api_base = f"{api_client.canvas_url}/api/v1"


# =============================================================================
# Background Task
# =============================================================================


async def _canvas_scan_file_task(job_id: str, cloud_file_id: str, credential_id: str):
    """Background task to scan a file from Canvas LMS."""
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.cloud_scan_job import handle_scan_job

    logger.info(f"Starting Canvas scan: job={job_id}, file={cloud_file_id}")

    with _get_db_ctx() as db:
        job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()
        if not job:
            logger.error(f"Scan job not found: {job_id}")
            return

        try:
            job.status = CloudJobStatus.PROCESSING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            job.progress_message = "Downloading file from Canvas..."
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
                f"Canvas scan complete: job={job_id}, "
                f"score={result.get('compliance_score')}, "
                f"issues={result.get('issues_found', 0)}"
            )
        except Exception as e:
            logger.error(f"Canvas scan failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.progress = 100
            job.progress_message = f"Scan failed: {e}"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/scan", response_model=CanvasScanResponse)
async def scan_canvas_file(
    request: CanvasScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CanvasScanResponse:
    """
    Queue accessibility scan for a single Canvas file.

    Requires API key authentication and lms_integration feature.
    """
    require_lti_course_access(principal, request.course_id)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )

    try:
        credential, api_client = await _get_canvas_client(dept_id, db)
        _rewrite_localhost_for_docker(api_client)

        try:
            # Resolve the file through the course-scoped Canvas API before
            # consulting cached state. Canvas file IDs are otherwise usable
            # across courses in the same account.
            canvas_files = await api_client.list_course_files(request.course_id)
            file_info = next(
                (item for item in canvas_files if str(item.id) == str(request.file_id)),
                None,
            )
            if file_info is None:
                raise HTTPException(status_code=404, detail="Canvas file not found")

            # Cached Canvas files are course-scoped too. Never return or
            # reassign a row created for another course.
            existing_cloud_file = (
                db.query(CloudFile)
                .filter(
                    CloudFile.provider == CloudProvider.CANVAS.value,
                    CloudFile.provider_file_id == request.file_id,
                    CloudFile.provider_parent_id == request.course_id,
                    CloudFile.department_id == dept_id,
                )
                .first()
            )

            if existing_cloud_file and not existing_cloud_file.needs_rescan:
                # Return existing scan results
                return CanvasScanResponse(
                    job_id="",
                    status="already_scanned",
                    cloud_file_id=existing_cloud_file.id,
                )

            file_type = _get_file_type(file_info.filename)

            if existing_cloud_file:
                # Update existing record
                existing_cloud_file.file_name = (
                    file_info.display_name or file_info.filename
                )
                existing_cloud_file.file_type = file_type
                existing_cloud_file.mime_type = file_info.content_type
                existing_cloud_file.file_size_bytes = file_info.size
                existing_cloud_file.web_view_link = file_info.url
                existing_cloud_file.provider_parent_id = request.course_id
                existing_cloud_file.needs_rescan = True
                cloud_file = existing_cloud_file
            else:
                # Create new CloudFile
                cloud_file = CloudFile(
                    id=str(uuid.uuid4()),
                    department_id=dept_id,
                    credential_id=credential.id,
                    provider=CloudProvider.CANVAS.value,
                    provider_file_id=request.file_id,
                    file_name=file_info.display_name or file_info.filename,
                    file_type=file_type,
                    mime_type=file_info.content_type,
                    file_size_bytes=file_info.size,
                    web_view_link=file_info.url,
                    provider_parent_id=request.course_id,
                )
                db.add(cloud_file)

            # Create scan job
            job_id = str(uuid.uuid4())
            scan_job = CloudJobQueue(
                id=job_id,
                department_id=dept_id,
                job_type=CloudJobType.SCAN.value,
                cloud_file_id=cloud_file.id,
                credential_id=credential.id,
                provider=CloudProvider.CANVAS.value,
                provider_file_id=request.file_id,
                status=CloudJobStatus.PENDING.value,
                priority=5,
            )
            db.add(scan_job)
            db.commit()

            # Queue background task
            background_tasks.add_task(
                _canvas_scan_file_task,
                job_id=job_id,
                cloud_file_id=cloud_file.id,
                credential_id=credential.id,
            )

            return CanvasScanResponse(
                job_id=job_id,
                status="queued",
                cloud_file_id=cloud_file.id,
            )

        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue Canvas scan: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue scan: {str(e)}",
        )


@router.post("/scan/bulk", response_model=CanvasBulkScanResponse)
async def scan_canvas_course_files(
    request: CanvasBulkScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CanvasBulkScanResponse:
    """
    Queue accessibility scans for all files in a Canvas course.

    Skips files that have already been scanned and don't need rescanning.
    Requires API key authentication and lms_integration feature.
    """
    require_lti_course_access(principal, request.course_id)
    dept_id = request.department_id or principal.department_id
    verify_department_access(dept_id, principal.department_id)

    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )

    try:
        credential, api_client = await _get_canvas_client(dept_id, db)
        _rewrite_localhost_for_docker(api_client)

        try:
            # List all files in the course
            canvas_files = await api_client.list_course_files(request.course_id)

            jobs: List[BulkScanFileJob] = []
            skipped = 0

            for file_info in canvas_files:
                file_id = file_info.id

                # Check if CloudFile exists and doesn't need rescan
                existing = (
                    db.query(CloudFile)
                    .filter(
                        CloudFile.provider == CloudProvider.CANVAS.value,
                        CloudFile.provider_file_id == file_id,
                        CloudFile.provider_parent_id == request.course_id,
                        CloudFile.department_id == dept_id,
                    )
                    .first()
                )
                if existing is not None and str(existing.provider_parent_id) != str(
                    request.course_id
                ):
                    existing = None

                if existing and not existing.needs_rescan:
                    skipped += 1
                    continue

                file_type = _get_file_type(file_info.filename)

                if existing:
                    # Update existing record
                    existing.file_name = file_info.display_name or file_info.filename
                    existing.file_type = file_type
                    existing.mime_type = file_info.content_type
                    existing.file_size_bytes = file_info.size
                    existing.web_view_link = file_info.url
                    existing.provider_parent_id = request.course_id
                    existing.needs_rescan = True
                    cloud_file = existing
                else:
                    # Create new CloudFile
                    cloud_file = CloudFile(
                        id=str(uuid.uuid4()),
                        department_id=dept_id,
                        credential_id=credential.id,
                        provider=CloudProvider.CANVAS.value,
                        provider_file_id=file_id,
                        file_name=file_info.display_name or file_info.filename,
                        file_type=file_type,
                        mime_type=file_info.content_type,
                        file_size_bytes=file_info.size,
                        web_view_link=file_info.url,
                        provider_parent_id=request.course_id,
                    )
                    db.add(cloud_file)

                # Create scan job
                job_id = str(uuid.uuid4())
                scan_job = CloudJobQueue(
                    id=job_id,
                    department_id=dept_id,
                    job_type=CloudJobType.SCAN.value,
                    cloud_file_id=cloud_file.id,
                    credential_id=credential.id,
                    provider=CloudProvider.CANVAS.value,
                    provider_file_id=file_id,
                    status=CloudJobStatus.PENDING.value,
                    priority=5,
                )
                db.add(scan_job)

                # Queue background task
                background_tasks.add_task(
                    _canvas_scan_file_task,
                    job_id=job_id,
                    cloud_file_id=cloud_file.id,
                    credential_id=credential.id,
                )

                jobs.append(
                    BulkScanFileJob(
                        file_id=file_id,
                        file_name=file_info.display_name or file_info.filename,
                        job_id=job_id,
                    )
                )

            db.commit()

            return CanvasBulkScanResponse(
                jobs=jobs,
                total=len(jobs),
                skipped=skipped,
            )

        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue Canvas bulk scan: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue bulk scan: {str(e)}",
        )


@router.get(
    "/courses/{course_id}/scan-status",
    response_model=CourseScanStatusResponse,
)
async def get_course_scan_status(
    course_id: str,
    file_ids: str = Query(..., description="Comma-separated Canvas file IDs"),
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CourseScanStatusResponse:
    """
    Get compliance scan status for files in a Canvas course.

    This is a database-only query (no Canvas API calls) so it can be
    polled frequently without rate-limit concerns.

    Requires API key authentication.
    """
    require_lti_course_access(principal, course_id)

    # Parse file IDs
    requested_file_ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]

    if not requested_file_ids:
        raise HTTPException(status_code=400, detail="No file IDs provided")

    # Query CloudFiles for the requested file IDs
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_parent_id == course_id,
            CloudFile.provider_file_id.in_(requested_file_ids),
            CloudFile.department_id == principal.department_id,
        )
        .all()
    )

    # Build lookup by provider_file_id
    cloud_file_map: Dict[str, CloudFile] = {
        cf.provider_file_id: cf for cf in cloud_files
    }

    files_status: List[FileScanStatus] = []
    scanned_count = 0
    total_compliance = 0.0

    for file_id in requested_file_ids:
        cf = cloud_file_map.get(file_id)

        if not cf:
            # File not yet tracked
            files_status.append(
                FileScanStatus(
                    provider_file_id=file_id,
                    file_name="Unknown",
                    file_type="unknown",
                    status="not_tracked",
                )
            )
            continue

        # Get scan result if available
        scan_result = None
        issues_count = 0
        compliance_score = None
        status = "pending"
        has_remediated = cf.has_remediated_version or False

        if cf.last_scan_id:
            scan = db.query(Scan).filter(Scan.id == cf.last_scan_id).first()
            if scan:
                status = (
                    str(scan.status.value)
                    if hasattr(scan.status, "value")
                    else str(scan.status)
                ).lower()
                scan_result = (
                    db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
                )
                if scan_result:
                    compliance_score = scan_result.compliance_score
                    issues_count = (
                        (scan_result.critical_issues or 0)
                        + (scan_result.high_issues or 0)
                        + (scan_result.medium_issues or 0)
                        + (scan_result.low_issues or 0)
                    )
                    scanned_count += 1
                    total_compliance += compliance_score
                    # Check if remediation has been run (stored in suggestions)
                    remediation_data = scan_result.suggestions or {}
                    if remediation_data.get("remediation_available"):
                        has_remediated = True
                    else:
                        has_remediated = cf.has_remediated_version or False
                else:
                    has_remediated = cf.has_remediated_version or False
        else:
            # Check if there's a pending/processing job
            latest_job = (
                db.query(CloudJobQueue)
                .filter(
                    CloudJobQueue.cloud_file_id == cf.id,
                    CloudJobQueue.job_type == CloudJobType.SCAN.value,
                )
                .order_by(CloudJobQueue.created_at.desc())
                .first()
            )
            if latest_job:
                status = latest_job.status

        files_status.append(
            FileScanStatus(
                provider_file_id=file_id,
                file_name=cf.file_name,
                file_type=cf.file_type,
                scan_id=cf.last_scan_id,
                compliance_score=compliance_score,
                issues_count=issues_count,
                status=status,
                has_remediated_version=has_remediated,
            )
        )

    average_compliance = (
        round(total_compliance / scanned_count, 1) if scanned_count > 0 else None
    )

    return CourseScanStatusResponse(
        course_id=course_id,
        total_files=len(requested_file_ids),
        scanned_files=scanned_count,
        average_compliance=average_compliance,
        files=files_status,
    )


@router.post("/upload-remediated", response_model=CanvasUploadRemediatedResponse)
async def upload_remediated_to_canvas(
    request: CanvasUploadRemediatedRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CanvasUploadRemediatedResponse:
    """
    Upload a remediated file back to Canvas.

    Looks up the remediation output from the scan's metadata,
    then uploads it to the specified Canvas course.

    Requires API key authentication and lms_integration feature.
    """
    require_lti_course_access(principal, request.course_id)

    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.last_scan_id == request.scan_id,
            CloudFile.department_id == principal.department_id,
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_parent_id == request.course_id,
        )
        .first()
    )
    if not cloud_file or str(cloud_file.provider_parent_id) != str(request.course_id):
        raise HTTPException(status_code=404, detail="Scan not found")

    scan = (
        db.query(Scan)
        .filter(
            Scan.id == request.scan_id,
            Scan.department_id == principal.department_id,
        )
        .first()
    )
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    await require_feature(
        db, principal.department_id, "lms_integration", "Canvas LMS Integration"
    )

    # Find the remediated file path.
    # The scan_id might be the original cloud scan (whose /tmp file is
    # gone) or a later dashboard re-scan.  Search multiple sources.
    from ..utils.file_storage import get_remediated_file_path

    remediated_path = None

    # 1. Check this scan's storage_path directly
    if scan.storage_path:
        sp = Path(scan.storage_path)
        if sp.exists() and ("remediat" in sp.name.lower()):
            remediated_path = str(sp)
        else:
            candidate = get_remediated_file_path(scan.storage_path)
            if Path(candidate).exists():
                remediated_path = candidate

    # 2. Check completed remediation output linked to this exact CloudFile.
    if not remediated_path:
        remediation_job = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.cloud_file_id == cloud_file.id,
                CloudJobQueue.job_type == CloudJobType.REMEDIATE.value,
                CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
            )
            .order_by(CloudJobQueue.completed_at.desc())
            .first()
        )
        if remediation_job and remediation_job.result_data:
            rp = remediation_job.result_data.get("output_file")
            if rp and Path(rp).exists():
                remediated_path = rp

    if not remediated_path:
        raise HTTPException(
            status_code=404,
            detail="No remediated file found for this scan. Please remediate the file first.",
        )

    # Check that the file exists on disk
    if not Path(remediated_path).exists():
        raise HTTPException(
            status_code=410,
            detail="Remediated file has expired. Please re-remediate.",
        )

    try:
        credential, api_client = await _get_canvas_client(principal.department_id, db)
        _rewrite_localhost_for_docker(api_client)

        try:
            # Build the accessible file name
            original_path = Path(scan.file_name)
            base_name = original_path.stem
            ext = original_path.suffix.lstrip(".")
            accessible_file_name = f"{base_name}_accessible.{ext}"

            # Upload to Canvas
            upload_result = await api_client.upload_file(
                course_id=request.course_id,
                local_path=remediated_path,
                file_name=accessible_file_name,
            )

            if not upload_result.success:
                raise HTTPException(
                    status_code=502,
                    detail=f"Canvas upload failed: {upload_result.error}",
                )

            # Update CloudFile remediation state
            cloud_file.has_remediated_version = True
            cloud_file.remediated_file_id = upload_result.file_id
            db.commit()

            logger.info(
                f"Uploaded remediated file to Canvas: scan={request.scan_id}, "
                f"course={request.course_id}, file={accessible_file_name}"
            )

            return CanvasUploadRemediatedResponse(
                success=True,
                canvas_file_url=upload_result.web_view_link,
                file_name=accessible_file_name,
            )

        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload remediated file to Canvas: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload to Canvas: {str(e)}",
        )
