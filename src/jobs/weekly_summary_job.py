"""Durable weekly-summary scheduling and queue delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import CloudJobQueue, EmailAlertSettings, MaintenanceCursor
from src.mailer import get_email_service

from .contracts import JobContext, JobFailure, JobResult, JobSuccess, LostJobOwnership
from .email_alert_job import build_weekly_summary_payload

SCHEDULER_CURSOR_KEY = "weekly_summary_schedule"
SCHEDULER_RECOVERY_HOURS = 168
_SCHEDULER_ADVISORY_KEY = 8_315_741_702_221


def _hour_floor(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _parse_hour(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _hour_floor(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def iter_schedule_hours(
    *,
    last_checked: datetime | None,
    now: datetime,
    recovery_hours: int = SCHEDULER_RECOVERY_HOURS,
) -> list[datetime]:
    """Return unprocessed UTC hour buckets, bounded to one weekly cycle."""
    current = _hour_floor(now)
    recovery_hours = max(1, min(SCHEDULER_RECOVERY_HOURS, int(recovery_hours)))
    earliest = current - timedelta(hours=recovery_hours - 1)
    start = (
        current
        if last_checked is None
        else _hour_floor(last_checked) + timedelta(hours=1)
    )
    start = max(start, earliest)
    if start > current:
        return []
    bucket_count = int((current - start).total_seconds() // 3600) + 1
    return [start + timedelta(hours=offset) for offset in range(bucket_count)]


def build_due_settings_query(bucket: datetime):
    """Select department-wide settings due in one exact UTC hour bucket."""
    bucket = _hour_floor(bucket)
    return select(EmailAlertSettings).where(
        EmailAlertSettings.alert_weekly_summary.is_(True),
        EmailAlertSettings.user_id.is_(None),
        EmailAlertSettings.weekly_summary_day == bucket.weekday(),
        EmailAlertSettings.weekly_summary_hour == bucket.hour,
    )


def weekly_summary_dedupe_key(bucket: datetime) -> str:
    """Return the stable schedule-window identity stored on a queue row."""
    return f"weekly-summary:{_hour_floor(bucket).strftime('%Y%m%dT%H%M%SZ')}"


class WeeklySummaryScheduler:
    """Advance one durable cursor and enqueue due department windows."""

    @staticmethod
    def _acquire(db: Session) -> bool:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return True
        return (
            db.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": _SCHEDULER_ADVISORY_KEY},
            ).scalar_one()
            is True
        )

    def run_once(
        self, db: Session, *, now: datetime | None = None
    ) -> dict[str, int | bool]:
        now = now or datetime.now(timezone.utc)
        if not self._acquire(db):
            db.rollback()
            return {"acquired": False, "buckets": 0, "enqueued": 0}

        cursor = db.get(MaintenanceCursor, SCHEDULER_CURSOR_KEY)
        if cursor is None:
            cursor = MaintenanceCursor(key=SCHEDULER_CURSOR_KEY, cursor_json={})
            db.add(cursor)
        cursor_json = cursor.cursor_json if isinstance(cursor.cursor_json, dict) else {}
        last_checked = _parse_hour(cursor_json.get("last_checked_hour"))
        buckets = iter_schedule_hours(last_checked=last_checked, now=now)
        enqueued = 0

        for bucket in buckets:
            settings_list = db.scalars(build_due_settings_query(bucket)).all()
            for settings in settings_list:
                job = CloudJobQueue(
                    department_id=str(settings.department_id),
                    job_type="weekly_summary",
                    payload={
                        "window_end": bucket.isoformat(),
                        "window_start": (bucket - timedelta(days=7)).isoformat(),
                    },
                    dedupe_key=weekly_summary_dedupe_key(bucket),
                    max_retries=3,
                    priority=5,
                    scheduled_for=now,
                )
                try:
                    with db.begin_nested():
                        db.add(job)
                        db.flush()
                    enqueued += 1
                except IntegrityError:
                    continue

        current_hour = _hour_floor(now)
        cursor.cursor_json = {
            "last_checked_hour": current_hour.isoformat(),
            "last_enqueued_count": enqueued,
            "last_error_code": None,
            "last_success_at": now.astimezone(timezone.utc).isoformat(),
        }
        return {"acquired": True, "buckets": len(buckets), "enqueued": enqueued}

    @staticmethod
    def record_failure(db: Session, *, now: datetime | None = None) -> None:
        """Persist one bounded failure signal after a rolled-back iteration."""
        now = now or datetime.now(timezone.utc)
        cursor = db.get(MaintenanceCursor, SCHEDULER_CURSOR_KEY)
        if cursor is None:
            cursor = MaintenanceCursor(key=SCHEDULER_CURSOR_KEY, cursor_json={})
            db.add(cursor)
        current = cursor.cursor_json if isinstance(cursor.cursor_json, dict) else {}
        cursor.cursor_json = {
            **current,
            "last_error_code": "weekly_summary_scheduler_failed",
            "last_failure_at": now.astimezone(timezone.utc).isoformat(),
        }


def _delivery_window(payload: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(payload, dict):
        return None
    start = _parse_hour(payload.get("window_start"))
    end = _parse_hour(payload.get("window_end"))
    if start is None or end is None or end - start != timedelta(days=7):
        return None
    return start, end


async def handle_weekly_summary_job(
    context: JobContext, db: Session, _token_manager: Any
) -> JobResult:
    """Prepare then deliver one department/window behind an external-effect fence."""
    job = db.get(CloudJobQueue, context.job_id)
    window = _delivery_window(dict(context.payload))
    if job is None or window is None:
        return JobFailure.deterministic("weekly_summary_payload_invalid")

    settings = db.scalar(
        select(EmailAlertSettings).where(
            EmailAlertSettings.department_id == str(job.department_id),
            EmailAlertSettings.user_id.is_(None),
            EmailAlertSettings.alert_weekly_summary.is_(True),
        )
    )
    if settings is None:
        return JobSuccess({"status": "no_op", "success": True})

    try:
        delivery = build_weekly_summary_payload(
            db,
            settings,
            window_start=window[0],
            window_end=window[1],
        )
    except Exception:
        return JobFailure.retryable("weekly_summary_preparation_failed")
    if delivery is None:
        return JobSuccess({"status": "no_op", "success": True})

    try:
        await context.assert_owned()
        await context.begin_external_effect()
        result = await get_email_service().send_weekly_summary(**delivery)
    except LostJobOwnership:
        raise
    except Exception:
        return JobFailure.indeterminate(
            "weekly_summary_delivery_indeterminate",
            {"manual_required": True, "retry_safe": False},
        )
    if not isinstance(result, dict) or result.get("success") is not True:
        return JobFailure.indeterminate(
            "weekly_summary_delivery_indeterminate",
            {"manual_required": True, "retry_safe": False},
        )
    return JobSuccess({"status": "completed", "success": True})


__all__ = [
    "SCHEDULER_CURSOR_KEY",
    "WeeklySummaryScheduler",
    "build_due_settings_query",
    "handle_weekly_summary_job",
    "iter_schedule_hours",
    "weekly_summary_dedupe_key",
]
