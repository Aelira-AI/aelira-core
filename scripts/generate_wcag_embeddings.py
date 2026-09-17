#!/usr/bin/env python3
"""Fill missing WCAG embeddings using the configured Ollama model.

From the repository root, with DATABASE_URL exported:
    python scripts/generate_wcag_embeddings.py

To explicitly allow downloading a missing model, add --pull. Existing vectors
are preserved, including those filled concurrently by API startup.
"""

import argparse
import asyncio
import json
import os
import sys

import asyncpg
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ai.wcag_bootstrap import (  # noqa: E402
    _EMBEDDING_IS_MISSING,
    _EMBEDDING_LOCK_NAMESPACE,
    _MISSING_EMBEDDINGS,
    _STORE_EMBEDDING,
    _model_is_available,
    create_embedding_text,
    validate_embedding,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:latest")


async def check_ollama_model(client: httpx.AsyncClient) -> bool:
    """Tagless Ollama requests mean :latest, not an arbitrary installed tag."""
    try:
        response = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=30.0)
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        configured = (
            EMBEDDING_MODEL if ":" in EMBEDDING_MODEL else f"{EMBEDDING_MODEL}:latest"
        )
        return _model_is_available(configured, models)
    except Exception:
        return False


async def pull_model(client: httpx.AsyncClient) -> bool:
    try:
        response = await client.post(
            f"{OLLAMA_HOST}/api/pull",
            json={"name": EMBEDDING_MODEL, "stream": False},
            timeout=300.0,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error") or payload.get("status") != "success":
            return False
        # A success response alone does not prove the configured tag is usable.
        return await check_ollama_model(client)
    except Exception:
        return False


async def generate_embedding(
    client: httpx.AsyncClient, text: str
) -> list[float] | None:
    try:
        response = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=30.0,
        )
        response.raise_for_status()
        return validate_embedding(response.json().get("embedding"))
    except Exception:
        print("Embedding generation failed.", file=sys.stderr)
        return None


async def repair_embeddings(conn, client: httpx.AsyncClient) -> int:
    rows = await conn.fetch(_MISSING_EMBEDDINGS)
    generated = failed = deferred = 0
    for row in rows:
        locked = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", _EMBEDDING_LOCK_NAMESPACE, row["id"]
        )
        if not locked:
            deferred += 1
            continue
        try:
            if not await conn.fetchval(_EMBEDDING_IS_MISSING, row["id"]):
                continue
            vector = await generate_embedding(client, create_embedding_text(dict(row)))
            if vector is None:
                failed += 1
                continue
            # The conditional write also protects against writers that do not
            # participate in the advisory lock protocol.
            result = await conn.execute(_STORE_EMBEDDING, json.dumps(vector), row["id"])
            generated += result == "UPDATE 1"
        finally:
            await conn.fetchval(
                "SELECT pg_advisory_unlock($1, $2)",
                _EMBEDDING_LOCK_NAMESPACE,
                row["id"],
            )
    print(f"Generated: {generated}; failed: {failed}; busy: {deferred}")
    if deferred:
        print("Another process is filling some rows; rerun after it finishes.")
    return 1 if failed or deferred else 0


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pull", action="store_true", help="allow downloading a missing model"
    )
    args = parser.parse_args(argv)
    if not DATABASE_URL:
        print("DATABASE_URL must be exported.", file=sys.stderr)
        return 1
    try:
        async with httpx.AsyncClient() as client:
            if not await check_ollama_model(client):
                if not args.pull:
                    print(
                        "Configured embedding model unavailable. Install it or rerun with --pull.",
                        file=sys.stderr,
                    )
                    return 1
                if not await pull_model(client):
                    print(
                        "Embedding model pull failed or model remains unavailable.",
                        file=sys.stderr,
                    )
                    return 1
                print("Model pulled successfully!")
            conn = await asyncpg.connect(
                DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
            )
            try:
                return await repair_embeddings(conn, client)
            finally:
                await conn.close()
    except Exception:
        # HTTP/driver exceptions can contain credentials; keep failure output
        # independent of exception text and connection-string representation.
        print(
            "Embedding repair failed; check database and Ollama configuration.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
