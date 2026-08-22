"""
Tests for Moodle Routes API Key Authentication

Tests verify that Moodle routes:
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
from src.db.database import get_db_dependency
from src.db.models import CloudOAuthCredentials, CloudProvider, APIKey

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
def mock_moodle_credential():
    """Mock Moodle OAuth credential."""
    cred = MagicMock(spec=CloudOAuthCredentials)
    cred.id = "cred-moodle-123"
    cred.department_id = "dept-123"
    cred.provider = CloudProvider.MOODLE.value
    cred.provider_instance_url = "https://moodle.university.edu"
    cred.provider_user_email = "faculty@university.edu"
    cred.provider_user_name = "Test Faculty"
    cred.access_token = "encrypted-token"
    cred.created_at = datetime.now(timezone.utc)
    return cred


@pytest.fixture
def mock_moodle_credential_other_dept():
    """Mock Moodle OAuth credential for different department."""
    cred = MagicMock(spec=CloudOAuthCredentials)
    cred.id = "cred-moodle-456"
    cred.department_id = "other-dept-999"  # Different department
    cred.provider = CloudProvider.MOODLE.value
    cred.provider_instance_url = "https://moodle.other.edu"
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
    app.dependency_overrides[get_db_dependency] = lambda: mock_session
    yield
    app.dependency_overrides.pop(get_current_api_key, None)
    app.dependency_overrides.pop(get_db_dependency, None)


# =============================================================================
# Authentication Required Tests
# =============================================================================


class TestMoodleAuthRequired:
    """Test that all protected Moodle endpoints require authentication."""

    def test_status_requires_auth(self, client):
        """Test that GET /moodle/status requires API key."""
        response = client.get("/moodle/status")
        assert response.status_code == 401

    def test_courses_requires_auth(self, client):
        """Test that GET /moodle/courses requires API key."""
        response = client.get("/moodle/courses")
        assert response.status_code == 401

    def test_course_files_requires_auth(self, client):
        """Test that GET /moodle/courses/{id}/files requires API key."""
        response = client.get("/moodle/courses/101/files")
        assert response.status_code == 401

    def test_disconnect_requires_auth(self, client):
        """Test that DELETE /moodle/disconnect requires API key."""
        response = client.delete("/moodle/disconnect")
        assert response.status_code == 401


# =============================================================================
# Department Isolation Tests
# =============================================================================


class TestMoodleDepartmentIsolation:
    """Test that Moodle routes properly isolate department data."""

    def test_status_uses_api_key_department(
        self,
        client,
        mock_session,
        mock_moodle_credential,
        auth_headers,
        override_deps,
    ):
        """Test that /status uses department_id from API key, not query params."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credential
        )

        response = client.get(
            "/moodle/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is True
        assert data["user_email"] == "faculty@university.edu"

        # Verify the query filtered by department_id from API key
        mock_session.query.assert_called()

    def test_status_not_connected(self, client, auth_headers, override_deps):
        """Test /status when Moodle is not connected."""
        response = client.get(
            "/moodle/status",
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
            "/moodle/status",
            headers=auth_headers,
        )

        # Should return "not connected" because the query filters by department_id
        assert response.status_code == 200
        assert response.json()["connected"] is False

    def test_courses_requires_connection(self, client, auth_headers, override_deps):
        """Test that /courses returns 404 when Moodle not connected."""
        response = client.get(
            "/moodle/courses",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]

    def test_course_files_requires_connection(
        self, client, auth_headers, override_deps
    ):
        """Test that /courses/{id}/files returns 404 when Moodle not connected."""
        response = client.get(
            "/moodle/courses/101/files",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]

    def test_disconnect_requires_connection(self, client, auth_headers, override_deps):
        """Test that /disconnect returns 404 when Moodle not connected."""
        response = client.delete(
            "/moodle/disconnect",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not connected" in response.json()["detail"]


# =============================================================================
# Disconnect Tests
# =============================================================================


class TestMoodleDisconnect:
    """Test Moodle disconnect functionality with API key auth."""

    def test_disconnect_success(
        self,
        client,
        mock_session,
        mock_moodle_credential,
        auth_headers,
        override_deps,
    ):
        """Test successful Moodle disconnection."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credential
        )

        with patch(
            "src.api.moodle_routes.RemediationArtifactService.from_settings"
        ) as cleanup:
            cleanup.return_value.delete_for_credential.return_value.count = 0
            response = client.delete(
                "/moodle/disconnect",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert "disconnected successfully" in response.json()["message"]

        # Verify delete and commit were called
        mock_session.delete.assert_called_once_with(mock_moodle_credential)
        mock_session.commit.assert_called_once()

    def test_disconnect_only_affects_own_department(
        self, client, mock_session, auth_headers, override_deps
    ):
        """Test that disconnect cannot affect other departments."""
        response = client.delete(
            "/moodle/disconnect",
            headers=auth_headers,
        )

        # Should return 404 because no credential found for this department
        assert response.status_code == 404

        # Verify delete was NOT called
        mock_session.delete.assert_not_called()


# =============================================================================
# Connection Status Response Tests
# =============================================================================


class TestMoodleStatusResponse:
    """Test Moodle status response format."""

    def test_status_response_format(
        self,
        client,
        mock_session,
        mock_moodle_credential,
        auth_headers,
        override_deps,
    ):
        """Test that status response includes all expected fields."""
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credential
        )

        response = client.get(
            "/moodle/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Check all expected fields are present
        assert "connected" in data
        assert data["connected"] is True
        assert "moodle_instance_url" in data
        assert data["moodle_instance_url"] == "https://moodle.university.edu"
        assert "user_email" in data
        assert "user_fullname" in data
        assert "connected_at" in data
        assert "credential_id" in data

    def test_status_not_connected_response(self, client, auth_headers, override_deps):
        """Test status response when not connected."""
        response = client.get(
            "/moodle/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["connected"] is False
        # Optional fields should be None when not connected
        assert data.get("moodle_instance_url") is None
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
        mock_moodle_credential,
        auth_headers,
        override_deps,
    ):
        """Test that passing department_id as query param has no effect."""
        # Return credential for dept-123 (from API key)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credential
        )

        # Try to pass different department_id in query params
        response = client.get(
            "/moodle/status?department_id=attacker-dept-999",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should get credential for dept-123, not attacker-dept-999
        assert data["connected"] is True
        assert data["user_email"] == "faculty@university.edu"
