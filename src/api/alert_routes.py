"""
Email Alert Settings API Routes

Manages department email notification preferences.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime, timezone
import uuid

from ..db.database import get_db_dependency
from ..db.models import EmailAlertSettings, Department
from ..api.auth_routes import get_current_api_key
from ..db.models import APIKey

router = APIRouter(prefix="/alerts", tags=["email-alerts"])


# Request/Response Models


class AlertSettingsRequest(BaseModel):
    """Request to update alert settings."""

    alert_on_scan_complete: Optional[bool] = None
    alert_on_critical_issues: Optional[bool] = None
    alert_weekly_summary: Optional[bool] = None
    email_addresses: Optional[List[EmailStr]] = None
    weekly_summary_day: Optional[int] = None  # 0=Monday, 6=Sunday
    weekly_summary_hour: Optional[int] = None  # 0-23 UTC


class AlertSettingsResponse(BaseModel):
    """Alert settings response."""

    id: str
    department_id: str
    alert_on_scan_complete: bool
    alert_on_critical_issues: bool
    alert_weekly_summary: bool
    email_addresses: List[str]
    weekly_summary_day: int
    weekly_summary_hour: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class AddEmailRequest(BaseModel):
    """Request to add an email address."""

    email: EmailStr


class RemoveEmailRequest(BaseModel):
    """Request to remove an email address."""

    email: EmailStr


class TestEmailRequest(BaseModel):
    """Request to send a test email."""

    email_type: str  # 'scan_complete', 'critical_issues', 'weekly_summary'


class TestEmailResponse(BaseModel):
    """Test email response."""

    success: bool
    message: str


class EmailListResponse(BaseModel):
    """Response for email list."""

    emails: List[str]
    count: int


class ScanCompleteRequest(BaseModel):
    """Request to trigger scan complete alert."""

    scan_id: str
    file_name: str
    issues_found: int
    compliance_score: float
    critical_issues: Optional[int] = None


class CriticalIssuesRequest(BaseModel):
    """Request to trigger critical issues alert."""

    scan_id: str
    file_name: str
    critical_issues: List[dict]


class WeeklySummaryRequest(BaseModel):
    """Request to trigger weekly summary alert."""

    start_date: str
    end_date: str
    total_scans: int
    total_issues: int
    avg_compliance_score: float
    top_issues: Optional[List[dict]] = None


class SendAlertRequest(BaseModel):
    """Request to send alert to multiple recipients."""

    recipients: List[EmailStr]
    subject: str
    body: str


class TriggerResponse(BaseModel):
    """Response for alert trigger endpoints."""

    success: bool
    message: str
    recipients_count: int = 0


# Helper functions


def get_or_create_settings(db: Session, department_id: str) -> EmailAlertSettings:
    """Get existing settings or create defaults."""
    settings = (
        db.query(EmailAlertSettings)
        .filter(EmailAlertSettings.department_id == department_id)
        .first()
    )

    if not settings:
        settings = EmailAlertSettings(
            id=str(uuid.uuid4()),
            department_id=department_id,
            alert_on_scan_complete=True,
            alert_on_critical_issues=True,
            alert_weekly_summary=True,
            email_addresses=[],
            weekly_summary_day=0,  # Monday
            weekly_summary_hour=9,  # 9 AM UTC
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)

    return settings


# Routes


@router.get("/settings", response_model=AlertSettingsResponse)
async def get_alert_settings(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get email alert settings for the department.

    Returns current notification preferences and email list.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    return AlertSettingsResponse(
        id=settings.id,
        department_id=settings.department_id,
        alert_on_scan_complete=settings.alert_on_scan_complete,
        alert_on_critical_issues=settings.alert_on_critical_issues,
        alert_weekly_summary=settings.alert_weekly_summary,
        email_addresses=settings.email_addresses or [],
        weekly_summary_day=getattr(settings, "weekly_summary_day", 0),
        weekly_summary_hour=getattr(settings, "weekly_summary_hour", 9),
        created_at=settings.created_at,
        updated_at=getattr(settings, "updated_at", None),
    )


@router.put("/settings", response_model=AlertSettingsResponse)
async def update_alert_settings(
    request: AlertSettingsRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Update email alert settings.

    Allows toggling notification types and updating email list.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    # Update only provided fields
    if request.alert_on_scan_complete is not None:
        settings.alert_on_scan_complete = request.alert_on_scan_complete

    if request.alert_on_critical_issues is not None:
        settings.alert_on_critical_issues = request.alert_on_critical_issues

    if request.alert_weekly_summary is not None:
        settings.alert_weekly_summary = request.alert_weekly_summary

    if request.email_addresses is not None:
        # Validate and dedupe email addresses
        emails = list(set(str(e) for e in request.email_addresses))
        settings.email_addresses = emails

    if request.weekly_summary_day is not None:
        if not 0 <= request.weekly_summary_day <= 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="weekly_summary_day must be 0-6 (Monday-Sunday)",
            )
        settings.weekly_summary_day = request.weekly_summary_day

    if request.weekly_summary_hour is not None:
        if not 0 <= request.weekly_summary_hour <= 23:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="weekly_summary_hour must be 0-23 (UTC)",
            )
        settings.weekly_summary_hour = request.weekly_summary_hour

    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(settings)

    return AlertSettingsResponse(
        id=settings.id,
        department_id=settings.department_id,
        alert_on_scan_complete=settings.alert_on_scan_complete,
        alert_on_critical_issues=settings.alert_on_critical_issues,
        alert_weekly_summary=settings.alert_weekly_summary,
        email_addresses=settings.email_addresses or [],
        weekly_summary_day=getattr(settings, "weekly_summary_day", 0),
        weekly_summary_hour=getattr(settings, "weekly_summary_hour", 9),
        created_at=settings.created_at,
        updated_at=getattr(settings, "updated_at", None),
    )


@router.post("/emails/add", response_model=AlertSettingsResponse)
async def add_email_address(
    request: AddEmailRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Add an email address to the notification list.

    Duplicate emails are ignored.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    email = str(request.email).lower()
    emails = settings.email_addresses or []

    if email not in [e.lower() for e in emails]:
        emails.append(request.email)
        settings.email_addresses = emails
        settings.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(settings)

    return AlertSettingsResponse(
        id=settings.id,
        department_id=settings.department_id,
        alert_on_scan_complete=settings.alert_on_scan_complete,
        alert_on_critical_issues=settings.alert_on_critical_issues,
        alert_weekly_summary=settings.alert_weekly_summary,
        email_addresses=settings.email_addresses or [],
        weekly_summary_day=getattr(settings, "weekly_summary_day", 0),
        weekly_summary_hour=getattr(settings, "weekly_summary_hour", 9),
        created_at=settings.created_at,
        updated_at=getattr(settings, "updated_at", None),
    )


@router.post("/emails/remove", response_model=AlertSettingsResponse)
async def remove_email_address(
    request: RemoveEmailRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Remove an email address from the notification list.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    email = str(request.email).lower()
    emails = settings.email_addresses or []

    # Filter out the email (case-insensitive)
    settings.email_addresses = [e for e in emails if e.lower() != email]
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(settings)

    return AlertSettingsResponse(
        id=settings.id,
        department_id=settings.department_id,
        alert_on_scan_complete=settings.alert_on_scan_complete,
        alert_on_critical_issues=settings.alert_on_critical_issues,
        alert_weekly_summary=settings.alert_weekly_summary,
        email_addresses=settings.email_addresses or [],
        weekly_summary_day=getattr(settings, "weekly_summary_day", 0),
        weekly_summary_hour=getattr(settings, "weekly_summary_hour", 9),
        created_at=settings.created_at,
        updated_at=getattr(settings, "updated_at", None),
    )


@router.post("/test", response_model=TestEmailResponse)
async def send_test_email(
    request: TestEmailRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Send a test email to verify configuration.

    Sends to all configured email addresses.
    """
    from ..email import get_email_service

    settings = get_or_create_settings(db, api_key.department_id)

    if not settings.email_addresses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email addresses configured. Add at least one email first.",
        )

    # Get department name
    department = (
        db.query(Department).filter(Department.id == api_key.department_id).first()
    )
    department_name = department.name if department else "Your Department"

    email_service = get_email_service()

    try:
        if request.email_type == "scan_complete":
            await email_service.send_scan_complete(
                to_emails=settings.email_addresses,
                file_name="test_document.pdf",
                compliance_score=85.5,
                issues_found=12,
                action_url="https://app.aelira.ai/scans/test",
                action_text="View Scan Results",
            )
        elif request.email_type == "critical_issues":
            await email_service.send_critical_issues(
                to_emails=settings.email_addresses,
                file_name="test_document.pdf",
                critical_issues=[
                    {
                        "rule": "image-alt",
                        "description": "Images must have alternate text",
                        "wcag": "WCAG 1.1.1 (A)",
                        "count": 5,
                    },
                    {
                        "rule": "color-contrast",
                        "description": "Text must have sufficient color contrast",
                        "wcag": "WCAG 1.4.3 (AA)",
                        "count": 3,
                    },
                ],
                action_url="https://app.aelira.ai/scans/test",
                action_text="Review Issues",
                remediate_url="https://app.aelira.ai/remediate/test",
            )
        elif request.email_type == "weekly_summary":
            await email_service.send_weekly_summary(
                to_emails=settings.email_addresses,
                department_name=department_name,
                total_files=150,
                total_issues=324,
                scans_this_week=45,
                issues_fixed=67,
                average_score=78.5,
                score_change="+2.3% from last week",
                critical_count=12,
                serious_count=45,
                moderate_count=89,
                minor_count=178,
                dashboard_url="https://app.aelira.ai/dashboard",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown email type: {request.email_type}. Use 'scan_complete', 'critical_issues', or 'weekly_summary'.",
            )

        return TestEmailResponse(
            success=True,
            message=f"Test {request.email_type} email sent to {len(settings.email_addresses)} recipient(s).",
        )

    except Exception as e:
        return TestEmailResponse(
            success=False,
            message=f"Failed to send test email: {str(e)}",
        )


