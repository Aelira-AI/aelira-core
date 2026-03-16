"""
Canvas LTI 1.3 Integration Module (Phase 4.5)

This module provides Learning Tools Interoperability (LTI) 1.3 integration
with Canvas LMS for seamless accessibility scanning within the LMS.

Features:
- LTI 1.3 launch handling with OAuth 2.0
- Deep linking for course content scanning
- Assignment and Grade Services (AGS) for compliance score passback
- Names and Role Provisioning Service (NRPS) for course roster access
- Content item return for accessibility reports

Canvas LTI Documentation:
- https://canvas.instructure.com/doc/api/file.lti_dev_key_config.html
- https://www.imsglobal.org/spec/lti/v1p3/
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
import logging
import json
import os
from enum import Enum

# PyLTI1p3 imports
from pylti1p3.tool_config import ToolConfDict
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.deep_link_resource import DeepLinkResource
from pylti1p3.grade import Grade
from pylti1p3.lineitem import LineItem
from pylti1p3.assignments_grades import AssignmentsGradesService

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class LTIRole(str, Enum):
    """LTI user roles"""

    INSTRUCTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
    LEARNER = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
    ADMINISTRATOR = (
        "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator"
    )
    CONTENT_DEVELOPER = (
        "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper"
    )
    MENTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Mentor"


class CanvasLaunchData(BaseModel):
    """Data extracted from Canvas LTI launch"""

    user_id: str
    user_name: str
    user_email: Optional[str] = None
    course_id: str
    course_name: str
    roles: List[str]
    is_instructor: bool
    is_student: bool
    resource_link_id: str
    deployment_id: str
    platform_id: str
    client_id: str
    nonce: str
    custom_params: Dict[str, Any] = {}


class LTIPlatformConfig(BaseModel):
    """Configuration for an LTI platform (Canvas instance)"""

    issuer: str  # e.g., "https://canvas.instructure.com"
    client_id: str
    deployment_id: str
    auth_login_url: str
    auth_token_url: str
    key_set_url: str
    private_key_file: str
    public_key_file: str


class GradePassbackResult(BaseModel):
    """Result of grade passback operation"""

    success: bool
    user_id: str
    score: float
    max_score: float
    comment: Optional[str] = None
    error: Optional[str] = None


class DeepLinkContent(BaseModel):
    """Content item for deep linking"""

    type: str  # "ltiResourceLink", "html", "link"
    title: str
    url: Optional[str] = None
    html: Optional[str] = None
    custom_params: Dict[str, str] = {}


# =============================================================================
# Canvas LTI Service
# =============================================================================


class CanvasLTIService:
    """
    Canvas LTI 1.3 Integration Service

    Handles:
    - OIDC login flow
    - LTI launch validation
    - Deep linking for content
    - Grade passback (AGS)
    - Roster access (NRPS)
    """

    def __init__(self, config_file: str = None):
        """
        Initialize Canvas LTI service.

        Args:
            config_file: Path to LTI configuration JSON file
        """
        self.config_file = config_file or os.getenv(
            "LTI_CONFIG_FILE", "lti_config.json"
        )
        self._tool_config = None
        self._load_config()

    def _load_config(self):
        """Load LTI tool configuration from file or environment"""
        # Default configuration structure for multiple platforms
        default_config = {
            "https://canvas.instructure.com": [
                {
                    "default": True,
                    "client_id": os.getenv("CANVAS_CLIENT_ID", ""),
                    "deployment_ids": [os.getenv("CANVAS_DEPLOYMENT_ID", "")],
                    "auth_login_url": "https://canvas.instructure.com/api/lti/authorize_redirect",
                    "auth_token_url": "https://canvas.instructure.com/login/oauth2/token",
                    "key_set_url": "https://canvas.instructure.com/api/lti/security/jwks",
                }
            ]
        }

        # Try to load from config file
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    file_config = json.load(f)
                    self._tool_config = ToolConfDict(file_config)
                    logger.info(f"Loaded LTI config from {self.config_file}")
                    return
            except Exception as e:
                logger.warning(f"Failed to load LTI config file: {e}")

        # Use environment-based config
        if os.getenv("CANVAS_CLIENT_ID"):
            self._tool_config = ToolConfDict(default_config)
            logger.info("Using environment-based LTI config")
        else:
            logger.warning("No LTI configuration found. Canvas integration disabled.")
            self._tool_config = None

    def get_tool_config(self) -> Optional[ToolConfDict]:
        """Get the tool configuration for PyLTI1p3"""
        return self._tool_config

    def is_configured(self) -> bool:
        """Check if LTI is properly configured"""
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

        This is the first step when Canvas redirects to our tool.

        Args:
            request_params: Parameters from the Canvas login request
            target_link_uri: The final launch URL
            session_service: Session storage service (FastAPI or Flask)
            cookie_service: Cookie management service

        Returns:
            Redirect URL for OIDC authentication
        """
        if not self.is_configured():
            raise ValueError("LTI not configured")

        oidc_login = OIDCLogin(
            request_params,
            self._tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
        )

        return oidc_login.redirect(target_link_uri)

    # =========================================================================
    # Launch Handling
    # =========================================================================

    def validate_launch(
        self, request_params: Dict[str, str], session_service: Any, cookie_service: Any
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
            raise ValueError("LTI not configured")

        message_launch = MessageLaunch(
            request_params,
            self._tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
        )

        return message_launch

    def extract_launch_data(self, message_launch: MessageLaunch) -> CanvasLaunchData:
        """
        Extract structured data from a validated LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            CanvasLaunchData with user, course, and context information
        """
        launch_data = message_launch.get_launch_data()

        # Extract user information
        user_id = launch_data.get("sub", "")
        user_name = launch_data.get("name", "")
        user_email = launch_data.get("email")

        # Extract context (course) information
        context = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/context", {}
        )
        course_id = context.get("id", "")
        course_name = context.get("title", "")

        # Extract roles
        roles = launch_data.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
        is_instructor = any(
            "Instructor" in role
            or "Administrator" in role
            or "ContentDeveloper" in role
            for role in roles
        )
        is_student = any("Learner" in role for role in roles)

        # Extract resource link
        resource_link = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/resource_link", {}
        )
        resource_link_id = resource_link.get("id", "")

        # Extract deployment info
        deployment_id = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
        )

        # Extract custom parameters
        custom_params = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/custom", {}
        )

        return CanvasLaunchData(
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
            platform_id=launch_data.get("iss", ""),
            client_id=launch_data.get("aud", ""),
            nonce=launch_data.get("nonce", ""),
            custom_params=custom_params,
        )

    # =========================================================================
    # Deep Linking
    # =========================================================================

    def is_deep_link_launch(self, message_launch: MessageLaunch) -> bool:
        """Check if this is a deep linking launch"""
        return message_launch.is_deep_link_launch()

    def create_deep_link_response(
        self, message_launch: MessageLaunch, content_items: List[DeepLinkContent]
    ) -> str:
        """
        Create a deep linking response with content items.

        This is used when an instructor selects content to add to their course.

        Args:
            message_launch: The original message launch
            content_items: List of content items to return to Canvas

        Returns:
            HTML form that auto-submits to Canvas with the content items
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
            file_id: Optional Canvas file ID to scan
            scan_type: Type of scan (document, image, video)

        Returns:
            DeepLinkContent item configured for accessibility scanning
        """
        custom_params = {
            "scan_type": scan_type,
        }
        if file_id:
            custom_params["canvas_file_id"] = file_id

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
        """Check if Assignment and Grade Services is available"""
        return message_launch.has_ags()

    def get_ags_service(
        self, message_launch: MessageLaunch
    ) -> Optional[AssignmentsGradesService]:
        """Get the AGS service if available"""
        if not self.has_ags(message_launch):
            return None
        return message_launch.get_ags()

    def submit_compliance_score(
        self,
        message_launch: MessageLaunch,
        user_id: str,
        compliance_score: float,
        max_score: float = 100.0,
        comment: str = None,
    ) -> GradePassbackResult:
        """
        Submit a compliance score as a grade to Canvas.

        Args:
            message_launch: The validated message launch
            user_id: Canvas user ID to submit grade for
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
                f"Submitted compliance score {compliance_score} for user {user_id}"
            )

            return GradePassbackResult(
                success=True,
                user_id=user_id,
                score=compliance_score,
                max_score=max_score,
                comment=comment,
            )

        except Exception as e:
            logger.error(f"Failed to submit grade: {e}")
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
        """Check if Names and Role Provisioning Service is available"""
        return message_launch.has_nrps()

    def get_course_members(self, message_launch: MessageLaunch) -> List[Dict[str, Any]]:
        """
        Get course members (roster) from Canvas.

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
        """
        Extract the issuer (platform ID) from a validated LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            The issuer URL (e.g., "https://canvas.instructure.com")
        """
        launch_data = message_launch.get_launch_data()
        return launch_data.get("iss", "")

    def get_client_id_from_launch(self, message_launch: MessageLaunch) -> str:
        """
        Extract the client_id from a validated LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            The client_id (from the 'aud' claim)
        """
        launch_data = message_launch.get_launch_data()
        aud = launch_data.get("aud", "")
        # aud can be a string or a list of strings
        if isinstance(aud, list):
            return aud[0] if aud else ""
        return str(aud)

    def generate_lti_config_json(self, base_url: str) -> Dict[str, Any]:
        """
        Generate LTI Developer Key configuration JSON for Canvas.

        This can be used when setting up the tool in Canvas.

        Args:
            base_url: Base URL of the Aelira application

        Returns:
            Dict that can be pasted into Canvas LTI configuration
        """
        return {
            "title": "Aelira Accessibility Scanner",
            "description": "WCAG 2.1 accessibility scanning and remediation for educational content",
            "privacy_level": "public",
            "oidc_initiation_url": f"{base_url}/lti/login",
            "target_link_uri": f"{base_url}/lti/launch",
            "scopes": [
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score",
                "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
            ],
            "extensions": [
                {
                    "platform": "canvas.instructure.com",
                    "settings": {
                        "platform": "canvas.instructure.com",
                        "placements": [
                            {
                                "placement": "course_navigation",
                                "enabled": True,
                                "message_type": "LtiResourceLinkRequest",
                                "target_link_uri": f"{base_url}/lti/launch",
                                "text": "Accessibility Scanner",
                                "icon_url": f"{base_url}/static/icons/accessibility-icon.svg",
                            },
                            {
                                "placement": "assignment_selection",
                                "enabled": True,
                                "message_type": "LtiDeepLinkingRequest",
                                "target_link_uri": f"{base_url}/lti/deep-link",
                                "text": "Add Accessibility Scan",
                            },
                            {
                                "placement": "editor_button",
                                "enabled": True,
                                "message_type": "LtiDeepLinkingRequest",
                                "target_link_uri": f"{base_url}/lti/deep-link",
                                "text": "Check Accessibility",
                                "icon_url": f"{base_url}/static/icons/accessibility-icon.svg",
                            },
                        ],
                    },
                }
            ],
            "public_jwk_url": f"{base_url}/lti/jwks",
            "custom_fields": {
                "canvas_course_id": "$Canvas.course.id",
                "canvas_user_id": "$Canvas.user.id",
                "canvas_user_roles": "$Canvas.membership.roles",
                "canvas_file_id": "$Canvas.file.id",
                "canvas_assignment_id": "$Canvas.assignment.id",
            },
        }


