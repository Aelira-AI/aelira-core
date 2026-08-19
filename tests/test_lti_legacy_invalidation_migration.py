"""Security migration invalidates LTI users without reviving them on rollback."""

import importlib.util
from pathlib import Path

from src.db.models import APIKey

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "2026_08_19_invalidate_legacy_lti_users.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "lti_legacy_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_api_key_prefix_has_named_model_index():
    indexes = {index.name: index for index in APIKey.__table__.indexes}

    assert "ix_api_keys_key_prefix" in indexes
    assert [column.name for column in indexes["ix_api_keys_key_prefix"].columns] == [
        "key_prefix"
    ]


def test_upgrade_creates_api_key_prefix_index(monkeypatch):
    migration = _load_migration()
    indexes = []
    monkeypatch.setattr(migration.op, "add_column", lambda *_: None)
    monkeypatch.setattr(migration.op, "execute", lambda *_: None)
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns: indexes.append((name, table, columns)),
    )

    migration.upgrade()

    assert indexes == [("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])]


def test_upgrade_marks_and_deactivates_only_active_lti_users(monkeypatch):
    migration = _load_migration()
    columns = []
    statements = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: columns.append((table, column)),
    )
    monkeypatch.setattr(migration.op, "create_index", lambda *_: None)
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.revision == "20260819_lti_reauth"
    assert migration.down_revision == "2026_08_18_canvas_content"
    assert len(columns) == 1
    table, marker = columns[0]
    assert table == "users"
    assert marker.name == "lti_reauthorization_required"
    assert marker.nullable is False
    assert str(marker.server_default.arg).lower() == "false"

    sql = "\n".join(str(statement).lower() for statement in statements)
    assert "update users" in sql
    assert "set is_active = false" in sql
    assert "lti_reauthorization_required = true" in sql
    assert "auth_provider = 'lti'" in sql
    assert "is_active = true" in sql
    assert "update api_keys" in sql
    assert "set is_active = false" in sql
    assert "user_id" in sql


def test_upgrade_revokes_all_legacy_static_prefix_api_keys(monkeypatch):
    migration = _load_migration()
    statements = []
    monkeypatch.setattr(migration.op, "add_column", lambda *_: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *_: None)
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(str(statement).lower() for statement in statements)
    assert "update api_keys set is_active = false" in sql
    assert "where key_prefix = 'aelira_live_'" in sql


def test_downgrade_reverses_only_added_schema(monkeypatch):
    migration = _load_migration()
    dropped_indexes = []
    dropped_columns = []
    executed = []
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, table_name=None: dropped_indexes.append((name, table_name)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column: dropped_columns.append((table, column)),
    )
    monkeypatch.setattr(migration.op, "execute", executed.append)

    migration.downgrade()

    assert dropped_indexes == [("ix_api_keys_key_prefix", "api_keys")]
    assert dropped_columns == [("users", "lti_reauthorization_required")]
    assert executed == []
