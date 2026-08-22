"""
Email Service

Provides email sending functionality with template support.
Supports SMTP and SendGrid backends.
"""

import html as html_lib
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict, Any
from pathlib import Path
import httpx

from src.services.email_templates import (
    get_email_wrapper,
)

logger = logging.getLogger(__name__)


class EmailService:
    """
    Email service with SMTP and SendGrid support.

    Features:
    - HTML and plain text email support
    - Template rendering with variables
    - Multiple recipients
    - SendGrid API integration
    - SMTP fallback
    """

    def __init__(
        self,
        smtp_host: str = None,
        smtp_port: int = None,
        smtp_user: str = None,
        smtp_password: str = None,
        from_email: str = None,
        from_name: str = None,
        sendgrid_api_key: str = None,
    ):
        """
        Initialize email service.

        Args:
            smtp_host: SMTP server host
            smtp_port: SMTP server port
            smtp_user: SMTP username
            smtp_password: SMTP password
            from_email: Default from email address
            from_name: Default from name
            sendgrid_api_key: SendGrid API key (if using SendGrid)
        """
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.sendgrid.net")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER", "apikey")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.from_email = from_email or os.getenv("FROM_EMAIL", "noreply@example.com")
        self.from_name = from_name or os.getenv("FROM_NAME", "Aelira Accessibility")
        self.sendgrid_api_key = sendgrid_api_key or os.getenv("SENDGRID_API_KEY", "")

        # Template directory
        self.template_dir = Path(__file__).parent / "templates"

    def is_configured(self) -> bool:
        """
        Check if email service is configured.

        Supports three modes:
        - SendGrid API key
        - Authenticated SMTP (with password)
        - Trusted-network SMTP without auth (e.g., Mailcow on same Docker network)
          Detected when SMTP_HOST is explicitly set in the environment.
        """
        if self.sendgrid_api_key:
            return True
        if self.smtp_password:
            return True
        # Trusted-network SMTP: if SMTP_HOST is explicitly set, assume the host
        # is reachable without auth (e.g., Mailcow postfix on same Docker network)
        if os.getenv("SMTP_HOST"):
            return True
        return False

    async def send_email(
        self,
        to_emails: List[str],
        subject: str,
        html_content: str,
        text_content: str = None,
        from_email: str = None,
        from_name: str = None,
        reply_to: str = None,
    ) -> Dict[str, Any]:
        """
        Send an email.

        Args:
            to_emails: List of recipient email addresses
            subject: Email subject
            html_content: HTML body content
            text_content: Plain text body (optional, auto-generated if not provided)
            from_email: Override from email
            from_name: Override from name
            reply_to: Reply-to address

        Returns:
            Dict with success status and message ID
        """
        if not self.is_configured():
            logger.warning("Email service not configured, skipping send")
            return {"success": False, "error": "Email service not configured"}

        from_email = from_email or self.from_email
        from_name = from_name or self.from_name

        # Use SendGrid API if configured
        if self.sendgrid_api_key:
            return await self._send_via_sendgrid(
                to_emails=to_emails,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                from_email=from_email,
                from_name=from_name,
                reply_to=reply_to,
            )
        else:
            return await self._send_via_smtp(
                to_emails=to_emails,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                from_email=from_email,
                from_name=from_name,
            )

    async def _send_via_sendgrid(
        self,
        to_emails: List[str],
        subject: str,
        html_content: str,
        text_content: str,
        from_email: str,
        from_name: str,
        reply_to: str,
    ) -> Dict[str, Any]:
        """Send email via SendGrid API."""
        try:
            payload = {
                "personalizations": [{"to": [{"email": email} for email in to_emails]}],
                "from": {
                    "email": from_email,
                    "name": from_name,
                },
                "subject": subject,
                "content": [
                    {"type": "text/html", "value": html_content},
                ],
            }

            if text_content:
                payload["content"].insert(
                    0, {"type": "text/plain", "value": text_content}
                )

            if reply_to:
                payload["reply_to"] = {"email": reply_to}

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.sendgrid_api_key}",
                        "Content-Type": "application/json",
                    },
                )

                if response.status_code in (200, 202):
                    message_id = response.headers.get("X-Message-Id", "")
                    logger.info(
                        f"Email sent via SendGrid to {to_emails}, message_id={message_id}"
                    )
                    return {"success": True, "message_id": message_id}
                else:
                    logger.error(
                        f"SendGrid error: {response.status_code} - {response.text}"
                    )
                    return {"success": False, "error": response.text}

        except Exception as e:
            logger.error(f"SendGrid send failed: {e}")
            return {"success": False, "error": str(e)}

    async def _send_via_smtp(
        self,
        to_emails: List[str],
        subject: str,
        html_content: str,
        text_content: str,
        from_email: str,
        from_name: str,
    ) -> Dict[str, Any]:
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = ", ".join(to_emails)

            # Add plain text part
            if text_content:
                msg.attach(MIMEText(text_content, "plain"))

            # Add HTML part
            msg.attach(MIMEText(html_content, "html"))

            # Send via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                try:
                    server.starttls()
                except (smtplib.SMTPNotSupportedError, smtplib.SMTPException):
                    pass  # Trusted network without TLS (e.g., local Mailcow)
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(from_email, to_emails, msg.as_string())

            logger.info(f"Email sent via SMTP to {to_emails}")
            return {"success": True}

        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return {"success": False, "error": str(e)}

    def render_template(
        self,
        template_name: str,
        variables: Dict[str, Any],
    ) -> str:
        """
        Render an email template with variables.

        Template files should contain only the inner content (no <!DOCTYPE>,
        <html>, <body>, or header/footer). The branded wrapper with logo,
        header, and footer is applied automatically via get_email_wrapper().

        Args:
            template_name: Name of template file (without extension)
            variables: Variables to substitute in template

        Returns:
            Rendered HTML content wrapped in branded template
        """
        template_path = self.template_dir / f"{template_name}.html"

        if not template_path.exists():
            # Use default template
            return self._default_template(variables)

        with open(template_path, "r") as f:
            template = f.read()

        # Variable substitution with HTML escaping to prevent injection.
        # Keys ending in _html or _url are inserted raw (trusted content).
        for key, value in variables.items():
            str_value = str(value)
            if not key.endswith(("_html", "_url")):
                str_value = html_lib.escape(str_value)
            template = template.replace(f"{{{{{key}}}}}", str_value)

        # Wrap in branded template
        unsubscribe_url = variables.get("unsubscribe_url") or None
        return get_email_wrapper(template, unsubscribe_url=unsubscribe_url)

    def _default_template(self, variables: Dict[str, Any]) -> str:
        """Generate a default HTML email template using branded wrapper."""
        title = variables.get("title", "Notification")
        message = variables.get("message", "")
        action_url = variables.get("action_url", "")
        action_text = variables.get("action_text", "View Details")
        unsubscribe_url = variables.get("unsubscribe_url", "")

        action_button = ""
        if action_url:
            action_button = f"""
            <div style="text-align: center; margin-top: 24px;">
                <a href="{action_url}"
                   style="background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 8px; display: inline-block; font-weight: 600;">
                    {action_text}
                </a>
            </div>
            """

        content = f"""
            <h2 style="color: #1a1a2e; margin: 0 0 16px 0;">{title}</h2>
            <div>{message}</div>
            {action_button}
        """

        return get_email_wrapper(content, unsubscribe_url=unsubscribe_url or None)

    # ==========================================================================
    # Convenience Methods for Common Notifications
    # ==========================================================================

    async def send_scan_complete(
        self,
        to_emails: List[str],
        file_name: str,
        compliance_score: float,
        issues_found: int,
        scan_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send scan completion notification."""
        # Sanitize user-controlled input
        safe_file_name = html_lib.escape(file_name)

        # Determine status color and message
        if compliance_score >= 90:
            status_color = "#22c55e"
            status_text = "Excellent"
        elif compliance_score >= 70:
            status_color = "#f59e0b"
            status_text = "Needs Improvement"
        else:
            status_color = "#ef4444"
            status_text = "Critical Issues Found"

        html = self.render_template(
            "scan_complete",
            {
                "title": "Scan Complete",
                "file_name": safe_file_name,
                "compliance_score": f"{compliance_score:.0f}%",
                "issues_found": issues_found,
                "status_color": status_color,
                "status_text": status_text,
                "action_url": scan_url or "",
                "action_text": "View Results",
                "message": f"""
                <p>The accessibility scan for <strong>{safe_file_name}</strong> has completed.</p>
                <div style="text-align: center; margin: 24px 0;">
                    <div style="font-size: 48px; font-weight: bold; color: {status_color};">
                        {compliance_score:.0f}%
                    </div>
                    <div style="color: {status_color}; font-weight: 600;">{status_text}</div>
                    <div style="color: #666; margin-top: 8px;">{issues_found} issues found</div>
                </div>
            """,
            },
        )

        return await self.send_email(
            to_emails=to_emails,
            subject=f"Scan Complete: {safe_file_name} - {compliance_score:.0f}% Compliance",
            html_content=html,
        )

    async def send_critical_issues(
        self,
        to_emails: List[str],
        file_name: str,
        critical_issues: List[Dict[str, Any]],
        scan_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send notification for critical accessibility issues."""
        safe_file_name = html_lib.escape(file_name)
        issues_html = ""
        for issue in critical_issues[:5]:  # Show top 5
            safe_type = html_lib.escape(str(issue.get("type", "Unknown")))
            safe_desc = html_lib.escape(str(issue.get("description", "")))
            issues_html += f"""
                <li style="margin-bottom: 8px;">
                    <strong>{safe_type}</strong>: {safe_desc}
                </li>
            """

        html = self.render_template(
            "critical_issues",
            {
                "title": "Critical Accessibility Issues Detected",
                "file_name": safe_file_name,
                "issues_count": len(critical_issues),
                "issues_html": issues_html,
                "action_url": scan_url or "",
                "action_text": "View & Fix Issues",
                "message": f"""
                <p style="color: #ef4444;">
                    <strong>Urgent:</strong> {len(critical_issues)} critical accessibility issues were found
                    in <strong>{safe_file_name}</strong>.
                </p>
                <p>These issues may prevent users with disabilities from accessing the content.</p>
                <ul style="color: #333; padding-left: 20px;">
                    {issues_html}
                </ul>
                {f'<p style="color: #666;">... and {len(critical_issues) - 5} more issues</p>' if len(critical_issues) > 5 else ''}
            """,
            },
        )

        return await self.send_email(
            to_emails=to_emails,
            subject=f"CRITICAL: {len(critical_issues)} Accessibility Issues in {safe_file_name}",
            html_content=html,
        )

    async def send_weekly_summary(
        self,
        to_emails: List[str],
        department_name: str,
        total_files: int,
        files_scanned: int,
        average_score: float,
        total_issues: int,
        issues_fixed: int,
        dashboard_url: str = None,
        unsubscribe_url: str = None,
    ) -> Dict[str, Any]:
        """
        Send weekly compliance summary.

        This is a MARKETING email - users must be able to unsubscribe.
        Always pass unsubscribe_url for CAN-SPAM/GDPR compliance.
        """
        html = self.render_template(
            "weekly_summary",
            {
                "title": "Weekly Accessibility Summary",
                "department_name": department_name,
                "total_files": total_files,
                "files_scanned": files_scanned,
                "average_score": f"{average_score:.0f}%",
                "total_issues": total_issues,
                "issues_fixed": issues_fixed,
                "action_url": dashboard_url or "",
                "action_text": "View Dashboard",
                "unsubscribe_url": unsubscribe_url or "",
                "message": f"""
                <p>Here's your weekly accessibility summary for <strong>{department_name}</strong>.</p>

                <div style="background: #f5f7fa; padding: 20px; border-radius: 8px; margin: 16px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0;"><strong>Files Tracked:</strong></td>
                            <td style="text-align: right;">{total_files}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0;"><strong>Files Scanned This Week:</strong></td>
                            <td style="text-align: right;">{files_scanned}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0;"><strong>Average Compliance Score:</strong></td>
                            <td style="text-align: right; color: {'#22c55e' if average_score >= 90 else '#f59e0b' if average_score >= 70 else '#ef4444'};">
                                {average_score:.0f}%
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0;"><strong>Total Issues Found:</strong></td>
                            <td style="text-align: right;">{total_issues}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0;"><strong>Issues Fixed:</strong></td>
                            <td style="text-align: right; color: #22c55e;">{issues_fixed}</td>
                        </tr>
                    </table>
                </div>

                <p style="color: #666; font-size: 14px;">
                    Remember: The WCAG 2.1 compliance deadline is April 26, 2027.
                </p>
            """,
            },
        )

        return await self.send_email(
            to_emails=to_emails,
            subject=f"Weekly Accessibility Summary - {department_name}",
            html_content=html,
        )

    async def send_remediation_partial_success(
        self,
        to_emails: List[str],
        file_name: str,
        fixed_count: int,
        failed_count: int,
        manual_count: int = 0,
        fixed_issues: Optional[List[Dict[str, Any]]] = None,
        failed_issues: Optional[List[Dict[str, Any]]] = None,
        scan_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send notification when auto-remediation partially succeeds.

        Some issues were fixed automatically, some could not be.
        """
        # file_name goes through render_template, which HTML-escapes all
        # non-_html/_url variables — pre-escaping here double-escaped it.
        fixed_issues_html = (
            '<p style="color: #666;">No issues were automatically fixed.</p>'
        )
        if fixed_issues:
            items = ""
            for issue in fixed_issues[:10]:
                safe_desc = html_lib.escape(str(issue.get("description", "Issue")))
                items += (
                    f'<li style="margin-bottom: 8px; color: #166534;">{safe_desc}</li>'
                )
            if len(fixed_issues) > 10:
                items += (
                    f'<li style="color: #666;">... and '
                    f"{len(fixed_issues) - 10} more</li>"
                )
            fixed_issues_html = (
                f'<ul style="padding-left: 20px; margin: 0;">{items}</ul>'
            )

        failed_issues_html = '<p style="color: #666;">No details available.</p>'
        if failed_issues:
            items = ""
            for issue in failed_issues[:10]:
                safe_desc = html_lib.escape(str(issue.get("description", "Issue")))
                safe_error = html_lib.escape(str(issue.get("error", "")))
                suffix = f" &mdash; {safe_error}" if safe_error else ""
                items += (
                    f'<li style="margin-bottom: 8px; color: #991b1b;">'
                    f"{safe_desc}{suffix}</li>"
                )
            if len(failed_issues) > 10:
                items += (
                    f'<li style="color: #666;">... and '
                    f"{len(failed_issues) - 10} more</li>"
                )
            failed_issues_html = (
                f'<ul style="padding-left: 20px; margin: 0;">{items}</ul>'
            )

        html = self.render_template(
            "remediation_partial_success",
            {
                "title": "Remediation Partially Complete",
                "file_name": file_name,
                "fixed_count": fixed_count,
                "failed_count": failed_count,
                "manual_count": manual_count,
                "fixed_issues_html": fixed_issues_html,
                "failed_issues_html": failed_issues_html,
                "action_url": scan_url or "",
                "action_text": "View Results",
            },
        )

        return await self.send_email(
            to_emails=to_emails,
            subject=(
                f"Remediation Partially Complete: {file_name} "
                f"({fixed_count} fixed, {failed_count} need attention)"
            ),
            html_content=html,
        )

    async def send_remediation_failure(
        self,
        to_emails: List[str],
        file_name: str,
        error_message: str,
        scan_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send notification when auto-remediation fails completely."""
        # render_template HTML-escapes all non-_html/_url variables;
        # pre-escaping here double-escaped them. Subjects are plain text.
        html = self.render_template(
            "remediation_failure",
            {
                "title": "Remediation Failed",
                "file_name": file_name,
                "error_message": (
                    error_message or "An unknown error occurred during remediation."
                ),
                "action_url": scan_url or "",
                "action_text": "View Scan",
            },
        )

        return await self.send_email(
            to_emails=to_emails,
            subject=f"Remediation Failed: {file_name}",
            html_content=html,
        )

    async def send_magic_link(
        self,
        to_email: str,
        magic_link_url: str,
        expires_minutes: int = 15,
    ) -> Dict[str, Any]:
        """
        Send magic link login email.

        Args:
            to_email: User's email address
            magic_link_url: The full magic link URL
            expires_minutes: Minutes until link expires

        Returns:
            Dict with success status
        """
        # Validate URL scheme to prevent javascript: injection
        from urllib.parse import urlparse

        parsed = urlparse(magic_link_url)
        if parsed.scheme not in ("https", "http"):
            raise ValueError(f"Invalid magic_link_url scheme: {parsed.scheme}")

        content = f"""
            <h2 style="color: #1a1a2e; margin: 0 0 16px 0;">Login to Your Account</h2>

            <p>
                Click the button below to securely log in to your Aelira account.
                This link will expire in {expires_minutes} minutes.
            </p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="{magic_link_url}"
                   style="background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: white; padding: 16px 32px;
                          text-decoration: none; border-radius: 8px; display: inline-block;
                          font-weight: 600; font-size: 16px;">
                    Log In to Aelira
                </a>
            </div>

            <p style="color: #666; font-size: 14px;">
                If the button doesn't work, you can copy and paste this link into your browser:
            </p>
            <p style="background: #f3f4f6; padding: 12px; border-radius: 6px; word-break: break-all;
                      font-size: 12px; color: #333;">
                {html_lib.escape(magic_link_url)}
            </p>

            <div style="margin-top: 24px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                <p style="color: #6b7280; font-size: 12px; margin: 0;">
                    <strong>Didn't request this email?</strong> You can safely ignore it.
                    Someone may have typed your email address by mistake.
                </p>
                <p style="color: #6b7280; font-size: 12px; margin: 12px 0 0 0;">
                    This link can only be used once and expires in {expires_minutes} minutes.
                </p>
            </div>
        """
        html = get_email_wrapper(content)

        return await self.send_email(
            to_emails=[to_email],
            subject="Log in to Aelira",
            html_content=html,
            text_content=f"Log in to Aelira\n\nClick this link to log in: {magic_link_url}\n\nThis link expires in {expires_minutes} minutes.",
        )

    async def send_welcome_magic_link(
        self,
        to_email: str,
        name: str,
        tier: str = "individual",
    ) -> Dict[str, Any]:
        """
        Send welcome email to new user who signed up via magic link.

        This does not include or create an API key because magic link users
        get session-based auth. They can explicitly create an API key in
        Settings if needed for programmatic access.

        Args:
            to_email: User's email address
            name: User's name
            tier: The user's tier

        Returns:
            Dict with success status
        """
        # Everything in Aelira Core is free and unlimited by default; the
        # welcome email states that rather than a plan name.
        tier_info = {
            "display_name": "Individual",
            "scans_limit": "Unlimited",
            "pages_limit": "Unlimited",
        }

        content = f"""
            <h2 style="color: #1a1a2e; margin: 0 0 16px 0;">Welcome, {html_lib.escape(name)}!</h2>

            <p>
                Your Aelira account is now active. You're all set to start making your
                educational content accessible.
            </p>

            <div style="background: #f0fdf4; border: 1px solid #22c55e; border-radius: 8px; padding: 16px; margin: 24px 0;">
                <p style="color: #166534; margin: 0; font-weight: 600;">
                    Your Plan: {tier_info['display_name']}
                </p>
                <ul style="color: #166534; margin: 8px 0 0 0; padding-left: 20px;">
                    <li>{tier_info['scans_limit']} document scans per month</li>
                    <li>Up to {tier_info['pages_limit']} pages per document</li>
                    <li>PDF, Word, PowerPoint, Excel, Images</li>
                    <li>AI-powered alt text generation</li>
                    <li>WCAG 2.1 AA compliance reports</li>
                </ul>
            </div>

            <h3 style="color: #1a1a2e; margin-top: 24px;">Get Started</h3>
            <ol style="line-height: 1.8; padding-left: 20px;">
                <li>Upload a document from your dashboard</li>
                <li>Review the accessibility scan results</li>
                <li>Use auto-remediation to fix issues automatically</li>
                <li>Download your compliant document</li>
            </ol>

            <div style="text-align: center; margin: 32px 0;">
                <a href="https://dashboard.example.com/dashboard"
                   style="background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: white; padding: 16px 32px;
                          text-decoration: none; border-radius: 8px; display: inline-block;
                          font-weight: 600; font-size: 16px;">
                    Go to Dashboard
                </a>
            </div>

            <div style="background: #fffbeb; border-radius: 8px; padding: 16px; margin-top: 24px;">
                <p style="color: #92400e; margin: 0; font-size: 14px;">
                    <strong>Deadline Reminder:</strong> The WCAG 2.1 compliance deadline
                    for educational institutions is April 26, 2027. Start scanning your
                    documents now to ensure compliance.
                </p>
            </div>
        """
        html = get_email_wrapper(content)

        return await self.send_email(
            to_emails=[to_email],
            subject=f"Welcome to Aelira, {name}!",
            html_content=html,
            text_content=f"Welcome to Aelira, {name}!\n\nYour account is now active. Visit https://dashboard.example.com to start scanning your documents for accessibility compliance.\n\nPlan: {tier_info['display_name']}\n- {tier_info['scans_limit']} document scans per month\n- Up to {tier_info['pages_limit']} pages per document\n\nThe WCAG 2.1 compliance deadline is April 26, 2027.\n\nNeed help? Contact support@example.com",
        )

    async def send_admin_notification(
        self,
        to_emails: List[str],
        subject: str,
        event_type: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send notification email to admins about significant events.

        Args:
            to_emails: List of admin email addresses
            subject: Email subject
            event_type: Type of event (e.g., "new_signup", "payment", etc.)
            details: Dictionary with event details to display

        Returns:
            Dict with success status
        """
        if not to_emails:
            return {"success": False, "error": "No admin emails configured"}

        # Build details table
        details_rows = ""
        for key, value in details.items():
            # Make keys more readable
            readable_key = html_lib.escape(key.replace("_", " ").title())
            safe_value = html_lib.escape(str(value))
            details_rows += f"""
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; color: #666;">
                        {readable_key}
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; color: #333; font-weight: 500;">
                        {safe_value}
                    </td>
                </tr>
            """

        content = f"""
            <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; margin-bottom: 24px; border-radius: 0 8px 8px 0;">
                <strong style="color: #1a1a2e;">{event_type.replace("_", " ").title()}</strong>
            </div>

            <table style="width: 100%; border-collapse: collapse; background: #f9fafb; border-radius: 8px;">
                {details_rows}
            </table>

            <p style="color: #6b7280; font-size: 12px; margin: 24px 0 0 0; text-align: center;">
                This is an automated notification from Aelira.
            </p>
        """
        html = get_email_wrapper(content)

        return await self.send_email(
            to_emails=to_emails,
            subject=f"[Aelira Admin] {subject}",
            html_content=html,
        )

    async def send_faculty_invitation(
        self,
        to_email: str,
        department_name: str,
        role: str,
        inviter_name: str,
        inviter_email: str,
        accept_url: str,
        expires_date: str,
    ) -> Dict[str, Any]:
        """
        Send faculty invitation email.

        Args:
            to_email: Invitee's email address
            department_name: Name of the department
            role: Role being assigned (faculty, admin)
            inviter_name: Name of the person who sent the invite
            inviter_email: Email of the inviter
            accept_url: URL to accept the invitation
            expires_date: Human-readable expiration date

        Returns:
            Dict with success status
        """
        # Format role for display
        role_display = role.replace("_", " ").title()
        if role_display.lower() == "faculty":
            role_display = "Faculty Member"
        elif role_display.lower() == "admin":
            role_display = "Department Admin"

        html = self.render_template(
            "faculty_invitation",
            {
                "department_name": department_name,
                "role": role_display,
                "inviter_name": inviter_name,
                "inviter_email": inviter_email,
                "accept_url": accept_url,
                "expires_date": expires_date,
            },
        )

        return await self.send_email(
            to_emails=[to_email],
            subject=f"You're invited to join {department_name} on Aelira",
            html_content=html,
            text_content=f"""You've been invited to join {department_name} on Aelira!

{inviter_name} has invited you to join as a {role_display}.

Accept your invitation: {accept_url}

This invitation expires on {expires_date}.

Aelira is an accessibility compliance platform that helps you make your course materials WCAG 2.1 compliant before the April 26, 2027 deadline.

If you weren't expecting this invitation, you can safely ignore this email.

Questions? Contact support@example.com""",
        )


# Singleton instance
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get or create the global email service instance."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
