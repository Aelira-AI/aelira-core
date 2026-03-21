"""
xAI (Grok) LLM Provider.

Implements the LLMProvider interface for xAI's Grok API.
Allows users to use their own xAI API keys.

Note: xAI uses an OpenAI-compatible API format.
"""

import base64
import time
import httpx
import logging
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


class XAIProvider(LLMProvider):
    """
    xAI Grok API provider.

    Supports text generation, code generation, and vision capabilities
    via the xAI API (OpenAI-compatible format).
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        """
        Initialize xAI provider.

        Args:
            config: Optional provider configuration.
        """
        super().__init__()

        if config is None:
            config = ProviderConfig.default_for_provider(ProviderType.XAI)

        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base or "https://api.x.ai/v1"
        self.text_model = config.text_model or "grok-2"
        self.code_model = config.code_model or "grok-2"
        self.vision_model = config.vision_model or "grok-2-vision"
        self.timeout = config.timeout

    @property
    def name(self) -> str:
        return "xai"

    @property
    def display_name(self) -> str:
        return "xAI Grok"

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
        """Initialize xAI provider."""
        if not self.api_key:
            logger.warning("xAI API key not configured")
            return False

        self._initialized = True
        logger.info(f"xAI provider initialized with models: text={self.text_model}")
        return True

    async def close(self) -> None:
        """Close xAI provider."""
        self._initialized = False

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        image_data: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """Build messages array for xAI API (OpenAI-compatible format)."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if image_data:
            # Vision request
            image_b64 = base64.b64encode(image_data).decode("utf-8")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                            },
                        },
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        return messages

    async def _call_api(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: float = 0.3,
    ) -> tuple[str, float, Optional[Dict[str, int]]]:
        """Make async call to xAI API."""
        start_time = time.perf_counter()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    timeout=float(self.timeout),
                )

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.warning(
                    f"xAI API error: {response.status_code} - {error_detail}"
                )
                return f"ERROR: {response.status_code} - {error_detail}", elapsed, None

            data = response.json()
            choices = data.get("choices", [])
            usage = data.get("usage", {})

            if choices:
                content = choices[0].get("message", {}).get("content", "")
                return content.strip(), elapsed, usage

            return "ERROR: No content in response", elapsed, None

        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            return f"ERROR: Request timed out after {self.timeout}s", elapsed, None
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"xAI API exception: {e}")
            return f"ERROR: {e}", elapsed, None

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text using xAI Grok."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="xAI API key not configured",
                provider=self.name,
                model=self.text_model,
            )

        messages = self._build_messages(prompt, system_prompt)
        content, elapsed, usage = await self._call_api(
            self.text_model, messages, max_tokens, temperature
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
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Generate code using xAI Grok."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="xAI API key not configured",
                provider=self.name,
                model=self.code_model,
            )

        system_prompt = f"You are an expert {language} developer. Generate clean, well-documented code."
        messages = self._build_messages(prompt, system_prompt)
        content, elapsed, usage = await self._call_api(
            self.code_model, messages, max_tokens, temperature
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
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Analyze an image using xAI Grok Vision."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="xAI API key not configured",
                provider=self.name,
                model=self.vision_model,
            )

        messages = self._build_messages(prompt, image_data=image_data)
        content, elapsed, usage = await self._call_api(
            self.vision_model, messages, max_tokens, temperature=0.3
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.vision_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.vision_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    def health_check(self) -> Dict[str, Any]:
        """Check xAI provider health."""
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
        """Get available xAI models."""
        return [
            "grok-2",
            "grok-2-vision",
            "grok-2-mini",
        ]
