"""Task10 weekly alert schedule hardening contracts."""

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.api import alert_routes
from src.api.alert_routes import AlertSettingsRequest
from src.db.models import EmailAlertSettings
from src.jobs import email_alert_job

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_08_19_email_alert_weekly_schedule.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "email_alert_weekly_schedule_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_backfills_before_constraints_and_downgrade_preserves_data(
    monkeypatch,
):
    migration = _load_migration()
    calls = []

    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: calls.append(("execute", str(statement), {})),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append(
            ("alter_column", (table, column), kwargs)
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition: calls.append(
            ("create_check_constraint", (name, table, str(condition)), {})
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table, **kwargs: calls.append(
            ("drop_constraint", (name, table), kwargs)
        ),
    )

    migration.upgrade()
    upgrade_calls = list(calls)
    calls.clear()
    migration.downgrade()
    downgrade_calls = list(calls)

    assert migration.down_revision == "20260819_session_refresh"
    assert [call[0] for call in upgrade_calls] == [
        "execute",
        "execute",
        "alter_column",
        "alter_column",
        "create_check_constraint",
        "create_check_constraint",
    ]
    assert "weekly_summary_day IS NULL" in upgrade_calls[0][1]
    assert "weekly_summary_day < 0" in upgrade_calls[0][1]
    assert "weekly_summary_day > 6" in upgrade_calls[0][1]
    assert "SET weekly_summary_day = 0" in upgrade_calls[0][1]
    assert "weekly_summary_hour IS NULL" in upgrade_calls[1][1]
    assert "weekly_summary_hour < 0" in upgrade_calls[1][1]
    assert "weekly_summary_hour > 23" in upgrade_calls[1][1]
    assert "SET weekly_summary_hour = 9" in upgrade_calls[1][1]

    alters = {
        call[1][1]: call[2] for call in upgrade_calls if call[0] == "alter_column"
    }
    assert alters["weekly_summary_day"]["nullable"] is False
    assert str(alters["weekly_summary_day"]["server_default"]) == "0"
    assert alters["weekly_summary_hour"]["nullable"] is False
    assert str(alters["weekly_summary_hour"]["server_default"]) == "9"
    checks = {call[1][0]: call[1][2] for call in upgrade_calls[-2:]}
    assert checks == {
        "ck_email_alert_settings_weekly_summary_day_range": (
            "weekly_summary_day BETWEEN 0 AND 6"
        ),
        "ck_email_alert_settings_weekly_summary_hour_range": (
            "weekly_summary_hour BETWEEN 0 AND 23"
        ),
    }

    assert [call[0] for call in downgrade_calls] == [
        "drop_constraint",
        "drop_constraint",
        "alter_column",
        "alter_column",
    ]
    assert all(call[0] != "execute" for call in downgrade_calls)
    downgrade_alters = {
        call[1][1]: call[2] for call in downgrade_calls if call[0] == "alter_column"
    }
    assert downgrade_alters["weekly_summary_day"]["nullable"] is True
    assert downgrade_alters["weekly_summary_day"]["server_default"] is None
    assert downgrade_alters["weekly_summary_hour"]["nullable"] is True
    assert downgrade_alters["weekly_summary_hour"]["server_default"] is None


def test_email_alert_schedule_model_metadata_matches_database_contract():
    day = EmailAlertSettings.__table__.c.weekly_summary_day
    hour = EmailAlertSettings.__table__.c.weekly_summary_hour

    assert day.nullable is False
    assert day.default.arg == 0
    assert str(day.server_default.arg) == "0"
    assert hour.nullable is False
    assert hour.default.arg == 9
    assert str(hour.server_default.arg) == "9"
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in EmailAlertSettings.__table__.constraints
        if constraint.name
        in {
            "ck_email_alert_settings_weekly_summary_day_range",
            "ck_email_alert_settings_weekly_summary_hour_range",
        }
    }
    assert checks == {
        "ck_email_alert_settings_weekly_summary_day_range": (
            "weekly_summary_day BETWEEN 0 AND 6"
        ),
        "ck_email_alert_settings_weekly_summary_hour_range": (
            "weekly_summary_hour BETWEEN 0 AND 23"
        ),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weekly_summary_day", 0),
        ("weekly_summary_day", 6),
        ("weekly_summary_hour", 0),
        ("weekly_summary_hour", 23),
        ("weekly_summary_day", None),
        ("weekly_summary_hour", None),
    ],
)
def test_alert_settings_request_accepts_schedule_boundaries(field, value):
    assert getattr(AlertSettingsRequest(**{field: value}), field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weekly_summary_day", -1),
        ("weekly_summary_day", 7),
        ("weekly_summary_hour", -1),
        ("weekly_summary_hour", 24),
    ],
)
def test_alert_settings_request_rejects_out_of_range_schedule(field, value):
    with pytest.raises(ValidationError):
        AlertSettingsRequest(**{field: value})


def test_get_alert_settings_compatibly_defaults_legacy_null_schedule(monkeypatch):
    settings = SimpleNamespace(
        id="settings-1",
        department_id="department-1",
        alert_on_scan_complete=True,
        alert_on_critical_issues=True,
        alert_weekly_summary=True,
        email_addresses=[],
        weekly_summary_day=None,
        weekly_summary_hour=None,
        created_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        updated_at=None,
    )
    monkeypatch.setattr(
        alert_routes, "get_or_create_settings", lambda _db, _id: settings
    )

    response = asyncio.run(
        alert_routes.get_alert_settings(
            api_key=SimpleNamespace(department_id="department-1"), db=MagicMock()
        )
    )

    assert response.weekly_summary_day == 0
    assert response.weekly_summary_hour == 9


def test_scheduler_selects_only_enabled_exact_current_schedule(monkeypatch):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc)  # Wednesday

    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = []
    db = MagicMock()
    db.query.return_value = query
    monkeypatch.setattr(email_alert_job, "datetime", FrozenDateTime)

    result = asyncio.run(email_alert_job.send_weekly_summaries(db))

    criteria = query.filter.call_args.args
    compiled = " AND ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in criteria
    )
    assert "email_alert_settings.alert_weekly_summary" in compiled
    assert "email_alert_settings.weekly_summary_day = 2" in compiled
    assert "email_alert_settings.weekly_summary_hour = 14" in compiled
    assert result == {"total_departments": 0, "emails_sent": 0, "errors": 0}
