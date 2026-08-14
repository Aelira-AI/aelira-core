"""
Session Service - User Session Management

Provides functions for:
- Session creation after successful authentication
- Session validation
- Session refresh
- Session revocation (logout)
- Magic link token management

Security:
- Sessions are tracked in database for revocation
- Refresh tokens are hashed before storage
- Sessions can be revoked on logout or security events
"""

import secrets
import bcrypt
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session as DBSession

from ..db.models import User, UserSession, MagicLink, Department, AuthProvider
from .jwt_service import get_jwt_service
from .auth_service import AuthService
from ..config.settings import get_settings
from ..mailer.email_service import get_email_service

logger = logging.getLogger(__name__)


class SessionService:
    """Service for user session management"""

    def __init__(self):
        self.settings = get_settings()
        self.jwt_service = get_jwt_service()

    def create_session(
        self,
        db: DBSession,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, datetime, datetime]:
        """
        Create a new session for a user (after successful login)

        Args:
            db: Database session
            user: Authenticated User object
            ip_address: Client IP address
            user_agent: Client user agent

        Returns:
            Tuple of (access_token, refresh_token, access_expires_at, refresh_expires_at)
        """
        # Create access token
        access_token, access_jti, access_expires_at = (
            self.jwt_service.create_access_token(
                user_id=user.id,
                department_id=user.department_id,
                email=user.email,
                role=user.role.value if user.role else "faculty",
            )
        )

        # Create refresh token
        refresh_token, raw_refresh, refresh_expires_at = (
            self.jwt_service.create_refresh_token(user_id=user.id)
        )

        # Hash refresh token for storage
        refresh_token_hash = bcrypt.hashpw(
            raw_refresh.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        # Create session record
        session = UserSession(
            user_id=user.id,
            refresh_token_hash=refresh_token_hash,
            access_token_jti=access_jti,
            expires_at=refresh_expires_at,
            ip_address=ip_address,
            user_agent=user_agent[:512] if user_agent else None,
        )
        db.add(session)

        # Update user's last login
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"Created session {session.id} for user {user.id}")
        return access_token, refresh_token, access_expires_at, refresh_expires_at

    def validate_session(
        self, db: DBSession, access_token: str
    ) -> Optional[Tuple[User, dict]]:
        """
        Validate an access token and return the user

        Args:
            db: Database session
            access_token: JWT access token

        Returns:
            Tuple of (User, token_payload) if valid, None otherwise
        """
        # Verify the access token
        payload = self.jwt_service.verify_access_token(access_token)
        if not payload:
            return None

        user_id = payload.get("sub")
        access_jti = payload.get("jti")

        # Check if session exists and is not revoked
        session = (
            db.query(UserSession)
            .filter(UserSession.user_id == user_id)
            .filter(UserSession.access_token_jti == access_jti)
            .filter(UserSession.revoked_at.is_(None))
            .filter(UserSession.expires_at > datetime.now(timezone.utc))
            .first()
        )

        if not session:
            logger.debug(
                f"No valid session found for user {user_id} with jti {access_jti}"
            )
            return None

        # Get the user
        user = (
            db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
        )
        if not user:
            logger.warning(f"User {user_id} not found or inactive")
            return None

        # Update last used time
        session.last_used_at = datetime.now(timezone.utc)
        db.commit()

        return user, payload

    def refresh_session(
        self,
        db: DBSession,
        refresh_token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[Tuple[str, str, datetime, datetime]]:
        """
        Refresh a session using the refresh token

        Args:
            db: Database session
            refresh_token: JWT refresh token
            ip_address: Client IP (for new session record)
            user_agent: Client user agent

        Returns:
            Tuple of (new_access_token, new_refresh_token, access_expires_at, refresh_expires_at)
            or None if invalid
        """
        # Verify refresh token
        payload = self.jwt_service.verify_refresh_token(refresh_token)
        if not payload:
            return None

        user_id = payload.get("sub")
        raw_token = payload.get("token")

        # Find the session with matching refresh token
        sessions = (
            db.query(UserSession)
            .filter(UserSession.user_id == user_id)
            .filter(UserSession.revoked_at.is_(None))
            .filter(UserSession.expires_at > datetime.now(timezone.utc))
            .all()
        )

        valid_session = None
        for session in sessions:
            try:
                if bcrypt.checkpw(
                    raw_token.encode("utf-8"),
                    session.refresh_token_hash.encode("utf-8"),
                ):
                    valid_session = session
                    break
            except Exception:
                continue

        if not valid_session:
            logger.warning(f"No valid session found for refresh token (user {user_id})")
            return None

        # Get the user
        user = (
            db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
        )
        if not user:
            logger.warning(f"User {user_id} not found or inactive during refresh")
            return None

        # Revoke old session
        valid_session.revoked_at = datetime.now(timezone.utc)

        # Create new session (token rotation)
        access_token, refresh_token_new, access_exp, refresh_exp = self.create_session(
            db, user, ip_address, user_agent
        )

        logger.info(f"Refreshed session for user {user_id}")
        return access_token, refresh_token_new, access_exp, refresh_exp

    def revoke_session(
        self, db: DBSession, user_id: str, access_token: Optional[str] = None
    ) -> bool:
        """
        Revoke a user's session (logout)

        Args:
            db: Database session
            user_id: User ID
            access_token: Optional - if provided, only revoke this specific session

        Returns:
            True if session(s) revoked, False otherwise
        """
        query = (
            db.query(UserSession)
            .filter(UserSession.user_id == user_id)
            .filter(UserSession.revoked_at.is_(None))
        )

        if access_token:
            # Revoke specific session
            payload = self.jwt_service.decode_token(access_token, verify_exp=False)
            if payload and payload.get("jti"):
                query = query.filter(UserSession.access_token_jti == payload.get("jti"))

        sessions = query.all()
        if not sessions:
            return False

        for session in sessions:
            session.revoked_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(f"Revoked {len(sessions)} session(s) for user {user_id}")
        return True

    def revoke_all_sessions(self, db: DBSession, user_id: str) -> int:
        """
        Revoke all sessions for a user (security event, password change, etc.)

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Number of sessions revoked
        """
        sessions = (
            db.query(UserSession)
            .filter(UserSession.user_id == user_id)
            .filter(UserSession.revoked_at.is_(None))
            .all()
        )

        for session in sessions:
            session.revoked_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(f"Revoked all {len(sessions)} sessions for user {user_id}")
        return len(sessions)

    # =========================================================================
    # Magic Link Management
    # =========================================================================

    def create_magic_link(
        self,
        db: DBSession,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        signup_name: Optional[str] = None,
        signup_institution: Optional[str] = None,
    ) -> str:
        """
        Create a magic link token for email authentication

        Args:
            db: Database session
            email: Email address
            ip_address: Client IP
            user_agent: Client user agent
            signup_name: Name provided during signup (new users only)
            signup_institution: Institution provided during signup (new users only)

        Returns:
            The magic link token (to be sent via email)
        """
        # Generate secure token
        raw_token = secrets.token_urlsafe(32)

        # Hash for storage
        token_hash = bcrypt.hashpw(raw_token.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        # Calculate expiration
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.magic_link_expire_minutes
        )

        # Create magic link record
        magic_link = MagicLink(
            email=email.lower(),
            token_hash=token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent[:512] if user_agent else None,
            signup_name=signup_name,
            signup_institution=signup_institution,
        )
        db.add(magic_link)
        db.commit()

        logger.info(f"Created magic link for {email}, expires {expires_at}")
        return raw_token

    def verify_magic_link(
        self,
        db: DBSession,
        email: str,
        token: str,
    ) -> Optional[MagicLink]:
        """
        Verify a magic link token

        Args:
            db: Database session
            email: Email address
            token: The magic link token

        Returns:
            MagicLink object if valid, None otherwise
        """
        # Find unused, non-expired magic links for this email
        magic_links = (
            db.query(MagicLink)
            .filter(MagicLink.email == email.lower())
            .filter(MagicLink.used_at.is_(None))
            .filter(MagicLink.expires_at > datetime.now(timezone.utc))
            .all()
        )

        for link in magic_links:
            try:
                if bcrypt.checkpw(
                    token.encode("utf-8"), link.token_hash.encode("utf-8")
                ):
                    # Mark as used
                    link.used_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info(f"Magic link verified for {email}")
                    return link
            except Exception:
                continue

        logger.warning(f"Invalid magic link attempt for {email}")
        return None

    def check_magic_link(
        self,
        db: DBSession,
        email: str,
        token: str,
    ) -> bool:
        """
        Check if a magic link token is valid WITHOUT consuming it.

        This is safe to call from prefetch/scanner requests since it
        doesn't mark the token as used.

        Args:
            db: Database session
            email: Email address
            token: The magic link token

        Returns:
            True if token is valid and unused, False otherwise
        """
        # Find unused, non-expired magic links for this email
        magic_links = (
            db.query(MagicLink)
            .filter(MagicLink.email == email.lower())
            .filter(MagicLink.used_at.is_(None))
            .filter(MagicLink.expires_at > datetime.now(timezone.utc))
            .all()
        )

        for link in magic_links:
            try:
                if bcrypt.checkpw(
                    token.encode("utf-8"), link.token_hash.encode("utf-8")
                ):
                    # Valid token found - but don't mark as used
                    return True
            except Exception:
                continue

        return False

    def get_or_create_user_for_magic_link(
        self,
        db: DBSession,
        email: str,
        name: Optional[str] = None,
        institution: Optional[str] = None,
    ) -> Tuple[User, bool]:
        """
        Get existing user or create new one for magic link login

        Args:
            db: Database session
            email: User's email
            name: Optional name for new users
            institution: Optional institution name for new users

        Returns:
            Tuple of (User, is_new_user)
        """
        # Check if user exists
        user = db.query(User).filter(User.email == email.lower()).first()
        if user:
            # Deactivated accounts cannot log back in via magic link
            if user.is_active is False:
                raise ValueError(
                    "This account has been deactivated. Please contact support."
                )
            # Mark email as verified (magic link proves email ownership)
            if not user.email_verified:
                user.email_verified = True
                user.email_verified_at = datetime.now(timezone.utc)
                db.commit()
            return user, False

        # Check if email is blocked (deactivated/deleted account)
        from ..services.account_deletion_service import AccountDeletionService

        blocked, block_reason = AccountDeletionService.is_email_blocked(db, email)
        if blocked:
            raise ValueError(
                block_reason or "This email address is not available for registration."
            )

        # Provisioning policy (defense in depth — the request endpoint applies
        # the same rules): closed by default, with two exceptions.
        # 1. First-run bootstrap: no users exist yet, so this login creates
        #    the deployment's admin and a department workspace.
        # 2. OPEN_SIGNUP=true: the operator deliberately runs an open
        #    deployment; new logins get individual workspaces (capped by
        #    INDIVIDUAL_ACCOUNT_LIMIT).
        is_bootstrap = db.query(User).count() == 0

        if not is_bootstrap and not self.settings.open_signup:
            raise ValueError(
                "Account provisioning is closed on this deployment. "
                "Ask your administrator for an invitation."
            )

        if is_bootstrap:
            import uuid as uuid_mod
            import os

            email_domain = email.split("@")[1] if "@" in email else "unknown"
            department = Department(
                id=str(uuid_mod.uuid4()),
                name=institution or email_domain,
                institution=institution or email_domain,
                contact_email=email,
                tier=os.getenv("DEFAULT_DEPARTMENT_TIER", "department"),
            )
            db.add(department)
            db.commit()
            db.refresh(department)
            logger.info(
                f"First-run bootstrap: created department {department.id} for {email}"
            )
        else:
            from ..config.settings import INDIVIDUAL_ACCOUNT_LIMIT

            individual_count = (
                db.query(Department).filter(Department.tier == "individual").count()
            )
            if individual_count >= INDIVIDUAL_ACCOUNT_LIMIT:
                raise ValueError(
                    "Self-service signups are currently full. "
                    "Ask your administrator or try again later."
                )
            department = self._get_or_create_individual_department(
                db, email, institution=institution
            )

        from ..db.models import UserRole

        user = User(
            email=email.lower(),
            name=name or email.split("@")[0],
            department_id=department.id,
            role=UserRole.ADMIN if is_bootstrap else UserRole.FACULTY,
            auth_provider=AuthProvider.MAGIC_LINK,
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()  # Get user.id before creating API key

        # Create an API key for the new user (for CLI/programmatic access)
        AuthService.create_api_key(
            db=db,
            user_id=user.id,
            department_id=department.id,
            name="Default API Key",
            rate_limit_per_hour=100,
            expires_days=None,  # No expiration for individual tier
        )

        db.commit()
        db.refresh(user)

        logger.info(f"Created new user {user.id} via magic link for {email}")

        # Send admin notification for new signup (fire and forget)
        self._notify_admins_new_signup(
            email, user.id, name=user.name, institution=institution
        )

        # Send welcome email to new user (fire and forget)
        self._send_welcome_email(email, user.name, department.tier)

        return user, True

    def _notify_admins_new_signup(
        self,
        email: str,
        user_id: str,
        name: Optional[str] = None,
        institution: Optional[str] = None,
    ) -> None:
        """Send async notification to admins about new signup (fire and forget)."""
        admin_emails_str = self.settings.admin_notification_emails
        if not admin_emails_str:
            return

        admin_emails = [e.strip() for e in admin_emails_str.split(",") if e.strip()]
        if not admin_emails:
            return

        try:
            email_service = get_email_service()
            # Get or create event loop and schedule the task
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, create one for this task
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Flag institution mismatch for admin review
            email_domain = email.split("@")[1] if "@" in email else ""
            institution_mismatch = False
            if institution and email_domain:
                # Simple heuristic: check if any word from institution appears in the domain
                inst_words = [w.lower() for w in institution.split() if len(w) > 2]
                domain_lower = email_domain.lower()
                institution_mismatch = not any(w in domain_lower for w in inst_words)

            subject_prefix = "[MISMATCH] " if institution_mismatch else ""

            loop.create_task(
                email_service.send_admin_notification(
                    to_emails=admin_emails,
                    subject=f"{subject_prefix}New Signup: {name or email} ({institution or 'no institution'})",
                    event_type="new_user_signup",
                    details={
                        "email": email,
                        "name": name or "(not provided)",
                        "institution": institution or "(not provided)",
                        "institution_mismatch": institution_mismatch,
                        "user_id": user_id,
                        "tier": "individual",
                        "source": "magic_link",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
        except Exception as e:
            # Don't fail signup if notification fails
            logger.warning(f"Failed to send admin notification for new signup: {e}")

    def _send_welcome_email(self, email: str, name: str, tier: str) -> None:
        """Send welcome email to new magic link user (fire and forget)."""
        try:
            email_service = get_email_service()
            if not email_service.is_configured():
                logger.debug("Email service not configured, skipping welcome email")
                return

            # Get or create event loop and schedule the task
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, create one for this task
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            loop.create_task(
                email_service.send_welcome_magic_link(
                    to_email=email,
                    name=name,
                    tier=tier,
                )
            )
            logger.info(f"Welcome email queued for {email}")
        except Exception as e:
            # Don't fail signup if email fails
            logger.warning(f"Failed to send welcome email to {email}: {e}")

    def _get_or_create_individual_department(
        self,
        db: DBSession,
        email: str,
        institution: Optional[str] = None,
    ) -> Department:
        """
        Get or create an individual department for a user

        Individual users get their own "department" with the individual tier.
        This simplifies the multi-tenant architecture.
        """
        # Create unique department for individual user
        import uuid

        dept_id = str(uuid.uuid4())
        email_domain = email.split("@")[1] if "@" in email else "unknown"

        department = Department(
            id=dept_id,
            name=f"Individual - {email}",
            institution=institution or email_domain,
            contact_email=email,
            tier="individual",
            max_users=1,
        )
        db.add(department)
        db.commit()
        db.refresh(department)

        logger.info(
            f"Created individual department {dept_id} for {email} (institution={institution or email_domain})"
        )
        return department


# Singleton instance
_session_service: Optional[SessionService] = None


def get_session_service() -> SessionService:
    """Get the singleton session service instance"""
    global _session_service
    if _session_service is None:
        _session_service = SessionService()
    return _session_service
