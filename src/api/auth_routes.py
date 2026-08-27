"""
Authentication API Endpoints

Provides endpoints for:
- API key generation
- API key management (list, revoke)
- Department account creation
- Quota status tracking
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Path
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import List, Literal, Optional, Tuple
from datetime import datetime, timedelta, timezone
import asyncio
from dataclasses import dataclass
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

from fastapi.responses import JSONResponse

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    User,
    Department,
    UserRole,
    AuditLogAction,
    AuditLogStatus,
    InvitationPurpose,
    InvitationStatus,
    UserInvitation,
)
from ..auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_key_management_principal,
    get_required_api_key,
    resolve_access_token,
)
from ..auth.auth_service import APIKeyQuotaError, AuthService, RateLimiter
from ..auth.redis_rate_limiter import RateLimitStorageUnavailable
from ..auth.session_service import get_session_service
from ..auth.jwt_service import get_jwt_service
from ..auth.lti_authorization import validate_lti_staff_token_payload
from ..middleware.quota import get_quota_status
from ..config.settings import get_settings
from ..mailer.email_service import get_email_service
from ..security.abuse_detector import check_signup_abuse, log_signup
from ..security.disposable_domains import is_disposable_domain
from ..security.audit_service import AuditPersistenceError, get_audit_service
from ..security.client_ip import get_client_ip
from ..services.account_deletion_service import AccountDeletionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])
# auto_error=False so we can return 401 instead of 403 for missing auth
security = HTTPBearer(auto_error=False)


# ==================== Request/Response Models ====================


class CreateAPIKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    rate_limit_per_hour: int = Field(default=100, ge=1, le=10000)
    expires_days: Optional[int] = Field(default=None, ge=1, le=3650)


class APIKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    rate_limit_per_hour: int
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    is_active: bool


class CreateAPIKeyResponse(BaseModel):
    api_key: APIKeyResponse
    full_key: str
    warning: str = "Store this key safely! It will only be shown once."


class CreateDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    institution: str = Field(min_length=1, max_length=255)
    contact_email: EmailStr
    contact_name: str = Field(min_length=1, max_length=255)
    first_admin_email: Optional[EmailStr] = None
    # Default from DEFAULT_DEPARTMENT_TIER: "department" (unlimited) for
    # self-hosted installs; a hosted service sets it to a limited tier.
    tier: str = Field(
        default=os.getenv("DEFAULT_DEPARTMENT_TIER", "department"),
        min_length=1,
        max_length=50,
    )


class DepartmentResponse(BaseModel):
    id: str
    name: str
    institution: str
    contact_email: str
    tier: str
    max_users: int
    created_at: datetime


class QuotaStatusResponse(BaseModel):
    """Response model for quota status."""

    tier: str
    unlimited: bool
    scans: dict
    pages: dict
    resets_at: Optional[str]
    features: List[str]
    excluded: List[str]


# ==================== Helper Functions ====================


@dataclass(frozen=True)
class SessionAccessIdentity:
    """Non-persistent API-key-shaped identity for a validated normal session."""

    id: str
    user_id: str
    department_id: str
    rate_limit_per_hour: int = 10000
    auth_method: str = "session"

    @classmethod
    def from_validated_session(
        cls, *, user_id: str, department_id: str, payload: dict
    ) -> "SessionAccessIdentity":
        """Derive a stable, DB-column-bounded id from trusted session context."""
        session_reference = payload.get("jti")
        if not isinstance(session_reference, str) or not session_reference:
            session_reference = user_id
        digest = hashlib.sha256(
            f"{session_reference}:{user_id}:{department_id}".encode("utf-8")
        ).hexdigest()[:28]
        return cls(
            id=f"session_{digest}",
            user_id=str(user_id),
            department_id=str(department_id),
        )


def get_current_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> APIKey | SessionAccessIdentity:
    """
    Dependency to get and validate current API key.

    Supports two authentication methods:
    1. Bearer token: Authorization: Bearer <api_key> (for CLI/programmatic access)
    2. Session cookie: aelira_access cookie (for dashboard users after magic link login)

    For normal session-based auth, returns a non-persistent identity derived only
    from the validated user and session payload. It never selects a database API
    key, so cookie authentication cannot inherit stale API-key tenant scope.

    Usage:
        @router.get("/protected")
        def protected_route(api_key: APIKey = Depends(get_current_api_key)):
            return {"user_id": api_key.user_id}
    """
    # Method 1: Check for Bearer token (API key)
    if credentials is not None:
        api_key_str = credentials.credentials
        api_key = AuthService.validate_api_key(db, api_key_str)

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check rate limit
        allowed, rate_headers = RateLimiter.check_rate_limit(
            api_key.id, api_key.rate_limit_per_hour
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Limit: {api_key.rate_limit_per_hour} requests/hour",
                headers=rate_headers,
            )

        return api_key

    # Method 2: Check for session cookie (dashboard users)
    access_token = request.cookies.get("aelira_access")
    if access_token:
        session_service = get_session_service()
        result = session_service.validate_session(db, access_token)
        is_normal_session = result is not None

        if result is None:
            # LTI-launch tokens arrive as this same cookie (lti_routes sets
            # aelira_access on launch) and legitimately have NO UserSession
            # row. Admit them by the SAME positive lti_launch claim the
            # Bearer path in get_required_api_key enforces, never by
            # "has no session", then resolve the user's API key exactly
            # like the session branch below.
            lti_payload = get_jwt_service().verify_access_token(access_token)
            if lti_payload:
                lti_user = validate_lti_staff_token_payload(lti_payload, db)
                if lti_user is not None:
                    result = (lti_user, lti_payload)

        if result:
            user, payload = result

            if is_normal_session:
                return SessionAccessIdentity.from_validated_session(
                    user_id=str(user.id),
                    department_id=str(user.department_id),
                    payload=payload,
                )

            # LTI cookie compatibility still requires an explicit API key in
            # the validated user's current department. A user without a usable
            # current department cannot safely resolve a tenant-bound key.
            current_user_id = str(user.id)
            current_department_id = getattr(user, "department_id", None)
            if not isinstance(current_department_id, str) or not current_department_id:
                logger.info(
                    "LTI cookie user %s has no current department", current_user_id
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "No active API key. Create one in Settings for "
                        "programmatic access."
                    ),
                )

            api_key = (
                db.query(APIKey)
                .filter(
                    APIKey.user_id == current_user_id,
                    APIKey.department_id == current_department_id,
                    APIKey.is_active == True,  # noqa: E712 - SQLAlchemy comparison
                )
                .order_by(APIKey.created_at)
                .first()
            )

            if (
                api_key is not None
                and getattr(api_key, "user_id", None) == current_user_id
                and getattr(api_key, "department_id", None) == current_department_id
            ):
                return api_key

            logger.info("LTI cookie user %s has no active API key", current_user_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No active API key. Create one in Settings for "
                    "programmatic access."
                ),
            )

    # No valid authentication found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide API key in Authorization header or login via dashboard.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ==================== API Key Management Endpoints ====================


@router.post("/keys", response_model=CreateAPIKeyResponse)
def create_api_key(
    request: CreateAPIKeyRequest,
    principal: AuthenticatedPrincipal = Depends(get_key_management_principal),
    db: Session = Depends(get_db_dependency),
):
    """
    Create a new API key

    Requires an authenticated dashboard session or API key. New keys inherit
    the trusted user_id and department_id from that principal.

    Returns the full API key - **store it safely, it will only be shown once!**
    """
    user_id = principal.user_id
    department_id = principal.department_id

    require_distributed = get_settings().env in {"production", "staging"}
    try:
        allowed, rate_headers = RateLimiter.check_rate_limit(
            f"api-key-create:{user_id}",
            5,
            require_distributed=require_distributed,
        )
    except RateLimitStorageUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key creation rate limit is unavailable",
        ) from None
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API key creation rate limit exceeded (5/hour)",
            headers=rate_headers,
        )

    try:
        api_key, full_key = AuthService.create_api_key(
            db=db,
            user_id=user_id,
            department_id=department_id,
            name=request.name,
            rate_limit_per_hour=request.rate_limit_per_hour,
            expires_days=request.expires_days,
            commit=False,
        )
        audit = get_audit_service(db)
        audit.log_api_key_create(
            user_id=user_id,
            department_id=department_id,
            api_key_id=api_key.id,
            key_name=request.name,
            commit=False,
        )

        response = {
            "api_key": APIKeyResponse(
                id=api_key.id,
                name=api_key.name,
                key_prefix=api_key.key_prefix,
                rate_limit_per_hour=api_key.rate_limit_per_hour,
                created_at=api_key.created_at,
                last_used_at=api_key.last_used_at,
                expires_at=api_key.expires_at,
                is_active=api_key.is_active,
            ),
            "full_key": full_key,
            "warning": "Store this key safely! It will only be shown once.",
        }
        db.commit()
    except APIKeyQuotaError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.error("API key creation transaction failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key creation failed",
        )

    return response


@router.get("/keys", response_model=List[APIKeyResponse])
def list_api_keys(
    principal: AuthenticatedPrincipal = Depends(get_key_management_principal),
    db: Session = Depends(get_db_dependency),
):
    """
    List all API keys for current user

    Requires an authenticated dashboard session or API key. Returns keys
    belonging to the same user in the principal's current department.
    """
    keys = AuthService.list_api_keys(
        db,
        principal.user_id,
        principal.department_id,
    )

    return [
        APIKeyResponse(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            rate_limit_per_hour=key.rate_limit_per_hour,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            expires_at=key.expires_at,
            is_active=key.is_active,
        )
        for key in keys
    ]


@router.delete("/keys/{key_id}")
def revoke_api_key(
    key_id: str = Path(min_length=1, max_length=128),
    principal: AuthenticatedPrincipal = Depends(get_key_management_principal),
    db: Session = Depends(get_db_dependency),
):
    """
    Revoke (deactivate) an API key

    Requires an authenticated dashboard session or API key. Can only revoke
    keys belonging to the same user in the principal's current department.
    """
    try:
        success = AuthService.revoke_api_key(
            db,
            key_id,
            principal.user_id,
            principal.department_id,
            commit=False,
        )

        if not success:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API key not found or unauthorized",
            )

        audit = get_audit_service(db)
        audit.log_api_key_revoke(
            user_id=principal.user_id,
            department_id=principal.department_id,
            api_key_id=key_id,
            commit=False,
        )
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("API key revocation transaction failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key revocation failed",
        )

    revoked_current_key = bool(
        principal.api_key is not None and str(principal.api_key.id) == key_id
    )
    return {
        "success": True,
        "message": "API key revoked",
        "revoked_current_key": revoked_current_key,
    }


@router.get("/validate")
def validate_api_key_dashboard(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Validate API key or JWT and return department info (for dashboard login)

    This endpoint is used by the dashboard to authenticate and get department details.
    Supports both DB API keys and LTI JWT Bearer tokens.
    """
    _api_key, user_id, department_id = api_key_info

    # Get department details
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found"
        )

    # Get user details
    user = db.query(User).filter(User.id == user_id).first()

    return {
        "valid": True,
        "department": {
            "id": department.id,
            "name": department.name,
            "institution": department.institution,
            "tier": department.tier,
            "max_users": department.max_users,
            "subscription_status": department.subscription_status,
        },
        "user": (
            {
                "id": user.id if user else None,
                "email": user.email if user else None,
                "name": user.name if user else None,
                "role": user.role.value if user and user.role else None,
            }
            if user
            else None
        ),
        "api_key": (
            {
                "id": _api_key.id,
                "name": _api_key.name,
                "rate_limit_per_hour": _api_key.rate_limit_per_hour,
            }
            if _api_key
            else None
        ),
    }


