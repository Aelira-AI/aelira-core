"""Manual maintenance must report failure without losing results or credentials."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Maintenance tests supply database doubles; no server is needed."""
    yield


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"operator_test_{name}", ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        PermissionError(),
        subprocess.CalledProcessError(1, "ollama"),
        subprocess.TimeoutExpired("ollama", 10),
    ],
)
def test_optional_host_cli_failure_preserves_metadata(monkeypatch, failure):
    module = load_script("evaluate_local_models")
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.platform, "platform", lambda: "synthetic Linux")
    monkeypatch.setattr(module.platform, "processor", lambda: "synthetic processor")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(module.subprocess, "run", fail)
    metadata = module._host_evidence()
    assert metadata["ollama_version"] == "unavailable"
    assert metadata["memory_bytes"] > 0


def test_present_host_cli_preserves_real_version(monkeypatch):
    module = load_script("evaluate_local_models")
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.platform, "platform", lambda: "synthetic Linux")
    monkeypatch.setattr(module.platform, "processor", lambda: "synthetic processor")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="ollama version test\n"
        ),
    )
    assert module._host_evidence()["ollama_version"] == "ollama version test"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["postgresql", "postgresql+asyncpg"])
async def test_description_repair_omits_credentials_and_normalizes_url(
    monkeypatch, capsys, scheme
):
    module = load_script("populate_human_descriptions")
    url = f"{scheme}://synthetic-user:synthetic-password@localhost/test_db"
    monkeypatch.setenv("DATABASE_URL", url)
    connection = AsyncMock()
    connection.fetch.return_value = []
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(module.asyncpg, "connect", connect)
    assert await module.main() == 0
    connect.assert_awaited_once_with(
        url.replace("postgresql+asyncpg://", "postgresql://")
    )
    output = capsys.readouterr()
    assert "synthetic-password" not in output.out + output.err
    assert "synthetic-user" not in output.out + output.err
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_description_connection_error_is_redacted(monkeypatch, capsys):
    module = load_script("populate_human_descriptions")
    url = "postgresql://synthetic-user:synthetic-password@localhost/test_db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(
        module.asyncpg, "connect", AsyncMock(side_effect=RuntimeError(url))
    )
    assert await module.main() == 1
    output = capsys.readouterr()
    assert "synthetic-password" not in output.out + output.err
    assert "failed" in output.err.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "installed", [["nomic-embed-text-other:latest"], ["nomic-embed-text:v2"]]
)
async def test_embedding_model_matching_is_exact(monkeypatch, installed):
    module = load_script("generate_wcag_embeddings")
    monkeypatch.setattr(module, "EMBEDDING_MODEL", "nomic-embed-text")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"models": [{"name": n} for n in installed]}
            )
        )
    ) as client:
        assert not await module.check_ollama_model(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector", [[], ["bad"], [True], [float("inf")], {"x": 1}, None]
)
async def test_embedding_repair_rejects_invalid_vectors(monkeypatch, vector):
    module = load_script("generate_wcag_embeddings")
    # Return data directly so this also tests non-finite values that a JSON
    # decoder can accept without the HTTP test encoder rejecting them first.
    client = AsyncMock()
    response = httpx.Response(
        200, request=httpx.Request("POST", "http://localhost/api/embeddings")
    )
    monkeypatch.setattr(response, "json", lambda: {"embedding": vector})
    client.post.return_value = response
    assert await module.generate_embedding(client, "synthetic guideline") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        ({"error": "failed"}, 200),
        ({"error": "failed"}, 500),
        ({"status": "pulling"}, 200),
    ],
)
async def test_embedding_pull_failure_never_reports_success(
    monkeypatch, capsys, payload, status
):
    module = load_script("generate_wcag_embeddings")
    monkeypatch.setattr(module, "DATABASE_URL", "postgresql://localhost/test_db")
    original_client = httpx.AsyncClient

    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda: original_client(transport=httpx.MockTransport(handle)),
    )
    connect = AsyncMock()
    monkeypatch.setattr(module.asyncpg, "connect", connect)
    assert await module.main(["--pull"]) == 1
    connect.assert_not_awaited()
    output = capsys.readouterr()
    assert "Model pulled successfully" not in output.out


