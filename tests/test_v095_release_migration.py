"""v0.9.5 release-boundary migration contracts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from conftest import require_disposable_postgres_url

ROOT = Path(__file__).parents[1]
PREVIOUS_HEAD = "20260822_task21_provenance"
V095_HEAD = "20260822_v095_job_quarantine"
HEAD = "20260828_region_provenance"
PRIOR_HEAD = "20260827_admin_handoff"
CANVAS_HEAD = "20260825_canvas_queue"
EQUATION_HEAD = "20260824_task8_review"
REASON = "pre_v0_9_5_job_quarantined"
MIGRATION = ROOT / "alembic/versions/2026_08_22_v095_job_quarantine.py"


def test_v095_quarantine_is_single_head_after_v094_invariants():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))

    assert scripts.get_heads() == [HEAD]
    assert scripts.get_revision(HEAD).down_revision == PRIOR_HEAD
    assert scripts.get_revision(PRIOR_HEAD).down_revision == CANVAS_HEAD
    assert scripts.get_revision(CANVAS_HEAD).down_revision == EQUATION_HEAD
    assert scripts.get_revision(EQUATION_HEAD).down_revision == V095_HEAD
    assert scripts.get_revision(V095_HEAD).down_revision == PREVIOUS_HEAD


def test_v095_quarantine_sql_is_terminal_complete_and_downgrade_safe(monkeypatch):
    spec = importlib.util.spec_from_file_location("v095_job_quarantine", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    sql = "\n".join(str(statement) for statement in statements)
    normalized = " ".join(sql.lower().split())
    assert "where status in ('pending', 'processing')" in normalized
    assert "status = 'failed'" in normalized
    assert f"last_error_code = '{REASON}'" in normalized
    assert "last_error_retryable = false" in normalized
    assert "completed_at = now()" in normalized
    for claim_field in (
        "claim_token",
        "worker_id",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
    ):
        assert f"{claim_field} = null" in normalized
    assert "payload" not in normalized
    assert "result_data" not in normalized
    assert "external_effect_state" not in normalized

    statements.clear()
    migration.downgrade()
    assert statements == []


def _destructive_database() -> str:
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL", "")
    if not database_url or os.getenv("ALLOW_DESTRUCTIVE_MIGRATION_TESTS") != "1":
        pytest.skip(
            "requires TEST_MIGRATION_DATABASE_URL and ALLOW_DESTRUCTIVE_MIGRATION_TESTS=1"
        )
    require_disposable_postgres_url(database_url, destructive=True)
    return database_url


@pytest.mark.integration
def test_v095_fresh_upgrade_downgrade_reupgrade_preserves_schema_and_data(monkeypatch):
    database_url = _destructive_database()
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            connection.execute(text("GRANT ALL ON SCHEMA public TO public"))

        # Fresh install reaches the sole release head.
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == HEAD
            )
        assert "cloud_job_queue" in inspect(engine).get_table_names()

        # Return to the exact prior release head and seed representative old work.
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO departments (id, name, institution, contact_email) "
                    "VALUES ('v095-dept', 'Release migration', 'Aelira', 'ops@example.test')"
                )
            )
            for job_id, job_type, status in (
                ("v095-scan", "scan", "pending"),
                ("v095-remediate", "remediate", "processing"),
                ("v095-upload", "upload", "pending"),
                ("v095-sync", "sync", "pending"),
                ("v095-complete", "scan", "completed"),
            ):
                claim = status == "processing"
                connection.execute(
                    text(
                        "INSERT INTO cloud_job_queue "
                        "(id, department_id, job_type, payload, status, progress, "
                        "attempt_count, retry_count, max_retries, scheduled_for, created_at, "
                        "updated_at, completed_at, claim_token, worker_id, claimed_at, "
                        "heartbeat_at, lease_expires_at) VALUES "
                        "(:id, 'v095-dept', :job_type, CAST(:payload AS jsonb), :status, 0, "
                        "0, 0, 3, now(), now(), now(), :completed_at, :claim_token, "
                        ":worker_id, :claimed_at, :heartbeat_at, :lease_expires_at)"
                    ),
                    {
                        "id": job_id,
                        "job_type": job_type,
                        "payload": '{"release_fixture":true}',
                        "status": status,
                        "completed_at": (
                            None if status != "completed" else "2026-08-22 00:00:00+00"
                        ),
                        "claim_token": (
                            "00000000-0000-0000-0000-000000000001" if claim else None
                        ),
                        "worker_id": "prior-worker" if claim else None,
                        "claimed_at": "2026-08-22 00:00:00+00" if claim else None,
                        "heartbeat_at": "2026-08-22 00:00:00+00" if claim else None,
                        "lease_expires_at": "2026-08-22 00:05:00+00" if claim else None,
                    },
                )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT id, job_type, status, last_error_code, last_error_retryable, "
                        "payload, claim_token, completed_at FROM cloud_job_queue ORDER BY id"
                    )
                )
                .mappings()
                .all()
            )
            quarantined = [row for row in rows if row["id"] != "v095-complete"]
            assert {row["job_type"] for row in quarantined} == {
                "scan",
                "remediate",
                "upload",
                "sync",
            }
            assert all(row["status"] == "failed" for row in quarantined)
            assert all(row["last_error_code"] == REASON for row in quarantined)
            assert all(row["last_error_retryable"] is False for row in quarantined)
            assert all(
                row["payload"] == {"release_fixture": True} for row in quarantined
            )
            assert all(row["claim_token"] is None for row in quarantined)
            assert all(row["completed_at"] is not None for row in quarantined)
            complete = next(row for row in rows if row["id"] == "v095-complete")
            assert complete["status"] == "completed"
            assert complete["last_error_code"] is None

        # Downgrade only moves the marker; it never revives quarantined intent.
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM cloud_job_queue WHERE status = 'failed' AND last_error_code = :reason"
                    ),
                    {"reason": REASON},
                ).scalar_one()
                == 4
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM cloud_job_queue WHERE status = 'failed' AND last_error_code = :reason"
                    ),
                    {"reason": REASON},
                ).scalar_one()
                == 4
            )
    finally:
        engine.dispose()