@router.get("/keys/validate")
def validate_api_key(
    api_key: APIKey | SessionAccessIdentity = Depends(get_current_api_key),
):
    """
    Validate current API key (for testing authentication)

    This endpoint requires authentication and returns the API key details.
    """
    if isinstance(api_key, SessionAccessIdentity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A real API key is required for this endpoint",
        )

    return {
        "valid": True,
        "api_key": {
            "id": api_key.id,
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "user_id": api_key.user_id,
            "department_id": api_key.department_id,
            "rate_limit_per_hour": api_key.rate_limit_per_hour,
        },
    }


# ==================== Department Management Endpoints ====================

ProvisioningOutcome = Literal["created", "reused", "rejected"]
ProvisioningReason = Literal[
    "missing_credentials",
    "credentials_rejected",
    "lti_not_allowed",
    "role_not_allowed",
    "rate_limited",
    "abuse_blocked",
    "abuse_challenge_required",
    "duplicate_department",
    "admin_email_unavailable",
    "handoff_revoked",
    "email_unavailable",
]
ProvisioningActorClass = Literal[
    "authenticated", "anonymous_public", "anonymous_closed", "unresolved_credentials"
]


def _enforce_department_creation_principal(
    principal: AuthenticatedPrincipal,
) -> AuthenticatedPrincipal:
    """Allow only normal account administrators to provision departments."""
    if principal.auth_method == "lti":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="LTI launch sessions cannot create departments",
        )
    if principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return principal


