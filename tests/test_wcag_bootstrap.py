"""Contracts for automatic WCAG knowledge-base bootstrap (issue #139)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import pytest


def _rule(rule_id: str, *, title: str | None = None) -> dict:
    return {
        "rule_id": rule_id,
        "wcag_criterion": "1.1.1",
        "wcag_level": "A",
        "wcag_version": "2.2",
        "title": title or rule_id,
        "description": f"Description for {rule_id}",
        "principle": "Perceivable",
        "guideline": "Text Alternatives",
        "severity_criteria": {"high": "blocks access"},
        "business_impact_template": None,
        "technical_impact": None,
        "fix_examples": [],
        "best_practices": [],
        "tags": ["test"],
        "act_rule_ids": [],
        "related_rules": [],
        "human_issue": None,
        "human_fixed": None,
    }


class _Connection:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = {row["rule_id"]: dict(row) for row in rows or []}
        self.executemany_calls = 0
        self.sql: list[str] = []
        self._lock = asyncio.Lock()

    async def fetchval(self, query: str, *args):
        self.sql.append(query)
        if "pg_try_advisory_lock" in query:
            return True
        if "pg_advisory_lock" in query:
            await self._lock.acquire()
            return None
        if "pg_advisory_unlock" in query:
            if len(args) == 1 and self._lock.locked():
                self._lock.release()
            return True
        if "SELECT embedding IS NULL" in query:
            row_id = args[0]
            return any(
                row["id"] == row_id and row.get("embedding") is None
                for row in self.rows.values()
            )
        if "WHERE embedding IS NOT NULL" in query:
            return sum(row.get("embedding") is not None for row in self.rows.values())
        if "count(*) FROM wcag_guidelines" in query:
            return len(self.rows)
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def executemany(self, query: str, values: list[tuple]) -> None:
        self.sql.append(query)
        self.executemany_calls += 1
        for value in values:
            self.rows[value[0]] = {
                "id": len(self.rows) + 1,
                "rule_id": value[0],
                "wcag_criterion": value[1],
                "wcag_level": value[2],
                "wcag_version": value[3],
                "title": value[4],
                "description": value[5],
                "principle": value[6],
                "guideline": value[7],
                "severity_criteria": value[8],
                "business_impact_template": value[9],
                "technical_impact": value[10],
                "fix_examples": value[11],
                "best_practices": value[12],
                "tags": value[13],
                "human_issue": value[16],
                "human_fixed": value[17],
                "embedding": None,
            }

    async def fetchrow(self, query: str, *args):
        self.sql.append(query)
        if "WHERE rule_id = $1" not in query:
            raise AssertionError(f"unexpected fetchrow query: {query}")
        row = self.rows.get(args[0])
        return dict(row) if row is not None else None

    async def fetch(self, query: str):
        self.sql.append(query)
        return [dict(row) for row in self.rows.values() if row.get("embedding") is None]

    async def execute(self, query: str, *args) -> None:
        self.sql.append(query)
        if "UPDATE wcag_guidelines" not in query:
            raise AssertionError(f"unexpected execute query: {query}")
        import json

        embedding, row_id = args
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        for row in self.rows.values():
            if row["id"] == row_id and row.get("embedding") is None:
                row["embedding"] = embedding
                return


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _knowledge_base(
    connection: _Connection, handler, *, embedding_provider: str = "ollama"
) -> object:
    from src.ai.wcag_knowledge_base import WCAGKnowledgeBase

    kb = object.__new__(WCAGKnowledgeBase)
    kb.pool = _Pool(connection)
    kb.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kb.ollama_host = "http://ollama.test:11434"
    kb.embedding_model = "configured-embed"
    kb.embedding_provider = embedding_provider
    return kb


def _available_model_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(
            200, json={"models": [{"name": "configured-embed:latest"}]}
        )
    if request.url.path == "/api/embeddings":
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})
    raise AssertionError(f"unexpected request: {request.method} {request.url}")


def test_embedding_provider_defaults_to_none(monkeypatch):
    from src.ai.wcag_knowledge_base import WCAGKnowledgeBase

    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    kb = WCAGKnowledgeBase(database_url="postgresql://unused:unused@localhost/unused")

    assert kb.embedding_provider == "none"


@pytest.mark.asyncio
async def test_default_provider_seeds_without_contacting_ollama():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    requests: list[str] = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise AssertionError("embedding-disabled bootstrap must not use HTTP")

    connection = _Connection()
    kb = _knowledge_base(connection, reject_network, embedding_provider="none")
    try:
        result = await bootstrap_wcag_knowledge_base(
            kb, guidelines=[_rule("button-name")]
        )
        guideline = await kb.get_by_rule_id("button-name")
    finally:
        await kb.http_client.aclose()

    assert result.seeded == 1
    assert result.embedded == 0
    assert result.grounding_available is False
    assert requests == []
    assert guideline is not None
    assert guideline["rule_id"] == "button-name"


@pytest.mark.asyncio
async def test_unsupported_provider_skips_embeddings_nonfatally(caplog):
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    requests: list[str] = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise AssertionError("unsupported provider must not use Ollama")

    connection = _Connection()
    kb = _knowledge_base(connection, reject_network, embedding_provider="anthropic")
    try:
        with caplog.at_level(logging.WARNING, logger="src.ai.wcag_bootstrap"):
            result = await bootstrap_wcag_knowledge_base(
                kb, guidelines=[_rule("button-name")]
            )
    finally:
        await kb.http_client.aclose()

    assert result.seeded == 1
    assert result.failed == 0
    assert result.grounding_available is False
    assert requests == []
    assert len(caplog.records) == 1
    assert "anthropic" in caplog.records[0].message


@pytest.mark.asyncio
async def test_semantic_search_is_empty_without_an_embedding_provider():
    requests: list[str] = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise AssertionError("disabled semantic search must not use Ollama")

    connection = _Connection(
        [{**_rule("button-name"), "id": 1, "embedding": [0.1, 0.2]}]
    )
    kb = _knowledge_base(connection, reject_network, embedding_provider="none")
    try:
        results = await kb.search("button-name")
    finally:
        await kb.http_client.aclose()

    assert results == []
    assert requests == []


@pytest.mark.asyncio
async def test_empty_table_receives_unique_bundled_corpus():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    connection = _Connection()
    kb = _knowledge_base(connection, _available_model_handler)
    try:
        result = await bootstrap_wcag_knowledge_base(
            kb, guidelines=[_rule("one"), _rule("two")]
        )
    finally:
        await kb.http_client.aclose()

    assert result.seeded == 2
    assert set(connection.rows) == {"one", "two"}
    assert connection.executemany_calls == 1


@pytest.mark.asyncio
async def test_duplicate_rule_ids_keep_first_corpus_entry():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    connection = _Connection()
    kb = _knowledge_base(connection, _available_model_handler)
    try:
        await bootstrap_wcag_knowledge_base(
            kb,
            guidelines=[
                _rule("duplicate", title="richer first"),
                _rule("duplicate", title="terser second"),
            ],
        )
    finally:
        await kb.http_client.aclose()

    assert connection.rows["duplicate"]["title"] == "richer first"


@pytest.mark.asyncio
async def test_nonempty_table_receives_no_seed_writes():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    connection = _Connection([{**_rule("operator-row"), "id": 1, "embedding": [1.0]}])
    kb = _knowledge_base(connection, _available_model_handler)
    try:
        result = await bootstrap_wcag_knowledge_base(
            kb, guidelines=[_rule("bundled-row")]
        )
    finally:
        await kb.http_client.aclose()

    assert result.seeded == 0
    assert set(connection.rows) == {"operator-row"}
    assert connection.executemany_calls == 0


@pytest.mark.asyncio
async def test_available_model_embeds_every_missing_guideline():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    connection = _Connection(
        [
            {**_rule("one"), "id": 1, "embedding": None},
            {**_rule("two"), "id": 2, "embedding": None},
        ]
    )
    kb = _knowledge_base(connection, _available_model_handler)
    try:
        result = await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert result.embedded == 2
    assert result.failed == 0
    assert all(row["embedding"] == [0.1, 0.2, 0.3] for row in connection.rows.values())


@pytest.mark.asyncio
async def test_existing_embeddings_trigger_zero_embedding_requests():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "configured-embed:latest"}]}
            )
        raise AssertionError("embedded rows must not trigger an embedding request")

    connection = _Connection([{**_rule("done"), "id": 1, "embedding": [0.5, 0.5]}])
    kb = _knowledge_base(connection, handler)
    try:
        result = await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert result.embedded == 0
    assert result.model_available is True
    assert "/api/embeddings" not in requests


@pytest.mark.asyncio
async def test_embedded_corpus_still_requires_query_model(caplog):
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    connection = _Connection([{**_rule("done"), "id": 1, "embedding": [0.5, 0.5]}])
    kb = _knowledge_base(connection, handler)
    try:
        with caplog.at_level(logging.WARNING, logger="src.ai.wcag_bootstrap"):
            result = await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert result.model_available is False
    assert (
        len([record for record in caplog.records if record.levelno == logging.WARNING])
        == 1
    )


@pytest.mark.asyncio
async def test_model_unavailable_logs_one_actionable_warning(caplog):
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    connection = _Connection()
    kb = _knowledge_base(connection, handler)
    try:
        with caplog.at_level(logging.WARNING, logger="src.ai.wcag_bootstrap"):
            result = await bootstrap_wcag_knowledge_base(kb, guidelines=[_rule("one")])
    finally:
        await kb.http_client.aclose()

    warnings = [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "configured-embed" in warnings[0]
    assert "docs/deployment/local-ai-models.md" in warnings[0]
    assert result.model_available is False
    assert connection._lock.locked() is False


@pytest.mark.asyncio
async def test_startup_never_pulls_a_model():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"models": []})

    connection = _Connection()
    kb = _knowledge_base(connection, handler)
    try:
        await bootstrap_wcag_knowledge_base(kb, guidelines=[_rule("one")])
    finally:
        await kb.http_client.aclose()

    assert "/api/pull" not in paths


@pytest.mark.asyncio
async def test_partial_embedding_failure_is_reported_once(caplog):
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "configured-embed"}]})
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"embedding": [0.2, 0.4]})
        return httpx.Response(503, text="unavailable")

    connection = _Connection(
        [
            {**_rule("one"), "id": 1, "embedding": None},
            {**_rule("two"), "id": 2, "embedding": None},
        ]
    )
    kb = _knowledge_base(connection, handler)
    try:
        with caplog.at_level(logging.WARNING, logger="src.ai.wcag_bootstrap"):
            result = await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    warnings = [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert result.embedded == 1
    assert result.failed == 1
    assert len(warnings) == 1
    assert "1 of 2" in warnings[0]


@pytest.mark.asyncio
async def test_dependency_outage_stops_remaining_embedding_requests(caplog):
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    embedding_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal embedding_requests
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "configured-embed"}]})
        embedding_requests += 1
        return httpx.Response(503, text="unavailable")

    connection = _Connection(
        [
            {**_rule("one"), "id": 1, "embedding": None},
            {**_rule("two"), "id": 2, "embedding": None},
        ]
    )
    kb = _knowledge_base(connection, handler)
    try:
        with caplog.at_level(logging.WARNING, logger="src.ai.wcag_bootstrap"):
            result = await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert result.embedded == 0
    assert result.failed == 2
    assert result.grounding_available is False
    assert embedding_requests == 1
    assert len(caplog.records) == 1


@pytest.mark.asyncio
async def test_embedding_bootstrap_has_a_whole_run_time_budget(monkeypatch):
    import src.ai.wcag_bootstrap as bootstrap_module

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "configured-embed"}]})
        await asyncio.sleep(1)
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(bootstrap_module, "_BOOTSTRAP_EMBEDDING_BUDGET_SECONDS", 0.01)
    connection = _Connection([{**_rule("one"), "id": 1, "embedding": None}])
    kb = _knowledge_base(connection, handler)
    try:
        result = await bootstrap_module.bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert result.failed == 1
    assert result.grounding_available is False
    assert connection._lock.locked() is False


@pytest.mark.asyncio
async def test_configured_host_and_model_are_used():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base

    observed: list[tuple[str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            import json

            body = json.loads(request.content)
        observed.append((str(request.url), body))
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "configured-embed:latest"}]}
            )
        return httpx.Response(200, json={"embedding": [0.1]})

    connection = _Connection([{**_rule("one"), "id": 1, "embedding": None}])
    kb = _knowledge_base(connection, handler)
    try:
        await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    assert observed[0][0] == "http://ollama.test:11434/api/tags"
    assert observed[1][1]["model"] == "configured-embed"


@pytest.mark.asyncio
async def test_bootstrap_writes_only_real_bundled_corpus_ids():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base
    from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    connection = _Connection()
    kb = _knowledge_base(connection, handler)
    try:
        await bootstrap_wcag_knowledge_base(kb)
    finally:
        await kb.http_client.aclose()

    expected_ids = {rule["rule_id"] for rule in ALL_WCAG_GUIDELINES}
    assert set(connection.rows) == expected_ids
    assert len(connection.rows) <= len(ALL_WCAG_GUIDELINES)
