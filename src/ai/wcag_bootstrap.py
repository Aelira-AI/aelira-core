"""Idempotent startup bootstrap for the bundled WCAG knowledge corpus."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

import httpx

from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES

if TYPE_CHECKING:
    from src.ai.wcag_knowledge_base import WCAGKnowledgeBase

logger = logging.getLogger(__name__)

_BOOTSTRAP_ADVISORY_KEY = 8_315_741_702_139
_EMBEDDING_LOCK_NAMESPACE = 831_574
_BOOTSTRAP_EMBEDDING_BUDGET_SECONDS = 60.0
_MODEL_GUIDE = "docs/deployment/local-ai-models.md"

UPSERT_GUIDELINE = """
INSERT INTO wcag_guidelines (
    rule_id, wcag_criterion, wcag_level, wcag_version, title, description,
    principle, guideline, severity_criteria, business_impact_template,
    technical_impact, fix_examples, best_practices, tags, act_rule_ids,
    related_rules, human_issue, human_fixed, updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
    $17, $18, now()
)
ON CONFLICT (rule_id) DO UPDATE SET
    wcag_criterion = EXCLUDED.wcag_criterion,
    wcag_level = EXCLUDED.wcag_level,
    wcag_version = EXCLUDED.wcag_version,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    principle = EXCLUDED.principle,
    guideline = EXCLUDED.guideline,
    severity_criteria = EXCLUDED.severity_criteria,
    business_impact_template = EXCLUDED.business_impact_template,
    technical_impact = EXCLUDED.technical_impact,
    fix_examples = EXCLUDED.fix_examples,
    best_practices = EXCLUDED.best_practices,
    tags = EXCLUDED.tags,
    act_rule_ids = EXCLUDED.act_rule_ids,
    related_rules = EXCLUDED.related_rules,
    human_issue = EXCLUDED.human_issue,
    human_fixed = EXCLUDED.human_fixed,
    updated_at = now()
"""

_MISSING_EMBEDDINGS = """
SELECT id, rule_id, wcag_criterion, wcag_level, title, description,
       principle, guideline, severity_criteria, tags
FROM wcag_guidelines
WHERE embedding IS NULL
ORDER BY id
"""

_STORE_EMBEDDING = """
UPDATE wcag_guidelines
SET embedding = $1::jsonb, updated_at = now()
WHERE id = $2 AND embedding IS NULL
"""

_EMBEDDING_IS_MISSING = """
SELECT embedding IS NULL
FROM wcag_guidelines
WHERE id = $1
"""


@dataclass(frozen=True)
class BootstrapResult:
    seeded: int
    embedded: int
    failed: int
    model_available: bool
    grounding_available: bool
    embedding_in_progress: bool = False


def deduplicate_guidelines(
    guidelines: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Keep the first bundled definition for each rule ID."""
    unique: dict[str, Mapping[str, Any]] = {}
    for guideline in guidelines:
        unique.setdefault(str(guideline["rule_id"]), guideline)
    return list(unique.values())


def guideline_seed_row(rule: Mapping[str, Any]) -> tuple[Any, ...]:
    """Map one bundled guideline onto the table's insert order."""
    return (
        rule["rule_id"],
        rule["wcag_criterion"],
        rule["wcag_level"],
        rule.get("wcag_version", "2.2"),
        rule["title"],
        rule["description"],
        rule["principle"],
        rule["guideline"],
        json.dumps(rule.get("severity_criteria") or {}),
        rule.get("business_impact_template"),
        rule.get("technical_impact"),
        json.dumps(rule.get("fix_examples") or []),
        list(rule.get("best_practices") or []),
        list(rule.get("tags") or []),
        list(rule.get("act_rule_ids") or []),
        list(rule.get("related_rules") or []),
        rule.get("human_issue"),
        rule.get("human_fixed"),
    )


def create_embedding_text(row: Mapping[str, Any]) -> str:
    """Create the canonical retrieval text for a stored guideline."""
    parts = [
        f"Rule: {row['rule_id']}",
        f"Title: {row['title']}",
        f"WCAG {row['wcag_criterion']} Level {row['wcag_level']}",
        f"Principle: {row['principle']}",
        f"Guideline: {row['guideline']}",
        f"Description: {row['description']}",
    ]
    severity = row.get("severity_criteria")
    if isinstance(severity, str):
        try:
            severity = json.loads(severity)
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(severity, dict):
        parts.append("Severity Criteria:")
        parts.extend(
            f"  {level}: {description}" for level, description in severity.items()
        )
    tags = row.get("tags")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def _model_is_available(configured: str, installed: Sequence[str]) -> bool:
    if ":" in configured:
        return configured in installed
    configured_base = configured.split(":", 1)[0]
    return any(name.split(":", 1)[0] == configured_base for name in installed)


async def _available_models(kb: WCAGKnowledgeBase) -> list[str] | None:
    try:
        response = await kb.http_client.get(f"{kb.ollama_host}/api/tags")
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", [])
        return [str(model.get("name", "")) for model in models if model.get("name")]
    except Exception:
        return None


async def _generate_embedding(
    kb: WCAGKnowledgeBase, row: Mapping[str, Any]
) -> list[float]:
    response = await kb.http_client.post(
        f"{kb.ollama_host}/api/embeddings",
        json={"model": kb.embedding_model, "prompt": create_embedding_text(row)},
    )
    response.raise_for_status()
    embedding = response.json().get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("embedding response did not contain a non-empty vector")
    if not all(isinstance(value, (int, float)) for value in embedding):
        raise ValueError("embedding response contained a non-numeric vector")
    return [float(value) for value in embedding]


