"""
User Management API Endpoints

Endpoints for:
- Department admin dashboard (view faculty, usage stats)
- Faculty invitation system (email invites with tokens)
- User role management (admin, faculty, student)

Author: Aelira Team
Created: January 11, 2026
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import logging

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    User,
    UserRole,
    Department,
    UserInvitation,
    InvitationPurpose,
    InvitationStatus,
    AuditLogAction,
    AuditLogStatus,
    Scan,
)
from ..db.scan_service import ScanService
from ..config.settings import get_tier_quota, get_settings
from ..mailer.email_service import get_email_service
from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..security.audit_service import get_audit_service
from ..services.account_deletion_service import AccountDeletionService
import asyncio

# Setup logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/admin", tags=["admin"])


# Pydantic models for requests/responses
class InviteUserRequest(BaseModel):
    email: EmailStr
    role: str = "faculty"  # faculty or admin


class UpdateUserRoleRequest(BaseModel):
    role: str  # faculty or admin


class DepartmentStatsResponse(BaseModel):
    total_users: int
    active_users: int
    total_scans: int
    historical_scan_count: int
    enrolled_document_count: int
    verified_document_count: int
    unverified_document_count: int
    scans_this_month: int
    avg_compliance_score: Optional[float]
    total_issues: int
    pending_invitations: int


# Auth dependency - require ADMIN or SUPER_ADMIN role
def get_admin_api_key(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Tuple[Optional[APIKey], str, str, UserRole]:
    """Admit normal admin sessions and API keys, never LTI launch scope."""
    if principal.auth_method == "lti" or principal.user_role not in {
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    }:
        raise HTTPException(status_code=403, detail="Admin access required")
    return (
        principal.api_key,
        principal.user_id,
        principal.department_id,
        principal.user_role,
    )


# ==================== User Management Endpoints ====================


@router.get("/users")
async def list_department_users(
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    List all users in the department.

    Returns:
        List of users with their roles and activity info
    """
    _, user_id, department_id, role = admin_info
    logger.info(f"Listing users for department: {department_id}")

    try:
        users = (
            db.query(User)
            .filter(User.department_id == department_id, User.is_active)
            .all()
        )

        # Batch-load scan counts to avoid N+1 queries
        user_ids = [u.id for u in users]
        scan_counts = {}
        if user_ids:
            counts = (
                db.query(Scan.user_id, func.count(Scan.id))
                .filter(Scan.user_id.in_(user_ids))
                .group_by(Scan.user_id)
                .all()
            )
            scan_counts = {uid: cnt for uid, cnt in counts}

        user_list = []
        for user in users:
            user_list.append(
                {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "picture_url": user.picture_url,
                    "role": user.role.value if user.role else "faculty",
                    "created_at": (
                        user.created_at.isoformat() if user.created_at else None
                    ),
                    "last_login_at": (
                        user.last_login_at.isoformat() if user.last_login_at else None
                    ),
                    "scan_count": scan_counts.get(user.id, 0),
                }
            )

        return {
            "success": True,
            "department_id": department_id,
            "users": user_list,
            "count": len(user_list),
        }

    except Exception as e:
        logger.error("Error listing users: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/invite")
