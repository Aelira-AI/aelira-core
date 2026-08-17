"""
LTI Launch Handler — auto-provision users, one-time code exchange, auth bridge.

Converts a validated LTI 1.3 launch into an Aelira access token via a
short-lived one-time code that the dashboard exchanges on first load.

Flow:
  Canvas → POST /lti/launch → handle_lti_launch()
      ↳ finds-or-creates User, mints JWT, stores one-time code
      ↳ 302 → dashboard.example.com/lti?code=...
  Dashboard → POST /lti/exchange {code}
      ↳ returns {access_token, course_id}

AGS context is persisted so background jobs can push grades without a
live LTI session.
"""

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.auth.jwt_service import JWTService
from src.auth.redis_rate_limiter import get_redis_client
from src.config.settings import get_settings
from src.db.models import (
    AuthProvider,
    LTIAGSContext,
    LTIRegistration,
    User,
    UserRole,
)
from src.integrations.canvas_lti import CanvasLaunchData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory fallback when Redis is unavailable (dev only)
# ---------------------------------------------------------------------------
_code_store: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Role mapping helpers
# ---------------------------------------------------------------------------

_ADMIN_ROLE_FRAGMENTS = {"Instructor", "Administrator"}
_FACULTY_ROLE_FRAGMENTS = {
    "TeachingAssistant",
    "ContentDeveloper",
    "Mentor",
    "Learner",
}


def _map_lti_roles_to_aelira(lti_roles: list[str]) -> UserRole:
    """Return the highest Aelira role implied by the LTI role URIs."""
    for role_uri in lti_roles:
        for fragment in _ADMIN_ROLE_FRAGMENTS:
            if fragment in role_uri:
                return UserRole.ADMIN
    for role_uri in lti_roles:
        for fragment in _FACULTY_ROLE_FRAGMENTS:
            if fragment in role_uri:
                return UserRole.FACULTY
    # Default — unknown roles still get lowest privilege
    return UserRole.FACULTY


def _simplify_roles(lti_roles: list[str]) -> list[str]:
    """Extract short role names from full URIs for token claims."""
    names: list[str] = []
    for uri in lti_roles:
        # URIs end with #RoleName or /RoleName
        for sep in ("#", "/"):
            if sep in uri:
                names.append(uri.rsplit(sep, 1)[-1])
                break
        else:
            names.append(uri)
    return names


def _is_canvas_admin(roles: List[str]) -> bool:
    """Check if LTI roles indicate an account-level admin."""
    admin_patterns = [
        "Administrator",
        "urn:lti:sysrole:ims/lis/SysAdmin",
        "urn:lti:instrole:ims/lis/Administrator",
        "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
        "http://purl.imsglobal.org/vocab/lis/v2/system/person#Administrator",
    ]
    for role in roles:
        for pattern in admin_patterns:
            if pattern in role:
                return True
    return False


# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------


def handle_lti_launch(
    launch_data: CanvasLaunchData,
    registration: LTIRegistration,
    db: Session,
    platform: str = "canvas",
) -> str:
    """
    Find or create a user for this LTI launch, mint a JWT, store a
    one-time code, and return the redirect URL the caller should 302 to.

    Args:
        launch_data: Validated LTI launch claims.
        registration: The matched LTI registration row.
        db: Active database session (caller manages commit scope).

    Returns:
        Full redirect URL including ``?code=...``.
    """
    settings = get_settings()
    department_id = str(registration.department_id)

    # --- Resolve email ---
    email = launch_data.user_email
    if not email:
        issuer_domain = urlparse(launch_data.platform_id).hostname or "lti.local"
        email = f"{launch_data.user_id}@{issuer_domain}"

    # --- Find or create user ---
    user = (
        db.query(User)
        .filter(User.email == email, User.department_id == department_id)
        .first()
    )

    if user is None:
        # Check whether the email exists in a *different* department
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            # Disambiguate by appending a department fragment
            local, _, domain = email.partition("@")
            email = f"{local}+{department_id[:8]}@{domain}"

        aelira_role = _map_lti_roles_to_aelira(launch_data.roles)
        user = User(
            email=email,
            name=launch_data.user_name or email,
            department_id=department_id,
            role=aelira_role,
            auth_provider=AuthProvider.LTI,
            is_active=True,
        )
        db.add(user)
        db.flush()  # get user.id without full commit
        logger.info(
            "LTI auto-provisioned user",
            extra={
                "user_id": user.id,
                "email": email,
                "department_id": department_id,
            },
        )

    # Always stamp the LTI provenance (even for returning users)
    user.lti_source = f"{launch_data.platform_id}:{launch_data.user_id}"
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()

    # --- Mint access token ---
    jwt_service = JWTService()
    course_id = launch_data.course_id or ""

    # If extract_launch_data() couldn't resolve a course id (custom params
    # not sent, or the context claim wasn't recognized as a course), try any
    # raw custom-claim keys that carry a course/context id before giving up.
    # Resolved here — before the token/code are minted — so the JWT claim,
    # the stored one-time code payload, and the redirect URL all agree.
    if not course_id:
        for key in (
            "canvas_course_id",
            "custom_canvas_course_id",
            "custom_course_id",
            "context_id",
        ):
            candidate = str(launch_data.custom_params.get(key, "") or "")
            if candidate and not candidate.startswith("$"):
                course_id = candidate
                break

    token, _jti, _exp = jwt_service.create_access_token(
        user_id=str(user.id),
        department_id=department_id,
        email=user.email,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        additional_claims={
            "course_id": course_id,
            "lti_launch": True,
            "lti_roles": _simplify_roles(launch_data.roles),
        },
        expires_in_minutes=settings.lti_access_token_expire_minutes,
    )

    # --- Store one-time code ---
    code = secrets.token_urlsafe(32)
    course_name = launch_data.course_name or ""
    payload = json.dumps(
        {
            "access_token": token,
            "course_id": course_id,
            "course_name": course_name,
            "platform": platform,
        }
    )
    _store_code(f"lti_code:{code}", payload, ttl=120)

    # --- Build redirect URL ---
    dashboard_url = (
        getattr(settings, "dashboard_url", None) or "https://dashboard.example.com"
    )

    # Route based on placement:
    # - account_navigation → admin overview (regardless of course context)
    # - course_navigation (or any other) → course-specific view, unless we
    #   still don't have a course id — a bare "/lti/course/" (empty param)
    #   doesn't match the client route and used to strand the user on the
    #   dashboard home. Send those to the overview instead.
    if launch_data.placement == "account_navigation" or not course_id:
        redirect_url = f"{dashboard_url}/lti/overview?code={code}"
    else:
        redirect_url = f"{dashboard_url}/lti/course/{course_id}?code={code}"

    logger.info(
        "LTI launch handled",
        extra={
            "user_id": user.id,
            "course_id": course_id,
            "department_id": department_id,
        },
    )
    return redirect_url


