"""Durable execution boundary for locally submitted accessibility scans."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

from sqlalchemy.orm import Session

from src.db.models import CloudJobQueue, Scan, ScanResult, ScanStatus
from src.jobs.contracts import JobFailure, JobResult, JobSuccess
from src.utils.file_storage import UPLOAD_BASE_DIR

LOCAL_SCAN_KINDS = frozenset(
    {
        "local_pdf",
        "local_powerpoint",
        "local_word",
        "local_excel",
        "local_latex",
        "local_latex_pdf",
        "local_code",
        "local_multimedia",
        "local_web",
        "local_web_batch",
        "local_web_sitemap",
    }
)

_FILE_SCAN_KINDS = LOCAL_SCAN_KINDS - {
    "local_web",
    "local_web_batch",
    "local_web_sitemap",
}
_HEX = frozenset("0123456789abcdef")
_MODES = frozenset({"quick", "comprehensive", "deep"})
_WHISPER_MODELS = frozenset({"base", "small", "medium", "large"})


class LocalScanJobError(ValueError):
    """Bounded local-scan validation failure safe for durable job state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise LocalScanJobError("local_scan_options_invalid")
    return value


def _int(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LocalScanJobError("local_scan_options_invalid")
    return value


def _string(value: Any, *, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LocalScanJobError("local_scan_options_invalid")
    return value


def _exact(options: Any, fields: frozenset[str]) -> dict[str, Any]:
    if type(options) is not dict or frozenset(options) != fields:
        raise LocalScanJobError("local_scan_options_invalid")
    return dict(options)


def normalize_local_scan_options(
    scan_kind: str, options: dict[str, Any]
) -> dict[str, Any]:
    """Validate the closed, bounded option schema for one local scan kind."""
    if scan_kind not in LOCAL_SCAN_KINDS:
        raise LocalScanJobError("local_scan_kind_invalid")

    boolean_fields: dict[str, frozenset[str]] = {
        "local_pdf": frozenset({"generate_alt_text", "enhance_descriptions"}),
        "local_powerpoint": frozenset({"generate_alt_text", "validate_alt_text"}),
        "local_word": frozenset({"generate_alt_text", "validate_alt_text"}),
        "local_excel": frozenset({"generate_chart_descriptions", "generate_alt_text"}),
        "local_latex": frozenset({"use_ollama"}),
        "local_latex_pdf": frozenset({"use_ollama"}),
        "local_code": frozenset({"scan_images", "generate_fixes", "validate_alt_text"}),
    }
    if scan_kind in boolean_fields:
        normalized = _exact(options, boolean_fields[scan_kind])
        return {key: _bool(value) for key, value in normalized.items()}

    if scan_kind == "local_multimedia":
        fields = frozenset(
            {
                "generate_captions",
                "generate_audio_descriptions",
                "generate_spoken_descriptions",
                "detect_flashing",
                "generate_transcript",
                "whisper_model",
            }
        )
        normalized = _exact(options, fields)
        for key in fields - {"whisper_model"}:
            normalized[key] = _bool(normalized[key])
        model = normalized["whisper_model"]
        if model not in _WHISPER_MODELS:
            raise LocalScanJobError("local_scan_options_invalid")
        return normalized

    common_web = frozenset(
        {
            "mode",
            "scan_images",
            "scan_multimedia",
            "scan_math",
            "max_pages",
            "generate_code_fixes",
            "capture_screenshots",
        }
    )
    fields_by_kind = {
        "local_web": common_web | {"url", "validate_alt_text", "max_depth"},
        "local_web_batch": common_web | {"urls", "max_depth"},
        "local_web_sitemap": common_web | {"sitemap_url", "priority_patterns"},
    }
    normalized = _exact(options, frozenset(fields_by_kind[scan_kind]))
    mode = normalized["mode"]
    if mode not in _MODES:
        raise LocalScanJobError("local_scan_options_invalid")
    for key in (
        "scan_images",
        "scan_multimedia",
        "scan_math",
        "generate_code_fixes",
        "capture_screenshots",
    ):
        normalized[key] = _bool(normalized[key])
    normalized["max_pages"] = _int(normalized["max_pages"], 1, 1000)

    if scan_kind == "local_web":
        normalized["url"] = _string(normalized["url"])
        normalized["validate_alt_text"] = _bool(normalized["validate_alt_text"])
        normalized["max_depth"] = _int(normalized["max_depth"], 0, 10)
    elif scan_kind == "local_web_batch":
        urls = normalized["urls"]
        if type(urls) is not list or not 1 <= len(urls) <= 50:
            raise LocalScanJobError("local_scan_options_invalid")
        normalized["urls"] = [_string(url) for url in urls]
        normalized["max_depth"] = _int(normalized["max_depth"], 0, 10)
    else:
        normalized["sitemap_url"] = _string(normalized["sitemap_url"])
        patterns = normalized["priority_patterns"]
        if type(patterns) is not list or len(patterns) > 100:
            raise LocalScanJobError("local_scan_options_invalid")
        normalized["priority_patterns"] = [
            _string(pattern, maximum=256) for pattern in patterns
        ]
    return normalized


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def enqueue_local_scan_job(
    db: Session,
    *,
    scan: Scan,
    scan_kind: str,
    options: dict[str, Any],
    input_sha256: str | None = None,
    enqueue: Callable[..., CloudJobQueue] | None = None,
) -> CloudJobQueue:
    """Add a tenant-fenced local scan to the existing durable scan queue."""
    if not isinstance(scan.id, str) or not scan.id:
        raise LocalScanJobError("local_scan_scope_invalid")
    if not isinstance(scan.department_id, str) or not scan.department_id:
        raise LocalScanJobError("local_scan_scope_invalid")
    normalized = normalize_local_scan_options(scan_kind, options)
    payload: dict[str, Any] = {
        "scan_kind": scan_kind,
        "scan_id": scan.id,
        "options": normalized,
    }
    if scan_kind in _FILE_SCAN_KINDS:
        if not _valid_sha256(input_sha256):
            raise LocalScanJobError("local_scan_input_hash_invalid")
        payload["input_sha256"] = input_sha256
    elif input_sha256 is not None:
        raise LocalScanJobError("local_scan_input_hash_invalid")

    if enqueue is None:
        from src.services.job_enqueue_service import enqueue_cloud_job

        enqueue = enqueue_cloud_job
    return enqueue(
        db,
        department_id=scan.department_id,
        job_type="scan",
        payload=payload,
        dedupe_key=f"local-scan:{scan.id}",
    )


@contextmanager
def materialize_verified_scan_input(
    scan: Scan, expected_sha256: str
) -> Iterator[tuple[str, bytes]]:
    """Yield a disposable copy after verifying tenant scope and exact bytes."""
    if not _valid_sha256(expected_sha256):
        raise LocalScanJobError("local_scan_input_hash_invalid")
    if not isinstance(scan.storage_path, str) or not scan.storage_path:
        raise LocalScanJobError("local_scan_input_unavailable")
    source = Path(scan.storage_path)
    expected_dir = (UPLOAD_BASE_DIR / scan.department_id / scan.id).resolve()
    resolved = source.resolve()
    if not resolved.is_relative_to(expected_dir):
        raise LocalScanJobError("local_scan_input_scope_invalid")
    if not resolved.is_file():
        raise LocalScanJobError("local_scan_input_unavailable")
    content = resolved.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise LocalScanJobError("local_scan_input_hash_mismatch")

    suffix = resolved.suffix[:16]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        work_path = Path(temporary.name)
    try:
        shutil.copy2(resolved, work_path)
        yield str(work_path), content
    finally:
        work_path.unlink(missing_ok=True)


def _validate_web_targets(scan_kind: str, options: dict[str, Any]) -> None:
    from src.utils.security import validate_url_not_private

    try:
        if scan_kind == "local_web":
            validate_url_not_private(options["url"])
        elif scan_kind == "local_web_batch":
            for url in options["urls"]:
                validate_url_not_private(url)
        elif scan_kind == "local_web_sitemap":
            validate_url_not_private(options["sitemap_url"])
    except ValueError as exc:
        raise LocalScanJobError("local_scan_url_invalid") from exc


def _run_local_processor(
    scan_kind: str,
    scan: Scan,
    options: dict[str, Any],
    work_path: str | None,
    content: bytes | None,
) -> None:
    filename = scan.file_name or "uploaded-file"
    if scan_kind in {
        "local_pdf",
        "local_powerpoint",
        "local_word",
        "local_excel",
        "local_latex",
        "local_latex_pdf",
    }:
        from src.api.education import scan_routes

        if scan_kind == "local_pdf":
            scan_routes.process_pdf_background(
                work_path,
                content,
                filename,
                scan.id,
                options["generate_alt_text"],
                options["enhance_descriptions"],
                workspace_id=scan.department_id,
            )
        elif scan_kind == "local_powerpoint":
            scan_routes.process_pptx_background(
                work_path,
                content,
                filename,
                scan.id,
                options["generate_alt_text"],
                options["validate_alt_text"],
                scan.storage_path,
                scan.user_id,
                scan.department_id,
            )
        elif scan_kind == "local_word":
            scan_routes.process_docx_background(
                work_path,
                content,
                filename,
                scan.id,
                options["generate_alt_text"],
                options["validate_alt_text"],
                scan.storage_path,
                scan.user_id,
                scan.department_id,
            )
        elif scan_kind == "local_excel":
            scan_routes.process_xlsx_background(
                work_path,
                content,
                filename,
                scan.id,
                options["generate_chart_descriptions"],
                options["generate_alt_text"],
                scan.storage_path,
                scan.user_id,
                scan.department_id,
            )
        elif scan_kind == "local_latex":
            scan_routes.process_latex_background(
                work_path,
                content,
                filename,
                scan.id,
                options["use_ollama"],
                scan.user_id,
                scan.department_id,
            )
        else:
            scan_routes.process_latex_pdf_background(
                work_path,
                filename,
                scan.id,
                options["use_ollama"],
                scan.user_id,
                scan.department_id,
                workspace_id=scan.department_id,
            )
        return

    if scan_kind in {"local_web", "local_web_batch", "local_web_sitemap", "local_code"}:
        from src.api.education import web_scan_routes

        if scan_kind == "local_code":
            web_scan_routes.process_code_background(
                work_path,
                content,
                filename,
                scan.id,
                options["scan_images"],
                options["generate_fixes"],
                options["validate_alt_text"],
                scan.user_id,
                scan.department_id,
                workspace_id=scan.department_id,
            )
        elif scan_kind == "local_web":
            web_scan_routes.process_web_scan_background(
                scan.id,
                options["url"],
                options["mode"],
                options["scan_images"],
                options["scan_multimedia"],
                options["scan_math"],
                options["validate_alt_text"],
                options["max_depth"],
                options["max_pages"],
                options["generate_code_fixes"],
                options["capture_screenshots"],
                workspace_id=scan.department_id,
            )
        elif scan_kind == "local_web_batch":
            web_scan_routes.process_batch_web_scan_background(
                scan.id,
                options["urls"],
                options["mode"],
                options["scan_images"],
                options["scan_multimedia"],
                options["scan_math"],
                options["max_depth"],
                options["max_pages"],
                options["generate_code_fixes"],
                options["capture_screenshots"],
                workspace_id=scan.department_id,
            )
        else:
            web_scan_routes.process_sitemap_scan_background(
                scan.id,
                options["sitemap_url"],
                options["mode"],
                options["scan_images"],
                options["scan_multimedia"],
                options["scan_math"],
                options["max_pages"],
                options["generate_code_fixes"],
                options["capture_screenshots"],
                options["priority_patterns"],
                workspace_id=scan.department_id,
            )
        return

    from src.api.education import multimedia_routes

    multimedia_routes.process_multimedia_background(
        work_path,
        content,
        filename,
        scan.id,
        options["generate_captions"],
        options["generate_audio_descriptions"],
        options["generate_spoken_descriptions"],
        options["detect_flashing"],
        options["generate_transcript"],
        options["whisper_model"],
        scan.user_id,
        scan.department_id,
    )


async def _commit_scan_failure(
    db: Session,
    scan: Scan,
    assert_owned: Callable[[], Awaitable[None]] | None,
) -> None:
    """Keep the public polling record terminal without persisting internals."""
    if assert_owned is not None:
        await assert_owned()
    message = "Processing encountered an error. Please try again."
    scan.status = ScanStatus.FAILED
    scan.progress = 0
    scan.error_message = message
    scan.progress_message = message
    db.commit()


async def handle_local_scan_job(job: CloudJobQueue, db: Session) -> JobResult:
    """Execute one durable local scan with replay and tenant fences."""
    payload = job.payload if type(getattr(job, "payload", None)) is dict else {}
    scan_kind = payload.get("scan_kind")
    if scan_kind not in LOCAL_SCAN_KINDS:
        return JobFailure.deterministic("local_scan_kind_invalid")
    expected_keys = {"scan_kind", "scan_id", "options"}
    if scan_kind in _FILE_SCAN_KINDS:
        expected_keys.add("input_sha256")
    if set(payload) != expected_keys:
        return JobFailure.deterministic("local_scan_payload_invalid")
    if any(
        getattr(job, field, None) is not None
        for field in ("provider", "credential_id", "cloud_file_id", "provider_file_id")
    ):
        return JobFailure.deterministic("local_scan_scope_invalid")
    try:
        options = normalize_local_scan_options(scan_kind, payload["options"])
    except LocalScanJobError as exc:
        return JobFailure.deterministic(exc.code)

    scan_id = payload.get("scan_id")
    scan = db.get(Scan, scan_id) if isinstance(scan_id, str) else None
    if scan is None:
        return JobFailure.deterministic("scan_not_found")
    if scan.department_id != job.department_id:
        return JobFailure.deterministic("local_scan_scope_invalid")

    result = db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
    if scan.status == ScanStatus.COMPLETED and result is not None:
        return JobSuccess({"success": True, "scan_id": scan.id})
    if scan.status == ScanStatus.COMPLETED:
        return JobFailure.indeterminate("local_scan_result_unavailable")
    if scan.status == ScanStatus.FAILED:
        return JobFailure.deterministic("local_scan_failed")

    assert_owned = getattr(job, "_assert_owned", None)
    if assert_owned is not None:
        await assert_owned()
    try:
        if scan_kind in _FILE_SCAN_KINDS:
            with materialize_verified_scan_input(
                scan, payload["input_sha256"]
            ) as materialized:
                work_path, content = materialized
                await asyncio.to_thread(
                    _run_local_processor,
                    scan_kind,
                    scan,
                    options,
                    work_path,
                    content,
                )
        else:
            _validate_web_targets(scan_kind, options)
            await asyncio.to_thread(
                _run_local_processor, scan_kind, scan, options, None, None
            )
    except LocalScanJobError as exc:
        await _commit_scan_failure(db, scan, assert_owned)
        return JobFailure.deterministic(exc.code)
    except Exception:
        attempt_count = getattr(job, "attempt_count", 0)
        max_retries = getattr(job, "max_retries", 3)
        if (
            type(attempt_count) is int
            and type(max_retries) is int
            and attempt_count >= max_retries
        ):
            await _commit_scan_failure(db, scan, assert_owned)
        return JobFailure.retryable("local_scan_execution_failed")

    if assert_owned is not None:
        await assert_owned()
    db.expire_all()
    refreshed = db.get(Scan, scan.id)
    refreshed_result = (
        db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
    )
    if refreshed is not None and refreshed.status == ScanStatus.COMPLETED:
        if refreshed_result is None:
            return JobFailure.indeterminate("local_scan_result_unavailable")
        return JobSuccess({"success": True, "scan_id": scan.id})
    if refreshed is not None and refreshed.status == ScanStatus.FAILED:
        return JobFailure.deterministic("local_scan_failed")
    return JobFailure.indeterminate("local_scan_terminal_state_unknown")
