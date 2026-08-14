#!/usr/bin/env python3
"""
Populate human-friendly descriptions in the WCAG knowledge base.

This script updates existing wcag_guidelines records with human_issue and
human_fixed descriptions from the wcag_human_descriptions module.

Usage:
    # From backend directory
    python scripts/populate_human_descriptions.py

    # With custom database URL
    DATABASE_URL="postgresql://..." python scripts/populate_human_descriptions.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from ai.wcag_human_descriptions import (
    WCAG_HUMAN_DESCRIPTIONS,
    RULE_HUMAN_DESCRIPTIONS,
    get_human_description_pair,
)


async def populate_human_descriptions(database_url: str) -> dict:
    """
    Update existing WCAG guidelines with human-friendly descriptions.

    Returns:
        Dictionary with update statistics
    """
    conn = await asyncpg.connect(database_url)

    try:
        # Get all existing rules
        rows = await conn.fetch("""
            SELECT id, rule_id, wcag_criterion
            FROM wcag_guidelines
            """)

        updated_count = 0
        skipped_count = 0
        not_found_count = 0
        not_found_rules = []

        for row in rows:
            rule_id = row["rule_id"]
            wcag_criterion = row["wcag_criterion"]

            # Get human-friendly descriptions
            desc = get_human_description_pair(
                wcag_criterion=wcag_criterion, rule_id=rule_id
            )

            if desc:
                await conn.execute(
                    """
                    UPDATE wcag_guidelines
                    SET human_issue = $1,
                        human_fixed = $2,
                        updated_at = NOW()
                    WHERE id = $3
                    """,
                    desc["issue"],
                    desc["fixed"],
                    row["id"],
                )
                updated_count += 1
                print(f"  [OK] {rule_id} (WCAG {wcag_criterion})")
            else:
                not_found_count += 1
                not_found_rules.append(f"{rule_id} (WCAG {wcag_criterion})")
                print(
                    f"  [--] {rule_id} (WCAG {wcag_criterion}) - no description found"
                )

        return {
            "updated": updated_count,
            "skipped": skipped_count,
            "not_found": not_found_count,
            "not_found_rules": not_found_rules,
            "total": len(rows),
        }

    finally:
        await conn.close()


async def main():
    """Main entry point."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        print("Example: DATABASE_URL='postgresql://user:pass@localhost/aelira'")
        sys.exit(1)

    print("=" * 60)
    print("WCAG Human-Friendly Descriptions Populator")
    print("=" * 60)
    print()
    print(f"Database: {database_url[:50]}...")
    print(f"WCAG criteria mappings: {len(WCAG_HUMAN_DESCRIPTIONS)}")
    print(f"Rule ID mappings: {len(RULE_HUMAN_DESCRIPTIONS)}")
    print()
    print("Updating records...")
    print()

    stats = await populate_human_descriptions(database_url)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total records:     {stats['total']}")
    print(f"Updated:           {stats['updated']}")
    print(f"No mapping found:  {stats['not_found']}")
    print()

    if stats["not_found_rules"]:
        print("Rules without human-friendly descriptions:")
        for rule in stats["not_found_rules"][:20]:  # Show first 20
            print(f"  - {rule}")
        if len(stats["not_found_rules"]) > 20:
            print(f"  ... and {len(stats['not_found_rules']) - 20} more")
        print()
        print("Consider adding these to src/ai/wcag_human_descriptions.py")

    print()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
