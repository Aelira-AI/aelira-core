#!/usr/bin/env python3
"""
Generate embeddings for all WCAG guidelines in the database.

This script:
1. Fetches all WCAG guidelines without embeddings
2. Generates embeddings using Ollama (nomic-embed-text)
3. Updates the database with the embeddings

Usage:
    cd backend
    ./venv/bin/python scripts/generate_wcag_embeddings.py
"""

import asyncio
import asyncpg
import httpx
import os
import json
import sys
from typing import List, Optional

# Configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://aelira:localdev123@localhost:5432/aelira_dev"
)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = "nomic-embed-text"


async def check_ollama_model(client: httpx.AsyncClient) -> bool:
    """Check if embedding model is available."""
    try:
        response = await client.get(f"{OLLAMA_HOST}/api/tags")
        if response.status_code == 200:
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]
            if any(EMBEDDING_MODEL in m for m in models):
                return True
            print(f"Model {EMBEDDING_MODEL} not found. Available: {models}")
            print(f"Run: ollama pull {EMBEDDING_MODEL}")
            return False
        return False
    except Exception as e:
        print(f"Failed to connect to Ollama: {e}")
        return False


async def generate_embedding(
    client: httpx.AsyncClient, text: str
) -> Optional[List[float]]:
    """Generate embedding for text using Ollama."""
    try:
        response = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["embedding"]
    except Exception as e:
        print(f"Failed to generate embedding: {e}")
        return None


def create_embedding_text(row: dict) -> str:
    """Create rich text for embedding from guideline data."""
    # Combine relevant fields for semantic search
    parts = [
        f"Rule: {row['rule_id']}",
        f"Title: {row['title']}",
        f"WCAG {row['wcag_criterion']} Level {row['wcag_level']}",
        f"Principle: {row['principle']}",
        f"Guideline: {row['guideline']}",
        f"Description: {row['description']}",
    ]

    # Add severity criteria if available
    if row.get("severity_criteria"):
        severity = row["severity_criteria"]
        if isinstance(severity, str):
            try:
                severity = json.loads(severity)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(severity, dict):
            parts.append("Severity Criteria:")
            for level, desc in severity.items():
                parts.append(f"  {level}: {desc}")

    # Add tags
    if row.get("tags"):
        tags = row["tags"]
        if tags:
            parts.append(f"Tags: {', '.join(tags)}")

    return "\n".join(parts)


async def main():
    """Generate embeddings for all WCAG guidelines."""
    print("=" * 60)
    print("WCAG Guideline Embedding Generator")
    print("=" * 60)
    print(
        f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}"
    )
    print(f"Ollama: {OLLAMA_HOST}")
    print(f"Model: {EMBEDDING_MODEL}")
    print()

    # Create HTTP client
    async with httpx.AsyncClient() as client:
        # Check Ollama model
        print("Checking Ollama model...")
        if not await check_ollama_model(client):
            print("\nPulling embedding model...")
            # Try to pull the model
            try:
                await client.post(
                    f"{OLLAMA_HOST}/api/pull",
                    json={"name": EMBEDDING_MODEL},
                    timeout=300.0,
                )
                print("Model pulled successfully!")
            except Exception as e:
                print(f"Failed to pull model: {e}")
                print(f"Please run: ollama pull {EMBEDDING_MODEL}")
                return 1

        print("Ollama model ready!\n")

        # Connect to database
        print("Connecting to database...")
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            print("Connected!\n")
        except Exception as e:
            print(f"Failed to connect to database: {e}")
            return 1

        try:
            # Get all guidelines without embeddings
            rows = await conn.fetch("""
                SELECT id, rule_id, wcag_criterion, wcag_level, title,
                       description, principle, guideline, severity_criteria, tags
                FROM wcag_guidelines
                WHERE embedding IS NULL
                ORDER BY id
            """)

            total = len(rows)
            print(f"Found {total} guidelines to process\n")

            if total == 0:
                print("All guidelines already have embeddings!")
                return 0

            # Process each guideline
            success = 0
            failed = 0

            for i, row in enumerate(rows, 1):
                rule_id = row["rule_id"]
                print(f"[{i}/{total}] Processing: {rule_id}...", end=" ")

                # Create embedding text
                text = create_embedding_text(dict(row))

                # Generate embedding
                embedding = await generate_embedding(client, text)

                if embedding:
                    # Stored as JSONB, not pgvector: the corpus is ~112 rows,
                    # so an index buys nothing and plain Postgres is enough for
                    # anyone self-hosting the open-source core.
                    embedding_str = json.dumps(embedding)

                    # Update database
                    await conn.execute(
                        """
                        UPDATE wcag_guidelines
                        SET embedding = $1::jsonb, updated_at = NOW()
                        WHERE id = $2
                    """,
                        embedding_str,
                        row["id"],
                    )

                    print(f"OK (dim={len(embedding)})")
                    success += 1
                else:
                    print("FAILED")
                    failed += 1

                # Small delay to avoid overwhelming Ollama
                await asyncio.sleep(0.1)

            print()
            print("=" * 60)
            print(f"COMPLETE: {success}/{total} embeddings generated")
            if failed > 0:
                print(f"FAILED: {failed} guidelines")
            print("=" * 60)

            # Verify
            count = await conn.fetchval("""
                SELECT COUNT(*) FROM wcag_guidelines WHERE embedding IS NOT NULL
            """)
            print(f"\nTotal guidelines with embeddings: {count}")

            return 0 if failed == 0 else 1

        finally:
            await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
