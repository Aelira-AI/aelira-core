"""Alembic revision identifiers must fit the existing version table."""

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

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


def test_image_equation_review_upgrade_preserves_sqlite_audit_links():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = scripts.get_revision("20260824_task8_review")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(text("CREATE TABLE scan_fixes (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE review_audit_log ("
                "id VARCHAR(36) PRIMARY KEY, "
                "fix_id VARCHAR(36) REFERENCES scan_fixes(id) ON DELETE SET NULL)"
            )
        )
        connection.execute(text("INSERT INTO scan_fixes (id) VALUES ('fix-1')"))
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
