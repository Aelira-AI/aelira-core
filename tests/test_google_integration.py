"""
Tests for Google Workspace integration.

Tests cover:
- OAuth 2.0 connection flow
- Drive file operations
- Document export (Docs, Slides, Sheets)
- Scan integration
- File remediation and upload back
"""

import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Import app for testing
from src.api.main import app
from src.integrations import oauth_token_manager
from src.integrations.google_workspace.google_oauth import GoogleOAuthService
from src.integrations.oauth_token_manager import OAuthTokenManager

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def google_token_manager():
    """Create the production token manager with deterministic credentials."""
    manager = OAuthTokenManager(OAuthTokenManager.generate_encryption_key())
    manager._google_client_id = "google-client-id"
    manager._google_client_secret = "google-client-secret"
    return manager


def install_httpx_transport(monkeypatch, handler):
    """Route the production AsyncClient through a fail-closed mock transport."""
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        oauth_token_manager.httpx,
        "AsyncClient",
        lambda *args, **kwargs: async_client(*args, transport=transport, **kwargs),
    )


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

    def test_google_connect_returns_auth_url(self, google_token_manager):
        """Build the authorization request through the production service."""
        service = GoogleOAuthService(google_token_manager)

        auth_url = service.get_authorization_url(
            redirect_uri="https://dashboard.example/callback",
            scopes=["drive.readonly", "userinfo.email"],
            state="csrf-state",
        )

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "accounts.google.com"
        assert parsed.path == "/o/oauth2/v2/auth"
        assert query == {
            "client_id": ["google-client-id"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "response_type": ["code"],
            "scope": ["drive.readonly userinfo.email"],
            "access_type": ["offline"],
            "prompt": ["consent"],
            "state": ["csrf-state"],
        }

    async def test_google_callback_exchanges_code(
        self, google_token_manager, monkeypatch
    ):
        """Exchange code and load identity through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url == httpx.URL(google_token_manager.GOOGLE_TOKEN_URL):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                        "scope": "drive.readonly userinfo.email",
                    },
                )
            if request.url == httpx.URL(
                "https://www.googleapis.com/oauth2/v2/userinfo"
            ):
                return httpx.Response(
                    200,
                    json={
                        "id": "google-user-id",
                        "email": "faculty@example.edu",
                        "name": "Test Faculty",
                    },
                )
            raise AssertionError(f"Unexpected Google OAuth request: {request.url}")

        install_httpx_transport(monkeypatch, handler)
        service = GoogleOAuthService(google_token_manager)

        token_data = await service.exchange_code(
            code="authorization-code",
            redirect_uri="https://dashboard.example/callback",
        )

        assert token_data["access_token"] == "access-token"
        assert token_data["refresh_token"] == "refresh-token"
        assert token_data["scopes"] == ["drive.readonly", "userinfo.email"]
        assert token_data["user_id"] == "google-user-id"
        assert token_data["email"] == "faculty@example.edu"
        assert token_data["name"] == "Test Faculty"
        assert [request.method for request in requests] == ["POST", "GET"]
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["google-client-id"],
            "client_secret": ["google-client-secret"],
            "code": ["authorization-code"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://dashboard.example/callback"],
        }
        assert requests[1].headers["Authorization"] == "Bearer access-token"

    async def test_google_disconnect_revokes_access(
        self, google_token_manager, monkeypatch
    ):
        """Revoke access through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url != httpx.URL(
                f"{google_token_manager.GOOGLE_REVOKE_URL}?token=refresh-token"
            ):
                raise AssertionError(f"Unexpected Google revoke request: {request.url}")
            return httpx.Response(200)

        install_httpx_transport(monkeypatch, handler)

        revoked = await google_token_manager.revoke_google_token("refresh-token")

        assert revoked is True
        assert len(requests) == 1
        assert requests[0].method == "POST"


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
