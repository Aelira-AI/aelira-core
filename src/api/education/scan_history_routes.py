"""Scan history, progress, and report endpoints."""

import json as _json
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...auth.canvas_permissions import require_lti_account_access
from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudProvider,
    Scan,
    ScanStatus,
    ScanType,
)
from ...db.scan_service import ScanService
from ...services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)
from ._scope import authorize_scan_access

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/scans")
async def get_scan_history(
    scan_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Get scan history for the current department

     View all past scans
    REQUIRES API KEY IN PRODUCTION

    Query params:
    - scan_type: Filter by type (pdf, powerpoint, latex)
    - limit: Max results (default 50)
    - offset: Pagination offset
    """
    _, user_id, department_id = principal.as_legacy_tuple()
    # Parse scan_type filter
    type_filter = None
    if scan_type:
        scan_type_lower = scan_type.lower()
        if scan_type_lower == "pdf":
            type_filter = ScanType.PDF
        elif scan_type_lower in ["powerpoint", "pptx"]:
            type_filter = ScanType.POWERPOINT
        elif scan_type_lower in ["latex", "tex"]:
            type_filter = ScanType.LATEX

    # Course-scoped LTI history is constrained in SQL, not post-filtered.
    if principal.auth_method == "lti" and not principal.lti_account_wide:
        query = (
            db.query(Scan)
            .join(CloudFile, CloudFile.last_scan_id == Scan.id)
            .filter(
                Scan.department_id == department_id,
                CloudFile.department_id == department_id,
                CloudFile.provider == CloudProvider.CANVAS.value,
                CloudFile.provider_parent_id == principal.lti_course_id,
            )
        )
        if type_filter:
            query = query.filter(Scan.scan_type == type_filter)
        scans = query.order_by(Scan.created_at.desc()).limit(limit).offset(offset).all()
    else:
        scans = ScanService.get_scan_history(
            db=db,
            department_id=department_id,
            scan_type=type_filter,
            limit=limit,
            offset=offset,
        )

    return {
        "success": True,
        "total_returned": len(scans),
        "scans": [
            {
                "scan_id": scan.id,
                "file_name": scan.file_name,
                "scan_type": scan.scan_type.value,
                "status": scan.status.value,
                "pages": scan.pages,
                "compliance_score": (
                    scan.result.compliance_score if scan.result else None
                ),
                "total_issues": (
                    (
                        scan.result.critical_issues
                        + scan.result.high_issues
                        + scan.result.medium_issues
                        + scan.result.low_issues
                    )
                    if scan.result
                    else None
                ),
                "created_at": scan.created_at.isoformat(),
                "completed_at": (
                    scan.completed_at.isoformat() if scan.completed_at else None
                ),
            }
            for scan in scans
        ],
    }


@router.get("/scans/{scan_id}")
async def get_scan_details(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Get detailed results for a specific scan

     View full scan details
    REQUIRES API KEY IN PRODUCTION
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    # Debug logging to track issues data
    if scan.result and scan.result.issues:
        _debug_issues = scan.result.issues
        if isinstance(_debug_issues, str):
            try:
                _debug_issues = _json.loads(_debug_issues)
            except (ValueError, TypeError):
                _debug_issues = {}
        logger.info(
            f"[GET SCAN] Scan {scan_id}: issues type={type(scan.result.issues)}, has_details={'details' in _debug_issues if isinstance(_debug_issues, dict) else 'N/A'}"
        )
        if isinstance(_debug_issues, dict) and "details" in _debug_issues:
            logger.info(
                f"[GET SCAN] Scan {scan_id}: details length={len(_debug_issues['details'])}"
            )

    issues_details = []
    if scan.result:
        raw_issues = scan.result.issues
        if isinstance(raw_issues, str):
            try:
                raw_issues = _json.loads(raw_issues)
            except (ValueError, TypeError):
                logger.warning(
                    f"[GET SCAN] Scan {scan_id}: Failed to parse issues JSON string, defaulting to []"
                )
                raw_issues = []

        if isinstance(raw_issues, dict):
            issues_details = raw_issues.get("details", [])
            logger.info(
                f"[GET SCAN] Scan {scan_id}: Extracted {len(issues_details)} issues from dict"
            )
        elif isinstance(raw_issues, list):
            issues_details = raw_issues
            logger.info(
                f"[GET SCAN] Scan {scan_id}: Using {len(issues_details)} issues from list"
            )
        else:
            logger.warning(
                f"[GET SCAN] Scan {scan_id}: Unexpected issues type: {type(raw_issues)}"
            )

    return {
        "success": True,
        "scan": {
            "scan_id": scan.id,
            "file_name": scan.file_name,
            "scan_type": scan.scan_type.value,
            "status": scan.status.value,
            "pages": scan.pages,
            "file_size_bytes": scan.file_size_bytes,
            "processing_time_ms": scan.processing_time_ms,
            "created_at": scan.created_at.isoformat(),
            "completed_at": (
                scan.completed_at.isoformat() if scan.completed_at else None
            ),
            "result": (
                {
                    "compliance_score": scan.result.compliance_score,
                    "wcag_level": scan.result.wcag_level,
                    "issues": issues_details,
                    "summary": {
                        "critical": scan.result.critical_issues,
                        "high": scan.result.high_issues,
                        "medium": scan.result.medium_issues,
                        "low": scan.result.low_issues,
                        "total": (
                            scan.result.critical_issues
                            + scan.result.high_issues
                            + scan.result.medium_issues
                            + scan.result.low_issues
                        ),
                    },
                    "structure": scan.result.structure,
                    "suggestions": scan.result.suggestions,
                    "ocr_used": scan.result.ocr_used,
                    "ollama_used": scan.result.ollama_used,
                }
                if scan.result
                else None
            ),
        },
    }


@router.get("/scans/{scan_id}/progress")
async def get_scan_progress(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Get real-time progress for a scan

     Poll for scan progress
    REQUIRES API KEY IN PRODUCTION

    Returns:
        - status: scan status (pending, processing, completed, failed)
        - progress: percentage complete (0-100)
        - progress_message: current operation description
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = (
        db.query(Scan)
        .filter(Scan.id == scan_id, Scan.department_id == department_id)
        .first()
    )

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    return {
        "success": True,
        "scan_id": scan.id,
        "status": scan.status.value,
        "progress": scan.progress or 0,
        "progress_message": scan.progress_message or "Initializing scan...",
        "completed_at": scan.completed_at.isoformat() if scan.completed_at else None,
        "error_message": scan.error_message,
    }


@router.delete("/scans/{scan_id}")
async def cancel_scan(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Cancel a scan in progress and delete its record

     Cancel running scans
    REQUIRES API KEY IN PRODUCTION

    Processing cancellation is durable. The worker stops and reaps its child
    before acknowledging the queue row or deleting scan results.
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = (
        db.query(Scan)
        .filter(Scan.id == scan_id, Scan.department_id == department_id)
        .first()
    )

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    try:
        now = datetime.now(timezone.utc)
        queue_jobs = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.department_id == department_id,
                CloudJobQueue.payload["scan_id"].as_string() == scan_id,
                CloudJobQueue.status.in_(
                    (
                        CloudJobStatus.PENDING.value,
                        CloudJobStatus.PROCESSING.value,
                    )
                ),
            )
            .with_for_update()
            .all()
        )
        for job in queue_jobs:
            if job.status == CloudJobStatus.PROCESSING.value:
                job.progress_message = "Cancellation requested"
                job.error_message = "scan_cancel_requested"
                job.last_error_code = "scan_cancel_requested"
                job.last_error_retryable = False
            else:
                job.status = CloudJobStatus.FAILED.value
                job.completed_at = now
                job.progress = 0
                job.progress_message = "Cancelled"
                job.result_data = {"cancelled": True}
                job.error_message = "scan_cancelled"
                job.last_error_code = "scan_cancelled"
                job.last_error_retryable = False
        processing_requested = any(
            job.status == CloudJobStatus.PROCESSING.value
            and job.last_error_code == "scan_cancel_requested"
            for job in queue_jobs
        )
        if not processing_requested:
            try:
                RemediationArtifactService.from_settings().delete_for_scan(
                    db, department_id=department_id, scan_id=scan_id
                )
            except ArtifactAuthorizationError:
                db.rollback()
                raise HTTPException(
                    status_code=409, detail="artifact_cleanup_required"
                ) from None
            scan.status = ScanStatus.FAILED
            scan.error_message = "Cancelled by user"
            scan.progress_message = "Scan cancelled"
            if scan.result:
                db.delete(scan.result)
            db.delete(scan)
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(f"[CANCEL] Scan {scan_id} cancelled by user")

    return {"success": True, "message": "Scan cancellation requested"}


@router.get("/scans/{scan_id}/report")
async def download_scan_report(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Download a comprehensive PDF report of the accessibility scan

     Download scan report
    REQUIRES API KEY IN PRODUCTION

    Returns:
        PDF report with scan results, issues, and AI-generated fixes
    """
    logger.info(f"[REPORT] Starting report generation for scan_id: {scan_id}")

    try:
        _, user_id, department_id = principal.as_legacy_tuple()
        logger.info(f"[REPORT] User ID: {user_id}, Department ID: {department_id}")

        scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
        logger.info(f"[REPORT] Scan retrieved: {scan is not None}")

        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")

        authorize_scan_access(db, scan, principal)

        logger.info(
            f"[REPORT] Scan status: {scan.status}, Has result: {scan.result is not None}"
        )

        # Generate report PDF
        from ...education.pdf_report_generator import AccessibilityPDFReportGenerator

        # Prepare scan data for report
        # Handle both array format (PDF scans) and nested dict format (website scans).
        # Guard against DB drivers returning JSON columns as raw strings.
        issues_data = []
        if scan.result and scan.result.issues:
            raw_issues = scan.result.issues
            if isinstance(raw_issues, str):
                try:
                    raw_issues = _json.loads(raw_issues)
                except (ValueError, TypeError):
                    raw_issues = []

            if isinstance(raw_issues, list):
                # PDF scans store issues as direct array
                issues_data = raw_issues
            elif isinstance(raw_issues, dict):
                # Website scans store issues nested under 'details'
                issues_data = raw_issues.get("details", [])
            else:
                issues_data = []

        scan_data = {
            "scan_id": scan.id,
            "url": scan.file_name,  # For websites, file_name contains the URL
            "created_at": scan.created_at.isoformat() if scan.created_at else None,
            "compliance_score": scan.result.compliance_score if scan.result else 0,
            "issues": issues_data,
            "pages_scanned": scan.pages or 1,
            "total_issues": (
                (
                    (scan.result.critical_issues or 0)
                    + (scan.result.high_issues or 0)
                    + (scan.result.medium_issues or 0)
                    + (scan.result.low_issues or 0)
                )
                if scan.result
                else 0
            ),
        }

        logger.info(
            f"[REPORT] Scan data prepared - Score: {scan_data['compliance_score']}, Total issues: {scan_data['total_issues']}, Issue count: {len(scan_data['issues'])}"
        )

        report_pdf = AccessibilityPDFReportGenerator.generate_website_report(scan_data)
        logger.info(
            f"[REPORT] PDF generated successfully - Size: {len(report_pdf)} bytes"
        )

        # Return as downloadable PDF file
        filename = f"accessibility-report-{scan_id[:8]}.pdf"

        return Response(
            content=report_pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"[REPORT] Error generating report: {type(e).__name__}: {str(e)}")
        logger.error(f"[REPORT] Traceback:\n{traceback.format_exc()}")
        raise


@router.get("/scans/{scan_id}/html")
async def get_scan_html(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Get the accessible HTML output for a scan (fixed/remediated code)

     Download fixed HTML
    REQUIRES API KEY IN PRODUCTION
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    if not scan.result or not scan.result.html_output:
        raise HTTPException(status_code=404, detail="HTML output not found")

    filename = f"fixed-{scan.file_name.replace('://', '-').replace('/', '-')[:50]}.html"

    return Response(
        content=scan.result.html_output,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats")
async def get_department_stats(
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Get statistics for the current department

     Department-wide statistics
    REQUIRES API KEY IN PRODUCTION
    """
    _, user_id, department_id = principal.as_legacy_tuple()
    require_lti_account_access(principal, principal.lti_platform or "canvas")

    stats = ScanService.get_department_stats(db=db, department_id=department_id)

    return {"success": True, "department_id": department_id, "stats": stats}
