"""Scan history, progress, and report endpoints."""

import json as _json
import logging
import traceback
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey, Scan, ScanStatus, ScanType
from ...db.scan_service import ScanService
from ._shared import get_api_key_or_mock

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/scans")
async def get_scan_history(
    scan_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get scan history for the current department

    ✨ NEW ENDPOINT - View all past scans
    REQUIRES API KEY IN PRODUCTION 🔒

    Query params:
    - scan_type: Filter by type (pdf, powerpoint, latex)
    - limit: Max results (default 50)
    - offset: Pagination offset
    """
    _, user_id, department_id = api_key_info
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

    # Get scans
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
                    else 0
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
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get detailed results for a specific scan

    ✨ NEW ENDPOINT - View full scan details
    REQUIRES API KEY IN PRODUCTION 🔒
    """
    _, user_id, department_id = api_key_info

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    if scan.department_id != department_id:
        raise HTTPException(status_code=403, detail="Access denied")

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
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get real-time progress for a scan

    ✨ NEW ENDPOINT - Poll for scan progress
    REQUIRES API KEY IN PRODUCTION 🔒

    Returns:
        - status: scan status (pending, processing, completed, failed)
        - progress: percentage complete (0-100)
        - progress_message: current operation description
    """
    _, user_id, department_id = api_key_info

    scan = db.query(Scan).filter(Scan.id == scan_id).first()

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    if scan.department_id != department_id:
        raise HTTPException(status_code=403, detail="Access denied")

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
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Cancel a scan in progress and delete its record

    ✨ NEW ENDPOINT - Cancel running scans
    REQUIRES API KEY IN PRODUCTION 🔒

    Note: Background processing will continue until next progress check,
    but the scan will be marked as CANCELLED and results will be deleted.
    """
    _, user_id, department_id = api_key_info

    scan = db.query(Scan).filter(Scan.id == scan_id).first()

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    if scan.department_id != department_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Mark as cancelled (background task will check this)
    scan.status = ScanStatus.FAILED
    scan.error_message = "Cancelled by user"
    scan.progress_message = "Scan cancelled"

    # Delete associated result if it exists
    if scan.result:
        db.delete(scan.result)

    # Delete the scan
    db.delete(scan)
    db.commit()

    logger.info(f"[CANCEL] Scan {scan_id} cancelled by user")

    return {"success": True, "message": "Scan cancelled successfully"}


@router.get("/scans/{scan_id}/report")
async def download_scan_report(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Download a comprehensive PDF report of the accessibility scan

    ✨ NEW ENDPOINT - Download scan report
    REQUIRES API KEY IN PRODUCTION 🔒

    Returns:
        PDF report with scan results, issues, and AI-generated fixes
    """
    logger.info(f"[REPORT] Starting report generation for scan_id: {scan_id}")

    try:
        _, user_id, department_id = api_key_info
        logger.info(f"[REPORT] User ID: {user_id}, Department ID: {department_id}")

        scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
        logger.info(f"[REPORT] Scan retrieved: {scan is not None}")

        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")

        if scan.department_id != department_id:
            raise HTTPException(status_code=403, detail="Access denied")

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
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get the accessible HTML output for a scan (fixed/remediated code)

    ✨ NEW ENDPOINT - Download fixed HTML
    REQUIRES API KEY IN PRODUCTION 🔒
    """
    _, user_id, department_id = api_key_info

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan or not scan.result or not scan.result.html_output:
        raise HTTPException(status_code=404, detail="HTML output not found")

    if scan.department_id != department_id:
        raise HTTPException(status_code=403, detail="Access denied")

    filename = f"fixed-{scan.file_name.replace('://', '-').replace('/', '-')[:50]}.html"

    return Response(
        content=scan.result.html_output,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats")
async def get_department_stats(
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get statistics for the current department

    ✨ NEW ENDPOINT - Department-wide statistics
    REQUIRES API KEY IN PRODUCTION 🔒
    """
    _, user_id, department_id = api_key_info

    stats = ScanService.get_department_stats(db=db, department_id=department_id)

    return {"success": True, "department_id": department_id, "stats": stats}
