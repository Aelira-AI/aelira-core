"""
Audit Service for logging security-sensitive actions.

Provides centralized audit logging for compliance and security monitoring.
All authentication, authorization, and sensitive operations should be logged.
"""

import logging
from typing import Dict, Any, Optional
import uuid

from sqlalchemy.orm import Session
from fastapi import Request

from ..db.models import AuditLog, AuditLogAction, AuditLogStatus

logger = logging.getLogger(__name__)


class AuditService:
    """
    Service for logging security-sensitive actions.

    Usage:
        audit = AuditService(db)
        audit.log_action(
            action=AuditLogAction.LOGIN_SUCCESS,
            user_id="user-123",
            request=request,
            details={"method": "magic_link"}
        )
    """

    def __init__(self, db: Session):
        """
        Initialize audit service.

        Args:
            db: Database session
        """
        self.db = db

    def log_action(
        self,
        action: AuditLogAction,
        status: AuditLogStatus = AuditLogStatus.SUCCESS,
        user_id: Optional[str] = None,
        department_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """
        Log an audit event.

        Args:
            action: The action being logged (from AuditLogAction enum)
            status: Success or failure status
            user_id: ID of user performing the action (if authenticated)
            department_id: ID of department context (if applicable)
            resource_type: Type of resource affected (user, api_key, session, etc.)
            resource_id: ID of the affected resource
            ip_address: Client IP address (extracted from request if provided)
            user_agent: Client user agent (extracted from request if provided)
            details: Additional action-specific details as JSON
            request: FastAPI request object (for extracting IP and user agent)

        Returns:
            The created AuditLog record
        """
        # Extract IP and user agent from request if not provided
        if request:
            if not ip_address:
                ip_address = self._get_client_ip(request)
            if not user_agent:
                user_agent = request.headers.get("user-agent", "")[:512]

        # Create audit log entry
        audit_log = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            department_id=department_id,
            action=action.value if isinstance(action, AuditLogAction) else action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
            status=status.value if isinstance(status, AuditLogStatus) else status,
        )

        try:
            self.db.add(audit_log)
            self.db.commit()
            self.db.refresh(audit_log)

            logger.debug(
                f"Audit log created: action={action}, user_id={user_id}, status={status}"
            )

            return audit_log

        except Exception as e:
            logger.error(f"Failed to create audit log: {e}")
            self.db.rollback()
            # Don't raise - audit logging should not break the main flow
            return None

    def log_login_success(
        self,
        user_id: str,
        department_id: Optional[str] = None,
        auth_method: str = "magic_link",
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log successful login."""
        return self.log_action(
            action=AuditLogAction.LOGIN_SUCCESS,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="session",
            details={"auth_method": auth_method},
            request=request,
        )

    def log_login_failure(
        self,
        email: Optional[str] = None,
        reason: str = "invalid_credentials",
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log failed login attempt."""
        return self.log_action(
            action=AuditLogAction.LOGIN_FAILURE,
            status=AuditLogStatus.FAILURE,
            details={"email": email, "reason": reason},
            request=request,
        )

    def log_logout(
        self,
        user_id: str,
        department_id: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log user logout."""
        return self.log_action(
            action=AuditLogAction.LOGOUT,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="session",
            request=request,
        )

    def log_api_key_create(
        self,
        user_id: str,
        department_id: str,
        api_key_id: str,
        key_name: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log API key creation."""
        return self.log_action(
            action=AuditLogAction.API_KEY_CREATE,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="api_key",
            resource_id=api_key_id,
            details={"key_name": key_name},
            request=request,
        )

    def log_api_key_revoke(
        self,
        user_id: str,
        department_id: str,
        api_key_id: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log API key revocation."""
        return self.log_action(
            action=AuditLogAction.API_KEY_REVOKE,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="api_key",
            resource_id=api_key_id,
            request=request,
        )

    def log_session_revoke(
        self,
        user_id: str,
        session_id: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log session revocation."""
        return self.log_action(
            action=AuditLogAction.SESSION_REVOKE,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            resource_type="session",
            resource_id=session_id,
            request=request,
        )

    def log_session_revoke_all(
        self,
        user_id: str,
        sessions_revoked: int,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log revoking all other sessions."""
        return self.log_action(
            action=AuditLogAction.SESSION_REVOKE_ALL,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            resource_type="session",
            details={"sessions_revoked": sessions_revoked},
            request=request,
        )

    def log_user_invite(
        self,
        inviter_user_id: str,
        department_id: str,
        invitee_email: str,
        invitation_id: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log user invitation sent."""
        return self.log_action(
            action=AuditLogAction.USER_INVITE_SENT,
            status=AuditLogStatus.SUCCESS,
            user_id=inviter_user_id,
            department_id=department_id,
            resource_type="invitation",
            resource_id=invitation_id,
            details={"invitee_email": invitee_email},
            request=request,
        )

    def log_invite_accepted(
        self,
        user_id: str,
        department_id: str,
        invitation_id: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log user accepting an invitation."""
        return self.log_action(
            action=AuditLogAction.USER_INVITE_ACCEPTED,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="invitation",
            resource_id=invitation_id,
            request=request,
        )

    def log_role_change(
        self,
        admin_user_id: str,
        target_user_id: str,
        department_id: str,
        old_role: str,
        new_role: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log user role change."""
        return self.log_action(
            action=AuditLogAction.USER_ROLE_CHANGE,
            status=AuditLogStatus.SUCCESS,
            user_id=admin_user_id,
            department_id=department_id,
            resource_type="user",
            resource_id=target_user_id,
            details={"old_role": old_role, "new_role": new_role},
            request=request,
        )

    def log_cloud_connect(
        self,
        user_id: str,
        department_id: str,
        provider: str,
        credential_id: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log cloud integration connection."""
        return self.log_action(
            action=AuditLogAction.CLOUD_CONNECT,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="cloud_credential",
            resource_id=credential_id,
            details={"provider": provider},
            request=request,
        )

    def log_cloud_disconnect(
        self,
        user_id: str,
        department_id: str,
        provider: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log cloud integration disconnection."""
        return self.log_action(
            action=AuditLogAction.CLOUD_DISCONNECT,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="cloud_credential",
            details={"provider": provider},
            request=request,
        )

    def log_profile_update(
        self,
        user_id: str,
        fields_updated: list,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log user profile update."""
        return self.log_action(
            action=AuditLogAction.USER_PROFILE_UPDATE,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            resource_type="user",
            resource_id=user_id,
            details={"fields_updated": fields_updated},
            request=request,
        )

    def log_remediation_complete(
        self,
        user_id: str,
        department_id: str,
        scan_id: str,
        file_type: str,
        use_ai: bool,
        total_issues: int,
        fixed_count: int,
        manual_count: int,
        original_score: float,
        remediated_score: float,
        improvement: float,
        duration_seconds: float,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log successful document remediation."""
        return self.log_action(
            action=AuditLogAction.REMEDIATION_COMPLETE,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="scan",
            resource_id=scan_id,
            details={
                "scan_id": scan_id,
                "file_type": file_type,
                "use_ai": use_ai,
                "total_issues": total_issues,
                "fixed_count": fixed_count,
                "manual_count": manual_count,
                "original_score": original_score,
                "remediated_score": remediated_score,
                "improvement": improvement,
                "duration_seconds": duration_seconds,
            },
            request=request,
        )

    def log_remediation_failed(
        self,
        user_id: str,
        department_id: str,
        scan_id: str,
        file_type: str,
        use_ai: bool,
        error: str,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log failed document remediation."""
        return self.log_action(
            action=AuditLogAction.REMEDIATION_FAILED,
            status=AuditLogStatus.FAILURE,
            user_id=user_id,
            department_id=department_id,
            resource_type="scan",
            resource_id=scan_id,
            details={
                "scan_id": scan_id,
                "file_type": file_type,
                "use_ai": use_ai,
                "error": error[:500],
            },
            request=request,
        )

    def log_remediation_download(
        self,
        user_id: str,
        department_id: str,
        scan_id: str,
        file_type: str,
        format: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Log remediated file download."""
        return self.log_action(
            action=AuditLogAction.REMEDIATION_DOWNLOAD,
            status=AuditLogStatus.SUCCESS,
            user_id=user_id,
            department_id=department_id,
            resource_type="scan",
            resource_id=scan_id,
            details={
                "scan_id": scan_id,
                "file_type": file_type,
                "format": format,
            },
            request=request,
        )

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, handling proxies."""
        # Check for forwarded IP (behind load balancer/proxy)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Take the first IP in the chain (original client)
            return forwarded.split(",")[0].strip()

        # Check for real IP header (Nginx)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"


def get_audit_service(db: Session) -> AuditService:
    """Factory function to get an audit service instance."""
    return AuditService(db)
