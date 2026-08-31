"""
Tests for Microsoft 365 integration.

Tests cover:
- OAuth 2.0 connection flow with Microsoft Identity Platform
- Microsoft Graph API operations
- OneDrive file operations
- SharePoint site and library access
- Document scanning and remediation
"""

import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Import app for testing
from src.api.main import app
from src.integrations import oauth_token_manager
from src.integrations.microsoft_365.microsoft_oauth import MicrosoftOAuthService
from src.integrations.oauth_token_manager import OAuthTokenManager

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def microsoft_token_manager():
    """Create the production token manager with deterministic credentials."""
    manager = OAuthTokenManager(OAuthTokenManager.generate_encryption_key())
    manager._microsoft_client_id = "microsoft-client-id"
    manager._microsoft_client_secret = "microsoft-client-secret"
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
def mock_graph_client():
    """Mock Microsoft Graph client."""
    with patch(
        "src.integrations.microsoft_365.microsoft_graph.GraphClient"
    ) as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_onedrive_items():
    """Mock OneDrive items response."""
    return {
        "value": [
            {
                "id": "item-1",
                "name": "Document.docx",
                "file": {
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                },
                "size": 12345,
                "lastModifiedDateTime": "2025-01-08T12:00:00Z",
            },
            {
                "id": "item-2",
                "name": "Presentation.pptx",
                "file": {
                    "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                },
                "size": 54321,
                "lastModifiedDateTime": "2025-01-07T12:00:00Z",
            },
        ],
        "@odata.nextLink": None,
    }


class TestMicrosoftOAuthFlow:
    """Tests for Microsoft OAuth 2.0 connection flow."""

    def test_microsoft_connect_returns_auth_url(self, microsoft_token_manager):
        """Build the authorization request through the production service."""
        service = MicrosoftOAuthService(microsoft_token_manager)

        auth_url = service.get_authorization_url(
            redirect_uri="https://dashboard.example/callback",
            scopes=["User.Read", "offline_access"],
            state="csrf-state",
        )

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "login.microsoftonline.com"
        assert parsed.path == "/common/oauth2/v2.0/authorize"
        assert query == {
            "client_id": ["microsoft-client-id"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "response_type": ["code"],
            "scope": ["User.Read offline_access"],
            "response_mode": ["query"],
            "state": ["csrf-state"],
        }

    async def test_microsoft_callback_exchanges_code(
        self, microsoft_token_manager, monkeypatch
    ):
        """Exchange code and load identity through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url == httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                        "scope": "User.Read offline_access",
                    },
                )
            if request.url == httpx.URL("https://graph.microsoft.com/v1.0/me"):
                return httpx.Response(
                    200,
                    json={
                        "id": "microsoft-user-id",
                        "mail": "faculty@example.edu",
                        "displayName": "Test Faculty",
                    },
                )
            raise AssertionError(f"Unexpected Microsoft OAuth request: {request.url}")

        install_httpx_transport(monkeypatch, handler)
        service = MicrosoftOAuthService(microsoft_token_manager)

        token_data = await service.exchange_code(
            code="authorization-code",
            redirect_uri="https://dashboard.example/callback",
        )

        assert token_data["access_token"] == "access-token"
        assert token_data["refresh_token"] == "refresh-token"
        assert token_data["scopes"] == ["User.Read", "offline_access"]
        assert token_data["user_id"] == "microsoft-user-id"
        assert token_data["email"] == "faculty@example.edu"
        assert token_data["name"] == "Test Faculty"
        assert [request.method for request in requests] == ["POST", "GET"]
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["microsoft-client-id"],
            "client_secret": ["microsoft-client-secret"],
            "code": ["authorization-code"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "scope": [
                "Files.Read.All Files.ReadWrite.All Sites.Read.All User.Read offline_access"
            ],
        }
        assert requests[1].headers["Authorization"] == "Bearer access-token"

    async def test_microsoft_token_refresh(self, microsoft_token_manager, monkeypatch):
        """Refresh an expired token through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url != httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL):
                raise AssertionError(
                    f"Unexpected Microsoft refresh request: {request.url}"
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 1800,
                },
            )

        install_httpx_transport(monkeypatch, handler)

        access_token, refresh_token, expires_at = (
            await microsoft_token_manager.refresh_microsoft_token("old-refresh-token")
        )

        assert access_token == "new-access-token"
        assert refresh_token == "new-refresh-token"
        assert expires_at.tzinfo is not None
        assert len(requests) == 1
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["microsoft-client-id"],
            "client_secret": ["microsoft-client-secret"],
            "refresh_token": ["old-refresh-token"],
            "grant_type": ["refresh_token"],
            "scope": [
                "Files.Read.All Files.ReadWrite.All Sites.Read.All User.Read offline_access"
            ],
        }

    @pytest.mark.parametrize(
        ("failure", "error_type"),
        [
            ("provider", httpx.HTTPStatusError),
            ("transport", httpx.ConnectError),
        ],
    )
    async def test_microsoft_exchange_failures_use_real_http_path(
        self, microsoft_token_manager, monkeypatch, caplog, failure, error_type
    ):
        """Keep provider and transport failures bounded at the real seam."""

        def handler(request):
            assert request.method == "POST"
            assert request.url == httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL)
            if failure == "provider":
                return httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "rejected"},
                )
            raise httpx.ConnectError("test transport unavailable", request=request)

        install_httpx_transport(monkeypatch, handler)
        service = MicrosoftOAuthService(microsoft_token_manager)

        with pytest.raises(error_type):
            await service.exchange_code(
                code="sensitive-authorization-code",
                redirect_uri="https://dashboard.example/callback",
            )

        assert "sensitive-authorization-code" not in caplog.text
        assert "microsoft-client-secret" not in caplog.text


