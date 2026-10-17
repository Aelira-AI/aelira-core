"""Real PostgreSQL proof for partial media scan score persistence."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from conftest import require_disposable_postgres_url


pytestmark = pytest.mark.integration


def test_nullable_score_upgrade_and_guarded_downgrade_preserve_rows(monkeypatch):
    url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("requires an explicit disposable TEST_MIGRATION_DATABASE_URL")
    require_disposable_postgres_url(url, destructive=False)

    path = (
        Path(__file__).parents[1]
        / "alembic/versions/2026_10_18_media_assessment_truthfulness.py"
    )
    spec = importlib.util.spec_from_file_location("media_assessment_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "20261017_canvas_scope"

    schema = f"media_truth_{uuid4().hex}"
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                connection.execute(
                    text(
                        "CREATE TABLE scan_results (id text PRIMARY KEY, compliance_score double precision NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO scan_results (id, compliance_score) VALUES ('legacy', 84)"
                    )
                )
                monkeypatch.setattr(
                    migration, "op", Operations(MigrationContext.configure(connection))
                )

                migration.upgrade()
                columns = {
                    column["name"]: column
                    for column in inspect(connection).get_columns(
                        "scan_results", schema=schema
                    )
                }
                assert columns["compliance_score"]["nullable"]
                connection.execute(
                    text(
                        "INSERT INTO scan_results (id, compliance_score) VALUES ('partial', NULL)"
                    )
                )

                savepoint = connection.begin_nested()
                with pytest.raises(DBAPIError, match="reconciliation"):
                    migration.downgrade()
                savepoint.rollback()
                assert connection.execute(
                    text("SELECT id, compliance_score FROM scan_results ORDER BY id")
                ).all() == [("legacy", 84.0), ("partial", None)]

                connection.execute(
                    text("DELETE FROM scan_results WHERE id = 'partial'")
                )
                migration.downgrade()
                columns = {
                    column["name"]: column
                    for column in inspect(connection).get_columns(
                        "scan_results", schema=schema
                    )
                }
                assert not columns["compliance_score"]["nullable"]
                assert (
                    connection.execute(
                        text(
                            "SELECT compliance_score FROM scan_results WHERE id = 'legacy'"
                        )
                    ).scalar_one()
                    == 84.0
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
