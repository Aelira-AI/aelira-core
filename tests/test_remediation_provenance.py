"""Persisted remediation provenance contracts."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from src.db.models import CloudFile

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT / "alembic" / "versions" / "2026_08_22_task21_remediation_provenance.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("task21_provenance", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_cloud_file_has_nullable_constrained_remediation_origin():
    column = CloudFile.__table__.c.remediation_origin
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in CloudFile.__table__.constraints
        if getattr(constraint, "name", None)
    }

    assert column.nullable is True
    assert constraints["ck_cloud_files_remediation_origin"] == (
        "remediation_origin IS NULL OR " "remediation_origin IN ('automatic', 'manual')"
    )


def test_migration_adds_only_nullable_owned_column_and_constraint(monkeypatch):
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: calls.append(("add_column", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *args, **kwargs: calls.append(("create_check_constraint", args, kwargs)),
    )

    migration.upgrade()

    assert migration.down_revision == "20260822_task18_identity"
    assert [call[0] for call in calls] == ["add_column", "create_check_constraint"]
    column = calls[0][1][1]
    assert column.name == "remediation_origin"
    assert column.nullable is True
    assert calls[1][1] == (
        "ck_cloud_files_remediation_origin",
        "cloud_files",
        "remediation_origin IS NULL OR remediation_origin IN ('automatic', 'manual')",
    )


def test_migration_downgrade_removes_only_owned_constraint_and_column(monkeypatch):
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: calls.append(("drop_constraint", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda *args, **kwargs: calls.append(("drop_column", args, kwargs)),
    )

    migration.downgrade()

    assert calls == [
        (
            "drop_constraint",
            ("ck_cloud_files_remediation_origin", "cloud_files"),
            {"type_": "check"},
        ),
        ("drop_column", ("cloud_files", "remediation_origin"), {}),
    ]


def test_canvas_invalidation_clears_remediation_origin():
    from src.services.canvas_identity_service import invalidate_canvas_derived_state

    cloud_file = SimpleNamespace(remediation_origin="automatic")
    invalidate_canvas_derived_state(cloud_file)

    assert cloud_file.remediation_origin is None
