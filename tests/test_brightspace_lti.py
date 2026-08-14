"""
Tests for Brightspace LTI 1.3 integration service.

Tests cover:
- Service configuration from environment variables
- Launch data extraction (instructor and student roles)
- LTI config JSON generation with Brightspace-specific endpoints
"""

import os
from unittest.mock import patch

from src.integrations.brightspace_lti import (
    BrightspaceLTIService,
    BrightspaceLaunchData,
    DeepLinkContent,
    GradePassbackResult,
    FastAPISessionService,
    FastAPICookieService,
)

# =============================================================================
# Fixtures
# =============================================================================


BRIGHTSPACE_ENV = {
    "BRIGHTSPACE_LTI_ISSUER": "https://myuni.brightspace.com",
    "BRIGHTSPACE_LTI_CLIENT_ID": "test-client-id-123",
    "BRIGHTSPACE_LTI_DEPLOYMENT_ID": "test-deployment-001",
    "BRIGHTSPACE_LTI_AUTH_URL": "https://myuni.brightspace.com/d2l/lti/authenticate",
    "BRIGHTSPACE_LTI_TOKEN_URL": "https://myuni.brightspace.com/core/connect/token",
    "BRIGHTSPACE_LTI_KEYSET_URL": "https://myuni.brightspace.com/d2l/.well-known/jwks",
}


def _make_launch_dict(
    *,
    roles=None,
    org_unit_id=None,
    context_id="ctx-fallback-456",
    email="instructor@myuni.edu",
):
    """Build a raw JWT claims dict that mimics a Brightspace LTI launch."""
    if roles is None:
        roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
        ]

    custom = {}
    if org_unit_id is not None:
        custom["org_unit_id"] = org_unit_id

    return {
        "iss": "https://myuni.brightspace.com",
        "sub": "user-abc-123",
        "aud": "test-client-id-123",
        "nonce": "nonce-xyz-789",
        "name": "Dr. Jane Smith",
        "email": email,
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "test-deployment-001",
        "https://purl.imsglobal.org/spec/lti/claim/context": {
            "id": context_id,
            "title": "Introduction to Accessibility",
        },
        "https://purl.imsglobal.org/spec/lti/claim/roles": roles,
        "https://purl.imsglobal.org/spec/lti/claim/resource_link": {
            "id": "resource-link-001",
            "title": "Accessibility Scanner",
        },
        "https://purl.imsglobal.org/spec/lti/claim/custom": custom,
    }


# =============================================================================
# TestBrightspaceLTIServiceConfig
# =============================================================================


class TestBrightspaceLTIServiceConfig:
    """Test service configuration from environment variables."""

    def test_not_configured_without_env_vars(self):
        """Service reports not configured when env vars are missing."""
        with patch.dict(os.environ, {}, clear=True):
            service = BrightspaceLTIService()
            assert service.is_configured() is False

    def test_configured_with_env_vars(self):
        """Service reports configured when all env vars are set."""
        with patch.dict(os.environ, BRIGHTSPACE_ENV, clear=True):
            service = BrightspaceLTIService()
            assert service.is_configured() is True

    def test_not_configured_with_partial_env_vars(self):
        """Service reports not configured when only some env vars are set."""
        partial = {"BRIGHTSPACE_LTI_ISSUER": "https://myuni.brightspace.com"}
        with patch.dict(os.environ, partial, clear=True):
            service = BrightspaceLTIService()
            assert service.is_configured() is False


# =============================================================================
# TestBrightspaceLaunchDataExtraction
# =============================================================================


