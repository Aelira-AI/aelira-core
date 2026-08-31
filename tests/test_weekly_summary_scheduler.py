from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from src.jobs.contracts import FailureKind, JobContext, JobFailure, JobSuccess
from src.mailer.email_service import EmailService
from src.jobs.weekly_summary_job import (
    SCHEDULER_CURSOR_KEY,
    WeeklySummaryScheduler,
    build_due_settings_query,
    handle_weekly_summary_job,
    iter_schedule_hours,
    weekly_summary_dedupe_key,
)

UTC = timezone.utc


def _context(*, begin_external_effect: AsyncMock | None = None) -> JobContext:
    return JobContext(
        job_id="11111111-1111-4111-8111-111111111111",
        job_type="weekly_summary",
        payload={
            "window_end": "2026-08-24T10:00:00+00:00",
            "window_start": "2026-08-17T10:00:00+00:00",
        },
        claim_token="claim-1",
        worker_id="worker-test",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
        assert_owned=AsyncMock(),
        begin_external_effect=begin_external_effect
        or AsyncMock(return_value="effect-token"),
    )


def test_schedule_hours_are_exact_and_recover_missed_buckets() -> None:
    now = datetime(2026, 8, 30, 14, 37, tzinfo=UTC)
    last_checked = datetime(2026, 8, 30, 11, 0, tzinfo=UTC)

    assert iter_schedule_hours(last_checked=last_checked, now=now) == [
        datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        datetime(2026, 8, 30, 13, 0, tzinfo=UTC),
        datetime(2026, 8, 30, 14, 0, tzinfo=UTC),
    ]


def test_schedule_recovery_is_bounded_to_one_week() -> None:
    now = datetime(2026, 8, 30, 14, 37, tzinfo=UTC)
    buckets = iter_schedule_hours(
        last_checked=now - timedelta(days=30),
        now=now,
    )

    assert len(buckets) == 168
    assert buckets[0] == datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    assert buckets[-1] == datetime(2026, 8, 30, 14, 0, tzinfo=UTC)


