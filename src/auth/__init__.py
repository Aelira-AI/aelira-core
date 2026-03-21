"""Authentication package - API key management, JWT, and session handling"""

from .auth_service import AuthService, RateLimiter
from .jwt_service import JWTService, get_jwt_service
from .session_service import SessionService, get_session_service
from .dependencies import (
    get_required_api_key,
    get_api_key_or_mock,
    verify_department_access,
)

__all__ = [
    "AuthService",
    "RateLimiter",
    "JWTService",
    "get_jwt_service",
    "SessionService",
    "get_session_service",
    "get_required_api_key",
    "get_api_key_or_mock",
    "verify_department_access",
]
