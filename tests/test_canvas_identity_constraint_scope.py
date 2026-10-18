"""Canvas content IDs may overlap without weakening other cloud providers."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from src.db.models import CloudFile


def test_canvas_content_ids_can_overlap_but_other_provider_file_ids_cannot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    table = CloudFile.__table__
    table.create(engine)

    def row(id: str, provider: str, source: str, course: str) -> dict:
        return {
            "id": id,
            "department_id": "department",
            "credential_id": "credential",
            "provider": provider,
            "provider_file_id": "182",
            "provider_parent_id": course,
            "content_source": source,
            "file_name": f"{source}-{id}",
            "file_type": "html",
        }

    with engine.begin() as connection:
        connection.execute(
            table.insert(), row("canvas-file", "canvas", "file", "course")
        )
        connection.execute(
            table.insert(), row("canvas-page", "canvas", "page", "course")
        )
        connection.execute(
            table.insert(), row("other-one", "google", "file", "folder-one")
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                table.insert(), row("other-two", "google", "file", "folder-two")
            )


def test_migration_replaces_global_constraint_after_non_canvas_index(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/2026_10_17_canvas_identity_scope.py"
    )
    spec = importlib.util.spec_from_file_location("canvas_identity_scope", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    calls = []
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: calls.append(("create_index", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: calls.append(("drop_constraint", args, kwargs)),
    )
    migration.upgrade()

    assert migration.down_revision == "20260928_pdf_edit_pub"
    assert [call[0] for call in calls] == ["create_index", "drop_constraint"]
    index = calls[0]
    assert index[1][:2] == ("uq_cloud_files_non_canvas_file_identity", "cloud_files")
    assert str(index[2]["postgresql_where"]) == "provider <> 'canvas'"
    assert calls[1][1][:2] == ("uq_cloud_files_dept_provider_file", "cloud_files")