def test_due_query_requires_enabled_department_schedule_match() -> None:
    bucket = datetime(2026, 8, 26, 14, tzinfo=UTC)  # Wednesday
    compiled = str(
        build_due_settings_query(bucket).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "email_alert_settings.alert_weekly_summary IS true" in compiled
    assert "email_alert_settings.user_id IS NULL" in compiled
    assert "email_alert_settings.weekly_summary_day = 2" in compiled
    assert "email_alert_settings.weekly_summary_hour = 14" in compiled


def test_dedupe_key_is_stable_per_exact_department_window() -> None:
    bucket = datetime(2026, 8, 26, 14, tzinfo=UTC)

    assert weekly_summary_dedupe_key(bucket) == "weekly-summary:20260826T140000Z"


def test_scheduler_persists_cursor_and_privacy_bounded_job() -> None:
    bucket = datetime(2026, 8, 26, 14, 37, tzinfo=UTC)
    database = MagicMock()
    database.get_bind.return_value.dialect.name = "sqlite"
    database.get.return_value = None
    database.scalars.return_value.all.return_value = [
        SimpleNamespace(department_id="33333333-3333-4333-8333-333333333333")
    ]
    database.begin_nested.side_effect = lambda: nullcontext()

    result = WeeklySummaryScheduler().run_once(database, now=bucket)

    added = [call.args[0] for call in database.add.call_args_list]
    cursor = next(
        item for item in added if getattr(item, "key", None) == SCHEDULER_CURSOR_KEY
    )
    job = next(
        item for item in added if getattr(item, "job_type", None) == "weekly_summary"
    )
    assert result == {"acquired": True, "buckets": 1, "enqueued": 1}
    assert cursor.cursor_json["last_checked_hour"] == "2026-08-26T14:00:00+00:00"
    assert job.payload == {
        "window_end": "2026-08-26T14:00:00+00:00",
        "window_start": "2026-08-19T14:00:00+00:00",
    }
    assert "@" not in str(job.payload)


def test_duplicate_trigger_attempt_is_an_idempotent_noop() -> None:
    bucket = datetime(2026, 8, 26, 14, 37, tzinfo=UTC)
    database = MagicMock()
    database.get_bind.return_value.dialect.name = "sqlite"
    database.get.return_value = None
    database.scalars.return_value.all.return_value = [
        SimpleNamespace(department_id="33333333-3333-4333-8333-333333333333")
    ]
    database.begin_nested.side_effect = lambda: nullcontext()
    database.flush.side_effect = IntegrityError("duplicate", {}, Exception())

    result = WeeklySummaryScheduler().run_once(database, now=bucket)

    assert result == {"acquired": True, "buckets": 1, "enqueued": 0}


@pytest.mark.asyncio
async def test_disabled_delivery_noops_before_external_effect(monkeypatch) -> None:
    from src.jobs import weekly_summary_job

    context = _context()
    database = MagicMock()
    database.get.return_value = SimpleNamespace(
        department_id="33333333-3333-4333-8333-333333333333"
    )
    database.scalar.return_value = None
    send = AsyncMock()
    monkeypatch.setattr(
        weekly_summary_job,
        "get_email_service",
        lambda: SimpleNamespace(send_weekly_summary=send),
    )

    result = await handle_weekly_summary_job(context, database, None)

    assert isinstance(result, JobSuccess)
    assert result.result["status"] == "no_op"
    context.begin_external_effect.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_send_failure_is_retryable(monkeypatch) -> None:
    from src.jobs import weekly_summary_job

    context = _context()
    database = MagicMock()
    database.get.return_value = SimpleNamespace(
        department_id="33333333-3333-4333-8333-333333333333"
    )
    database.scalar.return_value = SimpleNamespace(alert_weekly_summary=True)
    monkeypatch.setattr(
        weekly_summary_job,
        "build_weekly_summary_payload",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )

    result = await handle_weekly_summary_job(context, database, None)

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.RETRYABLE
    assert result.code == "weekly_summary_preparation_failed"
    context.begin_external_effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_provider_outcome_is_terminal_and_not_retry_safe(
    monkeypatch,
) -> None:
    from src.jobs import weekly_summary_job

    events: list[str] = []
    begin_external_effect = AsyncMock(
        side_effect=lambda: events.append("fence") or "effect-token"
    )
    context = _context(begin_external_effect=begin_external_effect)
    database = MagicMock()
    database.get.return_value = SimpleNamespace(
        department_id="33333333-3333-4333-8333-333333333333"
    )
    database.scalar.return_value = SimpleNamespace(alert_weekly_summary=True)
    monkeypatch.setattr(
        weekly_summary_job,
        "build_weekly_summary_payload",
        MagicMock(return_value={"to_emails": ["recipient@example.test"]}),
    )
    send = AsyncMock(
        side_effect=lambda **_kwargs: events.append("provider")
        or {"success": False, "error": "Delivery unavailable"}
    )
    monkeypatch.setattr(
        weekly_summary_job,
        "get_email_service",
        lambda: SimpleNamespace(send_weekly_summary=send),
    )

    result = await handle_weekly_summary_job(context, database, None)

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.INDETERMINATE
    assert result.code == "weekly_summary_delivery_indeterminate"
    assert result.details == {"manual_required": True, "retry_safe": False}
    context.begin_external_effect.assert_awaited_once()
    send.assert_awaited_once()
    assert events == ["fence", "provider"]


def test_queue_fence_and_permanent_window_index_cover_weekly_delivery() -> None:
    from src.db.models import CloudJobQueue

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in CloudJobQueue.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    indexes = {index.name: index for index in CloudJobQueue.__table__.indexes}

    assert "weekly_summary" in constraints["ck_cloud_job_queue_external_effect_owned"]
    window_index = indexes["uq_cloud_job_queue_weekly_summary_window"]
    assert window_index.unique is True
    predicate = str(window_index.dialect_options["postgresql"]["where"])
    assert "weekly_summary" in predicate
    assert "status" not in predicate


def test_migration_downgrade_clears_weekly_delivery_fences() -> None:
    migration = Path(
        "alembic/versions/2026_08_30_weekly_summary_scheduler.py"
    ).read_text()

    assert "WHERE job_type = 'weekly_summary'" in migration
    assert "external_effect_state = NULL" in migration


def test_weekly_external_effect_failure_uses_specific_terminal_code() -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    worker = JobProcessor(session_factory=MagicMock())
    claim = ClaimedJob(
        "job-1",
        "weekly_summary",
        {},
        "claim-1",
        "worker-1",
        1,
        3,
    )
    values = worker._finish_values(
        claim,
        JobFailure.indeterminate("provider_unknown"),
        external_effect_state="requesting",
    )

    assert values["status"] == "failed"
    assert values["last_error_code"] == "weekly_summary_delivery_indeterminate"
    assert values["last_error_retryable"] is False
    assert values["external_effect_state"] == "indeterminate"


@pytest.mark.parametrize(
    ("attempt_count", "expected_status"),
    [(1, "pending"), (3, "failed")],
)
def test_weekly_pre_send_retries_are_bounded(
    attempt_count: int, expected_status: str
) -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    worker = JobProcessor(session_factory=MagicMock())
    claim = ClaimedJob(
        "job-1",
        "weekly_summary",
        {},
        "claim-1",
        "worker-1",
        attempt_count,
        3,
    )
    values = worker._finish_values(
        claim,
        JobFailure.retryable("weekly_summary_preparation_failed"),
        external_effect_state=None,
    )

    assert values["status"] == expected_status
    assert values["last_error_retryable"] is True


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "not_started"),
        (
            {
                "last_error_code": "weekly_summary_scheduler_failed",
                "last_success_at": "2026-08-30T13:55:00+00:00",
            },
            "failed",
        ),
        ({"last_success_at": "2026-08-30T13:55:00+00:00"}, "healthy"),
        ({"last_success_at": "2026-08-30T13:00:00+00:00"}, "stale"),
    ],
)
def test_scheduler_health_states_are_bounded(payload, expected) -> None:
    from src.jobs.operational_health import _weekly_summary_scheduler_health

    cursor = SimpleNamespace(cursor_json=payload)
    state, _last_success, error_code = _weekly_summary_scheduler_health(
        cursor,
        now=datetime(2026, 8, 30, 14, tzinfo=UTC),
    )

    assert state == expected
    assert error_code in {None, "weekly_summary_scheduler_failed"}
    assert "department" not in str((state, error_code))


