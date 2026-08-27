"""Task17B Alembic chain and disposable-PostgreSQL down/up coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from conftest import require_disposable_postgres_url

ROOT = Path(__file__).parents[1]
HEAD = "20260828_issue_tenant_repair"
REGION_HEAD = "20260828_region_provenance"
PRIOR_HEAD = "20260827_admin_handoff"
CANVAS_HEAD = "20260825_canvas_queue"
EQUATION_HEAD = "20260824_task8_review"
V095_HEAD = "20260822_v095_job_quarantine"
TASK21_HEAD = "20260822_task21_provenance"
TASK18_HEAD = "20260822_task18_identity"
TASK17_HEAD = "20260822_upload_effect_fence"
RECONCILE = "20260821_task17b_reconcile"
ORPHAN = "20260821_task17b_orphan"
TASK17A = "20260821_task17a_jobs"


def test_task17b_migrations_are_one_linear_reversible_head():
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [HEAD]
    assert scripts.get_revision(HEAD).down_revision == REGION_HEAD
    assert scripts.get_revision(REGION_HEAD).down_revision == PRIOR_HEAD
    assert scripts.get_revision(PRIOR_HEAD).down_revision == CANVAS_HEAD
    assert scripts.get_revision(CANVAS_HEAD).down_revision == EQUATION_HEAD
    assert scripts.get_revision(EQUATION_HEAD).down_revision == V095_HEAD
    assert scripts.get_revision(V095_HEAD).down_revision == TASK21_HEAD
    assert scripts.get_revision(TASK21_HEAD).down_revision == TASK18_HEAD
    assert scripts.get_revision(TASK18_HEAD).down_revision == TASK17_HEAD
    assert scripts.get_revision(TASK17_HEAD).down_revision == RECONCILE
    assert scripts.get_revision(RECONCILE).down_revision == ORPHAN
    assert scripts.get_revision(ORPHAN).down_revision == TASK17A


@pytest.mark.integration
def test_task17b_migrations_downgrade_then_upgrade_on_disposable_postgres(monkeypatch):
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not database_url or os.getenv("ALLOW_DESTRUCTIVE_MIGRATION_TESTS") != "1":
        pytest.skip(
            "requires TEST_MIGRATION_DATABASE_URL and "
            "ALLOW_DESTRUCTIVE_MIGRATION_TESTS=1"
        )
    require_disposable_postgres_url(database_url, destructive=True)
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
            connection.exec_driver_sql("GRANT ALL ON SCHEMA public TO public")
        command.upgrade(config, "head")
        schema = inspect(engine)
        assert "artifact_orphan_quarantine" in schema.get_table_names()
        assert "maintenance_cursors" in schema.get_table_names()
        orphan_columns = {
            column["name"]
            for column in schema.get_columns("artifact_orphan_quarantine")
        }
        assert {
            "intent_token",
            "source_mtime_ns",
            "source_device",
            "source_inode",
            "recovery_error",
            "purge_claimed_at",
            "purge_token",
        } <= orphan_columns
        assert {
            "last_renewed_at",
            "renewal_status",
            "renewal_result",
            "provider_resource_id",
            "provider_channel_resource_id",
            "pending_renewal_channel_id",
            "pending_renewal_started_at",
        } <= {
            column["name"]
            for column in schema.get_columns("cloud_webhook_subscriptions")
        }
        columns = {
            column["name"] for column in schema.get_columns("content_writeback_log")
        }
        assert "reconciliation_lease_token" in columns

        command.downgrade(config, TASK17A)
        schema = inspect(engine)
        assert "artifact_orphan_quarantine" not in schema.get_table_names()
        assert "maintenance_cursors" not in schema.get_table_names()
        assert not {
            "last_renewed_at",
            "renewal_status",
            "renewal_result",
            "provider_resource_id",
            "provider_channel_resource_id",
            "pending_renewal_channel_id",
            "pending_renewal_started_at",
        } & {
            column["name"]
            for column in schema.get_columns("cloud_webhook_subscriptions")
        }
        columns = {
            column["name"] for column in schema.get_columns("content_writeback_log")
        }
        assert "reconciliation_lease_token" not in columns

        command.upgrade(config, "head")
        schema = inspect(engine)
        assert "artifact_orphan_quarantine" in schema.get_table_names()
        assert "maintenance_cursors" in schema.get_table_names()
        assert "reconciliation_lease_token" in {
            column["name"] for column in schema.get_columns("content_writeback_log")
        }
    finally:
        engine.dispose()


def test_orphan_model_and_migration_define_purging_claim_constraints():
    from src.db.models import ArtifactOrphanQuarantine

    migration = (
        ROOT / "alembic/versions/2026_08_21_task17b_orphan_quarantine.py"
    ).read_text()
    constraints = "\n".join(
        str(constraint.sqltext)
        for constraint in ArtifactOrphanQuarantine.__table__.constraints
        if hasattr(constraint, "sqltext")
    )

    for required in ("purging", "purge_claimed_at", "purge_token"):
        assert required in migration
        assert (
            required in constraints
            or required in ArtifactOrphanQuarantine.__table__.columns
        )
