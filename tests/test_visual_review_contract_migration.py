"""Migration coverage for typed visual review contracts and digests."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    select,
)
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    ROOT / "alembic" / "versions" / "2026_08_28_typed_visual_review_contracts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "typed_visual_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_sqlite_upgrade_and_downgrade_preserve_legacy_rows(monkeypatch):
    migration = _load_migration()
    engine = create_engine("sqlite://")
    metadata = MetaData()
    scan_fixes = Table(
        "scan_fixes",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("source_kind", String(32)),
        Column("source_locator", JSON),
        Column("verification_evidence", JSON),
    )
    artifacts = Table(
        "remediation_artifacts",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("approval_checksum", String(64)),
    )
    metadata.create_all(engine)
    legacy_locator = {"source_kind": "page_raster_region", "page_number": 2}
    with engine.begin() as connection:
        connection.execute(
            scan_fixes.insert().values(
                id="fix-legacy",
                source_kind="image_equation",
                source_locator=legacy_locator,
                verification_evidence={"passed": True},
            )
        )
        connection.execute(
            artifacts.insert().values(id="artifact-legacy", approval_checksum="a" * 64)
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        migration.upgrade()
        migration.upgrade()
        assert migration.down_revision == "20260828_deadline_profile"
        assert {
            "visual_semantic_contract",
            "review_digest",
            "approved_review_digest",
        }.issubset(
            {column["name"] for column in inspect(connection).get_columns("scan_fixes")}
        )
        assert "approval_review_digest" in {
            column["name"]
            for column in inspect(connection).get_columns("remediation_artifacts")
        }
        assert {
            "ck_scan_fixes_visual_semantic_contract",
            "ck_scan_fixes_review_digest",
            "ck_scan_fixes_approved_review_digest",
        }.issubset(
            {
                constraint["name"]
                for constraint in inspect(connection).get_check_constraints(
                    "scan_fixes"
                )
            }
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO scan_fixes (id, review_digest) VALUES (?, ?)",
                ("fix-invalid", "A" * 64),
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO scan_fixes (id, source_kind, visual_semantic_contract) "
                "VALUES (?, ?, ?)",
                ("fix-untyped-contract", None, "{}"),
            )
        row = connection.execute(select(scan_fixes)).mappings().one()
        assert row["source_locator"] == legacy_locator
        assert row["verification_evidence"] == {"passed": True}
        migrated = (
            connection.exec_driver_sql(
                "SELECT visual_semantic_contract, review_digest, approved_review_digest "
                "FROM scan_fixes WHERE id = 'fix-legacy'"
            )
            .mappings()
            .one()
        )
        assert migrated == {
            "visual_semantic_contract": None,
            "review_digest": None,
            "approved_review_digest": None,
        }

        migration.downgrade()
        assert {
            column["name"] for column in inspect(connection).get_columns("scan_fixes")
        } == {
            "id",
            "source_kind",
            "source_locator",
            "verification_evidence",
        }
        assert {
            column["name"]
            for column in inspect(connection).get_columns("remediation_artifacts")
        } == {"id", "approval_checksum"}
        row = connection.exec_driver_sql(
            "SELECT source_locator, verification_evidence FROM scan_fixes "
            "WHERE id = 'fix-legacy'"
        ).one()
        assert row[0] is not None
        assert row[1] is not None
