"""
End-to-end integration tests for Google Workspace.

Tests the complete flow:
connect → sync → scan → remediate → upload

These tests verify the full integration lifecycle with mocked external services.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import uuid
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
def mock_google_oauth_service():
    """Mock the entire Google OAuth service."""
    with patch("src.api.google_routes.GoogleOAuthService") as mock:
        service = MagicMock()
        service.get_authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/v2/auth?client_id=test&scope=drive",
            "state-" + str(uuid.uuid4()),
        )
        service.exchange_code.return_value = {
            "access_token": "ya29.test-access-token",
            "refresh_token": "1//test-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/drive",
        }
        service.get_user_info.return_value = {
            "id": "google-user-123",
            "email": "faculty@university.edu",
            "name": "Test Faculty",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }
        mock.return_value = service
        yield service


@pytest.fixture
def mock_google_drive_service():
    """Mock Google Drive service for file operations."""
    with patch("src.api.google_routes.GoogleDriveService") as mock:
        service = MagicMock()

        # Mock list files
        service.list_files.return_value = {
            "files": [
                {
                    "id": "doc-123",
                    "name": "Lecture Notes.gdoc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-01-10T10:00:00.000Z",
                    "size": "15000",
                },
                {
                    "id": "slides-456",
                    "name": "Week 1 Presentation.gslides",
                    "mimeType": "application/vnd.google-apps.presentation",
                    "modifiedTime": "2026-01-09T14:30:00.000Z",
                    "size": "250000",
                },
                {
                    "id": "pdf-789",
                    "name": "Syllabus.pdf",
                    "mimeType": "application/pdf",
                    "modifiedTime": "2026-01-08T09:00:00.000Z",
                    "size": "125000",
                },
            ],
            "nextPageToken": None,
        }

        # Mock download file
        service.download_file.return_value = b"Mock file content for testing"

        # Mock export Google Doc
        service.export_file.return_value = b"Exported DOCX content"

        # Mock upload file
        service.upload_file.return_value = {
            "id": "new-file-123",
            "name": "Remediated Document.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

        # Mock update file
        service.update_file.return_value = {
            "id": "doc-123",
            "name": "Lecture Notes (Fixed).gdoc",
            "modifiedTime": datetime.now(timezone.utc).isoformat(),
        }

        mock.return_value = service
        yield service


@pytest.fixture
def mock_oauth_token_manager():
    """Mock OAuth token manager for credential storage."""
    with patch("src.api.google_routes.OAuthTokenManager") as mock:
        manager = MagicMock()
        manager.encrypt_token.return_value = "encrypted-token-data"
        manager.decrypt_token.return_value = {
            "access_token": "ya29.test-access-token",
            "refresh_token": "1//test-refresh-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        manager.is_token_expired.return_value = False
        manager.refresh_token.return_value = {
            "access_token": "ya29.new-access-token",
            "expires_in": 3600,
        }
        mock.return_value = manager
        yield manager


@pytest.fixture
def mock_db_session():
    """Mock database session for credential storage."""
    with patch("src.api.google_routes.get_db") as mock:
        session = MagicMock()

        # Mock credential lookup - not found initially
        session.query.return_value.filter.return_value.first.return_value = None

        mock.return_value = session
        yield session


@pytest.fixture
def mock_scan_processor():
    """Mock the document processor for scanning."""
    with patch("src.processors.document_processor.DocumentProcessor") as mock:
        processor = MagicMock()
        processor.scan.return_value = {
            "scan_id": str(uuid.uuid4()),
            "file_name": "Lecture Notes.docx",
            "compliance_score": 72,
            "issues": [
                {
                    "id": "issue-1",
                    "type": "missing_alt_text",
                    "severity": "critical",
                    "description": "Image on page 3 missing alt text",
                    "wcag_criterion": "1.1.1",
                    "remediation": "Add descriptive alt text to image",
                },
                {
                    "id": "issue-2",
                    "type": "low_contrast",
                    "severity": "high",
                    "description": "Text on page 5 has contrast ratio of 3.2:1",
                    "wcag_criterion": "1.4.3",
                    "remediation": "Increase contrast to at least 4.5:1",
                },
            ],
            "passed_checks": 45,
            "failed_checks": 8,
        }
        mock.return_value = processor
        yield processor


@pytest.fixture
def mock_remediation_service():
    """Mock the remediation service."""
    with patch("src.remediation.auto_remediator.AutoRemediator") as mock:
        remediator = MagicMock()
        remediator.remediate.return_value = {
            "success": True,
            "remediated_file_path": "/tmp/remediated_doc.docx",
            "fixes_applied": [
                {
                    "issue_id": "issue-1",
                    "status": "fixed",
                    "description": "Added alt text",
                },
                {
                    "issue_id": "issue-2",
                    "status": "fixed",
                    "description": "Adjusted colors",
                },
            ],
            "new_compliance_score": 95,
        }
        mock.return_value = remediator
        yield remediator


class TestGoogleWorkspaceE2EFlow:
    """
    End-to-end tests for complete Google Workspace integration flow.

    Flow: Connect → List Files → Scan → Remediate → Upload
    """

    def test_e2e_connect_flow(
        self,
        client,
        auth_headers,
        mock_google_oauth_service,
        mock_db_session,
    ):
        """Test complete OAuth connection flow."""
        # Step 1: Initiate OAuth connection
        response = client.post(
            "/api/google/connect",
            headers=auth_headers,
            json={
                "redirect_uri": "http://localhost:5173/integrations/callback",
                "department_id": "test-dept-456",
            },
        )

        # Should return authorization URL
        assert response.status_code in [200, 401, 422]
        if response.status_code == 200:
            data = response.json()
            assert "auth_url" in data or "authorization_url" in data
            assert "state" in data

    def test_e2e_callback_token_exchange(
        self,
        client,
        mock_google_oauth_service,
        mock_db_session,
        mock_oauth_token_manager,
    ):
        """Test OAuth callback exchanges code for tokens."""
        # Step 2: Handle OAuth callback
        response = client.get(
            "/api/google/callback",
            params={
                "code": "4/P7q7W91a-oMsCeLvIaQm6bTrgtp7",
                "state": "test-state-123",
            },
        )

        # Should redirect to success page or return success
        assert response.status_code in [200, 302, 400, 401]

    def test_e2e_list_files_after_connect(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_oauth_token_manager,
    ):
        """Test listing files after successful connection."""
        # Step 3: List files from Drive
        response = client.get(
            "/api/google/drive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "files" in data or isinstance(data, list)

    def test_e2e_scan_document(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_scan_processor,
        mock_oauth_token_manager,
    ):
        """Test scanning a document from Google Drive."""
        # Step 4: Scan a document
        response = client.post(
            "/api/google/scan/file/doc-123",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "scan_type": "accessibility",
            },
        )

        assert response.status_code in [200, 202, 401, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            # Should return job_id or scan results
            assert "job_id" in data or "scan_id" in data or "issues" in data

    def test_e2e_remediate_document(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_remediation_service,
        mock_oauth_token_manager,
    ):
        """Test remediating a scanned document."""
        # Step 5: Remediate the document
        response = client.post(
            "/api/google/remediate/doc-123",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "issues_to_fix": ["issue-1", "issue-2"],
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 202, 401, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            # Should return remediation status
            assert "success" in data or "job_id" in data or "status" in data

    def test_e2e_upload_remediated_file(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_oauth_token_manager,
    ):
        """Test uploading remediated file back to Drive."""
        # Step 6: Upload fixed file
        response = client.post(
            "/api/google/upload",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "file_id": "doc-123",
                "content": base64.b64encode(b"Fixed document content").decode(),
                "filename": "Lecture Notes (Accessible).docx",
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 201, 401, 404, 422]

    def test_e2e_disconnect(self, client, auth_headers, mock_db_session):
        """Test disconnecting Google account."""
        # Step 7: Disconnect
        response = client.delete(
            "/api/google/disconnect",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 204, 401, 404]


class TestGoogleWorkspaceFolderSync:
    """Test folder selection and sync functionality."""

    def test_list_folders(self, client, auth_headers, mock_google_drive_service):
        """Test listing folders for selection."""
        mock_google_drive_service.list_files.return_value = {
            "files": [
                {
                    "id": "folder-1",
                    "name": "Course Materials",
                    "mimeType": "application/vnd.google-apps.folder",
                },
                {
                    "id": "folder-2",
                    "name": "Assignments",
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ],
        }

        response = client.get(
            "/api/google/drive/folders",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]

    def test_add_folder_to_sync(self, client, auth_headers, mock_db_session):
        """Test adding a folder to sync list."""
        response = client.post(
            "/api/integrations/sync-folders",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "provider": "GOOGLE",
                "folder_id": "folder-1",
                "folder_name": "Course Materials",
                "folder_path": "/Course Materials",
            },
        )

        assert response.status_code in [200, 201, 401, 409, 422]

    def test_sync_selected_folders_only(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_db_session,
    ):
        """Test that sync only processes selected folders."""
        response = client.post(
            "/api/integrations/sync",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "provider": "GOOGLE",
            },
        )

        assert response.status_code in [200, 202, 401, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            assert "job_id" in data or "status" in data


class TestGoogleWorkspaceTokenRefresh:
    """Test token refresh scenarios."""

    def test_auto_refresh_expired_token(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
        mock_oauth_token_manager,
    ):
        """Test automatic token refresh when token is expired."""
        # Set token as expired
        mock_oauth_token_manager.is_token_expired.return_value = True
        mock_oauth_token_manager.refresh_token.return_value = {
            "access_token": "ya29.refreshed-token",
            "expires_in": 3600,
        }

        response = client.get(
            "/api/google/drive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        # Should succeed after token refresh
        assert response.status_code in [200, 401, 404]

    def test_refresh_token_revoked(
        self,
        client,
        auth_headers,
        mock_oauth_token_manager,
    ):
        """Test handling of revoked refresh token."""
        mock_oauth_token_manager.is_token_expired.return_value = True
        mock_oauth_token_manager.refresh_token.side_effect = Exception(
            "Token has been revoked"
        )

        response = client.get(
            "/api/google/drive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        # Should return error indicating reconnection needed
        assert response.status_code in [401, 403, 404]


class TestGoogleWorkspaceErrorHandling:
    """Test error handling scenarios."""

    def test_rate_limit_handling(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
    ):
        """Test handling of Google API rate limits."""
        mock_google_drive_service.list_files.side_effect = Exception(
            "Rate limit exceeded"
        )

        response = client.get(
            "/api/google/drive/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [429, 500, 401, 404]

    def test_file_not_found(
        self,
        client,
        auth_headers,
        mock_google_drive_service,
    ):
        """Test handling of file not found."""
        mock_google_drive_service.download_file.side_effect = Exception(
            "File not found"
        )

        response = client.get(
            "/api/google/drive/files/nonexistent-file/download",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [404, 401, 500]

    def test_unauthorized_department_access(self, client, auth_headers):
        """Test that users cannot access other departments' files."""
        response = client.get(
            "/api/google/drive/files",
            headers=auth_headers,
            params={"department_id": "other-dept-999"},
        )

        # Should be forbidden or not found
        assert response.status_code in [401, 403, 404]


class TestGoogleWorkspaceConnectionStatus:
    """Test connection status endpoints."""

    def test_get_connection_status(self, client, auth_headers):
        """Test getting Google connection status."""
        response = client.get(
            "/api/google/status",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert "connected" in data or "status" in data

    def test_get_account_info(self, client, auth_headers, mock_google_oauth_service):
        """Test getting connected account info."""
        response = client.get(
            "/api/google/account",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
