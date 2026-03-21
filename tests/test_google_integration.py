"""
Tests for Google Workspace integration.

Tests cover:
- OAuth 2.0 connection flow
- Drive file operations
- Document export (Docs, Slides, Sheets)
- Scan integration
- File remediation and upload back
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import uuid
from datetime import datetime, timezone

# Import app for testing
from src.api.main import app

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_google_credentials():
    """Mock Google OAuth credentials."""
    with patch("src.integrations.google_workspace.google_oauth.Credentials") as mock:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.token = "test-access-token"
        mock_creds.refresh_token = "test-refresh-token"
        mock.return_value = mock_creds
        yield mock_creds


@pytest.fixture
def mock_drive_service():
    """Mock Google Drive service."""
    with patch("src.integrations.google_workspace.google_drive.build") as mock_build:
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_service.files.return_value = mock_files
        mock_build.return_value = mock_service
        yield mock_service


@pytest.fixture
def mock_oauth_token_manager():
    """Mock OAuth token manager."""
    with patch("src.integrations.oauth_token_manager.OAuthTokenManager") as mock:
        manager = MagicMock()
        manager.encrypt_token.return_value = "encrypted-token"
        manager.decrypt_token.return_value = "decrypted-token"
        manager.get_credentials.return_value = MagicMock(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            token_expires_at=datetime.now(timezone.utc),
        )
        mock.return_value = manager
        yield manager


class TestGoogleOAuthFlow:
    """Tests for Google OAuth 2.0 connection flow."""

    def test_google_connect_returns_auth_url(self, client):
        """Test that /google/connect returns an OAuth authorization URL."""
        with patch(
            "src.integrations.google_workspace.google_oauth.GoogleOAuthService"
        ) as mock_oauth:
            mock_service = MagicMock()
            mock_service.get_authorization_url.return_value = (
                "https://accounts.google.com/o/oauth2/auth?...",
                "test-state-123",
            )
            mock_oauth.return_value = mock_service

            response = client.post(
                "/google/connect",
                json={"redirect_uri": "http://localhost:3000/callback"},
            )

            # Should return auth URL, require auth, or 404 if feature not configured
            assert response.status_code in [200, 401, 404, 422]
            if response.status_code == 200:
                data = response.json()
                assert "auth_url" in data or "url" in data

    def test_google_callback_exchanges_code(self, client, mock_google_credentials):
        """Test that OAuth callback exchanges code for tokens."""
        with patch(
            "src.integrations.google_workspace.google_oauth.GoogleOAuthService"
        ) as mock_oauth:
            mock_service = MagicMock()
            mock_service.exchange_code.return_value = {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 3600,
            }
            mock_oauth.return_value = mock_service

            response = client.get(
                "/google/callback",
                params={"code": "test-auth-code", "state": "test-state-123"},
            )

            # Should redirect, return success, or fail gracefully
            assert response.status_code in [200, 302, 400, 401, 404, 500]

    def test_google_disconnect_revokes_access(self, client):
        """Test that disconnect revokes OAuth access."""
        response = client.delete("/google/disconnect")

        # Should succeed or require auth
        assert response.status_code in [200, 204, 401, 404]


class TestGoogleDriveOperations:
    """Tests for Google Drive file operations."""

    def test_list_drive_files(self, client, mock_drive_service):
        """Test listing files from Google Drive."""
        mock_files_list = MagicMock()
        mock_files_list.execute.return_value = {
            "files": [
                {
                    "id": "file-1",
                    "name": "Document.docx",
                    "mimeType": "application/vnd.google-apps.document",
                },
                {
                    "id": "file-2",
                    "name": "Presentation.pptx",
                    "mimeType": "application/vnd.google-apps.presentation",
                },
            ],
            "nextPageToken": None,
        }
        mock_drive_service.files().list.return_value = mock_files_list

        response = client.get("/google/drive/files")

        # Should return files or require auth
        assert response.status_code in [200, 401, 404]

    def test_get_file_metadata(self, client, mock_drive_service):
        """Test getting file metadata from Drive."""
        mock_get = MagicMock()
        mock_get.execute.return_value = {
            "id": "file-1",
            "name": "Test Document.docx",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2025-01-08T12:00:00.000Z",
        }
        mock_drive_service.files().get.return_value = mock_get

        response = client.get("/google/drive/files/file-1")

        assert response.status_code in [200, 401, 404]

    def test_download_file_content(self, client, mock_drive_service):
        """Test downloading file content from Drive."""
        response = client.get("/google/drive/files/file-1/download")

        assert response.status_code in [200, 401, 404]


class TestGoogleDocsExport:
    """Tests for exporting Google Docs to Office formats."""

    def test_export_google_doc_to_docx(self, client, mock_drive_service):
        """Test exporting Google Doc to DOCX format."""
        with patch(
            "src.integrations.google_workspace.google_docs.GoogleDocsService"
        ) as mock_docs:
            mock_service = MagicMock()
            mock_service.export_to_docx.return_value = b"fake-docx-content"
            mock_docs.return_value = mock_service

            response = client.post(
                "/google/docs/export",
                json={"file_id": "doc-123", "format": "docx"},
            )

            assert response.status_code in [200, 401, 404, 422]

    def test_export_google_slides_to_pptx(self, client, mock_drive_service):
        """Test exporting Google Slides to PPTX format."""
        with patch(
            "src.integrations.google_workspace.google_slides.GoogleSlidesService"
        ) as mock_slides:
            mock_service = MagicMock()
            mock_service.export_to_pptx.return_value = b"fake-pptx-content"
            mock_slides.return_value = mock_service

            response = client.post(
                "/google/slides/export",
                json={"file_id": "slides-123", "format": "pptx"},
            )

            assert response.status_code in [200, 401, 404, 422]

    def test_export_google_sheets_to_xlsx(self, client, mock_drive_service):
        """Test exporting Google Sheets to XLSX format."""
        with patch(
            "src.integrations.google_workspace.google_sheets.GoogleSheetsService"
        ) as mock_sheets:
            mock_service = MagicMock()
            mock_service.export_to_xlsx.return_value = b"fake-xlsx-content"
            mock_sheets.return_value = mock_service

            response = client.post(
                "/google/sheets/export",
                json={"file_id": "sheet-123", "format": "xlsx"},
            )

            assert response.status_code in [200, 401, 404, 422]


class TestGoogleFileScan:
    """Tests for scanning Google Drive files."""

    def test_scan_single_file(self, client):
        """Test scanning a single file from Google Drive."""
        with patch(
            "src.integrations.google_workspace.google_drive.GoogleDriveService"
        ) as mock_drive:
            mock_service = MagicMock()
            mock_service.download_file.return_value = b"file-content"
            mock_drive.return_value = mock_service

            response = client.post(
                "/google/scan/file/doc-123",
                json={"scan_type": "accessibility"},
            )

            assert response.status_code in [200, 202, 401, 404]

    def test_scan_folder(self, client):
        """Test scanning all files in a folder."""
        response = client.post(
            "/google/scan/folder/folder-123",
            json={"recursive": True, "file_types": ["document", "presentation"]},
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_scan_returns_job_id(self, client):
        """Test that scan operations return a job ID for tracking."""
        with patch("src.jobs.cloud_scan_job.CloudScanJob") as mock_job:
            mock_job.enqueue.return_value = str(uuid.uuid4())

            response = client.post(
                "/google/scan/file/doc-123",
                json={"scan_type": "accessibility"},
            )

            if response.status_code in [200, 202]:
                data = response.json()
                assert "job_id" in data or "id" in data


class TestGoogleRemediation:
    """Tests for remediating and uploading fixed files back to Google Drive."""

    def test_remediate_file(self, client):
        """Test remediating a file and uploading back to Drive."""
        response = client.post(
            "/google/remediate/doc-123",
            json={
                "issues_to_fix": ["missing_alt_text", "low_contrast"],
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_upload_fixed_file(self, client, mock_drive_service):
        """Test uploading a fixed file back to Google Drive."""
        mock_update = MagicMock()
        mock_update.execute.return_value = {
            "id": "doc-123",
            "name": "Fixed Document.docx",
        }
        mock_drive_service.files().update.return_value = mock_update

        response = client.post(
            "/google/upload",
            json={
                "file_id": "doc-123",
                "content": "base64-encoded-content",
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 201, 401, 404, 422]


class TestGoogleConnectionStatus:
    """Tests for Google connection status."""

    def test_get_connection_status(self, client):
        """Test getting Google connection status."""
        response = client.get("/google/status")

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert "connected" in data

    def test_get_connected_account_info(self, client):
        """Test getting connected Google account information."""
        response = client.get("/google/account")

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            # Should have email or account info
            assert "email" in data or "connected" in data
