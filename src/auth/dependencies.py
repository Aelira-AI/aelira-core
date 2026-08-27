"""
Authentication Dependencies for FastAPI Routes

Provides reusable authentication dependencies that can be imported
by any route file.

SECURITY:
- All endpoints require valid API key authentication by default
- Session cookie authentication is also supported (for dashboard users)
- Mock auth ONLY available with ALLOW_MOCK_AUTH=true (local dev only)
- Mock auth is NEVER allowed in production environment

Authentication Methods (checked in order):
1. Bearer token: Authorization: Bearer ***
2. Session cookie: aelira_access cookie (JWT from magic link login)
3. Mock auth: ALLOW_MOCK_AUTH=true (dev only)
"""

import logging
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..db.database import get_db_dependency
from ..db.models import APIKey, User, UserRole
from ..auth.lti_authorization import validate_lti_staff_token_payload

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme (doesn't auto-error so we can provide custom messages)
security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Immutable authorization context produced by authentication."""

    api_key: APIKey | None
    user_id: str
    department_id: str
    user_role: UserRole
    auth_method: Literal["api_key", "session", "lti", "mock"]
    lti_course_id: str | None = None
    lti_staff_role: str | None = None
    lti_account_wide: bool = False
    lti_platform: str | None = None

    def __post_init__(self) -> None:
        """Reject authorization contexts that cannot come from trusted auth."""

        if not isinstance(self.user_id, str) or not self.user_id:
            raise ValueError("Authenticated principals require a user id")
        if not isinstance(self.department_id, str) or not self.department_id:
            raise ValueError("Authenticated principals require a department id")
        if not isinstance(self.user_role, UserRole):
            raise ValueError("Authenticated principals require a valid user role")
        if self.auth_method not in {"api_key", "session", "lti", "mock"}:
            raise ValueError("Authenticated principals require a valid auth method")
        if type(self.lti_account_wide) is not bool:
            raise ValueError("LTI account scope must be boolean")

        if self.auth_method != "lti":
            if (
                self.lti_course_id is not None
                or self.lti_staff_role is not None
                or self.lti_account_wide
                or self.lti_platform is not None
            ):
                raise ValueError("Only LTI principals may carry LTI authorization")
            return

        if self.api_key is not None:
            raise ValueError("LTI principals cannot carry API keys")
        # Directly constructed legacy/test principals predate provider binding
        # and represent Canvas unless stated otherwise. Signed tokens never use
        # this fallback: _principal_from_lti_payload requires an explicit claim.
        if self.lti_platform is None:
            object.__setattr__(self, "lti_platform", "canvas")
        if self.lti_platform not in {"canvas", "brightspace", "blackboard"}:
            raise ValueError("Malformed LTI platform scope")
        if self.lti_staff_role == "Administrator":
            if not self.lti_account_wide or self.user_role is not UserRole.ADMIN:
                raise ValueError("Malformed LTI administrator principal")
            return
        if self.lti_staff_role in {
            "Instructor",
            "TeachingAssistant",
            "ContentDeveloper",
        }:
            if (
                self.lti_account_wide
                or self.user_role is not UserRole.FACULTY
                or not isinstance(self.lti_course_id, str)
                or not self.lti_course_id.strip()
            ):
                raise ValueError("Malformed LTI course-staff principal")
            return
        raise ValueError("Malformed LTI staff principal")

    def as_legacy_tuple(self) -> Tuple[Optional[APIKey], str, str]:
        """Return the historical dependency value consumed by existing routes."""

        return self.api_key, self.user_id, self.department_id


def _user_role(user: User) -> UserRole:
    """Normalize the database role while rejecting malformed identities."""

    role = user.role
    if role is None:
        return UserRole.FACULTY
    return role if isinstance(role, UserRole) else UserRole(role)


def _principal_from_lti_payload(
    payload: dict, db: Session
) -> AuthenticatedPrincipal | None:
    """Build an LTI principal only after canonical v2 claim validation."""

    user = validate_lti_staff_token_payload(payload, db)
    if user is None:
        return None

    course_id = payload.get("course_id")
    staff_role = payload.get("lti_staff_role")
    account_wide = payload.get("lti_account_wide")
    platform = payload.get("lti_platform")
    if (
        not isinstance(staff_role, str)
        or not isinstance(account_wide, bool)
        or platform not in {"canvas", "brightspace", "blackboard"}
        or (course_id is not None and not isinstance(course_id, str))
    ):
        return None

    try:
        return AuthenticatedPrincipal(
            api_key=None,
            user_id=str(user.id),
            department_id=str(user.department_id),
            user_role=_user_role(user),
            auth_method="lti",
            lti_course_id=course_id,
            lti_staff_role=staff_role,
            lti_account_wide=account_wide,
            lti_platform=platform,
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class ResolvedAccessToken:
    """Canonical result shared by protected routes and session validation."""

    user: User
    payload: dict
    principal: AuthenticatedPrincipal


def resolve_access_token(
    db: Session,
    token: str,
    *,
    session_service=None,
    jwt_service=None,
) -> ResolvedAccessToken | None:
    """Resolve a live normal session or a canonical v2 LTI access token."""
    if jwt_service is None:
        from ..auth.jwt_service import JWTService

        jwt_service = JWTService()
    payload = jwt_service.verify_access_token(token)
    if not payload:
        return None

    if payload.get("lti_launch") is True:
        principal = _principal_from_lti_payload(payload, db)
        if principal is None:
            return None
        user = (
            db.query(User)
            .filter(User.id == principal.user_id, User.is_active.is_(True))
            .first()
        )
        if user is None:
            return None
        return ResolvedAccessToken(user=user, payload=payload, principal=principal)

    if session_service is None:
        from ..auth.session_service import get_session_service

        session_service = get_session_service()
    result = session_service.validate_session(db, token)
    if not result:
        return None
    user, validated_payload = result
    try:
        principal = AuthenticatedPrincipal(
            api_key=None,
            user_id=str(user.id),
            department_id=str(user.department_id),
            user_role=_user_role(user),
            auth_method="session",
        )
    except ValueError:
        return None
    return ResolvedAccessToken(
        user=user, payload=validated_payload, principal=principal
    )


def get_authenticated_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> AuthenticatedPrincipal:
    """Authenticate once and retain the authorization context for policy checks."""
    from ..config.settings import get_settings
    from ..auth.auth_service import AuthService, RateLimiter
    from ..auth.session_service import get_session_service

    settings = get_settings()
    session_service = get_session_service()

    # Method 1: Check for Bearer token (API key, then JWT fallback)
    if credentials:
        token = credentials.credentials
        api_key = AuthService.validate_api_key(db, token)
        if api_key:
            owner = getattr(api_key, "user", None)
            if not isinstance(owner, User):
                owner = db.query(User).filter(User.id == api_key.user_id).first()
            if (
                owner is not None
                and owner.is_active is True
                and str(owner.department_id) == str(api_key.department_id)
            ):
                try:
                    principal = AuthenticatedPrincipal(
                        api_key=api_key,
                        user_id=str(owner.id),
                        department_id=str(owner.department_id),
                        user_role=_user_role(owner),
                        auth_method="api_key",
                    )
                except ValueError:
                    pass
                else:
                    # Retain only the validated, bounded principal so callers
                    # can audit a rate-limit denial without retaining the key.
                    request.state.authenticated_principal = principal
                    allowed, rate_headers = RateLimiter.check_rate_limit(
                        api_key.id, api_key.rate_limit_per_hour
                    )
                    if not allowed:
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=(
                                "Rate limit exceeded. Limit: "
                                f"{api_key.rate_limit_per_hour} requests/hour"
                            ),
                            headers=rate_headers,
                        )
                    return principal

        resolved = resolve_access_token(db, token, session_service=session_service)
        if resolved is not None:
            logger.debug(
                f"{resolved.principal.auth_method} Bearer auth for user "
                f"{resolved.principal.user_id}"
            )
            return resolved.principal

        logger.warning("Invalid Bearer token attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Method 2: Check for session cookie (dashboard and LTI users)
    access_token = request.cookies.get("aelira_access")
    if access_token:
        resolved = resolve_access_token(
            db, access_token, session_service=session_service
        )
        if resolved is not None:
            logger.debug(
                f"{resolved.principal.auth_method} cookie auth for user "
                f"{resolved.principal.user_id}"
            )
            return resolved.principal
        logger.debug("Invalid session cookie, will fall through to other auth methods")

    # Method 3: Mock auth (development only - strict positive checks)
    if settings.allow_mock_auth is True and settings.env.lower() == "development":
        mock_user_id = "dev-user-local"
        mock_dept_id = "dev-dept-local"
        logger.debug(
            "Mock auth active (ENV=development, ALLOW_MOCK_AUTH=true). "
            "Using stable dev identity."
        )
        try:
            from src.db.database import get_db
            from src.db.models import Department

            with get_db() as mock_db:
                if (
                    not mock_db.query(Department)
                    .filter(Department.id == mock_dept_id)
                    .first()
                ):
                    mock_db.add(
                        Department(
                            id=mock_dept_id,
                            name="Dev Department",
                            institution="Dev University",
                            contact_email="dev@localhost",
                            tier="department",
                            max_users=999,
                        )
                    )
                    mock_db.flush()
                if not mock_db.query(User).filter(User.id == mock_user_id).first():
                    mock_db.add(
                        User(
                            id=mock_user_id,
                            email="dev@localhost",
                            name="Dev User",
                            department_id=mock_dept_id,
                            role="admin",
                        )
                    )
                    mock_db.commit()
        except Exception as exc:
            logger.debug("Mock auth record setup skipped: %s", type(exc).__name__)

        return AuthenticatedPrincipal(
            api_key=None,
            user_id=mock_user_id,
            department_id=mock_dept_id,
            user_role=UserRole.ADMIN,
            auth_method="mock",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide 'Authorization: Bearer ***' header or login via dashboard.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_key_management_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> AuthenticatedPrincipal:
    """Authenticate key CRUD without stale Bearer state shadowing a session.

    Normal dashboard sessions are authoritative. LTI launch cookies cannot
    manage account-wide programmatic credentials. When a cookie is absent,
    invalid, or LTI-scoped, a valid database API key remains supported.
    """
    from ..auth.auth_service import AuthService, RateLimiter
    from ..auth.session_service import get_session_service

    cookie_token = request.cookies.get("aelira_access")
    cookie_was_lti = False
    if cookie_token:
        resolved = resolve_access_token(
            db, cookie_token, session_service=get_session_service()
        )
        if resolved is not None:
            if resolved.principal.auth_method == "session":
                return resolved.principal
            cookie_was_lti = resolved.principal.auth_method == "lti"

    if credentials is not None:
        api_key = AuthService.validate_api_key(db, credentials.credentials)
        if api_key is not None:
            owner = getattr(api_key, "user", None)
            if not isinstance(owner, User):
                owner = db.query(User).filter(User.id == api_key.user_id).first()
            if (
                owner is not None
                and owner.is_active is True
                and str(owner.department_id) == str(api_key.department_id)
            ):
                allowed, rate_headers = RateLimiter.check_rate_limit(
                    api_key.id, api_key.rate_limit_per_hour
                )
                if not allowed:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=(
                            "Rate limit exceeded. Limit: "
                            f"{api_key.rate_limit_per_hour} requests/hour"
                        ),
                        headers=rate_headers,
                    )
                try:
                    return AuthenticatedPrincipal(
                        api_key=api_key,
                        user_id=str(owner.id),
                        department_id=str(owner.department_id),
                        user_role=_user_role(owner),
                        auth_method="api_key",
                    )
                except ValueError:
                    pass

    if cookie_was_lti:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="LTI launch sessions cannot manage API keys",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Log in via dashboard or provide a valid API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_required_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> Tuple[Optional[APIKey], str, str]:
    """Backward-compatible adapter for routes expecting the legacy tuple."""

    return get_authenticated_principal(request, credentials, db).as_legacy_tuple()


def verify_department_access(
    requested_department_id: str,
    authenticated_department_id: str,
) -> None:
    """
    Verify that the authenticated user has access to the requested department.

    Raises:
        HTTPException 403: If user doesn't have access to the department
    """
    if requested_department_id != authenticated_department_id:
        logger.warning(
            "Department access denied for authenticated department %s",
            authenticated_department_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this department",
        )


# Alias for backward compatibility
get_api_key_or_mock = get_required_api_key
