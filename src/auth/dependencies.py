"""
Authentication Dependencies for FastAPI Routes

Provides reusable authentication dependencies that can be imported
by any route file.

SECURITY:
- All endpoints require valid API key authentication by default
- Session cookie authentication is also supported (for dashboard users)
- Mock auth ONLY available with ALLOW_MOCK_AUTH=true (local dev only)
- Mock auth is NEVER allowed in production environment

Authentication Methods (checked in order):
1. Bearer token: Authorization: Bearer <api_key>
2. Session cookie: aelira_access cookie (JWT from magic link login)
3. Mock auth: ALLOW_MOCK_AUTH=true (dev only)
"""

import logging
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..db.database import get_db_dependency
from ..db.models import APIKey

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme (doesn't auto-error so we can provide custom messages)
security = HTTPBearer(auto_error=False)


def get_required_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dependency),
) -> Tuple[Optional[APIKey], str, str]:
    """
    SECURE: Requires valid authentication (API key or session cookie).

    This is the default auth dependency for all protected endpoints.
    Supports two authentication methods:
    1. Bearer token: Authorization: Bearer <api_key> (for CLI/programmatic access)
    2. Session cookie: aelira_access cookie (for dashboard users after magic link login)

    Mock authentication is ONLY allowed when ALLOW_MOCK_AUTH=true (for local dev).

    Returns:
        Tuple of (api_key_or_none, user_id, department_id)
        - api_key is None for session-based auth

    Raises:
        HTTPException 401: If no valid credentials provided
    """
    from ..config.settings import get_settings
    from ..auth.auth_service import AuthService
    from ..auth.session_service import get_session_service

    settings = get_settings()

    # Method 1: Check for Bearer token (API key, then JWT fallback)
    if credentials:
        token = credentials.credentials

        # 1a: Try API key validation first
        api_key = AuthService.validate_api_key(db, token)
        if api_key:
            return api_key, api_key.user_id, api_key.department_id

        # 1b: Try JWT Bearer token (e.g. LTI session tokens)
        from ..auth.jwt_service import JWTService

        jwt_service = JWTService()
        payload = jwt_service.decode_token(token)
        if payload:
            user_id = payload.get("sub") or payload.get("user_id")
            department_id = payload.get("department_id")
            if user_id and department_id:
                logger.debug(f"JWT Bearer auth successful for user {user_id}")
                return None, user_id, department_id

        # Both API key and JWT failed
        key_preview = (
            token[:8] + "..."
            if len(token) > 8
            else "***"
        )
        logger.warning(f"Invalid Bearer token attempt: {key_preview}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Method 2: Check for session cookie (dashboard users)
    access_token = request.cookies.get("aelira_access")
    if access_token:
        session_service = get_session_service()
        result = session_service.validate_session(db, access_token)
        if result:
            user, payload = result
            logger.debug(f"Session auth successful for user {user.id}")
            # Return None for api_key (session-based), but provide user/department info
            return None, user.id, user.department_id

        # Session cookie exists but is invalid/expired
        logger.debug("Invalid session cookie, will fall through to other auth methods")

    # Method 3: Mock auth (development only - STRICT positive checks)
    # SECURITY: Use positive logic to avoid typos enabling mock auth
    is_dev_environment = settings.env.lower() == "development"
    mock_auth_explicitly_enabled = settings.allow_mock_auth is True

    if mock_auth_explicitly_enabled and is_dev_environment:
        # Use stable IDs so requests are consistent across a dev session
        mock_user_id = "dev-user-local"
        mock_dept_id = "dev-dept-local"
        logger.debug(
            "Mock auth active (ENV=development, ALLOW_MOCK_AUTH=true). "
            "Using stable dev identity."
        )

        # Auto-create mock department/user so FK constraints are satisfied
        try:
            from src.db.database import get_db
            from src.db.models import Department, User

            with get_db() as db:
                if not db.query(Department).filter(Department.id == mock_dept_id).first():
                    db.add(Department(
                        id=mock_dept_id,
                        name="Dev Department",
                        institution="Dev University",
                        contact_email="dev@localhost",
                        tier="department",
                        max_users=999,
                    ))
                    db.flush()
                if not db.query(User).filter(User.id == mock_user_id).first():
                    db.add(User(
                        id=mock_user_id,
                        email="dev@localhost",
                        name="Dev User",
                        department_id=mock_dept_id,
                        role="admin",
                    ))
                    db.commit()
        except Exception as e:
            logger.debug(f"Mock auth: dev records already exist or error: {e}")

        return None, mock_user_id, mock_dept_id

    # No valid authentication found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide 'Authorization: Bearer <api_key>' header or login via dashboard.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_department_access(
    requested_department_id: str,
    authenticated_department_id: str,
) -> None:
    """
    Verify that the authenticated user has access to the requested department.

    Raises:
        HTTPException 403: If user doesn't have access to the department
    """
    if requested_department_id != authenticated_department_id:
        logger.warning(
            f"Department access denied: requested={requested_department_id}, "
            f"authenticated={authenticated_department_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this department",
        )


# Alias for backward compatibility
get_api_key_or_mock = get_required_api_key
