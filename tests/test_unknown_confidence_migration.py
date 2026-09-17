"""Conservative legacy correction preserves audit history and revokes authority."""

from datetime import datetime, timezone
from types import SimpleNamespace

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from src.db.models import ReviewAuditLog, ScanFix
from src.services.scan_fix_service import review_digest_for


def _schema(connection):
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String, primary_key=True))
    scans = sa.Table(
        "scans",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("current_remediation_artifact_id", sa.String),
    )
    fixes = ScanFix.__table__.to_metadata(metadata)
    fixes.c.confidence.nullable = False
    fixes.c.confidence.server_default = sa.DefaultClause("1.0")
    audit = ReviewAuditLog.__table__.to_metadata(metadata)
    artifacts = sa.Table(
        "remediation_artifacts",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("scan_id", sa.String),
        sa.Column("review_status", sa.String),
        sa.Column("approval_checksum", sa.String),
        sa.Column("approval_review_digest", sa.String),
        sa.Column("approved_by_id", sa.String),
        sa.Column("approved_by_ref", sa.String),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("written_back_at", sa.DateTime(timezone=True)),
    )
    clouds = sa.Table(
        "cloud_files",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("current_remediation_artifact_id", sa.String),
        sa.Column("writeback_status", sa.String),
        sa.Column("has_remediated_version", sa.Boolean),
        sa.Column("remediation_origin", sa.String),
    )
    metadata.create_all(connection)
    return scans, fixes, audit, artifacts, clouds


def _migration(connection):
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260917_unknown_confidence")
    assert revision.down_revision == "20260905_visual_analysis"
    revision.module.op = Operations(MigrationContext.configure(connection))
    return revision.module


def test_correction_is_conservative_idempotent_and_invalidates_only_current_unwritten_approvals():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        scans, fixes, audit, artifacts, clouds = _schema(connection)
        now = datetime.now(timezone.utc)
        connection.execute(
            scans.insert(),
            [
                {"id": "scan", "current_remediation_artifact_id": "current"},
                {"id": "written-scan", "current_remediation_artifact_id": "written"},
            ],
        )
        rows = []
        for identifier, method, score, status, scan_id in (
            ("legacy-auto", "ai_generated", 1.0, "auto_approved", "scan"),
            ("legacy-human", "ai_generated", 1.0, "approved", "scan"),
            ("legacy-rejected", "ai_generated", 1.0, "rejected", "scan"),
            ("legacy-written", "ai_generated", 1.0, "approved", "written-scan"),
            ("explicit-zero", "ai_generated", 0.0, "pending", "scan"),
            ("explicit-score", "ai_generated", 0.8, "approved", "scan"),
            ("explicit-rule", "rule", 1.0, "auto_approved", "scan"),
            ("explicit-vision", "ai_vision", 0.55, "pending", "scan"),
        ):
            rows.append(
                dict(
                    id=identifier,
                    scan_id=scan_id,
                    issue_id=identifier,
                    occurrence_key=identifier.ljust(64, "0"),
                    category="alt_text",
                    severity="high",
                    description="A multiline\ndescription",
                    fixed_content="A chart",
                    fix_method=method,
                    confidence=score,
                    needs_review=False,
                    source_kind=None,
                    review_status=status,
                    reviewed_by="reviewer",
                    reviewed_at=now,
                    review_notes="Previously accepted",
                    review_digest="a" * 64,
                    approved_review_digest="b" * 64,
                )
            )
        connection.execute(fixes.insert(), rows)
        connection.execute(
            audit.insert().values(
                id="old-audit",
                scan_id="scan",
                action="fix_approve",
                details={"notes": "Retained"},
            )
        )
        connection.execute(
            artifacts.insert(),
            [
                dict(
                    id=identifier,
                    scan_id=scan_id,
                    review_status="approved",
                    approval_checksum="c" * 64,
                    approval_review_digest="d" * 64,
                    approved_by_id="reviewer",
                    approved_by_ref="reviewer",
                    approved_at=now,
                    written_back_at=now if identifier == "written" else None,
                )
                for identifier, scan_id in (
                    ("current", "scan"),
                    ("cloud-current", "scan"),
                    ("archived", "scan"),
                    ("written", "written-scan"),
                )
            ],
        )
        connection.execute(
            clouds.insert(),
            [
                dict(
                    id=identifier,
                    current_remediation_artifact_id=identifier,
                    writeback_status="approved",
                    has_remediated_version=True,
                    remediation_origin="automatic",
                )
                for identifier in ("cloud-current", "written")
            ],
        )

        migration = _migration(connection)
        migration.upgrade()
        migration.upgrade()
        result = {row.id: row._mapping for row in connection.execute(sa.select(fixes))}
        for identifier in (
            "legacy-auto",
            "legacy-human",
            "legacy-rejected",
            "legacy-written",
        ):
            row = result[identifier]
            assert row["confidence"] is None
            assert row["needs_review"] is True
            assert row["approved_review_digest"] is None
            assert row["review_digest"] == review_digest_for(SimpleNamespace(**row))
            assert row["review_status"] == (
                "rejected" if identifier == "legacy-rejected" else "pending"
            )
        assert result["legacy-human"]["reviewed_by"] is None
        assert result["legacy-rejected"]["reviewed_by"] == "reviewer"
        for identifier in (
            "explicit-zero",
            "explicit-score",
            "explicit-rule",
            "explicit-vision",
        ):
            original = next(row for row in rows if row["id"] == identifier)
            assert result[identifier]["confidence"] == original["confidence"]
            assert result[identifier]["approved_review_digest"] == "b" * 64
        events = connection.execute(sa.select(audit)).mappings().all()
        assert len(events) == 7  # Original + four corrections + two invalidations.
        assert next(row for row in events if row["id"] == "old-audit")["details"] == {
            "notes": "Retained"
        }
        correction = next(row for row in events if row["fix_id"] == "legacy-human")
        assert correction["details"]["previous"]["confidence"] == 1.0
        assert correction["details"]["previous"]["approved_review_digest"] == "b" * 64
        assert (
            correction["details"]["previous"]["review_notes"] == "Previously accepted"
        )
        artifact_rows = {
            row.id: row._mapping for row in connection.execute(sa.select(artifacts))
        }
        for identifier in ("current", "cloud-current"):
            assert artifact_rows[identifier]["review_status"] == "pending"
            assert artifact_rows[identifier]["approval_review_digest"] is None
        for identifier in ("archived", "written"):
            assert artifact_rows[identifier]["review_status"] == "approved"
            assert artifact_rows[identifier]["approval_review_digest"] == "d" * 64
        cloud_rows = {
            row.id: row._mapping for row in connection.execute(sa.select(clouds))
        }
        assert cloud_rows["cloud-current"]["writeback_status"] == "pending_review"
        assert cloud_rows["cloud-current"]["has_remediated_version"] is False
        assert cloud_rows["written"]["writeback_status"] == "approved"
        confidence = next(
            column
            for column in sa.inspect(connection).get_columns("scan_fixes")
            if column["name"] == "confidence"
        )
        assert confidence["nullable"] is True
        assert confidence["default"] is None
        with pytest.raises(RuntimeError, match="inventing scores"):
            migration.downgrade()
    engine.dispose()


def test_empty_schema_can_downgrade_without_manufacturing_scores():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _schema(connection)
        migration = _migration(connection)
        migration.upgrade()
        migration.downgrade()
        confidence = next(
            column
            for column in sa.inspect(connection).get_columns("scan_fixes")
            if column["name"] == "confidence"
        )
        assert confidence["nullable"] is False
    engine.dispose()
