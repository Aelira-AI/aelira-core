"""
JWT Service - Token Creation and Validation

Provides functions for:
- Access token creation (15 min, stored in httpOnly cookie)
- Refresh token creation (7 days, stored in httpOnly cookie)
- Token validation and decoding
- Token refresh flow

Security:
- Supports HS256 (symmetric, dev) or RS256 (asymmetric, production)
- All tokens include jti (JWT ID) for revocation tracking
- Access tokens are short-lived (15 min default)
- Refresh tokens tracked in database for logout/revocation
"""

import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
import jwt

from ..config.settings import get_settings

logger = logging.getLogger(__name__)


class JWTService:
    """Service for JWT token creation and validation"""

    def __init__(self):
        self.settings = get_settings()
        self._private_key = None
        self._public_key = None
        self._load_keys()

    def _load_keys(self) -> None:
        """Load JWT signing keys based on algorithm configuration"""
        if self.settings.jwt_algorithm == "RS256":
            # RS256: Load RSA key pair
            if self.settings.jwt_private_key_path:
                try:
                    with open(self.settings.jwt_private_key_path, "r") as f:
                        self._private_key = f.read()
                    logger.info("Loaded JWT private key from file")
                except FileNotFoundError:
                    logger.error(
                        f"JWT private key file not found: {self.settings.jwt_private_key_path}"
                    )
                    raise ValueError("JWT_PRIVATE_KEY_PATH file not found")

            if self.settings.jwt_public_key_path:
                try:
                    with open(self.settings.jwt_public_key_path, "r") as f:
                        self._public_key = f.read()
                    logger.info("Loaded JWT public key from file")
                except FileNotFoundError:
                    logger.error(
                        f"JWT public key file not found: {self.settings.jwt_public_key_path}"
                    )
                    raise ValueError("JWT_PUBLIC_KEY_PATH file not found")
        else:
            # HS256: Use symmetric secret
            if not self.settings.jwt_secret:
                # Generate a random secret for development
                self._private_key = secrets.token_hex(32)
                self._public_key = self._private_key
                logger.warning(
                    "JWT_SECRET not set - using random secret. "
                    "Set JWT_SECRET in environment for persistent sessions."
                )
            else:
                self._private_key = self.settings.jwt_secret
                self._public_key = self.settings.jwt_secret

    def create_access_token(
        self,
        user_id: str,
        department_id: str,
        email: str,
        role: str,
        additional_claims: Optional[Dict[str, Any]] = None,
        expires_in_minutes: Optional[int] = None,
    ) -> Tuple[str, str, datetime]:
        """
        Create a new access token

        Args:
            user_id: User ID
            department_id: Department ID
            email: User's email
            role: User's role (faculty, admin, super_admin)
            additional_claims: Optional extra claims to include

        Returns:
            Tuple of (token, jti, expires_at)
        """
        jti = secrets.token_hex(16)  # JWT ID for revocation
        expire_minutes = (
            expires_in_minutes or self.settings.jwt_access_token_expire_minutes
        )
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)

        payload = {
            "sub": user_id,
            "jti": jti,
            "type": "access",
            "email": email,
            "department_id": department_id,
            "role": role,
            "iat": datetime.now(timezone.utc),
            "exp": expires_at,
        }

        if additional_claims:
            payload.update(additional_claims)

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm=self.settings.jwt_algorithm,
        )

        logger.debug(f"Created access token for user {user_id}, expires {expires_at}")
        return token, jti, expires_at

    def create_refresh_token(self, user_id: str) -> Tuple[str, str, datetime]:
        """
        Create a new refresh token

        Args:
            user_id: User ID

        Returns:
            Tuple of (token, token_hash_input, expires_at)
            - token: The refresh token to send to client
            - token_hash_input: The raw token value to hash and store in DB
            - expires_at: When the token expires
        """
        # Generate a secure random token
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self.settings.jwt_refresh_token_expire_days
        )

        # Create JWT wrapper (so we can include expiration in token itself)
        payload = {
            "sub": user_id,
            "token": raw_token,
            "type": "refresh",
            "iat": datetime.now(timezone.utc),
            "exp": expires_at,
        }

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm=self.settings.jwt_algorithm,
        )

        logger.debug(f"Created refresh token for user {user_id}, expires {expires_at}")
        return token, raw_token, expires_at

    def decode_token(
        self, token: str, verify_exp: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Decode and validate a JWT token

        Args:
            token: The JWT token string
            verify_exp: Whether to verify expiration (default True)

        Returns:
            Decoded payload dict, or None if invalid
        """
        try:
            options = {"verify_exp": verify_exp}
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=[self.settings.jwt_algorithm],
                options=options,
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.debug("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    def verify_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify an access token and return the payload

        Args:
            token: The access token string

        Returns:
            Decoded payload if valid access token, None otherwise
        """
        payload = self.decode_token(token)
        if payload and payload.get("type") == "access":
            return payload
        return None

    def verify_refresh_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify a refresh token and return the payload

        Args:
            token: The refresh token string

        Returns:
            Decoded payload if valid refresh token, None otherwise
        """
        payload = self.decode_token(token)
        if payload and payload.get("type") == "refresh":
            return payload
        return None

    def get_user_id_from_token(self, token: str) -> Optional[str]:
        """
        Extract user ID from any token type

        Args:
            token: JWT token string

        Returns:
            User ID or None if invalid
        """
        payload = self.decode_token(token, verify_exp=False)
        if payload:
            return payload.get("sub")
        return None


# Singleton instance
_jwt_service: Optional[JWTService] = None


def get_jwt_service() -> JWTService:
    """Get the singleton JWT service instance"""
    global _jwt_service
    if _jwt_service is None:
        _jwt_service = JWTService()
    return _jwt_service
