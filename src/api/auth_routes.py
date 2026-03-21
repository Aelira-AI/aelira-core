"""
Authentication API Endpoints

Provides endpoints for:
- API key generation
- API key management (list, revoke)
- Department account creation
- Self-service individual faculty signup
- Quota status tracking
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional
from datetime import datetime, timedelta
import logging
import re

from fastapi.responses import JSONResponse

from ..db.database import get_db_dependency
from ..db.models import APIKey, User, Department, UserRole, WaitlistSignup
from ..auth.dependencies import get_required_api_key
from ..auth.auth_service import AuthService, RateLimiter
from ..auth.session_service import get_session_service
from ..auth.jwt_service import get_jwt_service
from ..middleware.quota import get_quota_status, get_next_period_end
from ..config.settings import get_tier_quota, get_settings, FREE_ACCOUNT_LIMIT
from ..mailer.email_service import get_email_service
from ..security.abuse_detector import check_signup_abuse, log_signup
from ..security.audit_service import get_audit_service
from ..services.email_templates import render_department_trial_welcome_email
from ..config.settings import get_settings as get_app_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])
# auto_error=False so we can return 401 instead of 403 for missing auth
security = HTTPBearer(auto_error=False)


# ==================== Request/Response Models ====================


class CreateAPIKeyRequest(BaseModel):
    name: str
    rate_limit_per_hour: int = 100
    expires_days: Optional[int] = None


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
    name: str
    institution: str
    contact_email: EmailStr
    contact_name: str
    tier: str = "trial"


class DepartmentResponse(BaseModel):
    id: str
    name: str
    institution: str
    contact_email: str
    tier: str
    max_users: int
    created_at: datetime


# ==================== Individual Faculty Signup Models ====================


class IndividualSignupRequest(BaseModel):
    """Request model for individual faculty self-service signup."""

    email: EmailStr
    name: str
    institution: Optional[str] = None  # Auto-detected from email domain if not provided

    @validator("email")
    def validate_edu_email(cls, v):
        """Validate that email is from an educational institution (.edu domain)."""
        # Allow .edu and common international education domains
        edu_patterns = [
            r"\.edu$",
            r"\.edu\.[a-z]{2}$",  # .edu.au, .edu.sg, etc.
            r"\.ac\.[a-z]{2}$",  # .ac.uk, .ac.jp, etc.
            r"\.edu\.[a-z]{2}\.[a-z]{2}$",  # .edu.co.uk, etc.
        ]

        domain = v.split("@")[1].lower()
        is_edu = any(re.search(pattern, domain) for pattern in edu_patterns)

        if not is_edu:
            raise ValueError(
                "Email must be from an educational institution (.edu domain). "
                "If you're from an institution without a .edu domain, please contact support."
            )
        return v


class IndividualSignupResponse(BaseModel):
    """Response model for individual faculty signup."""

    success: bool
    message: str
    api_key: str  # Full API key (shown only once)
    key_prefix: str  # For identification
    department_id: str
    user_id: str
    tier: str
    quota: dict  # Initial quota info
    warning: str = "Store this API key safely! It will only be shown once."


class QuotaStatusResponse(BaseModel):
    """Response model for quota status."""

    tier: str
    unlimited: bool
    scans: dict
    pages: dict
    resets_at: Optional[str]
    features: List[str]
    excluded: List[str]
    upgrade_url: str = "/pricing"


# ==================== Helper Functions ====================


def get_current_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> APIKey:
    """
    Dependency to get and validate current API key.

    Supports two authentication methods:
    1. Bearer token: Authorization: Bearer <api_key> (for CLI/programmatic access)
    2. Session cookie: aelira_access cookie (for dashboard users after magic link login)

    For session-based auth, looks up the user's API key from the database.

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

        if result:
            user, payload = result

            # Look up user's default API key
            api_key = (
                db.query(APIKey)
                .filter(
                    APIKey.user_id == user.id,
                    APIKey.is_active == True,  # noqa: E712 - SQLAlchemy comparison
                )
                .order_by(APIKey.created_at)  # Get the first (default) key
                .first()
            )

            if api_key:
                # Apply rate limiting for session users too
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

            # User authenticated but no API key found (shouldn't happen normally)
            logger.warning(f"Session user {user.id} has no API key - creating one")
            # Create API key for the user
            api_key, _ = AuthService.create_api_key(
                db=db,
                user_id=user.id,
                department_id=user.department_id,
                name="Default API Key",
                rate_limit_per_hour=100,
                expires_days=None,
            )
            return api_key

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
    current_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Create a new API key

    Requires a valid API key. New keys inherit the user_id and department_id
    from the authenticating key.

    Returns the full API key - **store it safely, it will only be shown once!**
    """
    # Get user/department from authenticated API key
    user_id = current_key.user_id
    department_id = current_key.department_id

    # Create API key
    api_key, full_key = AuthService.create_api_key(
        db=db,
        user_id=user_id,
        department_id=department_id,
        name=request.name,
        rate_limit_per_hour=request.rate_limit_per_hour,
        expires_days=request.expires_days,
    )

    # Audit log API key creation
    audit = get_audit_service(db)
    audit.log_api_key_create(
        user_id=user_id,
        department_id=department_id,
        api_key_id=api_key.id,
        key_name=request.name,
    )

    return {
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


@router.get("/keys", response_model=List[APIKeyResponse])
def list_api_keys(
    current_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    List all API keys for current user

    Requires a valid API key. Returns all keys belonging to the same user.
    """
    # Get user_id from authenticated API key
    user_id = current_key.user_id

    keys = AuthService.list_api_keys(db, user_id)

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
    key_id: str,
    current_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Revoke (deactivate) an API key

    Requires a valid API key. Can only revoke keys belonging to the same user.
    """
    # Get user_id from authenticated API key
    user_id = current_key.user_id

    success = AuthService.revoke_api_key(db, key_id, user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or unauthorized",
        )

    # Audit log API key revocation
    audit = get_audit_service(db)
    audit.log_api_key_revoke(
        user_id=user_id,
        department_id=current_key.department_id,
        api_key_id=key_id,
    )

    return {"success": True, "message": f"API key {key_id} revoked"}


@router.get("/validate")
def validate_api_key_dashboard(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Validate API key and return department info (for dashboard login)

    This endpoint is used by the dashboard to authenticate and get department details.
    """
    # Get department details
    department = (
        db.query(Department).filter(Department.id == api_key.department_id).first()
    )

    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found"
        )

    # Get user details
    user = db.query(User).filter(User.id == api_key.user_id).first()

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
        "api_key": {
            "id": api_key.id,
            "name": api_key.name,
            "rate_limit_per_hour": api_key.rate_limit_per_hour,
        },
    }


