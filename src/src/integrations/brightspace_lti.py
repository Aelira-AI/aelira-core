"""
Brightspace LTI 1.3 Integration Module

This module provides Learning Tools Interoperability (LTI) 1.3 integration
with D2L Brightspace for seamless accessibility scanning within the LMS.

Features:
- LTI 1.3 launch handling with OAuth 2.0
- Deep linking for course content scanning
- Assignment and Grade Services (AGS) for compliance score passback
- Names and Role Provisioning Service (NRPS) for course roster access
- OrgUnitId normalization to provider-agnostic course_id

Brightspace LTI Documentation:
- https://documentation.brightspace.com/EN/integrations/ipsis/lti_advantage.htm
- https://www.imsglobal.org/spec/lti/v1p3/

Brightspace-specific endpoints:
- Issuer:  https://{institution}.brightspace.com
- JWKS:    /d2l/.well-known/jwks
- Auth:    /d2l/lti/authenticate
- Token:   /core/connect/token
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
import logging
import json
import os

# PyLTI1p3 imports (same library as Canvas / Blackboard)
from pylti1p3.request import Request as LTIRequest
from pylti1p3.redirect import Redirect
from pylti1p3.tool_config import ToolConfDict
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.deep_link_resource import DeepLinkResource
from pylti1p3.grade import Grade
from pylti1p3.lineitem import LineItem

logger = logging.getLogger(__name__)


# =============================================================================
# FastAPI ↔ PyLTI1p3 Framework Adapters
# =============================================================================


class FastAPILTIRequest(LTIRequest):
    """Wraps a dict of request params into the interface PyLTI1p3 expects."""

    def __init__(self, params: Dict[str, str], secure: bool = True):
        self._params = params
        self._secure = secure

    def get_param(self, key: str) -> str:
        return self._params.get(key, "")

    def is_secure(self) -> bool:
        return self._secure

    @property
    def session(self):
        return {}


class FastAPIRedirect(Redirect):
    """Redirect adapter that simply stores the URL for later use."""

    def __init__(self, url: str = ""):
        self._url = url

    def do_redirect(self) -> str:
        return self._url

    def do_js_redirect(self) -> str:
        return self._url

    def set_redirect_url(self, location: str):
        self._url = location

    def get_redirect_url(self) -> str:
        return self._url


class FastAPIOIDCLogin(OIDCLogin):
    """OIDCLogin subclass that returns a FastAPIRedirect."""

    def get_redirect(self, url: str) -> FastAPIRedirect:
        redirect = FastAPIRedirect()
        redirect.set_redirect_url(url)
        return redirect


class FastAPIMessageLaunch(MessageLaunch):
    """MessageLaunch subclass for FastAPI."""

    def _get_request_param(self, key: str) -> str:
        return self._request.get_param(key)


# =============================================================================
# Data Models
# =============================================================================


class BrightspaceLaunchData(BaseModel):
    """Data extracted from a Brightspace LTI launch.

    ``course_id`` is normalised from the Brightspace OrgUnitId so that
    downstream code can treat it the same as Canvas/Blackboard course_id.
    """

    user_id: str
    user_name: str
    user_email: Optional[str] = None
    course_id: str  # Normalised from OrgUnitId (custom param) or context.id
    course_name: str
    roles: List[str]
    is_instructor: bool
    is_student: bool
    resource_link_id: str
    deployment_id: str
    platform_id: str  # Brightspace issuer URL
    client_id: str
    nonce: str
    custom_params: Dict[str, Any] = {}


class DeepLinkContent(BaseModel):
    """Content item for deep linking."""

    type: str  # "ltiResourceLink", "html", "link"
    title: str
    url: Optional[str] = None
    html: Optional[str] = None
    custom_params: Dict[str, str] = {}


class GradePassbackResult(BaseModel):
    """Result of grade passback operation."""

    success: bool
    user_id: str
    score: float
    max_score: float
    comment: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Brightspace LTI Service
# =============================================================================


class BrightspaceLTIService:
    """
    Brightspace LTI 1.3 Integration Service

    Handles:
    - OIDC login flow
    - LTI launch validation
    - Launch data extraction (OrgUnitId → course_id normalisation)
    - Deep linking for content
    - Grade passback (AGS)
    - Roster access (NRPS)
    """

    # Brightspace standard endpoint paths
    BRIGHTSPACE_AUTH_PATH = "/d2l/lti/authenticate"
    BRIGHTSPACE_TOKEN_PATH = "/core/connect/token"
    BRIGHTSPACE_JWKS_PATH = "/d2l/.well-known/jwks"

    def __init__(self):
        """
        Initialize Brightspace LTI service.

        Configuration is loaded from environment variables:
        - BRIGHTSPACE_LTI_ISSUER
        - BRIGHTSPACE_LTI_CLIENT_ID
        - BRIGHTSPACE_LTI_DEPLOYMENT_ID
        - BRIGHTSPACE_LTI_AUTH_URL
        - BRIGHTSPACE_LTI_TOKEN_URL
        - BRIGHTSPACE_LTI_KEYSET_URL
        """
        self._tool_config: Optional[ToolConfDict] = None
        self._load_config()

    def _load_config(self):
        """Load LTI tool configuration from environment variables."""
        issuer = os.getenv("BRIGHTSPACE_LTI_ISSUER", "")
        client_id = os.getenv("BRIGHTSPACE_LTI_CLIENT_ID", "")
        deployment_id = os.getenv("BRIGHTSPACE_LTI_DEPLOYMENT_ID", "")

        if not issuer or not client_id:
            logger.warning(
                "Brightspace LTI not configured. "
                "Set BRIGHTSPACE_LTI_ISSUER and BRIGHTSPACE_LTI_CLIENT_ID."
            )
            self._tool_config = None
            return

        # Allow explicit overrides, otherwise derive from issuer
        base = issuer.rstrip("/")
        auth_url = os.getenv(
            "BRIGHTSPACE_LTI_AUTH_URL", f"{base}{self.BRIGHTSPACE_AUTH_PATH}"
        )
        token_url = os.getenv(
            "BRIGHTSPACE_LTI_TOKEN_URL", f"{base}{self.BRIGHTSPACE_TOKEN_PATH}"
        )
        keyset_url = os.getenv(
            "BRIGHTSPACE_LTI_KEYSET_URL", f"{base}{self.BRIGHTSPACE_JWKS_PATH}"
        )

        config = {
            issuer: [
                {
                    "default": True,
                    "client_id": client_id,
                    "deployment_ids": [deployment_id] if deployment_id else [],
                    "auth_login_url": auth_url,
                    "auth_token_url": token_url,
                    "key_set_url": keyset_url,
                }
            ]
        }

        self._tool_config = ToolConfDict(config)

        private_key_path = os.getenv("BRIGHTSPACE_LTI_PRIVATE_KEY_PATH", "")
        public_key_path = os.getenv("BRIGHTSPACE_LTI_PUBLIC_KEY_PATH", "")
        self._tool_public_key_pem: Optional[str] = None

        if private_key_path and os.path.exists(private_key_path):
            with open(private_key_path, "r") as f:
                private_key_pem = f.read()
            self._tool_config.set_private_key(issuer, private_key_pem, client_id)
        else:
            logger.warning(
                "BRIGHTSPACE_LTI_PRIVATE_KEY_PATH not set or file missing — "
                "outbound signing (deep link responses, AGS grade passback) "
                "will fail."
            )

        if public_key_path and os.path.exists(public_key_path):
            with open(public_key_path, "r") as f:
                self._tool_public_key_pem = f.read()
            self._tool_config.set_public_key(
                issuer, self._tool_public_key_pem, client_id
            )
        else:
            logger.warning(
                "BRIGHTSPACE_LTI_PUBLIC_KEY_PATH not set or file missing — "
                "JWKS endpoint will return empty key set."
            )

        logger.info("Using environment-based Brightspace LTI config")

    def get_tool_public_key_pem(self) -> Optional[str]:
        """Return the tool's public key PEM, if loaded."""
        return self._tool_public_key_pem

    def get_tool_config(self) -> Optional[ToolConfDict]:
        """Get the tool configuration for PyLTI1p3."""
        return self._tool_config

    def is_configured(self) -> bool:
        """Check if LTI is properly configured."""
        return self._tool_config is not None

    # =========================================================================
    # OIDC Login Flow
    # =========================================================================

    def initiate_oidc_login(
        self,
        request_params: Dict[str, str],
        target_link_uri: str,
        session_service: Any,
        cookie_service: Any,
    ) -> str:
        """
        Initiate OIDC login flow for LTI 1.3.

        This is the first step when Brightspace redirects to our tool.

        Args:
            request_params: Parameters from the Brightspace login request
            target_link_uri: The final launch URL
            session_service: Session storage service
            cookie_service: Cookie management service

        Returns:
            Redirect URL for OIDC authentication
        """
        if not self.is_configured():
            raise ValueError("Brightspace LTI not configured")

        oidc_login = FastAPIOIDCLogin(
            FastAPILTIRequest(request_params),
            self._tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
        )

        return oidc_login.redirect(target_link_uri)

    # =========================================================================
    # Launch Handling
    # =========================================================================

    def validate_launch(
        self,
        request_params: Dict[str, str],
        session_service: Any,
        cookie_service: Any,
    ) -> MessageLaunch:
        """
        Validate an LTI launch request and return the message launch object.

        Args:
            request_params: Parameters from the launch request (including id_token)
            session_service: Session storage service
            cookie_service: Cookie management service

        Returns:
            MessageLaunch object with validated launch data
        """
        if not self.is_configured():
            raise ValueError("Brightspace LTI not configured")

        message_launch = FastAPIMessageLaunch(
            FastAPILTIRequest(request_params),
            self._tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
        )

        return message_launch

    def extract_launch_data(
        self, message_launch: MessageLaunch
    ) -> BrightspaceLaunchData:
        """
        Extract structured data from a validated LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            BrightspaceLaunchData with user, course, and context information
        """
        launch_data = message_launch.get_launch_data()
        return self._extract_launch_data_from_dict(launch_data)

    def _extract_launch_data_from_dict(
        self, raw_data: Dict[str, Any]
    ) -> BrightspaceLaunchData:
        """
        Parse a JWT claims dict into BrightspaceLaunchData.

        Key design choice: prefers ``org_unit_id`` from custom parameters
        (Brightspace's native course identifier) for ``course_id``, falling
        back to ``context.id`` when the custom param is absent.

        Roles: checks for Instructor / Administrator / ContentDeveloper /
        TeachingAssistant fragments for ``is_instructor``, Learner for
        ``is_student``.
        """
        # -- User information --
        user_id = raw_data.get("sub", "")
        user_name = raw_data.get("name", "")
        user_email = raw_data.get("email")

        # -- Context (course) information --
        context = raw_data.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
        custom_params = raw_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/custom", {}
        )

        # Prefer Brightspace OrgUnitId from custom param, fall back to context.id
        org_unit_id = custom_params.get("org_unit_id")
        course_id = str(org_unit_id) if org_unit_id else context.get("id", "")
        course_name = context.get("title", "")

        # -- Roles --
        roles = raw_data.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
        is_instructor = any(
            "Instructor" in role
            or "Administrator" in role
            or "ContentDeveloper" in role
            or "TeachingAssistant" in role
            for role in roles
        )
        is_student = any("Learner" in role for role in roles)

        # -- Resource link --
        resource_link = raw_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/resource_link", {}
        )
        resource_link_id = resource_link.get("id", "")

        # -- Deployment --
        deployment_id = raw_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
        )

        return BrightspaceLaunchData(
            user_id=user_id,
            user_name=user_name,
            user_email=user_email,
            course_id=course_id,
            course_name=course_name,
            roles=roles,
            is_instructor=is_instructor,
            is_student=is_student,
            resource_link_id=resource_link_id,
            deployment_id=deployment_id,
            platform_id=raw_data.get("iss", ""),
            client_id=raw_data.get("aud", ""),
            nonce=raw_data.get("nonce", ""),
            custom_params=custom_params,
        )

    # =========================================================================
    # Deep Linking
    # =========================================================================

    def is_deep_link_launch(self, message_launch: MessageLaunch) -> bool:
        """Check if this is a deep linking launch."""
        return message_launch.is_deep_link_launch()

    def create_deep_link_response(
        self,
        message_launch: MessageLaunch,
        content_items: List[DeepLinkContent],
    ) -> str:
        """
        Create a deep linking response with content items.

        This is used when an instructor selects content to add to their course.

        Args:
            message_launch: The original message launch
            content_items: List of content items to return to Brightspace

        Returns:
            HTML form that auto-submits to Brightspace with the content items
        """
        deep_link = message_launch.get_deep_link()

        resources = []
        for item in content_items:
            if item.type == "ltiResourceLink":
                resource = DeepLinkResource()
                resource.set_url(item.url)
                resource.set_title(item.title)
                if item.custom_params:
                    resource.set_custom_params(item.custom_params)
                resources.append(resource)

        return deep_link.output_response_form(resources)

    def create_scan_content_item(
        self,
        title: str,
        launch_url: str,
        file_id: str = None,
        scan_type: str = "document",
    ) -> DeepLinkContent:
        """
        Create a content item for an accessibility scan.

        Args:
            title: Display title for the content item
            launch_url: URL to launch when item is clicked
            file_id: Optional Brightspace file ID to scan
            scan_type: Type of scan (document, image, video)

        Returns:
            DeepLinkContent item configured for accessibility scanning
        """
        custom_params = {
            "scan_type": scan_type,
        }
        if file_id:
            custom_params["brightspace_file_id"] = file_id

        return DeepLinkContent(
            type="ltiResourceLink",
            title=title,
            url=launch_url,
            custom_params=custom_params,
        )

    # =========================================================================
    # Assignment and Grade Services (AGS)
    # =========================================================================

    def has_ags(self, message_launch: MessageLaunch) -> bool:
        """Check if Assignment and Grade Services is available."""
        return message_launch.has_ags()

    def submit_compliance_score(
        self,
        message_launch: MessageLaunch,
        user_id: str,
        compliance_score: float,
        max_score: float = 100.0,
        comment: str = None,
    ) -> GradePassbackResult:
        """
        Submit a compliance score as a grade to Brightspace.

        Args:
            message_launch: The validated message launch
            user_id: Brightspace user ID to submit grade for
            compliance_score: The compliance score (0-100)
            max_score: Maximum possible score (default 100)
            comment: Optional comment with the grade

        Returns:
            GradePassbackResult with success/failure status
        """
        try:
            if not self.has_ags(message_launch):
                return GradePassbackResult(
                    success=False,
                    user_id=user_id,
                    score=compliance_score,
                    max_score=max_score,
                    error="Assignment and Grade Services not available",
                )

            ags = message_launch.get_ags()

            # Create or get line item
            line_item = LineItem()
            line_item.set_label("Accessibility Compliance Score")
            line_item.set_score_maximum(max_score)

            # Check if line item exists, create if not
            line_items = ags.get_lineitems()
            existing = next(
                (
                    li
                    for li in line_items
                    if li.get("label") == "Accessibility Compliance Score"
                ),
                None,
            )

            if existing:
                line_item.set_id(existing.get("id"))
            else:
                line_item = ags.find_or_create_lineitem(line_item)

            # Create grade
            grade = Grade()
            grade.set_score_given(compliance_score)
            grade.set_score_maximum(max_score)
            grade.set_user_id(user_id)
            grade.set_activity_progress("Completed")
            grade.set_grading_progress("FullyGraded")

            if comment:
                grade.set_comment(comment)

            grade.set_timestamp(datetime.utcnow().isoformat() + "Z")

            # Submit grade
            ags.put_grade(grade, line_item)

            logger.info(
                f"Submitted compliance score {compliance_score} to Brightspace "
                f"for user {user_id}"
            )

            return GradePassbackResult(
                success=True,
                user_id=user_id,
                score=compliance_score,
                max_score=max_score,
                comment=comment,
            )

        except Exception as e:
            logger.error(f"Failed to submit grade to Brightspace: {e}")
            return GradePassbackResult(
                success=False,
                user_id=user_id,
                score=compliance_score,
                max_score=max_score,
                error=str(e),
            )

    # =========================================================================
    # Names and Role Provisioning Service (NRPS)
    # =========================================================================

    def has_nrps(self, message_launch: MessageLaunch) -> bool:
        """Check if Names and Role Provisioning Service is available."""
        return message_launch.has_nrps()

    def get_course_members(self, message_launch: MessageLaunch) -> List[Dict[str, Any]]:
        """
        Get course members (roster) from Brightspace.

        Args:
            message_launch: The validated message launch

        Returns:
            List of course members with roles and user info
        """
        if not self.has_nrps(message_launch):
            logger.warning("NRPS not available for this launch")
            return []

        nrps = message_launch.get_nrps()
        members = nrps.get_members()

        return [
            {
                "user_id": member.get("user_id"),
                "name": member.get("name"),
                "email": member.get("email"),
                "roles": member.get("roles", []),
                "is_instructor": any(
                    "Instructor" in r for r in member.get("roles", [])
                ),
                "is_student": any("Learner" in r for r in member.get("roles", [])),
            }
            for member in members
        ]

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_issuer_from_launch(self, message_launch: MessageLaunch) -> str:
        """Extract the issuer (platform ID) from a validated LTI launch."""
        launch_data = message_launch.get_launch_data()
        return launch_data.get("iss", "")

    def get_client_id_from_launch(self, message_launch: MessageLaunch) -> str:
        """Extract the client_id from a validated LTI launch."""
        launch_data = message_launch.get_launch_data()
        aud = launch_data.get("aud", "")
        # aud can be a string or a list of strings
        if isinstance(aud, list):
            return aud[0] if aud else ""
        return str(aud)

    def generate_lti_config_json(self, base_url: str) -> Dict[str, Any]:
        """
        Generate LTI configuration JSON for Brightspace registration.

        This can be pasted into the Brightspace Admin external tool
        registration page.

        Args:
            base_url: Base URL of the Aelira application

        Returns:
            Dict with Brightspace-specific LTI configuration
        """
        return {
            "title": "Aelira Accessibility Scanner",
            "description": (
                "WCAG 2.1 accessibility scanning and remediation "
                "for educational content"
            ),
            "oidc_initiation_url": f"{base_url}/lti/brightspace/login",
            "target_link_uri": f"{base_url}/lti/brightspace/launch",
            "scopes": [
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score",
                "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
            ],
            "messages": [
                {
                    "type": "LtiResourceLinkRequest",
                    "target_link_uri": f"{base_url}/lti/brightspace/launch",
                    "label": "Accessibility Scanner",
                },
                {
                    "type": "LtiDeepLinkingRequest",
                    "target_link_uri": f"{base_url}/lti/brightspace/deep-link",
                    "label": "Add Accessibility Scan",
                },
            ],
            "custom_fields": {
                "org_unit_id": "$Context.id.history",
                "brightspace_user_id": "$User.id",
                "brightspace_user_roles": "$Membership.role",
                "brightspace_org_defined_id": "$OrgDefinedId",
            },
            "public_jwk_url": f"{base_url}/lti/brightspace/jwks",
            "public_jwk": {},  # Populated with actual key at runtime
        }


