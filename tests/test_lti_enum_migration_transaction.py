"""PostgreSQL enum additions must commit before later migrations use them."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_03_19_lti_auth_provider.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("lti_auth_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_lti_enum_addition_runs_inside_autocommit_block_before_schema_work(monkeypatch):
    migration = _load_migration()
    events = []

    class Context:
        @contextmanager
        def autocommit_block(self):
            events.append("autocommit-enter")
            yield
            events.append("autocommit-exit")

    monkeypatch.setattr(migration.op, "get_context", lambda: Context())
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: events.append(("execute", statement)),
    )
    monkeypatch.setattr(migration, "column_exists", lambda *_args: True)
    monkeypatch.setattr(migration, "table_exists", lambda *_args: True)

    migration.upgrade()

    assert events == [
        "autocommit-enter",
        ("execute", "ALTER TYPE authprovider ADD VALUE IF NOT EXISTS 'lti'"),
        "autocommit-exit",
    ]