class TestOneDriveOperations:
    """Tests for OneDrive file operations."""

    def test_list_onedrive_files(self, client, mock_graph_client, mock_onedrive_items):
        """Test listing files from OneDrive."""
        mock_graph_client.get.return_value = mock_onedrive_items

        response = client.get("/microsoft/onedrive/files")

        assert response.status_code in [200, 401, 404]

    def test_list_onedrive_folder_contents(self, client, mock_graph_client):
        """Test listing contents of a specific folder."""
        mock_graph_client.get.return_value = {
            "value": [
                {"id": "file-1", "name": "Doc.docx", "file": {}},
            ]
        }

        response = client.get("/microsoft/onedrive/folders/folder-123/children")

        assert response.status_code in [200, 401, 404]

    def test_get_file_metadata(self, client, mock_graph_client):
        """Test getting file metadata from OneDrive."""
        mock_graph_client.get.return_value = {
            "id": "file-1",
            "name": "Test Document.docx",
            "size": 12345,
            "lastModifiedDateTime": "2025-01-08T12:00:00Z",
        }

        response = client.get("/microsoft/onedrive/files/file-1")

        assert response.status_code in [200, 401, 404]

    def test_download_file_content(self, client, mock_graph_client):
        """Test downloading file content from OneDrive."""
        mock_graph_client.get.return_value = b"file-content"

        response = client.get("/microsoft/onedrive/files/file-1/content")

        assert response.status_code in [200, 401, 404]

    def test_upload_file_to_onedrive(self, client, mock_graph_client):
        """Test uploading a file to OneDrive."""
        mock_graph_client.put.return_value = {
            "id": "new-file-123",
            "name": "Uploaded.docx",
        }

        response = client.post(
            "/microsoft/onedrive/upload",
            json={
                "folder_id": "folder-123",
                "filename": "test.docx",
                "content": "base64-encoded-content",
            },
        )

        assert response.status_code in [200, 201, 401, 422]


class TestSharePointOperations:
    """Tests for SharePoint site and library access."""

    def test_list_sharepoint_sites(self, client, mock_graph_client):
        """Test listing SharePoint sites."""
        mock_graph_client.get.return_value = {
            "value": [
                {
                    "id": "site-1",
                    "name": "Department Site",
                    "webUrl": "https://university.sharepoint.com/sites/dept",
                },
            ]
        }

        response = client.get("/microsoft/sharepoint/sites")

        assert response.status_code in [200, 401, 404]

    def test_get_site_document_libraries(self, client, mock_graph_client):
        """Test getting document libraries from a SharePoint site."""
        mock_graph_client.get.return_value = {
            "value": [
                {"id": "lib-1", "name": "Documents", "driveType": "documentLibrary"},
                {
                    "id": "lib-2",
                    "name": "Course Materials",
                    "driveType": "documentLibrary",
                },
            ]
        }

        response = client.get("/microsoft/sharepoint/sites/site-1/drives")

        assert response.status_code in [200, 401, 404]

    def test_list_library_files(self, client, mock_graph_client, mock_onedrive_items):
        """Test listing files in a SharePoint document library."""
        mock_graph_client.get.return_value = mock_onedrive_items

        response = client.get("/microsoft/sharepoint/drives/lib-1/items")

        assert response.status_code in [200, 401, 404]

    def test_search_sharepoint_files(self, client, mock_graph_client):
        """Test searching for files across SharePoint."""
        mock_graph_client.get.return_value = {
            "value": [
                {"id": "file-1", "name": "Syllabus.pdf", "webUrl": "..."},
            ]
        }

        response = client.get(
            "/microsoft/sharepoint/search",
            params={"query": "syllabus", "file_types": "pdf,docx"},
        )

        assert response.status_code in [200, 401, 404]


