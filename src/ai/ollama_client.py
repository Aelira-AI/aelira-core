"""Ollama client for accessibility analysis using open-source LLMs.

Model Selection (Updated January 2026):

Classification Benchmarks:
- qwen2.5-coder:1.5b: 60% accuracy, 4.2s, 28.6 tok/s - FAST (current default)
- qwen3:4b-instruct: 72% accuracy, 25.7s, 14.2 tok/s - MORE ACCURATE (6x slower)

Code Generation Benchmarks:
- qwen2.5-coder:3b: 100% accuracy!, 3.1s, 6.6 tok/s - BEST (current default)
- qwen3:4b-instruct: 86% accuracy, 2.8s, 6.4 tok/s - faster but less accurate

Vision/Alt Text (Updated Jan 2026):
- minicpm-v:latest: 54% accuracy, 39s, 5GB - RECOMMENDED (5x better than moondream)
- llava-llama3: 49% accuracy, 52s, 5.2GB - Alternative
- moondream:latest: 10% accuracy - REMOVED (misidentifies basic images)

Memory Requirements:
- Minimal: qwen2.5-coder:1.5b only (~1GB) - for 8GB RAM systems
- Recommended: 1.5b + 3b combo (~3GB) - for 16GB+ RAM systems
- Quality: qwen3:4b-instruct (~3GB) - for higher accuracy on classification

IMPORTANT - Qwen3 Thinking Mode:
Base Qwen3 models (qwen3:4b, qwen3:8b) output to a separate 'thinking' field,
resulting in empty responses. ALWAYS use '-instruct' variants instead:
- qwen3:4b-instruct (works correctly)
- qwen3:8b-instruct (works correctly)
The '/no_think' prefix does NOT fix this issue with base models.
"""

import ollama
from typing import Dict, Any, Optional
import json
import time
import os
import logging

# Import RAG knowledge base
from .wcag_knowledge_base import WCAGKnowledgeBase

from .severity_rules import resolve_severity

logger = logging.getLogger(__name__)

# Models that require thinking mode to be disabled
# These models use <think>...</think> tags by default which can cause empty responses
# NOTE: Use "-instruct" variants (e.g., qwen3:4b-instruct) to avoid this issue entirely
THINKING_MODE_MODELS = ["qwen3", "deepseek-r1"]

# Instruct variants that DON'T have thinking mode issues
INSTRUCT_VARIANTS = {
    "qwen3:4b": "qwen3:4b-instruct",
    "qwen3:8b": "qwen3:8b-instruct",
    "qwen3:14b": "qwen3:14b-instruct",
}

# Model configurations based on January 2026 benchmarks
# These can be overridden via environment variables for different hardware
MODEL_CONFIGS = {
    # For systems with limited RAM (8-16GB) - prioritize speed
    "minimal": {
        "classifier": "qwen2.5-coder:1.5b",
        "coder": "qwen2.5-coder:1.5b",
        "description": "Single 1.5B model for all tasks (~1GB RAM)",
    },
    # Recommended for most systems (16-32GB) - balanced speed/accuracy
    "recommended": {
        "classifier": "qwen2.5-coder:1.5b",  # Fast classification (4.2s, 60% accuracy)
        "coder": "qwen2.5-coder:3b",  # 100% code generation accuracy!
        "description": "Optimized for speed and accuracy (~3GB RAM)",
    },
    # For higher-end systems (32GB+) - maximum accuracy
    "performance": {
        "classifier": "qwen2.5-coder:3b",
        "coder": "qwen2.5-coder:3b",  # 100% code generation accuracy
        "description": "Both tasks use 3B model (~2GB RAM, slower but more accurate)",
    },
    # Quality profile - uses Qwen3 for higher classification accuracy
    # Trade-off: 6x slower classification (25s vs 4s) but +12% accuracy
    "quality": {
        "classifier": "qwen3:4b-instruct",  # 72% accuracy, 25.7s (vs 60% accuracy, 4.2s)
        "coder": "qwen2.5-coder:3b",  # Still use 2.5 Coder for code gen (100% vs 86%)
        "description": "Higher classification accuracy with Qwen3 (~3GB RAM, slower)",
    },
    # Legacy configuration (not recommended - 7B is too slow for CPU)
    "legacy": {
        "classifier": "llama3.2:3b",
        "coder": "qwen2.5-coder:7b",
        "description": "Original config - 7B model is too slow for CPU inference",
    },
}


