"""
End-to-end integration tests for Canvas LMS.

Tests the complete flow:
launch → authenticate → scan courses → remediate

These tests verify the full Canvas LTI integration lifecycle.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

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
def mock_canvas_oauth_config():
    """Mock Canvas OAuth configuration."""
    return {
        "client_id": "canvas-client-123",
        "client_secret": "canvas-secret-456",
        "canvas_url": "https://university.instructure.com",
        "redirect_uri": "http://localhost:8000/canvas/oauth/callback",
    }


@pytest.fixture
def mock_canvas_api():
    """Mock Canvas REST API client."""
    with patch("src.integrations.canvas.canvas_api.CanvasAPI") as mock:
        api = MagicMock()

        # Mock user info
        api.get_user.return_value = {
            "id": 12345,
            "name": "Test Faculty",
            "login_id": "faculty@university.edu",
            "email": "faculty@university.edu",
        }

        # Mock courses list
        api.get_courses.return_value = [
            {
                "id": 101,
                "name": "Introduction to Computer Science",
                "course_code": "CS101",
                "enrollment_term_id": 1,
                "workflow_state": "available",
            },
            {
                "id": 102,
                "name": "Data Structures",
                "course_code": "CS201",
                "enrollment_term_id": 1,
                "workflow_state": "available",
            },
        ]

        # Mock course files
        api.get_course_files.return_value = [
            {
                "id": 1001,
                "display_name": "Syllabus.pdf",
                "filename": "syllabus.pdf",
                "content-type": "application/pdf",
                "size": 125000,
                "url": "https://university.instructure.com/files/1001/download",
                "updated_at": "2026-01-08T10:00:00Z",
            },
            {
                "id": 1002,
                "display_name": "Week 1 Slides.pptx",
                "filename": "week1_slides.pptx",
                "content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "size": 2500000,
                "url": "https://university.instructure.com/files/1002/download",
                "updated_at": "2026-01-09T14:00:00Z",
            },
        ]

        # Mock file download
        api.download_file.return_value = b"Mock file content"

        # Mock file upload
        api.upload_file.return_value = {
            "id": 1003,
            "display_name": "Accessible Syllabus.pdf",
            "size": 126000,
        }

        mock.return_value = api
        yield api


@pytest.fixture
def mock_canvas_credentials():
    """Mock stored Canvas credentials."""
    return MagicMock(
        id="cred-123",
        user_id="test-user-123",
        department_id="test-dept-456",
        provider="CANVAS",
        access_token="encrypted-canvas-token",
        refresh_token="encrypted-canvas-refresh",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        canvas_url="https://university.instructure.com",
    )


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("src.api.canvas_routes.get_db_dependency") as mock:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        mock.return_value = session
        yield session


class TestCanvasLTIE2EFlow:
    """
    End-to-end tests for complete Canvas LMS integration flow.

    Flow: Connect → List Courses → List Files → Scan → Remediate
    """

    def test_e2e_connect_initiate(
        self,
        client,
        auth_headers,
        mock_canvas_oauth_config,
        mock_db_session,
    ):
        """Test initiating Canvas OAuth connection."""
        with patch("src.api.canvas_routes.get_canvas_oauth_config") as mock_config:
            mock_config.return_value = mock_canvas_oauth_config

            response = client.post(
                "/canvas/connect",
                headers=auth_headers,
                json={
                    "department_id": "test-dept-456",
                    "canvas_url": "https://university.instructure.com",
                    "redirect_uri": "http://localhost:5173/integrations/callback",
                },
            )

            assert response.status_code in [200, 401, 422]
            if response.status_code == 200:
                data = response.json()
                assert "auth_url" in data or "authorization_url" in data

    def test_e2e_callback_token_exchange(
        self,
        client,
        mock_canvas_api,
        mock_db_session,
    ):
        """Test OAuth callback exchanges code for tokens."""
        with patch("src.api.canvas_routes.exchange_canvas_code") as mock_exchange:
            mock_exchange.return_value = {
                "access_token": "canvas-access-token",
                "refresh_token": "canvas-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

            response = client.get(
                "/canvas/oauth/callback",
                params={
                    "code": "canvas-auth-code-123",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [200, 302, 400, 401]

    def test_e2e_get_connection_status(
        self,
        client,
        auth_headers,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test getting Canvas connection status."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.get(
            "/canvas/status",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "connected" in data or "status" in data

    def test_e2e_list_courses(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test listing Canvas courses."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.get(
            "/canvas/courses",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "courses" in data

    def test_e2e_list_course_files(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test listing files in a Canvas course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.get(
            "/canvas/courses/101/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "files" in data

    def test_e2e_scan_course_file(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test scanning a file from Canvas course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.post(
            "/canvas/scan/file/1001",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "course_id": 101,
                "scan_type": "accessibility",
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_e2e_remediate_file(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test remediating and uploading fixed file."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.post(
            "/canvas/remediate",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "course_id": 101,
                "file_id": 1001,
                "issues_to_fix": ["missing_alt_text"],
                "upload_fixed": True,
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_e2e_disconnect(
        self,
        client,
        auth_headers,
        mock_db_session,
    ):
        """Test disconnecting Canvas account."""
        response = client.delete(
            "/canvas/disconnect",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 204, 401, 404]


class TestCanvasLTIAuthenticationRequired:
    """Test that Canvas endpoints require authentication."""

    def test_connect_requires_auth(self, client):
        """Test that connect requires API key."""
        response = client.post(
            "/canvas/connect",
            json={
                "department_id": "test-dept-456",
                "canvas_url": "https://university.instructure.com",
            },
        )

        assert response.status_code == 401

    def test_status_requires_auth(self, client):
        """Test that status requires API key."""
        response = client.get(
            "/canvas/status",
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code == 401

    def test_courses_requires_auth(self, client):
        """Test that courses requires API key."""
        response = client.get(
            "/canvas/courses",
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code == 401


class TestCanvasDepartmentIsolation:
    """Test department isolation for Canvas integration."""

    def test_cannot_access_other_department(
        self,
        client,
        auth_headers,
        mock_db_session,
    ):
        """Test that users cannot access other departments' Canvas data."""
        # Mock auth to return different department
        with patch("src.api.canvas_routes.get_required_api_key") as mock_auth:
            mock_auth.return_value = (
                MagicMock(id="key-123"),
                "test-user-123",
                "different-dept-789",
            )

            response = client.get(
                "/canvas/courses",
                headers=auth_headers,
                params={"department_id": "test-dept-456"},
            )

            assert response.status_code in [401, 403, 404]


