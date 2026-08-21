"""Image alt text generation using Gemini API (primary) with Ollama fallback."""

import base64
import asyncio
from functools import wraps
import time
import os
import httpx
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Any, List, Optional
from PIL import Image

from src.config.settings import get_settings
from src.education.alt_text_quality import normalize_usable_alt_text

logger = logging.getLogger(__name__)


def _tracked_analysis(method):
    """Reset request-local usage once and aggregate nested analysis calls."""

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        outermost = self._analysis_depth == 0
        if outermost:
            self._reset_usage_metadata()
        self._analysis_depth += 1
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._analysis_depth -= 1

    return wrapped


class ImageAltTextGenerator:
    """Generate accessible alt text through an explicitly selected transport."""

    def __init__(self, lms_client=None, *, allow_legacy_transport: bool = False):
        """Create a generator without implicitly enabling any provider.

        LMS callers inject a purpose-bound compatibility client. Legacy
        non-LMS callers must opt in explicitly to the historical Gemini/
        Ollama transport.
        """
        self.lms_client = lms_client
        self.settings = get_settings()
        self.allow_legacy_transport = allow_legacy_transport
        self.gemini_api_key = None
        self.gemini_api_base = ""
        self.vision_model = ""
        self.use_gemini = False
        self.ollama_host = ""
        self.ollama_fallback = ""
        self._analysis_depth = 0
        self._usage = {}
        self._reset_usage_metadata()
        if allow_legacy_transport:
            self.gemini_api_key = self.settings.gemini_api_key
            self.gemini_api_base = self.settings.gemini_api_base
            self.vision_model = self.settings.gemini_vision_model
            self.use_gemini = self.settings.use_gemini and bool(self.gemini_api_key)
            self.ollama_host = self.settings.ollama_host
            self.ollama_fallback = self.settings.ollama_fallback_vision

    def _reset_usage_metadata(self) -> None:
        self._usage = {
            "ai_used": False,
            "external_ai_used": False,
            "providers_attempted": [],
            "provider": None,
            "model": None,
            "outcome": "allowed_not_used",
        }

    @property
    def usage_metadata(self):
        """Return a bounded, immutable snapshot of the latest public analysis."""
        return MappingProxyType(
            {
                **self._usage,
                "providers_attempted": tuple(self._usage["providers_attempted"]),
            }
        )

    def _record_attempt(self, provider: str, *, external: bool) -> None:
        if provider not in {"gemini", "ollama", "anthropic", "openai", "xai", "local"}:
            provider = "unknown"
        if provider != "unknown" and provider not in self._usage["providers_attempted"]:
            self._usage["providers_attempted"].append(provider)
        self._usage["external_ai_used"] = self._usage["external_ai_used"] or external
        self._usage["provider"] = provider if provider != "unknown" else None
        self._usage["outcome"] = "attempted_failed"

    def _record_result(self, *, provider: str, model: Any, success: bool) -> None:
        self._usage["provider"] = provider if provider != "unknown" else None
        self._usage["model"] = (
            model
            if isinstance(model, str)
            and 0 < len(model) <= 200
            and model.isprintable()
            and "\x00" not in model
            else None
        )
        if success:
            self._usage["ai_used"] = True
            self._usage["outcome"] = "used"

    async def _generate_vision(
        self, image_path: str, prompt: str, max_tokens: int = 300
    ) -> tuple[str, float, str, str]:
        """Dispatch vision through the injected LMS client or explicit legacy path."""
        if self.lms_client is not None:
            bound_provider = getattr(self.lms_client, "provider", None)
            provider = (
                bound_provider.casefold()
                if isinstance(bound_provider, str)
                and bound_provider.casefold()
                in {"gemini", "ollama", "anthropic", "openai", "xai", "local"}
                else "unknown"
            )
            self._record_attempt(provider, external=provider not in {"ollama", "local"})
            try:
                image_data = Path(image_path).read_bytes()
                result = await asyncio.to_thread(
                    self.lms_client.analyze_image_sync,
                    image_data=image_data,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
            except Exception:
                self._record_result(provider=provider, model=None, success=False)
                return "ERROR: provider_call_failed", 0.0, "none", ""
            if not isinstance(result, dict):
                self._record_result(provider=provider, model=None, success=False)
                return "ERROR: invalid_provider_response", 0.0, provider, ""
            if (
                result.get("success") is False
                and result.get("ai_used") is False
                and result.get("external_ai_used") is False
                and result.get("purpose_outcome") == "denied_at_dispatch"
                and provider != "unknown"
            ):
                self._usage.update(
                    {
                        "ai_used": False,
                        "external_ai_used": False,
                        "providers_attempted": [],
                        "provider": provider,
                        "model": None,
                        "outcome": "denied_at_dispatch",
                    }
                )
                return (
                    f"ERROR: {result.get('error', 'policy_denied')}",
                    result.get("inference_time", 0.0),
                    provider,
                    "",
                )
            if provider == "unknown":
                result_provider = result.get("provider")
                if isinstance(result_provider, str) and result_provider.casefold() in {
                    "gemini",
                    "ollama",
                    "anthropic",
                    "openai",
                    "xai",
                    "local",
                }:
                    provider = result_provider.casefold()
            model = result.get("model", "")
            elapsed = result.get("inference_time", 0.0)
            if not result.get("success"):
                self._record_result(provider=provider, model=model, success=False)
                return (
                    f"ERROR: {result.get('error', 'provider_call_failed')}",
                    elapsed,
                    provider,
                    model,
                )
            content = result.get("content")
            if not isinstance(content, str) or not content.strip():
                self._record_result(provider=provider, model=model, success=False)
                return "ERROR: invalid_provider_response", elapsed, provider, model
            self._record_result(provider=provider, model=model, success=True)
            return content.strip(), elapsed, provider, model

        if not self.allow_legacy_transport:
            return "ERROR: AI transport not authorized", 0.0, "none", ""

        provider = "gemini"
        if self.use_gemini:
            self._record_attempt("gemini", external=True)
            content, elapsed = await self._generate_with_gemini(
                image_path, prompt, max_tokens=max_tokens
            )
            if not content.startswith("ERROR:"):
                self._record_result(
                    provider="gemini", model=self.vision_model, success=True
                )
                return content, elapsed, provider, self.vision_model
            logger.warning("Gemini vision failed; trying explicit legacy Ollama")
        provider = "ollama"
        self._record_attempt("ollama", external=False)
        content, elapsed = await self._generate_with_ollama(image_path, prompt)
        self._record_result(
            provider="ollama",
            model=self.ollama_fallback,
            success=not content.startswith("ERROR:"),
        )
        return content, elapsed, provider, self.ollama_fallback

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64 for API calls."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def _get_mime_type(self, image_path: str) -> str:
        """Get MIME type based on file extension."""
        ext = Path(image_path).suffix.lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        if ext not in mime_types:
            raise ValueError("Unsupported image suffix")
        return mime_types[ext]

    def _validate_image(
        self,
        image_path: str,
        *,
        trusted_mime_type: Optional[str] = None,
        trusted_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate image file and get metadata."""
        try:
            if not os.path.exists(image_path):
                return {"valid": False, "error": "File not found"}

            ext = Path(image_path).suffix.lower()
            format_info = {
                "PNG": ("image/png", {".png"}, ".png"),
                "JPEG": ("image/jpeg", {".jpg", ".jpeg"}, ".jpg"),
                "GIF": ("image/gif", {".gif"}, ".gif"),
                "WEBP": ("image/webp", {".webp"}, ".webp"),
                "BMP": ("image/bmp", {".bmp"}, ".bmp"),
            }
            supported_suffixes = {
                suffix for _, suffixes, _ in format_info.values() for suffix in suffixes
            }
            if not ext:
                return {"valid": False, "error": "Unsupported format: suffixless"}
            if ext not in supported_suffixes:
                return {"valid": False, "error": f"Unsupported format: {ext}"}

            file_size = os.path.getsize(image_path)
            max_file_size = getattr(
                self.settings, "max_file_size_image", 10 * 1024 * 1024
            )
            if file_size > max_file_size:
                return {"valid": False, "error": "Image too large"}

            with Image.open(image_path) as img:
                format_name = img.format
                img.verify()

            if format_name not in format_info:
                return {"valid": False, "error": "Unsupported detected image format"}
            detected_mime, allowed_suffixes, canonical_suffix = format_info[format_name]
            if ext not in allowed_suffixes:
                return {"valid": False, "error": "Image suffix does not match content"}
            if trusted_suffix and trusted_suffix.lower() not in allowed_suffixes:
                return {
                    "valid": False,
                    "error": "Trusted suffix does not match content",
                }
            if trusted_mime_type and trusted_mime_type.casefold() != detected_mime:
                return {"valid": False, "error": "Trusted MIME does not match content"}

            with Image.open(image_path) as img:
                frame_count = getattr(img, "n_frames", 1)
                if type(frame_count) is not int or frame_count != 1:
                    return {
                        "valid": False,
                        "error": "Animated or multi-frame images are not supported",
                    }
                width, height = img.size
            max_pixels = getattr(self.settings, "max_image_pixels", 40_000_000)
            if width <= 0 or height <= 0 or width * height > max_pixels:
                return {"valid": False, "error": "Image pixel limit exceeded"}

            return {
                "valid": True,
                "width": width,
                "height": height,
                "format": format_name,
                "size_bytes": file_size,
                "content_type": detected_mime,
                "suffix": canonical_suffix,
            }

        except Exception as e:
            return {"valid": False, "error": f"Image validation failed: {str(e)}"}

    async def _generate_with_gemini(
        self, image_path: str, prompt: str, max_tokens: int = 300
    ) -> tuple[str, float]:
        """Generate alt text using Gemini API."""
        image_data = self._encode_image(image_path)
        mime_type = self._get_mime_type(image_path)

        start_time = time.perf_counter()

        # The vision endpoint returns 503 under load often enough to matter:
        # measured locally, one call in two failed while the next succeeded
        # twenty seconds later. Without a retry a transient refusal looks
        # exactly like an image nothing could describe, and the caller gives
        # up on an image that was perfectly describable.
        attempts = 3
        backoff = 2.0
        response = None

        try:
            for attempt in range(attempts):
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.gemini_api_base}/models/{self.vision_model}:generateContent",
                        # Header, not a query param: httpx logs the full request URL
                        # at INFO, so a key in the query string is written verbatim
                        # to stdout, Loki and any Sentry breadcrumb.
                        headers={"x-goog-api-key": self.gemini_api_key},
                        json={
                            "contents": [
                                {
                                    "parts": [
                                        {"text": prompt},
                                        {
                                            "inline_data": {
                                                "mime_type": mime_type,
                                                "data": image_data,
                                            }
                                        },
                                    ]
                                }
                            ],
                            "generationConfig": {
                                "temperature": 0.3,
                                "maxOutputTokens": max_tokens,
                                # gemini-2.5+ thinking tokens count against
                                # maxOutputTokens; without this the model can spend
                                # the whole budget reasoning and return a truncated
                                # fragment as alt text.
                                "thinkingConfig": {"thinkingBudget": 0},
                            },
                        },
                        timeout=60.0,
                    )

                if response.status_code not in (429, 500, 502, 503, 504):
                    break
                if attempt < attempts - 1:
                    logger.info(
                        "Vision API answered %s, retrying in %.0fs",
                        response.status_code,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.warning(
                    f"Gemini API error: {response.status_code} - {error_detail}"
                )
                return f"ERROR: {response.status_code}", elapsed

            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                finish_reason = candidates[0].get("finishReason")
                if finish_reason == "MAX_TOKENS":
                    # Incomplete text — surfacing it as alt text ships a
                    # mid-sentence fragment to users. Fail so callers fall
                    # back (Ollama / human review).
                    usage = data.get("usageMetadata", {})
                    logger.warning(
                        f"Gemini hit MAX_TOKENS (maxOutputTokens={max_tokens}, "
                        f"thoughts={usage.get('thoughtsTokenCount')}, "
                        f"output={usage.get('candidatesTokenCount')}) — "
                        "discarding truncated alt text"
                    )
                    return "ERROR: MAX_TOKENS — truncated output discarded", elapsed
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    content = parts[0].get("text", "")
                    return content.strip(), elapsed

            return "ERROR: No content in response", elapsed

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Gemini API exception: {e}")
            return f"ERROR: {e}", elapsed

    async def _generate_with_ollama(
        self, image_path: str, prompt: str
    ) -> tuple[str, float]:
        """Generate alt text using Ollama as fallback."""
        start_time = time.perf_counter()

        try:
            import ollama

            response = ollama.chat(
                model=self.ollama_fallback,
                messages=[{"role": "user", "content": prompt, "images": [image_path]}],
                options={
                    "temperature": 0.5,
                    "num_predict": 500,
                },
            )

            elapsed = time.perf_counter() - start_time
            content = response["message"]["content"].strip()
            return content, elapsed

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Ollama fallback error: {e}")
            return f"ERROR: {e}", elapsed

    @_tracked_analysis
    async def generate_alt_text(
        self,
        image_path: str,
        context: str = None,
        educational_context: bool = True,
        *,
        trusted_mime_type: Optional[str] = None,
        trusted_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate accessible alt text for an image.

        Args:
            image_path: Path to image file
            context: Optional context about where image appears
            educational_context: Whether this is for educational materials

        Returns:
            Dict with alt_text, description, inference_time, and metadata
        """
        # Validate image
        validation = self._validate_image(
            image_path,
            trusted_mime_type=trusted_mime_type,
            trusted_suffix=trusted_suffix,
        )
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "inference_time": 0,
                "provider": "none",
            }

        # Build prompt
        context_info = f"\n\nContext: {context}" if context else ""

        if educational_context:
            prompt = f"""Describe this image for a screen reader user who cannot see it.
If it contains text, diagrams, charts, equations, or code, describe the content clearly.
Be concise but accurate (1-3 sentences, under 150 characters for simple images, longer for complex content).
Focus on what's important for understanding the educational content.{context_info}"""
        else:
            prompt = f"""Describe this image for a screen reader user who cannot see it.
Be concise but accurate (1-2 sentences, under 125 characters).
Focus on the main visual elements and purpose.{context_info}"""

        alt_text, elapsed, provider, model = await self._generate_vision(
            image_path, prompt
        )

        # Check for errors
        if alt_text.startswith("ERROR:"):
            return {
                "success": False,
                "error": alt_text.removeprefix("ERROR: "),
                "inference_time": elapsed,
                "provider": provider,
            }

        alt_text = normalize_usable_alt_text(alt_text)
        if alt_text is None:
            return {
                "success": False,
                "error": "Unusable or incomplete alt text response",
                "inference_time": elapsed,
                "provider": provider,
            }

        return {
            "alt_text": alt_text,
            "long_description": alt_text,
            "image_type": "Photo",
            "educational_value": "Essential",
            "contains_text": False,
            "text_content": "",
            "success": True,
            "inference_time": elapsed,
            "provider": provider,
            "model": model,
            "image_metadata": {
                "width": validation.get("width"),
                "height": validation.get("height"),
                "format": validation.get("format"),
                "size_bytes": validation.get("size_bytes"),
            },
        }

    @_tracked_analysis
    async def batch_generate_alt_text(
        self,
        image_paths: List[str],
        context: str = None,
        educational_context: bool = True,
    ) -> Dict[str, Any]:
        """Generate alt text for multiple images.

        Args:
            image_paths: List of image file paths
            context: Optional context for all images
            educational_context: Whether these are educational materials

        Returns:
            Dict with results list and summary statistics
        """
        results = []
        total_time = 0
        success_count = 0
        failed_count = 0

        for image_path in image_paths:
            result = await self.generate_alt_text(
                image_path=image_path,
                context=context,
                educational_context=educational_context,
            )

            results.append({"image_path": image_path, "result": result})

            total_time += result.get("inference_time", 0)
            if result.get("success"):
                success_count += 1
            else:
                failed_count += 1

        return {
            "total_images": len(image_paths),
            "success_count": success_count,
            "failed_count": failed_count,
            "total_inference_time": total_time,
            "average_time_per_image": (
                total_time / len(image_paths) if image_paths else 0
            ),
            "results": results,
        }

    @_tracked_analysis
    async def validate_alt_text(
        self, image_path: str, existing_alt_text: str, context: str = None
    ) -> Dict[str, Any]:
        """Validate if existing alt text accurately describes the image.

        Args:
            image_path: Path to image file
            existing_alt_text: The current alt text to validate
            context: Optional context about where image appears

        Returns:
            Dict with validation results:
                - is_accurate: bool - whether alt text matches image
                - accuracy_score: float (0-1) - how well it matches
                - issues: List[str] - specific problems found
                - suggested_improvement: str - better alt text if needed
                - reasoning: str - explanation of the validation
        """
        # Validate image
        validation = self._validate_image(image_path)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "inference_time": 0,
                "provider": "none",
            }

        # Build validation prompt
        context_info = f"\n\nContext where image appears: {context}" if context else ""

        prompt = f"""Analyze this image and evaluate if the following alt text accurately describes it.

EXISTING ALT TEXT: "{existing_alt_text}"
{context_info}

Evaluate the alt text on these criteria:
1. ACCURACY: Does it correctly describe what's in the image?
2. COMPLETENESS: Does it capture the important visual information?
3. RELEVANCE: Is it appropriate for the context (not too verbose, not too brief)?
4. ACCESSIBILITY: Would a screen reader user understand the image's purpose?

Respond in this exact JSON format:
{{
    "is_accurate": true/false,
    "accuracy_score": 0.0-1.0,
    "issues": ["issue1", "issue2"],
    "suggested_improvement": "better alt text if needed, or null if current is good",
    "reasoning": "brief explanation"
}}

Common issues to check for:
- Generic text like "image" or "photo" that doesn't describe content
- Text that describes the wrong thing entirely
- Missing important visual elements (text in image, people, actions)
- Decorative images marked as informative or vice versa
- Alt text that's too long or too short for the image complexity"""

        response_text, elapsed, provider, model = await self._generate_vision(
            image_path, prompt, max_tokens=500
        )

        # Check for errors
        if response_text.startswith("ERROR:"):
            return {
                "success": False,
                "error": response_text.removeprefix("ERROR: "),
                "inference_time": elapsed,
                "provider": provider,
            }

        # Parse JSON response
        try:
            # Clean up response - extract JSON from markdown code blocks if present
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            import json

            result = json.loads(cleaned)

            return {
                "success": True,
                "is_accurate": result.get("is_accurate", False),
                "accuracy_score": result.get("accuracy_score", 0.0),
                "issues": result.get("issues", []),
                "suggested_improvement": result.get("suggested_improvement"),
                "reasoning": result.get("reasoning", ""),
                "existing_alt_text": existing_alt_text,
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse validation response as JSON: {e}")
            # Fallback: try to extract key information from text
            is_accurate = (
                "accurate" in response_text.lower()
                and "not accurate" not in response_text.lower()
            )
            return {
                "success": True,
                "is_accurate": is_accurate,
                "accuracy_score": 0.7 if is_accurate else 0.3,
                "issues": ["Could not parse detailed validation"],
                "suggested_improvement": None,
                "reasoning": response_text[:500],
                "existing_alt_text": existing_alt_text,
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

    @_tracked_analysis
    async def detect_image_type(
        self, image_path: str, context: str = None
    ) -> Dict[str, Any]:
        """Detect if an image is decorative or informative (WCAG 1.1.1).

        Decorative images should have empty alt="" attribute.
        Informative images need descriptive alt text.

        Args:
            image_path: Path to image file
            context: Optional context about where image appears

        Returns:
            Dict with:
                - is_decorative: bool - True if image is decorative
                - image_purpose: str - "decorative", "informative", "functional", "complex"
                - confidence: float (0-1) - how confident the classification is
                - reasoning: str - explanation of classification
                - recommended_alt: str - suggested alt text (empty for decorative)
        """
        # Validate image
        validation = self._validate_image(image_path)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "inference_time": 0,
                "provider": "none",
            }

        # Build classification prompt
        context_info = f"\n\nContext where image appears: {context}" if context else ""

        prompt = f"""Analyze this image and classify its purpose for web accessibility (WCAG 1.1.1).

{context_info}

Image classification types:
1. DECORATIVE - Visual decoration only, no information conveyed (borders, spacers, mood images)
2. INFORMATIVE - Conveys simple information (photos, illustrations, icons with meaning)
3. FUNCTIONAL - Performs an action when clicked (buttons, links, controls)
4. COMPLEX - Contains detailed information (charts, graphs, diagrams, infographics, maps)

Signs of DECORATIVE images:
- Abstract patterns, gradients, or textures
- Generic stock photos used only for visual appeal
- Borders, separators, or spacers
- Repeated background images
- Images that duplicate adjacent text

Signs of INFORMATIVE images:
- Photos of people, places, or things being discussed
- Icons that convey meaning (warning, success, etc.)
- Screenshots or product images
- Educational illustrations

Signs of COMPLEX images:
- Charts (bar, pie, line, scatter)
- Graphs with data points
- Diagrams showing relationships
- Infographics with multiple elements
- Maps or floor plans
- Tables presented as images

Respond in this exact JSON format:
{{
    "is_decorative": true/false,
    "image_purpose": "decorative" | "informative" | "functional" | "complex",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation of why this classification",
    "recommended_alt": "suggested alt text, or empty string for decorative",
    "visual_elements": ["list", "of", "key", "elements", "detected"]
}}"""

        response_text, elapsed, provider, model = await self._generate_vision(
            image_path, prompt, max_tokens=500
        )

        # Check for errors
        if response_text.startswith("ERROR:"):
            return {
                "success": False,
                "error": response_text.removeprefix("ERROR: "),
                "inference_time": elapsed,
                "provider": provider,
            }

        # Parse JSON response
        try:
            import json

            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            result = json.loads(cleaned)

            return {
                "success": True,
                "is_decorative": result.get("is_decorative", False),
                "image_purpose": result.get("image_purpose", "informative"),
                "confidence": result.get("confidence", 0.7),
                "reasoning": result.get("reasoning", ""),
                "recommended_alt": result.get("recommended_alt", ""),
                "visual_elements": result.get("visual_elements", []),
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse detection response as JSON: {e}")
            # Fallback: assume informative
            return {
                "success": True,
                "is_decorative": False,
                "image_purpose": "informative",
                "confidence": 0.5,
                "reasoning": f"Could not parse response: {response_text[:200]}",
                "recommended_alt": "",
                "visual_elements": [],
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

    @_tracked_analysis
    async def describe_chart_or_graph(
        self, image_path: str, context: str = None, detail_level: str = "standard"
    ) -> Dict[str, Any]:
        """Generate detailed accessible description for charts, graphs, and infographics.

        Provides comprehensive descriptions that convey the data and insights
        to users who cannot see the visual representation.

        Args:
            image_path: Path to image file
            context: Optional context about the data or topic
            detail_level: "brief", "standard", or "detailed"

        Returns:
            Dict with:
                - chart_type: str - type of visualization detected
                - title: str - detected or inferred title
                - short_description: str - 1-2 sentence summary
                - detailed_description: str - full accessible description
                - data_summary: str - key data points and trends
                - insights: List[str] - key insights from the visualization
                - accessibility_note: str - note for long description implementation
        """
        # Validate image
        validation = self._validate_image(image_path)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "inference_time": 0,
                "provider": "none",
            }

        # Build description prompt
        context_info = f"\n\nContext: {context}" if context else ""

        detail_instructions = {
            "brief": "Provide a concise 2-3 sentence description.",
            "standard": "Provide a comprehensive description with key data points.",
            "detailed": "Provide an exhaustive description including all visible data values, labels, and visual elements.",
        }

        prompt = f"""Analyze this chart, graph, diagram, or infographic and create an accessible description for screen reader users.

{context_info}

{detail_instructions.get(detail_level, detail_instructions["standard"])}

Your description should help someone who cannot see the image understand:
1. What type of visualization this is
2. What data or information is being presented
3. Key trends, patterns, or insights
4. Specific data values when visible
5. Any labels, legends, or annotations

Respond in this exact JSON format:
{{
    "chart_type": "bar chart" | "line graph" | "pie chart" | "scatter plot" | "flow diagram" | "infographic" | "table" | "map" | "timeline" | "organizational chart" | "other",
    "title": "detected or inferred title of the visualization",
    "short_description": "1-2 sentence summary for alt text (under 150 chars)",
    "detailed_description": "Full accessible description (3-5 paragraphs) explaining the visualization completely",
    "data_summary": {{
        "x_axis": "what the x-axis represents (if applicable)",
        "y_axis": "what the y-axis represents (if applicable)",
        "data_points": ["key data values visible"],
        "trends": ["increasing", "decreasing", "stable", "varies"],
        "comparisons": ["notable comparisons between elements"]
    }},
    "insights": [
        "Key insight 1",
        "Key insight 2",
        "Key insight 3"
    ],
    "visual_elements": {{
        "colors_used": ["list of colors and what they represent"],
        "legend_items": ["items in the legend if present"],
        "annotations": ["any text annotations on the chart"]
    }},
    "accessibility_note": "Recommendation for implementing this as an accessible description (e.g., use aria-describedby with long description)"
}}

Important guidelines:
- Be specific about numbers and values when visible
- Describe the overall trend before specific details
- Use clear, plain language
- For pie charts, describe percentages and segments
- For bar/line charts, describe the axes and trends
- For infographics, describe the logical flow and key points
- For maps, describe regions and what data is shown"""

        max_tokens = 1500 if detail_level == "detailed" else 1000
        response_text, elapsed, provider, model = await self._generate_vision(
            image_path, prompt, max_tokens=max_tokens
        )

        # Check for errors
        if response_text.startswith("ERROR:"):
            return {
                "success": False,
                "error": response_text.removeprefix("ERROR: "),
                "inference_time": elapsed,
                "provider": provider,
            }

        # Parse JSON response
        try:
            import json

            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            result = json.loads(cleaned)

            return {
                "success": True,
                "chart_type": result.get("chart_type", "unknown"),
                "title": result.get("title", ""),
                "short_description": result.get("short_description", ""),
                "detailed_description": result.get("detailed_description", ""),
                "data_summary": result.get("data_summary", {}),
                "insights": result.get("insights", []),
                "visual_elements": result.get("visual_elements", {}),
                "accessibility_note": result.get("accessibility_note", ""),
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse chart description as JSON: {e}")
            # Return raw text as description
            return {
                "success": True,
                "chart_type": "unknown",
                "title": "",
                "short_description": response_text[:150],
                "detailed_description": response_text,
                "data_summary": {},
                "insights": [],
                "visual_elements": {},
                "accessibility_note": "Manual review recommended",
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

    @_tracked_analysis
    async def analyze_image_comprehensive(
        self, image_path: str, context: str = None, existing_alt_text: str = None
    ) -> Dict[str, Any]:
        """Comprehensive image analysis combining detection, description, and validation.

        Performs all three analyses in sequence to provide complete accessibility
        recommendations for an image.

        Args:
            image_path: Path to image file
            context: Optional context about where image appears
            existing_alt_text: Optional existing alt text to validate

        Returns:
            Dict with complete analysis including type detection, description,
            and validation results if existing alt text provided.
        """
        import time

        start_time = time.perf_counter()

        result = {"image_path": image_path, "success": True, "total_inference_time": 0}

        # Step 1: Detect image type (decorative vs informative)
        type_result = await self.detect_image_type(image_path, context)
        result["type_detection"] = type_result
        result["total_inference_time"] += type_result.get("inference_time", 0)

        if not type_result.get("success"):
            result["success"] = False
            result["error"] = type_result.get("error", "Type detection failed")
            return result

        # Step 2: If decorative, we're done - empty alt is correct
        if type_result.get("is_decorative"):
            result["recommendation"] = {
                "alt_text": "",
                "reason": 'Image is decorative - use empty alt attribute (alt="")',
                "aria_hidden": True,
            }
            result["total_inference_time"] = time.perf_counter() - start_time
            return result

        # Step 3: Generate appropriate description based on type
        image_purpose = type_result.get("image_purpose", "informative")

        if image_purpose == "complex":
            # Use chart/graph description for complex images
            description_result = await self.describe_chart_or_graph(
                image_path, context, detail_level="standard"
            )
            result["description"] = description_result
            result["total_inference_time"] += description_result.get(
                "inference_time", 0
            )

            if description_result.get("success"):
                result["recommendation"] = {
                    "alt_text": description_result.get("short_description", ""),
                    "long_description": description_result.get(
                        "detailed_description", ""
                    ),
                    "reason": f"Complex image ({description_result.get('chart_type', 'visualization')}) - use short alt with aria-describedby for long description",
                    "implementation": "Use figure element with figcaption, or aria-describedby pointing to detailed description",
                }
        else:
            # Use standard alt text for informative images
            alt_result = await self.generate_alt_text(
                image_path, context, educational_context=True
            )
            result["description"] = alt_result
            result["total_inference_time"] += alt_result.get("inference_time", 0)

            if alt_result.get("success"):
                result["recommendation"] = {
                    "alt_text": alt_result.get("alt_text", ""),
                    "reason": "Informative image - use descriptive alt text",
                    "implementation": "Add alt attribute with the suggested text",
                }

        # Step 4: Validate existing alt text if provided
        if existing_alt_text:
            validation_result = await self.validate_alt_text(
                image_path, existing_alt_text, context
            )
            result["validation"] = validation_result
            result["total_inference_time"] += validation_result.get("inference_time", 0)

            if validation_result.get("success"):
                if not validation_result.get("is_accurate"):
                    result["recommendation"]["needs_update"] = True
                    result["recommendation"]["validation_issues"] = (
                        validation_result.get("issues", [])
                    )
                    if validation_result.get("suggested_improvement"):
                        result["recommendation"]["suggested_improvement"] = (
                            validation_result.get("suggested_improvement")
                        )

        result["total_inference_time"] = time.perf_counter() - start_time
        return result

    @_tracked_analysis
    async def batch_analyze_images(
        self,
        image_paths: List[str],
        context: str = None,
        include_existing_alt: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Batch analyze multiple images with comprehensive analysis.

        Args:
            image_paths: List of image file paths
            context: Optional shared context for all images
            include_existing_alt: Optional dict mapping image paths to existing alt text

        Returns:
            Dict with results for each image and summary statistics
        """
        import time

        start_time = time.perf_counter()

        results = []
        stats = {
            "total_images": len(image_paths),
            "decorative": 0,
            "informative": 0,
            "complex": 0,
            "functional": 0,
            "failed": 0,
            "needs_update": 0,
        }

        include_existing_alt = include_existing_alt or {}

        for image_path in image_paths:
            existing_alt = include_existing_alt.get(image_path)
            result = await self.analyze_image_comprehensive(
                image_path=image_path, context=context, existing_alt_text=existing_alt
            )

            results.append(result)

            if not result.get("success"):
                stats["failed"] += 1
            else:
                purpose = result.get("type_detection", {}).get(
                    "image_purpose", "informative"
                )
                if purpose == "decorative":
                    stats["decorative"] += 1
                elif purpose == "complex":
                    stats["complex"] += 1
                elif purpose == "functional":
                    stats["functional"] += 1
                else:
                    stats["informative"] += 1

                if result.get("recommendation", {}).get("needs_update"):
                    stats["needs_update"] += 1

        total_time = time.perf_counter() - start_time

        return {
            "summary": {
                **stats,
                "success_rate": (
                    (stats["total_images"] - stats["failed"]) / stats["total_images"]
                    if stats["total_images"] > 0
                    else 0
                ),
                "total_processing_time": total_time,
                "average_time_per_image": (
                    total_time / len(image_paths) if image_paths else 0
                ),
            },
            "results": results,
        }

    @_tracked_analysis
    async def score_alt_text_quality(
        self, image_path: str, alt_text: str, context: str = None
    ) -> Dict[str, Any]:
        """Score alt text quality on a 0-100 scale with detailed breakdown.

        Evaluates alt text on multiple WCAG-aligned criteria to provide
        a quantitative quality score for tracking and reporting.

        Args:
            image_path: Path to image file
            alt_text: The alt text to score
            context: Optional context about where image appears

        Returns:
            Dict with:
                - overall_score: int (0-100)
                - grade: str ("A", "B", "C", "D", "F")
                - criteria_scores: Dict with individual criterion scores
                - issues: List of specific problems found
                - suggestions: List of improvement recommendations
                - passes_wcag: bool - meets WCAG 2.1 AA requirements
        """
        # Handle empty alt text
        if not alt_text or not alt_text.strip():
            # Check if image should be decorative
            type_result = await self.detect_image_type(image_path, context)
            if type_result.get("success") and type_result.get("is_decorative"):
                return {
                    "success": True,
                    "overall_score": 100,
                    "grade": "A",
                    "criteria_scores": {
                        "appropriateness": 100,
                        "length": 100,
                        "descriptiveness": 100,
                        "relevance": 100,
                        "accessibility": 100,
                    },
                    "issues": [],
                    "suggestions": [],
                    "passes_wcag": True,
                    "note": "Empty alt text is correct for decorative images",
                    "is_decorative": True,
                    "inference_time": type_result.get("inference_time", 0),
                }
            else:
                return {
                    "success": True,
                    "overall_score": 0,
                    "grade": "F",
                    "criteria_scores": {
                        "appropriateness": 0,
                        "length": 0,
                        "descriptiveness": 0,
                        "relevance": 0,
                        "accessibility": 0,
                    },
                    "issues": ["Missing alt text for informative image"],
                    "suggestions": [
                        "Add descriptive alt text that conveys the image content"
                    ],
                    "passes_wcag": False,
                    "is_decorative": False,
                    "inference_time": type_result.get("inference_time", 0),
                }

        # Validate image
        validation = self._validate_image(image_path)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "inference_time": 0,
            }

        # Build scoring prompt
        context_info = f"\n\nContext where image appears: {context}" if context else ""

        prompt = f"""Analyze this image and score the quality of the following alt text on multiple criteria.

ALT TEXT TO SCORE: "{alt_text}"
{context_info}

Score each criterion from 0-100:

1. LENGTH (0-100):
   - 100: Appropriate length for image complexity (simple: 50-125 chars, complex: 125-300 chars)
   - 75: Slightly too short or too long
   - 50: Significantly too brief or verbose
   - 25: Very poor length (under 10 chars or over 500 chars)
   - 0: Empty or single word for informative image

2. DESCRIPTIVENESS (0-100):
   - 100: Specific and concrete (describes actual content visible)
   - 75: Mostly specific with minor vagueness
   - 50: Mix of specific and generic terms
   - 25: Mostly generic (e.g., "a picture", "an image of")
   - 0: Completely generic or meaningless

3. ACCURACY (0-100):
   - 100: Perfectly describes what's in the image
   - 75: Minor inaccuracies or missing details
   - 50: Some incorrect information or significant omissions
   - 25: Major inaccuracies
   - 0: Completely wrong or irrelevant

4. ACCESSIBILITY (0-100):
   - 100: Follows all WCAG best practices
   - 75: Minor accessibility issues
   - 50: Some accessibility problems
   - 25: Significant accessibility violations
   - 0: Completely inaccessible

   Check for:
   - Doesn't start with "image of" or "picture of" (redundant)
   - Doesn't include file name or extension
   - Uses clear, plain language
   - Conveys purpose/meaning, not just appearance
   - Appropriate for the image type (informative vs complex)

5. CONTEXT_FIT (0-100):
   - 100: Alt text fits perfectly for the usage context
   - 75: Mostly appropriate for context
   - 50: Somewhat appropriate
   - 25: Poor fit for context
   - 0: Completely inappropriate for context

Respond in this exact JSON format:
{{
    "length_score": 0-100,
    "length_analysis": "brief explanation",
    "descriptiveness_score": 0-100,
    "descriptiveness_analysis": "brief explanation",
    "accuracy_score": 0-100,
    "accuracy_analysis": "brief explanation",
    "accessibility_score": 0-100,
    "accessibility_analysis": "brief explanation",
    "context_fit_score": 0-100,
    "context_fit_analysis": "brief explanation",
    "issues": ["list", "of", "specific", "problems"],
    "suggestions": ["list", "of", "improvements"],
    "best_practice_violations": ["any WCAG violations"]
}}"""

        response_text, elapsed, provider, model = await self._generate_vision(
            image_path, prompt, max_tokens=800
        )

        # Check for errors
        if response_text.startswith("ERROR:"):
            return {
                "success": False,
                "error": response_text.removeprefix("ERROR: "),
                "inference_time": elapsed,
                "provider": provider,
            }

        # Parse JSON response
        try:
            import json

            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            result = json.loads(cleaned)

            # Calculate overall score (weighted average)
            weights = {
                "accuracy": 0.30,  # Most important - must describe correctly
                "descriptiveness": 0.25,  # Second most important
                "accessibility": 0.20,  # WCAG compliance
                "length": 0.15,  # Appropriate length
                "context_fit": 0.10,  # Context appropriateness
            }

            length_score = result.get("length_score", 50)
            descriptiveness_score = result.get("descriptiveness_score", 50)
            accuracy_score = result.get("accuracy_score", 50)
            accessibility_score = result.get("accessibility_score", 50)
            context_fit_score = result.get("context_fit_score", 50)

            overall_score = int(
                length_score * weights["length"]
                + descriptiveness_score * weights["descriptiveness"]
                + accuracy_score * weights["accuracy"]
                + accessibility_score * weights["accessibility"]
                + context_fit_score * weights["context_fit"]
            )

            # Determine grade
            if overall_score >= 90:
                grade = "A"
            elif overall_score >= 80:
                grade = "B"
            elif overall_score >= 70:
                grade = "C"
            elif overall_score >= 60:
                grade = "D"
            else:
                grade = "F"

            # Determine WCAG compliance (must score at least 70 in accessibility)
            passes_wcag = accessibility_score >= 70 and accuracy_score >= 50

            return {
                "success": True,
                "overall_score": overall_score,
                "grade": grade,
                "criteria_scores": {
                    "length": length_score,
                    "descriptiveness": descriptiveness_score,
                    "accuracy": accuracy_score,
                    "accessibility": accessibility_score,
                    "context_fit": context_fit_score,
                },
                "criteria_analysis": {
                    "length": result.get("length_analysis", ""),
                    "descriptiveness": result.get("descriptiveness_analysis", ""),
                    "accuracy": result.get("accuracy_analysis", ""),
                    "accessibility": result.get("accessibility_analysis", ""),
                    "context_fit": result.get("context_fit_analysis", ""),
                },
                "issues": result.get("issues", []),
                "suggestions": result.get("suggestions", []),
                "best_practice_violations": result.get("best_practice_violations", []),
                "passes_wcag": passes_wcag,
                "alt_text_analyzed": alt_text,
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse quality score response as JSON: {e}")
            # Fallback: use heuristics
            return self._heuristic_quality_score(alt_text, elapsed, provider)

    def _heuristic_quality_score(
        self, alt_text: str, elapsed: float, provider: str
    ) -> Dict[str, Any]:
        """Fallback heuristic scoring when AI parsing fails."""
        issues = []
        suggestions = []

        # Length scoring
        length = len(alt_text)
        if length < 10:
            length_score = 20
            issues.append("Alt text is too short")
            suggestions.append("Add more descriptive detail")
        elif length < 30:
            length_score = 60
            issues.append("Alt text may be too brief")
        elif length <= 150:
            length_score = 100
        elif length <= 300:
            length_score = 80
        else:
            length_score = 50
            issues.append("Alt text may be too long")
            suggestions.append("Consider using aria-describedby for long descriptions")

        # Descriptiveness scoring
        generic_phrases = [
            "image of",
            "picture of",
            "photo of",
            "a photo",
            "an image",
            "a picture",
            "screenshot",
            "graphic",
        ]
        lower_alt = alt_text.lower()
        descriptiveness_score = 80

        for phrase in generic_phrases:
            if lower_alt.startswith(phrase):
                descriptiveness_score -= 20
                issues.append(f"Starts with redundant phrase '{phrase}'")
                suggestions.append("Remove redundant phrases like 'image of'")
                break

        # Check for file extensions
        if any(ext in lower_alt for ext in [".jpg", ".png", ".gif", ".jpeg", ".webp"]):
            descriptiveness_score -= 30
            issues.append("Contains file extension")
            suggestions.append("Remove file name/extension from alt text")

        # Accessibility scoring
        accessibility_score = 85
        if lower_alt == alt_text:  # All lowercase might indicate poor quality
            accessibility_score -= 10

        # Overall score
        overall_score = int(
            length_score * 0.25
            + descriptiveness_score * 0.35
            + accessibility_score * 0.40
        )

        if overall_score >= 90:
            grade = "A"
        elif overall_score >= 80:
            grade = "B"
        elif overall_score >= 70:
            grade = "C"
        elif overall_score >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "success": True,
            "overall_score": overall_score,
            "grade": grade,
            "criteria_scores": {
                "length": length_score,
                "descriptiveness": descriptiveness_score,
                "accuracy": 70,  # Can't verify without AI
                "accessibility": accessibility_score,
                "context_fit": 70,  # Can't verify without AI
            },
            "issues": issues,
            "suggestions": suggestions,
            "passes_wcag": overall_score >= 70,
            "alt_text_analyzed": alt_text,
            "inference_time": elapsed,
            "provider": provider,
            "note": "Scored using heuristics (AI response parsing failed)",
        }

    @_tracked_analysis
    async def batch_score_alt_text_quality(
        self,
        items: List[Dict[str, str]],
        context: str = None,
    ) -> Dict[str, Any]:
        """Batch score alt text quality for multiple images.

        Args:
            items: List of dicts with 'image_path' and 'alt_text' keys
            context: Optional shared context for all images

        Returns:
            Dict with results for each image and aggregate statistics
        """
        import time

        start_time = time.perf_counter()

        results = []
        total_score = 0
        grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        wcag_pass_count = 0
        all_issues = []

        for item in items:
            image_path = item.get("image_path", "")
            alt_text = item.get("alt_text", "")

            result = await self.score_alt_text_quality(
                image_path=image_path, alt_text=alt_text, context=context
            )

            results.append({"image_path": image_path, "result": result})

            if result.get("success"):
                total_score += result.get("overall_score", 0)
                grade = result.get("grade", "F")
                grade_counts[grade] = grade_counts.get(grade, 0) + 1
                if result.get("passes_wcag"):
                    wcag_pass_count += 1
                all_issues.extend(result.get("issues", []))

        total_time = time.perf_counter() - start_time
        num_items = len(items)

        # Calculate aggregate statistics
        avg_score = total_score / num_items if num_items > 0 else 0

        # Find most common issues
        issue_counts = {}
        for issue in all_issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        common_issues = sorted(issue_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "summary": {
                "total_images": num_items,
                "average_score": round(avg_score, 1),
                "average_grade": (
                    "A"
                    if avg_score >= 90
                    else (
                        "B"
                        if avg_score >= 80
                        else "C" if avg_score >= 70 else "D" if avg_score >= 60 else "F"
                    )
                ),
                "grade_distribution": grade_counts,
                "wcag_compliance_rate": (
                    round(wcag_pass_count / num_items * 100, 1) if num_items > 0 else 0
                ),
                "common_issues": [
                    {"issue": issue, "count": count} for issue, count in common_issues
                ],
                "total_processing_time": round(total_time, 2),
                "average_time_per_image": (
                    round(total_time / num_items, 2) if num_items > 0 else 0
                ),
            },
            "results": results,
        }

    def health_check(self) -> Dict[str, Any]:
        """Check the explicitly configured vision transport."""
        if self.lms_client is not None:
            return {
                "status": "healthy",
                "provider": getattr(self.lms_client, "provider", None),
                "transport": "policy_bound_lms",
                "features": [
                    "generate_alt_text",
                    "validate_alt_text",
                    "detect_image_type",
                    "describe_chart_or_graph",
                    "analyze_image_comprehensive",
                    "batch_analyze_images",
                ],
            }
        if not self.allow_legacy_transport:
            return {
                "status": "disabled",
                "provider": None,
                "transport": "none",
                "features": [],
            }

        health = {
            "status": "healthy",
            "gemini_configured": bool(self.gemini_api_key),
            "gemini_model": self.vision_model,
            "use_gemini": self.use_gemini,
            "ollama_fallback": self.ollama_fallback,
            "ollama_host": self.ollama_host,
            "features": [
                "generate_alt_text",
                "validate_alt_text",
                "detect_image_type",
                "describe_chart_or_graph",
                "analyze_image_comprehensive",
                "batch_analyze_images",
            ],
        }

        if not self.use_gemini:
            # Check Ollama availability
            try:
                import ollama

                models_response = ollama.list()
                if hasattr(models_response, "models"):
                    available = [m.model for m in models_response.models]
                elif isinstance(models_response, dict) and "models" in models_response:
                    available = [m["name"] for m in models_response["models"]]
                else:
                    available = []

                vision_available = any(
                    self.ollama_fallback in name for name in available
                )
                health["ollama_available"] = vision_available
                if not vision_available:
                    health["status"] = "degraded"
                    health["warning"] = f"Ollama model {self.ollama_fallback} not found"
            except Exception as e:
                health["status"] = "degraded"
                health["ollama_error"] = str(e)

        return health
