"""
LLM Provider Manager.

Manages provider selection, fallback chains, and user preferences.
"""

import os
import logging
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse
from .types import ProviderType, ProviderConfig
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from .anthropic_provider import AnthropicProvider
from .xai_provider import XAIProvider
from ..cache import get_llm_cache, hash_image_data
from src.utils.async_helpers import run_async_from_sync

logger = logging.getLogger(__name__)


class ProviderRateLimiter:
    """
    Per-provider rate limiting to prevent API quota exhaustion.

    Tracks requests per minute (RPM) and requests per day (RPD) for each provider.
    Gemini free tier is particularly restrictive: 15 RPM, 1500 RPD.
    """

    # Default rate limits per provider (can be overridden via env vars)
    DEFAULT_LIMITS = {
        "gemini": {"rpm": 15, "rpd": 1500},  # Gemini free tier
        "ollama": {"rpm": 1000, "rpd": 100000},  # Local, effectively unlimited
        "openai": {"rpm": 60, "rpd": 10000},  # Depends on tier
        "anthropic": {"rpm": 60, "rpd": 10000},  # Depends on tier
        "xai": {"rpm": 60, "rpd": 10000},  # Grok rate limits
    }

    def __init__(self):
        """Initialize rate limiter with default limits."""
        from collections import defaultdict
        from datetime import datetime

        self._minute_counts: Dict[str, List[datetime]] = defaultdict(list)
        self._day_counts: Dict[str, List[datetime]] = defaultdict(list)
        self._limits = self._load_limits()

    def _load_limits(self) -> Dict[str, Dict[str, int]]:
        """Load rate limits from environment or use defaults."""
        limits = {}
        for provider, default in self.DEFAULT_LIMITS.items():
            rpm_key = f"{provider.upper()}_RATE_LIMIT_RPM"
            rpd_key = f"{provider.upper()}_RATE_LIMIT_RPD"

            limits[provider] = {
                "rpm": int(os.getenv(rpm_key, default["rpm"])),
                "rpd": int(os.getenv(rpd_key, default["rpd"])),
            }
        return limits

    def can_proceed(self, provider: str) -> bool:
        """
        Check if a request to the provider is allowed under rate limits.

        Args:
            provider: Provider name (gemini, ollama, openai, anthropic, xai)

        Returns:
            True if request is allowed, False if rate limited
        """
        from datetime import datetime, timedelta

        provider = provider.lower()
        now = datetime.utcnow()
        limits = self._limits.get(provider, {"rpm": 60, "rpd": 10000})

        # Clean old entries
        minute_ago = now - timedelta(minutes=1)
        day_ago = now - timedelta(days=1)

        self._minute_counts[provider] = [
            t for t in self._minute_counts[provider] if t > minute_ago
        ]
        self._day_counts[provider] = [
            t for t in self._day_counts[provider] if t > day_ago
        ]

        # Check limits
        if len(self._minute_counts[provider]) >= limits["rpm"]:
            logger.warning(f"Rate limit exceeded for {provider}: {limits['rpm']} RPM")
            return False
        if len(self._day_counts[provider]) >= limits["rpd"]:
            logger.warning(f"Rate limit exceeded for {provider}: {limits['rpd']} RPD")
            return False

        return True

    def record_request(self, provider: str):
        """
        Record a request to a provider.

        Args:
            provider: Provider name
        """
        from datetime import datetime

        provider = provider.lower()
        now = datetime.utcnow()
        self._minute_counts[provider].append(now)
        self._day_counts[provider].append(now)

    def get_usage(self, provider: str = None) -> Dict[str, Any]:
        """
        Get current usage statistics for a provider or all providers.

        Args:
            provider: Optional provider name. If None, returns all.

        Returns:
            Dict with rpm_used, rpd_used, rpm_limit, rpd_limit
        """
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        minute_ago = now - timedelta(minutes=1)
        day_ago = now - timedelta(days=1)

        def get_provider_usage(p: str) -> Dict:
            p = p.lower()
            limits = self._limits.get(p, {"rpm": 60, "rpd": 10000})

            # Clean and count
            minute_requests = [
                t for t in self._minute_counts.get(p, []) if t > minute_ago
            ]
            day_requests = [t for t in self._day_counts.get(p, []) if t > day_ago]

            return {
                "rpm_used": len(minute_requests),
                "rpm_limit": limits["rpm"],
                "rpm_remaining": max(0, limits["rpm"] - len(minute_requests)),
                "rpd_used": len(day_requests),
                "rpd_limit": limits["rpd"],
                "rpd_remaining": max(0, limits["rpd"] - len(day_requests)),
            }

        if provider:
            return get_provider_usage(provider)

        return {p: get_provider_usage(p) for p in self.DEFAULT_LIMITS.keys()}