class TestBrightspaceLaunchDataExtraction:
    """Test extraction of launch data from JWT claims dict."""

    def setup_method(self):
        with patch.dict(os.environ, BRIGHTSPACE_ENV, clear=True):
            self.service = BrightspaceLTIService()

    def test_basic_instructor_extraction(self):
        """Extract launch data for an instructor with org_unit_id custom param."""
        raw = _make_launch_dict(
            roles=[
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            ],
            org_unit_id="12345",
            context_id="ctx-fallback-456",
        )

        data = self.service._extract_launch_data_from_dict(raw)

        assert isinstance(data, BrightspaceLaunchData)
        assert data.user_id == "user-abc-123"
        assert data.user_name == "Dr. Jane Smith"
        assert data.user_email == "instructor@myuni.edu"
        # org_unit_id takes precedence over context.id
        assert data.course_id == "12345"
        assert data.course_name == "Introduction to Accessibility"
        assert data.is_instructor is True
        assert data.is_student is False
        assert data.resource_link_id == "resource-link-001"
        assert data.deployment_id == "test-deployment-001"
        assert data.platform_id == "https://myuni.brightspace.com"
        assert data.client_id == "test-client-id-123"
        assert data.nonce == "nonce-xyz-789"

    def test_student_role_detection(self):
        """Extract launch data for a student — is_student True, is_instructor False."""
        raw = _make_launch_dict(
            roles=[
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            ],
            org_unit_id="12345",
            email="student@myuni.edu",
        )
        raw["name"] = "Alice Student"
        raw["sub"] = "user-student-456"

        data = self.service._extract_launch_data_from_dict(raw)

        assert data.is_student is True
        assert data.is_instructor is False
        assert data.user_name == "Alice Student"
        assert data.user_id == "user-student-456"

    def test_fallback_to_context_id_when_no_org_unit(self):
        """When org_unit_id custom param is absent, fall back to context.id."""
        raw = _make_launch_dict(
            org_unit_id=None,
            context_id="ctx-fallback-456",
        )

        data = self.service._extract_launch_data_from_dict(raw)

        assert data.course_id == "ctx-fallback-456"

    def test_administrator_is_instructor(self):
        """Administrator role is treated as instructor."""
        raw = _make_launch_dict(
            roles=[
                "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
            ],
        )

        data = self.service._extract_launch_data_from_dict(raw)
        assert data.is_instructor is True
        assert data.is_student is False

    def test_content_developer_is_instructor(self):
        """ContentDeveloper role is treated as instructor."""
        raw = _make_launch_dict(
            roles=[
                "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
            ],
        )

        data = self.service._extract_launch_data_from_dict(raw)
        assert data.is_instructor is True

    def test_teaching_assistant_is_instructor(self):
        """TeachingAssistant role fragment is treated as instructor."""
        raw = _make_launch_dict(
            roles=[
                "http://purl.imsglobal.org/vocab/lis/v2/membership/Instructor#TeachingAssistant",
            ],
        )

        data = self.service._extract_launch_data_from_dict(raw)
        assert data.is_instructor is True

    def test_missing_email_is_none(self):
        """Email can be missing from launch data."""
        raw = _make_launch_dict()
        del raw["email"]

        data = self.service._extract_launch_data_from_dict(raw)
        assert data.user_email is None

    def test_custom_params_passed_through(self):
        """Custom parameters are preserved in the launch data."""
        raw = _make_launch_dict(org_unit_id="99999")
        raw["https://purl.imsglobal.org/spec/lti/claim/custom"][
            "extra_key"
        ] = "extra_val"

        data = self.service._extract_launch_data_from_dict(raw)
        assert data.custom_params["extra_key"] == "extra_val"
        assert data.custom_params["org_unit_id"] == "99999"


# =============================================================================
# TestBrightspaceLTIConfig
# =============================================================================