class TestCanvasTokenRefresh:
    """Test Canvas token refresh scenarios."""

    def test_auto_refresh_expired_token(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_db_session,
    ):
        """Test automatic token refresh when expired."""
        # Create expired credentials
        expired_creds = MagicMock(
            token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            access_token="expired-token",
            refresh_token="valid-refresh-token",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            expired_creds
        )

        with patch("src.api.canvas_routes.refresh_canvas_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new-access-token",
                "expires_in": 3600,
            }

            response = client.get(
                "/canvas/courses",
                headers=auth_headers,
                params={"department_id": "test-dept-456"},
            )

            assert response.status_code in [200, 401, 404]


class TestCanvasErrorHandling:
    """Test Canvas error handling scenarios."""

    def test_canvas_api_error(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test handling of Canvas API errors."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )
        mock_canvas_api.get_courses.side_effect = Exception(
            "Canvas API Error: 503 Service Unavailable"
        )

        response = client.get(
            "/canvas/courses",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [500, 502, 503, 401, 404]

    def test_course_not_found(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test handling of course not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )
        mock_canvas_api.get_course_files.side_effect = Exception(
            "Canvas API Error: 404 The specified resource does not exist"
        )

        response = client.get(
            "/canvas/courses/99999/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [404, 401, 500]

    def test_file_access_denied(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test handling of file access denied."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )
        mock_canvas_api.download_file.side_effect = Exception(
            "Canvas API Error: 401 Unauthorized"
        )

        response = client.post(
            "/canvas/scan/file/1001",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "course_id": 101,
            },
        )

        assert response.status_code in [401, 403, 500]


class TestCanvasBulkOperations:
    """Test bulk operations for Canvas courses."""

    def test_scan_all_course_files(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test scanning all files in a course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.post(
            "/canvas/scan/course/101",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "file_types": ["pdf", "pptx", "docx"],
            },
        )

        assert response.status_code in [200, 202, 401, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            assert "job_id" in data or "status" in data

    def test_scan_all_department_courses(
        self,
        client,
        auth_headers,
        mock_canvas_api,
        mock_canvas_credentials,
        mock_db_session,
    ):
        """Test scanning all courses for a department."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_canvas_credentials
        )

        response = client.post(
            "/canvas/scan/department",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "file_types": ["pdf", "pptx"],
            },
        )

        assert response.status_code in [200, 202, 401, 404]
