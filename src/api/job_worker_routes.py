"""Operational status for the dedicated durable queue workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, or_
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
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal

router = APIRouter(prefix="/api/jobs", tags=["Job workers"])


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
    now = datetime.now(timezone.utc)
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
        "status": "healthy" if live_workers else "degraded",
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
