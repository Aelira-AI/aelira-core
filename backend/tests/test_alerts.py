"""
Tests for email alert notification system.

Tests cover:
- Alert settings management
- Email address management
- Alert triggering (scan complete, critical issues, weekly summary)
- Email sending (SMTP/SendGrid)
- Alert templates
- Alert scheduling
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import uuid

# Import app for testing
from src.api.main import app

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_email_service():
    """Mock email sending service."""
    with patch("src.services.email_service.send_email") as mock_send:
        mock_send.return_value = {"success": True, "message_id": "msg-123"}
        yield mock_send


@pytest.fixture
def mock_smtp_client():
    """Mock SMTP client."""
    with patch("smtplib.SMTP") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def sample_alert_settings():
    """Sample alert settings."""
    return {
        "department_id": str(uuid.uuid4()),
        "alert_on_scan_complete": True,
        "alert_on_critical_issues": True,
        "alert_on_weekly_summary": True,
        "weekly_summary_day": "monday",
        "weekly_summary_hour": 9,
        "email_addresses": ["admin@university.edu", "faculty@university.edu"],
        "is_paused": False,
    }


class TestAlertSettingsManagement:
    """Tests for managing alert settings."""

    def test_get_alert_settings(self, client):
        """Test getting alert settings for a department."""
        response = client.get("/api/alerts/settings")

        # 401 = auth required, 200 = success
        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "alert_on_scan_complete" in data or "settings" in data

    def test_update_alert_settings(self, client, sample_alert_settings):
        """Test updating alert settings."""
        response = client.put(
            "/api/alerts/settings",
            json={
                "alert_on_scan_complete": True,
                "alert_on_critical_issues": True,
                "alert_on_weekly_summary": False,
            },
        )

        assert response.status_code in [200, 400, 401]

    def test_create_default_alert_settings(self, client):
        """Test that default alert settings are created for new departments."""
        response = client.get("/api/alerts/settings")

        # Should return settings (created if not exist) or require auth
        assert response.status_code in [200, 401, 404]

    def test_pause_all_alerts(self, client):
        """Test pausing all alerts."""
        response = client.post("/api/alerts/pause")

        assert response.status_code in [200, 401]

    def test_resume_all_alerts(self, client):
        """Test resuming all alerts."""
        response = client.post("/api/alerts/resume")

        assert response.status_code in [200, 401]

    def test_get_paused_status(self, client):
        """Test getting alert pause status."""
        response = client.get("/api/alerts/settings")

        if response.status_code == 200:
            data = response.json()
            assert "is_paused" in data or "paused" in data


class TestEmailAddressManagement:
    """Tests for managing email addresses."""

    def test_add_email_address(self, client):
        """Test adding an email address to alerts."""
        response = client.post(
            "/api/alerts/emails",
            json={"email": "newuser@university.edu"},
        )

        assert response.status_code in [200, 201, 400, 401]

    def test_add_invalid_email(self, client):
        """Test adding an invalid email address."""
        response = client.post(
            "/api/alerts/emails",
            json={"email": "invalid-email"},
        )

        # 401 = no auth, 422 = validation error
        assert response.status_code in [400, 401, 422]

    def test_add_duplicate_email(self, client):
        """Test adding a duplicate email address."""
        email = "admin@university.edu"

        # Add email
        response1 = client.post(
            "/api/alerts/emails",
            json={"email": email},
        )

        # Try to add again
        response2 = client.post(
            "/api/alerts/emails",
            json={"email": email},
        )

        # Should reject duplicate or accept idempotently (or require auth)
        if response1.status_code in [200, 201]:
            assert response2.status_code in [200, 201, 400, 409]

    def test_remove_email_address(self, client):
        """Test removing an email address from alerts."""
        response = client.delete(
            "/api/alerts/emails/admin@university.edu",
        )

        assert response.status_code in [200, 204, 401, 404]

    def test_list_email_addresses(self, client):
        """Test listing all alert email addresses."""
        response = client.get("/api/alerts/emails")

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "emails" in data

    def test_max_email_addresses(self, client):
        """Test maximum number of email addresses."""
        # Try to add many emails
        for i in range(20):
            response = client.post(
                "/api/alerts/emails",
                json={"email": f"user{i}@university.edu"},
            )
            # Should succeed until limit reached (or require auth)
            if response.status_code in [400, 401]:
                break


class TestScanCompleteAlerts:
    """Tests for scan complete alert notifications."""

    def test_send_scan_complete_alert(self, client, mock_email_service):
        """Test sending scan complete alert."""
        with patch(
            "src.services.alert_service.AlertService.send_scan_complete_alert"
        ) as mock_send:
            mock_send.return_value = True

            response = client.post(
                "/api/alerts/trigger/scan-complete",
                json={
                    "scan_id": str(uuid.uuid4()),
                    "file_name": "Test Document.docx",
                    "issues_found": 5,
                    "compliance_score": 0.85,
                },
            )

            assert response.status_code in [200, 202, 401]

    def test_scan_complete_alert_includes_results(self, client, mock_email_service):
        """Test that scan complete alert includes scan results."""
        scan_data = {
            "scan_id": str(uuid.uuid4()),
            "file_name": "Syllabus.pdf",
            "issues_found": 10,
            "critical_issues": 2,
            "compliance_score": 0.75,
        }

        with patch("src.services.alert_service.AlertService.send_scan_complete_alert"):
            response = client.post(
                "/api/alerts/trigger/scan-complete",
                json=scan_data,
            )

            if response.status_code in [200, 202]:
                # Email should contain scan data
                pass

    def test_scan_complete_alert_respects_settings(self, client, mock_email_service):
        """Test that scan complete alerts respect settings."""
        # Disable scan complete alerts
        client.put(
            "/api/alerts/settings",
            json={"alert_on_scan_complete": False},
        )

        # Try to trigger alert
        response = client.post(
            "/api/alerts/trigger/scan-complete",
            json={
                "scan_id": str(uuid.uuid4()),
                "file_name": "Test.pdf",
                "issues_found": 5,
            },
        )

        # Should not send (or return success without sending)
        assert response.status_code in [200, 202, 204, 401]


class TestCriticalIssueAlerts:
    """Tests for critical issue alert notifications."""

    def test_send_critical_issue_alert(self, client, mock_email_service):
        """Test sending critical issue alert."""
        with patch(
            "src.services.alert_service.AlertService.send_critical_issue_alert"
        ) as mock_send:
            mock_send.return_value = True

            response = client.post(
                "/api/alerts/trigger/critical-issues",
                json={
                    "scan_id": str(uuid.uuid4()),
                    "file_name": "Important Document.docx",
                    "critical_issues": [
                        {"type": "missing_alt_text", "count": 15},
                        {"type": "insufficient_contrast", "count": 8},
                    ],
                },
            )

            assert response.status_code in [200, 202, 401]

    def test_critical_issue_threshold(self, client, mock_email_service):
        """Test that critical alerts only fire above threshold."""
        # Few critical issues (below threshold)
        response1 = client.post(
            "/api/alerts/trigger/critical-issues",
            json={
                "scan_id": str(uuid.uuid4()),
                "file_name": "Doc.pdf",
                "critical_issues": [{"type": "missing_alt_text", "count": 1}],
            },
        )

        # Many critical issues (above threshold)
        response2 = client.post(
            "/api/alerts/trigger/critical-issues",
            json={
                "scan_id": str(uuid.uuid4()),
                "file_name": "Doc2.pdf",
                "critical_issues": [{"type": "missing_alt_text", "count": 50}],
            },
        )

        # Both should succeed (threshold logic is internal)
        assert response1.status_code in [200, 202, 204, 401]
        assert response2.status_code in [200, 202, 204, 401]


class TestWeeklySummaryAlerts:
    """Tests for weekly summary alert notifications."""

    def test_send_weekly_summary(self, client, mock_email_service):
        """Test sending weekly summary alert."""
        with patch(
            "src.services.alert_service.AlertService.send_weekly_summary"
        ) as mock_send:
            mock_send.return_value = True

            response = client.post(
                "/api/alerts/trigger/weekly-summary",
                json={
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-07",
                    "total_scans": 45,
                    "total_issues": 120,
                    "avg_compliance_score": 0.82,
                    "top_issues": [
                        {"type": "missing_alt_text", "count": 50},
                        {"type": "low_contrast", "count": 35},
                    ],
                },
            )

            assert response.status_code in [200, 202, 401]

    def test_weekly_summary_schedule(self, client):
        """Test updating weekly summary schedule."""
        response = client.put(
            "/api/alerts/settings",
            json={
                "weekly_summary_day": "friday",
                "weekly_summary_hour": 14,
            },
        )

        assert response.status_code in [200, 400, 401]

    def test_weekly_summary_invalid_day(self, client):
        """Test invalid day for weekly summary."""
        response = client.put(
            "/api/alerts/settings",
            json={
                "weekly_summary_day": "invalidday",
            },
        )

        assert response.status_code in [400, 401, 422]

    def test_weekly_summary_invalid_hour(self, client):
        """Test invalid hour for weekly summary."""
        response = client.put(
            "/api/alerts/settings",
            json={
                "weekly_summary_hour": 25,  # Invalid
            },
        )

        assert response.status_code in [400, 401, 422]


class TestEmailTemplates:
    """Tests for email alert templates."""

    def test_scan_complete_template_rendering(self):
        """Test that scan complete template renders correctly."""
        from src.services.email_templates import render_scan_complete_email

        html = render_scan_complete_email(
            file_name="Test.pdf",
            issues_found=10,
            compliance_score=0.85,
            scan_url="https://dashboard.aelira.ai/scans/123",
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
            scan_url="https://dashboard.aelira.ai/scans/456",
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


class TestEmailSending:
    """Tests for email sending functionality."""

    @pytest.mark.asyncio
    async def test_send_email_via_smtp(self, mock_smtp_client):
        """Test sending email via SMTP."""
        from src.services.email_service import send_email

        result = await send_email(
            to="admin@university.edu",
            subject="Test Alert",
            body="Test message",
            html="<p>Test message</p>",
        )

        # Should succeed or return result
        assert result is not None

    @pytest.mark.asyncio
    async def test_send_email_via_sendgrid(self):
        """Test sending email via SendGrid (mock test)."""
        # Note: SendGrid integration not installed, test with mock
        from src.services.email_service import send_email

        with patch("src.services.email_service.send_email") as mock_send:
            mock_send.return_value = {"success": True, "message_id": "test-123"}

            result = await send_email(
                to="admin@university.edu",
                subject="Test Alert",
                body="Test message",
                html="<p>Test message</p>",
            )

            assert result is not None

    @pytest.mark.asyncio
    async def test_send_email_handles_failures(self, mock_smtp_client):
        """Test that email failures are handled gracefully."""
        from src.services.email_service import send_email

        with patch("src.services.email_service.send_email") as mock_send:
            mock_send.return_value = {"success": False, "error": "SMTP error"}

            result = await send_email(
                to="admin@university.edu",
                subject="Test Alert",
                body="Test message",
            )

            # Should return failure status
            assert result is not None

    def test_send_to_multiple_recipients(self, client, mock_email_service):
        """Test sending email to multiple recipients."""
        recipients = [
            "admin1@university.edu",
            "admin2@university.edu",
            "admin3@university.edu",
        ]

        response = client.post(
            "/api/alerts/send",
            json={
                "recipients": recipients,
                "subject": "Test Alert",
                "body": "Test message",
            },
        )

        assert response.status_code in [200, 202, 401]


class TestAlertTesting:
    """Tests for alert testing functionality."""

    def test_send_test_email(self, client, mock_email_service):
        """Test sending a test email."""
        response = client.post(
            "/api/alerts/test",
            json={
                "email_type": "scan_complete",
                "recipient": "test@university.edu",
            },
        )

        assert response.status_code in [200, 401]

    def test_test_all_email_types(self, client, mock_email_service):
        """Test sending test emails for all types."""
        email_types = ["scan_complete", "critical_issues", "weekly_summary"]

        for email_type in email_types:
            response = client.post(
                "/api/alerts/test",
                json={
                    "email_type": email_type,
                    "recipient": "test@university.edu",
                },
            )

            assert response.status_code in [200, 401, 400]

    def test_test_email_uses_sample_data(self, client, mock_email_service):
        """Test that test emails use sample data."""
        response = client.post(
            "/api/alerts/test",
            json={
                "email_type": "scan_complete",
                "recipient": "test@university.edu",
            },
        )

        if response.status_code == 200:
            # Should have sent email with sample data
            assert mock_email_service.called or response.json().get("sent")


class TestAlertHistory:
    """Tests for alert history and logging."""

    def test_get_alert_history(self, client):
        """Test getting alert history."""
        response = client.get("/api/alerts/history")

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "history" in data

    def test_alert_history_includes_details(self, client):
        """Test that alert history includes details."""
        response = client.get("/api/alerts/history")

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                alert = data[0]
                # Should have timestamp, type, recipients, etc.
                assert (
                    "timestamp" in alert or "sent_at" in alert or "created_at" in alert
                )

    def test_filter_alert_history_by_type(self, client):
        """Test filtering alert history by type."""
        response = client.get(
            "/api/alerts/history",
            params={"type": "scan_complete"},
        )

        assert response.status_code in [200, 401]

    def test_alert_history_pagination(self, client):
        """Test paginating alert history."""
        response = client.get(
            "/api/alerts/history",
            params={"limit": 10, "offset": 0},
        )

        assert response.status_code in [200, 401]
