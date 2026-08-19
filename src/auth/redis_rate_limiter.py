"""
Redis client for rate limiting, caching, and OAuth state management.

Provides Redis connection, rate limiting, and secure OAuth state storage.
"""

import redis
import secrets
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta
import logging
import json

from ..config.settings import get_settings
from ..utils.security import redact_url_credentials

logger = logging.getLogger(__name__)


class OAuthStateStorageError(RuntimeError):
    """Raised when durable OAuth state storage is required but unavailable."""


_settings = get_settings()

# Redis client instance (lazy initialization)
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client instance (singleton pattern).

    Returns None if Redis is disabled or unavailable.
    """
    global _redis_client

    if not _settings.redis_enabled:
        logger.debug("Redis is disabled in settings")
        return None

    if _redis_client is None:
        try:
            # Parse Redis URL
            redis_url = _settings.redis_url
            _redis_client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,  # Increased timeout for Docker
                socket_timeout=5,
                health_check_interval=30,  # Check connection health every 30s
            )
            # Test connection
            _redis_client.ping()
            # Redacted: the URL carries the password inline, and logging it
            # verbatim wrote the credential to stdout, Loki and Sentry.
            logger.info(f"Connected to Redis at {redact_url_credentials(redis_url)}")
        except redis.ConnectionError as e:
            logger.warning(
                f"Failed to connect to Redis: {e}. Rate limiting will use in-memory fallback."
            )
            _redis_client = None
        except Exception as e:
            logger.warning(
                f"Redis error: {e}. Rate limiting will use in-memory fallback."
            )
            _redis_client = None

    return _redis_client


class RedisRateLimiter:
    """Redis-based rate limiter for API keys."""

    @staticmethod
    def check_rate_limit(api_key_id: str, limit_per_hour: int) -> Tuple[bool, dict]:
        """
        Check if API key has exceeded rate limit using Redis.

        Falls back to in-memory storage if Redis is unavailable.

        Args:
            api_key_id: API key ID
            limit_per_hour: Rate limit (requests per hour)

        Returns:
            Tuple of (allowed: bool, headers: dict)
        """
        redis_client = get_redis_client()

        if redis_client is None:
            # Fallback to in-memory rate limiting
            return RedisRateLimiter._check_rate_limit_memory(api_key_id, limit_per_hour)

        try:
            current_time = datetime.utcnow()
            hour_key = current_time.strftime("%Y-%m-%d-%H")
            redis_key = f"rate_limit:{api_key_id}:{hour_key}"

            # Get current count
            current_count = redis_client.get(redis_key)

            if current_count is None:
                # First request in this hour window
                redis_client.setex(redis_key, 3600, 1)  # Expire in 1 hour
                allowed = True
                remaining = limit_per_hour - 1
            else:
                current_count = int(current_count)

                if current_count >= limit_per_hour:
                    allowed = False
                    remaining = 0
                else:
                    # Increment count
                    redis_client.incr(redis_key)
                    allowed = True
                    remaining = limit_per_hour - current_count - 1

            # Get expiration time
            ttl = redis_client.ttl(redis_key)
            reset_at = datetime.utcnow() + timedelta(seconds=ttl if ttl > 0 else 3600)

            # Build headers
            headers = {
                "X-RateLimit-Limit": str(limit_per_hour),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Reset": str(int(reset_at.timestamp())),
            }

            logger.debug(
                f"Rate limit check for {api_key_id}: {current_count or 0}/{limit_per_hour}, allowed={allowed}"
            )

            return allowed, headers

        except redis.RedisError as e:
            logger.error(
                f"Redis error during rate limit check: {e}, falling back to in-memory"
            )
            return RedisRateLimiter._check_rate_limit_memory(api_key_id, limit_per_hour)
        except Exception as e:
            logger.error(
                f"Unexpected error during rate limit check: {e}, falling back to in-memory"
            )
            return RedisRateLimiter._check_rate_limit_memory(api_key_id, limit_per_hour)

    @staticmethod
    def _check_rate_limit_memory(
        api_key_id: str, limit_per_hour: int
    ) -> Tuple[bool, dict]:
        """
        Fallback in-memory rate limiting (for when Redis is unavailable).

        This is not production-ready for multi-instance deployments.
        """
        # In-memory storage (fallback)
        if not hasattr(RedisRateLimiter, "_rate_limits"):
            RedisRateLimiter._rate_limits = {}

        current_time = datetime.utcnow()
        hour_key = current_time.strftime("%Y-%m-%d-%H")
        key = f"{api_key_id}:{hour_key}"

        # Get current count
        if key not in RedisRateLimiter._rate_limits:
            RedisRateLimiter._rate_limits[key] = {
                "count": 0,
                "reset_at": current_time + timedelta(hours=1),
            }

        rate_data = RedisRateLimiter._rate_limits[key]

        # Check if window has reset
        if current_time >= rate_data["reset_at"]:
            rate_data["count"] = 0
            rate_data["reset_at"] = current_time + timedelta(hours=1)

        # Check limit
        if rate_data["count"] >= limit_per_hour:
            allowed = False
        else:
            allowed = True
            rate_data["count"] += 1

        # Build headers
        headers = {
            "X-RateLimit-Limit": str(limit_per_hour),
            "X-RateLimit-Remaining": str(max(0, limit_per_hour - rate_data["count"])),
            "X-RateLimit-Reset": str(int(rate_data["reset_at"].timestamp())),
        }

        logger.debug(
            f"Rate limit check (memory) for {api_key_id}: {rate_data['count']}/{limit_per_hour}, allowed={allowed}"
        )

        return allowed, headers

    @staticmethod
    def reset_rate_limit(api_key_id: str):
        """Reset rate limit for an API key (admin only)."""
        redis_client = get_redis_client()

        if redis_client is not None:
            try:
                # Delete all rate limit keys for this API key (pattern matching)
                # Note: Redis keys() with pattern can be slow on large datasets
                # In production, consider using SCAN instead
                pattern = f"rate_limit:{api_key_id}:*"
                # Get all matching keys
                keys = []
                for key in redis_client.scan_iter(match=pattern):
                    keys.append(key)

                if keys:
                    redis_client.delete(*keys)
                    logger.info(
                        f"Reset rate limit for {api_key_id} (Redis) - deleted {len(keys)} keys"
                    )
                else:
                    logger.debug(f"No rate limit keys found for {api_key_id}")
            except redis.RedisError as e:
                logger.error(f"Failed to reset rate limit in Redis: {e}")

        # Also clear in-memory fallback
        if hasattr(RedisRateLimiter, "_rate_limits"):
            keys_to_delete = [
                k
                for k in RedisRateLimiter._rate_limits.keys()
                if k.startswith(api_key_id)
            ]
            for key in keys_to_delete:
                del RedisRateLimiter._rate_limits[key]
            if keys_to_delete:
                logger.info(
                    f"Reset rate limit for {api_key_id} (memory) - deleted {len(keys_to_delete)} keys"
                )


class OAuthStateManager:
    """
    Secure OAuth state management with Redis storage and expiration.

    Prevents CSRF attacks by:
    1. Generating cryptographically secure state tokens
    2. Storing state server-side with TTL (default 10 minutes)
    3. Verifying and consuming state tokens (one-time use)
    """

    STATE_TTL_SECONDS = 600  # 10 minutes
    STATE_PREFIX = "oauth_state:"

    # In-memory fallback for when Redis is unavailable
    _memory_states: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def create_state(
        cls,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        allow_memory_fallback: bool = True,
    ) -> str:
        """
        Generate and store a new OAuth state token.

        Args:
            metadata: Optional metadata to store with the state (e.g., department_id, provider)
            allow_memory_fallback: Preserve legacy in-process storage when Redis
                is unavailable. Set false for flows that must fail closed.

        Returns:
            The generated state token (32-byte URL-safe string)
        """
        state = secrets.token_urlsafe(32)
        redis_client = get_redis_client()

        state_data = {
            "created_at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        }

        if redis_client is not None:
            try:
                redis_key = f"{cls.STATE_PREFIX}{state}"
                redis_client.setex(
                    redis_key,
                    cls.STATE_TTL_SECONDS,
                    json.dumps(state_data),
                )
                logger.debug("OAuth state created in Redis")
                return state
            except redis.RedisError:
                if not allow_memory_fallback:
                    logger.error(
                        "Failed to store OAuth state in required Redis storage"
                    )
                    raise OAuthStateStorageError("OAuth state storage is unavailable")
                logger.warning(
                    "Failed to store OAuth state in Redis, using memory fallback"
                )

        if not allow_memory_fallback:
            logger.error("Required Redis storage is unavailable for OAuth state")
            raise OAuthStateStorageError("OAuth state storage is unavailable")

        # Cleanup expired states before adding new one (lazy garbage collection)
        cls.cleanup_expired_memory_states()

        # Fallback to in-memory storage
        cls._memory_states[state] = {
            **state_data,
            "expires_at": datetime.utcnow() + timedelta(seconds=cls.STATE_TTL_SECONDS),
        }
        logger.debug("OAuth state created in memory")
        return state

    @classmethod
    def verify_and_consume_state(
        cls,
        state: str,
        *,
        allow_memory_fallback: bool = True,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Verify and consume an OAuth state token (one-time use).

        Args:
            state: The state token to verify
            allow_memory_fallback: Check legacy in-process state if Redis is
                unavailable. Set false to require Redis-backed verification.

        Returns:
            Tuple of (is_valid, metadata)
            - is_valid: True if state was valid and not expired
            - metadata: The metadata stored with the state, or None if invalid
        """
        if not state:
            return False, None

        redis_client = get_redis_client()

        if redis_client is not None:
            try:
                redis_key = f"{cls.STATE_PREFIX}{state}"
                # Get and delete atomically
                state_json = redis_client.getdel(redis_key)

                if state_json:
                    try:
                        state_data = json.loads(state_json)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Malformed OAuth state data was consumed")
                        return False, None
                    if not isinstance(state_data, dict):
                        logger.warning("Malformed OAuth state data was consumed")
                        return False, None
                    logger.debug("OAuth state verified and consumed from Redis")
                    return True, state_data.get("metadata")
                else:
                    logger.warning("OAuth state not found or expired")
                    return False, None
            except redis.RedisError:
                if not allow_memory_fallback:
                    logger.error(
                        "Required Redis storage failed during OAuth state verification"
                    )
                    return False, None
                logger.warning(
                    "Redis error verifying OAuth state, checking memory fallback"
                )

        if not allow_memory_fallback:
            logger.error(
                "Required Redis storage is unavailable for OAuth state verification"
            )
            return False, None

        # Check in-memory fallback
        if state in cls._memory_states:
            state_data = cls._memory_states.pop(state)
            if datetime.utcnow() < state_data.get("expires_at", datetime.min):
                logger.debug("OAuth state verified and consumed from memory")
                return True, state_data.get("metadata")
            else:
                logger.warning("OAuth state expired in memory")
                return False, None

        logger.warning("OAuth state not found")
        return False, None

    @classmethod
    def cleanup_expired_memory_states(cls):
        """Clean up expired states from in-memory storage."""
        now = datetime.utcnow()
        expired = [
            state
            for state, data in cls._memory_states.items()
            if data.get("expires_at", datetime.min) < now
        ]
        for state in expired:
            del cls._memory_states[state]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired OAuth states from memory")
