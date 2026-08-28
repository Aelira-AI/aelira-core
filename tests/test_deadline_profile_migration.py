"""US Title II profile migration and new-row behavior."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_08_28_canonical_deadline_profile.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("deadline_profile", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_existing_us_rows_backfill_large_but_new_incomplete_rows_remain_null(
    monkeypatch,
):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE departments (id VARCHAR(36) PRIMARY KEY, "
                "country_code VARCHAR(2), regulatory_framework VARCHAR(50))"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO departments VALUES "
                "('explicit-us', 'US', 'US_ADA_TITLE_II'), "
                "('legacy-us-country', 'US', NULL), "
                "('legacy-empty', NULL, NULL), "
                "('eu', 'DE', 'EU_EAA'), "
                "('none', 'US', 'NONE')"
            )
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO departments "
                "(id, country_code, regulatory_framework) "
                "VALUES ('new-incomplete-us', 'US', 'US_ADA_TITLE_II')"
            )
        )
        rows = {
            row.id: (row.country_code, row.regulatory_framework, row.entity_class)
            for row in connection.execute(
                sa.text(
                    "SELECT id, country_code, regulatory_framework, "
                    "title_ii_entity_class AS entity_class "
                    "FROM departments ORDER BY id"
                )
            ).all()
        }

        assert migration.down_revision == "20260828_scan_document_identity"
        assert rows == {
            "eu": ("DE", "EU_EAA", None),
            "explicit-us": ("US", "US_ADA_TITLE_II", "large"),
            "legacy-empty": ("US", "US_ADA_TITLE_II", "large"),
            "legacy-us-country": ("US", "US_ADA_TITLE_II", "large"),
            "new-incomplete-us": ("US", "US_ADA_TITLE_II", None),
            "none": ("US", "NONE", None),
        }

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO departments "
                    "(id, title_ii_entity_class) VALUES ('invalid', 'medium')"
                )
            )

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.downgrade()
        assert "title_ii_entity_class" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("departments")
        }