# ---------------------------------------------------------------------------
# One-time code exchange
# ---------------------------------------------------------------------------


def exchange_code(code: str) -> Optional[Dict]:
    """
    Exchange a one-time code for an access token.

    The code is deleted after successful retrieval (one-time use).

    Args:
        code: The code value received from the redirect URL.

    Returns:
        ``{"access_token": "...", "course_id": "..."}`` or ``None``
        if the code is invalid or expired.
    """
    key = f"lti_code:{code}"
    raw = _pop_code(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupt LTI code payload", extra={"key": key})
        return None


# ---------------------------------------------------------------------------
# Auth bridge (dashboard → LTI context)
# ---------------------------------------------------------------------------


def create_bridge_code(user_id: str, department_id: str) -> Tuple[str, str]:
    """
    Create a one-time bridge code for an already-authenticated user.

    This lets the dashboard open a new tab/iframe pre-authenticated
    in the LTI context without re-launching from the LMS.

    Returns:
        ``(code, url)``
    """
    settings = get_settings()
    code = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": user_id, "department_id": department_id})
    _store_code(f"lti_bridge:{code}", payload, ttl=30)

    dashboard_url = (
        getattr(settings, "dashboard_url", None) or "https://dashboard.example.com"
    )
    url = f"{dashboard_url}/lti/bridge?code={code}"
    return code, url


# ---------------------------------------------------------------------------
# AGS context persistence
# ---------------------------------------------------------------------------


def store_ags_context(
    launch_data: CanvasLaunchData,
    registration: LTIRegistration,
    db: Session,
    ags_claim: Optional[Dict] = None,
) -> None:
    """
    Persist AGS endpoints so background jobs can push grades later.

    Uses INSERT ... ON CONFLICT DO UPDATE (upsert) keyed on
    ``(department_id, course_id)``.

    Args:
        launch_data: Validated LTI launch claims.
        registration: The matched LTI registration row.
        db: Active database session.
        ags_claim: The raw AGS endpoint claim dict from the LTI launch
                   (``https://purl.imsglobal.org/spec/lti-ags/claim/endpoint``).
    """
    if not ags_claim:
        return

    department_id = str(registration.department_id)
    course_id = launch_data.course_id or ""

    lineitem_url = ags_claim.get("lineitems") or ags_claim.get("lineitem")
    token_endpoint = registration.auth_token_url or ""
    client_id = registration.client_id or ""
    scopes = ags_claim.get("scope", [])

    if not token_endpoint:
        logger.warning(
            "Cannot store AGS context: missing token_endpoint on registration",
            extra={"registration_id": registration.id},
        )
        return

    stmt = pg_insert(LTIAGSContext).values(
        department_id=department_id,
        course_id=course_id,
        lineitem_url=lineitem_url,
        token_endpoint=token_endpoint,
        client_id=client_id,
        scopes=scopes,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ags_dept_course",
        set_={
            "lineitem_url": stmt.excluded.lineitem_url,
            "token_endpoint": stmt.excluded.token_endpoint,
            "client_id": stmt.excluded.client_id,
            "scopes": stmt.excluded.scopes,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    db.execute(stmt)
    db.flush()

    logger.info(
        "Stored AGS context",
        extra={
            "department_id": department_id,
            "course_id": course_id,
        },
    )


# ---------------------------------------------------------------------------
# Redis / in-memory helpers
# ---------------------------------------------------------------------------


def _store_code(key: str, value: str, ttl: int = 30) -> None:
    """Store a short-lived value in Redis (or in-memory fallback)."""
    redis = get_redis_client()
    if redis is not None:
        try:
            redis.setex(key, ttl, value)
            return
        except Exception:
            logger.warning(
                "Redis setex failed, falling back to in-memory", exc_info=True
            )
    _code_store[key] = value


def _pop_code(key: str) -> Optional[str]:
    """Retrieve and delete a one-time code. Returns None if missing/expired."""
    redis = get_redis_client()
    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.get(key)
            pipe.delete(key)
            result = pipe.execute()
            raw = result[0]
            if raw is not None:
                return raw.decode() if isinstance(raw, bytes) else raw
            # Fall through — might be in memory if Redis write failed earlier
        except Exception:
            logger.warning("Redis pop failed, trying in-memory", exc_info=True)
    return _code_store.pop(key, None)