def _audit_department_provisioning(
    *,
    db: Session,
    request: Request,
    provisioner: AuthenticatedPrincipal | None,
    outcome: ProvisioningOutcome,
    reason: ProvisioningReason | None = None,
    target_department_id: str | None = None,
    actor_class: ProvisioningActorClass | None = None,
    auth_method: str | None = None,
    commit: bool = True,
):
    """Persist a bounded cross-tenant provisioning event."""
    try:
        return get_audit_service(db).log_action(
            action=AuditLogAction.DEPARTMENT_PROVISION,
            status=(
                AuditLogStatus.SUCCESS
                if outcome != "rejected"
                else AuditLogStatus.FAILURE
            ),
            user_id=provisioner.user_id if provisioner else None,
            department_id=provisioner.department_id if provisioner else None,
            resource_type="department",
            resource_id=target_department_id,
            ip_address=get_client_ip(request),
            details={
                "actor_class": actor_class
                or ("authenticated" if provisioner else "anonymous_public"),
                "auth_method": auth_method
                or (provisioner.auth_method if provisioner else "public"),
                "outcome": outcome,
                **({"reason": reason} if reason else {}),
            },
            commit=commit,
            required=commit,
        )
    except AuditPersistenceError:
        logger.error("Required department provisioning audit is unavailable")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Department provisioning audit is unavailable",
        ) from None


def _normalized_email(value: str) -> str:
    return value.strip().lower()


def _canonical_text(value: str) -> str:
    return value.strip().lower()


