"""Template rendering and compatibility wrapper contracts; no live delivery."""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize(
    "recipients", ["faculty@example.edu", ["faculty@example.edu", "admin@example.edu"]]
)
@pytest.mark.parametrize("success", [True, False])
async def test_compatibility_send_preserves_transport_result(
    monkeypatch, recipients, success
):
    from src.services import email_service

    result = {"success": success}
    send = AsyncMock(return_value=result)
    monkeypatch.setattr(email_service._email_service, "send_email", send)
    assert (
        await email_service.send_email(
            to=recipients, subject="Lecture", body="Ready", html="<p>Ready</p>"
        )
        == result
    )
    send.assert_awaited_once_with(
        to_emails=[recipients] if isinstance(recipients, str) else recipients,
        subject="Lecture",
        html_content="<p>Ready</p>",
        text_content="Ready",
    )


class TestEmailTemplates:
    """Tests for email alert templates."""

    def test_scan_complete_template_rendering(self):
        """Test that scan complete template renders correctly."""
        from src.services.email_templates import render_scan_complete_email

        html = render_scan_complete_email(
            file_name="Test.pdf",
            issues_found=10,
            compliance_score=0.85,
            scan_url="https://dashboard.example.com/scans/123",
        )

        assert "Test.pdf" in html
        assert "10" in html or "85" in html

    def test_critical_issue_template_rendering(self):
        """Test that critical issue template renders correctly."""
        from src.services.email_templates import render_critical_issue_email

        html = render_critical_issue_email(
            file_name="Important.docx",
            critical_issues=[
                {"type": "missing_alt_text", "count": 15},
            ],
            scan_url="https://dashboard.example.com/scans/456",
        )

        assert "Important.docx" in html
        assert "alt" in html.lower() or "15" in html

    def test_weekly_summary_template_rendering(self):
        """Test that weekly summary template renders correctly."""
        from src.services.email_templates import render_weekly_summary_email

        html = render_weekly_summary_email(
            start_date="2025-01-01",
            end_date="2025-01-07",
            total_scans=45,
            total_issues=120,
            avg_compliance_score=0.82,
        )

        assert "45" in html or "120" in html
