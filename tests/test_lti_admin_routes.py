"""
Tests for LTI Admin CRUD Routes

Tests cover:
- List LTI registrations for a department
- Create new LTI registration
- Update LTI registration (enable/disable, rename)
- Delete LTI registration
- Error cases (not found, conflict, invalid platform)
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Import app for testing
from src.api.main import app
from src.db.models import LTIRegistration, LTIPlatform

# LTI admin CRUD router (lti_admin_routes.py) does not exist yet
pytestmark = pytest.mark.skip(reason="LTI admin CRUD router not yet implemented")


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_api_key():
    """Mock API key for authentication."""
    api_key = MagicMock()
    api_key.id = "api-key-123"
    api_key.user_id = "user-123"
    api_key.department_id = "dept-123"
    return api_key


@pytest.fixture
def auth_headers():
    """Headers with mock authentication."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def mock_lti_registration():
    """Create a mock LTI registration."""
    reg = MagicMock(spec=LTIRegistration)
    reg.id = "reg-123"
    reg.department_id = "dept-123"
    reg.platform = LTIPlatform.CANVAS
    reg.platform_name = "University Canvas"
    reg.issuer = "https://canvas.university.edu"
    reg.client_id = "canvas-client-12345"
    reg.deployment_id = "deployment-1"
    reg.is_active = True
    reg.launch_count = 10
    reg.last_launch_at = datetime.now(timezone.utc)
    reg.created_at = datetime.now(timezone.utc)
    return reg


@pytest.fixture
def mock_lti_registration_blackboard():
    """Create a mock Blackboard LTI registration."""
    reg = MagicMock(spec=LTIRegistration)
    reg.id = "reg-456"
    reg.department_id = "dept-123"
    reg.platform = LTIPlatform.BLACKBOARD
    reg.platform_name = "University Blackboard"
    reg.issuer = "https://blackboard.university.edu"
    reg.client_id = "bb-client-67890"
    reg.deployment_id = None
    reg.is_active = True
    reg.launch_count = 5
    reg.last_launch_at = None
    reg.created_at = datetime.now(timezone.utc)
    return reg


# =============================================================================
# List LTI Registrations Tests
# =============================================================================


class TestListLTIRegistrations:
    """Tests for GET /integrations/lti/registrations."""

    def test_list_missing_auth(self, client):
        """Test that listing registrations requires authentication."""
        response = client.get("/integrations/lti/registrations")
        assert response.status_code == 401

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_list_empty(self, mock_db, mock_auth, client, mock_api_key, auth_headers):
        """Test listing when no registrations exist."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
            []
        )
        mock_db.return_value = mock_session

        response = client.get(
            "/integrations/lti/registrations",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["registrations"] == []
        assert data["total"] == 0

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_list_with_registrations(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        mock_lti_registration_blackboard,
        auth_headers,
    ):
        """Test listing multiple registrations."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_lti_registration,
            mock_lti_registration_blackboard,
        ]
        mock_db.return_value = mock_session

        response = client.get(
            "/integrations/lti/registrations",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["registrations"]) == 2

        # Check first registration
        reg1 = data["registrations"][0]
        assert reg1["id"] == "reg-123"
        assert reg1["platform"] == "canvas"
        assert reg1["client_id"] == "canvas-client-12345"
        assert reg1["is_active"] is True
        assert reg1["launch_count"] == 10


# =============================================================================
# Create LTI Registration Tests
# =============================================================================


