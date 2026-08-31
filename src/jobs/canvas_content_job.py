"""Durable remediation of immutable Canvas stored-content snapshots."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai.lms_remediation_client import LMSRemediationClient
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudProvider,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
)
from src.education.canvas_content_scanner import (
    _sanitize_html,
    _unwrap_html_fragment,
    _wrap_html_fragment,
)
from src.jobs.contracts import JobContext, JobFailure, JobSuccess, sanitize_json
from src.services.canvas_content_provenance import (
    canvas_content_sha256,
    install_canvas_content_owner,
    publish_canvas_content_candidate,
)
from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job
from src.utils.sanitization import sanitize_for_postgres

CANVAS_CONTENT_JOB_TYPE = "canvas_content"
SNAPSHOT_VERSION = 1
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_COMPRESSED_BYTES = 180 * 1024
MAX_QUEUE_PAYLOAD_BYTES = 262_144
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_ENCODED_SNAPSHOT_CHARS = ((MAX_COMPRESSED_BYTES + 2) // 3) * 4


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _encode_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw = _canonical_json(snapshot)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise JobEnqueueError("canvas_content_snapshot_too_large")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise JobEnqueueError("canvas_content_snapshot_too_large")
    digest = hashlib.sha256(raw).hexdigest()
    payload = {
        "version": SNAPSHOT_VERSION,
        "snapshot": base64.b64encode(compressed).decode("ascii"),
        "snapshot_sha256": digest,
    }
    if len(_canonical_json(payload)) > MAX_QUEUE_PAYLOAD_BYTES:
        raise JobEnqueueError("canvas_content_snapshot_too_large")
    return payload, digest


def _decode_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        raise ValueError("invalid_job_payload")
    encoded = payload.get("snapshot")
    expected = payload.get("snapshot_sha256")
    if (
        not isinstance(encoded, str)
        or len(encoded) > MAX_ENCODED_SNAPSHOT_CHARS
        or not isinstance(expected, str)
        or len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise ValueError("invalid_job_payload")
    try:
        compressed = base64.b64decode(encoded, validate=True)
        if len(compressed) > MAX_COMPRESSED_BYTES:
            raise ValueError("invalid_job_payload")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ValueError("invalid_job_payload") from exc
    if len(raw) > MAX_SNAPSHOT_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("invalid_job_payload")
    try:
        snapshot = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_job_payload") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_job_payload")
    return snapshot


def _scan_evidence(
    db: Session, cloud_file: CloudFile
) -> tuple[list[dict[str, Any]], float]:
    row = (
        db.query(Scan, ScanResult)
        .join(ScanResult, ScanResult.scan_id == Scan.id)
        .filter(
            Scan.id == cloud_file.last_scan_id,
            Scan.department_id == cloud_file.department_id,
            Scan.status == ScanStatus.COMPLETED,
            Scan.scan_type == ScanType.CANVAS_CONTENT,
        )
        .one_or_none()
    )
    if row is None:
        raise JobEnqueueError("invalid_canvas_content")
    _scan, result = row
    raw = result.issues if result is not None else []
    safe = sanitize_json(raw)
    issues = safe if isinstance(safe, list) else []
    score = result.compliance_score
    if (
        type(score) not in (int, float)
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 100.0
    ):
        raise JobEnqueueError("invalid_canvas_content")
    return issues, float(score)


def _content_updated_at(cloud_file: CloudFile) -> str:
    value = cloud_file.content_updated_at
    return value.isoformat() if hasattr(value, "isoformat") else ""


def _snapshot_material(
    db: Session, cloud_file: CloudFile, options: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    issues, compliance_score = _scan_evidence(db, cloud_file)
    source_sha256 = canvas_content_sha256(cloud_file.content_body)
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "department_id": cloud_file.department_id,
        "credential_id": cloud_file.credential_id,
        "cloud_file_id": str(cloud_file.id),
        "provider": CloudProvider.CANVAS.value,
        "provider_file_id": cloud_file.provider_file_id,
        "provider_parent_id": cloud_file.provider_parent_id,
        "content_source": cloud_file.content_source,
        "content_slug": cloud_file.content_slug,
        "content_updated_at": _content_updated_at(cloud_file),
        "scan_id": cloud_file.last_scan_id,
        "content_body": cloud_file.content_body,
        "content_sha256": source_sha256,
        "issues": issues,
        "issues_sha256": hashlib.sha256(_canonical_json(issues)).hexdigest(),
        "options": options,
        "options_sha256": hashlib.sha256(_canonical_json(options)).hexdigest(),
        "last_compliance_score": compliance_score,
    }
    payload, snapshot_sha256 = _encode_snapshot(snapshot)
    return snapshot, payload, f"canvas-content:{cloud_file.id}:{snapshot_sha256}"


def _dependency_matches_canvas_content(
    db: Session,
    *,
    dependency_id: str,
    source_scan_id: str,
    cloud_file: CloudFile,
    options: dict[str, Any],
) -> bool:
    if cloud_file.last_scan_id != source_scan_id:
        return False
    dependency = db.execute(
        select(CloudJobQueue).where(
            CloudJobQueue.id == dependency_id,
            CloudJobQueue.department_id == cloud_file.department_id,
            CloudJobQueue.job_type == "scan",
            CloudJobQueue.cloud_file_id == cloud_file.id,
            CloudJobQueue.provider == CloudProvider.CANVAS.value,
            CloudJobQueue.credential_id == cloud_file.credential_id,
            CloudJobQueue.provider_file_id == cloud_file.provider_file_id,
            CloudJobQueue.status.in_(
                (
                    CloudJobStatus.PROCESSING.value,
                    CloudJobStatus.COMPLETED.value,
                )
            ),
        )
    ).scalar_one_or_none()
    if dependency is None or not isinstance(dependency.payload, dict):
        return False
    expected = {
        "scan_kind": "canvas_content",
        "cloud_file_id": str(cloud_file.id),
        "credential_id": cloud_file.credential_id,
        "provider": CloudProvider.CANVAS.value,
        "provider_file_id": cloud_file.provider_file_id,
        "course_id": cloud_file.provider_parent_id,
        "content_source": cloud_file.content_source,
    }
    if any(dependency.payload.get(key) != value for key, value in expected.items()):
        return False
    dependency_options = sanitize_json(dependency.payload.get("scan_options") or {})
    return dependency_options == options


def enqueue_canvas_content_remediation(
    db: Session,
    *,
    cloud_file: CloudFile,
    options: dict[str, Any] | None = None,
    depends_on_job_id: str | None = None,
    dependency_scan_id: str | None = None,
) -> CloudJobQueue:
    """Atomically bind an active job to an immutable source/scan/options tuple."""
    if (
        cloud_file.provider != CloudProvider.CANVAS.value
        or cloud_file.content_source in (None, "file")
        or not isinstance(cloud_file.content_body, str)
        or not cloud_file.content_body
        or not isinstance(cloud_file.last_scan_id, str)
        or not cloud_file.last_scan_id
    ):
        raise JobEnqueueError("invalid_canvas_content")
    safe_options = sanitize_json(options or {})
    if not isinstance(safe_options, dict):
        raise JobEnqueueError("invalid_job_payload")
    locked = db.execute(
        select(CloudFile)
        .where(
            CloudFile.id == cloud_file.id,
            CloudFile.department_id == cloud_file.department_id,
            CloudFile.provider == CloudProvider.CANVAS.value,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        locked is None
        or locked.content_source in (None, "file")
        or not isinstance(locked.content_body, str)
        or not locked.content_body
        or not isinstance(locked.last_scan_id, str)
        or not locked.last_scan_id
        or not isinstance(locked.credential_id, str)
        or not locked.credential_id
        or not isinstance(locked.provider_file_id, str)
        or not locked.provider_file_id
        or not isinstance(locked.provider_parent_id, str)
        or not locked.provider_parent_id
        or not _content_updated_at(locked)
        or (
            locked.content_source == "page"
            and (not isinstance(locked.content_slug, str) or not locked.content_slug)
        )
    ):
        raise JobEnqueueError("invalid_canvas_content")
    snapshot, payload, dedupe_key = _snapshot_material(db, locked, safe_options)
    if depends_on_job_id is not None:
        if not isinstance(dependency_scan_id, str) or not dependency_scan_id:
            raise JobEnqueueError("dependency_scope_mismatch")
        if not _dependency_matches_canvas_content(
            db,
            dependency_id=depends_on_job_id,
            source_scan_id=dependency_scan_id,
            cloud_file=locked,
            options=safe_options,
        ):
            raise JobEnqueueError("dependency_scope_mismatch")
    elif dependency_scan_id is not None:
        raise JobEnqueueError("dependency_scope_mismatch")
    job = enqueue_cloud_job(
        db,
        department_id=locked.department_id,
        job_type=CANVAS_CONTENT_JOB_TYPE,
        payload=payload,
        dedupe_key=dedupe_key,
        provider=CloudProvider.CANVAS.value,
        credential_id=locked.credential_id,
        cloud_file_id=str(locked.id),
        provider_file_id=locked.provider_file_id,
        depends_on_job_id=depends_on_job_id,
        max_retries=0,
        execution_context={
            "version": SNAPSHOT_VERSION,
            "department_id": locked.department_id,
            "credential_id": locked.credential_id,
            "cloud_file_id": str(locked.id),
            "provider": CloudProvider.CANVAS.value,
            "provider_file_id": locked.provider_file_id,
            "provider_parent_id": locked.provider_parent_id,
            "content_source": locked.content_source,
            "content_slug": locked.content_slug,
            "content_updated_at": _content_updated_at(locked),
            "scan_id": locked.last_scan_id,
            "content_sha256": snapshot["content_sha256"],
            "snapshot_sha256": payload["snapshot_sha256"],
        },
    )
    install_canvas_content_owner(
        db,
        locked,
        job_id=str(job.id),
        source_sha256=snapshot["content_sha256"],
        scan_id=str(locked.last_scan_id),
        status=str(job.status),
    )
    return job


def _snapshot_is_valid(snapshot: dict[str, Any]) -> bool:
    required = {
        "version": int,
        "department_id": str,
        "credential_id": str,
        "cloud_file_id": str,
        "provider": str,
        "provider_file_id": str,
        "provider_parent_id": str,
        "content_source": str,
        "content_updated_at": str,
        "scan_id": str,
        "content_body": str,
        "content_sha256": str,
        "issues": list,
        "issues_sha256": str,
        "options": dict,
        "options_sha256": str,
    }
    content_slug = snapshot.get("content_slug")
    score = snapshot.get("last_compliance_score")
    try:
        issues_sha256 = hashlib.sha256(
            _canonical_json(snapshot.get("issues"))
        ).hexdigest()
        options_sha256 = hashlib.sha256(
            _canonical_json(snapshot.get("options"))
        ).hexdigest()
    except (TypeError, ValueError, OverflowError):
        return False
    return not any(
        type(snapshot.get(key)) is not kind for key, kind in required.items()
    ) and (
        snapshot["version"] == SNAPSHOT_VERSION
        and snapshot["provider"] == CloudProvider.CANVAS.value
        and all(
            snapshot[key]
            for key in (
                "department_id",
                "credential_id",
                "cloud_file_id",
                "provider_file_id",
                "provider_parent_id",
                "content_source",
                "content_updated_at",
                "scan_id",
            )
        )
        and snapshot["content_source"]
        in {"page", "assignment", "announcement", "quiz", "discussion"}
        and (content_slug is None or isinstance(content_slug, str))
        and (
            snapshot["content_source"] != "page"
            or isinstance(content_slug, str)
            and bool(content_slug)
        )
        and canvas_content_sha256(snapshot["content_body"])
        == snapshot["content_sha256"]
        and issues_sha256 == snapshot["issues_sha256"]
        and options_sha256 == snapshot["options_sha256"]
        and type(score) in (int, float)
        and math.isfinite(float(score))
        and 0.0 <= float(score) <= 100.0
        and all(
            len(snapshot[key]) == 64
            and all(character in "0123456789abcdef" for character in snapshot[key])
            for key in ("content_sha256", "issues_sha256", "options_sha256")
        )
    )


def _authority_is_current(
    job: CloudJobQueue,
    cloud_file: CloudFile,
    context: JobContext,
    snapshot: dict[str, Any],
) -> bool:
    metadata = (
        cloud_file.provider_metadata
        if isinstance(cloud_file.provider_metadata, dict)
        else {}
    )
    owner = metadata.get("canvas_content_remediation")
    return bool(
        context.job_type == CANVAS_CONTENT_JOB_TYPE
        and job.status == CloudJobStatus.PROCESSING.value
        and job.claim_token == context.claim_token
        and job.worker_id == context.worker_id
        and job.last_error_code != "scan_cancel_requested"
        and job.job_type == CANVAS_CONTENT_JOB_TYPE
        and job.provider == CloudProvider.CANVAS.value
        and job.credential_id == snapshot["credential_id"]
        and job.max_retries == 0
        and job.department_id == snapshot["department_id"]
        and job.cloud_file_id == snapshot["cloud_file_id"]
        and job.provider_file_id == snapshot["provider_file_id"]
        and job.payload == dict(context.payload)
        and job.execution_context
        == {
            "version": SNAPSHOT_VERSION,
            "department_id": snapshot["department_id"],
            "credential_id": snapshot["credential_id"],
            "cloud_file_id": snapshot["cloud_file_id"],
            "provider": CloudProvider.CANVAS.value,
            "provider_file_id": snapshot["provider_file_id"],
            "provider_parent_id": snapshot["provider_parent_id"],
            "content_source": snapshot["content_source"],
            "content_slug": snapshot["content_slug"],
            "content_updated_at": snapshot["content_updated_at"],
            "scan_id": snapshot["scan_id"],
            "content_sha256": snapshot["content_sha256"],
            "snapshot_sha256": context.payload.get("snapshot_sha256"),
        }
        and cloud_file.department_id == snapshot["department_id"]
        and cloud_file.credential_id == snapshot["credential_id"]
        and cloud_file.provider_file_id == snapshot["provider_file_id"]
        and cloud_file.provider_parent_id == snapshot["provider_parent_id"]
        and cloud_file.content_source == snapshot["content_source"]
        and cloud_file.content_slug == snapshot["content_slug"]
        and _content_updated_at(cloud_file) == snapshot["content_updated_at"]
        and cloud_file.last_scan_id == snapshot["scan_id"]
        and isinstance(cloud_file.content_body, str)
        and canvas_content_sha256(cloud_file.content_body) == snapshot["content_sha256"]
        and isinstance(owner, dict)
        and owner.get("job_id") == context.job_id
    )


def _lock_authority(
    db: Session, context: JobContext, snapshot: dict[str, Any]
) -> tuple[CloudJobQueue, CloudFile] | None:
    job = db.execute(
        select(CloudJobQueue)
        .where(CloudJobQueue.id == context.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    cloud_file = db.execute(
        select(CloudFile)
        .where(
            CloudFile.id == snapshot["cloud_file_id"],
            CloudFile.department_id == snapshot["department_id"],
            CloudFile.provider == CloudProvider.CANVAS.value,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        job is None
        or cloud_file is None
        or not _authority_is_current(job, cloud_file, context, snapshot)
    ):
        return None
    return job, cloud_file


@dataclass(frozen=True)
class _Candidate:
    body: str
    fixed_count: int
    manual_count: int
    failed_count: int
    score: float | None


def _remediate_snapshot(snapshot: dict[str, Any], job_id: str) -> _Candidate:
    from src.api.education.remediation_routes import (
        _normalize_issues_for_remediation,
    )
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.html_remediator import HtmlRemediator

    options = snapshot["options"]
    use_ai = options.get("use_ai") is True
    ai_client = None
    if use_ai:
        ai_client = LMSRemediationClient.bind_if_allowed(
            department_id=snapshot["department_id"],
            purpose="remediation",
            actor_id=(
                options.get("actor_id")
                if isinstance(options.get("actor_id"), str)
                else None
            ),
            job_id=job_id,
            scan_id=snapshot["scan_id"],
            cloud_file_id=snapshot["cloud_file_id"],
        )
    issues = _normalize_issues_for_remediation(snapshot["issues"])
    with tempfile.TemporaryDirectory(prefix="aelira-canvas-content-") as temp_dir:
        root = Path(temp_dir).resolve()
        source = root / "source.html"
        source.write_text(
            _wrap_html_fragment(snapshot["content_body"]), encoding="utf-8"
        )
        remediator = HtmlRemediator(
            str(source),
            issues,
            config=RemediationConfig(
                use_ai=ai_client is not None,
                allow_legacy_nested_ai=False,
                create_backup=False,
                output_directory=str(root),
            ),
            ai_client=ai_client,
        )
        result = remediator.remediate()
        if result.success is not True:
            raise ValueError("canvas_content_remediation_failed")
        output = Path(result.output_file or source).resolve()
        if not output.is_relative_to(root) or output.stat().st_size > MAX_OUTPUT_BYTES:
            raise ValueError("canvas_content_invalid_output")
        raw = output.read_bytes()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise ValueError("canvas_content_invalid_output")
        try:
            document = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("canvas_content_invalid_output") from exc
        body = sanitize_for_postgres(_sanitize_html(_unwrap_html_fragment(document)))
        if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("canvas_content_invalid_output")
    total = len(issues)
    raw_counts = (
        result.fixed_count,
        result.manual_count,
        getattr(result, "failed_count", 0),
    )
    if any(type(value) is not int for value in raw_counts):
        raise ValueError("canvas_content_invalid_output")
    fixed, manual, failed = raw_counts
    if any(value < 0 or value > total for value in (fixed, manual, failed)):
        raise ValueError("canvas_content_invalid_output")
    accounted = fixed + manual + failed
    if accounted < total:
        manual += total - accounted
    elif accounted > total:
        raise ValueError("canvas_content_invalid_output")
    score = getattr(result, "remediated_compliance_score", None)
    if score is None:
        original = snapshot.get("last_compliance_score")
        score = (
            min(
                100.0,
                round(float(original) + (100 - float(original)) * fixed / total, 1),
            )
            if isinstance(original, (int, float)) and total
            else (float(original) if isinstance(original, (int, float)) else None)
        )
    if (
        type(score) not in (int, float)
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 100.0
    ):
        raise ValueError("canvas_content_invalid_output")
    score = float(score)
    return _Candidate(body, fixed, manual, failed, score)


async def handle_canvas_content_job(
    context: JobContext, db: Session, _token_manager: Any
) -> JobSuccess | JobFailure:
    """Compute outside durable state, then publish only under the owned locks."""
    try:
        snapshot = _decode_snapshot(dict(context.payload))
    except ValueError:
        return JobFailure.deterministic("invalid_job_payload")
    if not _snapshot_is_valid(snapshot):
        return JobFailure.deterministic("invalid_job_payload")
    db.rollback()
    authority = _lock_authority(db, context, snapshot)
    if authority is None:
        db.rollback()
        return JobFailure.deterministic("canvas_content_stale_snapshot")
    db.rollback()
    try:
        candidate = await asyncio.to_thread(
            _remediate_snapshot, snapshot, context.job_id
        )
    except ValueError as exc:
        code = (
            str(exc)
            if str(exc)
            in {"canvas_content_remediation_failed", "canvas_content_invalid_output"}
            else "canvas_content_remediation_failed"
        )
        return JobFailure.deterministic(code)
    await context.assert_owned()
    db.rollback()
    authority = _lock_authority(db, context, snapshot)
    if authority is None:
        db.rollback()
        return JobFailure.deterministic("canvas_content_stale_snapshot")
    _job, cloud_file = authority
    cloud_file.remediated_body = candidate.body
    cloud_file.writeback_status = "pending_review"
    cloud_file.has_remediated_version = True
    cloud_file.remediation_origin = "manual"
    cloud_file.remediated_compliance_score = candidate.score
    cloud_file.remediated_issues_fixed = candidate.fixed_count
    cloud_file.remediated_issues_remaining = (
        candidate.manual_count + candidate.failed_count
    )
    publish_canvas_content_candidate(
        cloud_file,
        credential_id=snapshot["credential_id"],
        provider_file_id=snapshot["provider_file_id"],
        provider_parent_id=snapshot["provider_parent_id"],
        content_source=snapshot["content_source"],
        content_slug=snapshot["content_slug"],
        content_updated_at=snapshot["content_updated_at"],
        source_sha256=snapshot["content_sha256"],
        scan_id=snapshot["scan_id"],
        producer_job_id=context.job_id,
        snapshot_sha256=context.payload["snapshot_sha256"],
        candidate_sha256=canvas_content_sha256(candidate.body),
    )
    completion = {
        "success": True,
        "cloud_file_id": str(cloud_file.id),
        "scan_id": snapshot["scan_id"],
        "fixed_count": candidate.fixed_count,
        "manual_count": candidate.manual_count,
        "failed_count": candidate.failed_count,
        "remediated_compliance_score": candidate.score,
        "verified": False,
        "issues_remaining": candidate.manual_count + candidate.failed_count,
        "issues_introduced": 0,
    }
    db.commit()
    return JobSuccess(completion)


__all__ = [
    "CANVAS_CONTENT_JOB_TYPE",
    "MAX_COMPRESSED_BYTES",
    "MAX_ENCODED_SNAPSHOT_CHARS",
    "MAX_OUTPUT_BYTES",
    "MAX_QUEUE_PAYLOAD_BYTES",
    "MAX_SNAPSHOT_BYTES",
    "_decode_snapshot",
    "_encode_snapshot",
    "enqueue_canvas_content_remediation",
    "handle_canvas_content_job",
]
