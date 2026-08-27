"""
WCAG Knowledge Base - RAG (Retrieval-Augmented Generation) for AI Consistency

This module provides semantic search over WCAG guidelines. It retrieves
relevant guidelines based on violation context to ground LLM output in
canonical WCAG definitions.

Similarity is computed in Python over embeddings stored as JSONB, deliberately
rather than via pgvector. The corpus is ~112 rows, so an index buys nothing at
this scale, and requiring a custom Postgres image would raise the bar for
anyone self-hosting the open-source core. Plain Postgres is enough.

Key Features:
- Semantic similarity search using cosine distance
- Query embeddings generated via Ollama (nomic-embed-text)
- Returns top-K most relevant WCAG guidelines with severity criteria
- Grounds LLM output in factual WCAG reference material

What this is for: retrieval grounds the *explanation* a violation is reported
with, so cited criteria are retrieved rather than recalled. Severity itself is
no longer an LLM output at all; see src/ai/severity_rules.py.
"""

import json
import math

import asyncpg
import httpx
from typing import List, Dict, Any, Optional
import os
import logging

logger = logging.getLogger(__name__)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Returns 0.0 rather than raising when either vector is empty, zero-length, or
    a different dimension from the other: a malformed stored embedding should
    drop that guideline out of the ranking, not fail the whole scan.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class WCAGKnowledgeBase:
    """
    WCAG Knowledge Base with semantic search over JSONB embeddings.

    Usage:
        kb = WCAGKnowledgeBase()
        await kb.initialize()

        # Search for relevant guidelines
        guidelines = await kb.search(
            query="button with no accessible name",
            top_k=3
        )

        # Use guidelines to ground LLM classification
        context = kb.format_guidelines_for_prompt(guidelines)
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        ollama_host: Optional[str] = None,
        embedding_model: str = "nomic-embed-text",
        embedding_provider: Optional[str] = None,
    ):
        """
        Initialize WCAG Knowledge Base.

        Args:
            database_url: PostgreSQL connection string (defaults to env var)
            ollama_host: Ollama API host (defaults to env var)
            embedding_model: Embedding model name (default: nomic-embed-text)
            embedding_provider: Semantic embedding backend. Only ``ollama`` is
                currently supported; defaults to ``EMBEDDING_PROVIDER`` or
                ``none``.
        """
        # Get DATABASE_URL from parameter or environment (required)
        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL must be provided or set via environment variable. "
                "See backend/.env.example for template."
            )
        self.ollama_host = ollama_host or os.getenv(
            "OLLAMA_HOST", "http://localhost:11434"
        )
        self.embedding_model = embedding_model
        configured_provider = (
            embedding_provider
            if embedding_provider is not None
            else os.getenv("EMBEDDING_PROVIDER", "none")
        )
        self.embedding_provider = str(configured_provider).strip().lower() or "none"

        self.pool: Optional[asyncpg.Pool] = None
        self.http_client: Optional[httpx.AsyncClient] = None

    async def initialize(self) -> None:
        """Initialize database connection pool and HTTP client."""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url, min_size=1, max_size=10
            )
            self.http_client = httpx.AsyncClient(timeout=30.0)
            logger.info("WCAG Knowledge Base initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize WCAG Knowledge Base: {e}")
            raise

    async def close(self) -> None:
        """Close database connection pool and HTTP client."""
        if self.pool:
            await self.pool.close()
        if self.http_client:
            await self.http_client.aclose()
        logger.info("WCAG Knowledge Base closed")

    async def bootstrap(self):
        """Seed and embed the bundled corpus when startup finds it uninitialized."""
        from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

        return await bootstrap_wcag_knowledge_base(self)

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for query text using Ollama.

        Args:
            text: Query text to embed

        Returns:
            768-dimensional embedding vector
        """
        if self.embedding_provider != "ollama":
            raise RuntimeError(
                "semantic embeddings are disabled; set EMBEDDING_PROVIDER=ollama "
                "to enable them"
            )
        if self.http_client is None:
            raise RuntimeError(
                "WCAGKnowledgeBase HTTP client not initialized - call initialize() first"
            )
        try:
            response = await self.http_client.post(
                f"{self.ollama_host}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
            )
            response.raise_for_status()
            data = response.json()
            return data["embedding"]
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    async def search(
        self, query: str, top_k: int = 3, min_similarity: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant WCAG guidelines using semantic similarity.

        Args:
            query: Search query (e.g., "button with no accessible name")
            top_k: Number of results to return (default: 3)
            min_similarity: Minimum cosine similarity threshold (default: 0.5)

        Returns:
            List of guideline dictionaries with similarity scores

        Example:
            guidelines = await kb.search(
                query="button missing label",
                top_k=3
            )

            # Result:
            [
                {
                    "rule_id": "button-name",
                    "title": "Buttons must have discernible text",
                    "wcag_criterion": "4.1.2",
                    "wcag_level": "A",
                    "description": "...",
                    "severity_criteria": {...},
                    "similarity": 0.89
                },
                ...
            ]
        """
        if not self.pool:
            raise RuntimeError(
                "Knowledge base not initialized. Call initialize() first."
            )

        if self.embedding_provider != "ollama":
            logger.debug(
                "Semantic WCAG search skipped because embedding provider %s "
                "is not enabled",
                self.embedding_provider,
            )
            return []

        try:
            # Generate embedding for query
            query_embedding = await self.generate_embedding(query)

            # Fetch candidates and rank in Python. See the module docstring for
            # why this is not a pgvector query.
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT
                        rule_id,
                        wcag_criterion,
                        wcag_level,
                        wcag_version,
                        title,
                        description,
                        principle,
                        guideline,
                        severity_criteria,
                        business_impact_template,
                        technical_impact,
                        fix_examples,
                        best_practices,
                        tags,
                        human_issue,
                        human_fixed,
                        embedding
                    FROM wcag_guidelines
                    WHERE embedding IS NOT NULL
                """)

            if not rows:
                logger.warning(
                    "WCAG knowledge base has no embedded guidelines; "
                    "install the configured embedding model and restart the API. "
                    "See docs/deployment/local-ai-models.md"
                )
                return []

            scored = []
            for row in rows:
                stored = row["embedding"]
                if isinstance(stored, str):
                    stored = json.loads(stored)
                similarity = _cosine_similarity(query_embedding, stored)
                if similarity < min_similarity:
                    continue
                scored.append((similarity, row))

            scored.sort(key=lambda pair: pair[0], reverse=True)

            results = []
            for similarity, row in scored[:top_k]:
                results.append(
                    {
                        "rule_id": row["rule_id"],
                        "wcag_criterion": row["wcag_criterion"],
                        "wcag_level": row["wcag_level"],
                        "wcag_version": row["wcag_version"],
                        "title": row["title"],
                        "description": row["description"],
                        "principle": row["principle"],
                        "guideline": row["guideline"],
                        "severity_criteria": row["severity_criteria"],
                        "business_impact_template": row["business_impact_template"],
                        "technical_impact": row["technical_impact"],
                        "fix_examples": row["fix_examples"],
                        "best_practices": row["best_practices"],
                        "tags": row["tags"],
                        "human_issue": row["human_issue"],
                        "human_fixed": row["human_fixed"],
                        "similarity": similarity,
                    }
                )

            logger.info(f"Found {len(results)} guidelines for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise

    async def get_by_rule_id(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific guideline by rule ID.

        Args:
            rule_id: The axe-core rule ID (e.g., "button-name")

        Returns:
            Guideline dictionary or None if not found
        """
        if not self.pool:
            raise RuntimeError(
                "Knowledge base not initialized. Call initialize() first."
            )

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        rule_id,
                        wcag_criterion,
                        wcag_level,
                        wcag_version,
                        title,
                        description,
                        principle,
                        guideline,
                        severity_criteria,
                        business_impact_template,
                        technical_impact,
                        fix_examples,
                        best_practices,
                        tags,
                        human_issue,
                        human_fixed
                    FROM wcag_guidelines
                    WHERE rule_id = $1
                """,
                    rule_id,
                )

                if not row:
                    return None

                return dict(row)

        except Exception as e:
            logger.error(f"Failed to retrieve rule {rule_id}: {e}")
            raise

    def format_guidelines_for_prompt(
        self, guidelines: List[Dict[str, Any]], include_examples: bool = True
    ) -> str:
        """
        Format retrieved guidelines for injection into LLM prompt.

        Args:
            guidelines: List of guideline dictionaries from search()
            include_examples: Include fix examples (default: True)

        Returns:
            Formatted string for LLM context

        Example:
            context = kb.format_guidelines_for_prompt(guidelines)

            prompt = f'''
            Use these WCAG guidelines to classify the violation:

            {context}

            Now classify this violation: ...
            '''
        """
        if not guidelines:
            return "No relevant WCAG guidelines found."

        formatted = "**WCAG GUIDELINES (Canonical Reference):**\n\n"

        for i, guideline in enumerate(guidelines, 1):
            formatted += (
                f"**{i}. {guideline['title']}** (Rule: {guideline['rule_id']})\n"
            )
            formatted += f"   WCAG {guideline['wcag_criterion']} Level {guideline['wcag_level']}\n"
            formatted += f"   Similarity: {guideline.get('similarity', 0):.2%}\n\n"

            formatted += f"   **Description:**\n   {guideline['description']}\n\n"

            # Add severity criteria (most important for consistent classification)
            if guideline.get("severity_criteria"):
                formatted += "   **Severity Classification Criteria:**\n"
                severity = guideline["severity_criteria"]

                if isinstance(severity, dict):
                    for level in ["critical", "high", "medium", "low"]:
                        if level in severity:
                            formatted += (
                                f"   - **{level.upper()}**: {severity[level]}\n"
                            )
                elif isinstance(severity, str):
                    # Handle JSON string
                    import json

                    try:
                        severity_dict = json.loads(severity)
                        for level in ["critical", "high", "medium", "low"]:
                            if level in severity_dict:
                                formatted += f"   - **{level.upper()}**: {severity_dict[level]}\n"
                    except Exception:
                        formatted += f"   {severity}\n"

                formatted += "\n"

            # Add fix examples if requested
            if include_examples and guideline.get("fix_examples"):
                examples = guideline["fix_examples"]
                if isinstance(examples, str):
                    import json

                    try:
                        examples = json.loads(examples)
                    except Exception:
                        pass

                if examples and len(examples) > 0:
                    formatted += "   **Fix Example:**\n"
                    example = examples[0] if isinstance(examples, list) else examples
                    if isinstance(example, dict):
                        formatted += f"   Before: {example.get('before', 'N/A')}\n"
                        formatted += f"   After: {example.get('after', 'N/A')}\n\n"

            formatted += "---\n\n"

        formatted += "**IMPORTANT**: Use the severity criteria above to classify violations consistently.\n"
        formatted += "Match the violation details to the criteria, not subjective interpretation.\n"

        return formatted

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# Convenience function for one-off searches
async def search_wcag_guidelines(
    query: str,
    top_k: int = 3,
    database_url: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience function for one-off WCAG guideline searches.

    Args:
        query: Search query
        top_k: Number of results (default: 3)
        database_url: Optional database URL
        ollama_host: Optional Ollama host

    Returns:
        List of relevant guidelines

    Example:
        guidelines = await search_wcag_guidelines(
            "form input missing label",
            top_k=2
        )
    """
    async with WCAGKnowledgeBase(database_url, ollama_host) as kb:
        return await kb.search(query, top_k)
