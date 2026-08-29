"""
Blackboard LTI 1.3 Integration Service

This module provides Learning Tools Interoperability (LTI) 1.3 integration
with Blackboard Learn for seamless accessibility scanning within the LMS.

Blackboard LTI 1.3 Documentation:
- https://developer.anthology.com/learn/lti
- https://www.imsglobal.org/spec/lti/v1p3/

Blackboard-specific considerations:
- Uses Anthology Developer Portal for app registration
- Different issuer format than Canvas
- REST API available for extended functionality
"""

import base64
import hashlib
import logging
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel

# PyLTI1p3 imports (same library as Canvas)
from pylti1p3.tool_config import ToolConfDict
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.deep_link_resource import DeepLinkResource
from pylti1p3.grade import Grade
from pylti1p3.lineitem import LineItem

logger = logging.getLogger(__name__)

_MAX_SIGNING_KEY_BYTES = 64 * 1024
_MAX_OVERLAP_SIGNING_KEYS = 10
_PUBLIC_PEM_ENVELOPE = re.compile(
    r"\s*-----BEGIN (PUBLIC KEY|RSA PUBLIC KEY)-----\r?\n"
    r"(?:[A-Za-z0-9+/=]+\r?\n)+"
    r"-----END \1-----\s*",
    re.ASCII,
)
_PRIVATE_PEM_ENVELOPE = re.compile(
    r"\s*-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY)-----\r?\n"
    r"(?:[A-Za-z0-9+/=]+\r?\n)+"
    r"-----END \1-----\s*",
    re.ASCII,
)


class BlackboardSigningKeyConfigurationError(RuntimeError):
    """Bounded signal that the deployment signing-key contract is invalid."""


class BlackboardPlatformConfigurationError(RuntimeError):
    """Bounded signal that Blackboard platform registrations are unusable."""


@dataclass(frozen=True)
class BlackboardSigningKeySnapshot:
    """Immutable per-process signer and public verification set."""

    private_pem: str
    public_pem: str
    jwks_json: str


def _read_signing_key(path_value: str | None) -> bytes:
    if not isinstance(path_value, str) or not path_value.strip():
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    try:
        path = Path(path_value.strip())
        if not path.is_file() or path.stat().st_size > _MAX_SIGNING_KEY_BYTES:
            raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
        with path.open("rb") as key_file:
            value = key_file.read(_MAX_SIGNING_KEY_BYTES + 1)
    except BlackboardSigningKeyConfigurationError:
        raise
    except OSError:
        raise BlackboardSigningKeyConfigurationError(
            "signing_key_unavailable"
        ) from None
    if not value or len(value) > _MAX_SIGNING_KEY_BYTES:
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    return value


def _strict_pem_envelope(value: bytes, pattern: re.Pattern[str]) -> bytes:
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        raise BlackboardSigningKeyConfigurationError(
            "signing_key_unavailable"
        ) from None
    if pattern.fullmatch(decoded) is None:
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    return value


