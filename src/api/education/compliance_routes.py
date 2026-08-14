"""Compliance dashboard endpoints — department stats, issues, trends, PDF reports."""

import logging
from datetime import datetime
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey
from ._shared import get_api_key_or_mock

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/compliance/{department_id}/stats")
async def get_department_compliance_stats(
    department_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get comprehensive compliance statistics for a department

    ✨ NEW ENDPOINT - Department-wide compliance dashboard
    REQUIRES API KEY IN PRODUCTION 🔒

    Provides department administrators with complete overview of:
    - Overall compliance metrics (avg score, issue counts)
    - Scan type breakdown (PDF, PowerPoint, LaTeX, etc.)
    - Compliance rate (% of files >= 90 score)
    - Faculty participation stats
    - April 2027 deadline tracking
    - Estimated work remaining

    This is the main dashboard endpoint for department chairs.
    """
    _, user_id, department_id_from_token = api_key_info
    if department_id != department_id_from_token:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(f"Getting compliance stats for department: {department_id}")

    try:
        from ...education.compliance_dashboard import ComplianceDashboard

        # Generate comprehensive stats
        stats = ComplianceDashboard.get_department_compliance(db, department_id)

        # Convert dataclass to dict for JSON serialization
        return {
            "department_id": stats.department_id,
            "department_name": stats.department_name,
            "institution": stats.institution,
            "overview": {
                "total_scans": stats.total_scans,
                "total_files_scanned": stats.total_files_scanned,
                "total_pages_slides": stats.total_pages_slides,
                "compliance_rate": stats.compliance_rate,
            },
            "compliance_scores": {
                "average": stats.avg_compliance_score,
                "minimum": stats.min_compliance_score,
                "maximum": stats.max_compliance_score,
            },
            "issues": {
                "critical": stats.total_critical,
                "high": stats.total_high,
                "medium": stats.total_medium,
                "low": stats.total_low,
                "total": stats.total_issues,
            },
            "scan_types": {
                "pdf": stats.pdf_scans,
                "powerpoint": stats.powerpoint_scans,
                "latex": stats.latex_scans,
                "image": stats.image_scans,
                "video": stats.video_scans,
                "website": stats.website_scans,
                "code": stats.code_scans,
                "multimedia": stats.multimedia_scans,
            },
            "compliance_breakdown": {
                "compliant": stats.files_compliant,  # >= 90
                "needs_work": stats.files_needs_work,  # 70-89
                "critical": stats.files_critical,  # < 70
            },
            "activity": {
                "scans_last_7_days": stats.scans_last_7_days,
                "scans_last_30_days": stats.scans_last_30_days,
                "scans_this_month": stats.scans_this_month,
            },
            "april_2026_deadline": {
                "days_remaining": stats.days_until_deadline,
                "estimated_hours_remaining": stats.estimated_hours_remaining,
                "on_track": stats.on_track,
                "deadline_date": "2026-04-24",
            },
            "faculty": {
                "active_faculty": stats.active_faculty,
                "total_faculty": stats.total_faculty,
                "participation_rate": stats.faculty_participation_rate,
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting compliance stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get compliance stats. Please try again."
        )


@router.get("/compliance/{department_id}/issues")
async def get_priority_issues(
    department_id: str,
    severity: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get prioritized list of issues that need remediation

    ✨ NEW ENDPOINT - Issue priority queue
    REQUIRES API KEY IN PRODUCTION 🔒

    Returns a prioritized list of accessibility issues across all department files,
    sorted by severity (Critical → Low) and date (newest first).

    Useful for:
    - Department-wide issue tracking
    - Prioritizing remediation work
    - Compliance reporting
    - Faculty task assignment

    Args:
        severity: Filter by severity ('critical', 'high', 'medium', 'low')
        limit: Maximum number of issues to return (default: 50)
    """
    _, user_id, department_id_from_token = api_key_info
    if department_id != department_id_from_token:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(
        f"Getting priority issues for department: {department_id} (severity={severity})"
    )

    try:
        from ...education.compliance_dashboard import ComplianceDashboard

        # Get priority issues
        issues = ComplianceDashboard.get_priority_issues(
            db, department_id, severity=severity, limit=limit
        )

        # Format for JSON
        return {
            "total_issues": len(issues),
            "severity_filter": severity,
            "issues": [
                {
                    "scan_id": issue.scan_id,
                    "file_name": issue.file_name,
                    "scan_type": issue.scan_type,
                    "severity": issue.severity,
                    "issue_type": issue.issue_type,
                    "description": issue.description,
                    "page_slide_number": issue.page_slide_number,
                    "created_at": issue.created_at.isoformat(),
                    "user_name": issue.user_name,
                    "compliance_score": issue.compliance_score,
                    "estimated_fix_time_minutes": issue.estimated_fix_time_minutes,
                }
                for issue in issues
            ],
        }

    except Exception as e:
        logger.error(f"Error getting priority issues: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get priority issues. Please try again."
        )


@router.get("/compliance/{department_id}/trend")
async def get_compliance_trend(
    department_id: str,
    days: int = 30,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get compliance score trends over time

    ✨ NEW ENDPOINT - Trend analysis
    REQUIRES API KEY IN PRODUCTION 🔒

    Shows daily compliance scores and scan activity over the specified period.
    Useful for tracking progress toward April 2027 deadline.

    Args:
        days: Number of days to look back (default: 30)

    Returns:
        Daily compliance scores and scan counts for charting
    """
    _, user_id, department_id_from_token = api_key_info
    if department_id != department_id_from_token:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(f"Getting {days}-day compliance trend for department: {department_id}")

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 365")

    try:
        from ...education.compliance_dashboard import ComplianceDashboard

        # Get trend data
        trend = ComplianceDashboard.get_compliance_trend(db, department_id, days=days)

        # Build array-of-objects format expected by the dashboard
        dates = trend.get("dates", [])
        scores = trend.get("scores", [])
        scans = trend.get("scans_per_day", [])
        trend_points = [
            {
                "date": dates[i] if i < len(dates) else None,
                "avg_compliance_score": scores[i] if i < len(scores) else 0,
                "scan_count": scans[i] if i < len(scans) else 0,
            }
            for i in range(len(dates))
        ]

        return {
            "department_id": department_id,
            "period_days": days,
            "data_points": len(trend_points),
            "trend": trend_points,
        }

    except Exception as e:
        logger.error(f"Error getting compliance trend: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get compliance trend. Please try again."
        )


@router.get("/compliance/{department_id}/report/pdf")
async def generate_compliance_pdf_report(
    department_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Generate a legal-ready PDF compliance report for the department

    ✨ NEW ENDPOINT - Legal compliance PDF generation
    REQUIRES API KEY IN PRODUCTION 🔒

    Generates a comprehensive PDF report suitable for:
    - DOJ audit documentation
    - Section 504 compliance reviews
    - Internal department tracking
    - University administration reporting
    - April 2027 deadline documentation

    The report includes:
    - Executive summary with compliance rate
    - Department-wide statistics
    - Issue breakdown by severity
    - Faculty participation metrics
    - Recommendations for remediation
    - April 2027 deadline tracking

    Returns:
        PDF file download (application/pdf)
    """
    from fastapi.responses import Response

    _, user_id, department_id_from_token = api_key_info
    logger.info(f"Generating compliance PDF report for department: {department_id}")

    try:
        from ...education.compliance_dashboard import ComplianceDashboard
        from ...education.compliance_report_generator import ComplianceReportGenerator

        # Get comprehensive stats
        stats_obj = ComplianceDashboard.get_department_compliance(db, department_id)

        # Convert to dict for report generator
        stats = {
            "department_id": stats_obj.department_id,
            "department_name": stats_obj.department_name,
            "institution": stats_obj.institution,
            "overview": {
                "total_scans": stats_obj.total_scans,
                "total_files_scanned": stats_obj.total_files_scanned,
                "total_pages_slides": stats_obj.total_pages_slides,
                "compliance_rate": stats_obj.compliance_rate,
            },
            "compliance_scores": {
                "average": stats_obj.avg_compliance_score,
                "minimum": stats_obj.min_compliance_score,
                "maximum": stats_obj.max_compliance_score,
            },
            "issues": {
                "critical": stats_obj.total_critical,
                "high": stats_obj.total_high,
                "medium": stats_obj.total_medium,
                "low": stats_obj.total_low,
                "total": stats_obj.total_issues,
            },
            "scan_types": {
                "pdf": stats_obj.pdf_scans,
                "powerpoint": stats_obj.powerpoint_scans,
                "latex": stats_obj.latex_scans,
                "image": stats_obj.image_scans,
                "video": stats_obj.video_scans,
                "website": stats_obj.website_scans,
                "code": stats_obj.code_scans,
                "multimedia": stats_obj.multimedia_scans,
            },
            "compliance_breakdown": {
                "compliant": stats_obj.files_compliant,
                "needs_work": stats_obj.files_needs_work,
                "critical": stats_obj.files_critical,
            },
            "activity": {
                "scans_last_7_days": stats_obj.scans_last_7_days,
                "scans_last_30_days": stats_obj.scans_last_30_days,
                "scans_this_month": stats_obj.scans_this_month,
            },
            "april_2026_deadline": {
                "days_remaining": stats_obj.days_until_deadline,
                "estimated_hours_remaining": stats_obj.estimated_hours_remaining,
                "on_track": stats_obj.on_track,
            },
            "faculty": {
                "active_faculty": stats_obj.active_faculty,
                "total_faculty": stats_obj.total_faculty,
                "participation_rate": stats_obj.faculty_participation_rate,
            },
        }

        # Generate PDF
        pdf_bytes = ComplianceReportGenerator.generate_department_report(stats)

        # Return as downloadable file
        filename = f"compliance_report_{stats_obj.department_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating compliance PDF: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to generate compliance PDF. Please try again.",
        )