def _handoff_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lock_department_identity(db: Session, *, name: str, institution: str) -> None:
    """Serialize canonical duplicate checks on PostgreSQL."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    identity = f"{_canonical_text(institution)}\x00{_canonical_text(name)}"
    lock_key = int.from_bytes(
        hashlib.sha256(identity.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _lock_admin_handoff_email(db: Session, *, email: str) -> None:
    """Serialize globally unique first-administrator email claims on PostgreSQL."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    lock_key = int.from_bytes(
        hashlib.sha256(f"admin-handoff\x00{email}".encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _handoff_delivery_cooldown_active(invitation: UserInvitation) -> bool:
    queued_at = invitation.delivery_queued_at
    if queued_at is None:
        return False
    now = datetime.now(timezone.utc)
    if queued_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return queued_at > now - timedelta(minutes=15)


def _audit_admin_handoff(
    *,
    db: Session,
    request: Request,
    provisioner: AuthenticatedPrincipal | None,
    target_department_id: str,
    invitation_id: str,
    outcome: Literal["issued", "reissued"],
) -> None:
    """Stage a bounded tenant-targeted first-admin handoff event."""
    get_audit_service(db).log_action(
        action=AuditLogAction.DEPARTMENT_ADMIN_HANDOFF,
        status=AuditLogStatus.SUCCESS,
        user_id=provisioner.user_id if provisioner else None,
        department_id=target_department_id,
        resource_type="invitation",
        resource_id=invitation_id,
        ip_address=get_client_ip(request),
        details={
            "actor_class": "authenticated" if provisioner else "anonymous_public",
            "auth_method": provisioner.auth_method if provisioner else "public",
            "outcome": outcome,
            "role": UserRole.ADMIN.value,
        },
        commit=False,
    )


def _department_response(department: Department) -> DepartmentResponse:
    return DepartmentResponse(
        id=department.id,
        name=department.name,
        institution=department.institution,
        contact_email=department.contact_email,
        tier=department.tier,
        max_users=department.max_users,
        created_at=department.created_at,
    )


async def _send_admin_handoff_email(
    *,
    department_id: str,
    department_name: str,
    institution: str,
    recipient_email: str,
    raw_token: str,
    expires_at: datetime,
) -> None:
    settings = get_settings()
    email_service = get_email_service()
    dashboard_url = settings.public_dashboard_url
    accept_url = f"{dashboard_url.rstrip('/')}/accept-invitation#token={raw_token}"
    try:
        result = await email_service.send_admin_handoff_invitation(
            to_email=recipient_email,
            department_name=department_name,
            institution=institution,
            accept_url=accept_url,
            expires_date=expires_at.strftime("%B %d, %Y at %I:%M %p UTC"),
        )
        if not result.get("success"):
            logger.warning(
                "Administrator handoff email was not accepted for department %s",
                department_id,
            )
    except Exception as exc:
        logger.warning(
            "Administrator handoff email delivery failed for department %s: %s",
            department_id,
            type(exc).__name__,
        )


def _queue_admin_handoff_email(
    *,
    department: Department,
    recipient_email: str,
    raw_token: str,
    expires_at: datetime,
) -> None:
    department_id = department.id
    department_name = department.name
    institution = department.institution
    asyncio.create_task(
        _send_admin_handoff_email(
            department_id=department_id,
            department_name=department_name,
            institution=institution,
            recipient_email=recipient_email,
            raw_token=raw_token,
            expires_at=expires_at,
        )
    )


def authorize_department_creation(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> AuthenticatedPrincipal | None:
    """Resolve the department-provisioning policy before handler mutation."""
    settings = get_settings()
    has_session_cookie = bool(request.cookies.get("aelira_access"))
    has_authorization_header = bool(request.headers.get("Authorization"))
    if (
        settings.allow_public_department_creation
        and credentials is None
        and not has_session_cookie
        and not has_authorization_header
    ):
        return None

    principal: AuthenticatedPrincipal | None = None
    try:
        principal = get_authenticated_principal(request, credentials, db)
        return _enforce_department_creation_principal(principal)
    except HTTPException as exc:
        principal = principal or getattr(request.state, "authenticated_principal", None)
        unresolved_method = (
            "bearer"
            if has_authorization_header
            else "session" if has_session_cookie else "none"
        )
        reason: ProvisioningReason
        if principal is not None:
            if (
                principal.auth_method == "api_key"
                and exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            ):
                reason = "rate_limited"
            else:
                reason = (
                    "lti_not_allowed"
                    if principal.auth_method == "lti"
                    else "role_not_allowed"
                )
        else:
            reason = (
                "credentials_rejected"
                if unresolved_method != "none"
                else "missing_credentials"
            )
        _audit_department_provisioning(
            db=db,
            request=request,
            provisioner=principal,
            outcome="rejected",
            reason=reason,
            actor_class=(
                "authenticated"
                if principal
                else (
                    "unresolved_credentials"
                    if unresolved_method != "none"
                    else "anonymous_closed"
                )
            ),
            auth_method=principal.auth_method if principal else unresolved_method,
        )
        raise


@router.post("/departments", response_model=DepartmentResponse)
async def create_department(
    request: CreateDepartmentRequest,
    http_request: Request,
    _provisioner: AuthenticatedPrincipal | None = Depends(
        authorize_department_creation
    ),
    db: Session = Depends(get_db_dependency),
):
    """
    Create a new department account

    By default, this endpoint requires an administrator from a normal session
    or API key. Operators can explicitly restore anonymous provisioning with
    ALLOW_PUBLIC_DEPARTMENT_CREATION=true.

    Security:
    - Abuse detection (IP tracking, domain limits, bot detection)
    - Rate limiting on signup attempts
    """
    contact_email = _normalized_email(str(request.contact_email))
    first_admin_email = _normalized_email(
        str(request.first_admin_email or request.contact_email)
    )

    # Get client info for abuse detection
    client_ip = get_client_ip(http_request)
    user_agent = http_request.headers.get("user-agent")
    fingerprint = http_request.headers.get("x-device-fingerprint")

    # Check for abuse before proceeding
    abuse_result = await check_signup_abuse(
        db=db,
        email=first_admin_email,
        ip_address=client_ip,
        user_agent=user_agent,
        fingerprint=fingerprint,
    )

    if not abuse_result.allowed:
        log_signup(
            db=db,
            email=first_admin_email,
            ip_address=client_ip,
            user_agent=user_agent,
            fingerprint=fingerprint,
            success=False,
        )

        outcome = (
            "abuse_blocked"
            if abuse_result.recommended_action == "block"
            else "abuse_challenge_required"
        )
        _audit_department_provisioning(
            db=db,
            request=http_request,
            provisioner=_provisioner,
            outcome="rejected",
            reason=outcome,
        )

        if abuse_result.recommended_action == "block":
            logger.warning(
                "Department creation blocked by abuse detector: action=%s",
                abuse_result.recommended_action,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many signup attempts. Please try again later.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Additional verification is required before provisioning",
        )

    email_service = get_email_service()
    if not email_service.is_configured():
        _audit_department_provisioning(
            db=db,
            request=http_request,
            provisioner=_provisioner,
            outcome="rejected",
            reason="email_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery must be configured before provisioning",
        )

    raw_handoff_token: str | None = None
    try:
        _lock_department_identity(
            db, name=request.name, institution=request.institution
        )
        _lock_admin_handoff_email(db, email=first_admin_email)
        email_blocked, _ = AccountDeletionService.is_email_blocked(
            db,
            first_admin_email,
            commit_expired_cleanup=False,
        )
        if email_blocked:
            db.rollback()
            _audit_department_provisioning(
                db=db,
                request=http_request,
                provisioner=_provisioner,
                outcome="rejected",
                reason="admin_email_unavailable",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The administrator email is unavailable",
            )
        existing = (
            db.query(Department)
            .filter(
                func.lower(func.trim(Department.name)) == _canonical_text(request.name),
                func.lower(func.trim(Department.institution))
                == _canonical_text(request.institution),
            )
            .with_for_update()
            .first()
        )

        if existing is not None:
            handoff = (
                db.query(UserInvitation)
                .filter(
                    UserInvitation.department_id == existing.id,
                    UserInvitation.purpose
                    == InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
                )
                .with_for_update()
                .first()
            )
            exact_retry = (
                _canonical_text(existing.name) == _canonical_text(request.name)
                and _canonical_text(existing.institution)
                == _canonical_text(request.institution)
                and _normalized_email(existing.contact_email) == contact_email
                and _canonical_text(existing.contact_name or "")
                == _canonical_text(request.contact_name)
                and str(existing.tier) == request.tier
                and handoff is not None
                and _normalized_email(handoff.email) == first_admin_email
                and _provisioner is not None
                and bool(_provisioner.user_id)
                and handoff.invited_by == _provisioner.user_id
            )
            if not exact_retry:
                db.rollback()
                _audit_department_provisioning(
                    db=db,
                    request=http_request,
                    provisioner=_provisioner,
                    outcome="rejected",
                    reason="duplicate_department",
                    target_department_id=existing.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A department with this identity already exists",
                )

            assert handoff is not None
            if handoff.status == InvitationStatus.EXPIRED or (
                handoff.status == InvitationStatus.PENDING
                and not _handoff_delivery_cooldown_active(handoff)
            ):
                raw_handoff_token = secrets.token_urlsafe(48)
                handoff.token = _handoff_token_digest(raw_handoff_token)
                handoff.status = InvitationStatus.PENDING
                handoff.expires_at = datetime.utcnow() + timedelta(days=7)
                handoff.revoked_at = None
                handoff.delivery_queued_at = datetime.now(timezone.utc)
                _audit_admin_handoff(
                    db=db,
                    request=http_request,
                    provisioner=_provisioner,
                    target_department_id=existing.id,
                    invitation_id=handoff.id,
                    outcome="reissued",
                )
            elif handoff.status == InvitationStatus.REVOKED:
                db.rollback()
                _audit_department_provisioning(
                    db=db,
                    request=http_request,
                    provisioner=_provisioner,
                    outcome="rejected",
                    reason="handoff_revoked",
                    target_department_id=existing.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The administrator handoff has been revoked",
                )

            _audit_department_provisioning(
                db=db,
                request=http_request,
                provisioner=_provisioner,
                outcome="reused",
                target_department_id=existing.id,
                commit=False,
            )
            db.commit()
            response = _department_response(existing)
            if raw_handoff_token is not None:
                _queue_admin_handoff_email(
                    department=existing,
                    recipient_email=first_admin_email,
                    raw_token=raw_handoff_token,
                    expires_at=handoff.expires_at,
                )
            logger.info("Reused department provisioning %s", existing.id)
            return response

        existing_admin_email = (
            db.query(User).filter(func.lower(User.email) == first_admin_email).first()
        )
        existing_pending_invitation = (
            db.query(UserInvitation)
            .filter(
                func.lower(UserInvitation.email) == first_admin_email,
                UserInvitation.status == InvitationStatus.PENDING,
            )
            .first()
        )
        if existing_admin_email is not None or existing_pending_invitation is not None:
            db.rollback()
            _audit_department_provisioning(
                db=db,
                request=http_request,
                provisioner=_provisioner,
                outcome="rejected",
                reason="admin_email_unavailable",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The administrator email is unavailable",
            )

        department = Department(
            name=request.name,
            institution=request.institution,
            contact_email=contact_email,
            contact_name=request.contact_name,
            tier=request.tier,
            max_users=5 if request.tier == "trial" else 50,
            trial_ends_at=datetime.utcnow() + timedelta(days=30),
        )
        db.add(department)
        db.flush()
        db.refresh(department)
        raw_handoff_token = secrets.token_urlsafe(48)
        handoff = UserInvitation(
            department_id=department.id,
            email=first_admin_email,
            role=UserRole.ADMIN,
            token=_handoff_token_digest(raw_handoff_token),
            purpose=InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
            invited_by=_provisioner.user_id if _provisioner else None,
            status=InvitationStatus.PENDING,
            delivery_queued_at=datetime.now(timezone.utc),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(handoff)
        db.flush()
        response = _department_response(department)
        _audit_department_provisioning(
            db=db,
            request=http_request,
            provisioner=_provisioner,
            outcome="created",
            target_department_id=department.id,
            commit=False,
        )
        _audit_admin_handoff(
            db=db,
            request=http_request,
            provisioner=_provisioner,
            target_department_id=department.id,
            invitation_id=handoff.id,
            outcome="issued",
        )
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.error("Department provisioning persistence failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Department could not be created",
        )

    logger.info("Created department %s", department.id)

    try:
        _queue_admin_handoff_email(
            department=department,
            recipient_email=first_admin_email,
            raw_token=raw_handoff_token,
            expires_at=handoff.expires_at,
        )
        logger.info("Queued administrator handoff for department %s", department.id)
    except Exception as exc:
        logger.warning("Failed to send administrator handoff: %s", type(exc).__name__)

    return response


@router.get("/departments/{department_id}", response_model=DepartmentResponse)
def get_department(
    department_id: str,
    current_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get department details

    Requires a valid API key. Can only view details of the authenticated user's department.
    """
    # Verify user belongs to this department
    if current_key.department_id != department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this department",
        )

    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found"
        )

    return DepartmentResponse(
        id=department.id,
        name=department.name,
        institution=department.institution,
        contact_email=department.contact_email,
        tier=department.tier,
        max_users=department.max_users,
        created_at=department.created_at,
    )


# ==================== Individual Faculty Signup ====================


def extract_institution_from_email(email: str) -> str:
    """
    Extract institution name from email domain.

    Examples:
        faculty@stanford.edu -> Stanford University
        prof@cs.mit.edu -> MIT
        teacher@oxford.ac.uk -> Oxford
    """
    domain = email.split("@")[1].lower()

    # Remove common subdomains
    parts = domain.split(".")
    if len(parts) > 2:
        # Remove subdomains like 'cs', 'eng', 'mail', etc.
        main_domain = parts[-3] if parts[-1] in ["edu", "uk", "au", "jp"] else parts[-2]
    else:
        main_domain = parts[0]

    # Capitalize
    return main_domain.replace("-", " ").title() + " University"


@router.get("/quota")
def get_quota(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get current quota usage and limits for the authenticated user's department.

    Returns:
    - Current tier
    - Scans used/remaining this month
    - Pages used/remaining this month
    - Date when quotas reset
    - Available features for this tier
    - Features excluded from this tier (upgrade required)
    """
    quota_info = get_quota_status(db, api_key.department_id)

    if "error" in quota_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=quota_info["error"],
        )

    return quota_info


# ==================== Magic Link Authentication ====================


class MagicLinkRequestModel(BaseModel):
    """Request model for magic link login.

    This is a login endpoint, not a signup filter: accounts are provisioned
    by admin invitation, LMS launch, or domain-matched SSO, so no domain
    restriction is applied here. Disposable-address and abuse checks apply
    only when a new account would actually be created (open-signup mode or
    first-run bootstrap).
    """

    email: EmailStr
    # Name and institution are used when a new account is created
    # (open-signup mode or first-run bootstrap), optional otherwise
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    institution: Optional[str] = Field(None, min_length=2, max_length=200)
    next: Optional[str] = Field(None, max_length=2048)


class MagicLinkVerifyRequest(BaseModel):
    """Request model for verifying magic link"""

    email: str
    token: str


class SessionResponse(BaseModel):
    """Response model for session info"""

    user: dict
    department: dict
    expires_at: str


@router.post("/magic-link/request")
async def request_magic_link(
    request_body: MagicLinkRequestModel,
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Request a magic link for email-based login.

    Sends a login link to the provided email address.
    The link is valid for 15 minutes and can only be used once.

    Security:
    - Rate limited to 5 requests per email per hour
    - Rate limited to 10 requests per IP per hour
    """
    session_service = get_session_service()
    settings = get_settings()

    # Get client info
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    email = request_body.email.lower()

    # Rate limit by email (5/hour)
    email_limit_key = f"magic_link_email:{email}"
    email_allowed, _ = RateLimiter.check_rate_limit(email_limit_key, 5)
    if not email_allowed:
        logger.warning("Magic link rate limit exceeded: dimension=email")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many magic link requests. Please try again later.",
        )

    # Rate limit by IP (10/hour)
    ip_limit_key = f"magic_link_ip:{client_ip}"
    ip_allowed, _ = RateLimiter.check_rate_limit(ip_limit_key, 10)
    if not ip_allowed:
        logger.warning("Magic link rate limit exceeded: dimension=ip")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests from this location. Please try again later.",
        )

    # Check if email is blocked (deactivated/deleted account)
    # Return same response to prevent email enumeration
    from ..services.account_deletion_service import AccountDeletionService

    blocked, _ = AccountDeletionService.is_email_blocked(db, email)
    if blocked:
        logger.info("Magic link request blocked for deleted or deactivated account")
        return {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }

    # Deactivated accounts cannot request new login links.
    # Return the generic response to avoid email enumeration.
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user is not None and existing_user.is_active is False:
        logger.warning(
            "Magic link request blocked for deactivated user %s", existing_user.id
        )
        return {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }

    # Provisioning policy: closed by default. New accounts are created by
    # admin invitation, LMS (LTI) launch, or domain-matched SSO — not by
    # requesting a magic link. Two exceptions: OPEN_SIGNUP=true (an operator
    # deliberately running an open deployment) and the first-run bootstrap
    # (no users exist yet, so the first login must be able to create the
    # admin). Return the generic response so closed mode does not become an
    # account-enumeration oracle.
    if existing_user is None and not settings.open_signup:
        if db.query(User).count() > 0:
            logger.info("Magic link request ignored because signup is closed")
            return {
                "success": True,
                "message": "If an account exists with this email, you will receive a login link.",
            }

    # When a NEW account would be created, block disposable/temp-mail
    # addresses and run the abuse detector.
    if existing_user is None:
        if is_disposable_domain(email.split("@")[1]):
            logger.info("Magic link request ignored for disposable domain")
            return {
                "success": True,
                "message": "If an account exists with this email, you will receive a login link.",
            }
        abuse_result = await check_signup_abuse(
            db=db,
            email=email,
            ip_address=client_ip,
            user_agent=user_agent,
        )
        if not abuse_result.allowed:
            log_signup(
                db=db,
                email=email,
                ip_address=client_ip,
                user_agent=user_agent,
                success=False,
            )
            logger.warning(
                "Magic link signup blocked by abuse detector: action=%s",
                abuse_result.recommended_action,
            )
            if abuse_result.recommended_action == "challenge":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "verification_required",
                        "message": "Additional verification required to complete signup.",
                        "challenge_type": abuse_result.challenge_type,
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "signup_blocked",
                    "message": "We're unable to create an account with this email address.",
                },
            )

    # Create magic link (with signup profile data if provided)
    token = session_service.create_magic_link(
        db=db,
        email=email,
        ip_address=client_ip,
        user_agent=user_agent,
        signup_name=request_body.name,
        signup_institution=request_body.institution,
    )

    # Build a link with only a same-origin path continuation. Keep this policy
    # aligned with dashboard/src/utils/safeNext.ts.
    next_path = request_body.next
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or next_path.startswith("/\\")
    ):
        next_path = "/dashboard"
    query = urlencode({"email": email, "token": token, "next": next_path})
    magic_link_url = f"{settings.magic_link_base_url}/auth/verify?{query}"

    # Send email (non-blocking)
    try:
        email_service = get_email_service()
        if email_service.is_configured():
            import asyncio

            asyncio.create_task(
                email_service.send_magic_link(
                    to_email=email,
                    magic_link_url=magic_link_url,
                    expires_minutes=settings.magic_link_expire_minutes,
                )
            )
            logger.info("Magic link delivery queued")
    except Exception as e:
        logger.warning("Magic link delivery failed: %s", type(e).__name__)
        # Still return success to prevent email enumeration

    # Record new signups for IP-velocity / rapid-signup abuse tracking.
    if existing_user is None:
        log_signup(
            db=db,
            email=email,
            ip_address=client_ip,
            user_agent=user_agent,
            success=True,
        )

    # Always return success to prevent email enumeration
    return {
        "success": True,
        "message": "If an account exists with this email, you will receive a login link.",
    }


@router.get("/magic-link/check")
async def check_magic_link(
    email: str,
    token: str,
    db: Session = Depends(get_db_dependency),
):
    """
    Check if a magic link token is valid WITHOUT consuming it.

    This endpoint is safe to be called by email scanners, browser safety
    checks, or frontend validation - it does not consume the token.

    Returns:
        {"valid": true/false}
    """
    session_service = get_session_service()

    is_valid = session_service.check_magic_link(db, email.lower(), token)

    return {"valid": is_valid}


@router.post("/magic-link/verify")
async def verify_magic_link(
    body: MagicLinkVerifyRequest,
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Verify a magic link token and create a session.

    This is a POST endpoint to prevent email scanners and browser
    prefetch from accidentally consuming the token.

    On success, sets httpOnly cookies and returns session info.
    """
    email = body.email
    token = body.token
    session_service = get_session_service()
    settings = get_settings()

    # Get client info
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Verify the magic link
    magic_link = session_service.verify_magic_link(db, email.lower(), token)
    if not magic_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired magic link. Please request a new one.",
        )

    # Get or create user (use signup profile data stored on the magic link)
    try:
        user, is_new = session_service.get_or_create_user_for_magic_link(
            db,
            email.lower(),
            name=magic_link.signup_name,
            institution=magic_link.signup_institution,
        )
    except ValueError as e:
        # Blocked or deactivated account
        logger.warning("Magic link verification rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )

    # Create session
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    # Create response with cookies
    response = JSONResponse(
        content={
            "success": True,
            "message": "Login successful",
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value if user.role else "faculty",
            },
            "is_new_user": is_new,
            "redirect_url": f"{settings.magic_link_base_url}/dashboard",
        }
    )

    # Set httpOnly cookies
    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }

    # Only set domain in production (allows localhost in dev)
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

    logger.info(
        "Magic link login successful for user %s (new_user=%s)", user.id, is_new
    )
    return response


# ==================== Session Management ====================


def _session_cookie_settings(settings):
    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }
    if settings.env == "production" and settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain
    return cookie_settings


def _clear_session_cookies(response: JSONResponse, settings) -> JSONResponse:
    cookie_settings = _session_cookie_settings(settings)
    response.delete_cookie(key="aelira_access", **cookie_settings)
    response.delete_cookie(key="aelira_refresh", **cookie_settings)
    return response


@router.get("/session/validate")
async def validate_session(
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Validate the current session and return user info.

    Checks the access token from cookies or Authorization header.
    Used by the frontend to check if the user is logged in.
    """
    session_service = get_session_service()
    jwt_service = get_jwt_service()

    # Get access token from cookie or Bearer header
    access_token = request.cookies.get("aelira_access")
    if not access_token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    resolved = resolve_access_token(
        db,
        access_token,
        session_service=session_service,
        jwt_service=jwt_service,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )
    user = resolved.user
    payload = resolved.payload

    # Get department info
    department = (
        db.query(Department).filter(Department.id == user.department_id).first()
    )

    return {
        "valid": True,
        "auth_method": resolved.principal.auth_method,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role.value if user.role else "faculty",
            "email_verified": user.email_verified,
        },
        "department": {
            "id": department.id if department else None,
            "name": department.name if department else None,
            "tier": department.tier if department else None,
        },
        "expires_at": payload.get("exp"),
    }


@router.post("/session/refresh")
async def refresh_session(
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Refresh the session using the refresh token.

    Issues new access and refresh tokens (token rotation).
    """
    session_service = get_session_service()
    settings = get_settings()

    # Get client info
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Get refresh token from cookie
    refresh_token = request.cookies.get("aelira_refresh")
    if not refresh_token:
        return _clear_session_cookies(
            JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Refresh token required"},
            ),
            settings,
        )

    # Refresh session
    result = session_service.refresh_session(
        db=db,
        refresh_token=refresh_token,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    if not result:
        return _clear_session_cookies(
            JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": "Invalid or expired refresh token. Please log in again."
                },
            ),
            settings,
        )

    access_token, new_refresh_token, access_exp, refresh_exp = result

    # Create response with new cookies
    response = JSONResponse(content={"success": True, "message": "Session refreshed"})

    cookie_settings = _session_cookie_settings(settings)

    response.set_cookie(
        key="aelira_access",
        value=access_token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        **cookie_settings,
    )
    response.set_cookie(
        key="aelira_refresh",
        value=new_refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        **cookie_settings,
    )

    logger.debug("Session refreshed successfully")
    return response


