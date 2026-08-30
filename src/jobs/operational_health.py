"""Privacy-bounded operational state for durable workers and queue alerts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import CloudJobQueue, MaintenanceCursor, WorkerHeartbeat
from src.jobs.job_processor import build_runnable_pending_query
from src.jobs.registry import EXECUTABLE_JOB_TYPES


def _aware(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    value = _aware(value)
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())


def _progress_watermark(heartbeat: WorkerHeartbeat) -> datetime | None:
    metadata = heartbeat.metadata_json
    raw = metadata.get("progress_watermark_at") if isinstance(metadata, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def classify_worker_health(
    *,
    live_workers: int,
    runnable_pending: int,
    processing_count: int,
    expired_processing: int,
    stalled_processing: int,
    latest_progress: datetime | None,
    cutoff: datetime,
) -> str:
    """Classify one bounded global worker state for every alert surface."""
    if not live_workers:
        return "worker_unavailable"
    if expired_processing:
        return "expired_lease"
    if stalled_processing:
        return "stuck_processing"
    if processing_count:
        return "healthy_processing"
    if runnable_pending and (
        latest_progress is None or _aware(latest_progress) < _aware(cutoff)
    ):
        return "stuck_runnable_backlog"
    if runnable_pending:
        return "healthy_advancing"
    return "healthy_idle"


@dataclass(frozen=True)
class WorkerHealthSnapshot:
    """Aggregate state safe for global operators and low-cardinality metrics."""

    health_state: str
    queue: Mapping[str, int]
    live_workers: int
    draining_workers: int
    latest_heartbeat_at: datetime | None
    latest_heartbeat_age_seconds: float | None
    latest_progress_at: datetime | None
    latest_progress_age_seconds: float | None
    oldest_pending_created_at: datetime | None
    oldest_pending_age_seconds: float | None
    oldest_processing_heartbeat_at: datetime | None
    oldest_running_job_age_seconds: float | None
    runnable_pending: int
    expired_processing: int
    stalled_processing: int
    jobs_claimed: int
    jobs_completed: int
    jobs_failed: int
    weekly_summary_scheduler_state: str = "not_started"
    weekly_summary_last_success_at: datetime | None = None
    weekly_summary_last_success_age_seconds: float | None = None
    weekly_summary_last_error_code: str | None = None

    @property
    def status(self) -> str:
        return "healthy" if self.health_state.startswith("healthy_") else "degraded"


def build_worker_health_snapshot(
    *,
    now: datetime,
    heartbeat_cutoff: datetime,
    execution_seconds: float,
    queue_counts: Mapping[str, int],
    live_workers: int,
    draining_workers: int,
    latest_heartbeat: datetime | None,
    latest_progress: datetime | None,
    jobs_claimed: int,
    jobs_completed: int,
    jobs_failed: int,
    runnable_pending: int,
    processing_jobs: Sequence[object],
    oldest_pending: datetime | None,
    weekly_summary_scheduler_state: str = "not_started",
    weekly_summary_last_success: datetime | None = None,
    weekly_summary_last_error_code: str | None = None,
) -> WorkerHealthSnapshot:
    """Build alert state from already-bounded database facts."""
    now = _aware(now) or now
    heartbeat_cutoff = _aware(heartbeat_cutoff) or heartbeat_cutoff
    execution_seconds = min(86400.0, max(1.0, float(execution_seconds)))
    stalled_cutoff = now - timedelta(seconds=execution_seconds + 120.0)
    expired_processing = 0
    stalled_processing = 0
    claimed_times: list[datetime] = []
    heartbeat_times: list[datetime] = []
    for job in processing_jobs:
        lease = _aware(getattr(job, "lease_expires_at", None))
        claimed = _aware(getattr(job, "claimed_at", None))
        heartbeat = _aware(getattr(job, "heartbeat_at", None))
        if claimed is not None:
            claimed_times.append(claimed)
        if heartbeat is not None:
            heartbeat_times.append(heartbeat)
        if lease is None or lease < now:
            expired_processing += 1
        elif claimed is None or claimed < stalled_cutoff:
            stalled_processing += 1

    normalized_queue = {
        status: int(queue_counts.get(status, 0))
        for status in ("pending", "processing", "completed", "failed")
    }
    latest_heartbeat = _aware(latest_heartbeat)
    latest_progress = _aware(latest_progress)
    oldest_pending = _aware(oldest_pending)
    oldest_processing_heartbeat = min(heartbeat_times, default=None)
    oldest_running_claim = min(claimed_times, default=None)
    health_state = classify_worker_health(
        live_workers=int(live_workers),
        runnable_pending=int(runnable_pending),
        processing_count=normalized_queue["processing"],
        expired_processing=expired_processing,
        stalled_processing=stalled_processing,
        latest_progress=latest_progress,
        cutoff=heartbeat_cutoff,
    )
    return WorkerHealthSnapshot(
        health_state=health_state,
        queue=normalized_queue,
        live_workers=int(live_workers),
        draining_workers=int(draining_workers),
        latest_heartbeat_at=latest_heartbeat,
        latest_heartbeat_age_seconds=_age_seconds(now, latest_heartbeat),
        latest_progress_at=latest_progress,
        latest_progress_age_seconds=_age_seconds(now, latest_progress),
        oldest_pending_created_at=oldest_pending,
        oldest_pending_age_seconds=_age_seconds(now, oldest_pending),
        oldest_processing_heartbeat_at=oldest_processing_heartbeat,
        oldest_running_job_age_seconds=_age_seconds(now, oldest_running_claim),
        runnable_pending=int(runnable_pending),
        expired_processing=expired_processing,
        stalled_processing=stalled_processing,
        jobs_claimed=int(jobs_claimed),
        jobs_completed=int(jobs_completed),
        jobs_failed=int(jobs_failed),
        weekly_summary_scheduler_state=weekly_summary_scheduler_state,
        weekly_summary_last_success_at=_aware(weekly_summary_last_success),
        weekly_summary_last_success_age_seconds=_age_seconds(
            now, weekly_summary_last_success
        ),
        weekly_summary_last_error_code=weekly_summary_last_error_code,
    )


def _weekly_summary_scheduler_health(
    cursor: MaintenanceCursor | None, *, now: datetime
) -> tuple[str, datetime | None, str | None]:
    payload = cursor.cursor_json if cursor is not None else None
    if not isinstance(payload, dict):
        return "not_started", None, None
    last_success = _progress_timestamp(payload.get("last_success_at"))
    error_code = payload.get("last_error_code")
    safe_error = error_code if error_code == "weekly_summary_scheduler_failed" else None
    if safe_error is not None:
        return "failed", last_success, safe_error
    age = _age_seconds(now, last_success)
    if age is None:
        return "not_started", None, None
    if age > 900:
        return "stale", last_success, None
    return "healthy", last_success, None


def _progress_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def collect_worker_health_snapshot(
    db: Session, *, now: datetime | None = None
) -> WorkerHealthSnapshot:
    """Read one global, identifier-free worker and queue snapshot."""
    now = _aware(now) if now is not None else datetime.now(timezone.utc)
    assert now is not None
    cutoff = now - timedelta(minutes=2)
    queue_counts = {
        str(status): count
        for status, count in db.query(
            CloudJobQueue.status, func.count(CloudJobQueue.id)
        ).group_by(CloudJobQueue.status)
    }
    live_filter = (
        WorkerHeartbeat.status.in_(("running", "draining")),
        WorkerHeartbeat.heartbeat_at >= cutoff,
    )
    live_workers = (
        db.query(func.count(WorkerHeartbeat.worker_id)).filter(*live_filter).scalar()
        or 0
    )
    draining_workers = (
        db.query(func.count(WorkerHeartbeat.worker_id))
        .filter(
            WorkerHeartbeat.status == "draining",
            WorkerHeartbeat.heartbeat_at >= cutoff,
        )
        .scalar()
        or 0
    )
    latest_heartbeat = db.query(func.max(WorkerHeartbeat.heartbeat_at)).scalar()
    live_heartbeats = db.query(WorkerHeartbeat).filter(*live_filter).all()
    progress_watermarks = [
        watermark
        for heartbeat in live_heartbeats
        if (watermark := _progress_watermark(heartbeat)) is not None
    ]
    latest_progress = max(progress_watermarks, default=None)

    def _sum(column) -> int:
        return int(
            db.query(func.coalesce(func.sum(column), 0))
            .filter(WorkerHeartbeat.worker_id.is_not(None))
            .scalar()
            or 0
        )

    oldest_pending = (
        db.query(func.min(CloudJobQueue.created_at))
        .filter(CloudJobQueue.status == "pending")
        .scalar()
    )
    runnable_ids = build_runnable_pending_query(
        EXECUTABLE_JOB_TYPES, now=now
    ).subquery()
    runnable_pending = db.scalar(select(func.count()).select_from(runnable_ids)) or 0
    processing_jobs = (
        db.query(CloudJobQueue).filter(CloudJobQueue.status == "processing").all()
    )
    scheduler_state, scheduler_success, scheduler_error = (
        _weekly_summary_scheduler_health(
            db.get(MaintenanceCursor, "weekly_summary_schedule"),
            now=now,
        )
    )
    try:
        execution_seconds = float(
            os.environ.get("JOB_WORKER_MAX_EXECUTION_SECONDS", "3600")
        )
    except ValueError:
        execution_seconds = 3600.0
    return build_worker_health_snapshot(
        now=now,
        heartbeat_cutoff=cutoff,
        execution_seconds=execution_seconds,
        queue_counts=queue_counts,
        live_workers=live_workers,
        draining_workers=draining_workers,
        latest_heartbeat=latest_heartbeat,
        latest_progress=latest_progress,
        jobs_claimed=_sum(WorkerHeartbeat.jobs_claimed),
        jobs_completed=_sum(WorkerHeartbeat.jobs_completed),
        jobs_failed=_sum(WorkerHeartbeat.jobs_failed),
        runnable_pending=runnable_pending,
        processing_jobs=processing_jobs,
        oldest_pending=oldest_pending,
        weekly_summary_scheduler_state=scheduler_state,
        weekly_summary_last_success=scheduler_success,
        weekly_summary_last_error_code=scheduler_error,
    )
