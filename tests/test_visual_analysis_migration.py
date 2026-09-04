"""Migration coverage for the durable visual-analysis lifecycle."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_visual_analysis_migration_is_idempotent_constrained_and_reversible():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260905_visual_analysis")
    assert revision is not None
    assert revision.down_revision == "20260905_review_deferrals"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE departments (id TEXT PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE scans (id TEXT PRIMARY KEY, department_id TEXT NOT NULL)"
            )
        )
        connection.execute(text("CREATE TABLE scan_fixes (id TEXT PRIMARY KEY)"))
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()

        inspector = inspect(connection)
        assert {"visual_analyses", "visual_analysis_attempts"}.issubset(
            inspector.get_table_names()
        )
        assert {
            "idx_visual_analyses_scan_status",
            "idx_visual_analyses_recovery",
        } == {index["name"] for index in inspector.get_indexes("visual_analyses")}
        assert "uq_visual_analyses_department_request" in {
            item["name"] for item in inspector.get_unique_constraints("visual_analyses")
        }
        assert "uq_visual_analysis_attempt_number" in {
            item["name"]
            for item in inspector.get_unique_constraints("visual_analysis_attempts")
        }

        connection.execute(text("INSERT INTO departments (id) VALUES ('dept-1')"))
        connection.execute(
            text("INSERT INTO scans (id, department_id) VALUES ('scan-1', 'dept-1')")
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO visual_analyses "
                    "(id, department_id, scan_id, source_kind, "
                    "parent_artifact_sha256, source_locator, purpose, "
                    "request_digest, status, max_attempts, attempt_count) VALUES "
                    "('bad', 'dept-1', 'scan-1', 'photo', :digest, '{}', "
                    "'alt_text', :digest, 'queued', 3, 0)"
                ),
                {"digest": "a" * 64},
            )

        module.downgrade()
        assert "visual_analyses" not in inspect(connection).get_table_names()
        assert "visual_analysis_attempts" not in inspect(connection).get_table_names()

    engine.dispose()
