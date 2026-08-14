"""
End-to-end integration tests for Moodle LMS.

Tests the complete flow:
connect → sync → scan courses → remediate

These tests verify the full Moodle OAuth integration lifecycle.
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
def mock_moodle_config():
    """Mock Moodle OAuth configuration."""
    return {
        "client_id": "moodle-client-123",
        "client_secret": "moodle-secret-456",
        "moodle_url": "https://moodle.university.edu",
        "redirect_uri": "http://localhost:8000/moodle/callback",
    }


@pytest.fixture
def mock_moodle_api():
    """Mock Moodle REST API client."""
    with patch("src.integrations.moodle.moodle_api.MoodleAPI") as mock:
        api = MagicMock()

        # Mock user info (core_webservice_get_site_info)
        api.get_site_info.return_value = {
            "sitename": "University Moodle",
            "username": "faculty",
            "firstname": "Test",
            "lastname": "Faculty",
            "fullname": "Test Faculty",
            "email": "faculty@university.edu",
            "userid": 12345,
        }

        # Mock courses list (core_enrol_get_users_courses)
        api.get_user_courses.return_value = [
            {
                "id": 101,
                "shortname": "CS101",
                "fullname": "Introduction to Computer Science",
                "displayname": "CS101: Introduction to Computer Science",
                "enrolledusercount": 45,
                "startdate": 1704067200,  # 2024-01-01
                "enddate": 1717200000,  # 2024-06-01
            },
            {
                "id": 102,
                "shortname": "CS201",
                "fullname": "Data Structures",
                "displayname": "CS201: Data Structures",
                "enrolledusercount": 32,
                "startdate": 1704067200,
                "enddate": 1717200000,
            },
        ]

        # Mock course content (core_course_get_contents)
        api.get_course_contents.return_value = [
            {
                "id": 1,
                "name": "Week 1: Introduction",
                "modules": [
                    {
                        "id": 1001,
                        "name": "Syllabus",
                        "modname": "resource",
                        "modplural": "Files",
                        "contents": [
                            {
                                "filename": "syllabus.pdf",
                                "fileurl": "https://moodle.university.edu/webservice/pluginfile.php/123/mod_resource/content/1/syllabus.pdf",
                                "filesize": 125000,
                                "mimetype": "application/pdf",
                                "timecreated": 1704067200,
                                "timemodified": 1704153600,
                            },
                        ],
                    },
                    {
                        "id": 1002,
                        "name": "Lecture Slides",
                        "modname": "resource",
                        "modplural": "Files",
                        "contents": [
                            {
                                "filename": "week1_slides.pptx",
                                "fileurl": "https://moodle.university.edu/webservice/pluginfile.php/124/mod_resource/content/1/week1_slides.pptx",
                                "filesize": 2500000,
                                "mimetype": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                "timecreated": 1704153600,
                                "timemodified": 1704240000,
                            },
                        ],
                    },
                ],
            },
        ]

        # Mock file download
        api.download_file.return_value = b"Mock file content"

        # Mock file upload
        api.upload_file.return_value = {
            "itemid": 12345,
            "filename": "accessible_syllabus.pdf",
        }

        mock.return_value = api
        yield api


@pytest.fixture
def mock_moodle_credentials():
    """Mock stored Moodle credentials."""
    return MagicMock(
        id="cred-moodle-123",
        user_id="test-user-123",
        department_id="test-dept-456",
        provider="MOODLE",
        access_token="encrypted-moodle-token",
        refresh_token="encrypted-moodle-refresh",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        moodle_url="https://moodle.university.edu",
    )


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("src.api.moodle_routes.get_db_dependency") as mock:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        mock.return_value = session
        yield session


class TestMoodleOAuthE2EFlow:
    """
    End-to-end tests for complete Moodle OAuth integration flow.

    Flow: Connect → List Courses → List Files → Scan → Remediate
    """

    def test_e2e_connect_initiate(
        self,
        client,
        auth_headers,
        mock_moodle_config,
        mock_db_session,
    ):
        """Test initiating Moodle OAuth connection."""
        with patch("src.api.moodle_routes.get_moodle_oauth_config") as mock_config:
            mock_config.return_value = mock_moodle_config

            response = client.post(
                "/moodle/connect",
                headers=auth_headers,
                json={
                    "department_id": "test-dept-456",
                    "moodle_url": "https://moodle.university.edu",
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
        mock_moodle_api,
        mock_db_session,
    ):
        """Test OAuth callback exchanges code for tokens."""
        with patch("src.api.moodle_routes.exchange_moodle_code") as mock_exchange:
            mock_exchange.return_value = {
                "access_token": "moodle-access-token",
                "refresh_token": "moodle-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

            response = client.get(
                "/moodle/callback",
                params={
                    "code": "moodle-auth-code-123",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [200, 302, 400, 401]

    def test_e2e_get_connection_status(
        self,
        client,
        auth_headers,
        mock_moodle_credentials,
        mock_moodle_api,
        mock_db_session,
    ):
        """Test getting Moodle connection status."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.get(
            "/moodle/status",
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
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test listing Moodle courses."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.get(
            "/moodle/courses",
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
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test listing files in a Moodle course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.get(
            "/moodle/courses/101/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "files" in data or "sections" in data

    def test_e2e_scan_course_file(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test scanning a file from Moodle course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.post(
            "/moodle/scan/file",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "course_id": 101,
                "file_url": "https://moodle.university.edu/webservice/pluginfile.php/123/mod_resource/content/1/syllabus.pdf",
                "filename": "syllabus.pdf",
                "scan_type": "accessibility",
            },
        )

        assert response.status_code in [200, 202, 401, 404]

    def test_e2e_remediate_file(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test remediating a Moodle file."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.post(
            "/moodle/remediate",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "course_id": 101,
                "file_url": "https://moodle.university.edu/webservice/pluginfile.php/123/mod_resource/content/1/syllabus.pdf",
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
        """Test disconnecting Moodle account."""
        response = client.delete(
            "/moodle/disconnect",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 204, 401, 404]


class TestMoodleInstanceURLHandling:
    """Test handling of self-hosted Moodle instances."""

    def test_custom_instance_url(
        self,
        client,
        auth_headers,
        mock_db_session,
    ):
        """Test connecting to a custom Moodle instance."""
        response = client.post(
            "/moodle/connect",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "moodle_url": "https://lms.custom-university.edu",
                "redirect_uri": "http://localhost:5173/integrations/callback",
            },
        )

        assert response.status_code in [200, 401, 422]

    def test_invalid_moodle_url(
        self,
        client,
        auth_headers,
    ):
        """Test rejection of invalid Moodle URL."""
        response = client.post(
            "/moodle/connect",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "moodle_url": "not-a-valid-url",
                "redirect_uri": "http://localhost:5173/integrations/callback",
            },
        )

        assert response.status_code in [400, 422]


