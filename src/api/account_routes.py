"""
Account Management API Endpoints

Provides endpoints for:
- Account deactivation (soft delete)
- GDPR account deletion (request, confirm, cancel)
- Deletion status check
- Data export (GDPR Article 20)
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Tuple
from contextlib import contextmanager
import logging

from ..db.database import get_db_dependency
from ..db.models import APIKey, User
from ..auth.dependencies import get_required_api_key
from ..services.account_deletion_service import (
    AccountDeliveryError,
    AccountLifecycleError,
    get_account_deletion_service,
)
from ..security.client_ip import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["Account Management"])


# ── Pydantic Models ──────────────────────────────────────────────────────────


class DeactivateRequest(BaseModel):
    confirm: bool
    reason: Optional[str] = None


class DeletionConfirmRequest(BaseModel):
    code: str
    reason: Optional[str] = None


# ── Helper ───────────────────────────────────────────────────────────────────


def _get_user(db: Session, user_id: str) -> User:
    """Load the full User object from user_id."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


def _get_client_ip(request: Request) -> str:
    """Extract client IP across the configured trusted-proxy boundary."""
    return get_client_ip(request)


@contextmanager
def _account_operation(db: Session):
    """Preserve public domain errors and bound unexpected dependency failures."""
    try:
        yield
    except HTTPException:
        raise
    except AccountLifecycleError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except AccountDeliveryError:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Unable to deliver confirmation code. Please request a new code.",
        ) from None
    except Exception as exc:
        db.rollback()
        logger.error("Account operation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Unable to complete account operation. Please try again.",
        ) from None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/deactivate")
async def deactivate_account(
    body: DeactivateRequest,
    request: Request,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Deactivate account (soft delete). Revokes all sessions and API keys.
    Blocks re-registration for 90 days.

    Available to all users of the free self-hosted edition.
    """
    _, user_id, _ = api_key_info

    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must confirm account deactivation.",
        )

    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        result = service.deactivate_account(
            db=db,
            user=user,
            reason=body.reason,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        return result


@router.post("/deletion/request")
async def request_deletion(
    request: Request,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Send a 6-digit confirmation code to the user's email for GDPR account deletion.
    Code expires in 15 minutes.
    """
    _, user_id, _ = api_key_info
    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        result = await service.request_deletion_code(
            db=db,
            user=user,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        return result


@router.post("/deletion/confirm")
async def confirm_deletion(
    body: DeletionConfirmRequest,
    request: Request,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Confirm account deletion with the 6-digit code.
    Schedules permanent data deletion in 30 days.
    Account is deactivated immediately.
    """
    _, user_id, _ = api_key_info
    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        result = await service.confirm_deletion(
            db=db,
            user=user,
            code=body.code,
            reason=body.reason,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        return result


@router.post("/deletion/cancel")
async def cancel_deletion(
    request: Request,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Cancel a pending account deletion within the 30-day grace period.
    Reactivates the account.
    """
    _, user_id, _ = api_key_info
    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        result = service.cancel_pending_deletion(
            db=db,
            user=user,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        return result


@router.get("/deletion/status")
async def get_deletion_status(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Check if account deletion is pending and get details."""
    _, user_id, _ = api_key_info
    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        return service.get_deletion_status(db, user)


@router.get("/export")
async def export_data(
    request: Request,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Export all user data as JSON (GDPR Article 20 - Right to Data Portability).
    """
    _, user_id, _ = api_key_info
    with _account_operation(db):
        user = _get_user(db, user_id)
        service = get_account_deletion_service()
        return service.export_user_data(db, user)
