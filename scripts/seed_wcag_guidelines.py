#!/usr/bin/env python3
"""Load the WCAG guideline corpus into the ``wcag_guidelines`` table.

API startup now seeds an empty table automatically. This explicit entry point
remains available for operator repair and controlled corpus refreshes.

Idempotent: rows are upserted on ``rule_id``, so re-running updates existing
guidelines rather than duplicating them. Safe to run on every deploy.

Embeddings are deliberately not generated here. Run
``scripts/generate_wcag_embeddings.py`` afterwards, which fills ``embedding``
for any row still missing one.

Usage (inside the API container):
    python scripts/seed_wcag_guidelines.py
    python scripts/seed_wcag_guidelines.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg  # noqa: E402

from src.ai.wcag_bootstrap import (  # noqa: E402
    UPSERT_GUIDELINE,
    deduplicate_guidelines,
    guideline_seed_row,
)
from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES  # noqa: E402


async def seed(database_url: str, dry_run: bool) -> int:
    rules = deduplicate_guidelines(ALL_WCAG_GUIDELINES)
    print(f"corpus: {len(ALL_WCAG_GUIDELINES)} entries, {len(rules)} unique rule_ids")

    if dry_run:
        for rule in rules[:5]:
            print(
                f"  would upsert {rule['rule_id']} ({rule['wcag_criterion']} {rule['wcag_level']})"
            )
        print(f"  ... {max(0, len(rules) - 5)} more")
        return 0

    conn = await asyncpg.connect(database_url)
    try:
        before = await conn.fetchval("SELECT count(*) FROM wcag_guidelines")
        await conn.executemany(
            UPSERT_GUIDELINE, [guideline_seed_row(rule) for rule in rules]
        )
        after = await conn.fetchval("SELECT count(*) FROM wcag_guidelines")
        embedded = await conn.fetchval(
            "SELECT count(*) FROM wcag_guidelines WHERE embedding IS NOT NULL"
        )
        print(f"rows: {before} -> {after} ({embedded} with embeddings)")
        if after < len(rules):
            print("WARNING: fewer rows than unique rule_ids; check for insert failures")
            return 1
    finally:
        await conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written, touch nothing",
    )
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url and not args.dry_run:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    # asyncpg does not accept SQLAlchemy's +asyncpg dialect suffix.
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    return asyncio.run(seed(database_url, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
