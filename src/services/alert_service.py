"""
Alert Service

Manages alert notifications and preferences for departments.
Sends email alerts for scan completion, critical issues, and weekly summaries.

Preference Hierarchy:
1. User-level preferences (email_scan_complete, email_critical_alerts, etc.)
2. Department-level settings (EmailAlertSettings)
3. Defaults to True if neither exists
"""

import logging
from typing import List, Dict, Any, TYPE_CHECKING
from sqlalchemy.orm import Session

# Lazy import to avoid circular dependency:
# email_service -> email_templates -> services/__init__ -> alert_service -> email_service
if TYPE_CHECKING:
    from src.mailer.email_service import EmailService

logger = logging.getLogger(__name__)


# Alert type constants
ALERT_SCAN_COMPLETE = "scan_complete"
ALERT_REMEDIATION_COMPLETE = "remediation_complete"
ALERT_CRITICAL_ISSUES = "critical_issues"
ALERT_WEEKLY_SUMMARY = "weekly_summary"


class AlertService:
    """
    Service for managing and sending email alerts.

    Handles:
    - Alert preference management
    - Scan completion alerts
    - Critical issue alerts
    - Weekly summary alerts
    - Alert history tracking
    """

    def __init__(self, email_service: "EmailService" = None):
        """
        Initialize alert service.

        Args:
            email_service: Email service instance (optional, will create default if not provided)
        """
        if email_service is None:
            # Lazy import to avoid circular dependency
            from src.mailer.email_service import EmailService

            email_service = EmailService()
        self.email_service = email_service

    async def send_scan_complete_alert(
        self,
        to_emails: List[str],
        scan_id: str,
        file_name: str,
        issues_found: int,
        compliance_score: float,
        scan_url: str = None,
        department_id: str = None,
        db: Session = None,
    ) -> bool:
        """
        Send scan completion alert.

        Args:
            to_emails: List of recipient email addresses
            scan_id: Scan ID
            file_name: File name
            issues_found: Number of issues found
            compliance_score: Compliance score (0-1)
            scan_url: URL to view scan results
            department_id: Department ID (for checking alert settings)
            db: Database session (for checking alert settings)

        Returns:
            True if email sent successfully, False otherwise
        """
        # Check department-level setting and filter by user preferences
        if db and department_id:
            if not self.check_department_alert_enabled(
                department_id, ALERT_SCAN_COMPLETE, db
            ):
                logger.info(
                    f"Scan complete alerts disabled for department {department_id}"
                )
                return False

        # Filter recipients by their individual preferences
        if db:
            filtered_emails = self.filter_emails_by_preference(
                to_emails, ALERT_SCAN_COMPLETE, db
            )
            if not filtered_emails:
                logger.info("No recipients have scan complete alerts enabled")
                return True  # Successfully sent to 0 recipients (not an error)
            to_emails = filtered_emails

        try:
            result = await self.email_service.send_scan_complete(
                to_emails=to_emails,
                file_name=file_name,
                issues_found=issues_found,
                compliance_score=compliance_score,
                scan_url=scan_url,
            )

            if result.get("success"):
                logger.info(
                    f"Scan complete alert sent for scan {scan_id} to {len(to_emails)} recipients"
                )
                return True
            else:
                logger.error(
                    f"Failed to send scan complete alert: {result.get('error')}"
                )
                return False

        except Exception as e:
            logger.error(f"Error sending scan complete alert: {e}")
            return False

    async def send_critical_issue_alert(
        self,
        to_emails: List[str],
        scan_id: str,
        file_name: str,
        critical_issues: List[Dict[str, Any]],
        scan_url: str = None,
        department_id: str = None,
        db: Session = None,
    ) -> bool:
        """
        Send critical issue alert.

        Args:
            to_emails: List of recipient email addresses
            scan_id: Scan ID
            file_name: File name
            critical_issues: List of critical issues
            scan_url: URL to view scan results
            department_id: Department ID (for checking alert settings)
            db: Database session (for checking alert settings)

        Returns:
            True if email sent successfully, False otherwise
        """
        # Check department-level setting and filter by user preferences
        if db and department_id:
            if not self.check_department_alert_enabled(
                department_id, ALERT_CRITICAL_ISSUES, db
            ):
                logger.info(
                    f"Critical issue alerts disabled for department {department_id}"
                )
                return False

        # Filter recipients by their individual preferences
        if db:
            filtered_emails = self.filter_emails_by_preference(
                to_emails, ALERT_CRITICAL_ISSUES, db
            )
            if not filtered_emails:
                logger.info("No recipients have critical issue alerts enabled")
                return True  # Successfully sent to 0 recipients
            to_emails = filtered_emails

        try:
            result = await self.email_service.send_critical_issues(
                to_emails=to_emails,
                file_name=file_name,
                critical_issues=critical_issues,
                scan_url=scan_url,
            )

            if result.get("success"):
                logger.info(
                    f"Critical issue alert sent for scan {scan_id} to {len(to_emails)} recipients"
                )
                return True
            else:
                logger.error(
                    f"Failed to send critical issue alert: {result.get('error')}"
                )
                return False

        except Exception as e:
            logger.error(f"Error sending critical issue alert: {e}")
            return False

    async def send_weekly_summary(
        self,
        to_emails: List[str],
        start_date: str,
        end_date: str,
        total_scans: int,
        total_issues: int,
        avg_compliance_score: float,
        top_issues: List[Dict[str, Any]] = None,
        department_id: str = None,
        db: Session = None,
    ) -> bool:
        """
        Send weekly summary alert.

        Args:
            to_emails: List of recipient email addresses
            start_date: Summary start date
            end_date: Summary end date
            total_scans: Total number of scans
            total_issues: Total number of issues found
            avg_compliance_score: Average compliance score (0-1)
            top_issues: List of top issues
            department_id: Department ID (for checking alert settings)
            db: Database session (for checking alert settings)

        Returns:
            True if email sent successfully, False otherwise
        """
        # Check department-level setting and filter by user preferences
        if db and department_id:
            if not self.check_department_alert_enabled(
                department_id, ALERT_WEEKLY_SUMMARY, db
            ):
                logger.info(f"Weekly summary disabled for department {department_id}")
                return False

        # Filter recipients by their individual preferences
        if db:
            filtered_emails = self.filter_emails_by_preference(
                to_emails, ALERT_WEEKLY_SUMMARY, db
            )
            if not filtered_emails:
                logger.info("No recipients have weekly summary enabled")
                return True  # Successfully sent to 0 recipients
            to_emails = filtered_emails

        try:
            # Get department name from database if available
            department_name = "Your Department"
            if db and department_id:
                from ..db.models import Department

                department = (
                    db.query(Department).filter(Department.id == department_id).first()
                )
                if department:
                    department_name = department.name

            # Transform parameters to match EmailService.send_weekly_summary() signature
            result = await self.email_service.send_weekly_summary(
                to_emails=to_emails,
                department_name=department_name,
                total_files=total_scans,  # Map total_scans to total_files
                files_scanned=total_scans,  # Use total_scans for files scanned this week
                average_score=avg_compliance_score,
                total_issues=total_issues,
                issues_fixed=0,  # Default to 0 (could be calculated if data available)
                dashboard_url=None,  # Could be passed as parameter if needed
            )

            if result.get("success"):
                logger.info(
                    f"Weekly summary sent for {start_date} to {end_date} to {len(to_emails)} recipients"
                )
                return True
            else:
                logger.error(f"Failed to send weekly summary: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"Error sending weekly summary: {e}")
            return False

    def check_user_preference(
        self, user_email: str, alert_type: str, db: Session
    ) -> bool:
        """
        Check if a user has the given alert type enabled.

        Args:
            user_email: User's email address
            alert_type: Alert type constant (ALERT_SCAN_COMPLETE, etc.)
            db: Database session

        Returns:
            True if alert is enabled for this user, False otherwise
        """
        from ..db.models import User

        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            # Unknown user - default to False (don't spam random emails)
            return False

        # Map alert type to user preference field
        preference_map = {
            ALERT_SCAN_COMPLETE: user.email_scan_complete,
            ALERT_REMEDIATION_COMPLETE: user.email_remediation_complete,
            ALERT_CRITICAL_ISSUES: user.email_critical_alerts,
            ALERT_WEEKLY_SUMMARY: user.email_weekly_summary,
        }

        # Get preference, default to True if field doesn't exist
        preference = preference_map.get(alert_type)
        if preference is None:
            return True  # Default enabled

        return preference

    def filter_emails_by_preference(
        self, emails: List[str], alert_type: str, db: Session
    ) -> List[str]:
        """
        Filter a list of email addresses to only those with the alert type enabled.

        Args:
            emails: List of email addresses
            alert_type: Alert type constant
            db: Database session

        Returns:
            Filtered list of emails that have this alert enabled
        """
        if not db:
            # No DB session - can't check preferences, return all
            logger.warning("No DB session provided, cannot filter by preference")
            return emails

        return [
            email
            for email in emails
            if self.check_user_preference(email, alert_type, db)
        ]

    def check_department_alert_enabled(
        self, department_id: str, alert_type: str, db: Session
    ) -> bool:
        """
        Check if alert type is enabled at the department level.

        Args:
            department_id: Department ID
            alert_type: Alert type constant
            db: Database session

        Returns:
            True if alert is enabled for this department, False otherwise
        """
        from ..db.models import EmailAlertSettings

        settings = (
            db.query(EmailAlertSettings)
            .filter(
                EmailAlertSettings.department_id == department_id,
                EmailAlertSettings.user_id.is_(None),  # Department-wide settings
            )
            .first()
        )

        if not settings:
            # No settings - default to enabled
            return True

        # Map alert type to department setting
        setting_map = {
            ALERT_SCAN_COMPLETE: settings.alert_on_scan_complete,
            ALERT_CRITICAL_ISSUES: settings.alert_on_critical_issues,
            ALERT_WEEKLY_SUMMARY: settings.alert_weekly_summary,
        }

        setting = setting_map.get(alert_type)
        if setting is None:
            return True

        return setting

    def get_alert_recipients(
        self,
        department_id: str,
        alert_type: str,
        db: Session,
    ) -> List[str]:
        """
        Get email addresses for department alerts, filtered by user preferences.

        Args:
            department_id: Department ID
            alert_type: Alert type constant
            db: Database session

        Returns:
            List of email addresses that have this alert enabled
        """
        from ..db.models import User

        # Check department-level setting first
        if not self.check_department_alert_enabled(department_id, alert_type, db):
            return []

        # Get all active users in department with this alert enabled
        users = (
            db.query(User)
            .filter(
                User.department_id == department_id,
                User.is_active == True,
                User.email.isnot(None),
            )
            .all()
        )

        # Filter by user preference
        eligible_emails = []
        for user in users:
            if self.check_user_preference(user.email, alert_type, db):
                eligible_emails.append(user.email)

        return eligible_emails


# Global alert service instance
_alert_service_instance = None


def get_alert_service() -> AlertService:
    """Get global alert service instance."""
    global _alert_service_instance
    if _alert_service_instance is None:
        _alert_service_instance = AlertService()
    return _alert_service_instance
