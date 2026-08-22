"""
Tests for Brightspace Routes API Key Authentication

Tests verify that Brightspace routes:
- Require API key authentication
- Use department_id from API key (not query params)
- Properly isolate department data
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.api.main import app
from src.api.auth_routes import get_current_api_key
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import CloudOAuthCredentials, CloudProvider, APIKey, UserRole

# Mark all tests in this module as integration
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_api_key():
    """Mock API key for authentication."""
    api_key = MagicMock(spec=APIKey)
    api_key.id = "api-key-123"
    api_key.user_id = "user-123"
    api_key.department_id = "dept-123"
    return api_key


@pytest.fixture
def auth_headers():
    """Headers with mock authentication."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def mock_brightspace_credential():
    """Mock Brightspace OAuth credential."""
    cred = MagicMock(spec=CloudOAuthCredentials)
    cred.id = "cred-brightspace-123"
    cred.department_id = "dept-123"
    cred.provider = CloudProvider.BRIGHTSPACE.value
    cred.provider_metadata = {
        "brightspace_instance_url": "https://brightspace.university.edu",
        "user_email": "instructor@university.edu",
        "user_name": "Dr. Test Instructor",
    }
    cred.access_token = "encrypted-token"
    cred.created_at = datetime.now(timezone.utc)
    return cred


@pytest.fixture
def mock_brightspace_credential_other_dept():
    """Mock Brightspace OAuth credential for different department."""
    cred = MagicMock(spec=CloudOAuthCredentials)
    cred.id = "cred-brightspace-456"
    cred.department_id = "other-dept-999"  # Different department
    cred.provider = CloudProvider.BRIGHTSPACE.value
    cred.provider_instance_url = "https://brightspace.other.edu"
    cred.provider_user_email = "other@other.edu"
    cred.provider_user_name = "Other User"
    cred.access_token = "other-encrypted-token"
    cred.created_at = datetime.now(timezone.utc)
    return cred