# Global rate limiter instance
_rate_limiter: Optional[ProviderRateLimiter] = None


def get_rate_limiter() -> ProviderRateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = ProviderRateLimiter()
    return _rate_limiter


_run_async_in_thread = run_async_from_sync  # backwards compat alias


class ProviderManager:
    """
    Manages LLM providers with automatic fallback and user preferences.

    Features:
    - Multiple provider support (Gemini, Ollama, OpenAI, Anthropic, xAI)
    - Automatic fallback when primary provider fails
    - User-configurable primary and fallback providers
    - Health monitoring for all providers
    """

    def __init__(
        self,
        primary_provider: Optional[ProviderType] = None,
        fallback_provider: Optional[ProviderType] = None,
        configs: Optional[Dict[ProviderType, ProviderConfig]] = None,
    ):
        """
        Initialize provider manager.

        Args:
            primary_provider: Primary provider to use. Defaults to the explicit
                LLM_PROVIDER environment value; no value leaves inference disabled.
            fallback_provider: Fallback provider. Defaults to the explicit
                LLM_FALLBACK_PROVIDER environment value; no value disables fallback.
            configs: Optional provider configurations. If not provided, uses env vars.
        """
        # Open-core has no preferred cloud vendor. A provider exists only when
        # the operator explicitly selects one through construction or the
        # environment.
        self.primary_type: Optional[ProviderType] = (
            primary_provider
            if primary_provider is not None
            else self._get_env_provider("LLM_PROVIDER")
        )
        # Fallback defaults to None (disabled).
        # Set LLM_FALLBACK_PROVIDER=ollama to enable Ollama fallback.
        if fallback_provider is not None:
            self.fallback_type = fallback_provider
        else:
            self.fallback_type = self._get_env_provider("LLM_FALLBACK_PROVIDER", None)

        self.configs = configs or {}
        self._providers: Dict[ProviderType, LLMProvider] = {}
        self._initialized = False

        # Build default configs from environment
        self._build_default_configs()

    def _get_env_provider(
        self, env_var: str, default: Optional[ProviderType] = None
    ) -> Optional[ProviderType]:
        """Get provider type from environment variable.

        Returns None if the env var is set to 'none' or empty string,
        allowing callers to disable a provider (e.g. LLM_FALLBACK_PROVIDER=none).
        """
        value = os.getenv(env_var, "").lower().strip()
        if not value:
            return default
        if value == "none":
            return None
        try:
            return ProviderType.from_string(value)
        except ValueError:
            logger.warning(
                f"Invalid {env_var}={value}, using default "
                f"{default.value if default else 'none'}"
            )
        return default

    @staticmethod
    def _model_from_environment(name: str, legacy_name: str, default: str) -> str:
        """Read the canonical model variable with legacy fallback compatibility."""
        configured = os.getenv(name, "").strip()
        if configured:
            return configured
        legacy = os.getenv(legacy_name, "").strip()
        return legacy or default

    def _build_default_configs(self):
        """Build default configs from environment variables."""
        # Gemini config
        if ProviderType.GEMINI not in self.configs:
            self.configs[ProviderType.GEMINI] = ProviderConfig(
                provider_type=ProviderType.GEMINI,
                api_key=os.getenv("GEMINI_API_KEY", ""),
                api_base=os.getenv(
                    "GEMINI_API_BASE",
                    "https://generativelanguage.googleapis.com/v1beta",
                ),
                text_model=os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash"),
                code_model=os.getenv("GEMINI_CODE_MODEL", "gemini-2.5-flash"),
                vision_model=os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash"),
            )

        # Ollama config
        if ProviderType.OLLAMA not in self.configs:
            self.configs[ProviderType.OLLAMA] = ProviderConfig(
                provider_type=ProviderType.OLLAMA,
                host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                text_model=self._model_from_environment(
                    "OLLAMA_TEXT_MODEL", "OLLAMA_FALLBACK_TEXT", "gemma3:4b"
                ),
                code_model=self._model_from_environment(
                    "OLLAMA_CODE_MODEL", "OLLAMA_FALLBACK_CODE", "qwen2.5-coder:7b"
                ),
                vision_model=self._model_from_environment(
                    "OLLAMA_VISION_MODEL", "OLLAMA_FALLBACK_VISION", "qwen2.5vl:3b"
                ),
                embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            )

        # OpenAI config
        if ProviderType.OPENAI not in self.configs:
            self.configs[ProviderType.OPENAI] = ProviderConfig(
                provider_type=ProviderType.OPENAI,
                api_key=os.getenv("OPENAI_API_KEY", ""),
                api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
                text_model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
                code_model=os.getenv("OPENAI_CODE_MODEL", "gpt-4o"),
                vision_model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o"),
            )

        # Anthropic config
        if ProviderType.ANTHROPIC not in self.configs:
            self.configs[ProviderType.ANTHROPIC] = ProviderConfig(
                provider_type=ProviderType.ANTHROPIC,
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                api_base=os.getenv(
                    "ANTHROPIC_API_BASE", "https://api.anthropic.com/v1"
                ),
                text_model=os.getenv(
                    "ANTHROPIC_TEXT_MODEL", "claude-3-5-sonnet-20241022"
                ),
                code_model=os.getenv(
                    "ANTHROPIC_CODE_MODEL", "claude-3-5-sonnet-20241022"
                ),
                vision_model=os.getenv(
                    "ANTHROPIC_VISION_MODEL", "claude-3-5-sonnet-20241022"
                ),
            )

        # xAI config
        if ProviderType.XAI not in self.configs:
            self.configs[ProviderType.XAI] = ProviderConfig(
                provider_type=ProviderType.XAI,
                api_key=os.getenv("XAI_API_KEY", ""),
                api_base=os.getenv("XAI_API_BASE", "https://api.x.ai/v1"),
                text_model=os.getenv("XAI_TEXT_MODEL", "grok-2"),
                code_model=os.getenv("XAI_CODE_MODEL", "grok-2"),
                vision_model=os.getenv("XAI_VISION_MODEL", "grok-2-vision"),
            )

    def _create_provider(self, provider_type: ProviderType) -> LLMProvider:
        """Create a provider instance."""
        config = self.configs.get(provider_type)

        if provider_type == ProviderType.GEMINI:
            return GeminiProvider(config)
        elif provider_type == ProviderType.OLLAMA:
            return OllamaProvider(config)
        elif provider_type == ProviderType.OPENAI:
            return OpenAIProvider(config)
        elif provider_type == ProviderType.ANTHROPIC:
            return AnthropicProvider(config)
        elif provider_type == ProviderType.XAI:
            return XAIProvider(config)
        else:
            raise ValueError(f"Unknown provider type: {provider_type}")

    async def initialize(self) -> bool:
        """Initialize only the explicitly selected primary and fallback providers."""
        if self._initialized:
            return True

        success = False

        # Initialize primary provider
        if self.primary_type is not None:
            try:
                self._providers[self.primary_type] = self._create_provider(
                    self.primary_type
                )
                if await self._providers[self.primary_type].initialize():
                    logger.info(
                        f"Primary provider {self.primary_type.value} initialized"
                    )
                    success = True
                else:
                    logger.warning(
                        f"Primary provider {self.primary_type.value} failed to initialize"
                    )
            except Exception as e:
                logger.error(f"Error initializing primary provider: {e}")

        # Initialize fallback provider
        if self.fallback_type and self.fallback_type != self.primary_type:
            try:
                self._providers[self.fallback_type] = self._create_provider(
                    self.fallback_type
                )
                if await self._providers[self.fallback_type].initialize():
                    logger.info(
                        f"Fallback provider {self.fallback_type.value} initialized"
                    )
                    success = True
                else:
                    logger.warning(
                        f"Fallback provider {self.fallback_type.value} failed to initialize"
                    )
            except Exception as e:
                logger.error(f"Error initializing fallback provider: {e}")

        self._initialized = success
        return success

    async def close(self) -> None:
        """Close all providers."""
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception as e:
                logger.error(f"Error closing provider {provider.name}: {e}")

        self._providers.clear()
        self._initialized = False

    def get_provider(
        self, provider_type: Optional[ProviderType] = None
    ) -> Optional[LLMProvider]:
        """Get a specific provider or the primary provider."""
        if provider_type:
            return self._providers.get(provider_type)
        return self._providers.get(self.primary_type)

    def _selected_provider_name(self, provider: Optional[ProviderType] = None) -> str:
        """Return a bounded cache/telemetry label for the selected route."""
        selected = provider or self.primary_type or self.fallback_type
        return selected.value if selected is not None else "none"

    def get_available_providers(self) -> List[ProviderType]:
        """Get list of available (initialized) providers."""
        return [
            ptype
            for ptype, provider in self._providers.items()
            if provider.is_available
        ]

    async def _execute_with_fallback(
        self,
        method_name: str,
        provider: Optional[ProviderType] = None,
        **kwargs,
    ) -> LLMResponse:
        """Execute a method with fallback support."""
        if not self._initialized:
            await self.initialize()

        # Determine which providers to try
        providers_to_try = []
        if provider:
            # User specified a provider
            if provider in self._providers:
                providers_to_try.append(provider)
            else:
                return LLMResponse.error_response(
                    error=f"Provider {provider.value} not initialized",
                    provider=provider.value,
                    model="",
                )
        else:
            # Use primary with fallback
            if self.primary_type in self._providers:
                providers_to_try.append(self.primary_type)
            if self.fallback_type and self.fallback_type in self._providers:
                providers_to_try.append(self.fallback_type)

        if not providers_to_try:
            return LLMResponse.error_response(
                error="No providers available",
                provider="none",
                model="",
            )

        # Try each provider with rate limiting
        rate_limiter = get_rate_limiter()
        last_error = None
        attempted_providers: List[str] = []

        for ptype in providers_to_try:
            provider_instance = self._providers[ptype]

            if not provider_instance.is_available:
                logger.warning(f"Provider {ptype.value} not available, skipping")
                continue

            # Check rate limits before proceeding
            if not rate_limiter.can_proceed(ptype.value):
                logger.warning(f"Provider {ptype.value} rate limited, trying next")
                last_error = f"Rate limit exceeded for {ptype.value}"
                continue

            try:
                attempted_providers.append(ptype.value)
                method = getattr(provider_instance, method_name)
                response = await method(**kwargs)

                # Record the request (whether successful or not)
                rate_limiter.record_request(ptype.value)

                if response.success:
                    response.metadata["attempted_providers"] = (
                        attempted_providers.copy()
                    )
                    return response

                # Log the error and try next provider
                logger.warning(
                    f"Provider {ptype.value} failed: {response.error}, trying next"
                )
                last_error = response.error

            except Exception as e:
                # Still record the request attempt
                rate_limiter.record_request(ptype.value)
                logger.error(f"Provider {ptype.value} exception: {e}")
                last_error = str(e)

        # All providers failed
        return LLMResponse.error_response(
            error=f"All providers failed. Last error: {last_error}",
            provider=attempted_providers[-1] if attempted_providers else "none",
            model="",
            metadata={"attempted_providers": attempted_providers},
        )

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        provider: Optional[ProviderType] = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        """
        Generate text using configured providers with optional caching.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)
            system_prompt: Optional system instructions
            provider: Optional specific provider to use
            use_cache: Whether to use LLM response cache (default True)

        Returns:
            LLMResponse: Standardized response
        """
        # Check cache first (only for deterministic prompts with low temperature)
        cache = get_llm_cache()
        provider_name = self._selected_provider_name(provider)

        if use_cache and temperature <= 0.3:
            cached = cache.get(prompt, provider=provider_name)
            if cached:
                logger.debug("LLM cache hit for text generation")
                return LLMResponse(
                    success=True,
                    content=cached,
                    provider=provider_name,
                    model="cached",
                    inference_time=0.0,
                    metadata={"cached": True, "attempted_providers": []},
                )

        # Generate fresh response
        response = await self._execute_with_fallback(
            "generate_text",
            provider=provider,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )

        # Cache successful responses
        if use_cache and response.success and response.content:
            cache.set(prompt, response.content, provider=response.provider)

        return response

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
        provider: Optional[ProviderType] = None,
    ) -> LLMResponse:
        """
        Generate code using configured providers.

        Args:
            prompt: The code generation prompt
            language: Target programming language
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)
            provider: Optional specific provider to use

        Returns:
            LLMResponse: Standardized response with generated code
        """
        return await self._execute_with_fallback(
            "generate_code",
            provider=provider,
            prompt=prompt,
            language=language,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
        provider: Optional[ProviderType] = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        """
        Analyze an image using configured providers with optional caching.

        Caching is based on both prompt AND image content hash, so identical
        images with the same prompt return cached results.

        Args:
            image_data: Raw image bytes
            prompt: Analysis prompt
            max_tokens: Maximum tokens for response
            provider: Optional specific provider to use
            use_cache: Whether to use LLM response cache (default True)

        Returns:
            LLMResponse: Standardized response
        """
        # Generate image hash for cache key
        image_hash = hash_image_data(image_data)
        provider_name = self._selected_provider_name(provider)

        # Check cache first
        cache = get_llm_cache()
        if use_cache:
            cached = cache.get(prompt, image_hash=image_hash, provider=provider_name)
            if cached:
                logger.debug(f"LLM cache hit for image analysis (hash: {image_hash})")
                return LLMResponse(
                    success=True,
                    content=cached,
                    provider=provider_name,
                    model="cached",
                    inference_time=0.0,
                    metadata={
                        "cached": True,
                        "image_hash": image_hash,
                        "attempted_providers": [],
                    },
                )

        # Generate fresh response
        response = await self._execute_with_fallback(
            "analyze_image",
            provider=provider,
            image_data=image_data,
            prompt=prompt,
            max_tokens=max_tokens,
        )

        # Cache successful responses
        if use_cache and response.success and response.content:
            cache.set(
                prompt,
                response.content,
                image_hash=image_hash,
                provider=response.provider,
            )

        return response

    async def generate_embedding(
        self,
        text: str,
        provider: Optional[ProviderType] = None,
    ) -> LLMResponse:
        """
        Generate text embeddings.

        Args:
            text: Text to embed
            provider: Optional specific provider to use

        Returns:
            LLMResponse: Response with embedding in metadata
        """
        # Embeddings have limited provider support, prefer Ollama or OpenAI
        if provider is None:
            if ProviderType.OLLAMA in self._providers:
                provider = ProviderType.OLLAMA
            elif ProviderType.OPENAI in self._providers:
                provider = ProviderType.OPENAI

        return await self._execute_with_fallback(
            "generate_embedding",
            provider=provider,
            text=text,
        )

    # =========================================================================
    # Synchronous Methods (for scanner compatibility)
    # =========================================================================

    def _response_to_dict(self, response: LLMResponse) -> Dict[str, Any]:
        """Convert LLMResponse to dict format for backward compatibility.

        This matches the format returned by the old gemini_client.generate_text_sync().
        """
        if response.success:
            result = {
                "success": True,
                "content": response.content,
                "inference_time": response.inference_time or 0.0,
                "provider": response.provider,
                "model": response.model,
            }
        else:
            result = {
                "success": False,
                "error": response.error or "Unknown error",
                "inference_time": response.inference_time or 0.0,
                "provider": response.provider,
                "model": response.model,
            }
        if response.metadata:
            result["metadata"] = response.metadata
        return result

    def generate_text_sync(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        provider: Optional[ProviderType] = None,
    ) -> Dict[str, Any]:
        """
        Synchronous version of generate_text.

        Returns dict format for backward compatibility with scanners:
        {
            "success": bool,
            "content": str (on success),
            "error": str (on failure),
            "inference_time": float,
            "provider": str,
            "model": str
        }
        """
        try:
            response = _run_async_in_thread(
                self.generate_text(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system_prompt=system_prompt,
                    provider=provider,
                )
            )
            return self._response_to_dict(response)
        except Exception as e:
            logger.error(f"generate_text_sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "inference_time": 0.0,
                "provider": "error",
                "model": "",
            }

    def generate_code_sync(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
        provider: Optional[ProviderType] = None,
    ) -> Dict[str, Any]:
        """
        Synchronous version of generate_code.

        Returns dict format for backward compatibility with scanners.
        """
        try:
            response = _run_async_in_thread(
                self.generate_code(
                    prompt=prompt,
                    language=language,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    provider=provider,
                )
            )
            return self._response_to_dict(response)
        except Exception as e:
            logger.error(f"generate_code_sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "inference_time": 0.0,
                "provider": "error",
                "model": "",
            }

    def analyze_image_sync(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
        provider: Optional[ProviderType] = None,
    ) -> Dict[str, Any]:
        """
        Synchronous version of analyze_image.

        Returns dict format for backward compatibility with scanners.
        """
        try:
            response = _run_async_in_thread(
                self.analyze_image(
                    image_data=image_data,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    provider=provider,
                )
            )
            return self._response_to_dict(response)
        except Exception as e:
            logger.error(f"analyze_image_sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "inference_time": 0.0,
                "provider": "error",
                "model": "",
            }

    def health_check(self) -> Dict[str, Any]:
        """Check health of all providers."""
        result = {
            "status": "healthy",
            "primary_provider": (
                self.primary_type.value if self.primary_type is not None else None
            ),
            "fallback_provider": (
                self.fallback_type.value if self.fallback_type else None
            ),
            "providers": {},
        }

        healthy: set[ProviderType] = set()
        for ptype, provider in self._providers.items():
            health = provider.health_check()
            result["providers"][ptype.value] = health
            if health.get("status") == "healthy":
                healthy.add(ptype)

        if not healthy:
            result["status"] = "unhealthy"
        elif self.primary_type is not None and self.primary_type not in healthy:
            result["status"] = "degraded"
        elif (
            self.fallback_type is not None
            and self.fallback_type != self.primary_type
            and self.fallback_type not in healthy
        ):
            result["status"] = "degraded"

        return result

    def set_primary_provider(self, provider_type: ProviderType) -> bool:
        """
        Change the primary provider.

        Args:
            provider_type: New primary provider

        Returns:
            bool: True if provider is available
        """
        if (
            provider_type in self._providers
            and self._providers[provider_type].is_available
        ):
            self.primary_type = provider_type
            logger.info(f"Primary provider changed to {provider_type.value}")
            return True
        return False

    def set_fallback_provider(self, provider_type: Optional[ProviderType]) -> bool:
        """
        Change the fallback provider.

        Args:
            provider_type: New fallback provider (or None to disable fallback)

        Returns:
            bool: True if provider is available (or None was passed)
        """
        if provider_type is None:
            self.fallback_type = None
            logger.info("Fallback provider disabled")
            return True

        if (
            provider_type in self._providers
            and self._providers[provider_type].is_available
        ):
            self.fallback_type = provider_type
            logger.info(f"Fallback provider changed to {provider_type.value}")
            return True
        return False

    async def add_provider(
        self,
        provider_type: ProviderType,
        config: ProviderConfig,
    ) -> bool:
        """
        Add or update a provider with new configuration.

        Args:
            provider_type: Provider type
            config: Provider configuration

        Returns:
            bool: True if provider was successfully initialized
        """
        # Close existing provider if any
        if provider_type in self._providers:
            await self._providers[provider_type].close()

        self.configs[provider_type] = config
        provider = self._create_provider(provider_type)

        if await provider.initialize():
            self._providers[provider_type] = provider
            logger.info(f"Provider {provider_type.value} added/updated successfully")
            return True

        logger.warning(f"Failed to initialize provider {provider_type.value}")
        return False


# Global instance
_provider_manager: Optional[ProviderManager] = None


def get_provider_manager() -> ProviderManager:
    """Get the global provider manager instance."""
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = ProviderManager()
    return _provider_manager


async def initialize_provider_manager() -> bool:
    """Initialize the global provider manager."""
    manager = get_provider_manager()
    return await manager.initialize()


async def close_provider_manager() -> None:
    """Close the global provider manager."""
    global _provider_manager
    if _provider_manager:
        await _provider_manager.close()
        _provider_manager = None
