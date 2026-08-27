"""
User Management API Endpoints

Endpoints for:
- Department admin dashboard (view faculty, usage stats)
- Faculty invitation system (email invites with tokens)
- User role management (admin, faculty, student)

Author: Aelira Team
Created: January 11, 2026
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import secrets
import logging

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    User,
    UserRole,
    Department,
    UserInvitation,
    InvitationStatus,
    Scan,
    ScanResult,
)
from ..config.settings import get_tier_quota, get_settings
from ..mailer.email_service import get_email_service
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
    scans_this_month: int
    avg_compliance_score: float
    total_issues: int
    pending_invitations: int


# Auth dependency - require ADMIN or SUPER_ADMIN role
def get_admin_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
    db: Session = Depends(get_db_dependency),
) -> Tuple[APIKey, str, str, UserRole]:
    """Get API key and verify admin role."""
    from ..config.settings import get_settings
    from ..auth.auth_service import AuthService

    settings = get_settings()

    # Development mode - return mock admin (requires explicit opt-in)
    if settings.env == "development" and getattr(settings, "allow_mock_auth", False):
        if credentials:
            api_key = AuthService.validate_api_key(db, credentials.credentials)
            if api_key:
                # Get user role
                user = db.query(User).filter(User.id == api_key.user_id).first()
                if user and user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
                    return api_key, api_key.user_id, api_key.department_id, user.role

        # Mock admin for development
        logger.warning("Using mock admin credentials - development mode only")
        return None, "test-admin-123", "dev-dept-local", UserRole.ADMIN

    # Production - require valid API key and admin role
    if not credentials:
        raise HTTPException(status_code=401, detail="API key required")

    api_key = AuthService.validate_api_key(db, credentials.credentials)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")

    # Verify admin role
    user = db.query(User).filter(User.id == api_key.user_id).first()
    if not user or user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Admin access required")

    return api_key, api_key.user_id, api_key.department_id, user.role


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

        # Scan counts
        total_scans = (
            db.query(func.count(Scan.id))
            .filter(Scan.department_id == department_id)
            .scalar()
            or 0
        )

        # Scans this month
        first_of_month = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        scans_this_month = (
            db.query(func.count(Scan.id))
            .filter(
                Scan.department_id == department_id, Scan.created_at >= first_of_month
            )
            .scalar()
            or 0
        )

        # Average compliance score

        avg_score_result = (
            db.query(func.avg(ScanResult.compliance_score))
            .join(Scan, ScanResult.scan_id == Scan.id)
            .filter(Scan.department_id == department_id)
            .scalar()
        )
        avg_compliance_score = float(avg_score_result) if avg_score_result else 0.0

        # Total issues
        total_issues_result = (
            db.query(
                func.sum(
                    ScanResult.critical_issues
                    + ScanResult.high_issues
                    + ScanResult.medium_issues
                    + ScanResult.low_issues
                )
            )
            .join(Scan, ScanResult.scan_id == Scan.id)
            .filter(Scan.department_id == department_id)
            .scalar()
        )
        total_issues = int(total_issues_result) if total_issues_result else 0

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
                "total_scans": total_scans,
                "scans_this_month": scans_this_month,
                "avg_compliance_score": round(avg_compliance_score, 1),
                "total_issues": total_issues,
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
    token: str
    email: str
    name: Optional[str] = None
    picture_url: Optional[str] = None


@accept_router.post("/accept-invitation")
async def accept_invitation(
    request: AcceptInvitationRequest,
    db: Session = Depends(get_db_dependency),
):
    """
    Accept an invitation and create user account.

    Args:
        token: Invitation token from email
        google_id: Google OAuth ID
        email: User's email (must match invitation)
        name: User's name from Google
        picture_url: Profile picture URL

    Returns:
        Created user info
    """
    logger.info("Accepting an invitation")

    try:
        # Find invitation by token
        invitation = (
            db.query(UserInvitation)
            .filter(UserInvitation.token == request.token)
            .first()
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation token")

        # Check status
        if invitation.status == InvitationStatus.REVOKED:
            raise HTTPException(
                status_code=400, detail="This invitation has been revoked"
            )

        if invitation.status == InvitationStatus.ACCEPTED:
            raise HTTPException(
                status_code=400, detail="This invitation has already been used"
            )

        # Check expiration
        if invitation.expires_at < datetime.utcnow():
            invitation.status = InvitationStatus.EXPIRED
            db.commit()
            raise HTTPException(status_code=400, detail="This invitation has expired")

        # Verify email matches
        if invitation.email.lower() != request.email.lower():
            raise HTTPException(
                status_code=400, detail="Email does not match invitation"
            )

        # Check if user already exists
        existing_user = db.query(User).filter(User.email == request.email).first()
        if existing_user:
            raise HTTPException(
                status_code=400, detail="A user with this email already exists"
            )

        # Create user (google_id is set later via OAuth flow, not from request)
        user = User(
            email=request.email,
            name=request.name,
            picture_url=request.picture_url,
            department_id=invitation.department_id,
            role=invitation.role,
            is_active=True,
        )

        db.add(user)

        # Mark invitation as accepted
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.utcnow()

        db.commit()
        db.refresh(user)

        return {
            "success": True,
            "user_id": user.id,
            "email": user.email,
            "role": user.role.value,
            "department_id": user.department_id,
            "message": "Account created successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error accepting invitation: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))
