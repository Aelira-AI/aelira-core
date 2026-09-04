"""Operational status for the dedicated durable queue workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from src.db.database import get_db_dependency
from src.db.models import (
    ArtifactOrphanQuarantine,
    ContentWritebackLog,
    RemediationArtifact,
    UserRole,
)
from src.jobs.operational_health import collect_worker_health_snapshot
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal

router = APIRouter(prefix="/api/jobs", tags=["Job workers"])


NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class WorkerQueueStatus(BaseModel):
    pending: NonNegativeInt
    processing: NonNegativeInt
    completed: NonNegativeInt
    failed: NonNegativeInt


class WorkerLivenessStatus(BaseModel):
    live: NonNegativeInt
    draining: NonNegativeInt
    latest_heartbeat_at: datetime | None
    latest_heartbeat_age_seconds: NonNegativeFloat | None


class WorkerProgressStatus(BaseModel):
    jobs_claimed: NonNegativeInt
    jobs_completed: NonNegativeInt
    jobs_failed: NonNegativeInt
    oldest_pending_created_at: datetime | None
    oldest_pending_age_seconds: NonNegativeFloat | None
    oldest_processing_heartbeat_at: datetime | None
    oldest_running_job_age_seconds: NonNegativeFloat | None
    runnable_pending: NonNegativeInt
    expired_processing: NonNegativeInt
    stalled_processing: NonNegativeInt
    latest_progress_at: datetime | None
    latest_progress_age_seconds: NonNegativeFloat | None


class WorkerMaintenanceStatus(BaseModel):
    artifact_cleanup_due: NonNegativeInt


class WeeklySummarySchedulerStatus(BaseModel):
    state: Literal["not_started", "healthy", "stale", "failed"]
    last_success_at: datetime | None
    last_success_age_seconds: NonNegativeFloat | None
    last_error_code: Literal["weekly_summary_scheduler_failed"] | None


class WorkerReconciliationStatus(BaseModel):
    required: NonNegativeInt
    manual_required: NonNegativeInt
    failed_manual: NonNegativeInt


class WorkerOrphanStatus(BaseModel):
    pending_move: NonNegativeInt
    quarantined: NonNegativeInt
    restore_required: NonNegativeInt
    reviewed: NonNegativeInt
    purging: NonNegativeInt


class WorkerStatusResponse(BaseModel):
    generated_at: datetime
    status: Literal["healthy", "degraded"]
    health_state: Literal[
        "worker_unavailable",
        "expired_lease",
        "stuck_processing",
        "healthy_processing",
        "stuck_runnable_backlog",
        "healthy_advancing",
        "healthy_idle",
    ]
    queue: WorkerQueueStatus
    workers: WorkerLivenessStatus
    progress: WorkerProgressStatus
    maintenance: WorkerMaintenanceStatus
    weekly_summary_scheduler: WeeklySummarySchedulerStatus
    reconciliation: WorkerReconciliationStatus
    orphans: WorkerOrphanStatus


@router.get("/worker-status", response_model=WorkerStatusResponse)
def worker_status(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return bounded queue depth and aggregate worker liveness metrics."""
    # Global operational topology is never department/account-manager data.
    if principal.user_role is not UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    snapshot = collect_worker_health_snapshot(db)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=2)
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
        "generated_at": now,
        "status": snapshot.status,
        "health_state": snapshot.health_state,
        "queue": snapshot.queue,
        "workers": {
            "live": snapshot.live_workers,
            "draining": snapshot.draining_workers,
            "latest_heartbeat_at": snapshot.latest_heartbeat_at,
            "latest_heartbeat_age_seconds": snapshot.latest_heartbeat_age_seconds,
        },
        "progress": {
            "jobs_claimed": snapshot.jobs_claimed,
            "jobs_completed": snapshot.jobs_completed,
            "jobs_failed": snapshot.jobs_failed,
            "oldest_pending_created_at": snapshot.oldest_pending_created_at,
            "oldest_pending_age_seconds": snapshot.oldest_pending_age_seconds,
            "oldest_processing_heartbeat_at": snapshot.oldest_processing_heartbeat_at,
            "oldest_running_job_age_seconds": snapshot.oldest_running_job_age_seconds,
            "runnable_pending": snapshot.runnable_pending,
            "expired_processing": snapshot.expired_processing,
            "stalled_processing": snapshot.stalled_processing,
            "latest_progress_at": snapshot.latest_progress_at,
            "latest_progress_age_seconds": snapshot.latest_progress_age_seconds,
        },
        "maintenance": {"artifact_cleanup_due": cleanup_due},
        "weekly_summary_scheduler": {
            "state": snapshot.weekly_summary_scheduler_state,
            "last_success_at": snapshot.weekly_summary_last_success_at,
            "last_success_age_seconds": (
                snapshot.weekly_summary_last_success_age_seconds
            ),
            "last_error_code": snapshot.weekly_summary_last_error_code,
        },
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