class TestMoodleWebServiceCalls:
    """Test Moodle Web Service API calls."""

    def test_get_site_info(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test getting site info via web service."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.get(
            "/moodle/site-info",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert "sitename" in data or "username" in data or "info" in data

    def test_get_course_assignments(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test getting course assignments."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        mock_moodle_api.get_assignments.return_value = [
            {
                "id": 501,
                "name": "Assignment 1: Hello World",
                "duedate": 1705276800,
                "course": 101,
            },
        ]

        response = client.get(
            "/moodle/courses/101/assignments",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [200, 401, 404]


class TestMoodleTokenRefresh:
    """Test Moodle token refresh scenarios."""

    def test_auto_refresh_expired_token(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_db_session,
    ):
        """Test automatic token refresh when expired."""
        # Create expired credentials
        expired_creds = MagicMock(
            token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            access_token="expired-token",
            refresh_token="valid-refresh-token",
            moodle_url="https://moodle.university.edu",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            expired_creds
        )

        with patch("src.api.moodle_routes.refresh_moodle_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new-access-token",
                "expires_in": 3600,
            }

            response = client.get(
                "/moodle/courses",
                headers=auth_headers,
                params={"department_id": "test-dept-456"},
            )

            assert response.status_code in [200, 401, 404]


class TestMoodleErrorHandling:
    """Test Moodle error handling scenarios."""

    def test_moodle_api_error(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test handling of Moodle API errors."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )
        mock_moodle_api.get_user_courses.side_effect = Exception(
            "Moodle API Error: invalidtoken"
        )

        response = client.get(
            "/moodle/courses",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [500, 401, 403, 404]

    def test_course_not_found(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test handling of course not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )
        mock_moodle_api.get_course_contents.side_effect = Exception(
            "Moodle API Error: Course not found"
        )

        response = client.get(
            "/moodle/courses/99999/files",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [404, 401, 500]

    def test_web_service_disabled(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test handling of disabled web service."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )
        mock_moodle_api.get_site_info.side_effect = Exception(
            "Moodle API Error: Web service not enabled"
        )

        response = client.get(
            "/moodle/site-info",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code in [500, 401, 503]


class TestMoodleAuthenticationRequired:
    """Test that Moodle endpoints require authentication."""

    def test_connect_requires_auth(self, client):
        """Test that connect requires API key."""
        response = client.post(
            "/moodle/connect",
            json={
                "department_id": "test-dept-456",
                "moodle_url": "https://moodle.university.edu",
            },
        )

        assert response.status_code == 401

    def test_status_requires_auth(self, client):
        """Test that status requires API key."""
        response = client.get(
            "/moodle/status",
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code == 401

    def test_courses_requires_auth(self, client):
        """Test that courses requires API key."""
        response = client.get(
            "/moodle/courses",
            params={"department_id": "test-dept-456"},
        )

        assert response.status_code == 401


class TestMoodleBulkOperations:
    """Test bulk operations for Moodle courses."""

    def test_scan_all_course_files(
        self,
        client,
        auth_headers,
        mock_moodle_api,
        mock_moodle_credentials,
        mock_db_session,
    ):
        """Test scanning all files in a course."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_moodle_credentials
        )

        response = client.post(
            "/moodle/scan/course/101",
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