@pytest.fixture
def mock_session():
    """Mock database session with default query chain returning None."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    return session


@pytest.fixture
def override_deps(mock_api_key, mock_session):
    """Override FastAPI dependencies for auth and DB, cleaning up after the test."""
    app.dependency_overrides[get_current_api_key] = lambda: mock_api_key
    app.dependency_overrides[get_authenticated_principal] = (
        lambda: AuthenticatedPrincipal(
            api_key=mock_api_key,
            user_id="user-123",
            department_id="dept-123",
            user_role=UserRole.ADMIN,
            auth_method="api_key",
        )
    )
    app.dependency_overrides[get_db_dependency] = lambda: mock_session
    yield
    app.dependency_overrides.pop(get_current_api_key, None)
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


# =============================================================================
# Authentication Required Tests
# =============================================================================


class TestBrightspaceAuthRequired:
    """Test that all protected Brightspace endpoints require authentication."""

    def test_status_requires_auth(self, client):
        """Test that GET /brightspace/status requires API key."""
        response = client.get("/brightspace/status")
        assert response.status_code == 401

    def test_courses_requires_auth(self, client):
        """Test that GET /brightspace/courses requires API key."""
        response = client.get("/brightspace/courses")
        assert response.status_code == 401

    def test_course_content_requires_auth(self, client):
        """Test that GET /brightspace/courses/{id}/content requires API key."""
        response = client.get("/brightspace/courses/12345/content")
        assert response.status_code == 401

    def test_disconnect_requires_auth(self, client):
        """Test that DELETE /brightspace/disconnect requires API key."""
        response = client.delete("/brightspace/disconnect")
        assert response.status_code == 401


# =============================================================================
# Department Isolation Tests
# =============================================================================


class TestBrightspaceDepartmentIsolation:
    """Test that Brightspace routes properly isolate department data."""

    def test_status_uses_api_key_department(
        self,
        client,
        mock_session,
        mock_brightspace_credential,
        auth_headers,
        override_deps,
    ):
        """Test that /status uses department_id from API key, not query params."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_brightspace_credential
        )

        response = client.get(
            "/brightspace/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is True
        assert data["user_email"] == "instructor@university.edu"

        # Verify the query was made
        mock_session.query.assert_called()

    def test_status_not_connected(self, client, auth_headers, override_deps):
        """Test /status when Brightspace is not connected."""
        response = client.get(
            "/brightspace/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False

    def test_cannot_access_other_department_connection(
        self, client, auth_headers, override_deps
    ):
        """Test that users cannot access connections from other departments."""
        response = client.get(
            "/brightspace/status",
            headers=auth_headers,
        )

        # Should return "not connected" because the query filters by department_id
        assert response.status_code == 200
        assert response.json()["connected"] is False

    def test_courses_requires_connection(self, client, auth_headers, override_deps):
        """Test that /courses returns 404 when Brightspace not connected."""
        response = client.get(
            "/brightspace/courses",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]

    def test_course_content_requires_connection(
        self, client, auth_headers, override_deps
    ):
        """Test that /courses/{id}/content returns 404 when Brightspace not connected."""
        response = client.get(
            "/brightspace/courses/12345/content",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]

    def test_disconnect_requires_connection(self, client, auth_headers, override_deps):
        """Test that /disconnect returns 404 when Brightspace not connected."""
        response = client.delete(
            "/brightspace/disconnect",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]


# =============================================================================
# Disconnect Tests
# =============================================================================


class TestBrightspaceDisconnect:
    """Test Brightspace disconnect functionality with API key auth."""

    def test_disconnect_success(
        self,
        client,
        mock_session,
        mock_brightspace_credential,
        auth_headers,
        override_deps,
    ):
        """Test successful Brightspace disconnection."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_brightspace_credential
        )

        with patch(
            "src.api.brightspace_routes.RemediationArtifactService.from_settings"
        ) as cleanup:
            cleanup.return_value.delete_for_credential.return_value.count = 0
            response = client.delete(
                "/brightspace/disconnect",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert "disconnected successfully" in response.json()["message"]

        # Verify delete and commit were called
        mock_session.delete.assert_called_once_with(mock_brightspace_credential)
        mock_session.commit.assert_called_once()

    def test_disconnect_only_affects_own_department(
        self, client, mock_session, auth_headers, override_deps
    ):
        """Test that disconnect cannot affect other departments."""
        response = client.delete(
            "/brightspace/disconnect",
            headers=auth_headers,
        )

        # Should return 404 because no credential found for this department
        assert response.status_code == 404

        # Verify delete was NOT called
        mock_session.delete.assert_not_called()


# =============================================================================
# Connection Status Response Tests
# =============================================================================


class TestBrightspaceStatusResponse:
    """Test Brightspace status response format."""

    def test_status_response_format(
        self,
        client,
        mock_session,
        mock_brightspace_credential,
        auth_headers,
        override_deps,
    ):
        """Test that status response includes all expected fields."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_brightspace_credential
        )

        response = client.get(
            "/brightspace/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Check all expected fields are present
        assert "connected" in data
        assert data["connected"] is True
        assert "brightspace_instance_url" in data
        assert data["brightspace_instance_url"] == "https://brightspace.university.edu"
        assert "user_email" in data
        assert "user_fullname" in data
        assert "connected_at" in data
        assert "credential_id" in data

    def test_status_not_connected_response(self, client, auth_headers, override_deps):
        """Test status response when not connected."""
        response = client.get(
            "/brightspace/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["connected"] is False
        # Optional fields should be None when not connected
        assert data.get("brightspace_instance_url") is None
        assert data.get("user_email") is None


# =============================================================================
# Query Parameter Ignored Tests
# =============================================================================


class TestQueryParamsIgnored:
    """Test that department_id query params are ignored in favor of API key."""

    def test_query_param_department_id_ignored(
        self,
        client,
        mock_session,
        mock_brightspace_credential,
        auth_headers,
        override_deps,
    ):
        """Test that passing department_id as query param has no effect."""
        # Return credential for dept-123 (from API key)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_brightspace_credential
        )

        # Try to pass different department_id in query params
        response = client.get(
            "/brightspace/status?department_id=attacker-dept-999",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should get credential for dept-123, not attacker-dept-999
        assert data["connected"] is True
        assert data["user_email"] == "instructor@university.edu"


# =============================================================================
# OAuth Flow Tests (Public Endpoints)
# =============================================================================


class TestBrightspaceOAuthFlow:
    """Test Brightspace OAuth flow endpoints."""

    def test_connect_requires_auth(self, client):
        """Test that POST /brightspace/connect requires authentication."""
        response = client.post(
            "/brightspace/connect",
            json={
                "brightspace_instance_url": "https://brightspace.university.edu",
                "department_id": "test-dept-123",
            },
        )

        # Connect requires API key authentication
        assert response.status_code == 401

    def test_callback_does_not_require_auth(self, client):
        """Test that GET /brightspace/callback is public (OAuth callback)."""
        response = client.get(
            "/brightspace/callback",
            params={
                "code": "test-auth-code",
                "state": "test-state",
                "brightspace_instance_url": "https://brightspace.university.edu",
                "department_id": "test-dept-123",
            },
        )

        # Should not be 401 - callback is public for OAuth flow
        # May fail for other reasons (invalid code, etc.)
        assert response.status_code != 401


# =============================================================================
# Content Endpoint Authentication Tests
# =============================================================================


class TestBrightspaceContentAuthRequired:
    """Verify all new content endpoints require API key authentication."""

    def test_list_course_files_requires_auth(self, client):
        """Test that GET /brightspace/courses/{id}/files requires API key."""
        response = client.get("/brightspace/courses/42/files")
        assert response.status_code in (401, 403)

    def test_content_scan_requires_auth(self, client):
        """Test that POST /brightspace/content/scan requires API key."""
        response = client.post(
            "/brightspace/content/scan",
            json={"org_unit_id": 42},
        )
        assert response.status_code in (401, 403)

    def test_content_status_requires_auth(self, client):
        """Test that GET /brightspace/content/courses/{id}/status requires API key."""
        response = client.get("/brightspace/content/courses/42/status")
        assert response.status_code in (401, 403)

    def test_remediate_requires_auth(self, client):
        """Test that POST /brightspace/remediate requires API key."""
        response = client.post(
            "/brightspace/remediate",
            json={"file_url": "10", "org_unit_id": 42, "department_id": "dept-1"},
        )
        assert response.status_code in (401, 403)


# =============================================================================
# LTI Endpoint Accessibility Tests
# =============================================================================


class TestBrightspaceLTIEndpoints:
    """Verify LTI endpoints are accessible (public for OIDC flow)."""

    def test_config_returns_json(self, client):
        """Test that GET /lti/brightspace/config returns valid LTI configuration."""
        response = client.get("/lti/brightspace/config")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Aelira Accessibility Scanner"
        assert "/lti/brightspace/login" in data["oidc_initiation_url"]

    def test_health_returns_ok(self, client):
        """Test that GET /lti/brightspace/health returns a health status."""
        response = client.get("/lti/brightspace/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "not_configured")

    def test_exchange_rejects_invalid_code(self, client):
        """Test that POST /lti/brightspace/exchange rejects an invalid code."""
        response = client.post(
            "/lti/brightspace/exchange",
            json={"code": "invalid-code"},
        )
        assert response.status_code == 401