class TestBrightspaceLTIConfig:
    """Test LTI config JSON generation."""

    def setup_method(self):
        with patch.dict(os.environ, BRIGHTSPACE_ENV, clear=True):
            self.service = BrightspaceLTIService()

    def test_generate_lti_config_json_endpoints(self):
        """Config JSON has correct Brightspace-specific endpoints."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert (
            config["oidc_initiation_url"]
            == "https://api.example.com/lti/brightspace/login"
        )
        assert (
            config["target_link_uri"]
            == "https://api.example.com/lti/brightspace/launch"
        )

    def test_generate_lti_config_json_title(self):
        """Config JSON has the Aelira tool title."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert config["title"] == "Aelira Accessibility Scanner"

    def test_generate_lti_config_json_scopes(self):
        """Config JSON includes AGS and NRPS scopes."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert (
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem" in config["scopes"]
        )
        assert (
            "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly"
            in config["scopes"]
        )

    def test_generate_lti_config_json_custom_fields(self):
        """Config JSON includes org_unit_id in custom_fields."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert "org_unit_id" in config["custom_fields"]
        # Brightspace substitution variable for OrgUnit context
        assert "$Context.id.history" in config["custom_fields"]["org_unit_id"]

    def test_generate_lti_config_json_messages(self):
        """Config JSON includes LTI message types for resource link and deep linking."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert "messages" in config
        message_types = [m["type"] for m in config["messages"]]
        assert "LtiResourceLinkRequest" in message_types
        assert "LtiDeepLinkingRequest" in message_types

    def test_generate_lti_config_json_jwks(self):
        """Config JSON includes public JWK URL."""
        config = self.service.generate_lti_config_json("https://api.example.com")

        assert (
            config["public_jwk_url"] == "https://api.example.com/lti/brightspace/jwks"
        )


# =============================================================================
# TestSessionAndCookieServices
# =============================================================================


class TestSessionAndCookieServices:
    """Test FastAPI session and cookie service adapters."""

    def test_session_service_in_memory(self):
        """Session service stores and retrieves data in-memory."""
        svc = FastAPISessionService()
        svc.save_nonce("test-nonce-123")
        assert svc.check_nonce("test-nonce-123") is True

    def test_session_service_launch_data(self):
        """Session service stores and retrieves launch data."""
        svc = FastAPISessionService()
        body = {"sub": "user-123", "name": "Test User"}
        svc.save_launch_data("launch-key-1", body)
        assert svc.get_launch_data("launch-key-1") == body

    def test_session_service_state_params(self):
        """Session service stores and retrieves state params."""
        svc = FastAPISessionService()
        params = {"redirect_uri": "https://example.com"}
        svc.save_state_params("state-abc", params)
        assert svc.get_state_params("state-abc") == params

    def test_session_service_state_validation(self):
        """Session service validates state + id_token_hash."""
        svc = FastAPISessionService()
        svc.set_state_valid("state-abc", "hash-xyz")
        assert svc.check_state_is_valid("state-abc", "hash-xyz") is True
        assert svc.check_state_is_valid("state-abc", "wrong-hash") is False

    def test_cookie_service_in_memory(self):
        """Cookie service stores and retrieves cookies in-memory."""
        svc = FastAPICookieService()
        svc.set_cookie("lti_state", "state-value-123")
        assert svc.get_cookie("lti_state") == "state-value-123"

    def test_cookie_service_missing_cookie(self):
        """Cookie service returns None for missing cookie."""
        svc = FastAPICookieService()
        assert svc.get_cookie("nonexistent") is None


# =============================================================================
# TestDeepLinkContent
# =============================================================================


class TestDeepLinkContent:
    """Test deep link content item creation."""

    def setup_method(self):
        with patch.dict(os.environ, BRIGHTSPACE_ENV, clear=True):
            self.service = BrightspaceLTIService()

    def test_create_scan_content_item(self):
        """Create a scan content item with file_id and scan_type."""
        item = self.service.create_scan_content_item(
            title="Scan Lecture Notes",
            launch_url="https://api.example.com/lti/brightspace/launch",
            file_id="file-789",
            scan_type="document",
        )

        assert isinstance(item, DeepLinkContent)
        assert item.type == "ltiResourceLink"
        assert item.title == "Scan Lecture Notes"
        assert item.url == "https://api.example.com/lti/brightspace/launch"
        assert item.custom_params["scan_type"] == "document"
        assert item.custom_params["brightspace_file_id"] == "file-789"

    def test_create_scan_content_item_no_file_id(self):
        """Create a scan content item without file_id."""
        item = self.service.create_scan_content_item(
            title="Scan Course",
            launch_url="https://api.example.com/lti/brightspace/launch",
        )

        assert "brightspace_file_id" not in item.custom_params
        assert item.custom_params["scan_type"] == "document"


# =============================================================================
# TestGradePassbackResult
# =============================================================================


class TestGradePassbackResult:
    """Test GradePassbackResult model."""

    def test_success_result(self):
        result = GradePassbackResult(
            success=True,
            user_id="user-123",
            score=85.0,
            max_score=100.0,
            comment="Good compliance score",
        )
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        result = GradePassbackResult(
            success=False,
            user_id="user-123",
            score=0.0,
            max_score=100.0,
            error="AGS not available",
        )
        assert result.success is False
        assert result.error == "AGS not available"
