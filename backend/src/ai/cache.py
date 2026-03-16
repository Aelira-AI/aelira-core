"""
LLM Response Cache for Aelira.

Redis-based caching for LLM API responses to reduce costs and improve latency.
Caches based on prompt hash + image hash (for vision requests).

Expected savings: 50-80% reduction in API calls for repeated content.

Usage:
    from src.ai.cache import get_llm_cache

    cache = get_llm_cache()

    # Check cache before API call
    cached = cache.get(prompt, image_hash="abc123")
    if cached:
        return cached

    # After API call, cache the response
    cache.set(prompt, response, image_hash="abc123")
"""

import hashlib
import json
import logging
import os
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# Cache configuration from environment
CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL_HOURS = int(os.getenv("LLM_CACHE_TTL_HOURS", "24"))
CACHE_PREFIX = "llm_cache:"


class LLMCache:
    """
    Redis-based LLM response cache.

    Caches LLM responses based on:
    - Prompt content (hashed)
    - Image data hash (for vision requests)
    - Provider and model (optional, for cache isolation)

    Features:
    - 24-hour TTL by default (configurable)
    - Separate namespaces for text vs vision requests
    - Automatic cache invalidation on TTL expiry
    - Cache hit/miss statistics tracking
    """

    def __init__(
        self,
        redis_client=None,
        ttl_hours: int = None,
        enabled: bool = None,
    ):
        """
        Initialize LLM cache.

        Args:
            redis_client: Redis client instance. If None, uses the global Redis connection.
            ttl_hours: Cache TTL in hours. Defaults to LLM_CACHE_TTL_HOURS env var.
            enabled: Whether caching is enabled. Defaults to LLM_CACHE_ENABLED env var.
        """
        self.enabled = enabled if enabled is not None else CACHE_ENABLED
        self.ttl = (ttl_hours or CACHE_TTL_HOURS) * 3600  # Convert to seconds
        self._redis = redis_client
        self._stats = {"hits": 0, "misses": 0, "sets": 0}

    @property
    def redis(self):
        """Lazy initialization of Redis client."""
        if self._redis is None:
            try:
                from src.db.redis_client import get_redis_client
                self._redis = get_redis_client()
            except Exception as e:
                logger.warning(f"Failed to initialize Redis for LLM cache: {e}")
                self.enabled = False
                return None
        return self._redis

    def _make_key(
        self,
        prompt: str,
        image_hash: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate cache key from prompt and optional image hash.

        Args:
            prompt: The LLM prompt
            image_hash: Hash of image data (for vision requests)
            provider: LLM provider name (optional, for cache isolation)
            model: Model name (optional, for cache isolation)

        Returns:
            Cache key string
        """
        # Create hash of prompt content
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        # Build key components
        components = [CACHE_PREFIX]

        if provider:
            components.append(provider)
        if model:
            components.append(model)

        if image_hash:
            components.append(f"vision:{prompt_hash}:{image_hash[:16]}")
        else:
            components.append(f"text:{prompt_hash}")

        return ":".join(components)

    def get(
        self,
        prompt: str,
        image_hash: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get cached response for a prompt.

        Args:
            prompt: The LLM prompt
            image_hash: Hash of image data (for vision requests)
            provider: LLM provider name
            model: Model name

        Returns:
            Cached response string, or None if not cached
        """
        if not self.enabled or not self.redis:
            return None

        try:
            key = self._make_key(prompt, image_hash, provider, model)
            cached = self.redis.get(key)

            if cached:
                self._stats["hits"] += 1
                logger.debug(f"LLM cache hit for key {key[:50]}...")
                # Redis returns bytes, decode to string
                if isinstance(cached, bytes):
                    return cached.decode()
                return cached
            else:
                self._stats["misses"] += 1
                return None

        except Exception as e:
            logger.warning(f"LLM cache get failed: {e}")
            return None

    def set(
        self,
        prompt: str,
        response: str,
        image_hash: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Cache an LLM response.

        Args:
            prompt: The LLM prompt
            response: The LLM response to cache
            image_hash: Hash of image data (for vision requests)
            provider: LLM provider name
            model: Model name
            ttl: Optional custom TTL in seconds

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled or not self.redis:
            return False

        if not response:
            return False

        try:
            key = self._make_key(prompt, image_hash, provider, model)
            cache_ttl = ttl or self.ttl

            self.redis.setex(key, cache_ttl, response)
            self._stats["sets"] += 1
            logger.debug(f"LLM cache set for key {key[:50]}... (TTL: {cache_ttl}s)")
            return True

        except Exception as e:
            logger.warning(f"LLM cache set failed: {e}")
            return False

    def delete(
        self,
        prompt: str,
        image_hash: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bool:
        """
        Delete a cached response.

        Args:
            prompt: The LLM prompt
            image_hash: Hash of image data
            provider: LLM provider name
            model: Model name

        Returns:
            True if deleted, False otherwise
        """
        if not self.redis:
            return False

        try:
            key = self._make_key(prompt, image_hash, provider, model)
            self.redis.delete(key)
            return True
        except Exception as e:
            logger.warning(f"LLM cache delete failed: {e}")
            return False

    def clear_all(self, pattern: str = None) -> int:
        """
        Clear all LLM cache entries (or those matching a pattern).

        Args:
            pattern: Optional pattern to match (e.g., "llm_cache:gemini:*")

        Returns:
            Number of entries deleted
        """
        if not self.redis:
            return 0

        try:
            search_pattern = pattern or f"{CACHE_PREFIX}*"
            keys = list(self.redis.scan_iter(match=search_pattern))
            if keys:
                deleted = self.redis.delete(*keys)
                logger.info(f"Cleared {deleted} LLM cache entries")
                return deleted
            return 0
        except Exception as e:
            logger.warning(f"LLM cache clear failed: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with hit/miss/set counts and hit rate
        """
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0.0

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "total_requests": total,
            "hit_rate": f"{hit_rate:.1%}",
            "enabled": self.enabled,
            "ttl_hours": self.ttl // 3600,
        }


# Global cache instance
_llm_cache: Optional[LLMCache] = None


def get_llm_cache() -> LLMCache:
    """
    Get the global LLM cache instance.

    Returns:
        LLMCache instance
    """
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMCache()
    return _llm_cache


def hash_image_data(image_data: bytes) -> str:
    """
    Generate a hash of image data for cache key.

    Args:
        image_data: Raw image bytes

    Returns:
        SHA256 hash string (truncated to 16 chars)
    """
    return hashlib.sha256(image_data).hexdigest()[:16]
