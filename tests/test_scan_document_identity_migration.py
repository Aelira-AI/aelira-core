"""Stable scan document identity migration contract."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_08_28_scan_document_identity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("scan_document_identity", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_links_only_scans_with_unambiguous_provider_evidence(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE scans (id VARCHAR(36) PRIMARY KEY, "
                "department_id VARCHAR(36) NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE cloud_files (id VARCHAR(36) PRIMARY KEY, "
                "department_id VARCHAR(36) NOT NULL, last_scan_id VARCHAR(36))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE cloud_job_queue (id VARCHAR(36) PRIMARY KEY, "
                "department_id VARCHAR(36) NOT NULL, job_type VARCHAR(50) NOT NULL, "
                "cloud_file_id VARCHAR(36), result_data JSON)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE remediation_artifacts (id VARCHAR(36) PRIMARY KEY, "
                "department_id VARCHAR(36) NOT NULL, scan_id VARCHAR(36) NOT NULL, "
                "cloud_file_id VARCHAR(36))"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO scans (id, department_id) VALUES "
                "('provider-current', 'dept-a'), "
                "('provider-failed-job', 'dept-a'), "
                "('provider-failed-no-result', 'dept-a'), "
                "('provider-artifact-history', 'dept-a'), "
                "('standalone-failed', 'dept-a'), "
                "('ambiguous-provider', 'dept-a'), "
                "('foreign-evidence', 'dept-b')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO cloud_files (id, department_id, last_scan_id) "
                "VALUES ('cloud-current', 'dept-a', 'provider-current'), "
                "('cloud-job', 'dept-a', NULL), "
                "('cloud-artifact', 'dept-a', NULL), "
                "('cloud-ambiguous-a', 'dept-a', NULL), "
                "('cloud-ambiguous-b', 'dept-a', NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO cloud_job_queue "
                "(id, department_id, job_type, cloud_file_id, result_data) VALUES "
                "('job-failed', 'dept-a', 'scan', 'cloud-job', "
                '\'{"scan_id":"provider-failed-job"}\'), '
                "('job-failed-no-result', 'dept-a', 'scan', 'cloud-job', NULL), "
                "('job-unrelated', 'dept-a', 'upload', 'cloud-job', "
                '\'{"scan_id":"standalone-failed"}\'), '
                "('job-ambiguous-a', 'dept-a', 'scan', 'cloud-ambiguous-a', "
                '\'{"scan_id":"ambiguous-provider"}\'), '
                "('job-ambiguous-b', 'dept-a', 'scan', 'cloud-ambiguous-b', "
                '\'{"scan_id":"ambiguous-provider"}\'), '
                "('job-foreign', 'dept-b', 'scan', 'cloud-job', "
                '\'{"scan_id":"foreign-evidence"}\')'
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO remediation_artifacts "
                "(id, department_id, scan_id, cloud_file_id) VALUES "
                "('artifact-history', 'dept-a', 'provider-artifact-history', "
                "'cloud-artifact')"
            )
        )

        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()

        rows = {
            row.id: (row.document_id, row.document_source)
            for row in connection.execute(
                sa.text(
                    "SELECT id, document_id, document_source FROM scans ORDER BY id"
                )
            ).all()
        }
        indexes = {
            index["name"] for index in sa.inspect(connection).get_indexes("scans")
        }

        assert migration.down_revision == "20260828_issue_tenant_repair"
        assert rows == {
            "ambiguous-provider": (None, None),
            "foreign-evidence": (None, None),
            "provider-artifact-history": ("cloud-artifact", "cloud_file"),
            "provider-current": ("cloud-current", "cloud_file"),
            "provider-failed-job": ("cloud-job", "cloud_file"),
            "provider-failed-no-result": (None, None),
            "standalone-failed": (None, None),
        }
        assert "ix_scans_document_id" in indexes

        migration.downgrade()
        remaining_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("scans")
        }
        assert "document_id" not in remaining_columns
        assert "document_source" not in remaining_columns


def test_snapshot_score_nullability_round_trip_is_dialect_safe(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE compliance_snapshots ("
                "id VARCHAR(36) PRIMARY KEY, "
                "avg_compliance_score FLOAT NOT NULL DEFAULT 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO compliance_snapshots (id, avg_compliance_score) "
                "VALUES ('measured', 72)"
            )
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        migration._set_snapshot_score_nullable(True)

        upgraded_column = next(
            column
            for column in sa.inspect(connection).get_columns("compliance_snapshots")
            if column["name"] == "avg_compliance_score"
        )
        assert upgraded_column["nullable"] is True
        assert upgraded_column["default"] == "0"
        connection.execute(
            sa.text(
                "INSERT INTO compliance_snapshots (id, avg_compliance_score) "
                "VALUES ('unverified', NULL)"
            )
        )

        migration._set_snapshot_score_nullable(False)
        migration._set_snapshot_score_nullable(False)  # Idempotent replay.

        downgraded_column = next(
            column
            for column in sa.inspect(connection).get_columns("compliance_snapshots")
            if column["name"] == "avg_compliance_score"
        )
        scores = dict(
            connection.execute(
                sa.text(
                    "SELECT id, avg_compliance_score "
                    "FROM compliance_snapshots ORDER BY id"
                )
            ).all()
        )
        assert downgraded_column["nullable"] is False
        assert downgraded_column["default"] == "0"
        assert scores == {"measured": 72.0, "unverified": 0.0}
