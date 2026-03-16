"""
Populate WCAG Knowledge Base with Embeddings

This script:
1. Loads all 114 WCAG guidelines from seed data
2. Generates embeddings using Ollama (nomic-embed-text model)
3. Inserts/updates rules in PostgreSQL with pgvector

Usage:
    python populate_wcag_knowledge_base.py

Requirements:
    - PostgreSQL with pgvector extension running
    - Ollama running with nomic-embed-text model
    - Database tables created (run create_wcag_table_pgvector.sql first)
"""

import asyncio
import json
import os
import sys
from typing import List, Dict, Any

import asyncpg
import httpx
from tqdm import tqdm

# Import seed data
sys.path.insert(0, os.path.dirname(__file__))
from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set!")
    print(
        "Set it via: export DATABASE_URL='postgresql://user:password@localhost:5432/dbname'"
    )
    print("Or create a .env file in the backend/ directory (see .env.example)")
    sys.exit(1)

# Validate no unsafe defaults
if any(
    pattern in DATABASE_URL
    for pattern in ["dev_password_change_in_prod", "aelira_password", "change_me"]
):
    print("ERROR: Unsafe DATABASE_URL detected!")
    print("Do not use default/placeholder passwords in DATABASE_URL")
    sys.exit(1)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = "nomic-embed-text"  # 768-dimensional embeddings


async def generate_embedding(text: str, client: httpx.AsyncClient) -> List[float]:
    """Generate embedding using Ollama nomic-embed-text model"""
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
        print(f"Error generating embedding: {e}")
        raise


def create_embedding_text(rule: Dict[str, Any]) -> str:
    """
    Create text for embedding generation.
    Combines multiple fields for better semantic search.
    """
    parts = [
        f"Rule: {rule['rule_id']}",
        f"WCAG: {rule['wcag_criterion']} Level {rule['wcag_level']}",
        f"Title: {rule['title']}",
        f"Description: {rule['description']}",
        f"Principle: {rule['principle']}",
        f"Guideline: {rule['guideline']}",
    ]

    # Add severity criteria
    if isinstance(rule["severity_criteria"], dict):
        severity_text = " ".join(
            [f"{level}: {desc}" for level, desc in rule["severity_criteria"].items()]
        )
        parts.append(f"Severity: {severity_text}")

    # Add tags
    if rule.get("tags"):
        parts.append(f"Tags: {', '.join(rule['tags'])}")

    return " | ".join(parts)


async def insert_rule(
    pool: asyncpg.Pool, rule: Dict[str, Any], embedding: List[float]
) -> None:
    """Insert or update a single rule in the database"""
    try:
        async with pool.acquire() as conn:
            # Convert embedding list to PostgreSQL-compatible format
            # pgvector expects the vector as a string representation
            embedding_str = f"[{','.join(map(str, embedding))}]"

            await conn.execute(
                """
                INSERT INTO wcag_guidelines (
                    rule_id, wcag_criterion, wcag_level, wcag_version,
                    title, description, principle, guideline,
                    severity_criteria, business_impact_template, technical_impact,
                    fix_examples, best_practices, tags, act_rule_ids, related_rules,
                    human_issue, human_fixed,
                    embedding
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19::vector
                )
                ON CONFLICT (rule_id) DO UPDATE SET
                    wcag_criterion = EXCLUDED.wcag_criterion,
                    wcag_level = EXCLUDED.wcag_level,
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
                    embedding = EXCLUDED.embedding,
                    updated_at = NOW()
            """,
                rule["rule_id"],
                rule["wcag_criterion"],
                rule["wcag_level"],
                rule.get("wcag_version", "2.2"),
                rule["title"],
                rule["description"],
                rule["principle"],
                rule["guideline"],
                json.dumps(rule["severity_criteria"]),
                rule.get("business_impact_template", ""),
                rule.get("technical_impact", ""),
                json.dumps(rule.get("fix_examples", [])),
                rule.get("best_practices", []),
                rule.get("tags", []),
                rule.get("act_rule_ids", []),
                rule.get("related_rules", []),
                rule.get("human_issue"),
                rule.get("human_fixed"),
                embedding_str,
            )
    except Exception as e:
        print(f"Error inserting rule {rule['rule_id']}: {e}")
        raise