@router.post("/session/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Logout the current session.

    Revokes the session and clears cookies.
    """
    session_service = get_session_service()
    settings = get_settings()

    access_token = request.cookies.get("aelira_access")
    refresh_token = request.cookies.get("aelira_refresh")
    jwt_service = get_jwt_service()
    access_payload = (
        jwt_service.decode_token(access_token, verify_exp=False)
        if access_token
        else None
    )
    refresh_payload = (
        jwt_service.decode_token(refresh_token, verify_exp=False)
        if refresh_token
        else None
    )
    if not isinstance(access_payload, dict) or access_payload.get("type") != "access":
        access_payload = None
    if (
        not isinstance(refresh_payload, dict)
        or refresh_payload.get("type") != "refresh"
    ):
        refresh_payload = None

    access_sid = access_payload.get("sid") if access_payload else None
    refresh_sid = refresh_payload.get("sid") if refresh_payload else None
    if isinstance(access_sid, str) and access_sid:
        payload = access_payload
    elif isinstance(refresh_sid, str) and refresh_sid:
        payload = refresh_payload
    else:
        payload = access_payload

    if payload is not None:
        user_id = payload.get("sub")
        session_id = payload.get("sid")
        if isinstance(user_id, str) and user_id:
            if isinstance(session_id, str) and session_id:
                session_service.revoke_session(db, user_id, session_id=session_id)
            elif access_token and payload is access_payload:
                session_service.revoke_session(db, user_id, access_token)

            audit = get_audit_service(db)
            audit.log_logout(user_id=user_id, request=request)

    response = JSONResponse(content={"success": True, "message": "Logged out"})
    _clear_session_cookies(response, settings)

    logger.info("User logged out")
    return response


# ==================== User Profile ====================


class UserProfileResponse(BaseModel):
    """Response model for user profile."""

    id: str
    email: str
    name: str
    picture_url: Optional[str]
    email_notifications: bool
    timezone: str
    created_at: datetime
    email_verified: bool
    auth_provider: str


class UpdateProfileRequest(BaseModel):
    """Request model for updating user profile."""

    name: Optional[str] = None
    email_notifications: Optional[bool] = None
    timezone: Optional[str] = None


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    request: Request,
    db: Session = Depends(get_db_dependency),
    auth: tuple = Depends(get_required_api_key),
):
    """
    Get the current user's profile.

    Supports both Bearer token (API key) and session cookie authentication.
    """
    _, user_id, _ = auth

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Use getattr() for columns that may not exist in DB yet (migration pending)
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name or "",
        picture_url=user.picture_url,
        email_notifications=getattr(user, "email_scan_complete", True) or True,
        timezone=getattr(user, "timezone", "UTC") or "UTC",
        created_at=user.created_at,
        email_verified=getattr(user, "email_verified", False) or False,
        auth_provider=user.auth_provider.value if user.auth_provider else "magic_link",
    )


@router.patch("/profile", response_model=UserProfileResponse)
async def update_profile(
    profile_update: UpdateProfileRequest,
    request: Request,
    db: Session = Depends(get_db_dependency),
    auth: tuple = Depends(get_required_api_key),
):
    """
    Update the current user's profile.

    Supports both Bearer token (API key) and session cookie authentication.
    Only the fields provided will be updated.
    """
    _, user_id, _ = auth

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields if provided
    if profile_update.name is not None:
        # Validate name (non-empty, reasonable length)
        name = profile_update.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name cannot be empty",
            )
        if len(name) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name too long (max 100 characters)",
            )
        user.name = name

    if profile_update.email_notifications is not None:
        # Map single toggle to granular field (migration-safe)
        try:
            user.email_scan_complete = profile_update.email_notifications
        except Exception:
            pass  # Column may not exist in DB yet

    if profile_update.timezone is not None:
        # Validate timezone (basic validation)
        tz = profile_update.timezone.strip()
        if len(tz) > 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid timezone",
            )
        user.timezone = tz

    db.commit()
    db.refresh(user)

    logger.info(f"Updated profile for user {user.id}")

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name or "",
        picture_url=user.picture_url,
        email_notifications=getattr(user, "email_scan_complete", True) or True,
        timezone=getattr(user, "timezone", "UTC") or "UTC",
        created_at=user.created_at,
        email_verified=getattr(user, "email_verified", False) or False,
        auth_provider=user.auth_provider.value if user.auth_provider else "magic_link",
    )


# ==================== Email Preferences ====================


class EmailPreferencesResponse(BaseModel):
    """Response model for email notification preferences."""

    email_scan_complete: bool
    email_remediation_complete: bool
    email_critical_alerts: bool
    email_weekly_summary: bool
    weekly_summary_day: int  # 0=Monday, 6=Sunday
    weekly_summary_hour: int  # 0-23 UTC


class EmailPreferencesUpdate(BaseModel):
    """Request model for updating email preferences."""

    email_scan_complete: Optional[bool] = None
    email_remediation_complete: Optional[bool] = None
    email_critical_alerts: Optional[bool] = None
    email_weekly_summary: Optional[bool] = None
    weekly_summary_day: Optional[int] = Field(None, ge=0, le=6)
    weekly_summary_hour: Optional[int] = Field(None, ge=0, le=23)


@router.get("/profile/email-preferences", response_model=EmailPreferencesResponse)
async def get_email_preferences(
    request: Request,
    db: Session = Depends(get_db_dependency),
    auth: tuple = Depends(get_required_api_key),
):
    """
    Get the current user's email notification preferences.

    Supports both Bearer token (API key) and session cookie authentication.
    Returns all email preference settings including notification types
    and weekly summary schedule.
    """
    _, user_id, _ = auth

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return EmailPreferencesResponse(
        email_scan_complete=(
            user.email_scan_complete if user.email_scan_complete is not None else True
        ),
        email_remediation_complete=(
            user.email_remediation_complete
            if user.email_remediation_complete is not None
            else True
        ),
        email_critical_alerts=(
            user.email_critical_alerts
            if user.email_critical_alerts is not None
            else True
        ),
        email_weekly_summary=(
            user.email_weekly_summary if user.email_weekly_summary is not None else True
        ),
        weekly_summary_day=(
            user.weekly_summary_day if user.weekly_summary_day is not None else 0
        ),
        weekly_summary_hour=(
            user.weekly_summary_hour if user.weekly_summary_hour is not None else 9
        ),
    )


@router.patch("/profile/email-preferences", response_model=EmailPreferencesResponse)
async def update_email_preferences(
    prefs_update: EmailPreferencesUpdate,
    request: Request,
    db: Session = Depends(get_db_dependency),
    auth: tuple = Depends(get_required_api_key),
):
    """
    Update the current user's email notification preferences.

    Supports both Bearer token (API key) and session cookie authentication.
    Only the fields provided will be updated.

    Fields:
    - email_scan_complete: Notify when document scans complete
    - email_remediation_complete: Notify when auto-remediation finishes
    - email_critical_alerts: Immediate alerts for critical accessibility issues
    - email_weekly_summary: Weekly compliance digest
    - weekly_summary_day: Day of week (0=Monday, 6=Sunday)
    - weekly_summary_hour: Hour of day (0-23 UTC)
    """
    _, user_id, _ = auth

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields if provided
    if prefs_update.email_scan_complete is not None:
        user.email_scan_complete = prefs_update.email_scan_complete

    if prefs_update.email_remediation_complete is not None:
        user.email_remediation_complete = prefs_update.email_remediation_complete

    if prefs_update.email_critical_alerts is not None:
        user.email_critical_alerts = prefs_update.email_critical_alerts

    if prefs_update.email_weekly_summary is not None:
        user.email_weekly_summary = prefs_update.email_weekly_summary

    if prefs_update.weekly_summary_day is not None:
        user.weekly_summary_day = prefs_update.weekly_summary_day

    if prefs_update.weekly_summary_hour is not None:
        user.weekly_summary_hour = prefs_update.weekly_summary_hour

    db.commit()
    db.refresh(user)

    logger.info(f"Updated email preferences for user {user.id}")

    return EmailPreferencesResponse(
        email_scan_complete=(
            user.email_scan_complete if user.email_scan_complete is not None else True
        ),
        email_remediation_complete=(
            user.email_remediation_complete
            if user.email_remediation_complete is not None
            else True
        ),
        email_critical_alerts=(
            user.email_critical_alerts
            if user.email_critical_alerts is not None
            else True
        ),
        email_weekly_summary=(
            user.email_weekly_summary if user.email_weekly_summary is not None else True
        ),
        weekly_summary_day=(
            user.weekly_summary_day if user.weekly_summary_day is not None else 0
        ),
        weekly_summary_hour=(
            user.weekly_summary_hour if user.weekly_summary_hour is not None else 9
        ),
    )


# ==================== Session Management ====================


class SessionListItem(BaseModel):
    """Response model for a session in the list."""

    id: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime
    last_used_at: Optional[datetime]
    is_current: bool


class SessionListResponse(BaseModel):
    """Response model for list of sessions."""

    sessions: List[SessionListItem]
    total: int


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    db: Session = Depends(get_db_dependency),
    auth: tuple = Depends(get_required_api_key),
):
    """
    List all active sessions for the current user.

    Supports both Bearer token (API key) and session cookie authentication.
    Returns all non-revoked sessions, marking the current one.
    """
    from ..db.models import UserSession
    from datetime import timezone as tz

    _, user_id, _ = auth

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Try to get current session JTI from cookie (for marking current session)
    current_jti = None
    access_token = request.cookies.get("aelira_access")
    if access_token:
        session_service = get_session_service()
        result = session_service.validate_session(db, access_token)
        if result:
            _, payload = result
            current_jti = payload.get("jti")

    # Get all active sessions for this user
    sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .filter(UserSession.revoked_at.is_(None))
        .filter(UserSession.expires_at > datetime.now(tz.utc))
        .order_by(UserSession.created_at.desc())
        .all()
    )

    session_items = []
    for sess in sessions:
        # Parse user agent to get a friendlier description
        user_agent_display = sess.user_agent
        if user_agent_display and len(user_agent_display) > 100:
            user_agent_display = user_agent_display[:100] + "..."

        session_items.append(
            SessionListItem(
                id=sess.id,
                ip_address=sess.ip_address,
                user_agent=user_agent_display,
                created_at=sess.created_at,
                last_used_at=sess.last_used_at,
                is_current=sess.access_token_jti == current_jti,
            )
        )

    return SessionListResponse(
        sessions=session_items,
        total=len(session_items),
    )


@router.delete("/sessions/{session_id}")
async def revoke_session_by_id(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Revoke a specific session by ID.

    Requires a valid session. Cannot revoke the current session (use logout instead).
    """
    from ..db.models import UserSession
    from datetime import timezone as tz

    session_service = get_session_service()

    # Get access token from cookie
    access_token = request.cookies.get("aelira_access")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Validate session
    result = session_service.validate_session(db, access_token)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    user, payload = result
    current_jti = payload.get("jti")

    # Find the session to revoke
    target_session = (
        db.query(UserSession)
        .filter(UserSession.id == session_id)
        .filter(UserSession.user_id == user.id)
        .filter(UserSession.revoked_at.is_(None))
        .first()
    )

    if not target_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Don't allow revoking the current session
    if target_session.access_token_jti == current_jti:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke current session. Use logout instead.",
        )

    # Revoke the session
    target_session.revoked_at = datetime.now(tz.utc)
    db.commit()

    # Audit log session revocation
    audit = get_audit_service(db)
    audit.log_session_revoke(user_id=user.id, session_id=session_id, request=request)

    logger.info(f"User {user.id} revoked session {session_id}")

    return {"success": True, "message": "Session revoked"}


