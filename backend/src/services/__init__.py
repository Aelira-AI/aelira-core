"""
Services Module

Business logic and service layer for Aelira backend.

Note: EmailService is NOT re-exported here to avoid circular imports.
Import it directly: from src.mailer.email_service import EmailService
"""

from .alert_service import AlertService
from .email_templates import (
    render_scan_complete_email,
    render_critical_issue_email,
    render_weekly_summary_email,
    render_remediation_success_email,
    render_remediation_partial_email,
    render_remediation_failure_email,
)

__all__ = [
    "AlertService",
    "render_scan_complete_email",
    "render_critical_issue_email",
    "render_weekly_summary_email",
    "render_remediation_success_email",
    "render_remediation_partial_email",
    "render_remediation_failure_email",
]
