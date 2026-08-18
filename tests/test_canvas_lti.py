"""
Tests for Canvas LMS integration via REST API and OAuth 2.0.

Tests cover:
- OAuth 2.0 flow initiation
- OAuth callback handling
- Connection status
- Course listing
- File listing
- Remediation job creation
- Token refresh mechanism
- Disconnect flow
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
import uuid

# Import app for testing
from src.api.main import app
from src.auth.dependencies import get_required_api_key
from src.db.database import get_db_dependency

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_canvas_oauth_config():
    """Mock Canvas OAuth configuration."""
    return {
        "client_id": "canvas-test-client-id",
        "client_secret": "canvas-test-client-secret",
        "redirect_uri": "https://api.example.com/canvas/oauth/callback",
        "canvas_instance_url": "https://canvas.university.edu",
    }


@pytest.fixture
def mock_canvas_credentials():
    """Mock Canvas OAuth credentials from database."""
    return {
        "id": str(uuid.uuid4()),
        "department_id": "test-dept-456",
        "provider": "CANVAS",
        "access_token": "encrypted-access-token",
        "refresh_token": "encrypted-refresh-token",
        "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scopes": "url:GET|/api/v1/courses url:GET|/api/v1/users/:user_id/files",
        "is_active": True,
        "metadata": {
            "canvas_instance_url": "https://canvas.university.edu",
            "user_id": "canvas-user-123",
            "user_email": "instructor@university.edu",
            "user_name": "Test Instructor",
        },
    }


@pytest.fixture
def mock_api_key():
    """Mock API key for authentication."""
    api_key = MagicMock()
    api_key.user_id = "test-user-123"
    api_key.department_id = "test-dept-456"
    return api_key


@pytest.fixture
def auth_headers():
    """Headers with mock authentication."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def mock_session():
    """Mock database session with default query chain returning None."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    return session


@pytest.fixture(autouse=False)
def patch_require_feature():
    """Patch require_feature to be a no-op for Canvas integration tests.

    Canvas tests focus on integration logic, not feature gating.
    The mock DB session does not contain Department rows, so require_feature
    would always raise 404 "Department not found".
    """
    with patch("src.api.canvas_routes.require_feature", new_callable=AsyncMock):
        yield


@pytest.fixture
def override_deps(mock_api_key, mock_session, patch_require_feature):
    """Override FastAPI dependencies for auth and DB, cleaning up after the test.

    get_required_api_key returns a tuple: (api_key, user_id, department_id).
    """
    app.dependency_overrides[get_required_api_key] = lambda: (
        mock_api_key,
        "test-user-123",
        "test-dept-456",
    )
    app.dependency_overrides[get_db_dependency] = lambda: mock_session
    yield
    app.dependency_overrides.pop(get_required_api_key, None)
    app.dependency_overrides.pop(get_db_dependency, None)


class TestCanvasOAuthFlow:
    """Tests for Canvas OAuth 2.0 authentication flow."""

    def test_connect_canvas_missing_auth(self, client):
        """Test that /canvas/connect requires authentication."""
        response = client.post(
            "/canvas/connect",
            json={
                "canvas_instance_url": "https://canvas.university.edu",
                "department_id": "test-dept-456",
            },
        )
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    @patch("src.api.canvas_routes.CanvasOAuthService")
    def test_connect_canvas_success(
        self, mock_oauth_service, client, mock_api_key, auth_headers, override_deps
    ):
        """Test successful Canvas OAuth connection initiation."""
        mock_service_instance = MagicMock()
        mock_service_instance.is_configured.return_value = True
        mock_service_instance.get_authorization_url.return_value = "https://canvas.university.edu/login/oauth2/auth?client_id=test&redirect_uri=..."
        mock_oauth_service.return_value = mock_service_instance

        response = client.post(
            "/canvas/connect",
            json={
                "canvas_instance_url": "https://canvas.university.edu",
                "department_id": "test-dept-456",
            },
            headers=auth_headers,
        )

        # Should return authorization URL
        assert response.status_code == 200
        data = response.json()
        assert "authorization_url" in data
        assert "state" in data

    @patch("src.api.canvas_routes.CanvasOAuthService")
    def test_connect_canvas_not_configured(
        self, mock_oauth_service, client, mock_api_key, auth_headers, override_deps
    ):
        """Test Canvas connect when OAuth not configured."""
        mock_service_instance = MagicMock()
        mock_service_instance.is_configured.return_value = False
        mock_oauth_service.return_value = mock_service_instance

        response = client.post(
            "/canvas/connect",
            json={
                "canvas_instance_url": "https://canvas.university.edu",
                "department_id": "test-dept-456",
            },
            headers=auth_headers,
        )

        assert response.status_code == 500
        assert "Canvas OAuth not configured" in response.json()["detail"]

    def test_connect_canvas_department_access_denied(
        self, client, mock_api_key, mock_session, auth_headers
    ):
        """Test Canvas connect with wrong department."""
        # Override with a different department to trigger 403
        app.dependency_overrides[get_required_api_key] = lambda: (
            mock_api_key,
            "test-user-123",
            "different-dept",
        )
        app.dependency_overrides[get_db_dependency] = lambda: mock_session
        try:
            response = client.post(
                "/canvas/connect",
                json={
                    "canvas_instance_url": "https://canvas.university.edu",
                    "department_id": "test-dept-456",  # Trying to access different dept
                },
                headers=auth_headers,
            )

            assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(get_required_api_key, None)
            app.dependency_overrides.pop(get_db_dependency, None)


class TestCanvasConnectionStatus:
    """Tests for Canvas connection status endpoint."""

    def test_status_missing_auth(self, client):
        """Test that /canvas/status requires authentication."""
        response = client.get("/canvas/status?department_id=test-dept-456")
        assert response.status_code == 401

    def test_status_not_connected(self, client, auth_headers, override_deps):
        """Test status when Canvas is not connected."""
        response = client.get(
            "/canvas/status?department_id=test-dept-456",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False

    def test_status_connected(
        self,
        client,
        mock_session,
        mock_canvas_credentials,
        auth_headers,
        override_deps,
    ):
        """Test status when Canvas is connected."""
        # Mock database credential
        mock_credential = MagicMock()
        # The column is provider_metadata; this test set `metadata`, so the
        # route read an auto-created mock and the response model rejected it.
        mock_credential.provider_metadata = mock_canvas_credentials["metadata"]
        mock_credential.created_at = datetime.now(timezone.utc)
        mock_credential.id = mock_canvas_credentials["id"]

        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_credential
        )

        response = client.get(
            "/canvas/status?department_id=test-dept-456",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is True
        assert data["user_email"] == "instructor@university.edu"


class TestCanvasCourses:
    """Tests for Canvas course listing."""

    def test_list_courses_missing_auth(self, client):
        """Test that /canvas/courses requires authentication."""
        response = client.get("/canvas/courses?department_id=test-dept-456")
        assert response.status_code == 401

    @patch("src.api.canvas_routes._get_canvas_client")
    def test_list_courses_success(
        self, mock_get_client, client, mock_api_key, auth_headers, override_deps
    ):
        """Test successful course listing."""
        # Mock Canvas API client
        mock_api_client = AsyncMock()
        mock_course = MagicMock()
        mock_course.id = "12345"
        mock_course.name = "Test Course"
        mock_course.course_code = "TEST101"
        mock_course.workflow_state = "available"
        mock_course.start_at = None
        mock_course.end_at = None
        mock_api_client.list_courses.return_value = [mock_course]
        mock_api_client.close = AsyncMock()

        mock_credential = MagicMock()
        mock_get_client.return_value = (mock_credential, mock_api_client)

        response = client.get(
            "/canvas/courses?department_id=test-dept-456",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Course"


class TestCanvasFiles:
    """Tests for Canvas file listing."""

    def test_list_files_missing_auth(self, client):
        """Test that /canvas/courses/{id}/files requires authentication."""
        response = client.get("/canvas/courses/12345/files?department_id=test-dept-456")
        assert response.status_code == 401


class TestCanvasRemediation:
    """Tests for Canvas file remediation."""

    def test_remediate_missing_auth(self, client):
        """Test that /canvas/remediate requires authentication."""
        response = client.post(
            "/canvas/remediate",
            json={
                "file_id": "file-123",
                "course_id": "course-123",
                "department_id": "test-dept-456",
            },
        )
        assert response.status_code == 401

    def test_remediate_not_connected(self, client, auth_headers, override_deps):
        """Test remediation when Canvas not connected."""
        response = client.post(
            "/canvas/remediate",
            json={
                "file_id": "file-123",
                "course_id": "course-123",
                "department_id": "test-dept-456",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Canvas not connected" in data["message"]


class TestCanvasDisconnect:
    """Tests for Canvas disconnect flow."""

    def test_disconnect_missing_auth(self, client):
        """Test that /canvas/disconnect requires authentication."""
        response = client.delete("/canvas/disconnect?department_id=test-dept-456")
        assert response.status_code == 401

    def test_disconnect_not_connected(self, client, auth_headers, override_deps):
        """Test disconnect when Canvas not connected."""
        response = client.delete(
            "/canvas/disconnect?department_id=test-dept-456",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "Canvas not connected" in response.json()["detail"]

    def test_disconnect_success(
        self, client, mock_session, auth_headers, override_deps
    ):
        """Test successful disconnect."""
        # Mock existing credential
        mock_credential = MagicMock()
        mock_credential.is_active = True

        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_credential
        )

        response = client.delete(
            "/canvas/disconnect?department_id=test-dept-456",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] == "true"
        assert mock_credential.is_active is False


class TestCanvasOAuthCallback:
    """Tests for Canvas OAuth callback endpoint."""

    def test_callback_missing_params(self, client):
        """Test callback with missing parameters."""
        response = client.get("/canvas/oauth/callback")
        assert response.status_code == 422  # Validation error

    @patch("src.api.canvas_routes.CanvasAPIClient")
    @patch("src.api.canvas_routes.CanvasOAuthService")
    @patch("src.api.canvas_routes.OAuthTokenManager")
    def test_callback_success(
        self,
        mock_token_manager,
        mock_oauth_service,
        mock_api_client_cls,
        client,
        mock_session,
    ):
        """Test successful OAuth callback."""
        app.dependency_overrides[get_db_dependency] = lambda: mock_session

        try:
            # Mock OAuth exchange
            mock_service_instance = AsyncMock()
            mock_credential = MagicMock()
            mock_credential.canvas_instance_url = "https://canvas.university.edu"
            mock_credential.access_token = "new-access-token"
            mock_credential.refresh_token = "new-refresh-token"
            mock_credential.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            mock_credential.scope = "test-scope"
            mock_credential.user_id = "canvas-user-123"
            mock_service_instance.exchange_code_for_token.return_value = mock_credential
            mock_oauth_service.return_value = mock_service_instance

            # Mock Canvas API client for user info
            mock_api_instance = AsyncMock()
            mock_user_info = MagicMock()
            mock_user_info.email = "instructor@university.edu"
            mock_user_info.name = "Test Instructor"
            mock_api_instance.get_current_user.return_value = mock_user_info
            mock_api_instance.close = AsyncMock()
            mock_api_client_cls.return_value = mock_api_instance

            # Mock token manager
            mock_manager_instance = MagicMock()
            mock_manager_instance.encrypt_token.return_value = "encrypted-token"
            mock_token_manager.return_value = mock_manager_instance

            # Mock db to return None for existing credential check, then accept add/commit
            mock_session.query.return_value.filter.return_value.first.return_value = (
                None
            )

            # The callback now carries its context inside the state
            # parameter, base64-encoded, rather than as separate query
            # parameters an attacker could set. Build a real one.
            state = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "canvas_instance_url": "https://canvas.university.edu",
                            "department_id": "test-dept-456",
                        }
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )

            response = client.get(
                f"/canvas/oauth/callback?code=test-auth-code&state={state}",
                follow_redirects=False,
            )

            # The callback hands the browser back to the dashboard rather
            # than answering with JSON, so the contract under test is where
            # it sends the user and what it tells them on arrival.
            assert response.status_code in (302, 307)
            location = response.headers["location"]
            assert "/integrations?canvas=connected" in location
            assert "instructor@university.edu" in location
        finally:
            app.dependency_overrides.pop(get_db_dependency, None)