@router.delete("/sessions")
async def revoke_all_other_sessions(
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Revoke all sessions except the current one.

    Useful for security purposes (e.g., after password change, suspicious activity).
    """
    from ..db.models import UserSession
    from datetime import timezone as tz

    session_service = get_session_service()

    # Get access token from cookie
    access_token = request.cookies.get("aelira_access")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Validate session
    result = session_service.validate_session(db, access_token)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    user, payload = result
    current_jti = payload.get("jti")

    # Find all other active sessions
    other_sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .filter(UserSession.revoked_at.is_(None))
        .filter(UserSession.access_token_jti != current_jti)
        .all()
    )

    # Revoke them all
    revoked_count = 0
    for sess in other_sessions:
        sess.revoked_at = datetime.now(tz.utc)
        revoked_count += 1

    db.commit()

    # Audit log revoking all sessions
    if revoked_count > 0:
        audit = get_audit_service(db)
        audit.log_session_revoke_all(
            user_id=user.id,
            sessions_revoked=revoked_count,
            request=request,
        )

    logger.info(f"User {user.id} revoked {revoked_count} other sessions")

    return {
        "success": True,
        "message": f"Revoked {revoked_count} session(s)",
        "revoked_count": revoked_count,
    }


# ==================== Health Check ====================


@router.get("/health")
def auth_health():
    """Health check for authentication service"""
    return {
        "status": "healthy",
        "service": "authentication",
        "features": [
            "api-key-generation",
            "rate-limiting",
            "department-management",
            "individual-signup",
            "quota-tracking",
            "magic-link-login",
            "session-management",
        ],
    }
