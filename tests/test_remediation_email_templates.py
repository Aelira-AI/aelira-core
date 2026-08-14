"""
Tests for the partial-success and failure remediation email templates (#31).

These tests render only -- no SMTP connection and no database access.
EmailService.render_template() and the two new EmailService convenience
methods (send_remediation_partial_success / send_remediation_failure) build
HTML in-process; sending is short-circuited by patching EmailService.send_email
so nothing ever touches a real mail server.
"""

from unittest.mock import AsyncMock

import pytest

from src.mailer.email_service import EmailService


@pytest.fixture
def email_service():
    """An EmailService instance. Never configured with real SMTP/SendGrid
    creds -- render_template() doesn't need them, and every test that calls
    a send_* method patches send_email() before it can reach the network."""
    return EmailService()


class TestPartialSuccessTemplateRendering:
    def test_template_file_exists_and_renders(self, email_service):
        html = email_service.render_template(
            "remediation_partial_success",
            {
                "title": "Remediation Partially Complete",
                "file_name": "syllabus.docx",
                "fixed_count": 5,
                "failed_count": 2,
                "manual_count": 1,
                "fixed_issues_html": "<ul><li>Added alt text</li></ul>",
                "failed_issues_html": "<ul><li>Could not fix table headers</li></ul>",
                "action_url": "https://dashboard.example.com/scans/123",
                "action_text": "View Results",
            },
        )

        assert "Remediation Partially Complete" in html
        assert "syllabus.docx" in html
        assert "Added alt text" in html
        assert "Could not fix table headers" in html
        assert "https://dashboard.example.com/scans/123" in html
        # Branded wrapper applied (matches existing templates' registration
        # mechanism -- get_email_wrapper via render_template).
        assert "Aelira" in html

    def test_unfilled_template_placeholders_do_not_leak_braces_for_provided_keys(
        self, email_service
    ):
        html = email_service.render_template(
            "remediation_partial_success",
            {
                "title": "T",
                "file_name": "f.docx",
                "fixed_count": 1,
                "failed_count": 1,
                "manual_count": 0,
                "fixed_issues_html": "<p>ok</p>",
                "failed_issues_html": "<p>ok</p>",
                "action_url": "https://example.com",
                "action_text": "Go",
            },
        )
        assert "{{title}}" not in html
        assert "{{file_name}}" not in html
        assert "{{fixed_issues_html}}" not in html
        assert "{{failed_issues_html}}" not in html


class TestFailureTemplateRendering:
    def test_template_file_exists_and_renders(self, email_service):
        html = email_service.render_template(
            "remediation_failure",
            {
                "title": "Remediation Failed",
                "file_name": "handout.pdf",
                "error_message": "The PDF could not be parsed.",
                "action_url": "https://dashboard.example.com/scans/456",
                "action_text": "View Scan",
            },
        )

        assert "Remediation Failed" in html
        assert "handout.pdf" in html
        assert "The PDF could not be parsed." in html
        assert "https://dashboard.example.com/scans/456" in html
        assert "Aelira" in html

    def test_unfilled_template_placeholders_do_not_leak_braces(self, email_service):
        html = email_service.render_template(
            "remediation_failure",
            {
                "title": "T",
                "file_name": "f.pdf",
                "error_message": "boom",
                "action_url": "https://example.com",
                "action_text": "Go",
            },
        )
        assert "{{error_message}}" not in html
        assert "{{file_name}}" not in html


