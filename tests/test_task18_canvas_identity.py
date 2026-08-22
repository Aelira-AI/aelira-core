"""Composite Canvas identity contracts for Task 18."""

import importlib.util
from pathlib import Path

from src.db.models import CloudFile

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic/versions/2026_08_22_task18_canvas_identity.py"
INDEX_NAME = "uq_cloud_files_canvas_content_identity"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "task18_canvas_identity", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_model_declares_unique_canvas_composite_identity_index():
    indexes = {index.name: index for index in CloudFile.__table__.indexes}

    assert INDEX_NAME in indexes
    assert indexes[INDEX_NAME].unique is True
    expression_text = " ".join(
        str(expression) for expression in indexes[INDEX_NAME].expressions
    )
    assert "department_id" in expression_text
    assert "provider" in expression_text
    assert "provider_parent_id" in expression_text
    assert "content_source" in expression_text
    assert "provider_file_id" in expression_text
    assert "coalesce" in expression_text.lower()


def test_migration_fails_closed_on_duplicates_and_creates_composite_index(monkeypatch):
    migration = _load_migration()
    executed = []
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: executed.append(str(statement))
    )

    migration.upgrade()

    assert migration.revision == "20260822_task18_identity"
    assert migration.down_revision == "20260822_upload_effect_fence"
    sql = "\n".join(executed).lower()
    assert "having count(*) > 1" in sql
    assert "create unique index" in sql
    assert INDEX_NAME in sql
    assert "coalesce(content_source, 'file')" in sql
    assert "provider = 'canvas'" in sql


def test_migration_downgrade_drops_only_composite_index(monkeypatch):
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, table_name=None: calls.append((name, table_name)),
    )

    migration.downgrade()

    assert calls == [(INDEX_NAME, "cloud_files")]
