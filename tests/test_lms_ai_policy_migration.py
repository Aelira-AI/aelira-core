"""Task 14 slice 1 migration chain, defaults, and constraint contracts."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "2026_08_20_lms_ai_policy.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "lms_ai_policy_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_chain_revision_length_defaults_constraints_and_downgrade(
    monkeypatch,
):
    migration = _load_migration()
    calls = []

    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: calls.append(("add_column", table, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition: calls.append(
            ("create_check_constraint", name, table, str(condition))
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table, **kwargs: calls.append(
            ("drop_constraint", name, table, kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column: calls.append(("drop_column", table, column)),
    )

    migration.upgrade()
    upgrade_calls = list(calls)
    calls.clear()
    migration.downgrade()
    downgrade_calls = list(calls)

    assert migration.down_revision == "20260819_alert_schedule"
    assert len(migration.revision) <= 32
    columns = {call[2].name: call[2] for call in upgrade_calls[:3]}
    assert set(columns) == {"lms_ai_enabled", "lms_ai_provider", "lms_ai_purposes"}
    assert columns["lms_ai_enabled"].nullable is False
    assert str(columns["lms_ai_enabled"].server_default.arg).lower() == "false"
    assert columns["lms_ai_provider"].nullable is True
    assert columns["lms_ai_purposes"].nullable is False
    assert str(columns["lms_ai_purposes"].server_default.arg) == "'[]'::jsonb"

    checks = {call[1]: call[3] for call in upgrade_calls[3:]}
    assert "ck_departments_lms_ai_provider" in checks
    assert all(
        provider in checks["ck_departments_lms_ai_provider"]
        for provider in ("ollama", "gemini", "openai", "anthropic", "xai")
    )
    assert "ck_departments_lms_ai_purposes" in checks
    purposes_check = checks["ck_departments_lms_ai_purposes"]
    assert "jsonb_typeof" in purposes_check
    assert "jsonb_array_length(lms_ai_purposes::jsonb)" in purposes_check
    assert purposes_check.count("CASE WHEN") == 2
    assert "@> '[\"remediation\"]'::jsonb" in purposes_check
    assert "@> '[\"alt_text\"]'::jsonb" in purposes_check

    consistency_check = checks["ck_departments_lms_ai_policy_consistency"]
    assert "NOT lms_ai_enabled" in consistency_check
    assert "lms_ai_provider IS NULL" in consistency_check
    assert "lms_ai_purposes::jsonb = '[]'::jsonb" in consistency_check
    assert "lms_ai_enabled" in consistency_check
    assert "lms_ai_provider IS NOT NULL" in consistency_check
    assert "jsonb_array_length(lms_ai_purposes::jsonb) > 0" in consistency_check

    assert [call[0] for call in downgrade_calls] == [
        "drop_constraint",
        "drop_constraint",
        "drop_constraint",
        "drop_column",
        "drop_column",
        "drop_column",
    ]
