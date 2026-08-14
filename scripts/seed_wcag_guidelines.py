#!/usr/bin/env python3
"""Load the WCAG guideline corpus into the ``wcag_guidelines`` table.

The corpus has always lived in ``src/ai/wcag_seed_data*.py`` as Python data, and
the 2025-11-02 migration created the table, but nothing ever wrote the rows.
The result: ``wcag_guidelines`` was empty in production, every RAG lookup
retrieved nothing, and the knowledge base silently never influenced a single
classification. This script closes that gap.

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
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg  # noqa: E402

from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES  # noqa: E402

UPSERT = """
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


def deduplicate(guidelines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the first entry per rule_id.

    The corpus is assembled from three modules and a couple of rules appear in
    more than one. The detailed definitions come first, so first-wins keeps the
    richer entry rather than letting a terser duplicate overwrite it.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    for rule in guidelines:
        seen.setdefault(rule["rule_id"], rule)
    return list(seen.values())


def as_row(rule: Dict[str, Any]) -> tuple:
    """Map a corpus entry onto the table's column order."""
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


async def seed(database_url: str, dry_run: bool) -> int:
    rules = deduplicate(ALL_WCAG_GUIDELINES)
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
        await conn.executemany(UPSERT, [as_row(r) for r in rules])
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