async def bootstrap_wcag_knowledge_base(
    kb: WCAGKnowledgeBase,
    *,
    guidelines: Iterable[Mapping[str, Any]] = ALL_WCAG_GUIDELINES,
) -> BootstrapResult:
    """Seed an empty corpus once, then boundedly embed missing rows."""
    if kb.pool is None:
        raise RuntimeError("knowledge base must be initialized before bootstrap")

    async with kb.pool.acquire() as connection:
        locked = False
        try:
            await connection.fetchval(
                "SELECT pg_advisory_lock($1)", _BOOTSTRAP_ADVISORY_KEY
            )
            locked = True

            unique = deduplicate_guidelines(guidelines)
            existing = await connection.fetchval("SELECT count(*) FROM wcag_guidelines")
            seeded = 0
            if existing == 0:
                await connection.executemany(
                    UPSERT_GUIDELINE, [guideline_seed_row(rule) for rule in unique]
                )
                seeded = len(unique)
                logger.info("Seeded %d bundled WCAG guidelines", seeded)
        finally:
            if locked:
                await connection.fetchval(
                    "SELECT pg_advisory_unlock($1)", _BOOTSTRAP_ADVISORY_KEY
                )

        embedding_provider = (
            str(getattr(kb, "embedding_provider", "none") or "none").strip().lower()
        )
        if embedding_provider != "ollama":
            if embedding_provider == "none":
                logger.info(
                    "WCAG corpus is seeded; semantic grounding is disabled "
                    "(EMBEDDING_PROVIDER=none)"
                )
            else:
                logger.warning(
                    "WCAG corpus is seeded but semantic grounding is disabled: "
                    "embedding provider %s is unsupported. Supported values are "
                    "none and ollama.",
                    embedding_provider,
                )
            return BootstrapResult(
                seeded=seeded,
                embedded=0,
                failed=0,
                model_available=False,
                grounding_available=False,
            )

        if kb.http_client is None:
            logger.warning(
                "WCAG corpus is seeded but semantic grounding is unavailable: "
                "the configured Ollama embedding client is not initialized. See %s.",
                _MODEL_GUIDE,
            )
            return BootstrapResult(
                seeded=seeded,
                embedded=0,
                failed=0,
                model_available=False,
                grounding_available=False,
            )

        missing = await connection.fetch(_MISSING_EMBEDDINGS)
        installed = await _available_models(kb)
        if installed is None or not _model_is_available(kb.embedding_model, installed):
            logger.warning(
                "WCAG corpus is seeded but grounding is unavailable: embedding "
                "model %s is not reachable at the configured Ollama host. See %s.",
                kb.embedding_model,
                _MODEL_GUIDE,
            )
            return BootstrapResult(
                seeded=seeded,
                embedded=0,
                failed=0,
                model_available=False,
                grounding_available=False,
            )

        if not missing:
            return BootstrapResult(
                seeded=seeded,
                embedded=0,
                failed=0,
                model_available=True,
                grounding_available=True,
            )

        embedded = 0
        failed = 0
        deferred = 0
        try:
            async with asyncio.timeout(_BOOTSTRAP_EMBEDDING_BUDGET_SECONDS):
                for index, row in enumerate(missing):
                    row_locked = False
                    try:
                        row_locked = bool(
                            await connection.fetchval(
                                "SELECT pg_try_advisory_lock($1, $2)",
                                _EMBEDDING_LOCK_NAMESPACE,
                                row["id"],
                            )
                        )
                        if not row_locked:
                            deferred += 1
                            continue
                        if not await connection.fetchval(
                            _EMBEDDING_IS_MISSING, row["id"]
                        ):
                            continue
                        embedding = await _generate_embedding(kb, row)
                        await connection.execute(
                            _STORE_EMBEDDING, json.dumps(embedding), row["id"]
                        )
                        embedded += 1
                    except httpx.RequestError:
                        failed += len(missing) - index
                        break
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code >= 500:
                            failed += len(missing) - index
                            break
                        failed += 1
                    except Exception:
                        failed += 1
                    finally:
                        if row_locked:
                            await connection.fetchval(
                                "SELECT pg_advisory_unlock($1, $2)",
                                _EMBEDDING_LOCK_NAMESPACE,
                                row["id"],
                            )
        except TimeoutError:
            failed = len(missing) - embedded

        grounded_rows = await connection.fetchval(
            "SELECT count(*) FROM wcag_guidelines WHERE embedding IS NOT NULL"
        )
        if failed:
            logger.warning(
                "WCAG grounding remains incomplete: %d of %d guideline embeddings "
                "failed. See %s.",
                failed,
                len(missing),
                _MODEL_GUIDE,
            )
        else:
            logger.info("Generated %d WCAG guideline embeddings", embedded)
        return BootstrapResult(
            seeded=seeded,
            embedded=embedded,
            failed=failed,
            model_available=True,
            grounding_available=grounded_rows > 0,
            embedding_in_progress=deferred > 0 and failed == 0,
        )


__all__ = [
    "BootstrapResult",
    "UPSERT_GUIDELINE",
    "bootstrap_wcag_knowledge_base",
    "create_embedding_text",
    "deduplicate_guidelines",
    "guideline_seed_row",
]
