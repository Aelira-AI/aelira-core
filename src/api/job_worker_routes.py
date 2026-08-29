"""Operational status for the dedicated durable queue workers."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from src.db.database import get_db_dependency
from src.db.models import (
    ArtifactOrphanQuarantine,
    CloudJobQueue,
    ContentWritebackLog,
    RemediationArtifact,
    UserRole,
    WorkerHeartbeat,
)
from src.jobs.job_processor import build_runnable_pending_query
from src.jobs.registry import EXECUTABLE_JOB_TYPES
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal

router = APIRouter(prefix="/api/jobs", tags=["Job workers"])


def _worker_health_state(
    *,
    live_workers: int,
    runnable_pending: int,
    processing_count: int,
    expired_processing: int,
    stalled_processing: int,
    latest_progress: datetime | None,
    cutoff: datetime,
) -> str:
    if not live_workers:
        return "worker_unavailable"
    if expired_processing:
        return "expired_lease"
    if stalled_processing:
        return "stuck_processing"
    if processing_count:
        return "healthy_processing"
    if runnable_pending and (latest_progress is None or latest_progress < cutoff):
        return "stuck_runnable_backlog"
    if runnable_pending:
        return "healthy_advancing"
    return "healthy_idle"


@router.get("/worker-status")
def worker_status(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return bounded queue depth and aggregate worker liveness metrics."""
    # Global operational topology is never department/account-manager data.
    if principal.user_role is not UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    queue = {
        status: count
        for status, count in db.query(
            CloudJobQueue.status, func.count(CloudJobQueue.id)
        ).group_by(CloudJobQueue.status)
    }
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    live_workers = (
        db.query(func.count(WorkerHeartbeat.worker_id))
        .filter(
            WorkerHeartbeat.status.in_(("running", "draining")),
            WorkerHeartbeat.heartbeat_at >= cutoff,
        )
        .scalar()
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
    latest = db.query(func.max(WorkerHeartbeat.heartbeat_at)).scalar()
    live_heartbeat_rows = (
        db.query(WorkerHeartbeat)
        .filter(
            WorkerHeartbeat.status.in_(("running", "draining")),
            WorkerHeartbeat.heartbeat_at >= cutoff,
        )
        .all()
    )
    progress_watermarks: list[datetime] = []
    for heartbeat in live_heartbeat_rows:
        metadata = getattr(heartbeat, "metadata_json", None)
        raw = (
            metadata.get("progress_watermark_at")
            if isinstance(metadata, dict)
            else None
        )
        if not isinstance(raw, str):
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        progress_watermarks.append(parsed)
    latest_progress = max(progress_watermarks, default=None)
    jobs_claimed = (
        db.query(func.coalesce(func.sum(WorkerHeartbeat.jobs_claimed), 0))
        .filter(WorkerHeartbeat.worker_id.is_not(None))
        .scalar()
        or 0
    )
    jobs_completed = (
        db.query(func.coalesce(func.sum(WorkerHeartbeat.jobs_completed), 0))
        .filter(WorkerHeartbeat.worker_id.is_not(None))
        .scalar()
        or 0
    )
    jobs_failed = (
        db.query(func.coalesce(func.sum(WorkerHeartbeat.jobs_failed), 0))
        .filter(WorkerHeartbeat.worker_id.is_not(None))
        .scalar()
        or 0
    )
    oldest_pending = (
        db.query(func.min(CloudJobQueue.created_at))
        .filter(CloudJobQueue.status == "pending")
        .scalar()
    )
    oldest_processing_heartbeat = (
        db.query(func.min(CloudJobQueue.heartbeat_at))
        .filter(CloudJobQueue.status == "processing")
        .scalar()
    )
    now = datetime.now(timezone.utc)
    runnable_ids = build_runnable_pending_query(
        EXECUTABLE_JOB_TYPES, now=now
    ).subquery()
    runnable_pending = db.scalar(select(func.count()).select_from(runnable_ids)) or 0
    processing_jobs = (
        db.query(CloudJobQueue).filter(CloudJobQueue.status == "processing").all()
    )
    expired_processing = sum(
        1
        for job in processing_jobs
        if job.lease_expires_at is None or job.lease_expires_at < now
    )
    try:
        execution_seconds = float(
            os.environ.get("JOB_WORKER_MAX_EXECUTION_SECONDS", "3600")
        )
    except ValueError:
        execution_seconds = 3600.0
    execution_seconds = min(86400.0, max(1.0, execution_seconds))
    stalled_cutoff = now - timedelta(seconds=execution_seconds + 120.0)
    stalled_processing = sum(
        1
        for job in processing_jobs
        if job.lease_expires_at is not None
        and job.lease_expires_at >= now
        and (job.claimed_at is None or job.claimed_at < stalled_cutoff)
    )
    processing_count = queue.get("processing", 0)
    health_state = _worker_health_state(
        live_workers=live_workers,
        runnable_pending=runnable_pending,
        processing_count=processing_count,
        expired_processing=expired_processing,
        stalled_processing=stalled_processing,
        latest_progress=latest_progress,
        cutoff=cutoff,
    )
    cleanup_due = (
        db.query(func.count(RemediationArtifact.id))
        .filter(
            or_(
                RemediationArtifact.lifecycle_status.in_(("expired", "superseded")),
                and_(
                    RemediationArtifact.lifecycle_status == "available",
                    RemediationArtifact.expires_at <= now,
                ),
                and_(
                    RemediationArtifact.lifecycle_status == "staging",
                    RemediationArtifact.publication_heartbeat_at < cutoff,
                ),
            )
        )
        .scalar()
        or 0
    )

    def _count(model, column, value: str) -> int:
        return db.query(func.count(model.id)).filter(column == value).scalar() or 0

    reconciliation_required = _count(
        ContentWritebackLog,
        ContentWritebackLog.reconciliation_status,
        "reconciliation_required",
    )
    reconciliation_manual = _count(
        ContentWritebackLog,
        ContentWritebackLog.reconciliation_status,
        "manual_required",
    )
    reconciliation_failed = _count(
        ContentWritebackLog,
        ContentWritebackLog.reconciliation_status,
        "failed_manual",
    )
    orphan_pending_move = _count(
        ArtifactOrphanQuarantine,
        ArtifactOrphanQuarantine.status,
        "pending_move",
    )
    orphan_quarantined = _count(
        ArtifactOrphanQuarantine,
        ArtifactOrphanQuarantine.status,
        "quarantined",
    )
    orphan_restore_required = _count(
        ArtifactOrphanQuarantine,
        ArtifactOrphanQuarantine.status,
        "restore_required",
    )
    orphan_reviewed = _count(
        ArtifactOrphanQuarantine,
        ArtifactOrphanQuarantine.status,
        "reviewed",
    )
    orphan_purging = _count(
        ArtifactOrphanQuarantine,
        ArtifactOrphanQuarantine.status,
        "purging",
    )
    return {
        "status": "healthy" if health_state.startswith("healthy_") else "degraded",
        "health_state": health_state,
        "queue": {
            "pending": queue.get("pending", 0),
            "processing": queue.get("processing", 0),
            "completed": queue.get("completed", 0),
            "failed": queue.get("failed", 0),
        },
        "workers": {
            "live": live_workers,
            "draining": draining_workers,
            "latest_heartbeat_at": latest,
        },
        "progress": {
            "jobs_claimed": int(jobs_claimed),
            "jobs_completed": int(jobs_completed),
            "jobs_failed": int(jobs_failed),
            "oldest_pending_created_at": oldest_pending,
            "oldest_processing_heartbeat_at": oldest_processing_heartbeat,
            "runnable_pending": int(runnable_pending),
            "expired_processing": int(expired_processing),
            "stalled_processing": int(stalled_processing),
            "latest_progress_at": latest_progress,
        },
        "maintenance": {"artifact_cleanup_due": cleanup_due},
        "reconciliation": {
            "required": reconciliation_required,
            "manual_required": reconciliation_manual,
            "failed_manual": reconciliation_failed,
        },
        "orphans": {
            "pending_move": orphan_pending_move,
            "quarantined": orphan_quarantined,
            "restore_required": orphan_restore_required,
            "reviewed": orphan_reviewed,
            "purging": orphan_purging,
        },
    }
