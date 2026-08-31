"""Authenticated queued PDF evidence-report contract for CLI scans."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.database import get_db_dependency
from ...db.models import CloudJobQueue, CloudJobStatus
from ...jobs.report_job import (
    MAX_REPORT_ISSUES,
    MAX_REPORT_PAYLOAD_BYTES,
    REPORT_CONTENT_TYPE,
    read_report_artifact,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_ERRORS = {
    "invalid_job_scope",
    "job_execution_timeout",
    "job_handler_exception",
    "report_generation_failed",
    "report_payload_invalid",
    "report_storage_unavailable",
}


class ReportRequest(BaseModel):
    report_kind: Literal["scan", "analyze"]
    target: str = Field(min_length=1, max_length=2_048)
    compliance_score: float = Field(ge=0, le=100)
    issues: list[dict[str, str]] = Field(max_length=MAX_REPORT_ISSUES)
    created_at: str | None = Field(default=None, max_length=64)
    total_issues: int | None = Field(default=None, ge=0, le=1_000_000)
    severity_totals: dict[str, int] | None = None

    @field_validator("issues")
    @classmethod
    def validate_issues(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        limits = {
            "description": 2_000,
            "element": 1_000,
            "fix": 4_000,
            "impact": 32,
            "rule": 128,
        }
        for issue in value:
            if set(issue) - set(limits):
                raise ValueError("unsupported report issue field")
            if any(len(item) > limits[key] for key, item in issue.items()):
                raise ValueError("report issue field too long")
        return value

    @field_validator("severity_totals")
    @classmethod
    def validate_severity_totals(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        if value is None:
            return None
        if set(value) != {"critical", "serious", "moderate", "minor"}:
            raise ValueError("invalid severity totals")
        if any(type(count) is not int or count < 0 for count in value.values()):
            raise ValueError("invalid severity total")
        return value


def _get_report_job(db: Session, *, job_id: str, department_id: str) -> CloudJobQueue:
    job = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.id == job_id,
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.job_type == "report",
        )
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Report job not found")
    return job


def _artifact_shape(job: CloudJobQueue) -> dict[str, Any]:
    result = job.result_data if isinstance(job.result_data, dict) else {}
    required = {
        "artifact_id": str,
        "content_type": str,
        "filename": str,
        "sha256": str,
        "size_bytes": int,
        "storage_key": str,
    }
    if any(
        not isinstance(result.get(key), expected) for key, expected in required.items()
    ):
        raise HTTPException(status_code=503, detail="Report artifact unavailable")
    if (
        result["artifact_id"] != str(job.id)
        or result["content_type"] != REPORT_CONTENT_TYPE
        or not _SHA256.fullmatch(result["sha256"])
        or result["size_bytes"] < 5
    ):
        raise HTTPException(status_code=503, detail="Report artifact unavailable")
    return result


@router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
async def create_report(
    request: ReportRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    payload = request.model_dump(exclude_none=True)
    total_issues = (
        request.total_issues
        if request.total_issues is not None
        else len(request.issues)
    )
    if total_issues < len(request.issues):
        raise HTTPException(status_code=422, detail="Issue total is too small")
    if request.severity_totals is not None:
        if sum(request.severity_totals.values()) != total_issues:
            raise HTTPException(status_code=422, detail="Severity totals do not match")
    if (
        len(json.dumps(payload, separators=(",", ":")).encode())
        > MAX_REPORT_PAYLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Report evidence is too large")
    job_id = str(uuid.uuid4())
    job = CloudJobQueue(
        id=job_id,
        department_id=principal.department_id,
        job_type="report",
        payload=payload,
        status=CloudJobStatus.PENDING.value,
        priority=5,
        max_retries=2,
    )
    try:
        db.add(job)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Report job enqueue failed", extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(status_code=503, detail="Unable to queue report") from None
    return {
        "job_id": job_id,
        "status": CloudJobStatus.PENDING.value,
        "status_url": f"/education/reports/{job_id}",
    }


@router.get("/reports/{job_id}")
async def get_report_status(
    job_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    job = _get_report_job(db, job_id=job_id, department_id=principal.department_id)
    response: dict[str, Any] = {
        "job_id": str(job.id),
        "progress": int(job.progress or 0),
        "status": str(job.status),
    }
    if job.status == CloudJobStatus.COMPLETED.value:
        artifact = _artifact_shape(job)
        response["artifact"] = {
            "artifact_id": artifact["artifact_id"],
            "content_type": artifact["content_type"],
            "download_url": f"/education/reports/{job.id}/download",
            "filename": artifact["filename"],
            "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"],
        }
    elif job.status == CloudJobStatus.FAILED.value:
        response["error_code"] = (
            job.last_error_code
            if job.last_error_code in _PUBLIC_ERRORS
            else "report_generation_failed"
        )
    return response


@router.get("/reports/{job_id}/download")
async def download_report(
    job_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    job = _get_report_job(db, job_id=job_id, department_id=principal.department_id)
    if job.status != CloudJobStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="Report is not ready")
    artifact = _artifact_shape(job)
    try:
        pdf = read_report_artifact(
            artifact["storage_key"],
            expected_size=artifact["size_bytes"],
            expected_sha256=artifact["sha256"],
        )
    except (OSError, ValueError):
        logger.warning(
            "Report artifact integrity check failed", extra={"job_id": str(job.id)}
        )
        raise HTTPException(
            status_code=503, detail="Report artifact unavailable"
        ) from None
    return Response(
        content=pdf,
        media_type=REPORT_CONTENT_TYPE,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{artifact["filename"]}"',
            "X-Artifact-ID": artifact["artifact_id"],
            "X-Checksum-SHA256": artifact["sha256"],
        },
    )
