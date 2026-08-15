"""
Ollama API Client for AI-powered text generation

This module provides a clean interface to interact with Ollama for:
- ARIA label generation for mathematical equations
- Image alt text generation
- Natural language descriptions
"""

import asyncio
import httpx
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class OllamaClient:
    """Client for interacting with Ollama API"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        """
        Initialize Ollama client

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
        """
        self.base_url = base_url
        self.timeout = 30.0  # 30 second timeout

    async def generate(
        self,
        prompt: str,
        model: str = "qwen2.5:0.5b",
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 200,
    ) -> Optional[str]:
        """
        Generate text using Ollama

        Args:
            prompt: The input prompt
            model: Model name (default: qwen2.5:0.5b for speed)
            system_prompt: Optional system prompt for context
            temperature: Creativity level (0.0-1.0, lower = more deterministic)
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text or None if failed
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system_prompt:
            payload["system"] = system_prompt

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/api/generate", json=payload
                    )

                    if response.status_code != 200:
                        logger.error(
                            f"Ollama API error: {response.status_code} - {response.text}"
                        )
                        return None

                    result = response.json()
                    generated_text = result.get("response", "").strip()

                    logger.info(
                        f"Ollama generated {len(generated_text)} chars for prompt: {prompt[:50]}..."
                    )
                    return generated_text

            except _RETRYABLE_EXCEPTIONS as e:
                last_exc = e
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    f"Ollama request failed (attempt {attempt + 1}/{_MAX_RETRIES}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Ollama generation failed: {str(e)}", exc_info=True)
                return None

        logger.error(f"Ollama request failed after {_MAX_RETRIES} attempts: {last_exc}")
        return None

    def generate_sync(
        self,
        prompt: str,
        model: str = "qwen2.5:0.5b",
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 200,
    ) -> Optional[str]:
        """
        Synchronous version of generate() for non-async contexts

        Args:
            prompt: The input prompt
            model: Model name (default: qwen2.5:0.5b)
            system_prompt: Optional system prompt
            temperature: Creativity level (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text or None if failed
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system_prompt:
            payload["system"] = system_prompt

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        f"{self.base_url}/api/generate", json=payload
                    )

                    if response.status_code != 200:
                        logger.error(
                            f"Ollama API error: {response.status_code} - {response.text}"
                        )
                        return None

                    result = response.json()
                    generated_text = result.get("response", "").strip()

                    logger.info(
                        f"Ollama generated {len(generated_text)} chars for prompt: {prompt[:50]}..."
                    )
                    return generated_text

            except _RETRYABLE_EXCEPTIONS as e:
                last_exc = e
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    f"Ollama request failed (attempt {attempt + 1}/{_MAX_RETRIES}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            except Exception as e:
                logger.error(f"Ollama generation failed: {str(e)}", exc_info=True)
                return None

        logger.error(f"Ollama request failed after {_MAX_RETRIES} attempts: {last_exc}")
        return None

    async def check_health(self) -> bool:
        """
        Check if Ollama is running and responsive

        Returns:
            True if Ollama is healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Ollama health check failed: {str(e)}")
            return False

    def check_health_sync(self) -> bool:
        """
        Synchronous health check

        Returns:
            True if Ollama is healthy, False otherwise
        """
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Ollama health check failed: {str(e)}")
            return False


class MathDescriptionGenerator:
    """Generate natural language descriptions of mathematical expressions"""

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        """
        Initialize math description generator

        Args:
            ollama_url: Ollama API URL
        """
        self.ollama = OllamaClient(base_url=ollama_url)
        self.system_prompt = (
            "You are a mathematical notation expert. Your job is to describe LaTeX equations "
            "in clear, natural language for screen reader users. Be concise but complete. "
            "Use plain English, not LaTeX syntax. Keep descriptions under 50 words."
        )

    def generate_description(self, latex: str) -> Optional[str]:
        """
        Generate natural language description of a LaTeX equation

        Args:
            latex: LaTeX source code (e.g., "\\frac{a}{b}")

        Returns:
            Natural language description or None if failed

        Examples:
            Input: "\\frac{a}{b}"
            Output: "The fraction a over b"

            Input: "\\int_{0}^{\\infty} e^{-x} dx"
            Output: "The integral from 0 to infinity of e to the power of negative x with respect to x"

            Input: "x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}"
            Output: "The quadratic formula: x equals negative b plus or minus the square root of b squared minus 4ac, all divided by 2a"
        """
        prompt = f"""Describe this LaTeX equation in natural language for a screen reader user:

LaTeX: {latex}

Description:"""

        description = self.ollama.generate_sync(
            prompt=prompt,
            system_prompt=self.system_prompt,
            model="qwen2.5:0.5b",
            temperature=0.2,  # Low temperature for consistency
            max_tokens=100,
        )

        if description:
            # Clean up description (remove quotes, extra whitespace)
            description = description.strip("\"'").strip()
            # Limit to one sentence if multiple exist
            if "." in description:
                description = description.split(".")[0] + "."

        return description

    def generate_description_with_fallback(self, latex: str, fallback: str) -> str:
        """
        Generate description with fallback to heuristic if Ollama fails

        Args:
            latex: LaTeX source code
            fallback: Fallback description (heuristic-based)

        Returns:
            Natural language description (Ollama or fallback)
        """
        description = self.generate_description(latex)

        if description and len(description) > 10:  # Valid description
            return description
        else:
            logger.warning(f"Ollama failed for {latex[:30]}, using fallback")
            return fallback