@router.get("/history")
async def get_alert_history(
    limit: int = 50,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get recent email alert history.

    Returns last N emails sent to the department.
    """
    # This would query an email_log table if we had one
    # For now, return empty list
    return {
        "alerts": [],
        "message": "Email history tracking coming soon.",
    }


@router.post("/pause")
async def pause_alerts(
    days: int = 7,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Temporarily pause all email alerts.

    Useful during maintenance or breaks.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    # Store pause info (would need additional column)
    # For now, just disable all alerts
    settings.alert_on_scan_complete = False
    settings.alert_on_critical_issues = False
    settings.alert_weekly_summary = False
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": "All email alerts paused. Re-enable them in settings when ready.",
    }


@router.post("/resume")
async def resume_alerts(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Resume all email alerts.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    settings.alert_on_scan_complete = True
    settings.alert_on_critical_issues = True
    settings.alert_weekly_summary = True
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": "All email alerts resumed.",
    }


# Email list management endpoints (RESTful alternatives)


@router.get("/emails", response_model=EmailListResponse)
async def list_email_addresses(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List all configured alert email addresses.
    """
    settings = get_or_create_settings(db, api_key.department_id)
    emails = settings.email_addresses or []

    return EmailListResponse(
        emails=emails,
        count=len(emails),
    )


@router.post("/emails", response_model=AlertSettingsResponse)
async def add_email_post(
    request: AddEmailRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Add an email address to the notification list.

    Alternative to POST /emails/add for RESTful consistency.
    """
    return await add_email_address(request, api_key, db)


@router.delete("/emails/{email}")
async def delete_email_address(
    email: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Remove an email address from the notification list.
    """
    settings = get_or_create_settings(db, api_key.department_id)

    email_lower = email.lower()
    emails = settings.email_addresses or []
    original_count = len(emails)

    # Filter out the email (case-insensitive)
    settings.email_addresses = [e for e in emails if e.lower() != email_lower]

    if len(settings.email_addresses) == original_count:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email address not found: {email}",
        )

    settings.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": f"Email address removed: {email}",
    }


