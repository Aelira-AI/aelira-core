"""TOTP (Authenticator App) API Endpoints

Provides endpoints for:
- TOTP enrollment setup & verification (authenticated enrollment with otpauth URI)
- TOTP login verification (6-digit TOTP or single-use backup recovery code)
- Credential management (status, revoke)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from ..auth.totp_service import TOTPService
from ..auth.session_service import get_session_service
from ..config.settings import get_settings
from ..db.database import get_db_dependency
from ..db.models import User
from ..security.client_ip import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/totp", tags=["totp"])


class TOTPVerifyRequest(BaseModel):
    code: str = Field(
        ..., min_length=6, max_length=16, description="6-digit TOTP code or backup code"
    )


class TOTPLoginRequest(BaseModel):
    email: Optional[EmailStr] = Field(None, description="User email address")
    user_id: Optional[str] = Field(None, description="User UUID")
    code: str = Field(
        ..., min_length=6, max_length=16, description="6-digit TOTP code or backup code"
    )


class TOTPRevokeRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=6,
        max_length=16,
        description="Current TOTP code or backup code to confirm revocation",
    )


@router.post(
    "/enroll/setup",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Begin TOTP authenticator enrollment",
)
async def setup_totp_enrollment(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Generate an unconfirmed TOTP secret and otpauth provisioning URI for the user."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    enrollment = TOTPService.begin_enrollment(user=user, db=db, issuer="Aelira")
    return {
        "success": True,
        "secret": enrollment["secret"],
        "otpauth_uri": enrollment["otpauth_uri"],
        "instructions": "Scan this barcode with your authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Duo) or enter the secret key manually, then confirm with a 6-digit code.",
    }


@router.post(
    "/enroll/verify",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Confirm TOTP authenticator enrollment",
)
async def verify_totp_enrollment(
    req: TOTPVerifyRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Verify the first TOTP code to activate authenticator app MFA and generate backup recovery codes."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        result = TOTPService.confirm_enrollment(user=user, code=req.code, db=db)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return {
        "success": True,
        "status": result["status"],
        "backup_codes": result["backup_codes"],
        "message": result["message"],
    }


@router.get(
    "/status",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get user's TOTP MFA enrollment status",
)
async def get_totp_status(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Return whether the authenticated user has an active authenticator app configured."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    status_data = TOTPService.get_status(user=user, db=db)
    return {
        "success": True,
        **status_data,
    }


@router.delete(
    "/revoke",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Revoke TOTP authenticator app",
)
async def revoke_totp(
    req: TOTPRevokeRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Revoke user's authenticator app MFA after verifying a code."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        TOTPService.revoke(user=user, code=req.code, db=db)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return {
        "success": True,
        "message": "Authenticator app MFA revoked successfully",
    }


@router.post(
    "/login/verify",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Verify TOTP code during sign-in and issue session",
)
async def verify_totp_login(
    req: TOTPLoginRequest,
    request: Request,
    db: Session = Depends(get_db_dependency),
) -> JSONResponse:
    """Verify 6-digit TOTP code or single-use backup recovery code and issue session cookies."""
    settings = get_settings()

    # Resolve target user by user_id or email
    user: Optional[User] = None
    if req.user_id:
        user = (
            db.query(User)
            .filter(User.id == req.user_id, User.is_active == True)
            .first()
        )  # noqa: E712
    elif req.email:
        user = (
            db.query(User)
            .filter(User.email == req.email.lower(), User.is_active == True)
            .first()
        )  # noqa: E712

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or user not found",
        )

    valid, method, verified_user = TOTPService.verify_login(
        user_id=user.id,
        code=req.code,
        db=db,
    )
    if not valid or not verified_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=method or "Invalid authenticator code",
        )

    # Issue session tokens and stamp cookies matching platform standard
    session_service = get_session_service()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")

    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=verified_user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

    response = JSONResponse(
        content={
            "success": True,
            "message": "Login successful",
            "method": method,
            "user": {
                "id": verified_user.id,
                "email": verified_user.email,
                "name": verified_user.name,
                "role": verified_user.role.value if verified_user.role else "faculty",
            },
            "redirect_url": f"{settings.magic_link_base_url}/dashboard",
        }
    )

    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }
    if settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain

    response.set_cookie(
        key="aelira_access",
        value=access_token,
        expires=access_exp,
        **cookie_settings,
    )
    response.set_cookie(
        key="aelira_refresh",
        value=refresh_token,
        expires=refresh_exp,
        **cookie_settings,
    )

    return response
