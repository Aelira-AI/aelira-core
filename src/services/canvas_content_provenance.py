"""Durable provenance and review fences for Canvas stored HTML remediation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.db.models import (
    CanvasContentRemediationEvidence,
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudProvider,
)

CANDIDATE_KEY = "canvas_content_candidate"
OWNER_KEY = "canvas_content_remediation"
EVIDENCE_MAX_BYTES = 4096
EVIDENCE_MAX_ROWS_PER_FILE = 20
EVIDENCE_RETENTION_DAYS = 30
EVIDENCE_MAINTENANCE_BATCH = 200

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_OWNER_STATUSES = frozenset(
    {"pending", "processing", "completed", "failed", "invalidated"}
)


class CanvasContentProvenanceError(RuntimeError):
    """A bounded provenance validation or capacity failure."""


def canvas_content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _metadata(cloud_file: CloudFile) -> dict[str, Any]:
    return (
        dict(cloud_file.provider_metadata)
        if isinstance(cloud_file.provider_metadata, dict)
        else {}
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def canvas_candidate_fingerprint(
    *,
    department_id: str,
    credential_id: str,
    cloud_file_id: str,
    provider_file_id: str,
    provider_parent_id: str,
    content_source: str,
    content_slug: str | None,
    content_updated_at: str,
    source_sha256: str,
    scan_id: str,
    producer_job_id: str,
    snapshot_sha256: str,
    candidate_sha256: str,
) -> str:
    material = {
        "department_id": department_id,
        "credential_id": credential_id,
        "cloud_file_id": cloud_file_id,
        "provider": CloudProvider.CANVAS.value,
        "provider_file_id": provider_file_id,
        "provider_parent_id": provider_parent_id,
        "content_source": content_source,
        "content_slug": content_slug,
        "content_updated_at": content_updated_at,
        "source_sha256": source_sha256,
        "scan_id": scan_id,
        "producer_job_id": producer_job_id,
        "snapshot_sha256": snapshot_sha256,
        "candidate_sha256": candidate_sha256,
    }
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _allowlisted_diagnostics(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    owner = metadata.get(OWNER_KEY)
    if isinstance(owner, dict):
        clean_owner: dict[str, str] = {}
        for key in ("job_id", "scan_id"):
            value = owner.get(key)
            if isinstance(value, str) and 0 < len(value) <= 64:
                clean_owner[key] = value
        status = owner.get("status")
        if isinstance(status, str) and status in _OWNER_STATUSES:
            clean_owner["status"] = status
        source_hash = owner.get("source_sha256")
        if isinstance(source_hash, str) and _HASH_RE.fullmatch(source_hash):
            clean_owner["source_sha256"] = source_hash
        if clean_owner:
            result[OWNER_KEY] = clean_owner
    candidate = metadata.get(CANDIDATE_KEY)
    if isinstance(candidate, dict):
        clean_candidate = {
            key: value
            for key in (
                "fingerprint",
                "source_sha256",
                "candidate_sha256",
                "snapshot_sha256",
            )
            if isinstance((value := candidate.get(key)), str)
            and _HASH_RE.fullmatch(value)
        }
        for key in ("scan_id", "producer_job_id"):
            value = candidate.get(key)
            if isinstance(value, str) and 0 < len(value) <= 64:
                clean_candidate[key] = value
        if clean_candidate:
            result[CANDIDATE_KEY] = clean_candidate
    encoded = _canonical_json(result)
    if len(encoded) > EVIDENCE_MAX_BYTES:
        raise CanvasContentProvenanceError("canvas_evidence_too_large")
    return result


def _archive_candidate(
    db: Session, cloud_file: CloudFile, metadata: dict[str, Any], reason: str
) -> None:
    candidate = metadata.get(CANDIDATE_KEY)
    if not isinstance(candidate, dict):
        return
    source_sha256 = candidate.get("source_sha256")
    candidate_sha256 = candidate.get("candidate_sha256")
    if not (
        isinstance(source_sha256, str)
        and _HASH_RE.fullmatch(source_sha256)
        and isinstance(candidate_sha256, str)
        and _HASH_RE.fullmatch(candidate_sha256)
    ):
        return
    diagnostics = _allowlisted_diagnostics(metadata)
    stored_bytes = len(_canonical_json(diagnostics))
    if stored_bytes < 1 or stored_bytes > EVIDENCE_MAX_BYTES:
        raise CanvasContentProvenanceError("canvas_evidence_too_large")
    evidence_id = hashlib.sha256(
        _canonical_json(
            {
                "cloud_file_id": str(cloud_file.id),
                "candidate_sha256": candidate_sha256,
                "reason": reason,
            }
        )
    ).hexdigest()
    if db.get(CanvasContentRemediationEvidence, evidence_id) is not None:
        return
    now = datetime.now(timezone.utc)
    db.add(
        CanvasContentRemediationEvidence(
            id=evidence_id,
            department_id=cloud_file.department_id,
            cloud_file_id=str(cloud_file.id),
            source_sha256=source_sha256,
            candidate_sha256=candidate_sha256,
            source_scan_id=candidate.get("scan_id"),
            producer_job_id=candidate.get("producer_job_id"),
            quarantine_reason=reason,
            diagnostics=diagnostics,
            stored_bytes=stored_bytes,
            lifecycle_state="current",
            created_at=now,
            expires_at=now + timedelta(days=EVIDENCE_RETENTION_DAYS),
        )
    )
    db.flush()
    ids = list(
        db.scalars(
            select(CanvasContentRemediationEvidence.id)
            .where(CanvasContentRemediationEvidence.cloud_file_id == cloud_file.id)
            .order_by(
                CanvasContentRemediationEvidence.created_at.desc(),
                CanvasContentRemediationEvidence.id.desc(),
            )
            .offset(EVIDENCE_MAX_ROWS_PER_FILE)
        )
    )
    if ids:
        db.execute(
            delete(CanvasContentRemediationEvidence).where(
                CanvasContentRemediationEvidence.id.in_(ids)
            )
        )


def invalidate_canvas_content_candidate(
    db: Session,
    cloud_file: CloudFile,
    *,
    reason: str,
    clear_output: bool = True,
) -> None:
    """Quarantine bounded evidence and remove a candidate from live use."""
    if not isinstance(reason, str) or _REASON_RE.fullmatch(reason) is None:
        raise CanvasContentProvenanceError("canvas_evidence_invalid_reason")
    metadata = _metadata(cloud_file)
    _archive_candidate(db, cloud_file, metadata, reason)
    metadata.pop(CANDIDATE_KEY, None)
    owner = metadata.get(OWNER_KEY)
    if isinstance(owner, dict):
        metadata[OWNER_KEY] = {
            key: value
            for key, value in owner.items()
            if key in {"job_id", "source_sha256", "scan_id"}
        }
        metadata[OWNER_KEY].update({"status": "invalidated", "reason": reason})
    cloud_file.provider_metadata = metadata
    cloud_file.writeback_status = None
    if clear_output:
        cloud_file.remediated_body = None
        cloud_file.remediated_compliance_score = None
        cloud_file.remediated_issues_fixed = None
        cloud_file.remediated_issues_remaining = None
        cloud_file.has_remediated_version = False


def install_canvas_content_owner(
    db: Session,
    cloud_file: CloudFile,
    *,
    job_id: str,
    source_sha256: str,
    scan_id: str,
    status: str,
) -> None:
    metadata = _metadata(cloud_file)
    candidate = metadata.get(CANDIDATE_KEY)
    prior_job_id = (
        candidate.get("producer_job_id") if isinstance(candidate, dict) else None
    )
    if prior_job_id != job_id and (
        isinstance(candidate, dict) or cloud_file.has_remediated_version
    ):
        invalidate_canvas_content_candidate(
            db, cloud_file, reason="newer_remediation_owner"
        )
        metadata = _metadata(cloud_file)
    metadata[OWNER_KEY] = {
        "job_id": job_id,
        "status": status,
        "source_sha256": source_sha256,
        "scan_id": scan_id,
    }
    cloud_file.provider_metadata = metadata


def publish_canvas_content_candidate(
    cloud_file: CloudFile,
    *,
    credential_id: str,
    provider_file_id: str,
    provider_parent_id: str,
    content_source: str,
    content_slug: str | None,
    content_updated_at: str,
    source_sha256: str,
    scan_id: str,
    producer_job_id: str,
    snapshot_sha256: str,
    candidate_sha256: str,
) -> None:
    metadata = _metadata(cloud_file)
    fingerprint = canvas_candidate_fingerprint(
        department_id=cloud_file.department_id,
        credential_id=credential_id,
        cloud_file_id=str(cloud_file.id),
        provider_file_id=provider_file_id,
        provider_parent_id=provider_parent_id,
        content_source=content_source,
        content_slug=content_slug,
        content_updated_at=content_updated_at,
        source_sha256=source_sha256,
        scan_id=scan_id,
        producer_job_id=producer_job_id,
        snapshot_sha256=snapshot_sha256,
        candidate_sha256=candidate_sha256,
    )
    metadata[CANDIDATE_KEY] = {
        "fingerprint": fingerprint,
        "source_sha256": source_sha256,
        "scan_id": scan_id,
        "producer_job_id": producer_job_id,
        "snapshot_sha256": snapshot_sha256,
        "candidate_sha256": candidate_sha256,
        "status": "completed",
        "verified": False,
    }
    metadata[OWNER_KEY] = {
        "job_id": producer_job_id,
        "status": "completed",
        "source_sha256": source_sha256,
        "scan_id": scan_id,
    }
    cloud_file.provider_metadata = metadata


def canvas_content_candidate_is_current(
    db: Session, cloud_file: CloudFile, *, lock_job: bool = False
) -> bool:
    if cloud_file.provider != CloudProvider.CANVAS.value:
        return False
    if cloud_file.content_source in (None, "file"):
        return bool(cloud_file.has_remediated_version)
    if not isinstance(cloud_file.content_body, str) or not isinstance(
        cloud_file.remediated_body, str
    ):
        return False
    metadata = _metadata(cloud_file)
    candidate = metadata.get(CANDIDATE_KEY)
    owner = metadata.get(OWNER_KEY)
    if not isinstance(candidate, dict) or not isinstance(owner, dict):
        return False
    source_sha256 = canvas_content_sha256(cloud_file.content_body)
    candidate_sha256 = canvas_content_sha256(cloud_file.remediated_body)
    job_id = candidate.get("producer_job_id")
    scan_id = candidate.get("scan_id")
    snapshot_sha256 = candidate.get("snapshot_sha256")
    expected_fingerprint = canvas_candidate_fingerprint(
        department_id=cloud_file.department_id,
        credential_id=cloud_file.credential_id,
        cloud_file_id=str(cloud_file.id),
        provider_file_id=cloud_file.provider_file_id,
        provider_parent_id=(
            cloud_file.provider_parent_id
            if isinstance(cloud_file.provider_parent_id, str)
            else ""
        ),
        content_source=(
            cloud_file.content_source
            if isinstance(cloud_file.content_source, str)
            else ""
        ),
        content_slug=(
            cloud_file.content_slug
            if isinstance(cloud_file.content_slug, str)
            else None
        ),
        content_updated_at=(
            cloud_file.content_updated_at.isoformat()
            if isinstance(cloud_file.content_updated_at, datetime)
            else ""
        ),
        source_sha256=source_sha256,
        scan_id=scan_id if isinstance(scan_id, str) else "",
        producer_job_id=job_id if isinstance(job_id, str) else "",
        snapshot_sha256=(snapshot_sha256 if isinstance(snapshot_sha256, str) else ""),
        candidate_sha256=candidate_sha256,
    )
    if (
        candidate.get("status") != "completed"
        or candidate.get("source_sha256") != source_sha256
        or candidate.get("candidate_sha256") != candidate_sha256
        or candidate.get("fingerprint") != expected_fingerprint
        or scan_id != cloud_file.last_scan_id
        or owner
        != {
            "job_id": job_id,
            "status": "completed",
            "source_sha256": source_sha256,
            "scan_id": scan_id,
        }
    ):
        return False
    query = select(CloudJobQueue).where(
        CloudJobQueue.id == job_id,
        CloudJobQueue.department_id == cloud_file.department_id,
        CloudJobQueue.job_type == "canvas_content",
        CloudJobQueue.cloud_file_id == cloud_file.id,
        CloudJobQueue.provider == CloudProvider.CANVAS.value,
        CloudJobQueue.credential_id == cloud_file.credential_id,
        CloudJobQueue.provider_file_id == cloud_file.provider_file_id,
        CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
        CloudJobQueue.max_retries == 0,
    )
    if lock_job:
        query = query.with_for_update()
    job = db.execute(query).scalar_one_or_none()
    return bool(
        job is not None
        and isinstance(job.execution_context, dict)
        and job.execution_context
        == {
            "version": 1,
            "department_id": cloud_file.department_id,
            "credential_id": cloud_file.credential_id,
            "cloud_file_id": str(cloud_file.id),
            "provider": CloudProvider.CANVAS.value,
            "provider_file_id": cloud_file.provider_file_id,
            "provider_parent_id": cloud_file.provider_parent_id,
            "content_source": cloud_file.content_source,
            "content_slug": cloud_file.content_slug,
            "content_updated_at": (
                cloud_file.content_updated_at.isoformat()
                if isinstance(cloud_file.content_updated_at, datetime)
                else ""
            ),
            "scan_id": scan_id,
            "content_sha256": source_sha256,
            "snapshot_sha256": snapshot_sha256,
        }
    )


def lock_current_canvas_content_candidate(
    db: Session, cloud_file: CloudFile
) -> CloudFile | None:
    """Lock queue then content row and revalidate the exact candidate."""
    if not canvas_content_candidate_is_current(db, cloud_file, lock_job=True):
        return None
    locked = db.execute(
        select(CloudFile)
        .where(
            CloudFile.id == cloud_file.id,
            CloudFile.department_id == cloud_file.department_id,
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_file_id == cloud_file.provider_file_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if locked is None or not canvas_content_candidate_is_current(db, locked):
        return None
    return locked


def maintain_canvas_content_evidence(
    db: Session, *, now: datetime | None = None
) -> dict[str, int]:
    """Expire and delete bounded batches without touching live candidates."""
    current = now or datetime.now(timezone.utc)
    expire_ids = list(
        db.scalars(
            select(CanvasContentRemediationEvidence.id)
            .where(
                CanvasContentRemediationEvidence.lifecycle_state == "current",
                CanvasContentRemediationEvidence.expires_at <= current,
            )
            .order_by(CanvasContentRemediationEvidence.expires_at)
            .limit(EVIDENCE_MAINTENANCE_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    expired = 0
    if expire_ids:
        expired = int(
            db.execute(
                update(CanvasContentRemediationEvidence)
                .where(CanvasContentRemediationEvidence.id.in_(expire_ids))
                .values(lifecycle_state="expired")
            ).rowcount
            or 0
        )
    delete_ids = list(
        db.scalars(
            select(CanvasContentRemediationEvidence.id)
            .where(
                CanvasContentRemediationEvidence.lifecycle_state == "expired",
                CanvasContentRemediationEvidence.expires_at
                <= current - timedelta(days=7),
            )
            .order_by(CanvasContentRemediationEvidence.expires_at)
            .limit(EVIDENCE_MAINTENANCE_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    deleted = 0
    if delete_ids:
        deleted = int(
            db.execute(
                delete(CanvasContentRemediationEvidence).where(
                    CanvasContentRemediationEvidence.id.in_(delete_ids)
                )
            ).rowcount
            or 0
        )
    db.commit()
    return {"expired": expired, "deleted": deleted}


__all__ = [
    "CanvasContentProvenanceError",
    "canvas_candidate_fingerprint",
    "canvas_content_candidate_is_current",
    "canvas_content_sha256",
    "install_canvas_content_owner",
    "invalidate_canvas_content_candidate",
    "lock_current_canvas_content_candidate",
    "maintain_canvas_content_evidence",
    "publish_canvas_content_candidate",
]
