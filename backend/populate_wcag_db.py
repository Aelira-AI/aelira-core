#!/usr/bin/env python3
"""
Populate WCAG Knowledge Base with seed data.

This script populates the wcag_guidelines table with canonical WCAG rules
from the seed data files.
"""

import asyncio
import asyncpg
import os
import sys
import logging
import json
from src.ai.wcag_seed_data_complete import ALL_WCAG_GUIDELINES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def populate_database():
    """Populate wcag_guidelines table with seed data."""

    # Database connection - must be set via environment variable
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        logger.error("DATABASE_URL environment variable not set!")
        logger.error("Set it via: export DATABASE_URL='postgresql://user:password@localhost:5432/dbname'")
        logger.error("Or create a .env file in the backend/ directory (see .env.example)")
        sys.exit(1)

    # Validate no unsafe defaults
    if any(pattern in database_url for pattern in ["dev_password_change_in_prod", "aelira_password", "change_me"]):
        logger.error("Unsafe DATABASE_URL detected!")
        logger.error("Do not use default/placeholder passwords in DATABASE_URL")
        sys.exit(1)

    logger.info(f"Connecting to database...")
    conn = await asyncpg.connect(database_url)

    try:
        # Check if table is empty
        count = await conn.fetchval("SELECT COUNT(*) FROM wcag_guidelines")

        if count > 0:
            logger.warning(f"Database already has {count} guidelines.")
            response = input("Do you want to DELETE ALL and re-seed? (yes/no): ")
            if response.lower() != 'yes':
                logger.info("Aborted. Exiting.")
                return

            # Delete all existing records
            await conn.execute("DELETE FROM wcag_guidelines")
            logger.info("Deleted all existing guidelines.")

        # Insert all guidelines
        logger.info(f"Inserting {len(ALL_WCAG_GUIDELINES)} WCAG guidelines...")

        inserted = 0
        for guideline in ALL_WCAG_GUIDELINES:
            try:
                await conn.execute("""
                    INSERT INTO wcag_guidelines (
                        rule_id, wcag_criterion, wcag_level, wcag_version,
                        title, description, principle, guideline,
                        severity_criteria, business_impact_template, technical_impact,
                        fix_examples, best_practices, tags, act_rule_ids, related_rules
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12::jsonb, $13, $14, $15, $16
                    )
                """,
                    guideline["rule_id"],
                    guideline["wcag_criterion"],
                    guideline["wcag_level"],
                    guideline.get("wcag_version", "2.2"),
                    guideline["title"],
                    guideline["description"],
                    guideline["principle"],
                    guideline["guideline"],
                    json.dumps(guideline["severity_criteria"]),  # JSONB - serialize to JSON string
                    guideline.get("business_impact_template", ""),
                    guideline.get("technical_impact", ""),
                    json.dumps(guideline.get("fix_examples", [])),  # JSONB - serialize to JSON string
                    guideline.get("best_practices", []),  # TEXT[]
                    guideline.get("tags", []),  # TEXT[]
                    guideline.get("act_rule_ids", []),  # TEXT[]
                    guideline.get("related_rules", [])  # TEXT[]
                )
                inserted += 1

                if inserted % 10 == 0:
                    logger.info(f"  Inserted {inserted}/{len(ALL_WCAG_GUIDELINES)} guidelines...")

            except Exception as e:
                logger.error(f"Failed to insert {guideline['rule_id']}: {e}")
                continue

        logger.info(f"✅ Successfully inserted {inserted}/{len(ALL_WCAG_GUIDELINES)} guidelines!")

        # Verify
        final_count = await conn.fetchval("SELECT COUNT(*) FROM wcag_guidelines")
        logger.info(f"Database now has {final_count} guidelines.")

        # Show sample
        sample = await conn.fetch("SELECT rule_id, title, wcag_criterion FROM wcag_guidelines LIMIT 5")
        logger.info("\nSample guidelines:")
        for row in sample:
            logger.info(f"  - {row['rule_id']}: {row['title']} (WCAG {row['wcag_criterion']})")

    finally:
        await conn.close()
        logger.info("Database connection closed.")


if __name__ == "__main__":
    asyncio.run(populate_database())
