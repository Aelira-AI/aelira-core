"""
Email Service Re-export

Re-exports email service components from src.mailer.email_service
for backwards compatibility with tests that import from src.services.
"""

from src.mailer.email_service import EmailService

# Create a singleton instance for simple send_email calls
_email_service = EmailService()


async def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    html: str = None,
    from_email: str = None,
    from_name: str = None,
) -> dict:
    """
    Convenience function to send email.

    Args:
        to: Recipient email address(es)
        subject: Email subject
        body: Plain text body
        html: HTML body (optional)
        from_email: From email address (optional)
        from_name: From name (optional)

    Returns:
        dict with success status and message_id or error
    """
    if isinstance(to, str):
        to = [to]

    return await _email_service.send_email(
        to_emails=to,
        subject=subject,
        html_content=html or body,  # Use HTML if provided, else use body
        text_content=body,
    )


__all__ = ["EmailService", "send_email"]
