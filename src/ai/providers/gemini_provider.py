"""
Google Gemini LLM Provider.

Implements the LLMProvider interface for Google's Gemini API.
"""

import base64
import time
import httpx
import logging
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """
    Google Gemini API provider.

    Supports text generation, code generation, and vision capabilities
    via the Gemini API.
    """

    # Models that use thinking mode and need higher token limits
    THINKING_MODELS = ["gemini-2.5", "gemini-3"]

    def __init__(self, config: Optional[ProviderConfig] = None):
        """
        Initialize Gemini provider.

        Args:
            config: Optional provider configuration. If not provided,
                   will use defaults from environment variables.
        """
        super().__init__()

        if config is None:
            config = ProviderConfig.default_for_provider(ProviderType.GEMINI)

        self.config = config
        self.api_key = config.api_key
        self.api_base = (
            config.api_base or "https://generativelanguage.googleapis.com/v1beta"
        )
        self.text_model = config.text_model or "gemini-2.5-flash"
        self.code_model = config.code_model or "gemini-2.5-flash"
        self.vision_model = config.vision_model or "gemini-2.5-flash-image"
        self.timeout = config.timeout

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def display_name(self) -> str:
        return "Google Gemini"

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
        """Initialize Gemini provider."""
        if not self.api_key:
            logger.warning("Gemini API key not configured")
            return False

        self._initialized = True
        logger.info(
            f"Gemini provider initialized with models: text={self.text_model}, code={self.code_model}"
        )
        return True

    async def close(self) -> None:
        """Close Gemini provider."""
        self._initialized = False

    def _is_thinking_model(self, model: str) -> bool:
        """Check if model uses thinking mode (needs higher token limits)."""
        return any(t in model for t in self.THINKING_MODELS)

    def _build_contents(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        image_data: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """Build contents array for Gemini API."""
        contents = []

        if system_prompt:
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": f"System instructions: {system_prompt}"}],
                }
            )
            contents.append(
                {
                    "role": "model",
                    "parts": [
                        {"text": "Understood. I will follow these instructions."}
                    ],
                }
            )

        # Build user message parts
        parts = [{"text": prompt}]

        if image_data:
            # Add image as inline data
            parts.insert(
                0,
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",  # Assume JPEG, could detect
                        "data": base64.b64encode(image_data).decode("utf-8"),
                    }
                },
            )

        contents.append({"role": "user", "parts": parts})
        return contents

    async def _call_api(
        self,
        model: str,
        contents: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: float = 0.3,
    ) -> tuple[str, float]:
        """Make async call to Gemini API."""
        # Thinking models need higher token limits
        if self._is_thinking_model(model):
            max_tokens = max(max_tokens, 2000)

        start_time = time.perf_counter()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/models/{model}:generateContent",
                    params={"key": self.api_key},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens,
                        },
                    },
                    timeout=float(self.timeout),
                )

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.warning(
                    f"Gemini API error: {response.status_code} - {error_detail}"
                )
                return f"ERROR: {response.status_code} - {error_detail}", elapsed

            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    content = parts[0].get("text", "")
                    return content.strip(), elapsed

            return "ERROR: No content in response", elapsed

        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Gemini API timeout after {elapsed:.1f}s")
            return f"ERROR: Request timed out after {self.timeout}s", elapsed
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Gemini API exception: {e}")
            return f"ERROR: {e}", elapsed

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text using Gemini."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Gemini API key not configured",
                provider=self.name,
                model=self.text_model,
            )

        contents = self._build_contents(prompt, system_prompt)
        content, elapsed = await self._call_api(
            self.text_model, contents, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.text_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.text_model,
            inference_time=elapsed,
        )

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Generate code using Gemini."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Gemini API key not configured",
                provider=self.name,
                model=self.code_model,
            )

        system_prompt = f"You are an expert {language} developer. Generate clean, well-documented code."
        contents = self._build_contents(prompt, system_prompt)
        content, elapsed = await self._call_api(
            self.code_model, contents, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.code_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.code_model,
            inference_time=elapsed,
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Analyze an image using Gemini Vision."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="Gemini API key not configured",
                provider=self.name,
                model=self.vision_model,
            )

        contents = self._build_contents(prompt, image_data=image_data)
        content, elapsed = await self._call_api(
            self.vision_model, contents, max_tokens, temperature=0.3
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.vision_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.vision_model,
            inference_time=elapsed,
        )

    def health_check(self) -> Dict[str, Any]:
        """Check Gemini provider health."""
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
        """Get available Gemini models."""
        return [
            "gemini-2.5-flash",
            "gemini-2.5-flash-image",
            "gemini-2.5-pro",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]