@pytest.mark.asyncio
@pytest.mark.parametrize("available_after_pull", [False, True])
async def test_embedding_pull_requires_verified_available_model(
    monkeypatch, available_after_pull
):
    module = load_script("generate_wcag_embeddings")
    monkeypatch.setattr(
        module, "DATABASE_URL", "postgresql+asyncpg://localhost/test_db"
    )
    monkeypatch.setattr(module, "EMBEDDING_MODEL", "nomic-embed-text:latest")
    original_client = httpx.AsyncClient
    pulled = False

    def handle(request):
        nonlocal pulled
        if request.url.path == "/api/pull":
            assert json.loads(request.content) == {
                "name": "nomic-embed-text:latest",
                "stream": False,
            }
            pulled = True
            return httpx.Response(200, json={"status": "success"})
        models = (
            [{"name": "nomic-embed-text:latest"}]
            if pulled and available_after_pull
            else []
        )
        return httpx.Response(200, json={"models": models})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda: original_client(transport=httpx.MockTransport(handle)),
    )
    connection = AsyncMock()
    connection.fetch.return_value = []
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(module.asyncpg, "connect", connect)
    assert await module.main(["--pull"]) == (0 if available_after_pull else 1)
    if available_after_pull:
        connect.assert_awaited_once_with("postgresql://localhost/test_db")
        connection.close.assert_awaited_once()
    else:
        connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_model_never_downloads_without_explicit_option(monkeypatch):
    module = load_script("generate_wcag_embeddings")
    monkeypatch.setattr(module, "DATABASE_URL", "postgresql://localhost/test_db")
    original_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda: original_client(transport=httpx.MockTransport(handle)),
    )
    assert await module.main([]) == 1
    assert requests == ["/api/tags"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "busy",
        "filled",
        "filled_during_request",
        "invalid_vector",
        "write_error",
    ],
)
async def test_embedding_repair_respects_row_ownership_and_completion(
    monkeypatch, state
):
    module = load_script("generate_wcag_embeddings")
    row = dict(
        id=1,
        rule_id="test-rule",
        title="Test",
        wcag_criterion="1.1.1",
        wcag_level="A",
        principle="Test",
        guideline="Test",
        description="Test",
    )
    conn = AsyncMock()
    conn.fetch.return_value = [row]
    stored = [9.0] if state == "filled" else None
    calls = []

    async def fetchval(query, *args):
        calls.append(query)
        if "pg_try_advisory_lock" in query:
            return state != "busy"
        if "pg_advisory_unlock" in query:
            return True
        return stored is None

    async def execute(query, value, row_id):
        nonlocal stored
        assert row_id == 1
        assert "AND embedding IS NULL" in query
        if state == "write_error":
            raise RuntimeError("synthetic write failure")
        if stored is not None:
            return "UPDATE 0"
        stored = json.loads(value)
        return "UPDATE 1"

    conn.fetchval.side_effect = fetchval
    conn.execute.side_effect = execute
    requests = []

    def handle(request):
        nonlocal stored
        requests.append(request)
        if state == "filled_during_request":
            stored = [9.0]
        return httpx.Response(
            200,
            json={
                "embedding": ["invalid"] if state == "invalid_vector" else [1.0, 2.0]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if state == "write_error":
            with pytest.raises(RuntimeError):
                await module.repair_embeddings(conn, client)
        else:
            assert await module.repair_embeddings(conn, client) == (
                1 if state in {"busy", "invalid_vector"} else 0
            )
    if state == "busy":
        assert not requests
        assert not any("pg_advisory_unlock" in q for q in calls)
    else:
        assert sum("pg_advisory_unlock" in q for q in calls) == 1
    if state == "missing":
        assert stored == [1.0, 2.0]
    elif state in {"filled", "filled_during_request"}:
        assert stored == [9.0]
    else:
        assert stored is None
    if state in {"busy", "filled", "invalid_vector"}:
        conn.execute.assert_not_awaited()
