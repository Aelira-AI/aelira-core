"""
Email Service Module

Provides email notifications for:
- Scan completion alerts
- Critical issue notifications
- Weekly compliance summaries
- System alerts
- Marketing campaigns (via SES)

Supports SMTP and SendGrid backends for transactional emails.
Supports Amazon SES for marketing campaigns.
"""

from .email_service import EmailService, get_email_service

__all__ = [
    "EmailService",
    "get_email_service",
]
