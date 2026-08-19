"""Task8A session-rotation migration contract."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_08_19_session_refresh_rotation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "session_rotation_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_adds_nullable_replay_columns_and_schema_only_downgrade(monkeypatch):
    migration = _load_migration()
    added = []
    dropped = []
    monkeypatch.setattr(
        migration.op, "add_column", lambda table, column: added.append((table, column))
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column: dropped.append((table, column)),
    )

    migration.upgrade()
    migration.downgrade()

    expected = {
        "previous_refresh_token_hash",
        "refresh_grace_expires_at",
        "refresh_replay_used_at",
        "refresh_replay_ciphertext",
    }
    assert migration.down_revision == "2026_08_19_invalidate_legacy_lti_users"
    assert {
        column.name for table, column in added if table == "user_sessions"
    } == expected
    assert all(column.nullable for _table, column in added)
    added_types = {column.name: type(column.type).__name__ for _table, column in added}
    assert added_types == {
        "previous_refresh_token_hash": "String",
        "refresh_grace_expires_at": "DateTime",
        "refresh_replay_used_at": "DateTime",
        "refresh_replay_ciphertext": "Text",
    }
    assert {(table, column) for table, column in dropped} == {
        ("user_sessions", name) for name in expected
    }
