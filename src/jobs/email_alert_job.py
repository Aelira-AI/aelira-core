"""
Email Alert Job

Sends email notifications based on scan results and department settings.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from ..db.models import (
    EmailAlertSettings,
    Scan,
    Department,
    CloudFile,
    User,
)
from ..mailer import get_email_service
from ..services.alert_service import (
    ALERT_SCAN_COMPLETE,
    ALERT_CRITICAL_ISSUES,
    ALERT_WEEKLY_SUMMARY,
)

logger = logging.getLogger(__name__)


def filter_emails_by_user_preference(
    db: Session,
    emails: List[str],
    alert_type: str,
) -> List[str]:
    """
    Filter a list of email addresses to only those with the alert type enabled.

    Args:
        db: Database session
        emails: List of email addresses
        alert_type: Alert type constant (ALERT_SCAN_COMPLETE, etc.)

    Returns:
        Filtered list of emails that have this alert enabled
    """
    if not emails:
        return []

    # Map alert type to user preference field name
    preference_map = {
        ALERT_SCAN_COMPLETE: "email_scan_complete",
        ALERT_CRITICAL_ISSUES: "email_critical_alerts",
        ALERT_WEEKLY_SUMMARY: "email_weekly_summary",
    }

    pref_field = preference_map.get(alert_type)
    if not pref_field:
        return emails  # Unknown alert type, return all

    filtered = []
    for email in emails:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # Unknown user - skip (don't spam random emails)
            continue

        # Check user preference (default to True if field is None)
        preference = getattr(user, pref_field, None)
        if preference is None or preference:
            filtered.append(email)

    return filtered


async def send_scan_complete_alert(
    db: Session,
    department_id: str,
    scan_id: str,
    file_name: str,
    compliance_score: float,
    issues_found: int,
    scan_url: str,
) -> bool:
    """
    Send scan complete email if enabled for department.

    Args:
        db: Database session
        department_id: Department ID
        scan_id: Scan ID
        file_name: Name of scanned file
        compliance_score: Compliance score (0-100)
        issues_found: Number of issues found
        scan_url: URL to view scan results

    Returns:
        True if email was sent
    """
    settings = (
        db.query(EmailAlertSettings)
        .filter(EmailAlertSettings.department_id == department_id)
        .first()
    )

    if not settings or not settings.alert_on_scan_complete:
        return False

    if not settings.email_addresses:
        logger.warning(f"No email addresses configured for department {department_id}")
        return False

    # Filter by individual user preferences
    filtered_emails = filter_emails_by_user_preference(
        db, settings.email_addresses, ALERT_SCAN_COMPLETE
    )
    if not filtered_emails:
        logger.info(
            f"No recipients with scan_complete alerts enabled for department {department_id}"
        )
        return True  # Successfully sent to 0 recipients (not an error)

    try:
        email_service = get_email_service()
        await email_service.send_scan_complete(
            to_emails=filtered_emails,
            file_name=file_name,
            compliance_score=compliance_score,
            issues_found=issues_found,
            action_url=scan_url,
            action_text="View Scan Results",
        )
        logger.info(f"Sent scan complete alert to {len(filtered_emails)} recipients")
        return True
    except Exception as e:
        logger.error(f"Failed to send scan complete alert: {e}")
        return False


async def send_critical_issues_alert(
    db: Session,
    department_id: str,
    scan_id: str,
    file_name: str,
    issues: List[Dict[str, Any]],
    scan_url: str,
    remediate_url: str,
) -> bool:
    """
    Send critical issues alert if enabled and critical issues found.

    Args:
        db: Database session
        department_id: Department ID
        scan_id: Scan ID
        file_name: Name of scanned file
        issues: List of issues with severity
        scan_url: URL to view scan results
        remediate_url: URL to remediate file

    Returns:
        True if email was sent
    """
    settings = (
        db.query(EmailAlertSettings)
        .filter(EmailAlertSettings.department_id == department_id)
        .first()
    )

    if not settings or not settings.alert_on_critical_issues:
        return False

    if not settings.email_addresses:
        return False

    # Filter by individual user preferences
    filtered_emails = filter_emails_by_user_preference(
        db, settings.email_addresses, ALERT_CRITICAL_ISSUES
    )
    if not filtered_emails:
        logger.info(
            f"No recipients with critical_alerts enabled for department {department_id}"
        )
        return True  # Successfully sent to 0 recipients (not an error)

    # Filter critical issues
    critical_issues = []
    for issue in issues:
        severity = issue.get("severity", issue.get("impact", "minor")).lower()
        if severity in ("critical", "blocker"):
            critical_issues.append(
                {
                    "rule": issue.get("rule", issue.get("id", "unknown")),
                    "description": issue.get("description", issue.get("message", "")),
                    "wcag": issue.get("wcag", issue.get("help", "")),
                    "count": issue.get("count", 1),
                }
            )

    if not critical_issues:
        return False

    try:
        email_service = get_email_service()
        await email_service.send_critical_issues(
            to_emails=filtered_emails,
            file_name=file_name,
            critical_issues=critical_issues,
            action_url=scan_url,
            action_text="Review Issues",
            remediate_url=remediate_url,
        )
        logger.info(
            f"Sent critical issues alert ({len(critical_issues)} issues) to {len(filtered_emails)} recipients"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send critical issues alert: {e}")
        return False


async def send_weekly_summaries(db: Session) -> Dict[str, Any]:
    """
    Send weekly summary emails to all departments with enabled setting.

    Should be called by a scheduled job (e.g., cron) once per week.

    Args:
        db: Database session

    Returns:
        Summary of emails sent
    """
    # Get current day and hour (UTC)
    now = datetime.now(timezone.utc)
    current_day = now.weekday()  # 0=Monday
    current_hour = now.hour

    # Find departments with matching schedule
    settings_list = (
        db.query(EmailAlertSettings)
        .filter(
            EmailAlertSettings.alert_weekly_summary,
            EmailAlertSettings.weekly_summary_day == current_day,
            EmailAlertSettings.weekly_summary_hour == current_hour,
        )
        .all()
    )

    results = {
        "total_departments": len(settings_list),
        "emails_sent": 0,
        "errors": 0,
    }

    for settings in settings_list:
        if not settings.email_addresses:
            continue

        try:
            await _send_weekly_summary_for_department(db, settings)
            results["emails_sent"] += 1
        except Exception as e:
            logger.error(
                f"Failed to send weekly summary for {settings.department_id}: {e}"
            )
            results["errors"] += 1

    return results


async def _send_weekly_summary_for_department(
    db: Session,
    settings: EmailAlertSettings,
) -> bool:
    """Send weekly summary for a single department."""
    department_id = settings.department_id

    # Filter by individual user preferences
    filtered_emails = filter_emails_by_user_preference(
        db, settings.email_addresses, ALERT_WEEKLY_SUMMARY
    )
    if not filtered_emails:
        logger.info(
            f"No recipients with weekly_summary enabled for department {department_id}"
        )
        return True  # Successfully sent to 0 recipients (not an error)

    # Get department info
    department = db.query(Department).filter(Department.id == department_id).first()
    department_name = department.name if department else "Your Department"

    # Calculate date range (last 7 days)
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    week_end = now

    # Get scan statistics for the week
    scans = (
        db.query(Scan)
        .filter(
            Scan.department_id == department_id,
            Scan.created_at >= week_start,
            Scan.created_at <= week_end,
        )
        .all()
    )

    # Calculate metrics
    scans_this_week = len(scans)
    total_issues = sum(s.issues_found or 0 for s in scans)

    # Calculate average score
    scores = [s.compliance_score for s in scans if s.compliance_score is not None]
    average_score = sum(scores) / len(scores) if scores else 0

    # Get total tracked files
    total_files = (
        db.query(CloudFile).filter(CloudFile.department_id == department_id).count()
    )

    # Count issues by severity (aggregate from all scans)
    severity_counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    for scan in scans:
        if scan.issues_data:
            for issue in scan.issues_data:
                severity = issue.get("severity", issue.get("impact", "minor")).lower()
                if severity in ("critical", "blocker"):
                    severity_counts["critical"] += 1
                elif severity in ("serious", "major"):
                    severity_counts["serious"] += 1
                elif severity in ("moderate", "medium"):
                    severity_counts["moderate"] += 1
                else:
                    severity_counts["minor"] += 1

    # Calculate issues fixed (would need a tracking system for accurate count)
    # For now, estimate based on completed remediations
    issues_fixed = 0  # Placeholder

    # Calculate score change (would need historical data)
    score_change = "No previous data"  # Placeholder

    # Calculate days until deadline (April 24, 2026)
    deadline = datetime(2026, 4, 24, tzinfo=timezone.utc)
    days_until_deadline = (deadline - now).days

    # Calculate percentages for progress bars
    sum(severity_counts.values()) or 1

    email_service = get_email_service()
    await email_service.send_weekly_summary(
        to_emails=filtered_emails,
        department_name=department_name,
        total_files=total_files,
        total_issues=total_issues,
        scans_this_week=scans_this_week,
        issues_fixed=issues_fixed,
        average_score=round(average_score, 1),
        score_change=score_change,
        critical_count=severity_counts["critical"],
        serious_count=severity_counts["serious"],
        moderate_count=severity_counts["moderate"],
        minor_count=severity_counts["minor"],
        dashboard_url=f"https://app.aelira.ai/dashboard?dept={department_id}",
        week_start=week_start.strftime("%b %d"),
        week_end=week_end.strftime("%b %d, %Y"),
        days_until_deadline=days_until_deadline,
    )

    logger.info(
        f"Sent weekly summary to {department_name} ({len(filtered_emails)} recipients)"
    )
    return True


async def trigger_scan_alerts(
    db: Session,
    scan: Scan,
    app_base_url: str = "https://app.aelira.ai",
) -> Dict[str, bool]:
    """
    Trigger all applicable alerts for a completed scan.

    Call this after a scan completes.

    Args:
        db: Database session
        scan: Completed scan object
        app_base_url: Base URL of the application

    Returns:
        Dict indicating which alerts were sent
    """
    results = {
        "scan_complete_sent": False,
        "critical_issues_sent": False,
    }

    # Get file name from scan metadata or target URL
    file_name = "Unknown file"
    if scan.metadata:
        file_name = scan.metadata.get("file_name", file_name)
    elif scan.target_url:
        file_name = scan.target_url.split("/")[-1]

    scan_url = f"{app_base_url}/scans/{scan.id}"
    remediate_url = f"{app_base_url}/remediate/{scan.id}"

    # Send scan complete alert
    results["scan_complete_sent"] = await send_scan_complete_alert(
        db=db,
        department_id=scan.department_id,
        scan_id=scan.id,
        file_name=file_name,
        compliance_score=scan.compliance_score or 0,
        issues_found=scan.issues_found or 0,
        scan_url=scan_url,
    )

    # Send critical issues alert if applicable
    if scan.issues_data:
        results["critical_issues_sent"] = await send_critical_issues_alert(
            db=db,
            department_id=scan.department_id,
            scan_id=scan.id,
            file_name=file_name,
            issues=scan.issues_data,
            scan_url=scan_url,
            remediate_url=remediate_url,
        )

    return results
