"""
Anthropic LLM Provider.

Implements the LLMProvider interface for Anthropic's Claude API.
Allows users to use their own Anthropic API keys.
"""

import base64
import time
import httpx
import logging
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude API provider.

    Supports text generation, code generation, and vision capabilities
    via the Anthropic API.
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        """
        Initialize Anthropic provider.

        Args:
            config: Optional provider configuration.
        """
        super().__init__()

        if config is None:
            config = ProviderConfig.default_for_provider(ProviderType.ANTHROPIC)

        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base or "https://api.anthropic.com/v1"
        self.text_model = config.text_model or "claude-3-5-sonnet-20241022"
        self.code_model = config.code_model or "claude-3-5-sonnet-20241022"
        self.vision_model = config.vision_model or "claude-3-5-sonnet-20241022"
        self.timeout = config.timeout

        # Anthropic API version
        self.api_version = "2023-06-01"

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic Claude"

    @property
    def capabilities(self) -> ProviderCapability:
        return (
            ProviderCapability.TEXT_GENERATION
            | ProviderCapability.CODE_GENERATION
            | ProviderCapability.VISION
            | ProviderCapability.STREAMING
        )

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def is_local(self) -> bool:
        return False

    async def initialize(self) -> bool:
        """Initialize Anthropic provider."""
        if not self.api_key:
            logger.warning("Anthropic API key not configured")
            return False

        self._initialized = True
        logger.info(f"Anthropic provider initialized with model: {self.text_model}")
        return True

    async def close(self) -> None:
        """Close Anthropic provider."""
        self._initialized = False

    def _build_content(
        self,
        prompt: str,
        image_data: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """Build content array for Anthropic API."""
        content = []

        if image_data:
            # Add image first
            image_b64 = base64.b64encode(image_data).decode("utf-8")
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                }
            )

        content.append({"type": "text", "text": prompt})
        return content

    async def _call_api(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, float, Optional[Dict[str, int]]]:
        """Make async call to Anthropic API."""
        start_time = time.perf_counter()

        try:
            request_body = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            if system_prompt:
                request_body["system"] = system_prompt

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": self.api_version,
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                    timeout=float(self.timeout),
                )

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.warning(
                    f"Anthropic API error: {response.status_code} - {error_detail}"
                )
                return f"ERROR: {response.status_code} - {error_detail}", elapsed, None

            data = response.json()
            content_blocks = data.get("content", [])
            usage = data.get("usage", {})

            # Extract text from content blocks
            text_parts = []
            for block in content_blocks:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

            content = "\n".join(text_parts).strip()
            return content, elapsed, usage

        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            return f"ERROR: Request timed out after {self.timeout}s", elapsed, None
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Anthropic API exception: {e}")
            return f"ERROR: {e}", elapsed, None

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text using Anthropic Claude."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Anthropic API key not configured",
                provider=self.name,
                model=self.text_model,
            )

        messages = [{"role": "user", "content": prompt}]
        content, elapsed, usage = await self._call_api(
            self.text_model, messages, max_tokens, temperature, system_prompt
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.text_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.text_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("input_tokens")
            response.completion_tokens = usage.get("output_tokens")
            response.total_tokens = (
                (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            ) or None

        return response

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Generate code using Anthropic Claude."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Anthropic API key not configured",
                provider=self.name,
                model=self.code_model,
            )

        system_prompt = f"You are an expert {language} developer. Generate clean, well-documented code."
        messages = [{"role": "user", "content": prompt}]
        content, elapsed, usage = await self._call_api(
            self.code_model, messages, max_tokens, temperature, system_prompt
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.code_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.code_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("input_tokens")
            response.completion_tokens = usage.get("output_tokens")

        return response

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Analyze an image using Anthropic Claude Vision."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Anthropic API key not configured",
                provider=self.name,
                model=self.vision_model,
            )

        content = self._build_content(prompt, image_data)
        messages = [{"role": "user", "content": content}]
        result, elapsed, usage = await self._call_api(
            self.vision_model, messages, max_tokens, temperature=0.3
        )

        if result.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=result,
                provider=self.name,
                model=self.vision_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=result,
            provider=self.name,
            model=self.vision_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("input_tokens")
            response.completion_tokens = usage.get("output_tokens")

        return response

    def health_check(self) -> Dict[str, Any]:
        """Check Anthropic provider health."""
        if not self.api_key:
            return {
                "status": "unhealthy",
                "provider": self.name,
                "error": "API key not configured",
            }

        return {
            "status": "healthy",
            "provider": self.name,
            "display_name": self.display_name,
            "text_model": self.text_model,
            "code_model": self.code_model,
            "vision_model": self.vision_model,
            "api_base": self.api_base,
            "is_local": self.is_local,
        }

    def get_available_models(self) -> List[str]:
        """Get available Anthropic models."""
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-5-haiku-20241022",
            "claude-3-haiku-20240307",
        ]
