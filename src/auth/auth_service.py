"""
Authentication Service - API Key Management

Provides functions for:
- API key generation (with bcrypt hashing)
- API key validation
- Rate limiting (Redis-based)
- Usage tracking for billing
"""

import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
import logging

from ..db.models import APIKey, UsageTracking
from .redis_rate_limiter import RedisRateLimiter

logger = logging.getLogger(__name__)

# For backward compatibility, alias RateLimiter
RateLimiter = RedisRateLimiter


class AuthService:
    """Service for API key authentication and management"""

    @staticmethod
    def generate_api_key() -> Tuple[str, str, str]:
        """
        Generate a new API key with secure random token

        Returns:
            Tuple of (full_key, key_hash, key_prefix)
            - full_key: The actual key to give to user (show once!)
            - key_hash: bcrypt hash to store in database
            - key_prefix: First 12 chars for identification
        """
        # Generate secure random token (24 bytes = 48 hex chars)
        # Shorter to stay under bcrypt's 72-byte limit (12 prefix + 48 token = 60 bytes)
        random_token = secrets.token_hex(24)

        # Create full key with prefix
        full_key = f"aelira_live_{random_token}"

        # Hash the key for storage (bcrypt)
        # Key is now 60 bytes, well under bcrypt's 72-byte limit
        key_hash = bcrypt.hashpw(full_key.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        # Get prefix for identification (first 12 chars)
        key_prefix = full_key[:12]

        return full_key, key_hash, key_prefix

    @staticmethod
    def create_api_key(
        db: Session,
        user_id: str,
        department_id: str,
        name: str = "Default API Key",
        rate_limit_per_hour: int = 100,
        expires_days: Optional[int] = None,
    ) -> Tuple[APIKey, str]:
        """
        Create a new API key for a user

        Args:
            db: Database session
            user_id: User who owns this key
            department_id: Department the key belongs to
            name: Friendly name for the key
            rate_limit_per_hour: Rate limit (default 100 req/hour)
            expires_days: Optional expiration in days (None = never expires)

        Returns:
            Tuple of (APIKey object, full_key string)
            **IMPORTANT:** full_key is only returned once - store it safely!
        """
        # Generate key
        full_key, key_hash, key_prefix = AuthService.generate_api_key()

        # Calculate expiration
        expires_at = None
        if expires_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

        # Create database record
        api_key = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=name,
            user_id=user_id,
            department_id=department_id,
            rate_limit_per_hour=rate_limit_per_hour,
            expires_at=expires_at,
            is_active=True,
        )

        db.add(api_key)
        db.commit()
        db.refresh(api_key)

        logger.info(
            f"Created API key: {api_key.id} ({key_prefix}...) for user {user_id}"
        )

        return api_key, full_key

    @staticmethod
    def validate_api_key(db: Session, api_key: str) -> Optional[APIKey]:
        """
        Validate an API key and return the associated APIKey object.

        Uses Redis to cache the mapping from raw API key → key ID after
        the first successful bcrypt verification, so subsequent requests
        skip the expensive bcrypt loop (all 16 keys share the same prefix).
        """
        import hashlib
        from .redis_rate_limiter import get_redis_client

        # Fast cache lookup: SHA-256 of the raw key → API key DB id
        cache_key = None
        redis_client = get_redis_client()
        if redis_client:
            key_hash_hex = hashlib.sha256(api_key.encode()).hexdigest()
            cache_key = f"apikey_cache:{key_hash_hex}"
            try:
                cached_key_id = redis_client.get(cache_key)
                if cached_key_id:
                    db_key = db.query(APIKey).filter(
                        APIKey.id == cached_key_id, APIKey.is_active
                    ).first()
                    if db_key:
                        if db_key.expires_at and db_key.expires_at < datetime.now(timezone.utc):
                            logger.warning(f"API key {db_key.id} has expired")
                            redis_client.delete(cache_key)
                            return None
                        db_key.last_used_at = datetime.now(timezone.utc)
                        db.commit()
                        return db_key
                    # Cached key no longer valid, remove stale entry
                    redis_client.delete(cache_key)
            except Exception as e:
                logger.debug(f"Redis cache lookup failed: {e}")

        # Cache miss — fall back to bcrypt verification
        key_prefix = api_key[:12] if len(api_key) >= 12 else api_key

        potential_keys = (
            db.query(APIKey)
            .filter(APIKey.key_prefix == key_prefix)
            .filter(APIKey.is_active)
            .all()
        )

        for db_key in potential_keys:
            try:
                if bcrypt.checkpw(api_key.encode("utf-8"), db_key.key_hash.encode("utf-8")):
                    if db_key.expires_at and db_key.expires_at < datetime.now(timezone.utc):
                        logger.warning(f"API key {db_key.id} has expired")
                        return None

                    db_key.last_used_at = datetime.now(timezone.utc)
                    db.commit()

                    # Cache the result for 1 hour
                    if redis_client and cache_key:
                        try:
                            redis_client.setex(cache_key, 3600, str(db_key.id))
                        except Exception:
                            pass

                    logger.debug(f"Valid API key: {db_key.id} ({key_prefix}...)")
                    return db_key
            except Exception as e:
                logger.error(f"Error validating API key: {e}")
                continue

        logger.warning(f"Invalid API key attempted: {key_prefix}...")
        return None

    @staticmethod
    def revoke_api_key(db: Session, key_id: str, user_id: str) -> bool:
        """
        Revoke (deactivate) an API key

        Args:
            db: Database session
            key_id: API key ID to revoke
            user_id: User requesting revocation (must own the key)

        Returns:
            True if revoked, False if not found or unauthorized
        """
        api_key = (
            db.query(APIKey)
            .filter(APIKey.id == key_id)
            .filter(APIKey.user_id == user_id)
            .first()
        )

        if not api_key:
            logger.warning(
                f"API key {key_id} not found or unauthorized for user {user_id}"
            )
            return False

        api_key.is_active = False
        db.commit()

        logger.info(f"Revoked API key: {key_id} ({api_key.key_prefix}...)")
        return True

    @staticmethod
    def list_api_keys(db: Session, user_id: str) -> list:
        """
        List all API keys for a user (excluding hashes)

        Args:
            db: Database session
            user_id: User to list keys for

        Returns:
            List of APIKey objects (keys are masked)
        """
        keys = (
            db.query(APIKey)
            .filter(APIKey.user_id == user_id)
            .order_by(APIKey.created_at.desc())
            .all()
        )

        return keys

    @staticmethod
    def track_api_usage(
        db: Session,
        api_key: APIKey,
        endpoint: str,
        status_code: int,
        response_time_ms: int,
        request_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        pages_processed: int = 0,
        ollama_calls: int = 0,
    ):
        """
        Track API usage for billing and analytics

        Args:
            db: Database session
            api_key: The APIKey object used for this request
            endpoint: API endpoint called (e.g., "/api/education/pdf/scan")
            status_code: HTTP status code
            response_time_ms: Response time in milliseconds
            request_ip: Client IP address
            user_agent: Client user agent
            pages_processed: Number of pages/slides processed
            ollama_calls: Number of Ollama API calls made
        """
        # Determine scan type from endpoint
        from ..db.models import ScanType

        scan_type = None
        if "/pdf/" in endpoint:
            scan_type = ScanType.PDF
        elif "/powerpoint/" in endpoint:
            scan_type = ScanType.POWERPOINT
        elif "/latex/" in endpoint:
            scan_type = ScanType.LATEX

        usage = UsageTracking(
            api_key_id=api_key.id,
            endpoint=endpoint,
            scan_type=scan_type,
            request_ip=request_ip,
            user_agent=user_agent,
            status_code=status_code,
            response_time_ms=response_time_ms,
            pages_processed=pages_processed,
            ollama_calls=ollama_calls,
        )

        db.add(usage)
        db.commit()

        logger.debug(
            f"Tracked API usage: {endpoint} ({status_code}) for key {api_key.id}"
        )