@pytest.mark.asyncio
async def test_unavailable_fixed_metric_is_rendered_truthfully() -> None:
    service = EmailService()
    service.send_email = AsyncMock(return_value={"success": True})

    result = await service.send_weekly_summary(
        to_emails=["recipient@example.test"],
        department_name="Accessibility",
        total_files=3,
        total_issues=2,
        issues_fixed=None,
    )

    assert result == {"success": True}
    rendered = service.send_email.await_args.kwargs["html_content"]
    assert "Not available" in rendered
    assert ">Issues Fixed</p>" in rendered


def test_worker_scheduler_transaction_records_success(monkeypatch) -> None:
    from src.jobs import worker

    database = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = database
    scheduler = MagicMock()
    monkeypatch.setattr(worker, "SessionLocal", MagicMock(return_value=session))

    assert worker.run_weekly_summary_scheduler_once(
        scheduler,
        now=datetime(2026, 8, 30, 14, tzinfo=UTC),
    )
    scheduler.run_once.assert_called_once_with(
        database,
        now=datetime(2026, 8, 30, 14, tzinfo=UTC),
    )
    database.commit.assert_called_once()


def test_worker_scheduler_failure_is_bounded_and_persisted(monkeypatch) -> None:
    from src.jobs import worker

    failed_database = MagicMock()
    health_database = MagicMock()
    failed_session = MagicMock()
    failed_session.__enter__.return_value = failed_database
    health_session = MagicMock()
    health_session.__enter__.return_value = health_database
    scheduler = MagicMock()
    scheduler.run_once.side_effect = RuntimeError("private database detail")
    monkeypatch.setattr(
        worker,
        "SessionLocal",
        MagicMock(side_effect=[failed_session, health_session]),
    )
    now = datetime(2026, 8, 30, 14, tzinfo=UTC)

    assert worker.run_weekly_summary_scheduler_once(scheduler, now=now) is False
    scheduler.record_failure.assert_called_once_with(health_database, now=now)
    health_database.commit.assert_called_once()