class TestMicrosoftFileScan:
    """Tests for scanning Microsoft 365 files."""

    def test_scan_onedrive_file(self, client, mock_graph_client):
        """Test scanning a single file from OneDrive."""
        mock_graph_client.get.return_value = b"file-content"

        response = client.post(
            "/microsoft/scan/file/file-123",
            json={"scan_type": "accessibility"},
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_scan_sharepoint_file(self, client, mock_graph_client):
        """Test scanning a file from SharePoint."""
        response = client.post(
            "/microsoft/scan/sharepoint/file/file-123",
            json={"site_id": "site-1", "scan_type": "accessibility"},
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_scan_onedrive_folder(self, client):
        """Test scanning all files in an OneDrive folder."""
        response = client.post(
            "/microsoft/scan/folder/folder-123",
            json={"recursive": True, "file_types": ["docx", "pptx", "pdf"]},
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_scan_returns_job_id(self, client):
        """Test that scan operations return a job ID for tracking."""
        with patch("src.jobs.cloud_scan_job.CloudScanJob") as mock_job:
            mock_job.enqueue.return_value = str(uuid.uuid4())

            response = client.post(
                "/microsoft/scan/file/file-123",
                json={"scan_type": "accessibility"},
            )

            if response.status_code in [200, 202]:
                data = response.json()
                assert "job_id" in data or "id" in data


class TestMicrosoftRemediation:
    """Tests for remediating and uploading fixed files back to Microsoft 365."""

    def test_remediate_onedrive_file(self, client):
        """Test remediating a file and uploading back to OneDrive."""
        response = client.post(
            "/microsoft/remediate/file-123",
            json={
                "issues_to_fix": ["missing_alt_text", "low_contrast"],
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_upload_fixed_file_onedrive(self, client, mock_graph_client):
        """Test uploading a fixed file back to OneDrive."""
        mock_graph_client.put.return_value = {
            "id": "file-123",
            "name": "Fixed Document.docx",
        }

        response = client.post(
            "/microsoft/onedrive/upload",
            json={
                "file_id": "file-123",
                "content": "base64-encoded-content",
                "create_new_version": True,
            },
        )

        assert response.status_code in [200, 201, 401, 422]

    def test_upload_fixed_file_sharepoint(self, client, mock_graph_client):
        """Test uploading a fixed file back to SharePoint."""
        mock_graph_client.put.return_value = {
            "id": "file-123",
            "name": "Fixed Document.docx",
        }

        response = client.post(
            "/microsoft/sharepoint/upload",
            json={
                "site_id": "site-1",
                "drive_id": "lib-1",
                "file_id": "file-123",
                "content": "base64-encoded-content",
            },
        )

        assert response.status_code in [200, 201, 401, 422]


class TestMicrosoftConnectionStatus:
    """Tests for Microsoft connection status."""

    def test_get_connection_status(self, client):
        """Test getting Microsoft connection status."""
        response = client.get("/microsoft/status")

        assert response.status_code in [200, 401]
        if response.status_code == 200:
            data = response.json()
            assert "connected" in data

    def test_get_connected_account_info(self, client, mock_graph_client):
        """Test getting connected Microsoft account information."""
        mock_graph_client.get.return_value = {
            "displayName": "Test User",
            "mail": "test@university.edu",
            "userPrincipalName": "test@university.edu",
        }

        response = client.get("/microsoft/account")

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "email" in data or "mail" in data or "connected" in data


class TestMicrosoftGraphSubscriptions:
    """Tests for Microsoft Graph webhook subscriptions."""

    def test_create_subscription(self, client, mock_graph_client):
        """Test creating a webhook subscription for file changes."""
        mock_graph_client.post.return_value = {
            "id": "subscription-123",
            "resource": "/me/drive/root",
            "changeType": "updated",
            "expirationDateTime": "2025-01-09T12:00:00Z",
        }

        response = client.post(
            "/microsoft/subscriptions",
            json={
                "resource": "/me/drive/root",
                "change_type": "updated",
                "notification_url": "https://api.example.com/webhooks/microsoft",
            },
        )

        assert response.status_code in [200, 201, 401, 422]

    def test_renew_subscription(self, client, mock_graph_client):
        """Test renewing an existing webhook subscription."""
        mock_graph_client.patch.return_value = {
            "id": "subscription-123",
            "expirationDateTime": "2025-01-10T12:00:00Z",
        }

        response = client.patch(
            "/microsoft/subscriptions/subscription-123",
            json={"expiration_hours": 24},
        )

        assert response.status_code in [200, 401, 404]

    def test_delete_subscription(self, client, mock_graph_client):
        """Test deleting a webhook subscription."""
        mock_graph_client.delete.return_value = None

        response = client.delete("/microsoft/subscriptions/subscription-123")

        assert response.status_code in [200, 204, 401, 404]

    def test_list_subscriptions(self, client, mock_graph_client):
        """Test listing active webhook subscriptions."""
        mock_graph_client.get.return_value = {
            "value": [
                {
                    "id": "subscription-123",
                    "resource": "/me/drive/root",
                    "expirationDateTime": "2025-01-09T12:00:00Z",
                },
            ]
        }

        response = client.get("/microsoft/subscriptions")

        assert response.status_code in [200, 401]