class TestCreateLTIRegistration:
    """Tests for POST /integrations/lti/registrations."""

    def test_create_missing_auth(self, client):
        """Test that creating a registration requires authentication."""
        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "issuer": "https://canvas.university.edu",
                "client_id": "test-client-id",
            },
        )
        assert response.status_code == 401

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_success(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test successful LTI registration creation."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        # No existing registration
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        # Mock the refresh to set created_at
        def mock_refresh(obj):
            obj.created_at = datetime.now(timezone.utc)

        mock_session.refresh = mock_refresh

        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "platform_name": "Test University Canvas",
                "issuer": "https://canvas.test.edu",
                "client_id": "new-client-123",
                "deployment_id": "deploy-1",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "canvas"
        assert data["platform_name"] == "Test University Canvas"
        assert data["issuer"] == "https://canvas.test.edu"
        assert data["client_id"] == "new-client-123"
        assert data["is_active"] is True
        assert "id" in data
        assert "message" in data

        # Verify add and commit were called
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_invalid_platform(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test creating registration with invalid platform."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_db.return_value = mock_session

        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "invalid_platform",
                "issuer": "https://invalid.edu",
                "client_id": "test-client",
            },
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "Invalid platform" in response.json()["detail"]

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_duplicate_same_department(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        auth_headers,
    ):
        """Test creating duplicate registration in same department."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        # Return existing registration with same department
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_lti_registration
        )
        mock_db.return_value = mock_session

        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "issuer": "https://canvas.university.edu",
                "client_id": "canvas-client-12345",  # Same as mock
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_duplicate_different_department(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test creating duplicate registration from another department."""
        mock_auth.return_value = mock_api_key

        # Mock existing registration from different department
        existing_reg = MagicMock(spec=LTIRegistration)
        existing_reg.department_id = "other-dept-456"  # Different department

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            existing_reg
        )
        mock_db.return_value = mock_session

        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "issuer": "https://canvas.other.edu",
                "client_id": "already-registered-client",
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert "another department" in response.json()["detail"]

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_all_platforms(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test creating registrations for all supported platforms."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        def mock_refresh(obj):
            obj.created_at = datetime.now(timezone.utc)

        mock_session.refresh = mock_refresh

        platforms = ["canvas", "blackboard", "moodle", "brightspace"]

        for platform in platforms:
            response = client.post(
                "/integrations/lti/registrations",
                json={
                    "platform": platform,
                    "issuer": f"https://{platform}.university.edu",
                    "client_id": f"{platform}-client-123",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["platform"] == platform


# =============================================================================
# Update LTI Registration Tests
# =============================================================================


class TestUpdateLTIRegistration:
    """Tests for PATCH /integrations/lti/registrations/{registration_id}."""

    def test_update_missing_auth(self, client):
        """Test that updating a registration requires authentication."""
        response = client.patch(
            "/integrations/lti/registrations/reg-123",
            params={"is_active": False},
        )
        assert response.status_code == 401

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_update_deactivate(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        auth_headers,
    ):
        """Test deactivating an LTI registration."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_lti_registration
        )
        mock_db.return_value = mock_session

        response = client.patch(
            "/integrations/lti/registrations/reg-123",
            params={"is_active": False},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "reg-123"
        assert "message" in data

        # Verify the registration was modified
        assert mock_lti_registration.is_active is False
        mock_session.commit.assert_called_once()

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_update_rename(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        auth_headers,
    ):
        """Test renaming an LTI registration."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_lti_registration
        )
        mock_db.return_value = mock_session

        response = client.patch(
            "/integrations/lti/registrations/reg-123",
            params={"platform_name": "New Platform Name"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["platform_name"] == "New Platform Name"

        mock_session.commit.assert_called_once()

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_update_not_found(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test updating a non-existent registration."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        response = client.patch(
            "/integrations/lti/registrations/nonexistent-id",
            params={"is_active": False},
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_update_wrong_department(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test updating a registration owned by another department."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        # The filter should return None because department_id doesn't match
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        response = client.patch(
            "/integrations/lti/registrations/other-dept-reg",
            params={"is_active": False},
            headers=auth_headers,
        )

        assert response.status_code == 404


# =============================================================================
# Delete LTI Registration Tests
# =============================================================================


class TestDeleteLTIRegistration:
    """Tests for DELETE /integrations/lti/registrations/{registration_id}."""

    def test_delete_missing_auth(self, client):
        """Test that deleting a registration requires authentication."""
        response = client.delete("/integrations/lti/registrations/reg-123")
        assert response.status_code == 401

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_delete_success(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        auth_headers,
    ):
        """Test successful LTI registration deletion."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_lti_registration
        )
        mock_db.return_value = mock_session

        response = client.delete(
            "/integrations/lti/registrations/reg-123",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["platform"] == "canvas"
        assert data["client_id"] == "canvas-client-12345"

        # Verify delete and commit were called
        mock_session.delete.assert_called_once_with(mock_lti_registration)
        mock_session.commit.assert_called_once()

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_delete_not_found(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test deleting a non-existent registration."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        response = client.delete(
            "/integrations/lti/registrations/nonexistent-id",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_delete_wrong_department(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test deleting a registration owned by another department."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        # Filter returns None because department_id doesn't match
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        response = client.delete(
            "/integrations/lti/registrations/other-dept-reg",
            headers=auth_headers,
        )

        assert response.status_code == 404


# =============================================================================
# Edge Cases and Security Tests
# =============================================================================


class TestLTIAdminSecurity:
    """Security-focused tests for LTI admin endpoints."""

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_cannot_access_other_department_registrations(
        self, mock_db, mock_auth, client, auth_headers
    ):
        """Test that users cannot list registrations from other departments."""
        # API key for dept-123
        api_key = MagicMock()
        api_key.department_id = "dept-123"
        mock_auth.return_value = api_key

        # Registration from different department
        other_dept_reg = MagicMock(spec=LTIRegistration)
        other_dept_reg.department_id = "dept-other"

        mock_session = MagicMock()
        # Query should filter by department_id
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
            []
        )
        mock_db.return_value = mock_session

        response = client.get(
            "/integrations/lti/registrations",
            headers=auth_headers,
        )

        # Should return empty list (not the other department's registrations)
        assert response.status_code == 200
        assert response.json()["registrations"] == []

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_registration_includes_launch_stats(
        self,
        mock_db,
        mock_auth,
        client,
        mock_api_key,
        mock_lti_registration,
        auth_headers,
    ):
        """Test that registration listing includes launch statistics."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_lti_registration
        ]
        mock_db.return_value = mock_session

        response = client.get(
            "/integrations/lti/registrations",
            headers=auth_headers,
        )

        assert response.status_code == 200
        reg = response.json()["registrations"][0]
        assert "launch_count" in reg
        assert "last_launch_at" in reg
        assert reg["launch_count"] == 10

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_create_requires_issuer_and_client_id(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test that issuer and client_id are required for creation."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_db.return_value = mock_session

        # Missing issuer
        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "client_id": "test-client",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422  # Validation error

        # Missing client_id
        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "canvas",
                "issuer": "https://canvas.edu",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422  # Validation error

    @patch("src.api.integration_routes.get_current_api_key")
    @patch("src.api.integration_routes.get_db_dependency")
    def test_platform_is_case_insensitive(
        self, mock_db, mock_auth, client, mock_api_key, auth_headers
    ):
        """Test that platform name is case-insensitive."""
        mock_auth.return_value = mock_api_key

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db.return_value = mock_session

        def mock_refresh(obj):
            obj.created_at = datetime.now(timezone.utc)

        mock_session.refresh = mock_refresh

        # Test uppercase
        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "CANVAS",
                "issuer": "https://canvas.university.edu",
                "client_id": "uppercase-test",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["platform"] == "canvas"

        # Test mixed case
        response = client.post(
            "/integrations/lti/registrations",
            json={
                "platform": "BlackBoard",
                "issuer": "https://bb.university.edu",
                "client_id": "mixedcase-test",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["platform"] == "blackboard"
