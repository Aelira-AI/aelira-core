"""
OAuth Authentication Routes

Provides OAuth login for department tier users:
- Google OAuth
- Microsoft OAuth (Azure AD)

Security:
- OAuth is only available for department/university tier users
- Individual tier users must use magic links
- This encourages institutional adoption
"""

import secrets
import logging
from urllib.parse import urlencode
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import httpx

from ..db.database import get_db_dependency
from ..db.models import User, Department, AuthProvider, UserRole
from ..auth.session_service import get_session_service
from ..auth.auth_service import RateLimiter
from ..config.settings import get_settings
from ..security.client_ip import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["oauth"])

# Tiers that allow OAuth login
OAUTH_ALLOWED_TIERS = {"department"}
OAUTH_NEXT_COOKIE = "oauth_next"
OAUTH_ACCOUNT_UNAVAILABLE = "account_unavailable"
OAUTH_NOT_ALLOWED_MESSAGE = (
    "No account exists for this email. Ask your administrator for an invitation."
)


def _safe_next_path(next_path: str | None) -> str:
    """Resolve an untrusted continuation using the dashboard's path policy."""
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or next_path.startswith("/\\")
    ):
        return "/dashboard"
    return next_path


def _set_oauth_next_cookie(
    response: RedirectResponse, next_path: str | None, settings
) -> None:
    response.set_cookie(
        key=OAUTH_NEXT_COOKIE,
        value=_safe_next_path(next_path),
        max_age=600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


def _oauth_error_response(settings, error: str, message: str | None = None):
    query = {"error": error}
    if message:
        query["message"] = message
    response = RedirectResponse(
        url=f"{settings.magic_link_base_url.rstrip('/')}/login?{urlencode(query)}"
    )
    response.delete_cookie(key="oauth_state")
    response.delete_cookie(key=OAUTH_NEXT_COOKIE)
    return response


def _check_oauth_tier(db: Session, email: str) -> tuple[bool, str, Department | None]:
    """
    Check if user's email domain belongs to a department tier account.

    Returns:
        Tuple of (is_allowed, message, department)
    """
    # Existing users can always log in via OAuth, whatever their workspace type
    user = db.query(User).filter(User.email == email.lower()).first()
    if user:
        if user.is_active is False:
            return False, OAUTH_ACCOUNT_UNAVAILABLE, None
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )
        return True, "OK", department

    # User doesn't exist — allow just-in-time provisioning only into a
    # department workspace whose contact email shares this email's domain.
    # This is delegated provisioning (the admin created the department);
    # unknown domains do not get accounts via OAuth.
    email_domain = email.split("@")[1].lower() if "@" in email else ""

    departments = (
        db.query(Department).filter(Department.tier.in_(OAUTH_ALLOWED_TIERS)).all()
    )

    for dept in departments:
        if not dept.contact_email or "@" not in dept.contact_email:
            continue
        # Exact domain equality, not substring: "harvard.edu" must not match a
        # department contact at "harvard.edu.attacker.com", nor "edu" match all.
        contact_domain = dept.contact_email.rsplit("@", 1)[1].lower().rstrip(".")
        if email_domain == contact_domain:
            return True, "OK", dept

    return (
        False,
        OAUTH_NOT_ALLOWED_MESSAGE,
        None,
    )


# =============================================================================
# Google OAuth
# =============================================================================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@router.get("/google/login")
async def google_login(request: Request, next: str | None = None):
    """
    Initiate Google OAuth login.

    Redirects to Google's OAuth consent screen.
    State parameter is used to prevent CSRF.
    """
    settings = get_settings()

    if not settings.google_oauth_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured",
        )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Store state in session (using cookie for simplicity)
    # In production, use Redis or database
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,  # 10 minutes
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    _set_oauth_next_cookie(response, next, settings)

    logger.info("Initiating Google OAuth login")
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db_dependency),
):
    """Handle Google OAuth callbacks through a cleanup-safe public boundary."""
    settings = get_settings()
    try:
        return await _google_callback_impl(request, code, state, error, db)
    except Exception as exc:
        db.rollback()
        logger.error("Unexpected Google OAuth callback failure: %s", type(exc).__name__)
        return _oauth_error_response(settings, "oauth_error")


