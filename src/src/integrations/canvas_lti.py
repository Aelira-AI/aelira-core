"""
Canvas LTI 1.3 Integration Module

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
from pylti1p3.request import Request as LTIRequest
from pylti1p3.redirect import Redirect
from pylti1p3.tool_config import ToolConfDict
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.deep_link_resource import DeepLinkResource
from pylti1p3.grade import Grade
from pylti1p3.lineitem import LineItem
from pylti1p3.assignments_grades import AssignmentsGradesService

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
    TEACHING_ASSISTANT = "TeachingAssistant"


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
    placement: str = ""
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


class _PermissiveToolConf(ToolConfDict):
    """ToolConfDict that accepts any deployment_id.

    Canvas generates a new deployment_id for every sub-account, course context,
    and config re-paste, making an explicit allowlist impractical for multi-
    context tools. Issuer + client_id + JWT signature already authenticate the
    platform, so locking on deployment_id adds no real security.
    """

    def _get_deployment(self, iss_conf, deployment_id):
        from pylti1p3.deployment import Deployment

        d = Deployment()
        return d.set_deployment_id(deployment_id)


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
        self._tool_public_key_pem: Optional[str] = None
        self._load_config()
        self._load_keys()

    def _load_config(self):
        """Load LTI tool configuration from file or environment"""
        # Default configuration structure for multiple platforms
        canvas_issuer = os.getenv("CANVAS_ISSUER", "https://canvas.instructure.com")
        canvas_base = canvas_issuer.rstrip("/")
        default_config = {
            canvas_issuer: [
                {
                    "default": True,
                    "client_id": os.getenv("CANVAS_CLIENT_ID", ""),
                    "deployment_ids": [
                        d.strip()
                        for d in os.getenv("CANVAS_DEPLOYMENT_ID", "").split(",")
                        if d.strip()
                    ],
                    "auth_login_url": f"{canvas_base}/api/lti/authorize_redirect",
                    "auth_token_url": f"{canvas_base}/login/oauth2/token",
                    "key_set_url": f"{canvas_base}/api/lti/security/jwks",
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

        # Use environment-based config with permissive deployment validation.
        # Canvas generates a new deployment_id for each sub-account/context,
        # which makes maintaining an explicit list impractical. Security is
        # already ensured by issuer + client_id + JWT signature validation.
        if os.getenv("CANVAS_CLIENT_ID"):
            self._tool_config = _PermissiveToolConf(default_config)
            logger.info("Using environment-based LTI config")
        else:
            logger.warning("No LTI configuration found. Canvas integration disabled.")
            self._tool_config = None

    def _load_keys(self) -> None:
        """Attach the tool's RSA keypair to the config.

        LTI 1.3 is two-directional. Verifying an inbound Canvas launch uses
        Canvas's public JWKS and needs nothing from us. But anything the tool
        *signs* - deep-link content items, AGS grade passback, NRPS calls -
        must be verifiable by Canvas against a key we publish at /lti/jwks.

        Without this, launch appears to work while every signed response fails,
        which is exactly the state this integration was in until 2026-08-14:
        the JWKS route returned a hardcoded empty key set and no Canvas keypair
        existed on disk.
        """
        if self._tool_config is None:
            return

        private_key_path = os.getenv("CANVAS_LTI_PRIVATE_KEY_PATH", "")
        public_key_path = os.getenv("CANVAS_LTI_PUBLIC_KEY_PATH", "")
        issuer = os.getenv("CANVAS_ISSUER", "https://canvas.instructure.com")
        client_id = os.getenv("CANVAS_CLIENT_ID", "")

        if not private_key_path or not os.path.exists(private_key_path):
            logger.warning(
                "Canvas LTI private key not found (CANVAS_LTI_PRIVATE_KEY_PATH=%r). "
                "Inbound launches will still verify, but deep linking and grade "
                "passback cannot be signed and /lti/jwks will publish no keys.",
                private_key_path,
            )
            return

        try:
            with open(private_key_path, "r") as handle:
                self._tool_config.set_private_key(issuer, handle.read(), client_id)

            if public_key_path and os.path.exists(public_key_path):
                with open(public_key_path, "r") as handle:
                    self._tool_public_key_pem = handle.read()
                self._tool_config.set_public_key(
                    issuer, self._tool_public_key_pem, client_id
                )
                logger.info("Canvas LTI keypair loaded; JWKS will publish 1 key")
            else:
                logger.warning(
                    "Canvas LTI public key not found (CANVAS_LTI_PUBLIC_KEY_PATH=%r); "
                    "signing works but /lti/jwks will publish no keys, so Canvas "
                    "cannot verify what we sign.",
                    public_key_path,
                )
        except Exception as exc:
            logger.error("Failed to load Canvas LTI keys: %s", exc, exc_info=True)

    def get_tool_config(self) -> Optional[ToolConfDict]:
        """Get the tool configuration for PyLTI1p3"""
        return self._tool_config

    def get_tool_public_key_pem(self) -> Optional[str]:
        """Public key PEM used to build the published JWKS, if loaded."""
        return self._tool_public_key_pem

    def has_signing_keys(self) -> bool:
        """True when the tool can sign messages Canvas is able to verify."""
        return self._tool_public_key_pem is not None

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

        message_launch = FastAPIMessageLaunch(
            FastAPILTIRequest(request_params),
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
        custom_params = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/custom", {}
        )
        # Prefer Canvas-specific numeric course ID from custom params.
        # Filter out unsubstituted Canvas variables (e.g. "$Canvas.course.id"
        # which appears in account-level launches with no course context).
        # Only fall back to context ID when the context is actually a course
        # — account-level launches have context type "Account" with a hash ID.
        raw_course_id = str(custom_params.get("canvas_course_id", ""))
        if raw_course_id.startswith("$"):
            raw_course_id = ""
        context_types = context.get("type", [])
        is_course_context = (
            "http://purl.imsglobal.org/vocab/lis/v2/course#702CourseOffering"
            in context_types
            or "CourseOffering" in str(context_types)
        )
        course_id = raw_course_id or (
            context.get("id", "") if is_course_context else ""
        )
        course_name = context.get("title", "")

        # Extract roles
        roles = launch_data.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
        is_instructor = any(
            "Instructor" in role
            or "Administrator" in role
            or "ContentDeveloper" in role
            or "TeachingAssistant" in role
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

        # Extract placement (Canvas includes this to indicate which navigation
        # link triggered the launch: "course_navigation", "account_navigation", etc.)
        placement = launch_data.get("https://www.instructure.com/placement", "")

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
            placement=placement,
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
                                "placement": "account_navigation",
                                "enabled": True,
                                "default": "disabled",
                                "visibility": "admins",
                                "message_type": "LtiResourceLinkRequest",
                                "target_link_uri": f"{base_url}/lti/launch",
                                "text": "Accessibility Overview",
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
    Session service adapter for FastAPI that implements the PyLTI1p3
    SessionService interface using in-memory or Redis storage.
    """

    _PREFIX = "lti1p3"
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
                f"lti_session:{key}",
                self._LIFETIME,
                json.dumps(value) if not isinstance(value, (str, bytes)) else value,
            )
        else:
            self._store[key] = value

    def _get(self, key: str) -> Any:
        if self._redis:
            val = self._redis.get(f"lti_session:{key}")
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
            return bool(self._redis.exists(f"lti_session:{key}"))
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
    config = service.generate_lti_config_json("https://dashboard.example.com")

    print("=" * 60)
    print("Canvas LTI Developer Key Configuration")
    print("=" * 60)
    print(json.dumps(config, indent=2))
    print("\n✓ Copy this JSON into Canvas Developer Keys > Configure > Paste JSON")


if __name__ == "__main__":
    test_lti_config_generation()