# =============================================================================
# FastAPI Session/Cookie Adapters
# =============================================================================


class FastAPISessionService:
    """
    Session service adapter for FastAPI that implements the PyLTI1p3
    SessionService interface using in-memory or Redis storage.
    """

    _PREFIX = "lti1p3-bs"
    _LIFETIME = 86400

    def __init__(self, redis_client=None):
        self._store: Dict[str, Any] = {}
        self._redis = redis_client

    def _key(
        self, key: str, nonce: Optional[str] = None, add_prefix: bool = True
    ) -> str:
        return (
            ((self._PREFIX + "-") if add_prefix else "")
            + key
            + (("-" + nonce) if nonce else "")
        )

    def _set(self, key: str, value: Any):
        if self._redis:
            self._redis.setex(
                f"brightspace_lti_session:{key}",
                self._LIFETIME,
                json.dumps(value) if not isinstance(value, (str, bytes)) else value,
            )
        else:
            self._store[key] = value

    def _get(self, key: str) -> Any:
        if self._redis:
            val = self._redis.get(f"brightspace_lti_session:{key}")
            if val and isinstance(val, bytes):
                val = val.decode()
            if val:
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return val
            return None
        return self._store.get(key)

    def _check(self, key: str) -> bool:
        if self._redis:
            return bool(self._redis.exists(f"brightspace_lti_session:{key}"))
        return key in self._store

    # -- PyLTI1p3 SessionService interface --

    def save_nonce(self, nonce: str):
        self._set(self._key("nonce", nonce), True)

    def check_nonce(self, nonce: str) -> bool:
        return self._check(self._key("nonce", nonce))

    def save_launch_data(self, key: str, jwt_body):
        self._set(self._key(key, add_prefix=False), jwt_body)

    def get_launch_data(self, key: str):
        return self._get(self._key(key, add_prefix=False))

    def save_state_params(self, state: str, params):
        self._set(self._key(state), params)

    def get_state_params(self, state: str):
        return self._get(self._key(state))

    def set_state_valid(self, state: str, id_token_hash: str):
        self._set(self._key(state + "-id-token-hash"), id_token_hash)

    def check_state_is_valid(self, state: str, id_token_hash: str) -> bool:
        return self._get(self._key(state + "-id-token-hash")) == id_token_hash

    def set_data_storage(self, data_storage):
        pass  # Not needed — we are the storage

    def set_launch_data_lifetime(self, time_sec: int):
        self._LIFETIME = time_sec


