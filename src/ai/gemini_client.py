"""Provider-neutral accessibility analysis compatibility client.

Enhanced with RAG (Retrieval-Augmented Generation) for grounding
severity classifications in canonical WCAG guidelines.
"""

import time
import httpx
import logging
from copy import copy
from typing import Dict, Any, Optional

from src.config.settings import get_settings
from .providers.base import LLMResponse
from .providers.manager import ProviderManager, get_provider_manager
from .wcag_knowledge_base import WCAGKnowledgeBase
from .severity_rules import resolve_severity

logger = logging.getLogger(__name__)


class GeminiClient:
    """Preserve the legacy analysis API while routing through ProviderManager.

    The class name remains for import compatibility. Public generation methods
    honor ``LLM_PROVIDER`` and its explicitly configured fallback; the private
    direct Gemini/Ollama transports are retained only for older callers that
    import them directly.
    """

    # Models that use thinking mode and need higher token limits
    THINKING_MODELS = ["gemini-2.5", "gemini-3"]

    def __init__(
        self,
        enable_rag: bool = True,
        provider_manager: Optional[ProviderManager] = None,
    ):
        """Initialize the compatibility client.

        Args:
            enable_rag: Enable RAG for grounding classifications in WCAG knowledge base
            provider_manager: Optional manager injection for tests or policy-bound callers
        """
        self.settings = get_settings()
        self.provider_manager = provider_manager or get_provider_manager()
        self.api_key = self.settings.gemini_api_key
        self.api_base = self.settings.gemini_api_base
        self.text_model = self.settings.gemini_text_model
        self.code_model = self.settings.gemini_code_model
        self.use_gemini = self.settings.use_gemini and bool(self.api_key)
        self.ollama_host = self.settings.ollama_host
        self.ollama_fallback = self.settings.ollama_fallback_text
        # Honour LLM_FALLBACK_PROVIDER. This client predates ProviderManager and
        # used to reach for Ollama unconditionally, so a deployment that had
        # deliberately disabled the fallback still tried it and logged a
        # confusing "model not found" on every provider error. Some deployments
        # run Gemini-only on purpose (e.g. hosts without the resources for local
        # inference), so a disabled fallback must stay disabled.
        self.fallback_enabled = str(
            getattr(self.settings, "llm_fallback_provider", "none")
        ).strip().lower() not in ("none", "", "disabled")

        # RAG knowledge base for grounding classifications
        self.enable_rag = enable_rag
        self.kb: Optional[WCAGKnowledgeBase] = None
        self._kb_initialized = False

        if enable_rag:
            try:
                self.kb = WCAGKnowledgeBase(
                    ollama_host=self.ollama_host,
                    embedding_model=self.settings.ollama_embedding_model,
                    embedding_provider=getattr(
                        self.settings, "embedding_provider", "none"
                    ),
                )
                logger.info("WCAG knowledge base configured for AI analysis")
            except Exception as e:
                logger.warning(f"RAG knowledge base not available: {e}")
                self.enable_rag = False

    def bind_provider_manager(
        self, provider_manager: ProviderManager
    ) -> "GeminiClient":
        """Clone the compatibility surface while sharing provider-neutral RAG state."""

        bound = copy(self)
        bound.provider_manager = provider_manager
        return bound

    @staticmethod
    def _response_dict(response: LLMResponse) -> Dict[str, Any]:
        """Translate the shared provider response into the legacy dictionary."""
        result = {
            "success": response.success,
            "content": response.content,
            "inference_time": response.inference_time,
            "provider": response.provider,
            "model": response.model,
        }
        if response.error:
            result["error"] = response.error
        return result

    async def initialize_rag(self) -> bool:
        """Initialize RAG knowledge base connection (call once at startup)."""
        if not self.enable_rag or not self.kb:
            return False

        if self._kb_initialized:
            return True

        try:
            await self.kb.initialize()
            # Corpus seeding is provider-independent. A disabled embedding
            # backend only removes optional semantic search; exact rule-ID
            # grounding remains ready after the seed completes.
            await self.kb.bootstrap()
            self._kb_initialized = True
            logger.info("WCAG knowledge base initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize RAG knowledge base: {e}")
            try:
                await self.kb.close()
            except Exception:
                logger.debug("WCAG knowledge base cleanup also failed", exc_info=True)
            self.enable_rag = False
            return False

    async def close_rag(self):
        """Close RAG knowledge base connection."""
        if self.kb and self._kb_initialized:
            try:
                await self.kb.close()
                self._kb_initialized = False
            except Exception as e:
                logger.error(f"Error closing RAG knowledge base: {e}")

    def _is_thinking_model(self, model: str) -> bool:
        """Check if model uses thinking mode (needs higher token limits)."""
        return any(t in model for t in self.THINKING_MODELS)

    async def _call_gemini(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, float]:
        """Make async call to Gemini API."""
        # Thinking models need higher token limits
        if self._is_thinking_model(model):
            max_tokens = max(max_tokens, 2000)

        start_time = time.perf_counter()

        # Build messages
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

        contents.append({"role": "user", "parts": [{"text": prompt}]})

        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.api_base}/models/{model}:generateContent",
                    # Header, not a query param: httpx logs the full request URL
                    # at INFO, so a key in the query string is written verbatim
                    # to stdout, Loki and any Sentry breadcrumb.
                    headers={"x-goog-api-key": self.api_key},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens,
                        },
                    },
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

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Gemini API exception: {e}")
            return f"ERROR: {e}", elapsed

    def _call_gemini_sync(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, float]:
        """Make sync call to Gemini API."""
        if self._is_thinking_model(model):
            max_tokens = max(max_tokens, 2000)

        start_time = time.perf_counter()

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

        contents.append({"role": "user", "parts": [{"text": prompt}]})

        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)
            response = httpx.post(
                f"{self.api_base}/models/{model}:generateContent",
                # Header, not a query param: httpx logs the full request URL at
                # INFO, so a key in the query string is written verbatim to
                # stdout, Loki and any Sentry breadcrumb.
                headers={"x-goog-api-key": self.api_key},
                json={
                    "contents": contents,
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                    },
                },
                timeout=timeout,
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

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Gemini API exception: {e}")
            return f"ERROR: {e}", elapsed

    def _call_ollama(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, float]:
        """Make call to Ollama as fallback."""
        start_time = time.perf_counter()

        try:
            import ollama

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = ollama.chat(
                model=model,
                messages=messages,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            )

            elapsed = time.perf_counter() - start_time
            content = response["message"]["content"].strip()
            return content, elapsed

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Ollama fallback error: {e}")
            return f"ERROR: {e}", elapsed

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate text through the configured provider manager.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system_prompt: Optional system instructions

        Returns:
            Dict with content, inference_time, provider, model
        """
        response = await self.provider_manager.generate_text(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )
        return self._response_dict(response)

    def generate_text_sync(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronous version of :meth:`generate_text`."""
        response = self.provider_manager.generate_text_sync(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )
        return self._response_dict(response)

    async def generate_code_fix(
        self,
        html_snippet: str,
        rule_id: str,
        issue_description: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate WCAG-compliant code fix.

        Args:
            html_snippet: The HTML code with accessibility issues
            rule_id: WCAG rule ID (e.g., 'image-alt', 'color-contrast')
            issue_description: Description of the accessibility issue
            context: Optional context about the page/component

        Returns:
            Dict with fixed_code, explanation, inference_time
        """
        context_info = f"\nContext: {context}" if context else ""

        prompt = f"""Fix this WCAG accessibility violation.

Rule: {rule_id}
Issue: {issue_description}
{context_info}

Current HTML:
```html
{html_snippet}
```

Provide:
1. The fixed HTML code that resolves the accessibility issue
2. A brief explanation of what was changed and why

Format your response as:
FIXED CODE:
```html
[your fixed code here]
```

EXPLANATION:
[your explanation here]
"""

        response = await self.provider_manager.generate_code(
            prompt=prompt,
            language="html",
            max_tokens=1000,
            temperature=0.2,
        )
        content = response.content
        elapsed = response.inference_time
        provider = response.provider
        model = response.model

        if not response.success:
            return {
                "success": False,
                "error": response.error or "Code generation failed",
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        # Parse the response
        fixed_code = ""
        explanation = ""

        if "FIXED CODE:" in content:
            parts = content.split("FIXED CODE:", 1)
            if len(parts) > 1:
                code_part = parts[1]
                if "EXPLANATION:" in code_part:
                    code_part, explanation = code_part.split("EXPLANATION:", 1)
                    explanation = explanation.strip()

                # Extract code from markdown code block
                if "```html" in code_part:
                    code_part = code_part.split("```html", 1)[1]
                    if "```" in code_part:
                        code_part = code_part.split("```", 1)[0]
                elif "```" in code_part:
                    code_part = code_part.split("```", 1)[1]
                    if "```" in code_part:
                        code_part = code_part.split("```", 1)[0]

                fixed_code = code_part.strip()
        else:
            # Try to find any code block
            if "```" in content:
                parts = content.split("```")
                for i, part in enumerate(parts):
                    if i % 2 == 1:  # Odd indices are code blocks
                        if part.startswith("html"):
                            part = part[4:]
                        fixed_code = part.strip()
                        break

        return {
            "success": True,
            "fixed_code": fixed_code or content,
            "explanation": explanation or "Code fix generated",
            "inference_time": elapsed,
            "provider": provider,
            "model": model,
        }

    async def classify_severity(
        self, rule_id: str, impact: str, html_snippet: str, selector: str
    ) -> Dict[str, Any]:
        """Classify accessibility issue severity.

        Args:
            rule_id: WCAG rule ID
            impact: axe-core impact level
            html_snippet: The problematic HTML
            selector: CSS selector

        Returns:
            Dict with severity, explanation, business_impact
        """
        # Severity is computed, never generated. See src/ai/severity_rules.py
        # for why: sampling makes model output non-deterministic, and audit
        # reports have to be reproducible. The model writes the prose only.
        resolution = resolve_severity(rule_id, impact)

        prompt = f"""You are an accessibility expert explaining a WCAG violation.

Rule: {rule_id}
Impact: {impact}
HTML: {html_snippet}
Selector: {selector}

This violation has been classified as {resolution.severity} severity. Do not
question or restate the severity; explain the violation consistently with it.

Respond ONLY with valid JSON:
{{
  "explanation": "Brief explanation",
  "business_impact": "Legal/business risk"
}}"""

        generated = await self.generate_text(prompt, max_tokens=300, temperature=0.3)
        content = generated.get("content", "")
        elapsed = generated.get("inference_time", 0.0)
        provider = generated.get("provider", "none")
        model = generated.get("model", "")

        if not generated.get("success"):
            # Severity survives a provider outage: it was computed before the
            # model was called and does not depend on it. Only the prose is lost.
            return {
                "success": False,
                "error": generated.get("error", "Text generation failed"),
                "severity": resolution.severity,
                "severity_source": resolution.source,
                "explanation": "",
                "business_impact": "",
                "inference_time": elapsed,
                "provider": provider,
                "model": model,
            }

        # Try to parse JSON response
        import json

        try:
            # Find JSON in response
            if "{" in content and "}" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                json_str = content[start:end]
                data = json.loads(json_str)

                return {
                    "success": True,
                    "severity": resolution.severity,
                    "severity_source": resolution.source,
                    "explanation": data.get("explanation", ""),
                    "business_impact": data.get("business_impact", ""),
                    "inference_time": elapsed,
                    "provider": provider,
                    "model": model,
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # Prose could not be parsed. Severity is unaffected: it never came from
        # the model, so a malformed response degrades the explanation only.
        return {
            "success": True,
            "severity": resolution.severity,
            "severity_source": resolution.source,
            "explanation": content[:200],
            "business_impact": "",
            "inference_time": elapsed,
            "provider": provider,
            "model": model,
        }

    async def classify_severity_with_rag(
        self,
        rule_id: str,
        impact: str,
        html_snippet: str,
        selector: str,
        violation_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Classify accessibility issue severity with RAG-enhanced context.

        Uses the WCAG knowledge base to retrieve the exact rule and sends that
        grounded context through the configured provider manager.

        Args:
            rule_id: WCAG rule ID (e.g., "button-name")
            impact: axe-core impact level
            html_snippet: The problematic HTML
            selector: CSS selector
            violation_description: Optional description of the violation

        Returns:
            Dict with severity, explanation, business_impact, rag_guidelines
        """
        import json as json_module

        # Initialize RAG if not already done
        if self.enable_rag and not self._kb_initialized:
            await self.initialize_rag()

        # If RAG is not available, fall back to standard classification
        if not self.enable_rag or not self.kb or not self._kb_initialized:
            logger.debug("RAG not available, using standard classification")
            return await self.classify_severity(rule_id, impact, html_snippet, selector)

        start_time = time.perf_counter()

        try:
            # Axe already gives us a stable rule ID. Exact lookup is stronger
            # than semantic similarity here and keeps canonical grounding
            # available when an operator has not configured embeddings.
            guideline = await self.kb.get_by_rule_id(rule_id)
            guidelines = [guideline] if guideline else []

            if not guidelines:
                logger.debug(
                    f"No relevant guidelines found for {rule_id}, using standard classification"
                )
                return await self.classify_severity(
                    rule_id, impact, html_snippet, selector
                )

            # Format guidelines for LLM context
            wcag_context = self.kb.format_guidelines_for_prompt(
                guidelines, include_examples=False
            )

            # Enhanced prompt with RAG context
            # Severity is computed before the model is called and is not up for
            # negotiation. RAG still earns its place here: the retrieved WCAG
            # guidelines ground the *explanation* in canonical criteria.
            resolution = resolve_severity(rule_id, impact)

            prompt = f"""You are an accessibility expert explaining WCAG 2.2 violations.

{wcag_context}

Now explain this specific violation:

Rule: {rule_id}
Impact: {impact}
HTML: {html_snippet[:500]}
Selector: {selector}
{f'Description: {violation_description}' if violation_description else ''}

This violation has been classified as {resolution.severity} severity by Aelira's
rules engine. Do not question or restate the severity. Explain the violation
consistently with it, referencing the WCAG criteria above.

Respond ONLY with valid JSON:
{{
  "explanation": "Brief explanation referencing the WCAG criteria",
  "business_impact": "Legal/business risk based on WCAG level"
}}"""

            generated = await self.generate_text(
                prompt, max_tokens=400, temperature=0.2
            )
            content = generated.get("content", "")
            provider = generated.get("provider", "none")
            model = generated.get("model", "")

            total_time = time.perf_counter() - start_time

            if not generated.get("success"):
                return {
                    "success": False,
                    "error": generated.get("error", "Text generation failed"),
                    "severity": resolution.severity,
                    "severity_source": resolution.source,
                    "explanation": "",
                    "business_impact": "",
                    "inference_time": total_time,
                    "provider": provider,
                    "model": model,
                    "rag_enabled": True,
                    "rag_guidelines": [g["rule_id"] for g in guidelines],
                }

            # Parse JSON response
            try:
                if "{" in content and "}" in content:
                    start = content.index("{")
                    end = content.rindex("}") + 1
                    json_str = content[start:end]
                    data = json_module.loads(json_str)

                    return {
                        "success": True,
                        "severity": resolution.severity,
                        "severity_source": resolution.source,
                        "explanation": data.get("explanation", ""),
                        "business_impact": data.get("business_impact", ""),
                        "inference_time": total_time,
                        "provider": provider,
                        "model": model,
                        "rag_enabled": True,
                        "rag_guidelines": [
                            {
                                "rule_id": g["rule_id"],
                                "wcag_criterion": g["wcag_criterion"],
                                "wcag_level": g["wcag_level"],
                                "similarity": g.get("similarity", 0),
                            }
                            for g in guidelines
                        ],
                    }
            except (json_module.JSONDecodeError, ValueError):
                pass

            # Prose could not be parsed; severity is unaffected because it never
            # came from the model.
            return {
                "success": True,
                "severity": resolution.severity,
                "severity_source": resolution.source,
                "explanation": content[:200],
                "business_impact": "",
                "inference_time": total_time,
                "provider": provider,
                "model": model,
                "rag_enabled": True,
                "rag_guidelines": [g["rule_id"] for g in guidelines],
            }

        except Exception as e:
            # Loud on purpose. This path silently swallowed a broken knowledge
            # base for months: retrieval failed on every call, the fallback
            # produced plausible output, and nothing surfaced it. Severity is
            # unaffected either way (it is computed, not retrieved), but an
            # explanation grounded in nothing should be visible as such.
            logger.error(
                "RAG retrieval failed for rule_id=%s; falling back to ungrounded "
                "classification. Explanations will not cite retrieved WCAG text. "
                "Check that wcag_guidelines is seeded and reachable. Cause: %s",
                rule_id,
                e,
                exc_info=True,
            )
            result = await self.classify_severity(
                rule_id, impact, html_snippet, selector
            )
            result["rag_enabled"] = False
            result["rag_error"] = str(e)
            return result

    def health_check(self) -> Dict[str, Any]:
        """Check the configured provider chain and WCAG corpus state."""
        health = self.provider_manager.health_check()
        health["wcag_grounding"] = {
            "corpus_enabled": self.enable_rag,
            "corpus_initialized": self._kb_initialized,
            "embedding_provider": getattr(self.settings, "embedding_provider", "none"),
        }
        return health


# Global instance for convenience
_gemini_client: Optional[GeminiClient] = None


def get_accessibility_ai_client() -> GeminiClient:
    """Get the global provider-neutral accessibility analysis adapter."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client


def get_gemini_client() -> GeminiClient:
    """Backward-compatible alias for :func:`get_accessibility_ai_client`."""
    return get_accessibility_ai_client()