async def populate_database():
    """Main function to populate database with all rules"""
    print("=" * 80)
    print("WCAG Knowledge Base Population Script")
    print("=" * 80)
    print(f"\nTotal rules to process: {len(ALL_WCAG_GUIDELINES)}")
    print(
        f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}"
    )
    print(f"Ollama: {OLLAMA_HOST}")
    print(f"Embedding model: {EMBEDDING_MODEL}")
    print()

    # Check Ollama availability
    print("Checking Ollama availability...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
            response.raise_for_status()
            models = response.json().get("models", [])
            model_names = [m["name"] for m in models]

            if (
                EMBEDDING_MODEL not in model_names
                and f"{EMBEDDING_MODEL}:latest" not in model_names
            ):
                print(f"❌ Error: {EMBEDDING_MODEL} not found in Ollama.")
                print(f"Available models: {', '.join(model_names)}")
                print(f"\nPlease run: ollama pull {EMBEDDING_MODEL}")
                return

            print(f"✅ Ollama is running with {EMBEDDING_MODEL} model")
        except Exception as e:
            print(f"❌ Error connecting to Ollama: {e}")
            print("\nPlease ensure Ollama is running:")
            print("  docker-compose -f docker-compose.dev.yml up -d ollama")
            return

    # Connect to database
    print("\nConnecting to database...")
    try:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        print("✅ Database connection established")
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        print("\nPlease ensure PostgreSQL is running:")
        print("  docker-compose -f docker-compose.dev.yml up -d postgres")
        return

    # Check if table exists
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'wcag_guidelines'
            )
        """)

        if not table_exists:
            print("❌ Error: wcag_guidelines table does not exist")
            print("\nPlease create the table first:")
            print(
                "  docker cp create_wcag_table_pgvector.sql aelira-postgres-dev:/tmp/"
            )
            print(
                "  docker exec aelira-postgres-dev psql -U aelira -d aelira_dev -f /tmp/create_wcag_table_pgvector.sql"
            )
            await pool.close()
            return

        print("✅ wcag_guidelines table exists")

    # Process rules
    print(f"\nProcessing {len(ALL_WCAG_GUIDELINES)} rules...")
    print("(Generating embeddings and inserting into database)")
    print()

    async with httpx.AsyncClient() as client:
        for i, rule in enumerate(tqdm(ALL_WCAG_GUIDELINES, desc="Processing rules"), 1):
            try:
                # Create embedding text
                embedding_text = create_embedding_text(rule)

                # Generate embedding
                embedding = await generate_embedding(embedding_text, client)

                # Insert into database
                await insert_rule(pool, rule, embedding)

            except Exception as e:
                print(
                    f"\n❌ Error processing rule {rule.get('rule_id', 'unknown')}: {e}"
                )
                continue

    # Verify insertion
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM wcag_guidelines")
        print(f"\n✅ Successfully inserted/updated {count} rules in database")

        # Show sample
        sample = await conn.fetchrow("""
            SELECT rule_id, title, wcag_criterion, wcag_level
            FROM wcag_guidelines
            LIMIT 1
        """)

        if sample:
            print("\nSample rule:")
            print(f"  Rule ID: {sample['rule_id']}")
            print(f"  Title: {sample['title']}")
            print(f"  WCAG: {sample['wcag_criterion']} Level {sample['wcag_level']}")
            print("  Embedding: ✅ Vector stored (768 dimensions)")

    await pool.close()
    print("\n" + "=" * 80)
    print("✅ WCAG Knowledge Base population complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(populate_database())