class FastAPICookieService:
    """
    Cookie service adapter for FastAPI.

    Manages LTI cookies for state management with SameSite=None, Secure=True.
    """

    def __init__(self, request=None, response=None):
        self._request = request
        self._response = response
        self._cookies: Dict[str, str] = {}

    def get_cookie(self, name: str) -> Optional[str]:
        if self._request:
            return self._request.cookies.get(name)
        return self._cookies.get(name)

    def set_cookie(self, name: str, value: str, exp: int = 3600):
        if self._response:
            self._response.set_cookie(
                key=name,
                value=value,
                max_age=exp,
                httponly=True,
                samesite="None",
                secure=True,
            )
        self._cookies[name] = value


# =============================================================================
# Singleton Instance
# =============================================================================

_brightspace_lti_service: Optional[BrightspaceLTIService] = None


def get_brightspace_lti_service() -> BrightspaceLTIService:
    """Get the singleton Brightspace LTI service instance."""
    global _brightspace_lti_service
    if _brightspace_lti_service is None:
        _brightspace_lti_service = BrightspaceLTIService()
    return _brightspace_lti_service


# =============================================================================
# Testing
# =============================================================================


def test_lti_config_generation():
    """Test LTI configuration generation."""
    service = BrightspaceLTIService()
    config = service.generate_lti_config_json("https://api.example.com")

    print("=" * 60)
    print("Brightspace LTI Registration Configuration")
    print("=" * 60)
    print(json.dumps(config, indent=2))
    print(
        "\nUse this configuration when registering in "
        "Brightspace Admin > External Learning Tools"
    )


if __name__ == "__main__":
    test_lti_config_generation()