# Alert trigger endpoints


@router.post("/trigger/scan-complete", response_model=TriggerResponse)
async def trigger_scan_complete(
    request: ScanCompleteRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Trigger a scan complete notification email.

    Sends notification to all configured email addresses if alert_on_scan_complete is enabled.
    """
    from ..email import get_email_service

    settings = get_or_create_settings(db, api_key.department_id)

    # Check if scan complete alerts are enabled
    if not settings.alert_on_scan_complete:
        return TriggerResponse(
            success=True,
            message="Scan complete alerts are disabled. No email sent.",
            recipients_count=0,
        )

    if not settings.email_addresses:
        return TriggerResponse(
            success=True,
            message="No email addresses configured. No email sent.",
            recipients_count=0,
        )

    email_service = get_email_service()

    try:
        await email_service.send_scan_complete(
            to_emails=settings.email_addresses,
            file_name=request.file_name,
            compliance_score=request.compliance_score,
            issues_found=request.issues_found,
            action_url=f"https://app.aelira.ai/scans/{request.scan_id}",
            action_text="View Scan Results",
        )

        return TriggerResponse(
            success=True,
            message=f"Scan complete alert sent for {request.file_name}",
            recipients_count=len(settings.email_addresses),
        )

    except Exception as e:
        return TriggerResponse(
            success=False,
            message=f"Failed to send scan complete alert: {str(e)}",
            recipients_count=0,
        )


@router.post("/trigger/critical-issues", response_model=TriggerResponse)
async def trigger_critical_issues(
    request: CriticalIssuesRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Trigger a critical issues notification email.

    Sends notification when critical accessibility issues are found.
    """
    from ..email import get_email_service

    settings = get_or_create_settings(db, api_key.department_id)

    # Check if critical issue alerts are enabled
    if not settings.alert_on_critical_issues:
        return TriggerResponse(
            success=True,
            message="Critical issue alerts are disabled. No email sent.",
            recipients_count=0,
        )

    if not settings.email_addresses:
        return TriggerResponse(
            success=True,
            message="No email addresses configured. No email sent.",
            recipients_count=0,
        )

    email_service = get_email_service()

    try:
        # Format critical issues for email
        formatted_issues = []
        for issue in request.critical_issues:
            formatted_issues.append(
                {
                    "rule": issue.get("type", "unknown"),
                    "description": issue.get(
                        "description", "Critical accessibility issue"
                    ),
                    "wcag": issue.get("wcag", "WCAG 2.1"),
                    "count": issue.get("count", 1),
                }
            )

        await email_service.send_critical_issues(
            to_emails=settings.email_addresses,
            file_name=request.file_name,
            critical_issues=formatted_issues,
            action_url=f"https://app.aelira.ai/scans/{request.scan_id}",
            action_text="Review Issues",
            remediate_url=f"https://app.aelira.ai/remediate/{request.scan_id}",
        )

        return TriggerResponse(
            success=True,
            message=f"Critical issues alert sent for {request.file_name}",
            recipients_count=len(settings.email_addresses),
        )

    except Exception as e:
        return TriggerResponse(
            success=False,
            message=f"Failed to send critical issues alert: {str(e)}",
            recipients_count=0,
        )


@router.post("/trigger/weekly-summary", response_model=TriggerResponse)
async def trigger_weekly_summary(
    request: WeeklySummaryRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Trigger a weekly summary notification email.

    Sends a summary of accessibility compliance for the past week.
    """
    from ..email import get_email_service

    settings = get_or_create_settings(db, api_key.department_id)

    # Check if weekly summary alerts are enabled
    if not settings.alert_weekly_summary:
        return TriggerResponse(
            success=True,
            message="Weekly summary alerts are disabled. No email sent.",
            recipients_count=0,
        )

    if not settings.email_addresses:
        return TriggerResponse(
            success=True,
            message="No email addresses configured. No email sent.",
            recipients_count=0,
        )

    # Get department name
    department = (
        db.query(Department).filter(Department.id == api_key.department_id).first()
    )
    department_name = department.name if department else "Your Department"

    email_service = get_email_service()

    try:
        await email_service.send_weekly_summary(
            to_emails=settings.email_addresses,
            department_name=department_name,
            total_files=request.total_scans,
            total_issues=request.total_issues,
            scans_this_week=request.total_scans,
            issues_fixed=0,  # Would need to track this
            average_score=request.avg_compliance_score * 100,
            score_change="",
            critical_count=0,
            serious_count=0,
            moderate_count=0,
            minor_count=request.total_issues,
            dashboard_url="https://app.aelira.ai/dashboard",
        )

        return TriggerResponse(
            success=True,
            message=f"Weekly summary sent for {request.start_date} to {request.end_date}",
            recipients_count=len(settings.email_addresses),
        )

    except Exception as e:
        return TriggerResponse(
            success=False,
            message=f"Failed to send weekly summary: {str(e)}",
            recipients_count=0,
        )


@router.post("/send", response_model=TriggerResponse)
async def send_alert_to_recipients(
    request: SendAlertRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Send a custom alert email to specified recipients.
    """
    from ..email import get_email_service

    if not request.recipients:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one recipient email is required.",
        )

    email_service = get_email_service()

    try:
        # Use a generic send method or construct HTML
        for recipient in request.recipients:
            await email_service.send_email(
                to_email=str(recipient),
                subject=request.subject,
                html_content=f"<p>{request.body}</p>",
            )

        return TriggerResponse(
            success=True,
            message=f"Alert sent to {len(request.recipients)} recipient(s)",
            recipients_count=len(request.recipients),
        )

    except Exception as e:
        return TriggerResponse(
            success=False,
            message=f"Failed to send alert: {str(e)}",
            recipients_count=0,
        )
