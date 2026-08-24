"""Alembic revision identifiers must fit the existing version table."""

from pathlib import Path
import os

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url

from src.db.models import ScanFix

ROOT = Path(__file__).parents[1]


def test_every_revision_and_down_revision_fit_alembic_version_column():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    oversized = {}
    for revision in scripts.walk_revisions(base="base", head="heads"):
        if len(revision.revision) > 32:
            oversized[f"revision:{revision.revision}"] = len(revision.revision)

        down_revisions = revision.down_revision
        if isinstance(down_revisions, str):
            down_revisions = (down_revisions,)
        for down_revision in down_revisions or ():
            if len(down_revision) > 32:
                oversized[f"down_revision:{down_revision}"] = len(down_revision)

    assert oversized == {}


def test_scan_fix_review_evidence_columns_are_nullable_and_bounded():
    assert ScanFix.__table__.c.source_kind.nullable is True
    assert ScanFix.__table__.c.source_kind.type.length == 32
    assert ScanFix.__table__.c.provider_used.nullable is True
    assert ScanFix.__table__.c.provider_used.type.length == 64
    assert ScanFix.__table__.c.verification_evidence.nullable is True
    assert ScanFix.__table__.c.occurrence_key.nullable is False
    assert ScanFix.__table__.c.occurrence_key.type.length == 64
    assert "uq_scan_fixes_scan_occurrence" in {
        constraint.name for constraint in ScanFix.__table__.constraints
    }
    assert "ck_scan_fixes_source_kind" in {
        constraint.name for constraint in ScanFix.__table__.constraints
    }


def test_image_equation_review_evidence_upgrade_is_idempotent_on_existing_table():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    assert revision is not None

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scan_fixes ("
                "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL)"
            )
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()
        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("scan_fixes")
        }

    assert columns["source_kind"]["nullable"] is True
    assert columns["provider_used"]["nullable"] is True
    assert columns["verification_evidence"]["nullable"] is True
    assert str(columns["verification_evidence"]["type"]).upper() == "JSON"


def test_image_equation_review_upgrade_preserves_sqlite_audit_links():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(
            text(
                "CREATE TABLE scan_fixes ("
                "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE review_audit_log ("
                "id VARCHAR(36) PRIMARY KEY, "
                "fix_id VARCHAR(36) REFERENCES scan_fixes(id) ON DELETE SET NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scan_fixes (id, scan_id) VALUES ('fix-1', 'scan-1')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_audit_log (id, fix_id) VALUES ('audit-1', 'fix-1')"
            )
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        fix_id = connection.execute(
            text("SELECT fix_id FROM review_audit_log WHERE id = 'audit-1'")
        ).scalar_one()

    assert fix_id == "fix-1"


def test_review_upgrade_reconciles_legacy_duplicates_and_enforces_occurrence_unique():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scan_fixes ("
                "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL, "
                "issue_id TEXT NOT NULL, location TEXT, page_number INTEGER, "
                "review_status VARCHAR(20) NOT NULL, needs_review BOOLEAN NOT NULL, "
                "reviewed_by VARCHAR(36), reviewed_at DATETIME, review_notes TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE review_audit_log ("
                "id VARCHAR(36) PRIMARY KEY, fix_id VARCHAR(36) "
                "REFERENCES scan_fixes(id) ON DELETE SET NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scan_fixes VALUES "
                "('fix-b', 'scan-1', 'rule-1', 'page 1', 1, 'approved', 1, "
                "'user-1', CURRENT_TIMESTAMP, 'old'), "
                "('fix-a', 'scan-1', 'rule-1', 'page 1', 1, 'auto_approved', 0, "
                "NULL, NULL, NULL), "
                "('fix-c', 'scan-1', 'rule-1', 'page 2', 2, 'approved', 1, "
                "'user-1', CURRENT_TIMESTAMP, 'keep')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_audit_log VALUES "
                "('audit-a', 'fix-a'), ('audit-b', 'fix-b')"
            )
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()

        rows = connection.execute(
            text(
                "SELECT id, occurrence_key, review_status, reviewed_by "
                "FROM scan_fixes ORDER BY id"
            )
        ).all()
        links = connection.execute(
            text("SELECT fix_id FROM review_audit_log ORDER BY id")
        ).scalars().all()
        assert rows[0][0] == "fix-a"
        assert rows[0][2:] == ("pending", None)
        assert rows[1][0] == "fix-c"
        assert all(len(row[1]) == 64 for row in rows)
        assert links == ["fix-a", "fix-a"]

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO scan_fixes "
                    "(id, scan_id, issue_id, occurrence_key, review_status, needs_review) "
                    "VALUES ('fix-d', 'scan-1', 'rule-x', :key, 'pending', 1)"
                ),
                {"key": rows[0][1]},
            )