async def _google_callback_impl(
    request: Request,
    code: str | None,
    state: str | None,
    error: str | None,
    db: Session,
):
    """
    Handle Google OAuth callback.

    Validates state, exchanges code for tokens, creates/updates user, creates session.
    """
    settings = get_settings()
    session_service = get_session_service()

    # Check for errors
    if error:
        logger.warning("Google OAuth authorization was denied")
        return _oauth_error_response(settings, "oauth_denied")

    # Validate state (CSRF protection) - timing-safe comparison
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not state or not secrets.compare_digest(stored_state, state):
        logger.warning("Google OAuth state mismatch")
        return _oauth_error_response(settings, "invalid_state")

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.google_oauth_redirect_uri,
                },
            )
            token_data = token_response.json()

            if "error" in token_data:
                logger.error("Google OAuth token exchange was rejected")
                return _oauth_error_response(settings, "token_error")

            # Get user info
            userinfo_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo = userinfo_response.json()

    except Exception as exc:
        logger.error("Google OAuth exchange failed: %s", type(exc).__name__)
        return _oauth_error_response(settings, "oauth_error")

    email = userinfo.get("email", "").lower()
    google_id = userinfo.get("id")
    name = userinfo.get("name", email.split("@")[0])
    picture = userinfo.get("picture")

    # Check if user's tier allows OAuth
    is_allowed, message, department = _check_oauth_tier(db, email)
    if not is_allowed:
        logger.warning("Google OAuth account access denied")
        if message == OAUTH_ACCOUNT_UNAVAILABLE:
            return _oauth_error_response(settings, "oauth_error")
        return _oauth_error_response(settings, "tier_required", message)

    # Get or create user
    user = db.query(User).filter(User.email == email).first()

    if user:
        # Update existing user
        user.google_id = google_id
        user.name = name or user.name
        user.picture_url = picture
        user.email_verified = True
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
        if user.auth_provider == AuthProvider.MAGIC_LINK:
            user.auth_provider = AuthProvider.GOOGLE
    else:
        # Check if email is blocked (deactivated/deleted account)
        from ..services.account_deletion_service import AccountDeletionService

        blocked, _block_reason = AccountDeletionService.is_email_blocked(db, email)
        if blocked:
            logger.warning("Google OAuth registration blocked")
            return _oauth_error_response(settings, "oauth_error")

        # Create new user
        user = User(
            email=email,
            google_id=google_id,
            name=name,
            picture_url=picture,
            department_id=department.id,
            role=UserRole.FACULTY,
            auth_provider=AuthProvider.GOOGLE,
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()

    # Get client info for session
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Create session
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    # Redirect to the validated same-origin continuation with cookies.
    next_path = _safe_next_path(request.cookies.get(OAUTH_NEXT_COOKIE))
    response = RedirectResponse(
        url=f"{settings.magic_link_base_url.rstrip('/')}{next_path}",
        status_code=302,
    )

    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }

    if settings.env == "production" and settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain

    response.set_cookie(
        key="aelira_access",
        value=access_token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        **cookie_settings,
    )
    response.set_cookie(
        key="aelira_refresh",
        value=refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        **cookie_settings,
    )

    # Clear one-time OAuth cookies.
    response.delete_cookie(key="oauth_state")
    response.delete_cookie(key=OAUTH_NEXT_COOKIE)

    logger.info("Google OAuth login successful for user %s", user.id)
    return response


# =============================================================================
# Microsoft OAuth (Azure AD)
# =============================================================================


def _get_microsoft_auth_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"


def _get_microsoft_token_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


MICROSOFT_GRAPH_URL = "https://graph.microsoft.com/v1.0/me"


@router.get("/microsoft/login")
async def microsoft_login(request: Request, next: str | None = None):
    """
    Initiate Microsoft OAuth login.

    Redirects to Microsoft's OAuth consent screen.
    """
    settings = get_settings()

    if not settings.microsoft_oauth_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Microsoft OAuth is not configured",
        )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": settings.microsoft_oauth_client_id,
        "redirect_uri": settings.microsoft_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile User.Read",
        "state": state,
        "response_mode": "query",
    }

    auth_url = f"{_get_microsoft_auth_url(settings.microsoft_oauth_tenant_id)}?{urlencode(params)}"

    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    _set_oauth_next_cookie(response, next, settings)

    logger.info("Initiating Microsoft OAuth login")
    return response


@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db_dependency),
):
    """Handle Microsoft OAuth callbacks through a cleanup-safe public boundary."""
    settings = get_settings()
    try:
        return await _microsoft_callback_impl(
            request, code, state, error, error_description, db
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "Unexpected Microsoft OAuth callback failure: %s", type(exc).__name__
        )
        return _oauth_error_response(settings, "oauth_error")


