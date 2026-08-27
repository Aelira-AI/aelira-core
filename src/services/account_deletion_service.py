"""
Account Deletion Service

Handles account deactivation, GDPR deletion, re-registration blocking,
and data export.

Flow:
- Deactivation: Soft delete + 90-day re-registration cooldown
- GDPR Deletion: Email confirmation code → 30-day grace → PII scrub + permanent block
"""

import hashlib
import secrets
import logging
import uuid as uuid_mod
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Any

from sqlalchemy.orm import Session as DBSession

from ..db.models import (
    User,
    Department,
    APIKey,
    UserSession,
    MagicLink,
    Scan,
    ScanResult,
    DeletedEmail,
    AuditLog,
    AuditLogAction,
)
from ..auth.session_service import get_session_service
from ..security.audit_service import AuditService
from ..mailer.email_service import get_email_service
from .remediation_artifact_service import RemediationArtifactService

logger = logging.getLogger(__name__)

# Cooldown periods
DEACTIVATION_COOLDOWN_DAYS = 90
GDPR_GRACE_PERIOD_DAYS = 30
CONFIRMATION_CODE_EXPIRY_MINUTES = 15


class AccountDeletionService:
    """Core service for account lifecycle management."""

    @staticmethod
    def hash_email(email: str) -> str:
        """SHA-256 hash of lowercase email for blocklist lookup."""
        return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()

    @staticmethod
    def is_email_blocked(
        db: DBSession,
        email: str,
        *,
        commit_expired_cleanup: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an email is blocked from re-registration.

        Locked workflows can set ``commit_expired_cleanup=False`` so removing an
        expired cooldown remains part of their surrounding transaction.

        Returns:
            (is_blocked, reason_message)
        """
        email_hash = AccountDeletionService.hash_email(email)
        record = (
            db.query(DeletedEmail).filter(DeletedEmail.email_hash == email_hash).first()
        )

        if not record:
            return False, None

        # GDPR deletion = permanent block (cooldown_until is NULL)
        if record.cooldown_until is None:
            return True, "This email address is not available for registration."

        # Check if cooldown has expired
        now = datetime.now(timezone.utc)
        if now < record.cooldown_until:
            remaining = (record.cooldown_until - now).days
            return (
                True,
                f"Account re-registration is temporarily blocked. Please try again in {remaining} days.",
            )

        # Cooldown expired — allow re-registration, remove block
        db.delete(record)
        if commit_expired_cleanup:
            db.commit()
        else:
            db.flush()
        return False, None

    def deactivate_account(
        self,
        db: DBSession,
        user: User,
        reason: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Soft-delete an account. Revokes sessions/keys, blocks re-registration
        for 90 days.

        Returns:
            Status dict with message
        """
        # Aelira Core ships no billing integration, so subscription status never
        # blocks deactivation. An operator wiring up a billing provider should
        # add their own pre-deactivation check here.
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )

        # 1. Deactivate user
        user.is_active = False
        user.deactivated_at = datetime.now(timezone.utc)

        # 2. Revoke all sessions
        session_service = get_session_service()
        revoked_count = session_service.revoke_all_sessions(db, user.id)

        # 3. Deactivate all API keys
        keys = (
            db.query(APIKey)
            .filter(
                APIKey.user_id == user.id,
                APIKey.is_active == True,  # noqa: E712
            )
            .all()
        )
        for key in keys:
            key.is_active = False

        # 4. Store email hash with 90-day cooldown
        email_hash = self.hash_email(user.email)
        existing = (
            db.query(DeletedEmail).filter(DeletedEmail.email_hash == email_hash).first()
        )
        if not existing:
            deleted_record = DeletedEmail(
                id=str(uuid_mod.uuid4()),
                email_hash=email_hash,
                deletion_type="deactivated",
                cooldown_until=datetime.now(timezone.utc)
                + timedelta(days=DEACTIVATION_COOLDOWN_DAYS),
                previous_tier=department.tier if department else None,
                reason=reason,
            )
            db.add(deleted_record)

        # 5. Deactivate department if individual
        if department and department.tier.startswith("individual"):
            department.is_active = False

        db.commit()

        # 6. Audit log
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DEACTIVATE,
            user_id=user.id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "reason": reason,
                "sessions_revoked": revoked_count,
                "keys_deactivated": len(keys),
            },
        )

        logger.info(
            "Account deactivated: user=%s, sessions_revoked=%s, keys_deactivated=%s",
            user.id,
            revoked_count,
            len(keys),
        )

        return {
            "message": "Account deactivated successfully.",
            "sessions_revoked": revoked_count,
            "keys_deactivated": len(keys),
        }

    def request_deletion_code(
        self,
        db: DBSession,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Generate a 6-digit confirmation code and send it to the user's email.

        Returns:
            Dict with message and code_expires_at
        """
        # Generate 6-digit code
        code = f"{secrets.randbelow(1000000):06d}"

        # Hash with bcrypt before storing
        code_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        # Store on user
        user.deletion_confirmation_code_hash = code_hash
        user.deletion_confirmation_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=CONFIRMATION_CODE_EXPIRY_MINUTES
        )
        db.commit()

        # Send email with code (fire-and-forget async)
        try:
            email_service = get_email_service()
            if email_service.is_configured():
                from ..services.email_templates import render_deletion_code_email
                import asyncio

                subject = "Aelira Account Deletion - Confirmation Code"
                html_body, text_body = render_deletion_code_email(
                    name=user.name or "there",
                    code=code,
                )
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                loop.create_task(
                    email_service.send_email(
                        to_emails=[user.email],
                        subject=subject,
                        html_content=html_body,
                        text_content=text_body,
                    )
                )
                logger.info("Deletion confirmation email queued for user %s", user.id)
        except Exception as e:
            logger.error(
                "Failed to send deletion confirmation email for user %s: %s",
                user.id,
                type(e).__name__,
            )
            # Don't fail the request if email fails — code is still stored

        # Audit log
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DELETION_REQUESTED,
            user_id=user.id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        expires_at = user.deletion_confirmation_expires_at.isoformat()
        logger.info(f"Deletion confirmation code sent to user {user.id}")

        return {
            "message": "Confirmation code sent to your email.",
            "code_expires_at": expires_at,
        }

    def confirm_deletion(
        self,
        db: DBSession,
        user: User,
        code: str,
        reason: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Verify confirmation code and schedule account deletion in 30 days.
        Account is deactivated immediately.

        Returns:
            Dict with message and scheduled_for date
        """
        # Verify code hash exists
        if not user.deletion_confirmation_code_hash:
            raise ValueError(
                "No deletion request pending. Please request a new confirmation code."
            )

        # Check expiry
        if (
            user.deletion_confirmation_expires_at
            and datetime.now(timezone.utc) > user.deletion_confirmation_expires_at
        ):
            # Clear expired code
            user.deletion_confirmation_code_hash = None
            user.deletion_confirmation_expires_at = None
            db.commit()
            raise ValueError("Confirmation code has expired. Please request a new one.")

        # Verify code
        if not bcrypt.checkpw(
            code.encode("utf-8"),
            user.deletion_confirmation_code_hash.encode("utf-8"),
        ):
            raise ValueError("Invalid confirmation code.")

        # Schedule deletion
        now = datetime.now(timezone.utc)
        scheduled_for = now + timedelta(days=GDPR_GRACE_PERIOD_DAYS)

        user.deletion_requested_at = now
        user.deletion_scheduled_for = scheduled_for
        user.deletion_confirmation_code_hash = None
        user.deletion_confirmation_expires_at = None

        # Immediately deactivate
        user.is_active = False
        user.deactivated_at = now

        # Revoke all sessions
        session_service = get_session_service()
        session_service.revoke_all_sessions(db, user.id)

        # Deactivate API keys
        db.query(APIKey).filter(
            APIKey.user_id == user.id,
            APIKey.is_active == True,  # noqa: E712
        ).update({"is_active": False})

        # If this deployment integrates a billing provider, mark the
        # subscription cancelled here before scrubbing the account. Aelira
        # Core ships no billing provider integration by default — an
        # operator wiring one up should cancel it before this point runs.
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )
        if department and department.subscription_status == "active":
            department.subscription_status = "cancelled"

        # Store email hash with permanent block
        email_hash = self.hash_email(user.email)
        existing = (
            db.query(DeletedEmail).filter(DeletedEmail.email_hash == email_hash).first()
        )
        if existing:
            existing.deletion_type = "gdpr_deleted"
            existing.cooldown_until = None  # Permanent block
            existing.reason = reason
        else:
            deleted_record = DeletedEmail(
                id=str(uuid_mod.uuid4()),
                email_hash=email_hash,
                deletion_type="gdpr_deleted",
                cooldown_until=None,  # Permanent
                previous_tier=department.tier if department else None,
                reason=reason,
            )
            db.add(deleted_record)

        # Deactivate department if individual
        if department and department.tier.startswith("individual"):
            department.is_active = False

        db.commit()

        # Audit log
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DELETION_CONFIRMED,
            user_id=user.id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "scheduled_for": scheduled_for.isoformat(),
                "reason": reason,
            },
        )

        # Send scheduled email (fire-and-forget async)
        try:
            email_service = get_email_service()
            if email_service.is_configured():
                from ..services.email_templates import render_deletion_scheduled_email
                import asyncio

                subject = "Your Aelira Account Deletion is Scheduled"
                html_body, text_body = render_deletion_scheduled_email(
                    name=user.name or "there",
                    scheduled_date=scheduled_for.strftime("%B %d, %Y"),
                )
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                loop.create_task(
                    email_service.send_email(
                        to_emails=[user.email],
                        subject=subject,
                        html_content=html_body,
                        text_content=text_body,
                    )
                )
                logger.info("Deletion scheduled email queued for user %s", user.id)
        except Exception as e:
            logger.error(
                "Failed to send deletion scheduled email for user %s: %s",
                user.id,
                type(e).__name__,
            )

        logger.info(
            f"Account deletion confirmed: user={user.id}, scheduled_for={scheduled_for.isoformat()}"
        )

        return {
            "message": "Account deletion scheduled. Your data will be permanently removed after 30 days.",
            "scheduled_for": scheduled_for.isoformat(),
        }

    def cancel_pending_deletion(
        self,
        db: DBSession,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Cancel a scheduled deletion within the 30-day grace period.
        Reactivates the account.
        """
        if not user.deletion_scheduled_for:
            raise ValueError("No pending deletion to cancel.")

        if datetime.now(timezone.utc) > user.deletion_scheduled_for:
            raise ValueError(
                "Deletion grace period has expired and cannot be cancelled."
            )

        # Clear deletion fields
        user.deletion_requested_at = None
        user.deletion_scheduled_for = None
        user.is_active = True
        user.deactivated_at = None

        # Remove email hash block
        email_hash = self.hash_email(user.email)
        deleted_record = (
            db.query(DeletedEmail).filter(DeletedEmail.email_hash == email_hash).first()
        )
        if deleted_record:
            db.delete(deleted_record)

        # Reactivate department if individual
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )
        if department and department.tier.startswith("individual"):
            department.is_active = True

        db.commit()

        # Audit log
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DELETION_CANCELLED,
            user_id=user.id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        logger.info(f"Account deletion cancelled for user {user.id}")
        return {
            "message": "Account deletion cancelled. Your account has been reactivated."
        }

    def get_deletion_status(self, db: DBSession, user: User) -> dict:
        """Get current deletion status for a user."""
        if not user.deletion_scheduled_for:
            return {
                "deletion_pending": False,
                "scheduled_for": None,
                "days_remaining": None,
                "can_cancel": False,
            }

        now = datetime.now(timezone.utc)
        remaining = (user.deletion_scheduled_for - now).days
        can_cancel = now < user.deletion_scheduled_for

        return {
            "deletion_pending": True,
            "scheduled_for": user.deletion_scheduled_for.isoformat(),
            "days_remaining": max(0, remaining),
            "can_cancel": can_cancel,
        }

    def execute_scheduled_deletion(self, db: DBSession, user_id: str) -> bool:
        """
        Hard delete: called by background job after 30-day grace period.
        Scrubs PII while retaining email hash for re-registration blocking.

        Returns:
            True if deletion executed, False if user not found or not scheduled
        """
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False

        if not user.deletion_scheduled_for:
            return False

        if datetime.now(timezone.utc) < user.deletion_scheduled_for:
            return False

        original_email = user.email
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )

        # Account deletion is explicit destructive authority for managed outputs.
        artifact_count = 0
        if department is not None:
            artifact_cleanup = (
                RemediationArtifactService.from_settings().cleanup_for_user(
                    db,
                    department_id=department.id,
                    user_id=user_id,
                    destructive_actor_ref="account_deletion",
                )
            )
            artifact_count = artifact_cleanup.count

        # 1. Delete scan results
        scans = db.query(Scan).filter(Scan.user_id == user_id).all()
        for scan in scans:
            # Delete associated scan results
            db.query(ScanResult).filter(ScanResult.scan_id == scan.id).delete()
        # Delete scans
        db.query(Scan).filter(Scan.user_id == user_id).delete()

        # 2. Delete API keys
        db.query(APIKey).filter(APIKey.user_id == user_id).delete()

        # 3. Delete sessions
        db.query(UserSession).filter(UserSession.user_id == user_id).delete()

        # 4. Delete magic links
        db.query(MagicLink).filter(MagicLink.email == original_email.lower()).delete()

        # 5. Ensure email hash is in deleted_emails with permanent block
        email_hash = self.hash_email(original_email)
        existing = (
            db.query(DeletedEmail).filter(DeletedEmail.email_hash == email_hash).first()
        )
        if not existing:
            deleted_record = DeletedEmail(
                id=str(uuid_mod.uuid4()),
                email_hash=email_hash,
                deletion_type="gdpr_deleted",
                cooldown_until=None,
                previous_tier=department.tier if department else None,
            )
            db.add(deleted_record)

        # 6. Scrub PII from user record (keep row for audit log FK integrity)
        user.email = f"deleted-{user.id}@deleted.invalid"
        user.name = "[Deleted User]"
        user.google_id = None
        user.microsoft_id = None
        user.picture_url = None
        user.email_verified = False
        user.email_marketing = False
        user.email_marketing_confirmed = False
        user.email_marketing_confirmation_token = None
        user.email_marketing_confirmed_at = None
        user.deletion_confirmation_code_hash = None
        user.deletion_confirmation_expires_at = None

        try:
            # Commit artifact rows and departing-user parents atomically.
            db.commit()
        except Exception:
            db.rollback()
            raise

        # 7. Audit log
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DELETION_EXECUTED,
            user_id=user_id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user_id,
            details={
                "email_hash": email_hash[:12] + "...",
                "artifact_actor_ref": "account_deletion",
                "artifacts_deleted": artifact_count,
            },
        )

        logger.info("Account deletion executed: user=%s", user_id)
        return True

    def export_user_data(self, db: DBSession, user: User) -> dict:
        """
        Export all user data as a JSON-serializable dict (GDPR Article 20).
        """
        department = (
            db.query(Department).filter(Department.id == user.department_id).first()
        )

        # Get scans
        scans = db.query(Scan).filter(Scan.user_id == user.id).all()
        scan_data = []
        for scan in scans:
            result = db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
            scan_entry: dict[str, Any] = {
                "id": scan.id,
                "scan_type": scan.scan_type.value if scan.scan_type else None,
                "status": scan.status.value if scan.status else None,
                "file_name": scan.file_name,
                "file_size_bytes": scan.file_size_bytes,
                "pages": scan.pages,
                "processing_time_ms": scan.processing_time_ms,
                "created_at": scan.created_at.isoformat() if scan.created_at else None,
                "completed_at": (
                    scan.completed_at.isoformat() if scan.completed_at else None
                ),
            }
            if result:
                scan_entry["result"] = {
                    "compliance_score": result.compliance_score,
                    "wcag_level": result.wcag_level,
                    "critical_issues": result.critical_issues,
                    "high_issues": result.high_issues,
                    "medium_issues": result.medium_issues,
                    "low_issues": result.low_issues,
                }
            scan_data.append(scan_entry)

        # Get API key metadata (no secrets)
        api_keys = db.query(APIKey).filter(APIKey.user_id == user.id).all()
        key_data = [
            {
                "id": key.id,
                "name": key.name,
                "key_prefix": key.prefix if hasattr(key, "prefix") else key.key_prefix,
                "is_active": key.is_active,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "last_used_at": (
                    key.last_used_at.isoformat() if key.last_used_at else None
                ),
            }
            for key in api_keys
        ]

        # Get audit logs
        audit_logs = (
            db.query(AuditLog)
            .filter(AuditLog.user_id == user.id)
            .order_by(AuditLog.created_at.desc())
            .limit(500)
            .all()
        )
        audit_data = [
            {
                "action": log.action,
                "status": log.status,
                "resource_type": log.resource_type,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "ip_address": log.ip_address,
            }
            for log in audit_logs
        ]

        # Log the export
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.ACCOUNT_DATA_EXPORT,
            user_id=user.id,
            department_id=user.department_id,
            resource_type="user",
            resource_id=user.id,
        )

        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value if user.role else None,
                "auth_provider": (
                    user.auth_provider.value if user.auth_provider else None
                ),
                "email_verified": user.email_verified,
                "timezone": user.timezone,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login_at": (
                    user.last_login_at.isoformat() if user.last_login_at else None
                ),
            },
            "department": {
                "id": department.id if department else None,
                "name": department.name if department else None,
                "institution": department.institution if department else None,
                "tier": department.tier if department else None,
            },
            "preferences": {
                "email_scan_complete": user.email_scan_complete,
                "email_remediation_complete": user.email_remediation_complete,
                "email_critical_alerts": user.email_critical_alerts,
                "email_weekly_summary": user.email_weekly_summary,
                "email_marketing": user.email_marketing,
                "weekly_summary_day": user.weekly_summary_day,
                "weekly_summary_hour": user.weekly_summary_hour,
            },
            "scans": scan_data,
            "api_keys": key_data,
            "audit_logs": audit_data,
        }


# Singleton
_account_deletion_service: Optional[AccountDeletionService] = None


def get_account_deletion_service() -> AccountDeletionService:
    """Get or create singleton AccountDeletionService."""
    global _account_deletion_service
    if _account_deletion_service is None:
        _account_deletion_service = AccountDeletionService()
    return _account_deletion_service
