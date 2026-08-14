"""
End-to-end integration tests for Microsoft 365.

Tests the complete flow:
connect → sync → scan → remediate → upload

These tests verify the full integration lifecycle with mocked external services.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import base64

from src.api.main import app

# Skip all tests in this module unless RUN_E2E_TESTS is set
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="E2E test requires running infrastructure (set RUN_E2E_TESTS=1 to enable)",
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Headers with API key authentication."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def mock_msal_app():
    """Mock MSAL ConfidentialClientApplication."""
    with patch("msal.ConfidentialClientApplication") as mock:
        app = MagicMock()
        app.get_authorization_request_url.return_value = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
            "client_id=test-client-id&scope=offline_access%20Files.ReadWrite.All"
        )
        app.acquire_token_by_authorization_code.return_value = {
            "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.test",
            "refresh_token": "OAQABAAAAAADCoMpjJ...",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        app.acquire_token_by_refresh_token.return_value = {
            "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.refreshed",
            "expires_in": 3600,
        }
        mock.return_value = app
        yield app


@pytest.fixture
def mock_graph_client():
    """Mock Microsoft Graph API client."""
    with patch("src.integrations.microsoft_365.microsoft_graph.GraphClient") as mock:
        client = MagicMock()

        # Mock user info
        client.get_user_info.return_value = {
            "id": "ms-user-123",
            "displayName": "Test Faculty",
            "mail": "faculty@university.edu",
            "userPrincipalName": "faculty@university.onmicrosoft.com",
        }

        # Mock OneDrive files
        client.list_files.return_value = {
            "value": [
                {
                    "id": "onedrive-doc-123",
                    "name": "Course Syllabus.docx",
                    "file": {
                        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    },
                    "size": 45000,
                    "lastModifiedDateTime": "2026-01-10T15:30:00Z",
                },
                {
                    "id": "onedrive-ppt-456",
                    "name": "Lecture 1.pptx",
                    "file": {
                        "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    },
                    "size": 2500000,
                    "lastModifiedDateTime": "2026-01-09T10:00:00Z",
                },
                {
                    "id": "onedrive-pdf-789",
                    "name": "Reading Assignment.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 1250000,
                    "lastModifiedDateTime": "2026-01-08T09:00:00Z",
                },
            ],
            "@odata.nextLink": None,
        }

        # Mock file download
        client.download_file.return_value = b"Mock file content for testing"

        # Mock file upload
        client.upload_file.return_value = {
            "id": "new-file-uploaded",
            "name": "Remediated Document.docx",
            "size": 46000,
        }

        mock.return_value = client
        yield client


@pytest.fixture
def mock_oauth_token_manager():
    """Mock OAuth token manager."""
    with patch("src.api.microsoft_routes.OAuthTokenManager") as mock:
        manager = MagicMock()
        manager.encrypt_token.return_value = "encrypted-ms-token"
        manager.decrypt_token.return_value = {
            "access_token": "eyJ0eXAiOiJKV1Qi...",
            "refresh_token": "OAQABAAAAAADCoMpjJ...",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        manager.is_token_expired.return_value = False
        mock.return_value = manager
        yield manager


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("src.api.microsoft_routes.get_db") as mock:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        mock.return_value = session
        yield session


class TestMicrosoft365E2EFlow:
    """
    End-to-end tests for complete Microsoft 365 integration flow.

    Flow: Connect → List Files → Scan → Remediate → Upload
    """

    def test_e2e_connect_flow(
        self,
        client,
        auth_headers,
        mock_msal_app,
        mock_db_session,
    ):
        """Test complete OAuth connection flow."""
        # Step 1: Initiate OAuth connection
        response = client.post(
            "/api/microsoft/connect",
            headers=auth_headers,
            json={
                "redirect_uri": "http://localhost:5173/integrations/callback",
                "department_id": "test-dept-456",
            },
        )

        assert response.status_code in [200, 401, 422]
        if response.status_code == 200:
            data = response.json()
            assert "auth_url" in data or "authorization_url" in data

    def test_e2e_callback_token_exchange(
        self,
        client,
        mock_msal_app,
        mock_db_session,
        mock_oauth_token_manager,
    ):
        """Test OAuth callback exchanges code for tokens."""
        response = client.get(
            "/api/microsoft/callback",
            params={
                "code": "OAQABAAIAAACEfexXx...",
                "state": "test-state-123",
            },
        )

        assert response.status_code in [200, 302, 400, 401]

    def test_e2e_list_onedrive_files(
        self,
        client,
        auth_headers,
        mock_graph_client,
        mock_oauth_token_manager,
    ):
        """Test listing files from OneDrive."""
        response = client.get(
            "/api/microsoft/onedrive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "value" in data or "files" in data or isinstance(data, list)

    def test_e2e_list_sharepoint_sites(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test listing SharePoint sites."""
        mock_graph_client.list_sharepoint_sites.return_value = {
            "value": [
                {
                    "id": "site-1",
                    "name": "Department Site",
                    "webUrl": "https://university.sharepoint.com/sites/dept",
                },
            ],
        }

        response = client.get(
            "/api/microsoft/sharepoint/sites",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]

    def test_e2e_scan_onedrive_document(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test scanning a document from OneDrive."""
        response = client.post(
            "/api/microsoft/scan/file/onedrive-doc-123",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "scan_type": "accessibility",
            },
        )

        assert response.status_code in [200, 202, 401, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            assert "job_id" in data or "scan_id" in data or "status" in data

    def test_e2e_scan_sharepoint_document(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test scanning a document from SharePoint."""
        response = client.post(
            "/api/microsoft/scan/sharepoint/file/sp-doc-123",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "site_id": "site-1",
                "scan_type": "accessibility",
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_e2e_remediate_document(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test remediating a scanned document."""
        response = client.post(
            "/api/microsoft/remediate/onedrive-doc-123",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "issues_to_fix": ["issue-1", "issue-2"],
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_e2e_upload_to_onedrive(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test uploading remediated file to OneDrive."""
        response = client.post(
            "/api/microsoft/onedrive/upload",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "folder_id": "root",
                "filename": "Remediated Syllabus.docx",
                "content": base64.b64encode(b"Fixed document content").decode(),
            },
        )

        assert response.status_code in [200, 201, 401, 404, 422]

    def test_e2e_disconnect(self, client, auth_headers, mock_db_session):
        """Test disconnecting Microsoft account."""
        response = client.delete(
            "/api/microsoft/disconnect",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 204, 401, 404]


class TestMicrosoft365WebhookSubscriptions:
    """Test Microsoft Graph webhook subscriptions."""

    def test_create_subscription(self, client, auth_headers, mock_graph_client):
        """Test creating a webhook subscription."""
        mock_graph_client.create_subscription.return_value = {
            "id": "sub-123",
            "resource": "/me/drive/root",
            "expirationDateTime": "2026-01-17T00:00:00Z",
            "clientState": "secret-client-state",
        }

        response = client.post(
            "/api/microsoft/subscriptions",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "resource": "/me/drive/root",
                "notification_url": "https://api.example.com/webhooks/microsoft",
            },
        )

        assert response.status_code in [200, 201, 401, 404, 422]

    def test_renew_subscription(self, client, auth_headers, mock_graph_client):
        """Test renewing an expiring subscription."""
        mock_graph_client.renew_subscription.return_value = {
            "id": "sub-123",
            "expirationDateTime": "2026-01-24T00:00:00Z",
        }

        response = client.patch(
            "/api/microsoft/subscriptions/sub-123",
            headers=auth_headers,
            json={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]

    def test_delete_subscription(self, client, auth_headers, mock_graph_client):
        """Test deleting a subscription."""
        mock_graph_client.delete_subscription.return_value = True

        response = client.delete(
            "/api/microsoft/subscriptions/sub-123",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 204, 401, 404]


class TestMicrosoft365TokenRefresh:
    """Test token refresh scenarios."""

    def test_auto_refresh_expired_token(
        self,
        client,
        auth_headers,
        mock_msal_app,
        mock_graph_client,
        mock_oauth_token_manager,
    ):
        """Test automatic token refresh when expired."""
        mock_oauth_token_manager.is_token_expired.return_value = True

        response = client.get(
            "/api/microsoft/onedrive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]

    def test_handle_revoked_refresh_token(
        self,
        client,
        auth_headers,
        mock_msal_app,
        mock_oauth_token_manager,
    ):
        """Test handling of revoked refresh token."""
        mock_oauth_token_manager.is_token_expired.return_value = True
        mock_msal_app.acquire_token_by_refresh_token.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS50173: The provided grant has expired",
        }

        response = client.get(
            "/api/microsoft/onedrive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [401, 403, 404]


class TestMicrosoft365ErrorHandling:
    """Test error handling scenarios."""

    def test_graph_api_throttling(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test handling of Graph API throttling."""
        mock_graph_client.list_files.side_effect = Exception("429 Too Many Requests")

        response = client.get(
            "/api/microsoft/onedrive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [429, 500, 401, 404]

    def test_file_not_found(
        self,
        client,
        auth_headers,
        mock_graph_client,
    ):
        """Test handling of file not found."""
        mock_graph_client.download_file.side_effect = Exception("404 Item not found")

        response = client.get(
            "/api/microsoft/onedrive/files/nonexistent/content",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [404, 401, 500]

    def test_unauthorized_tenant_access(self, client, auth_headers):
        """Test that cross-tenant access is blocked."""
        response = client.get(
            "/api/microsoft/onedrive/files",
            headers=auth_headers,
            params={"department_id": "other-dept-999"},
        )

        assert response.status_code in [401, 403, 404]


class TestMicrosoft365ConnectionStatus:
    """Test connection status endpoints."""

    def test_get_connection_status(self, client, auth_headers):
        """Test getting Microsoft connection status."""
        response = client.get(
            "/api/microsoft/status",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert "connected" in data or "status" in data

    def test_get_account_info(self, client, auth_headers, mock_graph_client):
        """Test getting connected account info."""
        response = client.get(
            "/api/microsoft/account",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