def _base64url_uint(value: int) -> str:
    encoded = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _public_jwk(public_key: rsa.RSAPublicKey) -> Dict[str, str]:
    numbers = public_key.public_numbers()
    modulus = _base64url_uint(numbers.n)
    exponent = _base64url_uint(numbers.e)
    thumbprint_input = json.dumps(
        {"e": exponent, "kty": "RSA", "n": modulus},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    kid = base64.urlsafe_b64encode(hashlib.sha256(thumbprint_input).digest())
    return {
        "kty": "RSA",
        "n": modulus,
        "e": exponent,
        "kid": kid.rstrip(b"=").decode("ascii"),
        "alg": "RS256",
        "use": "sig",
    }


def _active_signing_material():
    private_pem = _strict_pem_envelope(
        _read_signing_key(os.getenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH")),
        _PRIVATE_PEM_ENVELOPE,
    )
    public_pem = _strict_pem_envelope(
        _read_signing_key(os.getenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH")),
        _PUBLIC_PEM_ENVELOPE,
    )
    try:
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        public_key = serialization.load_pem_public_key(public_pem)
    except (TypeError, UnicodeDecodeError, ValueError, UnsupportedAlgorithm):
        raise BlackboardSigningKeyConfigurationError(
            "signing_key_unavailable"
        ) from None
    if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(
        public_key, rsa.RSAPublicKey
    ):
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    if private_key.key_size < 2048 or public_key.key_size < 2048:
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
    canonical_private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    canonical_public_pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key, canonical_private_pem, canonical_public_pem


def _load_signing_snapshot() -> BlackboardSigningKeySnapshot:
    _, active_public_key, private_pem, public_pem = _active_signing_material()
    keys = [_public_jwk(active_public_key)]
    seen = {keys[0]["kid"]}
    overlap_value = os.getenv("BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS", "")
    if overlap_value:
        raw_paths = overlap_value.split(",")
        if len(raw_paths) > _MAX_OVERLAP_SIGNING_KEYS or any(
            not path.strip() for path in raw_paths
        ):
            raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
        for path in raw_paths:
            try:
                overlap_key = serialization.load_pem_public_key(
                    _strict_pem_envelope(_read_signing_key(path), _PUBLIC_PEM_ENVELOPE)
                )
            except BlackboardSigningKeyConfigurationError:
                raise
            except (TypeError, ValueError, UnsupportedAlgorithm):
                raise BlackboardSigningKeyConfigurationError(
                    "signing_key_unavailable"
                ) from None
            if not isinstance(overlap_key, rsa.RSAPublicKey):
                raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
            if overlap_key.key_size < 2048:
                raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
            jwk = _public_jwk(overlap_key)
            if jwk["kid"] not in seen:
                keys.append(jwk)
                seen.add(jwk["kid"])
    return BlackboardSigningKeySnapshot(
        private_pem=private_pem.decode("ascii"),
        public_pem=public_pem.decode("ascii"),
        jwks_json=json.dumps({"keys": keys}, separators=(",", ":")),
    )


def _bounded_platform_string(value: Any, *, maximum: int = 2048) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= maximum
        and value.isprintable()
        and "\x00" not in value
    )


def _validate_platform_config(config: Any) -> Dict[str, Any]:
    if not isinstance(config, dict) or not config or len(config) > 100:
        raise BlackboardPlatformConfigurationError("platform_config_invalid")
    registration_count = 0
    for issuer, issuer_config in config.items():
        if not _bounded_platform_string(issuer):
            raise BlackboardPlatformConfigurationError("platform_config_invalid")
        registrations = (
            issuer_config if isinstance(issuer_config, list) else [issuer_config]
        )
        if not registrations or len(registrations) > 100:
            raise BlackboardPlatformConfigurationError("platform_config_invalid")
        for registration in registrations:
            if not isinstance(registration, dict):
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
            if not all(
                _bounded_platform_string(registration.get(field))
                for field in ("client_id", "auth_login_url", "auth_token_url")
            ):
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
            deployments = registration.get("deployment_ids")
            if (
                not isinstance(deployments, list)
                or not deployments
                or len(deployments) > 100
                or not all(
                    _bounded_platform_string(value, maximum=512)
                    for value in deployments
                )
            ):
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
            key_set_url = registration.get("key_set_url")
            key_set = registration.get("key_set")
            if not _bounded_platform_string(key_set_url) and not (
                isinstance(key_set, dict) and bool(key_set)
            ):
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
            if "default" in registration and not isinstance(
                registration["default"], bool
            ):
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
            registration_count += 1
            if registration_count > 500:
                raise BlackboardPlatformConfigurationError("platform_config_invalid")
    if not registration_count:
        raise BlackboardPlatformConfigurationError("platform_config_invalid")
    return config


# =============================================================================
# Models
# =============================================================================


class BlackboardLaunchData(BaseModel):
    """Data extracted from Blackboard LTI launch"""

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
    platform_id: str  # Blackboard instance URL
    client_id: str
    nonce: str
    custom_params: Dict[str, Any] = {}
    # Blackboard-specific fields
    blackboard_user_uuid: Optional[str] = None
    blackboard_course_uuid: Optional[str] = None


class BlackboardPlatformConfig(BaseModel):
    """Configuration for a Blackboard Learn instance"""

    issuer: str  # e.g., "https://university.blackboard.com"
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
# Blackboard LTI Service
# =============================================================================


class BlackboardLTIService:
    """
    Blackboard LTI 1.3 Integration Service

    Handles:
    - OIDC login flow
    - LTI launch validation
    - Deep linking for content
    - Grade passback (AGS)
    - Roster access (NRPS)
    """

    # Blackboard standard endpoints (may vary by instance)
    BLACKBOARD_AUTH_PATH = "/learn/api/public/v1/oauth2/authorizationcode"
    BLACKBOARD_TOKEN_PATH = "/learn/api/public/v1/oauth2/token"
    BLACKBOARD_JWKS_PATH = "/learn/api/public/v1/jwt/jwks"

    def __init__(self, config_file: str = None):
        """
        Initialize Blackboard LTI service.

        Args:
            config_file: Path to LTI configuration JSON file
        """
        self.config_file = config_file or os.getenv(
            "BLACKBOARD_LTI_CONFIG_FILE", "blackboard_lti_config.json"
        )
        try:
            self._signing_snapshot = _load_signing_snapshot()
        except BlackboardSigningKeyConfigurationError:
            self._signing_snapshot = None
        self._tool_config = None
        self._platform_config_error_source = None
        self._load_config()

    def _load_config(self):
        """Load LTI tool configuration from file or environment"""
        # An existing selected file is authoritative and supports deployments
        # with multiple Blackboard issuers/clients without duplicate env state.
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as config_handle:
                    file_config = json.load(config_handle)
                self._tool_config = ToolConfDict(_validate_platform_config(file_config))
                self._attach_signing_keys(file_config)
                logger.info("Loaded Blackboard LTI config file")
            except Exception as exc:
                self._platform_config_error_source = "file"
                self._tool_config = None
                logger.warning(
                    "Blackboard LTI config file is invalid: %s",
                    type(exc).__name__,
                )
            return

        # Get Blackboard instance URL from environment
        blackboard_url = os.getenv("BLACKBOARD_URL", "").rstrip("/")
        client_id = os.getenv("BLACKBOARD_CLIENT_ID", "")
        deployment_id = os.getenv("BLACKBOARD_DEPLOYMENT_ID", "")

        if not blackboard_url or not client_id:
            logger.warning(
                "Blackboard LTI not configured. Set BLACKBOARD_URL and BLACKBOARD_CLIENT_ID."
            )
            self._tool_config = None
            return

        # Default configuration for Blackboard
        default_config = {
            blackboard_url: [
                {
                    "default": True,
                    "client_id": client_id,
                    "deployment_ids": [deployment_id] if deployment_id else [],
                    "auth_login_url": f"{blackboard_url}{self.BLACKBOARD_AUTH_PATH}",
                    "auth_token_url": f"{blackboard_url}{self.BLACKBOARD_TOKEN_PATH}",
                    "key_set_url": f"{blackboard_url}{self.BLACKBOARD_JWKS_PATH}",
                }
            ]
        }

        # Use environment-based config
        try:
            self._tool_config = ToolConfDict(_validate_platform_config(default_config))
            self._attach_signing_keys(default_config)
            logger.info("Using environment-based Blackboard LTI config")
        except Exception as exc:
            self._platform_config_error_source = "environment"
            self._tool_config = None
            logger.warning(
                "Blackboard LTI environment configuration is invalid: %s",
                type(exc).__name__,
            )

    def _attach_signing_keys(self, config: Dict[str, Any]) -> None:
        """Bind the deployment-global active pair to every Blackboard client."""
        snapshot = self._signing_snapshot
        if snapshot is None:
            return
        if self._tool_config is None:
            return
        for issuer, issuer_config in config.items():
            clients = (
                issuer_config if isinstance(issuer_config, list) else [issuer_config]
            )
            for client in clients:
                client_id = (
                    client.get("client_id") if isinstance(client, dict) else None
                )
                self._tool_config.set_private_key(
                    issuer, snapshot.private_pem, client_id
                )
                self._tool_config.set_public_key(issuer, snapshot.public_pem, client_id)

    def get_tool_config(self) -> Optional[ToolConfDict]:
        """Get the tool configuration for PyLTI1p3"""
        return self._tool_config

    def is_configured(self) -> bool:
        """Check if LTI is properly configured"""
        return self._tool_config is not None and self._signing_snapshot is not None

    def configuration_message(self) -> str:
        """Return a bounded operator-facing readiness explanation."""
        if self._platform_config_error_source == "file":
            return (
                "BLACKBOARD_LTI_CONFIG_FILE must contain a valid Blackboard LTI "
                "platform configuration."
            )
        if self._tool_config is None:
            return (
                "Set BLACKBOARD_URL, BLACKBOARD_CLIENT_ID, and "
                "BLACKBOARD_DEPLOYMENT_ID to enable Blackboard LTI."
            )
        if self._signing_snapshot is None:
            return (
                "Set BLACKBOARD_LTI_PRIVATE_KEY_PATH and "
                "BLACKBOARD_LTI_PUBLIC_KEY_PATH to a matching RSA 2048+ pair; "
                "overlap paths must contain public PEMs."
            )
        return "Blackboard LTI integration ready"

    def get_signing_jwks(self) -> Dict[str, List[Dict[str, str]]]:
        """Return the signer-coherent immutable public verification snapshot."""
        if self._signing_snapshot is None:
            raise BlackboardSigningKeyConfigurationError("signing_key_unavailable")
        return json.loads(self._signing_snapshot.jwks_json)

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

        Args:
            request_params: Parameters from the Blackboard login request
            target_link_uri: The final launch URL
            session_service: Session storage service
            cookie_service: Cookie management service

        Returns:
            Redirect URL for OIDC authentication
        """
        if not self.is_configured():
            raise ValueError("Blackboard LTI not configured")

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
        self,
        request_params: Dict[str, str],
        session_service: Any,
        cookie_service: Any,
    ) -> MessageLaunch:
        """
        Validate an LTI launch request and return the message launch object.

        Args:
            request_params: Parameters from the launch request
            session_service: Session storage service
            cookie_service: Cookie management service

        Returns:
            MessageLaunch object with validated launch data
        """
        if not self.is_configured():
            raise ValueError("Blackboard LTI not configured")

        message_launch = MessageLaunch(
            request_params,
            self._tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
        )

        return message_launch

    def extract_launch_data(
        self, message_launch: MessageLaunch
    ) -> BlackboardLaunchData:
        """
        Extract structured data from a validated LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            BlackboardLaunchData with user, course, and context information
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

        # Extract custom parameters (Blackboard-specific)
        custom_params = launch_data.get(
            "https://purl.imsglobal.org/spec/lti/claim/custom", {}
        )

        # Extract Blackboard-specific identifiers
        blackboard_user_uuid = custom_params.get("user_uuid")
        blackboard_course_uuid = custom_params.get("course_uuid")

        return BlackboardLaunchData(
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
            blackboard_user_uuid=blackboard_user_uuid,
            blackboard_course_uuid=blackboard_course_uuid,
        )

    def get_issuer_from_launch(self, message_launch: MessageLaunch) -> str:
        """
        Extract the issuer (platform URL) from an LTI launch.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            The issuer URL (e.g., "https://university.blackboard.com")
        """
        launch_data = message_launch.get_launch_data()
        return launch_data.get("iss", "")

    def get_client_id_from_launch(self, message_launch: MessageLaunch) -> str:
        """
        Extract the client_id from an LTI launch.

        The client_id is in the 'aud' (audience) claim. This can be a string
        or a list of strings; we return the first one.

        Args:
            message_launch: Validated MessageLaunch object

        Returns:
            The client_id assigned by the LTI platform
        """
        launch_data = message_launch.get_launch_data()
        aud = launch_data.get("aud", "")

        # Handle case where aud is a list
        if isinstance(aud, list):
            return aud[0] if aud else ""

        return str(aud)

    # =========================================================================
    # Deep Linking
    # =========================================================================

    def is_deep_link_launch(self, message_launch: MessageLaunch) -> bool:
        """Check if this is a deep linking launch"""
        return message_launch.is_deep_link_launch()

    def create_deep_link_response(
        self,
        message_launch: MessageLaunch,
        content_items: List[DeepLinkContent],
    ) -> str:
        """
        Create a deep linking response with content items.

        Args:
            message_launch: The original message launch
            content_items: List of content items to return to Blackboard

        Returns:
            HTML form that auto-submits to Blackboard with the content items
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
        content_id: str = None,
        scan_type: str = "document",
    ) -> DeepLinkContent:
        """
        Create a content item for an accessibility scan.

        Args:
            title: Display title for the content item
            launch_url: URL to launch when item is clicked
            content_id: Optional Blackboard content ID to scan
            scan_type: Type of scan (document, image, video)

        Returns:
            DeepLinkContent item configured for accessibility scanning
        """
        custom_params = {
            "scan_type": scan_type,
        }
        if content_id:
            custom_params["blackboard_content_id"] = content_id

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

    def submit_compliance_score(
        self,
        message_launch: MessageLaunch,
        user_id: str,
        compliance_score: float,
        max_score: float = 100.0,
        comment: str = None,
    ) -> GradePassbackResult:
        """
        Submit a compliance score as a grade to Blackboard.

        Args:
            message_launch: The validated message launch
            user_id: Blackboard user ID to submit grade for
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

            # Check if line item exists
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
                f"Submitted compliance score {compliance_score} to Blackboard for user {user_id}"
            )

            return GradePassbackResult(
                success=True,
                user_id=user_id,
                score=compliance_score,
                max_score=max_score,
                comment=comment,
            )

        except Exception as exc:
            logger.error("Blackboard grade passback failed: %s", type(exc).__name__)
            return GradePassbackResult(
                success=False,
                user_id=user_id,
                score=compliance_score,
                max_score=max_score,
                error="Blackboard grade passback failed",
            )

    # =========================================================================
    # Names and Role Provisioning Service (NRPS)
    # =========================================================================

    def has_nrps(self, message_launch: MessageLaunch) -> bool:
        """Check if Names and Role Provisioning Service is available"""
        return message_launch.has_nrps()

    def get_course_members(self, message_launch: MessageLaunch) -> List[Dict[str, Any]]:
        """
        Get course members (roster) from Blackboard.

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

    def generate_lti_config_json(self, base_url: str) -> Dict[str, Any]:
        """
        Generate LTI configuration JSON for Blackboard registration.

        This can be used when registering the tool in Blackboard Admin.

        Args:
            base_url: Base URL of the Aelira application

        Returns:
            Dict that can be used for Blackboard LTI registration
        """
        return {
            "title": "Aelira Accessibility Scanner",
            "description": "WCAG 2.1 accessibility scanning and remediation for educational content",
            "oidc_initiation_url": f"{base_url}/lti/blackboard/login",
            "target_link_uri": f"{base_url}/lti/blackboard/launch",
            "scopes": [
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score",
                "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
            ],
            "messages": [
                {
                    "type": "LtiResourceLinkRequest",
                    "target_link_uri": f"{base_url}/lti/blackboard/launch",
                    "label": "Accessibility Scanner",
                },
                {
                    "type": "LtiDeepLinkingRequest",
                    "target_link_uri": f"{base_url}/lti/blackboard/deep-link",
                    "label": "Add Accessibility Scan",
                },
            ],
            "custom_parameters": {
                "user_uuid": "$User.id",
                "course_uuid": "$Context.id",
                "content_id": "$Content.id",
            },
            "public_jwk_url": f"{base_url}/lti/blackboard/jwks",
        }


# =============================================================================
# Session/Cookie Adapters (reuse from Canvas module)
# =============================================================================


class BlackboardSessionService:
    """
    Session service adapter for Blackboard LTI.

    Stores LTI session data in-memory or Redis.
    """

    def __init__(self, redis_client=None):
        self._sessions: Dict[str, Dict] = {}
        self._redis = redis_client

    def get(self, key: str) -> Optional[str]:
        if self._redis:
            return self._redis.get(f"blackboard_lti_session:{key}")
        return self._sessions.get(key)

    def set(self, key: str, value: str, exp: int = 3600):
        if self._redis:
            self._redis.setex(f"blackboard_lti_session:{key}", exp, value)
        else:
            self._sessions[key] = value

    def delete(self, key: str):
        if self._redis:
            self._redis.delete(f"blackboard_lti_session:{key}")
        else:
            self._sessions.pop(key, None)


class BlackboardCookieService:
    """
    Cookie service adapter for Blackboard LTI.

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

_blackboard_lti_service: Optional[BlackboardLTIService] = None


def get_blackboard_lti_service() -> BlackboardLTIService:
    """Get the singleton Blackboard LTI service instance"""
    global _blackboard_lti_service
    if _blackboard_lti_service is None:
        _blackboard_lti_service = BlackboardLTIService()
    return _blackboard_lti_service


# =============================================================================
# Testing
# =============================================================================


def test_lti_config_generation():
    """Test LTI configuration generation"""
    service = BlackboardLTIService()
    config = service.generate_lti_config_json("https://dashboard.example.com")

    print("=" * 60)
    print("Blackboard LTI Registration Configuration")
    print("=" * 60)
    print(json.dumps(config, indent=2))
    print("\nUse this configuration when registering in Blackboard Admin")


if __name__ == "__main__":
    test_lti_config_generation()
