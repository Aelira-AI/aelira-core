"""Durable generation and integrity checks for CLI evidence-report artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid
from typing import Any, Mapping

from src.config.settings import get_settings
from src.db.models import CloudJobQueue
from src.jobs.contracts import (
    JobContext,
    JobFailure,
    JobResult,
    JobSuccess,
    LostJobOwnership,
)

REPORT_CONTENT_TYPE = "application/pdf"
MAX_REPORT_PAYLOAD_BYTES = 240_000
MAX_REPORT_ISSUES = 50
_ISSUE_FIELDS = {
    "description": 2_000,
    "element": 1_000,
    "fix": 4_000,
    "impact": 32,
    "rule": 128,
}


def _safe_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return html.escape(value[:limit], quote=False)


def _normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        encoded = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_REPORT_PAYLOAD_BYTES:
        return None
    report_kind = payload.get("report_kind")
    target = payload.get("target")
    score = payload.get("compliance_score")
    issues = payload.get("issues")
    total_issues = payload.get(
        "total_issues", len(issues) if isinstance(issues, list) else 0
    )
    severity_totals = payload.get("severity_totals")
    if report_kind not in {"scan", "analyze"}:
        return None
    if not isinstance(target, str) or not target or len(target) > 2_048:
        return None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    if not 0 <= float(score) <= 100:
        return None
    if not isinstance(issues, list) or len(issues) > MAX_REPORT_ISSUES:
        return None
    if (
        type(total_issues) is not int
        or total_issues < len(issues)
        or total_issues > 1_000_000
    ):
        return None
    if severity_totals is not None:
        if not isinstance(severity_totals, Mapping) or set(severity_totals) != {
            "critical",
            "serious",
            "moderate",
            "minor",
        }:
            return None
        if any(
            type(count) is not int or count < 0 for count in severity_totals.values()
        ):
            return None
        if sum(severity_totals.values()) != total_issues:
            return None

    normalized_issues: list[dict[str, str]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            return None
        normalized: dict[str, str] = {}
        for key, limit in _ISSUE_FIELDS.items():
            if key in issue:
                if not isinstance(issue[key], str) or len(issue[key]) > limit:
                    return None
                normalized[key] = _safe_text(issue[key], limit=limit)
        normalized_issues.append(normalized)

    created_at = payload.get("created_at")
    if created_at is not None and (
        not isinstance(created_at, str) or len(created_at) > 64
    ):
        return None
    result = {
        "url": _safe_text(target, limit=2_048),
        "compliance_score": float(score),
        "issues": normalized_issues,
        "pages_scanned": 1,
        "total_issues": total_issues,
    }
    if severity_totals is not None:
        result["severity_totals"] = dict(severity_totals)
    if created_at:
        result["created_at"] = created_at
    return result


def _storage_key(*, department_id: str, job_id: str) -> str:
    parsed = str(uuid.UUID(job_id))
    department_key = hashlib.sha256(department_id.encode("utf-8")).hexdigest()[:32]
    return f"{department_key}/{parsed}.pdf"


def _artifact_path(root: Path, storage_key: str) -> Path:
    key = PurePosixPath(storage_key)
    if key.is_absolute() or ".." in key.parts or len(key.parts) != 2:
        raise ValueError("invalid_report_storage_key")
    return root.joinpath(*key.parts)


def _ensure_directory(path: Path) -> None:
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    current = path
    while True:
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("report_artifact_directory_invalid")
        if current.parent == current:
            break
        current = current.parent


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError("report_artifact_invalid")
    if info.st_size < 5 or info.st_size > max_bytes:
        raise OSError("report_artifact_size_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        data = b""
        while len(data) <= max_bytes:
            chunk = os.read(descriptor, min(1_048_576, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(descriptor)
    if len(data) != info.st_size or not data.startswith(b"%PDF-"):
        raise OSError("report_artifact_integrity_invalid")
    return data


def _publish_pdf(path: Path, pdf: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o640)
    try:
        offset = 0
        while offset < len(pdf):
            written = os.write(descriptor, pdf[offset:])
            if written <= 0:
                raise OSError("report_artifact_write_failed")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_report_artifact(
    storage_key: str, *, expected_size: int, expected_sha256: str
) -> bytes:
    settings = get_settings()
    path = _artifact_path(Path(settings.report_artifact_dir), storage_key)
    data = _read_regular_file(path, max_bytes=settings.report_artifact_max_bytes)
    if (
        len(data) != expected_size
        or not hashlib.sha256(data).hexdigest() == expected_sha256
    ):
        raise OSError("report_artifact_identity_mismatch")
    return data


async def handle_report_job(
    context: JobContext, session: Any, _token_manager: Any
) -> JobResult:
    """Generate one immutable PDF artifact from bounded client scan evidence."""
    payload = _normalize_payload(context.payload)
    if payload is None:
        return JobFailure.deterministic("report_payload_invalid")
    job = session.get(CloudJobQueue, context.job_id)
    if job is None or job.job_type != "report" or not job.department_id:
        return JobFailure.deterministic("invalid_job_scope")

    settings = get_settings()
    try:
        storage_key = _storage_key(
            department_id=str(job.department_id), job_id=context.job_id
        )
        root = Path(settings.report_artifact_dir)
        path = _artifact_path(root, storage_key)
        _ensure_directory(path.parent)
        if path.exists():
            pdf = _read_regular_file(path, max_bytes=settings.report_artifact_max_bytes)
        else:
            from src.education.pdf_report_generator import (
                AccessibilityPDFReportGenerator,
            )

            pdf = await asyncio.to_thread(
                AccessibilityPDFReportGenerator.generate_website_report, payload
            )
            if (
                len(pdf) < 5
                or len(pdf) > settings.report_artifact_max_bytes
                or not pdf.startswith(b"%PDF-")
            ):
                return JobFailure.deterministic("report_generation_failed")
            await context.assert_owned()
            _publish_pdf(path, pdf)
        sha256 = hashlib.sha256(pdf).hexdigest()
        return JobSuccess(
            {
                "artifact_id": context.job_id,
                "content_type": REPORT_CONTENT_TYPE,
                "download_available": True,
                "filename": f"aelira-accessibility-report-{context.job_id}.pdf",
                "sha256": sha256,
                "size_bytes": len(pdf),
                "status": "completed",
                "storage_key": storage_key,
                "success": True,
            }
        )
    except LostJobOwnership:
        raise
    except (OSError, ValueError):
        return JobFailure.retryable("report_storage_unavailable")
    except Exception:
        return JobFailure.deterministic("report_generation_failed")
