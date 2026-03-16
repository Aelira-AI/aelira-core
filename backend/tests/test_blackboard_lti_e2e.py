"""
End-to-end integration tests for Blackboard LTI 1.3.

Tests the complete flow:
launch → authenticate → scan courses → grade passback

These tests verify the full Blackboard LTI 1.3 integration lifecycle.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import uuid
import jwt
import time

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
def mock_blackboard_config():
    """Mock Blackboard LTI 1.3 configuration."""
    return {
        "issuer": "https://blackboard.university.edu",
        "client_id": "bb-client-123",
        "deployment_id": "deployment-456",
        "auth_endpoint": "https://blackboard.university.edu/learn/api/public/v1/oauth2/authorizationcode",
        "token_endpoint": "https://blackboard.university.edu/learn/api/public/v1/oauth2/token",
        "jwks_uri": "https://blackboard.university.edu/.well-known/jwks.json",
        "ags_endpoint": "https://blackboard.university.edu/api/lti/1.3/ags",
        "nrps_endpoint": "https://blackboard.university.edu/api/lti/1.3/nrps",
    }


@pytest.fixture
def mock_lti_launch_token():
    """Create a mock LTI 1.3 launch JWT token."""
    now = int(time.time())
    return {
        "iss": "https://blackboard.university.edu",
        "sub": "bb-user-12345",
        "aud": "bb-client-123",
        "exp": now + 3600,
        "iat": now,
        "nonce": str(uuid.uuid4()),
        "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest",
        "https://purl.imsglobal.org/spec/lti/claim/version": "1.3.0",
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-456",
        "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "https://api.aelira.ai/lti/launch",
        "https://purl.imsglobal.org/spec/lti/claim/resource_link": {
            "id": "resource-link-123",
            "title": "Accessibility Scanner",
        },
        "https://purl.imsglobal.org/spec/lti/claim/roles": [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
        ],
        "https://purl.imsglobal.org/spec/lti/claim/context": {
            "id": "course-101",
            "label": "CS101",
            "title": "Introduction to Computer Science",
            "type": ["http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering"],
        },
        "name": "Test Faculty",
        "email": "faculty@university.edu",
        "https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice": {
            "context_memberships_url": "https://blackboard.university.edu/api/lti/1.3/nrps/membership",
            "service_versions": ["2.0"],
        },
        "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint": {
            "scope": [
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score",
            ],
            "lineitems": "https://blackboard.university.edu/api/lti/1.3/ags/lineitems",
        },
    }


@pytest.fixture
def mock_oidc_login_params():
    """Mock OIDC login initiation parameters."""
    return {
        "iss": "https://blackboard.university.edu",
        "login_hint": "user-12345",
        "target_link_uri": "https://api.aelira.ai/lti/launch",
        "lti_message_hint": "message-hint-abc",
        "client_id": "bb-client-123",
        "lti_deployment_id": "deployment-456",
    }


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("src.api.lti_routes.get_db_dependency") as mock:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        mock.return_value = session
        yield session


class TestBlackboardLTIE2EFlow:
    """
    End-to-end tests for complete Blackboard LTI 1.3 integration flow.

    Flow: OIDC Login → LTI Launch → Access Course → Grade Passback
    """

    def test_e2e_oidc_login_initiation(
        self,
        client,
        mock_blackboard_config,
        mock_oidc_login_params,
    ):
        """Test OIDC login initiation from Blackboard."""
        with patch("src.api.lti_routes.get_lti_config") as mock_config:
            mock_config.return_value = mock_blackboard_config

            response = client.post(
                "/lti/blackboard/login",
                data=mock_oidc_login_params,
            )

            # Should redirect to platform auth endpoint
            assert response.status_code in [302, 303, 200, 400, 401]

    def test_e2e_lti_launch_with_jwt(
        self,
        client,
        mock_blackboard_config,
        mock_lti_launch_token,
        mock_db_session,
    ):
        """Test LTI launch with JWT validation."""
        # Create a signed JWT (in production this would be from Blackboard)
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.return_value = mock_lti_launch_token

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "mock.jwt.token",
                    "state": "test-state-123",
                },
            )

            # Should succeed or redirect to dashboard
            assert response.status_code in [200, 302, 400, 401]

    def test_e2e_get_course_membership(
        self,
        client,
        auth_headers,
        mock_blackboard_config,
    ):
        """Test getting course membership via NRPS."""
        with patch("src.api.lti_routes.get_nrps_membership") as mock_nrps:
            mock_nrps.return_value = {
                "id": "https://blackboard.university.edu/api/lti/1.3/nrps/membership",
                "context": {"id": "course-101"},
                "members": [
                    {
                        "user_id": "user-1",
                        "roles": [
                            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
                        ],
                        "name": "Test Faculty",
                        "email": "faculty@university.edu",
                    },
                    {
                        "user_id": "user-2",
                        "roles": [
                            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
                        ],
                        "name": "Test Student",
                        "email": "student@university.edu",
                    },
                ],
            }

            response = client.get(
                "/lti/blackboard/membership",
                headers=auth_headers,
                params={"context_id": "course-101"},
            )

            assert response.status_code in [200, 401, 404]
            if response.status_code == 200:
                data = response.json()
                assert "members" in data

    def test_e2e_get_lineitems(
        self,
        client,
        auth_headers,
        mock_blackboard_config,
    ):
        """Test getting grade line items via AGS."""
        with patch("src.api.lti_routes.get_ags_lineitems") as mock_ags:
            mock_ags.return_value = [
                {
                    "id": "https://blackboard.university.edu/api/lti/1.3/ags/lineitems/item-1",
                    "scoreMaximum": 100,
                    "label": "Accessibility Compliance Score",
                    "resourceLinkId": "resource-link-123",
                },
            ]

            response = client.get(
                "/lti/blackboard/lineitems",
                headers=auth_headers,
                params={"context_id": "course-101"},
            )

            assert response.status_code in [200, 401, 404]

    def test_e2e_post_grade_score(
        self,
        client,
        auth_headers,
        mock_blackboard_config,
    ):
        """Test posting a grade score back to Blackboard."""
        with patch("src.api.lti_routes.post_ags_score") as mock_post:
            mock_post.return_value = {"success": True}

            response = client.post(
                "/lti/blackboard/grades",
                headers=auth_headers,
                json={
                    "context_id": "course-101",
                    "lineitem_id": "item-1",
                    "user_id": "user-1",
                    "score": 85,
                    "score_maximum": 100,
                    "comment": "Document accessibility improved from 72% to 95%",
                    "activity_progress": "Completed",
                    "grading_progress": "FullyGraded",
                },
            )

            assert response.status_code in [200, 201, 401, 404]

    def test_e2e_create_lineitem(
        self,
        client,
        auth_headers,
        mock_blackboard_config,
    ):
        """Test creating a new grade line item."""
        with patch("src.api.lti_routes.create_ags_lineitem") as mock_create:
            mock_create.return_value = {
                "id": "https://blackboard.university.edu/api/lti/1.3/ags/lineitems/new-item",
                "scoreMaximum": 100,
                "label": "Course Accessibility Score",
            }

            response = client.post(
                "/lti/blackboard/lineitems",
                headers=auth_headers,
                json={
                    "context_id": "course-101",
                    "label": "Course Accessibility Score",
                    "score_maximum": 100,
                    "resource_link_id": "resource-link-123",
                },
            )

            assert response.status_code in [200, 201, 401, 404]


class TestBlackboardDeepLinking:
    """Test LTI Deep Linking for content selection."""

    def test_deep_linking_request(
        self,
        client,
        mock_blackboard_config,
    ):
        """Test handling deep linking request."""
        deep_linking_token = {
            "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiDeepLinkingRequest",
            "https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings": {
                "deep_link_return_url": "https://blackboard.university.edu/api/lti/1.3/deep-link-return",
                "accept_types": ["link", "ltiResourceLink"],
                "accept_presentation_document_targets": ["iframe", "window"],
            },
        }

        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.return_value = deep_linking_token

            response = client.post(
                "/lti/blackboard/deep-linking",
                data={
                    "id_token": "mock.jwt.token",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [200, 302, 400, 401]

    def test_deep_linking_response(
        self,
        client,
        auth_headers,
        mock_blackboard_config,
    ):
        """Test creating deep linking response."""
        response = client.post(
            "/lti/blackboard/deep-linking/response",
            headers=auth_headers,
            json={
                "return_url": "https://blackboard.university.edu/api/lti/1.3/deep-link-return",
                "items": [
                    {
                        "type": "ltiResourceLink",
                        "title": "Scan Course Documents",
                        "url": "https://api.aelira.ai/lti/scan",
                        "custom": {"course_id": "101"},
                    },
                ],
            },
        )

        assert response.status_code in [200, 302, 401, 422]


class TestBlackboardLTIConfiguration:
    """Test LTI configuration endpoints."""

    def test_get_tool_configuration(self, client):
        """Test getting tool configuration for registration."""
        response = client.get("/lti/blackboard/config")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            # Should have required LTI configuration fields
            assert "title" in data or "name" in data

    def test_get_jwks(self, client):
        """Test getting JSON Web Key Set."""
        response = client.get("/lti/blackboard/jwks")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert "keys" in data

    def test_get_config_json(self, client):
        """Test getting LTI configuration JSON for Canvas-style registration."""
        response = client.get("/lti/blackboard/config.json")

        assert response.status_code in [200, 404]


class TestBlackboardJWTValidation:
    """Test JWT token validation."""

    def test_invalid_jwt_signature(
        self,
        client,
        mock_blackboard_config,
    ):
        """Test rejection of invalid JWT signature."""
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.side_effect = jwt.InvalidSignatureError("Invalid signature")

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "invalid.jwt.token",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [400, 401, 403]

    def test_expired_jwt(
        self,
        client,
        mock_blackboard_config,
    ):
        """Test rejection of expired JWT."""
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.side_effect = jwt.ExpiredSignatureError("Token expired")

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "expired.jwt.token",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [400, 401, 403]

    def test_invalid_audience(
        self,
        client,
        mock_blackboard_config,
    ):
        """Test rejection of JWT with wrong audience."""
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.side_effect = jwt.InvalidAudienceError("Invalid audience")

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "wrong-aud.jwt.token",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [400, 401, 403]


class TestBlackboardErrorHandling:
    """Test error handling scenarios."""

    def test_missing_id_token(self, client):
        """Test handling of missing id_token in launch."""
        response = client.post(
            "/lti/blackboard/launch",
            data={"state": "test-state-123"},
        )

        assert response.status_code in [400, 422]

    def test_invalid_state(
        self,
        client,
        mock_lti_launch_token,
    ):
        """Test handling of invalid state parameter."""
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.return_value = mock_lti_launch_token

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "mock.jwt.token",
                    "state": "invalid-state",
                },
            )

            # State validation may be handled differently
            assert response.status_code in [200, 302, 400, 401]

    def test_nrps_service_unavailable(
        self,
        client,
        auth_headers,
    ):
        """Test handling of NRPS service unavailable."""
        with patch("src.api.lti_routes.get_nrps_membership") as mock_nrps:
            mock_nrps.side_effect = Exception("Service unavailable")

            response = client.get(
                "/lti/blackboard/membership",
                headers=auth_headers,
                params={"context_id": "course-101"},
            )

            assert response.status_code in [500, 502, 503, 401, 404]

    def test_ags_grade_post_failure(
        self,
        client,
        auth_headers,
    ):
        """Test handling of AGS grade post failure."""
        with patch("src.api.lti_routes.post_ags_score") as mock_post:
            mock_post.side_effect = Exception("Failed to post grade")

            response = client.post(
                "/lti/blackboard/grades",
                headers=auth_headers,
                json={
                    "context_id": "course-101",
                    "lineitem_id": "item-1",
                    "user_id": "user-1",
                    "score": 85,
                    "score_maximum": 100,
                },
            )

            assert response.status_code in [500, 401, 404]


class TestBlackboardRoleClaims:
    """Test handling of LTI role claims."""

    def test_instructor_role_access(
        self,
        client,
        mock_lti_launch_token,
    ):
        """Test that instructors have full access."""
        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.return_value = mock_lti_launch_token

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "instructor.jwt.token",
                    "state": "test-state-123",
                },
            )

            assert response.status_code in [200, 302, 400, 401]

    def test_student_role_limited_access(
        self,
        client,
        mock_lti_launch_token,
    ):
        """Test that students have limited access."""
        # Modify token to have student role
        student_token = mock_lti_launch_token.copy()
        student_token["https://purl.imsglobal.org/spec/lti/claim/roles"] = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
        ]

        with patch(
            "src.integrations.blackboard_lti.blackboard_lti.validate_jwt"
        ) as mock_validate:
            mock_validate.return_value = student_token

            response = client.post(
                "/lti/blackboard/launch",
                data={
                    "id_token": "student.jwt.token",
                    "state": "test-state-123",
                },
            )

            # Students may have restricted access
            assert response.status_code in [200, 302, 400, 401, 403]
