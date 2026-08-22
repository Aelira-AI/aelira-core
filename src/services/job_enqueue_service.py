"""Tenant-fenced enqueue boundary for durable cloud jobs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
)


class JobEnqueueError(ValueError):
    """A bounded enqueue validation failure safe to map at an API boundary."""


def _snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Detach queue input from caller-owned mutable objects."""
    from src.jobs.contracts import validate_json_object

    validated = validate_json_object(value)
    return json.loads(json.dumps(validated, separators=(",", ":")))


def _active_dedupe(
    db: Session, *, department_id: str, job_type: str, dedupe_key: str
) -> CloudJobQueue | None:
    return (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.job_type == job_type,
            CloudJobQueue.dedupe_key == dedupe_key,
            CloudJobQueue.status.in_(
                (CloudJobStatus.PENDING.value, CloudJobStatus.PROCESSING.value)
            ),
        )
        .first()
    )


def enqueue_cloud_job(
    db: Session,
    *,
    department_id: str,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
    provider: str | None = None,
    credential_id: str | None = None,
    cloud_file_id: str | None = None,
    provider_file_id: str | None = None,
    depends_on_job_id: str | None = None,
    priority: int = 5,
    max_retries: int = 3,
    scheduled_for: datetime | None = None,
    execution_context: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> CloudJobQueue:
    """Validate authority and add one immutable-input durable job.

    The caller owns the surrounding transaction. A nested savepoint confines a
    partial-unique dedupe race so the winner can be returned without aborting
    unrelated route work in the outer transaction.
    """
    if type(payload) is not dict:
        raise JobEnqueueError("payload_object_required")
    from src.jobs.registry import EXECUTABLE_JOB_TYPES

    if not isinstance(job_type, str) or job_type not in EXECUTABLE_JOB_TYPES:
        raise JobEnqueueError("job_type_not_registered")
    if not isinstance(department_id, str) or not department_id:
        raise JobEnqueueError("department_required")
    if not isinstance(dedupe_key, str) or not dedupe_key.strip():
        raise JobEnqueueError("dedupe_key_required")
    if len(dedupe_key) > 255:
        raise JobEnqueueError("dedupe_key_too_long")
    if type(priority) is not int or not 0 <= priority <= 100:
        raise JobEnqueueError("priority_invalid")
    if type(max_retries) is not int or not 0 <= max_retries <= 100:
        raise JobEnqueueError("max_retries_invalid")

    payload_snapshot = _snapshot(payload)
    context_snapshot = _snapshot(execution_context or {})

    if depends_on_job_id is not None:
        dependency = db.get(CloudJobQueue, depends_on_job_id)
        if dependency is None:
            raise JobEnqueueError("dependency_not_found")
        if dependency.department_id != department_id:
            raise JobEnqueueError("dependency_tenant_mismatch")

    if credential_id is not None:
        credential = db.get(CloudOAuthCredentials, credential_id)
        if credential is None:
            raise JobEnqueueError("credential_not_found")
        if credential.department_id != department_id:
            raise JobEnqueueError("credential_tenant_mismatch")
        if provider is None or credential.provider != provider:
            raise JobEnqueueError("credential_provider_mismatch")
        if credential.is_active is not True:
            raise JobEnqueueError("credential_inactive")

    if cloud_file_id is not None:
        cloud_file = db.get(CloudFile, cloud_file_id)
        if cloud_file is None:
            raise JobEnqueueError("cloud_file_not_found")
        if cloud_file.department_id != department_id:
            raise JobEnqueueError("cloud_file_tenant_mismatch")
        if provider is not None and cloud_file.provider != provider:
            raise JobEnqueueError("cloud_file_provider_mismatch")
        if credential_id is not None and cloud_file.credential_id != credential_id:
            raise JobEnqueueError("cloud_file_credential_mismatch")
        if (
            provider_file_id is not None
            and cloud_file.provider_file_id != provider_file_id
        ):
            raise JobEnqueueError("cloud_file_remote_id_mismatch")

    existing = _active_dedupe(
        db,
        department_id=department_id,
        job_type=job_type,
        dedupe_key=dedupe_key,
    )
    if existing is not None:
        return existing

    job_values: dict[str, Any] = {
        "id": job_id or str(uuid.uuid4()),
        "department_id": department_id,
        "job_type": job_type,
        "payload": payload_snapshot,
        "dedupe_key": dedupe_key,
        "provider": provider,
        "credential_id": credential_id,
        "cloud_file_id": cloud_file_id,
        "provider_file_id": provider_file_id,
        "depends_on_job_id": depends_on_job_id,
        "priority": priority,
        "max_retries": max_retries,
        "execution_context": context_snapshot,
        "status": CloudJobStatus.PENDING.value,
    }
    if scheduled_for is not None:
        job_values["scheduled_for"] = scheduled_for
    job = CloudJobQueue(**job_values)
    savepoint = db.begin_nested()
    try:
        db.add(job)
        db.flush()
        savepoint.commit()
        return job
    except IntegrityError as exc:
        savepoint.rollback()
        existing = _active_dedupe(
            db,
            department_id=department_id,
            job_type=job_type,
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return existing
        raise JobEnqueueError("enqueue_integrity_error") from exc