def test_review_migration_downgrade_and_reupgrade_restore_occurrence_constraint():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scan_fixes ("
                "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL, "
                "issue_id TEXT NOT NULL, location TEXT, page_number INTEGER, "
                "review_status VARCHAR(20) NOT NULL, needs_review BOOLEAN NOT NULL, "
                "reviewed_by VARCHAR(36), reviewed_at DATETIME, review_notes TEXT)"
            )
        )
        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.downgrade()
        assert "occurrence_key" not in {
            column["name"] for column in inspect(connection).get_columns("scan_fixes")
        }
        module.upgrade()
        constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_unique_constraints("scan_fixes")
        }
        assert "uq_scan_fixes_scan_occurrence" in constraints


def _task8_postgres_url() -> str:
    database_url = os.getenv("TEST_TASK8_POSTGRES_URL", "")
    if not database_url:
        pytest.skip("set TEST_TASK8_POSTGRES_URL for the real PostgreSQL contract")
    assert make_url(database_url).get_backend_name() == "postgresql"
    return database_url


@pytest.mark.integration
def test_review_migration_uses_jsonb_and_survives_postgres_replay_cycle():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine(_task8_postgres_url())
    schema = "task8_review_migration"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(
                text(
                    "CREATE TABLE scan_fixes ("
                    "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL, "
                    "issue_id TEXT NOT NULL, location TEXT, page_number INTEGER, "
                    "review_status VARCHAR(20) NOT NULL, needs_review BOOLEAN NOT NULL, "
                    "reviewed_by VARCHAR(36), reviewed_at TIMESTAMPTZ, review_notes TEXT)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO scan_fixes "
                    "(id, scan_id, issue_id, location, page_number, review_status, needs_review) "
                    "VALUES ('fix-1', 'scan-1', 'rule-1', 'page 1', 1, 'pending', true)"
                )
            )
            module = revision.module
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            module.upgrade()

            data_type = connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'scan_fixes' "
                    "AND column_name = 'verification_evidence'"
                ),
                {"schema": schema},
            ).scalar_one()
            assert data_type == "jsonb"
            connection.execute(
                text(
                    "UPDATE scan_fixes SET verification_evidence = "
                    "CAST(:evidence AS jsonb), source_kind = 'image_equation'"
                ),
                {"evidence": '{"passed":true}'},
            )
            assert connection.execute(
                text("SELECT verification_evidence->>'passed' FROM scan_fixes")
            ).scalar_one() == "true"

            module.downgrade()
            assert {
                column["name"]
                for column in inspect(connection).get_columns(
                    "scan_fixes", schema=schema
                )
            }.isdisjoint(
                {
                    "occurrence_key",
                    "verification_evidence",
                    "source_kind",
                    "provider_used",
                }
            )
            module.upgrade()
            assert connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'scan_fixes' "
                    "AND column_name = 'verification_evidence'"
                ),
                {"schema": schema},
            ).scalar_one() == "jsonb"
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.integration
def test_review_migration_postgres_constraints_reject_invalid_and_duplicate_rows():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine(_task8_postgres_url())
    schema = "task8_review_constraints"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(
                text(
                    "CREATE TABLE scan_fixes ("
                    "id VARCHAR(36) PRIMARY KEY, scan_id VARCHAR(36) NOT NULL, "
                    "issue_id TEXT NOT NULL, location TEXT, page_number INTEGER)"
                )
            )
            module = revision.module
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            key = "a" * 64
            connection.execute(
                text(
                    "INSERT INTO scan_fixes "
                    "(id, scan_id, issue_id, occurrence_key, source_kind) "
                    "VALUES ('fix-1', 'scan-1', 'rule-1', :key, 'image_equation')"
                ),
                {"key": key},
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO scan_fixes "
                        "(id, scan_id, issue_id, occurrence_key) "
                        "VALUES ('fix-2', 'scan-1', 'rule-2', :key)"
                    ),
                    {"key": key},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO scan_fixes "
                        "(id, scan_id, issue_id, occurrence_key, source_kind) "
                        "VALUES ('fix-3', 'scan-1', 'rule-3', :key, 'forged')"
                    ),
                    {"key": "b" * 64},
                )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