@router.get("/keys/validate")
def validate_api_key(api_key: APIKey = Depends(get_current_api_key)):
    """
    Validate current API key (for testing authentication)

    This endpoint requires authentication and returns the API key details.
    """
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


@router.post("/departments", response_model=DepartmentResponse)
async def create_department(
    request: CreateDepartmentRequest,
    http_request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Create a new department account

    This endpoint is for initial department signup.

    Security:
    - Abuse detection (IP tracking, domain limits, bot detection)
    - Rate limiting on signup attempts
    """
    # Get client info for abuse detection
    client_ip = http_request.client.host if http_request.client else "unknown"
    user_agent = http_request.headers.get("user-agent")
    fingerprint = http_request.headers.get("x-device-fingerprint")

    # Forward IP headers (if behind proxy)
    forwarded_for = http_request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Check for abuse before proceeding
    abuse_result = await check_signup_abuse(
        db=db,
        email=request.contact_email,
        ip_address=client_ip,
        user_agent=user_agent,
        fingerprint=fingerprint,
    )

    if not abuse_result.allowed:
        log_signup(
            db=db,
            email=request.contact_email,
            ip_address=client_ip,
            user_agent=user_agent,
            fingerprint=fingerprint,
            success=False,
        )

        if abuse_result.recommended_action == "block":
            logger.warning(
                f"Blocked department creation: {request.contact_email} from {client_ip} - "
                f"Reason: {abuse_result.reason}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many signup attempts. Please try again later.",
            )

    # Check if department already exists
    existing = (
        db.query(Department)
        .filter(
            Department.name == request.name,
            Department.institution == request.institution,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Department already exists"
        )

    # Create department
    department = Department(
        name=request.name,
        institution=request.institution,
        contact_email=request.contact_email,
        contact_name=request.contact_name,
        tier=request.tier,
        max_users=5 if request.tier == "trial" else 50,
        trial_ends_at=datetime.utcnow() + timedelta(days=30),
    )

    db.add(department)
    db.commit()
    db.refresh(department)

    logger.info(
        f"Created department: {department.id} ({department.name} at {department.institution})"
    )

    # Send department trial welcome email (non-blocking)
    try:
        app_settings = get_app_settings()
        email_service = get_email_service()

        html_content = render_department_trial_welcome_email(
            name=request.contact_name,
            department_name=request.name,
            institution=request.institution,
            trial_days=30,
            dashboard_url=f"{app_settings.magic_link_base_url}/dashboard",
        )

        asyncio.create_task(
            email_service.send_email(
                to_emails=[request.contact_email],
                subject=f"Welcome to Your Aelira Trial - {request.name}",
                html_content=html_content,
                text_content=f"Welcome to your 30-day Aelira trial for {request.name} at {request.institution}!",
            )
        )
        logger.info(
            f"Queued department trial welcome email for {request.contact_email}"
        )
    except Exception as e:
        logger.warning(f"Failed to send department trial welcome email: {e}")

    return DepartmentResponse(
        id=department.id,
        name=department.name,
        institution=department.institution,
        contact_email=department.contact_email,
        tier=department.tier,
        max_users=department.max_users,
        created_at=department.created_at,
    )


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


@router.post("/signup/individual", response_model=IndividualSignupResponse)
async def signup_individual(
    signup_request: IndividualSignupRequest,
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Self-service signup for individual faculty members.

    Creates:
    - A personal "department" (individual_free tier, max_users=1)
    - A user account linked to the department
    - An API key for dashboard/API access

    Requirements:
    - Must use an educational email (.edu, .ac.uk, etc.)

    Security:
    - Abuse detection (IP tracking, domain limits, bot detection)
    - Rate limiting on signup attempts

    Returns the API key ONCE - it must be saved by the user.
    """
    import uuid

    # Get client info for abuse detection
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    fingerprint = request.headers.get("x-device-fingerprint")

    # Forward IP headers (if behind proxy)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Check for abuse before proceeding
    abuse_result = await check_signup_abuse(
        db=db,
        email=signup_request.email,
        ip_address=client_ip,
        user_agent=user_agent,
        fingerprint=fingerprint,
    )

    if not abuse_result.allowed:
        # Log the blocked attempt
        log_signup(
            db=db,
            email=signup_request.email,
            ip_address=client_ip,
            user_agent=user_agent,
            fingerprint=fingerprint,
            success=False,
        )

        if abuse_result.recommended_action == "block":
            logger.warning(
                f"Blocked signup attempt: {signup_request.email} from {client_ip} - "
                f"Reason: {abuse_result.reason}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "signup_blocked",
                    "message": "Too many signup attempts. Please try again later.",
                    "reason": abuse_result.reason,
                },
            )
        elif abuse_result.recommended_action == "challenge":
            logger.info(
                f"Challenge required for signup: {signup_request.email} - "
                f"Type: {abuse_result.challenge_type}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "verification_required",
                    "message": "Additional verification required to complete signup.",
                    "challenge_type": abuse_result.challenge_type,
                    "signals": [s.signal_type for s in abuse_result.signals],
                },
            )

    # Check if email already exists
    existing_user = db.query(User).filter(User.email == signup_request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists. Please log in instead.",
        )

    # Check free account limit (scarcity control for bootstrapped operation)
    free_account_count = (
        db.query(Department).filter(Department.tier == "individual_free").count()
    )

    if free_account_count >= FREE_ACCOUNT_LIMIT:
        logger.warning(
            f"Free account limit reached ({free_account_count}/{FREE_ACCOUNT_LIMIT}). "
            f"Rejecting signup: {signup_request.email}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "free_accounts_full",
                "message": "Free accounts are currently full. "
                "Join our waitlist or upgrade to a paid plan for immediate access.",
                "current_count": free_account_count,
                "limit": FREE_ACCOUNT_LIMIT,
                "waitlist_url": "/waitlist",
                "pricing_url": "/pricing",
            },
        )

    # Check if user is converting from waitlist
    waitlist_signup = (
        db.query(WaitlistSignup)
        .filter(WaitlistSignup.email == signup_request.email)
        .filter(WaitlistSignup.converted == False)  # noqa: E712
        .first()
    )

    # Auto-detect institution from email if not provided
    institution = signup_request.institution or extract_institution_from_email(
        signup_request.email
    )

    # Create "individual department" for this faculty member
    department = Department(
        id=str(uuid.uuid4()),
        name=f"{signup_request.name}'s Workspace",  # Personal workspace name
        institution=institution,
        contact_email=signup_request.email,
        contact_name=signup_request.name,
        tier="individual_free",
        max_users=1,  # Individual tier = single user
        subscription_status="active",  # Free tier is always "active"
        scans_this_month=0,
        pages_this_month=0,
        quota_reset_at=get_next_period_end(),  # 30-day rolling period from signup
    )
    db.add(department)
    db.flush()  # Get ID without committing

    # Create user account
    # For self-service signup, we use email as google_id placeholder
    # Real Google OAuth can be linked later
    user = User(
        id=str(uuid.uuid4()),
        email=signup_request.email,
        google_id=f"self_signup_{signup_request.email}",  # Placeholder for non-OAuth signup
        name=signup_request.name,
        department_id=department.id,
        role=UserRole.ADMIN,  # Owner of their individual workspace
        is_active=True,
        # Inherit marketing opt-in from waitlist if converting (only if double opt-in confirmed)
        email_marketing=(
            waitlist_signup.newsletter and waitlist_signup.email_confirmed
            if waitlist_signup
            else False
        ),
        email_marketing_confirmed=(
            waitlist_signup.email_confirmed if waitlist_signup else False
        ),
        email_marketing_confirmed_at=(
            waitlist_signup.email_confirmed_at if waitlist_signup else None
        ),
    )
    db.add(user)
    db.flush()

    # Create API key
    api_key, full_key = AuthService.create_api_key(
        db=db,
        user_id=user.id,
        department_id=department.id,
        name="Default API Key",
        rate_limit_per_hour=100,
        expires_days=None,  # No expiration for individual tier
    )

    db.commit()

    # Mark waitlist signup as converted (if applicable)
    if waitlist_signup:
        waitlist_signup.converted = True
        waitlist_signup.converted_at = datetime.utcnow()
        waitlist_signup.converted_user_id = user.id
        db.commit()
        logger.info(
            f"Waitlist conversion: {signup_request.email} (waitlist_id={waitlist_signup.id}) -> user_id={user.id}"
        )

    logger.info(
        f"Individual signup: {signup_request.email} ({institution}) - "
        f"department={department.id}, user={user.id}"
        f"{' (from waitlist)' if waitlist_signup else ''}"
    )

    # Log successful signup for abuse tracking
    log_signup(
        db=db,
        email=signup_request.email,
        ip_address=client_ip,
        user_agent=user_agent,
        fingerprint=fingerprint,
        success=True,
    )

    # Get initial quota status
    quota_info = get_quota_status(db, department.id)

    # Send welcome email (non-blocking)
    tier_config = get_tier_quota("individual_free")
    try:
        email_service = get_email_service()
        if email_service.is_configured():
            import asyncio

            asyncio.create_task(
                email_service.send_welcome_individual(
                    to_email=signup_request.email,
                    name=signup_request.name,
                    api_key=full_key,
                    tier="individual_free",
                    scans_limit=tier_config.get("scans_per_month", 10),
                    pages_limit=tier_config.get("pages_per_scan", 50),
                )
            )
            logger.info(f"Welcome email queued for {signup_request.email}")
    except Exception as e:
        # Don't fail signup if email fails
        logger.warning(f"Failed to send welcome email to {signup_request.email}: {e}")

    return IndividualSignupResponse(
        success=True,
        message=f"Welcome to Aelira, {signup_request.name}! Your free account is ready.",
        api_key=full_key,
        key_prefix=api_key.key_prefix,
        department_id=department.id,
        user_id=user.id,
        tier="individual_free",
        quota=quota_info,
        warning="Store this API key safely! It will only be shown once.",
    )


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
    """Request model for magic link signup/login - requires educational email"""

    email: EmailStr
    # Name and institution are required for new signups, optional for returning logins
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    institution: Optional[str] = Field(None, min_length=2, max_length=200)

    @validator("email")
    def validate_edu_email(cls, v):
        """Validate that email is from an educational institution."""
        # Allow .edu and common international education domains
        edu_patterns = [
            r"\.edu$",  # .edu
            r"\.edu\.[a-z]{2}$",  # .edu.au, .edu.sg, etc.
            r"\.ac\.[a-z]{2}$",  # .ac.uk, .ac.jp, etc.
            r"\.edu\.[a-z]{2}\.[a-z]{2}$",  # .edu.co.uk, etc.
        ]

        domain = v.split("@")[1].lower()
        is_edu = any(re.search(pattern, domain) for pattern in edu_patterns)

        if not is_edu:
            raise ValueError(
                "Please use your educational email address (.edu, .edu.au, .ac.uk, etc.). "
                "Aelira is currently available for faculty and staff at educational institutions."
            )
        return v


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
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    email = request_body.email.lower()

    # Rate limit by email (5/hour)
    email_limit_key = f"magic_link_email:{email}"
    email_allowed, _ = RateLimiter.check_rate_limit(email_limit_key, 5)
    if not email_allowed:
        logger.warning(f"Magic link rate limit exceeded for email: {email}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many magic link requests. Please try again later.",
        )

    # Rate limit by IP (10/hour)
    ip_limit_key = f"magic_link_ip:{client_ip}"
    ip_allowed, _ = RateLimiter.check_rate_limit(ip_limit_key, 10)
    if not ip_allowed:
        logger.warning(f"Magic link rate limit exceeded for IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests from this location. Please try again later.",
        )

    # Check if email is blocked (deactivated/deleted account)
    # Return same response to prevent email enumeration
    from ..services.account_deletion_service import AccountDeletionService

    blocked, _ = AccountDeletionService.is_email_blocked(db, email)
    if blocked:
        logger.info(
            f"Magic link request blocked for deleted/deactivated email: {email}"
        )
        return {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }

    # Create magic link (with signup profile data if provided)
    token = session_service.create_magic_link(
        db=db,
        email=email,
        ip_address=client_ip,
        user_agent=user_agent,
        signup_name=request_body.name,
        signup_institution=request_body.institution,
    )

    # Build magic link URL
    magic_link_url = (
        f"{settings.magic_link_base_url}/auth/verify?email={email}&token={token}"
    )

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
            logger.info(f"Magic link email queued for {email}")
    except Exception as e:
        logger.warning(f"Failed to send magic link email to {email}: {e}")
        # Still return success to prevent email enumeration

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
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Verify the magic link
    magic_link = session_service.verify_magic_link(db, email.lower(), token)
    if not magic_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired magic link. Please request a new one.",
        )

    # Get or create user (use signup profile data stored on the magic link)
    user, is_new = session_service.get_or_create_user_for_magic_link(
        db,
        email.lower(),
        name=magic_link.signup_name,
        institution=magic_link.signup_institution,
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

    logger.info(f"Magic link login successful for {email} (new_user={is_new})")
    return response


# ==================== Session Management ====================


@router.get("/session/validate")
async def validate_session(
    request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Validate the current session and return user info.

    Checks the access token from cookies.
    Used by the frontend to check if the user is logged in.
    """
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

    # Get department info
    department = (
        db.query(Department).filter(Department.id == user.department_id).first()
    )

    return {
        "valid": True,
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
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Get refresh token from cookie
    refresh_token = request.cookies.get("aelira_refresh")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    # Refresh session
    result = session_service.refresh_session(
        db=db,
        refresh_token=refresh_token,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token. Please log in again.",
        )

    access_token, new_refresh_token, access_exp, refresh_exp = result

    # Create response with new cookies
    response = JSONResponse(content={"success": True, "message": "Session refreshed"})

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

    # Get access token from cookie
    access_token = request.cookies.get("aelira_access")

    user_id = None
    if access_token:
        # Get user ID from token
        jwt_service = get_jwt_service()
        user_id = jwt_service.get_user_id_from_token(access_token)
        if user_id:
            # Revoke session
            session_service.revoke_session(db, user_id, access_token)

            # Audit log logout
            audit = get_audit_service(db)
            audit.log_logout(user_id=user_id, request=request)

    # Create response that clears cookies
    response = JSONResponse(content={"success": True, "message": "Logged out"})

    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }

    if settings.env == "production" and settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain

    # Clear cookies by setting empty value and immediate expiration
    response.delete_cookie(key="aelira_access", **cookie_settings)
    response.delete_cookie(key="aelira_refresh", **cookie_settings)

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
    email_marketing: bool
    email_marketing_pending_confirmation: bool = False  # True if awaiting double opt-in
    weekly_summary_day: int  # 0=Monday, 6=Sunday
    weekly_summary_hour: int  # 0-23 UTC


class EmailPreferencesUpdate(BaseModel):
    """Request model for updating email preferences."""

    email_scan_complete: Optional[bool] = None
    email_remediation_complete: Optional[bool] = None
    email_critical_alerts: Optional[bool] = None
    email_weekly_summary: Optional[bool] = None
    email_marketing: Optional[bool] = None
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

    # Pending confirmation = has a token but not yet confirmed
    pending = bool(
        user.email_marketing_confirmation_token and not user.email_marketing_confirmed
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
        email_marketing=(
            user.email_marketing if user.email_marketing is not None else False
        ),
        email_marketing_pending_confirmation=pending,
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
    - email_marketing: Marketing and product update emails
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

    # Track if marketing consent changed for CRM sync
    marketing_consent_changed = False
    old_marketing_opt_in = user.email_marketing

    # Update fields if provided
    if prefs_update.email_scan_complete is not None:
        user.email_scan_complete = prefs_update.email_scan_complete

    if prefs_update.email_remediation_complete is not None:
        user.email_remediation_complete = prefs_update.email_remediation_complete

    if prefs_update.email_critical_alerts is not None:
        user.email_critical_alerts = prefs_update.email_critical_alerts

    if prefs_update.email_weekly_summary is not None:
        user.email_weekly_summary = prefs_update.email_weekly_summary

    if prefs_update.email_marketing is not None:
        if old_marketing_opt_in != prefs_update.email_marketing:
            marketing_consent_changed = True

        if prefs_update.email_marketing:
            # Opting IN — requires double opt-in confirmation via email
            # Don't set email_marketing=True yet; send confirmation first
            import secrets
            import os
            import asyncio

            token = secrets.token_urlsafe(48)
            user.email_marketing_confirmation_token = token
            user.email_marketing_confirmed = False
            # Keep email_marketing as False until confirmed
            user.email_marketing = False

            # Send confirmation email
            try:
                api_base = os.getenv("API_BASE_URL", "https://api.aelira.ai")
                confirmation_url = f"{api_base}/confirm-email/{token}"
                email_service = get_email_service()
                asyncio.create_task(
                    email_service.send_email_confirmation(
                        to_email=user.email,
                        confirmation_url=confirmation_url,
                        context="marketing",
                    )
                )
                logger.info(f"Marketing double opt-in email sent to {user.email}")
            except Exception as email_error:
                logger.warning(
                    f"Failed to send marketing confirmation email: {email_error}"
                )
        else:
            # Opting OUT — immediate, no confirmation needed
            user.email_marketing = False
            user.email_marketing_confirmed = False
            user.email_marketing_confirmation_token = None
            user.email_marketing_confirmed_at = None

    if prefs_update.weekly_summary_day is not None:
        user.weekly_summary_day = prefs_update.weekly_summary_day

    if prefs_update.weekly_summary_hour is not None:
        user.weekly_summary_hour = prefs_update.weekly_summary_hour

    db.commit()
    db.refresh(user)

    logger.info(f"Updated email preferences for user {user.id}")

    pending = bool(
        user.email_marketing_confirmation_token and not user.email_marketing_confirmed
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
        email_marketing=(
            user.email_marketing if user.email_marketing is not None else False
        ),
        email_marketing_pending_confirmation=pending,
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
