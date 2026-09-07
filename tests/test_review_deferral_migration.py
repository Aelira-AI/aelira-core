"""Migration coverage for canonical review deferral storage."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_review_deferral_migration_is_idempotent_and_reversible():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260905_review_deferrals")
    assert revision is not None
    assert revision.down_revision == "20260831_institution_scope"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scan_fixes ("
                "id TEXT PRIMARY KEY, review_status TEXT NOT NULL DEFAULT 'pending')"
            )
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()

        columns = {
            column["name"] for column in inspect(connection).get_columns("scan_fixes")
        }
        assert {
            "deferral_status",
            "deferral_owner",
            "deferral_reason",
            "deferral_expires_at",
            "deferral_created_at",
            "deferral_updated_at",
            "deferral_closed_at",
        }.issubset(columns)
        assert "idx_scan_fixes_deferral" in {
            index["name"] for index in inspect(connection).get_indexes("scan_fixes")
        }
        assert "ck_scan_fixes_deferral_state" in {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("scan_fixes")
        }
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO scan_fixes "
                    "(id, review_status, deferral_status, deferral_owner, "
                    "deferral_reason, deferral_expires_at, deferral_created_at, "
                    "deferral_updated_at) VALUES "
                    "('invalid', 'pending', 'active', '', 'reason', "
                    "'2099-01-01', '2026-01-01', '2026-01-01')"
                )
            )

        module.downgrade()
        assert {
            column["name"] for column in inspect(connection).get_columns("scan_fixes")
        } == {"id", "review_status"}

    engine.dispose()