# =============================================================================
# FastAPI Session/Cookie Adapters
# =============================================================================


class FastAPISessionService:
    """
    Session service adapter for FastAPI.

    Stores LTI session data in-memory or Redis.
    """

    def __init__(self, redis_client=None):
        self._sessions: Dict[str, Dict] = {}
        self._redis = redis_client

    def get(self, key: str) -> Optional[str]:
        if self._redis:
            return self._redis.get(f"lti_session:{key}")
        return self._sessions.get(key)

    def set(self, key: str, value: str, exp: int = 3600):
        if self._redis:
            self._redis.setex(f"lti_session:{key}", exp, value)
        else:
            self._sessions[key] = value

    def delete(self, key: str):
        if self._redis:
            self._redis.delete(f"lti_session:{key}")
        else:
            self._sessions.pop(key, None)


class FastAPICookieService:
    """
    Cookie service adapter for FastAPI.

    Manages LTI cookies for state management.
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

_lti_service: Optional[CanvasLTIService] = None


def get_canvas_lti_service() -> CanvasLTIService:
    """Get the singleton Canvas LTI service instance"""
    global _lti_service
    if _lti_service is None:
        _lti_service = CanvasLTIService()
    return _lti_service


# =============================================================================
# Testing
# =============================================================================


def test_lti_config_generation():
    """Test LTI configuration generation"""
    service = CanvasLTIService()
    config = service.generate_lti_config_json("https://app.aelira.ai")

    print("=" * 60)
    print("Canvas LTI Developer Key Configuration")
    print("=" * 60)
    print(json.dumps(config, indent=2))
    print("\n✓ Copy this JSON into Canvas Developer Keys > Configure > Paste JSON")


if __name__ == "__main__":
    test_lti_config_generation()