async def invite_user(
    request: InviteUserRequest,
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Invite a new user to the department.

    Sends an invitation email with a secure token.
    Token expires in 7 days.

    Args:
        email: Email address to invite
        role: Role to assign (faculty or admin)

    Returns:
        Invitation details
    """
    _, user_id, department_id, admin_role = admin_info
    logger.info("Creating invitation for department %s", department_id)

    try:
        # Validate role
        try:
            invite_role = UserRole(request.role.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {request.role}")

        # Only super_admin can invite other super_admins
        if invite_role == UserRole.SUPER_ADMIN and admin_role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403,
                detail="Only super admins can invite other super admins",
            )

        # Check if user already exists
        existing_user = db.query(User).filter(User.email == request.email).first()
        if existing_user:
            if existing_user.department_id == department_id:
                raise HTTPException(
                    status_code=400,
                    detail="User is already a member of this department",
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="User is already a member of another department",
                )

        # Check for existing pending invitation
        existing_invite = (
            db.query(UserInvitation)
            .filter(
                UserInvitation.email == request.email,
                UserInvitation.department_id == department_id,
                UserInvitation.status == InvitationStatus.PENDING,
            )
            .first()
        )

        if existing_invite:
            raise HTTPException(
                status_code=400,
                detail="An invitation is already pending for this email",
            )

        # Check administrator-configured workspace user capacity
        department = db.query(Department).filter(Department.id == department_id).first()
        if department:
            # Get default capacity from workspace configuration (or department override)
            tier_quota = get_tier_quota(department.tier)
            max_users = tier_quota.get("max_users", -1)

            # Department-level override if set
            if department.max_users is not None and department.max_users > 0:
                max_users = department.max_users

            # -1 means unlimited users
            if max_users > 0:
                current_user_count = (
                    db.query(func.count(User.id))
                    .filter(User.department_id == department_id, User.is_active)
                    .scalar()
                    or 0
                )

                pending_invites = (
                    db.query(func.count(UserInvitation.id))
                    .filter(
                        UserInvitation.department_id == department_id,
                        UserInvitation.status == InvitationStatus.PENDING,
                    )
                    .scalar()
                    or 0
                )

                if current_user_count + pending_invites >= max_users:
                    tier_name = department.tier or "unknown"
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "user_limit_exceeded",
                            "message": f"Workspace user capacity reached ({max_users} users; configuration {tier_name}). "
                            "Ask your administrator to adjust this workspace's capacity.",
                            "current_users": current_user_count,
                            "pending_invites": pending_invites,
                            "max_users": max_users,
                            "tier": tier_name,
                        },
                    )

        # Generate secure token
        token = secrets.token_urlsafe(48)  # 64 characters base64

        # Create invitation
        invitation = UserInvitation(
            department_id=department_id,
            email=request.email,
            role=invite_role,
            token=token,
            invited_by=user_id if user_id != "test-admin-123" else None,
            status=InvitationStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )

        db.add(invitation)
        db.commit()
        db.refresh(invitation)

        # Send invitation email
        try:
            settings = get_settings()
            email_service = get_email_service()

            # Get inviter info
            inviter = (
                db.query(User).filter(User.id == user_id).first()
                if user_id != "test-admin-123"
                else None
            )
            inviter_name = (
                inviter.name if inviter and inviter.name else "Your department admin"
            )
            inviter_email = inviter.email if inviter else "admin@example.com"

            # Build accept URL
            dashboard_url = (
                settings.dashboard_url
                if hasattr(settings, "dashboard_url")
                else "https://dashboard.example.com"
            )
            accept_url = f"{dashboard_url}/accept-invitation?token={token}"

            # Format expiration date
            expires_date = invitation.expires_at.strftime("%B %d, %Y at %I:%M %p UTC")

            # Send email asynchronously
            asyncio.create_task(
                email_service.send_faculty_invitation(
                    to_email=request.email,
                    department_name=(
                        department.name if department else "Your department"
                    ),
                    role=invite_role.value,
                    inviter_name=inviter_name,
                    inviter_email=inviter_email,
                    accept_url=accept_url,
                    expires_date=expires_date,
                )
            )
            logger.info("Invitation email queued for invitation %s", invitation.id)
        except Exception as email_error:
            # Log email error but don't fail the invitation
            logger.error(
                "Failed to send invitation email: %s", type(email_error).__name__
            )

        return {
            "success": True,
            "invitation_id": invitation.id,
            "email": request.email,
            "role": invite_role.value,
            "expires_at": invitation.expires_at.isoformat(),
            "message": f"Invitation sent to {request.email}",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error inviting user: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{target_user_id}")
async def remove_user(
    target_user_id: str,
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Remove a user from the department.

    Deactivates the user account (soft delete).

    Args:
        target_user_id: ID of user to remove

    Returns:
        Success message
    """
    _, user_id, department_id, admin_role = admin_info
    logger.info("Removing a user from department %s", department_id)

    try:
        # Get target user
        target_user = (
            db.query(User)
            .filter(User.id == target_user_id, User.department_id == department_id)
            .first()
        )

        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Cannot remove yourself
        if target_user_id == user_id:
            raise HTTPException(status_code=400, detail="Cannot remove yourself")

        # Only super_admin can remove admins
        if target_user.role == UserRole.ADMIN and admin_role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403, detail="Only super admins can remove admins"
            )

        # Cannot remove super_admins
        if target_user.role == UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403, detail="Cannot remove super admin users"
            )

        # Soft delete - deactivate user and prevent LTI launch reactivation.
        target_user.is_active = False
        target_user.lti_reauthorization_required = False
        target_user.deactivated_at = datetime.now(timezone.utc)
        db.commit()

        return {
            "success": True,
            "message": f"User {target_user.email} has been removed",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error removing user: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{target_user_id}/role")
async def update_user_role(
    target_user_id: str,
    request: UpdateUserRoleRequest,
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Update a user's role.

    Args:
        target_user_id: ID of user to update
        role: New role (faculty or admin)

    Returns:
        Updated user info
    """
    _, user_id, department_id, admin_role = admin_info
    logger.info("Updating a user role for department %s", department_id)

    try:
        # Validate role
        try:
            new_role = UserRole(request.role.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {request.role}")

        # Only super_admin can assign super_admin
        if new_role == UserRole.SUPER_ADMIN and admin_role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403, detail="Only super admins can promote to super admin"
            )

        # Get target user
        target_user = (
            db.query(User)
            .filter(
                User.id == target_user_id,
                User.department_id == department_id,
                User.is_active,
            )
            .first()
        )

        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Cannot change super_admin role unless you're super_admin
        if (
            target_user.role == UserRole.SUPER_ADMIN
            and admin_role != UserRole.SUPER_ADMIN
        ):
            raise HTTPException(
                status_code=403, detail="Cannot change super admin role"
            )

        # Update role
        old_role = target_user.role
        target_user.role = new_role
        db.commit()

        return {
            "success": True,
            "user_id": target_user.id,
            "email": target_user.email,
            "old_role": old_role.value if old_role else None,
            "new_role": new_role.value,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error updating user role: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Invitation Management Endpoints ====================


@router.get("/invitations")
async def list_invitations(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    List all invitations for the department.

    Args:
        status: Optional filter by status (pending, accepted, expired, revoked)

    Returns:
        List of invitations
    """
    _, user_id, department_id, role = admin_info
    logger.info(f"Listing invitations for department: {department_id}")

    try:
        query = db.query(UserInvitation).filter(
            UserInvitation.department_id == department_id
        )

        if status:
            try:
                status_enum = InvitationStatus(status.lower())
                query = query.filter(UserInvitation.status == status_enum)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

        invitations = query.order_by(UserInvitation.created_at.desc()).all()

        # Check for expired invitations
        now = datetime.utcnow()
        for invite in invitations:
            if invite.status == InvitationStatus.PENDING and invite.expires_at < now:
                invite.status = InvitationStatus.EXPIRED
                db.commit()

        # Batch-load inviter names to avoid N+1 queries
        inviter_ids = {inv.invited_by for inv in invitations if inv.invited_by}
        inviter_map = {}
        if inviter_ids:
            inviters = (
                db.query(User.id, User.name).filter(User.id.in_(inviter_ids)).all()
            )
            inviter_map = {u.id: u.name for u in inviters}

        invitation_list = []
        for invite in invitations:
            inviter_name = (
                inviter_map.get(invite.invited_by) if invite.invited_by else None
            )

            invitation_list.append(
                {
                    "id": invite.id,
                    "email": invite.email,
                    "role": invite.role.value if invite.role else "faculty",
                    "status": invite.status.value if invite.status else "pending",
                    "invited_by_name": inviter_name,
                    "created_at": (
                        invite.created_at.isoformat() if invite.created_at else None
                    ),
                    "expires_at": (
                        invite.expires_at.isoformat() if invite.expires_at else None
                    ),
                    "accepted_at": (
                        invite.accepted_at.isoformat() if invite.accepted_at else None
                    ),
                }
            )

        return {
            "success": True,
            "department_id": department_id,
            "invitations": invitation_list,
            "count": len(invitation_list),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing invitations: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(
    invitation_id: str,
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Revoke a pending invitation.

    Args:
        invitation_id: ID of invitation to revoke

    Returns:
        Success message
    """
    _, user_id, department_id, role = admin_info
    logger.info("Revoking an invitation for department %s", department_id)

    try:
        invitation = (
            db.query(UserInvitation)
            .filter(
                UserInvitation.id == invitation_id,
                UserInvitation.department_id == department_id,
            )
            .first()
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invitation not found")

        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot revoke invitation with status: {invitation.status.value}",
            )

        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "message": f"Invitation to {invitation.email} has been revoked",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error revoking invitation: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/invitations/{invitation_id}/resend")
async def resend_invitation(
    invitation_id: str,
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Resend an invitation email and extend expiration.

    Args:
        invitation_id: ID of invitation to resend

    Returns:
        Updated invitation details
    """
    _, user_id, department_id, role = admin_info
    logger.info("Resending an invitation for department %s", department_id)

    try:
        invitation = (
            db.query(UserInvitation)
            .filter(
                UserInvitation.id == invitation_id,
                UserInvitation.department_id == department_id,
            )
            .first()
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invitation not found")

        if invitation.status not in [
            InvitationStatus.PENDING,
            InvitationStatus.EXPIRED,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resend invitation with status: {invitation.status.value}",
            )

        # Reset to pending and extend expiration
        invitation.status = InvitationStatus.PENDING
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        invitation.token = secrets.token_urlsafe(48)  # New token for security
        db.commit()

        # Resend invitation email
        try:
            settings = get_settings()
            email_service = get_email_service()

            # Get department info
            department = (
                db.query(Department).filter(Department.id == department_id).first()
            )

            # Get inviter info (the admin resending)
            inviter = (
                db.query(User).filter(User.id == user_id).first()
                if user_id != "test-admin-123"
                else None
            )
            inviter_name = (
                inviter.name if inviter and inviter.name else "Your department admin"
            )
            inviter_email = inviter.email if inviter else "admin@example.com"

            # Build accept URL
            dashboard_url = (
                settings.dashboard_url
                if hasattr(settings, "dashboard_url")
                else "https://dashboard.example.com"
            )
            accept_url = f"{dashboard_url}/accept-invitation?token={invitation.token}"

            # Format expiration date
            expires_date = invitation.expires_at.strftime("%B %d, %Y at %I:%M %p UTC")

            # Send email asynchronously
            asyncio.create_task(
                email_service.send_faculty_invitation(
                    to_email=invitation.email,
                    department_name=(
                        department.name if department else "Your department"
                    ),
                    role=invitation.role.value if invitation.role else "faculty",
                    inviter_name=inviter_name,
                    inviter_email=inviter_email,
                    accept_url=accept_url,
                    expires_date=expires_date,
                )
            )
            logger.info("Invitation email resent for invitation %s", invitation.id)
        except Exception as email_error:
            # Log email error but don't fail the resend
            logger.error(
                "Failed to resend invitation email: %s", type(email_error).__name__
            )

        return {
            "success": True,
            "message": f"Invitation resent to {invitation.email}",
            "expires_at": invitation.expires_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error resending invitation: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Department Stats Endpoints ====================


@router.get("/stats")
async def get_department_stats(
    db: Session = Depends(get_db_dependency),
    admin_info: Tuple[Optional[APIKey], str, str, UserRole] = Depends(
        get_admin_api_key
    ),
):
    """
    Get department usage statistics.

    Returns:
        Department stats including user counts, scan counts, compliance data
    """
    _, user_id, department_id, role = admin_info
    logger.info(f"Getting stats for department: {department_id}")

    try:
        # User counts
        total_users = (
            db.query(func.count(User.id))
            .filter(User.department_id == department_id, User.is_active)
            .scalar()
            or 0
        )

        # Active users (logged in within 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        active_users = (
            db.query(func.count(User.id))
            .filter(
                User.department_id == department_id,
                User.is_active,
                User.last_login_at >= thirty_days_ago,
            )
            .scalar()
            or 0
        )

        compliance_stats = ScanService.get_department_stats(db, department_id)

        # Pending invitations
        pending_invitations = (
            db.query(func.count(UserInvitation.id))
            .filter(
                UserInvitation.department_id == department_id,
                UserInvitation.status == InvitationStatus.PENDING,
            )
            .scalar()
            or 0
        )

        # Department info
        department = db.query(Department).filter(Department.id == department_id).first()

        return {
            "success": True,
            "department_id": department_id,
            "department_name": department.name if department else None,
            "institution": department.institution if department else None,
            "tier": department.tier if department else None,
            "max_users": department.max_users if department else None,
            "stats": {
                "total_users": total_users,
                "active_users": active_users,
                "total_scans": compliance_stats["total_scans"],
                "historical_scan_count": compliance_stats["historical_scan_count"],
                "enrolled_document_count": compliance_stats["enrolled_document_count"],
                "verified_document_count": compliance_stats["verified_document_count"],
                "unverified_document_count": compliance_stats[
                    "unverified_document_count"
                ],
                "scans_this_month": compliance_stats["scans_this_month"],
                "avg_compliance_score": compliance_stats["avg_compliance_score"],
                "total_issues": compliance_stats["total_issues"],
                "pending_invitations": pending_invitations,
            },
        }

    except Exception as e:
        logger.error("Error getting department stats: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Accept Invitation Endpoint (Public) ====================
# This needs to be in a separate router without admin auth

accept_router = APIRouter(prefix="/auth", tags=["auth"])


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    token: str = Field(min_length=32, max_length=128)
    email: EmailStr
    name: Optional[str] = Field(default=None, max_length=255)
    picture_url: Optional[str] = Field(default=None, max_length=512)


def _invitation_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _invitation_expired(expires_at: datetime) -> bool:
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires_at < now


def _audit_invitation_acceptance(
    *,
    db: Session,
    invitation: UserInvitation,
    http_request: Request,
    outcome: str,
    status_value: AuditLogStatus,
    user_id: str | None = None,
) -> None:
    get_audit_service(db).log_action(
        action=AuditLogAction.USER_INVITE_ACCEPTED,
        status=status_value,
        user_id=user_id,
        department_id=invitation.department_id,
        resource_type="invitation",
        resource_id=invitation.id,
        request=http_request,
        details={"outcome": outcome, "purpose": invitation.purpose},
        commit=False,
    )


@accept_router.post("/accept-invitation")
async def accept_invitation(
    payload: AcceptInvitationRequest,
    http_request: Request,
    db: Session = Depends(get_db_dependency),
):
    """
    Accept an invitation and create user account.

    Args:
        token: Invitation token from email
        email: User's email (must match invitation)
        name: User's display name
        picture_url: Profile picture URL

    Returns:
        Created user info
    """
    logger.info("Accepting an invitation")

    try:
        normalized_email = str(payload.email).strip().lower()
        token_digest = _invitation_token_digest(payload.token)
        invitation = (
            db.query(UserInvitation)
            .filter(
                or_(
                    and_(
                        UserInvitation.purpose
                        == InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
                        UserInvitation.token == token_digest,
                    ),
                    and_(
                        UserInvitation.purpose
                        != InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
                        UserInvitation.token == payload.token,
                    ),
                )
            )
            .with_for_update()
            .first()
        )

        if not invitation:
            raise HTTPException(
                status_code=404,
                detail="Invitation is invalid or unavailable",
            )

        is_admin_handoff = (
            invitation.purpose == InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value
        )

        if invitation.status == InvitationStatus.REVOKED:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invitation could not be accepted"
                    if is_admin_handoff
                    else "This invitation is no longer available"
                ),
            )

        if invitation.status == InvitationStatus.ACCEPTED:
            if is_admin_handoff and invitation.email.lower() == normalized_email:
                accepted_user = (
                    db.query(User)
                    .filter(
                        func.lower(User.email) == normalized_email,
                        User.department_id == invitation.department_id,
                        User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]),
                        User.is_active.is_(True),
                    )
                    .first()
                )
                if accepted_user is not None:
                    return {
                        "success": True,
                        "outcome": "already_accepted",
                        "login_required": True,
                        "message": "Administrator setup is already complete. Log in to continue.",
                    }
            raise HTTPException(
                status_code=400 if is_admin_handoff else 409,
                detail=(
                    "Invitation could not be accepted"
                    if is_admin_handoff
                    else "This invitation has already been used"
                ),
            )

        if _invitation_expired(invitation.expires_at):
            invitation.status = InvitationStatus.EXPIRED
            _audit_invitation_acceptance(
                db=db,
                invitation=invitation,
                http_request=http_request,
                outcome="expired",
                status_value=AuditLogStatus.FAILURE,
            )
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invitation could not be accepted"
                    if is_admin_handoff
                    else "This invitation has expired"
                ),
            )

        if invitation.email.lower() != normalized_email:
            _audit_invitation_acceptance(
                db=db,
                invitation=invitation,
                http_request=http_request,
                outcome="email_mismatch",
                status_value=AuditLogStatus.FAILURE,
            )
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invitation could not be accepted"
                    if is_admin_handoff
                    else "Email does not match invitation"
                ),
            )

        email_blocked, _ = AccountDeletionService.is_email_blocked(
            db,
            normalized_email,
            commit_expired_cleanup=False,
        )
        if email_blocked:
            _audit_invitation_acceptance(
                db=db,
                invitation=invitation,
                http_request=http_request,
                outcome="email_blocked",
                status_value=AuditLogStatus.FAILURE,
            )
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invitation could not be accepted"
                    if is_admin_handoff
                    else "This email is not available for registration"
                ),
            )

        existing_user = (
            db.query(User).filter(func.lower(User.email) == normalized_email).first()
        )
        if existing_user:
            if existing_user.department_id != invitation.department_id:
                _audit_invitation_acceptance(
                    db=db,
                    invitation=invitation,
                    http_request=http_request,
                    outcome="email_bound_to_other_department",
                    status_value=AuditLogStatus.FAILURE,
                )
                db.commit()
                raise HTTPException(
                    status_code=400 if is_admin_handoff else 409,
                    detail=(
                        "Invitation could not be accepted"
                        if is_admin_handoff
                        else "This email is already assigned to another department"
                    ),
                )
            if not is_admin_handoff:
                raise HTTPException(
                    status_code=409, detail="A user with this email already exists"
                )
            user = existing_user
            user.role = UserRole.ADMIN
            user.is_active = True
            if payload.name:
                user.name = payload.name
            if payload.picture_url:
                user.picture_url = payload.picture_url
        else:
            user = User(
                email=normalized_email,
                name=payload.name,
                picture_url=payload.picture_url,
                department_id=invitation.department_id,
                role=invitation.role,
                is_active=True,
            )
            db.add(user)
            db.flush()

        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.utcnow()
        _audit_invitation_acceptance(
            db=db,
            invitation=invitation,
            http_request=http_request,
            outcome="accepted",
            status_value=AuditLogStatus.SUCCESS,
            user_id=user.id,
        )
        db.commit()
        db.refresh(user)

        return {
            "success": True,
            "outcome": "accepted",
            "user_id": user.id,
            "email": user.email,
            "role": user.role.value,
            "department_id": user.department_id,
            "login_required": True,
            "message": (
                "Administrator setup is complete. Log in to continue."
                if is_admin_handoff
                else "Account created successfully"
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Error accepting invitation: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Invitation could not be accepted")