class TestSendRemediationPartialSuccess:
    @pytest.mark.asyncio
    async def test_builds_html_and_subject_with_issue_lists(self, email_service):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_partial_success(
            to_emails=["faculty@university.edu"],
            file_name="lecture-notes.pptx",
            fixed_count=3,
            failed_count=1,
            manual_count=0,
            fixed_issues=[
                {"description": "Added alt text to slide 2 image"},
                {"description": "Fixed heading order"},
            ],
            failed_issues=[
                {"description": "Table on slide 5", "error": "Ambiguous headers"}
            ],
            scan_url="/scans/789",
        )

        email_service.send_email.assert_awaited_once()
        call_kwargs = email_service.send_email.call_args.kwargs
        assert "lecture-notes.pptx" in call_kwargs["subject"]
        assert "3 fixed" in call_kwargs["subject"]
        assert "1 need attention" in call_kwargs["subject"]
        assert "Added alt text to slide 2 image" in call_kwargs["html_content"]
        assert "Table on slide 5" in call_kwargs["html_content"]
        assert "Ambiguous headers" in call_kwargs["html_content"]

    @pytest.mark.asyncio
    async def test_handles_missing_issue_lists_gracefully(self, email_service):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_partial_success(
            to_emails=["faculty@university.edu"],
            file_name="doc.docx",
            fixed_count=2,
            failed_count=1,
            fixed_issues=None,
            failed_issues=None,
            scan_url=None,
        )

        email_service.send_email.assert_awaited_once()
        html_content = email_service.send_email.call_args.kwargs["html_content"]
        assert "No issues were automatically fixed" in html_content
        assert "No details available" in html_content

    @pytest.mark.asyncio
    async def test_escapes_html_in_file_name_and_issue_descriptions(
        self, email_service
    ):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_partial_success(
            to_emails=["faculty@university.edu"],
            file_name="<script>alert(1)</script>.docx",
            fixed_count=1,
            failed_count=1,
            fixed_issues=[{"description": "<img src=x onerror=alert(1)>"}],
            failed_issues=[{"description": "bad <b>markup</b>", "error": "n/a"}],
        )

        html_content = email_service.send_email.call_args.kwargs["html_content"]
        assert "<script>alert(1)</script>" not in html_content
        assert "<img src=x onerror=alert(1)>" not in html_content
        assert "&lt;script&gt;" in html_content


class TestSendRemediationFailure:
    @pytest.mark.asyncio
    async def test_builds_html_and_subject_with_error_message(self, email_service):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_failure(
            to_emails=["faculty@university.edu"],
            file_name="corrupted.pdf",
            error_message="Could not open the PDF: file is corrupted.",
            scan_url="/scans/999",
        )

        email_service.send_email.assert_awaited_once()
        call_kwargs = email_service.send_email.call_args.kwargs
        assert "corrupted.pdf" in call_kwargs["subject"]
        assert (
            "Could not open the PDF: file is corrupted." in call_kwargs["html_content"]
        )

    @pytest.mark.asyncio
    async def test_default_error_message_when_none_provided(self, email_service):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_failure(
            to_emails=["faculty@university.edu"],
            file_name="doc.docx",
            error_message="",
        )

        html_content = email_service.send_email.call_args.kwargs["html_content"]
        assert "unknown error" in html_content.lower()

    @pytest.mark.asyncio
    async def test_escapes_html_in_error_message(self, email_service):
        email_service.send_email = AsyncMock(return_value={"success": True})

        await email_service.send_remediation_failure(
            to_emails=["faculty@university.edu"],
            file_name="doc.docx",
            error_message="<script>alert(1)</script>",
        )

        html_content = email_service.send_email.call_args.kwargs["html_content"]
        assert "<script>alert(1)</script>" not in html_content
        assert "&lt;script&gt;" in html_content


class TestAlertServiceRemediationWiring:
    """Confirms AlertService wires to the new EmailService methods. No DB
    (db=None skips the department/preference checks) and no SMTP (the
    underlying EmailService call is mocked)."""

    @pytest.mark.asyncio
    async def test_partial_success_alert_calls_email_service(self):
        from src.services.alert_service import AlertService

        mock_email_service = AsyncMock()
        mock_email_service.send_remediation_partial_success = AsyncMock(
            return_value={"success": True}
        )
        alert_service = AlertService(email_service=mock_email_service)

        result = await alert_service.send_remediation_partial_success_alert(
            to_emails=["faculty@university.edu"],
            scan_id="scan-1",
            file_name="doc.docx",
            fixed_count=2,
            failed_count=1,
            db=None,
        )

        assert result is True
        mock_email_service.send_remediation_partial_success.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_alert_calls_email_service(self):
        from src.services.alert_service import AlertService

        mock_email_service = AsyncMock()
        mock_email_service.send_remediation_failure = AsyncMock(
            return_value={"success": True}
        )
        alert_service = AlertService(email_service=mock_email_service)

        result = await alert_service.send_remediation_failure_alert(
            to_emails=["faculty@university.edu"],
            scan_id="scan-2",
            file_name="doc.docx",
            error_message="Parsing failed",
            db=None,
        )

        assert result is True
        mock_email_service.send_remediation_failure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_success_alert_returns_false_on_send_failure(self):
        from src.services.alert_service import AlertService

        mock_email_service = AsyncMock()
        mock_email_service.send_remediation_partial_success = AsyncMock(
            return_value={"success": False, "error": "not configured"}
        )
        alert_service = AlertService(email_service=mock_email_service)

        result = await alert_service.send_remediation_partial_success_alert(
            to_emails=["faculty@university.edu"],
            scan_id="scan-3",
            file_name="doc.docx",
            fixed_count=1,
            failed_count=1,
            db=None,
        )

        assert result is False