def get_model_config() -> Dict[str, str]:
    """Get model configuration based on environment or defaults.

    Environment variables:
        AELIRA_MODEL_PROFILE: minimal|recommended|performance|legacy
        AELIRA_CLASSIFIER_MODEL: Override classifier model
        AELIRA_CODER_MODEL: Override coder model

    Returns:
        Dict with 'classifier' and 'coder' model names
    """
    profile = os.getenv("AELIRA_MODEL_PROFILE", "recommended")
    config = MODEL_CONFIGS.get(profile, MODEL_CONFIGS["recommended"])

    # Allow individual model overrides
    return {
        "classifier": os.getenv("AELIRA_CLASSIFIER_MODEL", config["classifier"]),
        "coder": os.getenv("AELIRA_CODER_MODEL", config["coder"]),
    }


class OllamaClient:
    """Client for interacting with Ollama models for accessibility analysis.

    Optimized for CPU inference on modest hardware (4 cores, 8-32GB RAM).
    Uses benchmarked model selections for best speed/accuracy tradeoff.
    """

    def __init__(
        self,
        host: str = None,
        enable_rag: bool = True,
        model_profile: str = None,
        embedding_model: str = "nomic-embed-text",
    ):
        """Initialize Ollama client.

        Args:
            host: Ollama server URL. Defaults to OLLAMA_HOST env var or http://localhost:11434
            enable_rag: Enable RAG-based classification (default: True)
            model_profile: Model profile to use (minimal|recommended|performance|legacy)
                          Defaults to AELIRA_MODEL_PROFILE env var or "recommended"
            embedding_model: Ollama model used for WCAG retrieval embeddings
        """
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")

        # Get model configuration
        if model_profile:
            os.environ["AELIRA_MODEL_PROFILE"] = model_profile
        config = get_model_config()

        self.classifier_model = config["classifier"]
        self.coder_model = config["coder"]

        logger.info(
            f"OllamaClient initialized with classifier={self.classifier_model}, coder={self.coder_model}"
        )

        # Track which models need thinking mode disabled
        self._classifier_needs_no_think = self._requires_no_think(self.classifier_model)
        self._coder_needs_no_think = self._requires_no_think(self.coder_model)

        if self._classifier_needs_no_think:
            logger.info(
                f"Classifier model {self.classifier_model} requires /no_think prefix"
            )
        if self._coder_needs_no_think:
            logger.info(f"Coder model {self.coder_model} requires /no_think prefix")

        # RAG knowledge base for consistent classifications
        self.enable_rag = enable_rag
        self.kb: Optional[WCAGKnowledgeBase] = None
        if enable_rag:
            self.kb = WCAGKnowledgeBase(
                ollama_host=self.host,
                embedding_model=embedding_model,
            )

    async def initialize(self) -> bool:
        """Initialize RAG knowledge base if enabled."""
        if self.enable_rag and self.kb:
            try:
                await self.kb.initialize()
                result = await self.kb.bootstrap()
                if not result.grounding_available and not result.embedding_in_progress:
                    self.enable_rag = False
                    return False
                return True
            except Exception as e:
                logger.error(f"Failed to initialize RAG knowledge base: {e}")
                self.enable_rag = False  # Disable RAG on failure
        return False

    async def close(self):
        """Close RAG knowledge base if initialized."""
        if self.kb:
            try:
                await self.kb.close()
            except Exception as e:
                logger.error(f"Error closing RAG knowledge base: {e}")

    @staticmethod
    def _requires_no_think(model_name: str) -> bool:
        """Check if a model requires thinking mode to be disabled.

        Qwen3 and DeepSeek-R1 models have a "thinking mode" that outputs
        <think>...</think> tags before the actual response. When used via
        API without proper handling, this can cause empty or malformed responses.

        Args:
            model_name: Name of the Ollama model

        Returns:
            True if model requires /no_think prefix
        """
        model_lower = model_name.lower()
        return any(
            thinking_model in model_lower for thinking_model in THINKING_MODE_MODELS
        )

    def _prepare_prompt(self, prompt: str, model_name: str) -> str:
        """Prepare prompt with any model-specific prefixes.

        For models with thinking mode (Qwen3, DeepSeek-R1), prepends '/no_think'
        to disable the thinking output and get direct responses.

        Args:
            prompt: Original prompt text
            model_name: Model that will process this prompt

        Returns:
            Prepared prompt, possibly with /no_think prefix
        """
        if self._requires_no_think(model_name):
            return f"/no_think\n\n{prompt}"
        return prompt

    def _clean_response(self, content: str) -> str:
        """Clean response content, removing any thinking mode artifacts.

        Some models may still output empty <think></think> tags even with
        /no_think. This method removes those artifacts.

        Args:
            content: Raw response content

        Returns:
            Cleaned content with thinking tags removed
        """
        import re

        # Remove empty thinking tags
        content = re.sub(r"<think>\s*</think>", "", content)
        # Remove thinking tags with content (fallback for models that still think)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        return content.strip()

    async def classify_issue(
        self, rule_id: str, impact: str, html_snippet: str, selector: str
    ) -> Dict[str, Any]:
        """Classify accessibility issue severity using Llama 3.2 3B.

        Args:
            rule_id: WCAG rule identifier (e.g., "image-alt")
            impact: Axe-core's impact level
            html_snippet: HTML code with the violation
            selector: CSS selector for the element

        Returns:
            Dict with severity, explanation, business_impact, inference_time
        """

        prompt = f"""You are an accessibility expert analyzing WCAG 2.1 AA violations.

Violation Details:
- Rule: {rule_id}
- Impact: {impact}
- HTML: {html_snippet}
- Selector: {selector}

Classify this issue's severity (Critical/High/Medium/Low) considering:
1. Legal risk (lawsuit potential)
2. User impact (how many users affected)
3. Fix difficulty (how hard to remediate)

Respond ONLY with valid JSON in this exact format:
{{
  "severity": "Critical|High|Medium|Low",
  "explanation": "2 sentence plain-English explanation",
  "business_impact": "1 sentence about business/legal risk"
}}"""

        # Prepare prompt for thinking-mode models
        prepared_prompt = self._prepare_prompt(prompt, self.classifier_model)

        start = time.time()

        try:
            response = ollama.chat(
                model=self.classifier_model,
                messages=[{"role": "user", "content": prepared_prompt}],
                options={
                    "temperature": 0.3,  # Low temp for consistent classifications
                    "num_predict": 200,  # Limit response length
                },
            )

            elapsed = time.time() - start

            # Parse JSON response, cleaning any thinking mode artifacts
            content = self._clean_response(response["message"]["content"])

            # Extract JSON from response (model might add extra text)
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                json_str = content[json_start:json_end]
                result = json.loads(json_str)
            else:
                result = json.loads(content)

            # Severity is computed, never generated (see severity_rules.py).
            _resolution = resolve_severity(rule_id, impact)
            result["severity"] = _resolution.severity
            result["severity_source"] = _resolution.source

            result["inference_time"] = elapsed
            result["model"] = self.classifier_model
            return result

        except json.JSONDecodeError as e:
            # Fallback if model doesn't return valid JSON
            elapsed = time.time() - start
            return {
                "severity": resolve_severity(rule_id, impact).severity,
                "explanation": (
                    content[:200]
                    if "content" in locals()
                    else "Unable to parse AI response"
                ),
                "business_impact": "Requires manual review",
                "inference_time": elapsed,
                "model": self.classifier_model,
                "error": f"JSON parse error: {str(e)}",
            }
        except Exception as e:
            elapsed = time.time() - start
            return {
                "severity": resolve_severity(rule_id, impact).severity,
                "explanation": f"AI analysis failed: {str(e)}",
                "business_impact": "Requires manual review",
                "inference_time": elapsed,
                "model": self.classifier_model,
                "error": str(e),
            }

    async def classify_issue_with_rag(
        self,
        rule_id: str,
        impact: str,
        html_snippet: str,
        selector: str,
        violation_description: str = None,
    ) -> Dict[str, Any]:
        """Classify accessibility issue severity using RAG-enhanced prompt.

        This method uses semantic search to retrieve relevant WCAG guidelines
        and their severity criteria, then grounds the LLM classification in
        canonical WCAG definitions for consistent results.

        Args:
            rule_id: WCAG rule identifier (e.g., "image-alt")
            impact: Axe-core's impact level
            html_snippet: HTML code with the violation
            selector: CSS selector for the element
            violation_description: Optional plain-English description of violation

        Returns:
            Dict with severity, explanation, business_impact, inference_time,
            rag_guidelines (list of retrieved guidelines)
        """

        # Fallback to non-RAG classification if RAG is disabled
        if not self.enable_rag or not self.kb:
            logger.warning("RAG disabled, falling back to non-RAG classification")
            return await self.classify_issue(rule_id, impact, html_snippet, selector)

        start = time.time()

        try:
            # Create search query from violation context
            search_query = (
                f"{rule_id} {violation_description or ''} {html_snippet[:100]}"
            )

            # Retrieve relevant WCAG guidelines via semantic search
            guidelines = await self.kb.search(search_query, top_k=2, min_similarity=0.5)

            if not guidelines:
                logger.warning(
                    f"No relevant guidelines found for {rule_id}, falling back"
                )
                return await self.classify_issue(
                    rule_id, impact, html_snippet, selector
                )

            # Format guidelines for LLM context
            wcag_context = self.kb.format_guidelines_for_prompt(
                guidelines, include_examples=False
            )

            # Enhanced prompt with RAG context
            prompt = f"""You are an accessibility expert analyzing WCAG 2.2 violations.

{wcag_context}

**Violation to Classify:**
- Rule: {rule_id}
- Axe-core Impact: {impact}
- HTML: {html_snippet}
- Selector: {selector}
{f"- Description: {violation_description}" if violation_description else ""}

**CRITICAL INSTRUCTIONS:**
1. Use ONLY the severity criteria provided above to classify this violation
2. Match the violation details to the criteria descriptions exactly
3. Do NOT use subjective interpretation - follow the canonical criteria
4. Consider the HTML context to determine which severity level applies

Classify this issue's severity (Critical/High/Medium/Low) by matching it to the criteria.

Respond ONLY with valid JSON in this exact format:
{{
  "severity": "Critical|High|Medium|Low",
  "explanation": "2 sentence plain-English explanation referencing the criteria",
  "business_impact": "1 sentence about business/legal risk",
  "matched_criterion": "Quote the specific severity criterion that applies"
}}"""

            # Prepare prompt for thinking-mode models
            prepared_prompt = self._prepare_prompt(prompt, self.classifier_model)

            response = ollama.chat(
                model=self.classifier_model,
                messages=[{"role": "user", "content": prepared_prompt}],
                options={
                    "temperature": 0.0,  # Zero temp for maximum consistency with RAG
                    "num_predict": 300,
                },
            )

            elapsed = time.time() - start

            # Parse JSON response, cleaning any thinking mode artifacts
            content = self._clean_response(response["message"]["content"])
            json_start = content.find("{")
            json_end = content.rfind("}") + 1

            if json_start != -1 and json_end > json_start:
                json_str = content[json_start:json_end]
                result = json.loads(json_str)
            else:
                result = json.loads(content)

            # Severity is computed, never generated (see severity_rules.py).
            # RAG still grounds the explanation prose in canonical WCAG text.
            _resolution = resolve_severity(rule_id, impact)
            result["severity"] = _resolution.severity
            result["severity_source"] = _resolution.source

            # Add metadata
            result["inference_time"] = elapsed
            result["model"] = self.classifier_model
            result["rag_enabled"] = True
            result["rag_guidelines"] = [
                {
                    "rule_id": g["rule_id"],
                    "title": g["title"],
                    "similarity": g["similarity"],
                }
                for g in guidelines
            ]

            logger.info(
                f"RAG classification for {rule_id}: {result['severity']} (similarity: {guidelines[0]['similarity']:.2%})"
            )
            return result

        except json.JSONDecodeError as e:
            elapsed = time.time() - start
            logger.error(f"JSON parse error in RAG classification: {e}")
            return {
                "severity": resolve_severity(rule_id, impact).severity,
                "explanation": (
                    content[:200]
                    if "content" in locals()
                    else "Unable to parse AI response"
                ),
                "business_impact": "Requires manual review",
                "inference_time": elapsed,
                "model": self.classifier_model,
                "rag_enabled": True,
                "error": f"JSON parse error: {str(e)}",
            }
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"RAG classification failed: {e}")
            # Fallback to non-RAG classification on error
            return await self.classify_issue(rule_id, impact, html_snippet, selector)

    async def generate_fix(
        self,
        rule_id: str,
        violation_description: str,
        html_snippet: str,
        wcag_criterion: str = None,
        # Context-aware fix generation (optional)
        page_url: str = None,
        page_title: str = None,
        page_context: str = None,
    ) -> Dict[str, Any]:
        """Generate context-aware code fix using Qwen 2.5 Coder 7B.

        Args:
            rule_id: WCAG rule identifier
            violation_description: Plain-English description of the issue
            html_snippet: Current HTML with the violation
            wcag_criterion: WCAG success criterion (optional)
            page_url: URL of the page being scanned (optional)
            page_title: Title of the page (optional)
            page_context: Additional context about the page purpose (optional)

        Returns:
            Dict with fix_recommendation, model, inference_time
        """

        wcag_info = f" ({wcag_criterion})" if wcag_criterion else ""

        # Build context section if any context is provided
        context_section = ""
        if page_url or page_title or page_context:
            context_section = "\n**Page Context:**\n"
            if page_url:
                context_section += f"- URL: {page_url}\n"
            if page_title:
                context_section += f"- Page Title: {page_title}\n"
            if page_context:
                context_section += f"- Purpose: {page_context}\n"

            # Add specific guidance based on page type
            context_section += "\n⚠️ CRITICAL REQUIREMENT: You MUST analyze the page context above to determine the ACTUAL PURPOSE of this element.\n\n"

            # Infer page type and suggest appropriate labels
            if page_url and page_title:
                lower_url = page_url.lower()
                lower_title = page_title.lower()

                # Detect page type
                if (
                    "contact" in lower_url
                    or "contact" in lower_title
                    or "get in touch" in (page_context or "").lower()
                ):
                    context_section += "Based on the context, this appears to be a **CONTACT PAGE**. Use labels like:\n"
                    context_section += "- 'Send Message', 'Submit Contact Form', 'Send Inquiry', 'Contact Us'\n"
                    context_section += "- **DO NOT use 'Submit Order'** - this is NOT an e-commerce page!\n\n"
                elif (
                    "shop" in lower_url
                    or "cart" in lower_url
                    or "checkout" in lower_url
                    or "product" in lower_url
                ):
                    context_section += "Based on the context, this appears to be an **E-COMMERCE PAGE**. Use labels like:\n"
                    context_section += "- 'Add to Cart', 'Buy Now', 'Proceed to Checkout', 'Place Order'\n\n"
                elif (
                    "blog" in lower_url or "article" in lower_url or "post" in lower_url
                ):
                    context_section += "Based on the context, this appears to be a **BLOG/ARTICLE PAGE**. Use labels like:\n"
                    context_section += "- 'Subscribe', 'Post Comment', 'Share Article', 'Read More'\n\n"
                elif (
                    "login" in lower_url or "signin" in lower_url or "auth" in lower_url
                ):
                    context_section += "Based on the context, this appears to be a **LOGIN PAGE**. Use labels like:\n"
                    context_section += "- 'Log In', 'Sign In', 'Submit Credentials', 'Authenticate'\n\n"
                else:
                    context_section += "Analyze the page title and URL to infer the button's actual purpose. Generate labels that match the page's actual function.\n\n"

        prompt = f"""You are an expert web developer specializing in accessibility.

Violation: {violation_description}
Current HTML: {html_snippet}
WCAG Rule: {rule_id}{wcag_info}
{context_section}
Generate a complete fix including:
1. Updated HTML with proper ARIA/semantic tags (use context-appropriate labels)
2. CSS if needed for visual accessibility
3. JavaScript if needed for keyboard navigation
4. Step-by-step instructions for developers

Be specific - provide copy-paste code, not generic advice.

Respond in this format:
## Fixed HTML
[code]

## Additional CSS (if needed)
[code or "None required"]

## Additional JavaScript (if needed)
[code or "None required"]

## Implementation Steps
1. [step]
2. [step]
3. [step]"""

        # Prepare prompt for thinking-mode models
        prepared_prompt = self._prepare_prompt(prompt, self.coder_model)

        start = time.time()

        try:
            response = ollama.chat(
                model=self.coder_model,
                messages=[{"role": "user", "content": prepared_prompt}],
                options={
                    "temperature": 0.5,  # Moderate temp for creative but accurate fixes
                    "num_predict": 1000,  # Allow longer code responses
                },
            )

            elapsed = time.time() - start

            # Clean response of any thinking mode artifacts
            content = self._clean_response(response["message"]["content"])

            return {
                "fix_recommendation": content,
                "model": self.coder_model,
                "inference_time": elapsed,
            }

        except Exception as e:
            elapsed = time.time() - start
            return {
                "fix_recommendation": f"Unable to generate fix: {str(e)}",
                "model": self.coder_model,
                "inference_time": elapsed,
                "error": str(e),
            }

    async def summarize_report(
        self,
        critical_count: int,
        high_count: int,
        medium_count: int,
        low_count: int,
        total_issues: int,
        top_issues: list[str],
    ) -> Dict[str, Any]:
        """Generate executive summary using the classifier model.

        Args:
            critical_count: Number of critical issues
            high_count: Number of high severity issues
            medium_count: Number of medium severity issues
            low_count: Number of low severity issues
            total_issues: Total number of issues
            top_issues: List of top issue descriptions

        Returns:
            Dict with summary, inference_time
        """

        prompt = f"""You are a compliance consultant explaining technical issues to business owners.

Scan Results:
- Critical: {critical_count}
- High: {high_count}
- Medium: {medium_count}
- Low: {low_count}
- Total: {total_issues}

Top Issues:
{chr(10).join(f'- {issue}' for issue in top_issues[:5])}

Summarize in 3 short paragraphs:
1. Overall compliance status (pass/fail, biggest risks)
2. Top 3 priorities to fix immediately
3. Recommended next steps

Use business language, not technical jargon. Focus on lawsuit risk."""

        # Prepare prompt for thinking-mode models
        prepared_prompt = self._prepare_prompt(prompt, self.classifier_model)

        start = time.time()

        try:
            response = ollama.chat(
                model=self.classifier_model,
                messages=[{"role": "user", "content": prepared_prompt}],
                options={
                    "temperature": 0.7,  # Higher temp for natural writing
                    "num_predict": 400,
                },
            )

            elapsed = time.time() - start

            # Clean response of any thinking mode artifacts
            content = self._clean_response(response["message"]["content"])

            return {
                "summary": content,
                "model": self.classifier_model,
                "inference_time": elapsed,
            }

        except Exception as e:
            elapsed = time.time() - start
            return {
                "summary": f"Unable to generate summary: {str(e)}",
                "model": self.classifier_model,
                "inference_time": elapsed,
                "error": str(e),
            }

    def health_check(self) -> Dict[str, Any]:
        """Check if Ollama and models are available.

        Returns:
            Dict with status, model availability, and profile information
        """
        try:
            models_response = ollama.list()
            available_models = [m.model for m in models_response.models]

            classifier_available = any(
                self.classifier_model in name for name in available_models
            )
            coder_available = any(self.coder_model in name for name in available_models)

            # Get current profile
            current_profile = os.getenv("AELIRA_MODEL_PROFILE", "recommended")

            return {
                "status": (
                    "healthy"
                    if (classifier_available and coder_available)
                    else "partial"
                ),
                "ollama_host": self.host,
                "model_profile": current_profile,
                "classifier_model": self.classifier_model,
                "classifier_available": classifier_available,
                "coder_model": self.coder_model,
                "coder_available": coder_available,
                "rag_enabled": self.enable_rag,
                "total_models": len(available_models),
                "available_models": available_models,
                "available_profiles": list(MODEL_CONFIGS.keys()),
                "profile_descriptions": {
                    k: v["description"] for k, v in MODEL_CONFIGS.items()
                },
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "ollama_host": self.host,
                "model_profile": os.getenv("AELIRA_MODEL_PROFILE", "recommended"),
            }

    @staticmethod
    def get_recommended_models_for_hardware(
        ram_gb: int, has_gpu: bool = False
    ) -> Dict[str, Any]:
        """Get recommended model configuration based on hardware specs.

        Args:
            ram_gb: Available RAM in gigabytes
            has_gpu: Whether a GPU is available for inference

        Returns:
            Dict with recommended profile and models
        """
        if has_gpu:
            # GPU users can handle larger models
            return {
                "profile": "performance",
                "models": MODEL_CONFIGS["performance"],
                "reason": "GPU available - using larger models for maximum accuracy",
            }
        elif ram_gb >= 32:
            return {
                "profile": "performance",
                "models": MODEL_CONFIGS["performance"],
                "reason": "32GB+ RAM - using 3B model for all tasks",
            }
        elif ram_gb >= 16:
            return {
                "profile": "recommended",
                "models": MODEL_CONFIGS["recommended"],
                "reason": "16-32GB RAM - using 1.5B for classification, 3B for code fixes",
            }
        else:
            return {
                "profile": "minimal",
                "models": MODEL_CONFIGS["minimal"],
                "reason": "Under 16GB RAM - using single 1.5B model for all tasks",
            }
