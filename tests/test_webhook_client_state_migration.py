"""Real PostgreSQL proof for the additive cloud_webhook_subscriptions.client_state_hash column."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from conftest import require_disposable_postgres_url
from src.database_url import engine_url
from src.db.models import CloudWebhookSubscription

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[1]


def _migration():
    path = ROOT / "alembic/versions/2026_10_28_webhook_client_state.py"
    spec = importlib.util.spec_from_file_location("webhook_client_state", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_is_the_single_head_after_media_captions():
    migration = _migration()
    assert migration.revision == "20261028_webhook_secret"
    assert migration.down_revision == "20261020_media_captions"
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["20261028_webhook_secret"]
    column = CloudWebhookSubscription.__table__.c.client_state_hash
    assert column.nullable is True
    assert column.server_default is None
    assert column.type.length == 64


def test_upgrade_is_additive_and_idempotent_and_downgrade_keeps_the_column(
    monkeypatch,
):
    url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("requires an explicit disposable migration database URL")
    require_disposable_postgres_url(url, destructive=False)
    migration = _migration()

    schema = f"webhook_secret_{uuid4().hex}"
    engine = create_engine(engine_url(url))
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                connection.execute(
                    text(
                        "CREATE TABLE cloud_webhook_subscriptions (id text PRIMARY KEY, "
                        "provider varchar(20) NOT NULL, subscription_id varchar(255) "
                        "NOT NULL, is_active boolean)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO cloud_webhook_subscriptions "
                        "(id, provider, subscription_id, is_active) VALUES "
                        "('legacy', 'microsoft', 'graph-sub', true), "
                        "('google', 'google', 'channel-1', true)"
                    )
                )
                monkeypatch.setattr(
                    migration, "op", Operations(MigrationContext.configure(connection))
                )

                def columns():
                    return {
                        column["name"]: column
                        for column in inspect(connection).get_columns(
                            "cloud_webhook_subscriptions", schema=schema
                        )
                    }

                migration.upgrade()
                migration.upgrade()  # idempotent
                added = columns()["client_state_hash"]
                assert added["nullable"] is True
                assert added["default"] is None
                assert getattr(added["type"], "length", None) == 64
                # No backfill: an existing subscription has no secret and its
                # notifications are refused until it is recreated.
                assert connection.execute(
                    text(
                        "SELECT id, subscription_id, client_state_hash "
                        "FROM cloud_webhook_subscriptions ORDER BY id"
                    )
                ).all() == [
                    ("google", "channel-1", None),
                    ("legacy", "graph-sub", None),
                ]
                connection.execute(
                    text(
                        "UPDATE cloud_webhook_subscriptions "
                        "SET client_state_hash = :stored WHERE id = 'legacy'"
                    ),
                    {"stored": "a" * 64},
                )

                # Downgrading drops nothing, so a stored hash survives a
                # downgrade and a later upgrade.
                migration.downgrade()
                assert "client_state_hash" in columns()
                migration.upgrade()
                assert connection.execute(
                    text(
                        "SELECT id, provider, subscription_id, is_active, "
                        "client_state_hash FROM cloud_webhook_subscriptions ORDER BY id"
                    )
                ).all() == [
                    ("google", "google", "channel-1", True, None),
                    ("legacy", "microsoft", "graph-sub", True, "a" * 64),
                ]
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
