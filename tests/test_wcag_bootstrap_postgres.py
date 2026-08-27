"""PostgreSQL proof that concurrent API workers seed the corpus once."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import httpx
import pytest

CREATE_TABLE = """
CREATE TABLE wcag_guidelines (
    id serial PRIMARY KEY,
    rule_id varchar(50) UNIQUE NOT NULL,
    wcag_criterion varchar(20) NOT NULL,
    wcag_level varchar(5) NOT NULL,
    wcag_version varchar(10) NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    principle varchar(50) NOT NULL,
    guideline varchar(100) NOT NULL,
    severity_criteria jsonb NOT NULL,
    business_impact_template text,
    technical_impact text,
    fix_examples jsonb,
    best_practices text[],
    tags text[],
    act_rule_ids text[],
    related_rules text[],
    embedding jsonb,
    human_issue text,
    human_fixed text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
)
"""


def _asyncpg_url() -> str:
    url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/aelira_test",
    )
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_bootstrap_seeds_once_and_embeds_each_row_once():
    from src.ai.wcag_bootstrap import bootstrap_wcag_knowledge_base
    from src.ai.wcag_knowledge_base import WCAGKnowledgeBase
    from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES

    database_url = _asyncpg_url()
    try:
        admin = await asyncpg.connect(database_url)
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL unavailable: {exc}")

    schema = f"wcag_bootstrap_{uuid.uuid4().hex}"
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        pool = await asyncpg.create_pool(
            database_url,
            min_size=2,
            max_size=2,
            server_settings={"search_path": f'"{schema}",public'},
        )
        async with pool.acquire() as connection:
            await connection.execute(CREATE_TABLE)

        embedding_requests = 0

        def available_model(request: httpx.Request) -> httpx.Response:
            nonlocal embedding_requests
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200, json={"models": [{"name": "configured-embed"}]}
                )
            assert request.url.path == "/api/embeddings"
            embedding_requests += 1
            return httpx.Response(200, json={"embedding": [0.1, 0.2]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(available_model))
        kb = object.__new__(WCAGKnowledgeBase)
        kb.pool = pool
        kb.http_client = client
        kb.ollama_host = "http://ollama.test:11434"
        kb.embedding_model = "configured-embed"
        kb.embedding_provider = "ollama"
        try:
            first, second = await asyncio.gather(
                bootstrap_wcag_knowledge_base(kb),
                bootstrap_wcag_knowledge_base(kb),
            )
            async with pool.acquire() as connection:
                count = await connection.fetchval(
                    "SELECT count(*) FROM wcag_guidelines"
                )
        finally:
            await client.aclose()
            await pool.close()

        expected = len({rule["rule_id"] for rule in ALL_WCAG_GUIDELINES})
        assert sorted([first.seeded, second.seeded]) == [0, expected]
        assert count == expected
        assert embedding_requests == expected
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
