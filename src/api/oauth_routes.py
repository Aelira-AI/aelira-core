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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["oauth"])

# Tiers that allow OAuth login
OAUTH_ALLOWED_TIERS = {"department", "university", "trial"}


def _check_oauth_tier(db: Session, email: str) -> tuple[bool, str, Department | None]:
    """
    Check if user's email domain belongs to a department tier account.

    Returns:
        Tuple of (is_allowed, message, department)
    """
    # Check if user exists and belongs to allowed tier
    user = db.query(User).filter(User.email == email.lower()).first()
    if user:
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )
        if department and department.tier in OAUTH_ALLOWED_TIERS:
            return True, "OK", department
        return (
            False,
            "OAuth login is only available for department and university tier accounts. "
            "Please use magic link login or upgrade your plan.",
            department,
        )

    # User doesn't exist - check if email domain matches any department
    email_domain = email.split("@")[1].lower() if "@" in email else ""

    # Find departments by contact email domain
    departments = (
        db.query(Department).filter(Department.tier.in_(OAUTH_ALLOWED_TIERS)).all()
    )

    for dept in departments:
        if dept.contact_email and email_domain in dept.contact_email.lower():
            return True, "OK", dept

    return (
        False,
        "OAuth login is only available for department and university tier accounts. "
        "Please use magic link login or contact your institution's admin.",
        None,
    )


# =============================================================================
# Google OAuth
# =============================================================================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@router.get("/google/login")
async def google_login(request: Request):
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

    logger.info("Initiating Google OAuth login")
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db_dependency),
):
    """
    Handle Google OAuth callback.

    Validates state, exchanges code for tokens, creates/updates user, creates session.
    """
    settings = get_settings()
    session_service = get_session_service()

    # Check for errors
    if error:
        logger.warning(f"Google OAuth error: {error}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=oauth_denied"
        )

    # Validate state (CSRF protection) - timing-safe comparison
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        logger.warning("Google OAuth state mismatch")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=invalid_state"
        )

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
                logger.error(f"Google token error: {token_data}")
                return RedirectResponse(
                    url=f"{settings.magic_link_base_url}/login?error=token_error"
                )

            # Get user info
            userinfo_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo = userinfo_response.json()

    except Exception as e:
        logger.error(f"Google OAuth error: {e}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=oauth_error"
        )

    email = userinfo.get("email", "").lower()
    google_id = userinfo.get("id")
    name = userinfo.get("name", email.split("@")[0])
    picture = userinfo.get("picture")

    # Check if user's tier allows OAuth
    is_allowed, message, department = _check_oauth_tier(db, email)
    if not is_allowed:
        logger.warning(f"Google OAuth denied for {email}: {message}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=tier_required&message={message}"
        )

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

        blocked, block_reason = AccountDeletionService.is_email_blocked(db, email)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=block_reason
                or "This email address is not available for registration.",
            )

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

    db.commit()
    db.refresh(user)

    # Get client info for session
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Create session
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    # Redirect to dashboard with cookies
    response = RedirectResponse(
        url=f"{settings.magic_link_base_url}/dashboard",
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

    # Clear OAuth state cookie
    response.delete_cookie(key="oauth_state")

    logger.info(f"Google OAuth login successful for {email}")
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
async def microsoft_login(request: Request):
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

    logger.info("Initiating Microsoft OAuth login")
    return response


@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    db: Session = Depends(get_db_dependency),
):
    """
    Handle Microsoft OAuth callback.
    """
    settings = get_settings()
    session_service = get_session_service()

    # Check for errors
    if error:
        logger.warning(f"Microsoft OAuth error: {error} - {error_description}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=oauth_denied"
        )

    # Validate state - timing-safe comparison
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        logger.warning("Microsoft OAuth state mismatch")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=invalid_state"
        )

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
                logger.error(f"Microsoft token error: {token_data}")
                return RedirectResponse(
                    url=f"{settings.magic_link_base_url}/login?error=token_error"
                )

            # Get user info from Graph API
            userinfo_response = await client.get(
                MICROSOFT_GRAPH_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo = userinfo_response.json()

    except Exception as e:
        logger.error(f"Microsoft OAuth error: {e}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=oauth_error"
        )

    email = (userinfo.get("mail") or userinfo.get("userPrincipalName", "")).lower()
    microsoft_id = userinfo.get("id")
    name = userinfo.get("displayName", email.split("@")[0])

    if not email:
        logger.error("Microsoft OAuth: No email returned")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=no_email"
        )

    # Check tier
    is_allowed, message, department = _check_oauth_tier(db, email)
    if not is_allowed:
        logger.warning(f"Microsoft OAuth denied for {email}: {message}")
        return RedirectResponse(
            url=f"{settings.magic_link_base_url}/login?error=tier_required"
        )

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

        blocked, block_reason = AccountDeletionService.is_email_blocked(db, email)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=block_reason
                or "This email address is not available for registration.",
            )

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

    db.commit()
    db.refresh(user)

    # Get client info
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Create session
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    # Redirect with cookies
    response = RedirectResponse(
        url=f"{settings.magic_link_base_url}/dashboard",
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

    logger.info(f"Microsoft OAuth login successful for {email}")
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
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

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
        "oauth_allowed": False,
        "message": None,
    }

    if email:
        is_allowed, message, _ = _check_oauth_tier(db, email)
        result["oauth_allowed"] = is_allowed
        if not is_allowed:
            result["message"] = message

    return result
