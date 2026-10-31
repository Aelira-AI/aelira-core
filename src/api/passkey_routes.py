"""
Passkey (WebAuthn / FIDO2) API Endpoints

Provides endpoints for:
- WebAuthn registration options & verification (authenticated enrollment)
- WebAuthn login options & assertion verification (public passwordless sign-in)
- Credential management (list, rename, revoke enrolled passkeys)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
)
from ..auth.passkey_service import (
    PasskeyService,
    WebAuthnVerificationError,
    get_passkey_service,
)
from ..auth.session_service import get_session_service
from ..config.settings import get_settings
from ..db.database import get_db_dependency
from ..db.models import User, WebAuthnCredential
from ..security.client_ip import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/passkey", tags=["passkeys"])


# ==================== Request / Response Models ====================


class PasskeyRegisterOptionsResponse(BaseModel):
    options: Dict[str, Any]
    challenge_id: str


class PasskeyRegisterVerifyRequest(BaseModel):
    challenge_id: str
    credential: Dict[str, Any]
    friendly_name: Optional[str] = Field(None, max_length=100)


class PasskeyLoginOptionsRequest(BaseModel):
    email: Optional[EmailStr] = None


class PasskeyLoginOptionsResponse(BaseModel):
    options: Dict[str, Any]
    challenge_id: str


class PasskeyLoginVerifyRequest(BaseModel):
    challenge_id: str
    credential: Dict[str, Any]


class PasskeyCredentialSummary(BaseModel):
    id: str
    friendly_name: str
    aaguid: Optional[str]
    created_at: str
    last_used_at: Optional[str]
    backup_eligible: bool


class PasskeyRenameRequest(BaseModel):
    friendly_name: str = Field(..., min_length=1, max_length=100)


# ==================== Registration Endpoints ====================


@router.post(
    "/register/options",
    response_model=PasskeyRegisterOptionsResponse,
    summary="Generate passkey registration challenge options",
)
async def get_registration_options(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> PasskeyRegisterOptionsResponse:
    """Generate options for registering a new passkey under the authenticated user."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    options_json, challenge_id = passkey_service.generate_registration_options(
        db=db,
        user=user,
        request=request,
    )

    return PasskeyRegisterOptionsResponse(
        options=json.loads(options_json),
        challenge_id=challenge_id,
    )


@router.post(
    "/register/verify",
    summary="Verify and store a newly enrolled passkey",
)
async def verify_registration(
    body: PasskeyRegisterVerifyRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> Dict[str, Any]:
    """Verify registration response and link the passkey to the user account."""
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        credential = passkey_service.verify_registration(
            db=db,
            user=user,
            challenge_id=body.challenge_id,
            credential_data=body.credential,
            friendly_name=body.friendly_name,
        )
    except WebAuthnVerificationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return {
        "success": True,
        "message": "Passkey registered successfully",
        "credential": {
            "id": credential.id,
            "friendly_name": credential.friendly_name,
            "created_at": credential.created_at.isoformat()
            if credential.created_at
            else None,
        },
    }


# ==================== Authentication Endpoints ====================


@router.post(
    "/login/options",
    response_model=PasskeyLoginOptionsResponse,
    summary="Generate passkey authentication challenge options",
)
async def get_login_options(
    request: Request,
    body: Optional[PasskeyLoginOptionsRequest] = None,
    db: Session = Depends(get_db_dependency),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> PasskeyLoginOptionsResponse:
    """
    Generate authentication options for logging in with a passkey.

    If email is passed, options restrict to credentials for that user.
    If email is omitted, options allow discoverable resident keys (Conditional UI).
    """
    email = body.email if body else None
    options_json, challenge_id = passkey_service.generate_authentication_options(
        db=db,
        request=request,
        email=email,
    )

    return PasskeyLoginOptionsResponse(
        options=json.loads(options_json),
        challenge_id=challenge_id,
    )


@router.post(
    "/login/verify",
    summary="Verify passkey assertion and issue session cookies",
)
async def verify_login(
    body: PasskeyLoginVerifyRequest,
    request: Request,
    db: Session = Depends(get_db_dependency),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> JSONResponse:
    """Verify assertion from passkey and mint user session."""
    session_service = get_session_service()
    settings = get_settings()

    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        user, _ = passkey_service.verify_authentication(
            db=db,
            challenge_id=body.challenge_id,
            credential_data=body.credential,
        )
    except WebAuthnVerificationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    # Issue session tokens
    access_token, refresh_token, access_exp, refresh_exp = (
        session_service.create_session(
            db=db,
            user=user,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    )

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
            "redirect_url": f"{settings.magic_link_base_url}/dashboard",
        }
    )

    # Set httpOnly session cookies matching platform standard
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


# ==================== Management Endpoints ====================


@router.get(
    "/list",
    response_model=List[PasskeyCredentialSummary],
    summary="List registered passkeys for current user",
)
async def list_user_passkeys(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> List[PasskeyCredentialSummary]:
    """Retrieve all passkeys registered by the authenticated user."""
    creds = (
        db.query(WebAuthnCredential)
        .filter(WebAuthnCredential.user_id == principal.user_id)
        .order_by(WebAuthnCredential.created_at.desc())
        .all()
    )

    return [
        PasskeyCredentialSummary(
            id=c.id,
            friendly_name=c.friendly_name,
            aaguid=c.aaguid,
            created_at=c.created_at.isoformat() if c.created_at else "",
            last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
            backup_eligible=c.backup_eligible,
        )
        for c in creds
    ]


@router.patch(
    "/{credential_id}",
    summary="Rename an enrolled passkey",
)
async def rename_passkey(
    credential_id: str,
    body: PasskeyRenameRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Update friendly name for a passkey owned by the user."""
    cred = (
        db.query(WebAuthnCredential)
        .filter(
            WebAuthnCredential.id == credential_id,
            WebAuthnCredential.user_id == principal.user_id,
        )
        .first()
    )
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Passkey not found",
        )

    cred.friendly_name = body.friendly_name.strip()
    db.commit()

    return {
        "success": True,
        "message": "Passkey renamed successfully",
        "credential_id": cred.id,
        "friendly_name": cred.friendly_name,
    }


@router.delete(
    "/{credential_id}",
    summary="Revoke an enrolled passkey",
)
async def delete_passkey(
    credential_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Delete a registered passkey owned by the user."""
    cred = (
        db.query(WebAuthnCredential)
        .filter(
            WebAuthnCredential.id == credential_id,
            WebAuthnCredential.user_id == principal.user_id,
        )
        .first()
    )
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Passkey not found",
        )

    db.delete(cred)
    db.commit()

    logger.info("Revoked passkey %s for user %s", credential_id, principal.user_id)
    return {
        "success": True,
        "message": "Passkey revoked successfully",
    }