async def _microsoft_callback_impl(
    request: Request,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
    db: Session,
):
    """
    Handle Microsoft OAuth callback.
    """
    settings = get_settings()
    session_service = get_session_service()

    # Check for errors
    if error:
        logger.warning("Microsoft OAuth authorization was denied")
        return _oauth_error_response(settings, "oauth_denied")

    # Validate state - timing-safe comparison
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not state or not secrets.compare_digest(stored_state, state):
        logger.warning("Microsoft OAuth state mismatch")
        return _oauth_error_response(settings, "invalid_state")

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                _get_microsoft_token_url(settings.microsoft_oauth_tenant_id),
                data={
                    "client_id": settings.microsoft_oauth_client_id,
                    "client_secret": settings.microsoft_oauth_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.microsoft_oauth_redirect_uri,
                    "scope": "openid email profile User.Read",
                },
            )
            token_data = token_response.json()

            if "error" in token_data:
                logger.error("Microsoft OAuth token exchange was rejected")
                return _oauth_error_response(settings, "token_error")

            # Get user info from Graph API
            userinfo_response = await client.get(
                MICROSOFT_GRAPH_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo = userinfo_response.json()

    except Exception as exc:
        logger.error("Microsoft OAuth exchange failed: %s", type(exc).__name__)
        return _oauth_error_response(settings, "oauth_error")

    email = (userinfo.get("mail") or userinfo.get("userPrincipalName", "")).lower()
    microsoft_id = userinfo.get("id")
    name = userinfo.get("displayName", email.split("@")[0])

    if not email:
        logger.error("Microsoft OAuth: No email returned")
        return _oauth_error_response(settings, "no_email")

    # Check tier
    is_allowed, message, department = _check_oauth_tier(db, email)
    if not is_allowed:
        logger.warning("Microsoft OAuth account access denied")
        if message == OAUTH_ACCOUNT_UNAVAILABLE:
            return _oauth_error_response(settings, "oauth_error")
        return _oauth_error_response(settings, "tier_required", message)

    # Get or create user
    user = db.query(User).filter(User.email == email).first()

    if user:
        user.microsoft_id = microsoft_id
        user.name = name or user.name
        user.email_verified = True
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
        if user.auth_provider == AuthProvider.MAGIC_LINK:
            user.auth_provider = AuthProvider.MICROSOFT
    else:
        # Check if email is blocked (deactivated/deleted account)
        from ..services.account_deletion_service import AccountDeletionService

        blocked, _block_reason = AccountDeletionService.is_email_blocked(db, email)
        if blocked:
            logger.warning("Microsoft OAuth registration blocked")
            return _oauth_error_response(settings, "oauth_error")

        user = User(
            email=email,
            microsoft_id=microsoft_id,
            name=name,
            department_id=department.id,
            role=UserRole.FACULTY,
            auth_provider=AuthProvider.MICROSOFT,
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()

    # Get client info
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Create session
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    # Redirect to the validated same-origin continuation with cookies.
    next_path = _safe_next_path(request.cookies.get(OAUTH_NEXT_COOKIE))
    response = RedirectResponse(
        url=f"{settings.magic_link_base_url.rstrip('/')}{next_path}",
        status_code=302,
    )

    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }

    if settings.env == "production" and settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain

    response.set_cookie(
        key="aelira_access",
        value=access_token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        **cookie_settings,
    )
    response.set_cookie(
        key="aelira_refresh",
        value=refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        **cookie_settings,
    )

    response.delete_cookie(key="oauth_state")
    response.delete_cookie(key=OAUTH_NEXT_COOKIE)

    logger.info("Microsoft OAuth login successful for user %s", user.id)
    return response


# =============================================================================
# OAuth Status Check
# =============================================================================


@router.get("/oauth/status")
async def oauth_status(
    request: Request,
    email: str = None,
    db: Session = Depends(get_db_dependency),
):
    """
    Check if OAuth is available for an email address.

    Used by the frontend to show/hide OAuth buttons.

    Security:
    - Rate limited to 60 requests per IP per hour (prevents enumeration)
    """
    settings = get_settings()

    # Rate limit by IP (60/hour - generous for UI but prevents enumeration abuse)
    client_ip = get_client_ip(request)

    ip_limit_key = f"oauth_status_ip:{client_ip}"
    ip_allowed, _ = RateLimiter.check_rate_limit(ip_limit_key, 60)
    if not ip_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    result = {
        "google_available": bool(settings.google_oauth_client_id),
        "microsoft_available": bool(settings.microsoft_oauth_client_id),
        # Do not reveal whether an email belongs to an active, inactive, or
        # unknown account. The callback performs the authoritative policy
        # check after provider authentication.
        "oauth_allowed": bool(
            settings.google_oauth_client_id or settings.microsoft_oauth_client_id
        ),
        "message": None,
    }

    return result
