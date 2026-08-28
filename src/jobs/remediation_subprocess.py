"""Killable document remediation process with descriptor-bound output claims."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import sys
from types import SimpleNamespace
from typing import Any

from src.education.remediation.output_claim import DescriptorBoundOutputClaim

_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ISSUES = 10_000


class RemediationSubprocessError(RuntimeError):
    """A child failure whose message is safe to consume as a stable code."""


class RemediationSubprocessTimeout(RemediationSubprocessError):
    """The child exceeded its hard wall deadline and was reaped."""


@dataclass
class SubprocessRemediationResult:
    """Remediation result reconstructed from bounded child output."""

    values: dict[str, Any]
    output_claim: DescriptorBoundOutputClaim | None = None

    def __post_init__(self) -> None:
        visual_contracts = []
        for key, value in self.values.items():
            if key in {"fixed_issues", "manual_issues"} and isinstance(value, list):
                records = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    record = dict(item)
                    contract = record.get("visual_semantic_contract")
                    if contract is not None:
                        if key != "fixed_issues":
                            raise RemediationSubprocessError("remediation_failed")
                        try:
                            from src.education.remediation.base import FixedIssue
                            from src.education.visual_semantic_contract import (
                                VisualSemanticContractAdapter,
                            )

                            validated_contract = (
                                VisualSemanticContractAdapter.validate_python(contract)
                            )
                            record["visual_semantic_contract"] = validated_contract
                            FixedIssue.model_validate(record)
                        except (TypeError, ValueError) as exc:
                            raise RemediationSubprocessError(
                                "remediation_failed"
                            ) from exc
                        visual_contracts.append(validated_contract)
                    records.append(SimpleNamespace(**record))
                value = records
            setattr(self, key, value)
        if visual_contracts and (
            not isinstance(self.output_claim, DescriptorBoundOutputClaim)
            or self.output_claim.closed
        ):
            raise RemediationSubprocessError("remediation_failed")
        if visual_contracts:
            assert self.output_claim is not None
            claimed_sha256 = self.output_claim.sha256
            for contract in visual_contracts:
                saved_evidence = [
                    evidence
                    for evidence in contract.verification_evidence
                    if evidence.evidence_kind
                    in {
                        "standalone_formula_saved_v1",
                        "scanned_region_formula_saved_v1",
                    }
                ]
                if (
                    len(saved_evidence) != 1
                    or saved_evidence[0].saved_file_sha256 != claimed_sha256
                ):
                    raise RemediationSubprocessError("remediation_failed")
        self.output_file = (
            self.output_claim.display_path if self.output_claim is not None else None
        )
        self.improvement = self.values.get("compliance_improvement")

    def has_output_claim(self) -> bool:
        return self.output_claim is not None and not self.output_claim.closed

    def output_claim_metadata(self) -> dict[str, Any]:
        if not self.has_output_claim():
            raise RuntimeError("remediation output claim is unavailable")
        assert self.output_claim is not None
        return {
            "filename": self.output_claim.filename,
            "size_bytes": self.output_claim.size,
            "sha256": self.output_claim.sha256,
            "mime_type": self.output_claim.mime,
        }

    def open_output_stream(self):
        if not self.has_output_claim():
            raise RuntimeError("remediation output claim is unavailable")
        assert self.output_claim is not None
        return self.output_claim.open_stream()

    def close_output_claim(self) -> None:
        if self.output_claim is not None:
            self.output_claim.close()


_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".tex": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".zip": "application/zip",
    ".vtt": "text/vtt",
    ".txt": "text/plain",
}


def _safe_issues(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("details", [])
    if not isinstance(value, list) or len(value) > _MAX_ISSUES:
        raise RemediationSubprocessError("invalid_job_payload")
    return [item for item in value if isinstance(item, dict)]


def _json_record(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        record = value.model_dump(mode="json")
        if record.get("visual_semantic_contract") is None:
            record.pop("visual_semantic_contract", None)
        return record
    if isinstance(value, dict):
        record = dict(value)
        if record.get("visual_semantic_contract") is None:
            record.pop("visual_semantic_contract", None)
        return record
    fields = getattr(value, "__dict__", None)
    record = dict(fields) if isinstance(fields, dict) else {}
    if record.get("visual_semantic_contract") is None:
        record.pop("visual_semantic_contract", None)
    return record


def _purpose_clients(binding: Any) -> tuple[Any, Any]:
    if not isinstance(binding, dict):
        return None, None
    from src.ai.lms_remediation_client import LMSRemediationClient

    common = {
        key: binding.get(key)
        for key in ("department_id", "actor_id", "job_id", "scan_id", "cloud_file_id")
        if isinstance(binding.get(key), str) and binding.get(key)
    }
    remediation = (
        LMSRemediationClient.bind_if_allowed(purpose="remediation", **common)
        if binding.get("remediation") is True
        else None
    )
    alt_text = (
        LMSRemediationClient.bind_if_allowed(purpose="alt_text", **common)
        if binding.get("alt_text") is True
        else None
    )
    if binding.get("remediation") is True and remediation is None:
        raise RemediationSubprocessError("policy_not_permitted")
    if binding.get("alt_text") is True and alt_text is None:
        raise RemediationSubprocessError("policy_not_permitted")
    return remediation, alt_text


def _build_remediator(request: dict[str, Any], source: Path, work_dir: Path):
    from src.education.remediation.base import OutputFormat, RemediationConfig

    options = request.get("options") if isinstance(request.get("options"), dict) else {}
    lms_binding = request.get("lms_binding")
    ai_client, alt_text_client = _purpose_clients(lms_binding)
    authoritative = isinstance(lms_binding, dict)
    use_ai = bool(options.get("use_ai", True))
    if not authoritative and use_ai:
        from src.ai.providers import get_provider_manager

        ai_client = get_provider_manager()
    config = RemediationConfig(
        use_ai=use_ai if not authoritative else ai_client is not None,
        allow_legacy_nested_ai=not authoritative,
        fix_alt_text=(not authoritative or alt_text_client is not None),
        verify_fixes=True,
        create_backup=False,
        output_directory=str(work_dir),
    )
    config.latex_output_formats = [
        OutputFormat(value)
        for value in options.get("latex_formats", ["tex", "pdf", "html"])
        if value in {"tex", "pdf", "html"}
    ]
    config.multimedia_output_format = OutputFormat(
        options.get("multimedia_format", "individual")
    )
    config.include_original_in_zip = bool(options.get("include_original_in_zip", True))
    scan_type = str(request.get("scan_type", "")).upper()
    issues = _safe_issues(request.get("issues"))

    if scan_type == "PDF" or (scan_type == "LATEX" and source.suffix.lower() == ".pdf"):
        from src.education.remediation.pdf_remediator import PdfRemediator as cls
    elif scan_type in {"WORD", "DOCX"}:
        from src.education.remediation.docx_remediator import DocxRemediator as cls
    elif scan_type in {"POWERPOINT", "PPTX"}:
        from src.education.remediation.pptx_remediator import PptxRemediator as cls
    elif scan_type in {"EXCEL", "XLSX"}:
        from src.education.remediation.xlsx_remediator import XlsxRemediator as cls
    elif scan_type == "LATEX":
        from src.education.remediation.latex_remediator import LatexRemediator as cls
    elif scan_type in {"MULTIMEDIA", "VIDEO"}:
        from src.education.remediation.multimedia_remediator import (
            MultimediaRemediator as cls,
        )
    elif scan_type in {"CODE", "CANVAS_CONTENT", "WEBSITE", "HTML"}:
        from src.education.remediation.html_remediator import HtmlRemediator as cls
    else:
        raise RemediationSubprocessError("remediation_unsupported")

    return cls(
        file_path=str(source),
        issues=issues,
        config=config,
        ai_client=ai_client,
        alt_text_client=alt_text_client,
    )


def _run_child(request: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(request.get("source_path", ""))).resolve(strict=True)
    work_dir = Path(str(request.get("work_dir", ""))).resolve(strict=True)
    if not source.is_file() or not work_dir.is_dir() or source.parent != work_dir:
        raise RemediationSubprocessError("source_file_unavailable")
    result = _build_remediator(request, source, work_dir).remediate()
    try:
        return {
            "success": bool(result.success),
            "output_file": result.output_file,
            "total_issues": getattr(result, "total_issues", 0),
            "fixed_count": result.fixed_count,
            "manual_count": result.manual_count,
            "failed_count": result.failed_count,
            "skipped_count": result.skipped_count,
            "original_compliance_score": result.original_compliance_score,
            "remediated_compliance_score": result.remediated_compliance_score,
            "compliance_improvement": result.improvement,
            "duration_seconds": result.duration_seconds,
            "verification_passed": getattr(result, "verification_passed", False),
            "fixed_issues": [_json_record(item) for item in result.fixed_issues],
            "manual_issues": [_json_record(item) for item in result.manual_issues],
            "failed_issues": [_json_record(item) for item in result.failed_issues],
        }
    finally:
        close_claim = getattr(result, "close_output_claim", None)
        if callable(close_claim):
            close_claim()


def _write_response(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        encoded = b'{"success":false,"error_code":"invalid_job_payload"}'
    path.write_bytes(encoded)


def child_main(request_path: Path, response_path: Path) -> int:
    try:
        if request_path.stat().st_size > _MAX_REQUEST_BYTES:
            raise RemediationSubprocessError("invalid_job_payload")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise RemediationSubprocessError("invalid_job_payload")
        _write_response(response_path, _run_child(request))
        return 0
    except RemediationSubprocessError as exc:
        code = str(exc)
        if code not in {
            "invalid_job_payload",
            "policy_not_permitted",
            "source_file_unavailable",
            "remediation_unsupported",
        }:
            code = "remediation_failed"
        _write_response(response_path, {"success": False, "error_code": code})
        return 1
    except Exception:
        _write_response(
            response_path, {"success": False, "error_code": "remediation_failed"}
        )
        return 1


async def _terminate_process_group(
    process: asyncio.subprocess.Process, grace_seconds: float
) -> None:
    def group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    except PermissionError:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    deadline = asyncio.get_running_loop().time() + max(grace_seconds, 0.0)
    while group_exists() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    if group_exists():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    await process.wait()


def _claim_output(
    output: Any, *, work_dir: Path, work_dir_fd: int
) -> DescriptorBoundOutputClaim | None:
    if output is None:
        return None
    if not isinstance(output, str):
        raise RemediationSubprocessError("remediation_failed")
    output_path = Path(output)
    if not output_path.is_absolute() or ".." in output_path.parts:
        raise RemediationSubprocessError("remediation_failed")
    try:
        relative = output_path.relative_to(work_dir)
    except ValueError as exc:
        raise RemediationSubprocessError("remediation_failed") from exc
    if not relative.parts or ".." in relative.parts:
        raise RemediationSubprocessError("remediation_failed")
    filename = relative.parts[-1]
    if (
        filename in {"", ".", ".."}
        or len(filename) > 512
        or "\x00" in filename
        or "\\" in filename
        or Path(filename).name != filename
    ):
        raise RemediationSubprocessError("remediation_failed")
    mime = _MIME_BY_EXTENSION.get(Path(filename).suffix.lower())
    if mime is None:
        raise RemediationSubprocessError("remediation_failed")

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    opened_directories: list[int] = []
    descriptor = -1
    try:
        directory_fd = os.dup(work_dir_fd)
        os.set_inheritable(directory_fd, False)
        opened_directories.append(directory_fd)
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened_directories.append(directory_fd)
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RemediationSubprocessError("remediation_failed")
        owned = descriptor
        descriptor = -1
        return DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
            owned,
            filename=filename,
            display_path=str(output_path),
            mime=mime,
        )
    except OSError as exc:
        raise RemediationSubprocessError("remediation_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        for opened_fd in reversed(opened_directories):
            os.close(opened_fd)


def _write_bound_file(directory_fd: int, name: str, data: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("bound file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_bound_file(source: Path, directory_fd: int, name: str) -> None:
    source_fd = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    destination_fd = -1
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise RemediationSubprocessError("source_file_unavailable")
        destination_fd = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        while chunk := os.read(source_fd, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError("bound file copy made no progress")
                view = view[written:]
        os.fsync(destination_fd)
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)


def _read_bound_file(directory_fd: int, name: str, *, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum:
            raise RemediationSubprocessError("remediation_failed")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise RemediationSubprocessError("remediation_failed")
        return data
    except OSError as exc:
        raise RemediationSubprocessError("remediation_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _remove_bound_directory_contents(directory_fd: int) -> None:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    for name in os.listdir(directory_fd):
        child_fd = -1
        try:
            child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
        except OSError as exc:
            if exc.errno not in {errno.ENOTDIR, errno.ELOOP}:
                raise
            os.unlink(name, dir_fd=directory_fd)
        else:
            try:
                _remove_bound_directory_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
    os.fsync(directory_fd)
    if os.listdir(directory_fd):
        raise OSError("authoritative work directory retained entries")


def _remove_bound_work_directory(
    *, root_fd: int, work_dir_fd: int, expected_state: os.stat_result
) -> None:
    _remove_bound_directory_contents(work_dir_fd)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    matches: list[str] = []
    for name in os.listdir(root_fd):
        candidate_fd = -1
        try:
            candidate_fd = os.open(name, directory_flags, dir_fd=root_fd)
            candidate = os.fstat(candidate_fd)
            if (candidate.st_dev, candidate.st_ino) == (
                expected_state.st_dev,
                expected_state.st_ino,
            ):
                matches.append(name)
        except OSError:
            continue
        finally:
            if candidate_fd >= 0:
                os.close(candidate_fd)
    if len(matches) != 1:
        raise OSError("authoritative work directory identity is no longer removable")
    os.rmdir(matches[0], dir_fd=root_fd)
    os.fsync(root_fd)


def _path_matches_state(path: Path, expected_state: os.stat_result) -> bool:
    try:
        current = path.lstat()
    except OSError:
        return False
    return not stat.S_ISLNK(current.st_mode) and (
        current.st_dev,
        current.st_ino,
    ) == (expected_state.st_dev, expected_state.st_ino)


def _create_bound_work_directory(
    root_fd: int, root: Path, directory_flags: int
) -> tuple[Path, int, os.stat_result]:
    for _ in range(100):
        name = f"job-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
        except FileExistsError:
            continue
        work_dir_fd = -1
        try:
            work_dir_fd = os.open(name, directory_flags, dir_fd=root_fd)
            state = os.fstat(work_dir_fd)
            return Path(os.path.abspath(root / name)), work_dir_fd, state
        except BaseException:
            if work_dir_fd >= 0:
                os.close(work_dir_fd)
            os.rmdir(name, dir_fd=root_fd)
            raise
    raise RemediationSubprocessError("remediation_failed")


async def run_remediation_subprocess(
    *,
    source_path: str,
    scan_type: Any,
    issues: Any,
    options: dict[str, Any],
    work_root: str | Path,
    lms_binding: dict[str, Any] | None = None,
    timeout_seconds: float,
    termination_grace_seconds: float,
) -> SubprocessRemediationResult:
    """Run one remediation in an isolated process group and reap every child."""
    try:
        source = Path(source_path).resolve(strict=True)
    except OSError as exc:
        raise RemediationSubprocessError("source_file_unavailable") from exc
    root = Path(os.path.abspath(work_root))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_state = root.lstat()
    if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
        raise RemediationSubprocessError("remediation_failed")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    root_fd = os.open(root, directory_flags)
    opened_root = os.fstat(root_fd)
    if (opened_root.st_dev, opened_root.st_ino) != (
        root_state.st_dev,
        root_state.st_ino,
    ):
        os.close(root_fd)
        raise RemediationSubprocessError("remediation_failed")
    try:
        work_dir, work_dir_fd, work_dir_state = _create_bound_work_directory(
            root_fd, root, directory_flags
        )
    except BaseException:
        os.close(root_fd)
        raise
    process: asyncio.subprocess.Process | None = None
    output_claim: DescriptorBoundOutputClaim | None = None
    result_value: SubprocessRemediationResult | None = None
    primary_error: BaseException | None = None
    try:
        local_source = work_dir / f"source{source.suffix.lower()}"
        await asyncio.to_thread(
            _copy_bound_file, source, work_dir_fd, local_source.name
        )
        request = {
            "source_path": str(local_source),
            "scan_type": str(getattr(scan_type, "value", scan_type)).upper(),
            "issues": _safe_issues(issues),
            "options": options,
            "work_dir": str(work_dir),
            "lms_binding": lms_binding,
        }
        try:
            encoded = json.dumps(
                request, allow_nan=False, separators=(",", ":")
            ).encode()
        except (TypeError, ValueError) as exc:
            raise RemediationSubprocessError("invalid_job_payload") from exc
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise RemediationSubprocessError("invalid_job_payload")
        request_path = work_dir / "request.json"
        response_path = work_dir / "response.json"
        _write_bound_file(work_dir_fd, request_path.name, encoded)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.jobs.remediation_subprocess",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError as exc:
            await _terminate_process_group(process, termination_grace_seconds)
            raise RemediationSubprocessTimeout("job_execution_timeout") from exc
        await _terminate_process_group(process, termination_grace_seconds)
        if not _path_matches_state(work_dir, work_dir_state):
            raise RemediationSubprocessError("remediation_failed")
        try:
            response = json.loads(
                _read_bound_file(
                    work_dir_fd, response_path.name, maximum=_MAX_RESPONSE_BYTES
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemediationSubprocessError("remediation_failed") from exc
        if not isinstance(response, dict):
            raise RemediationSubprocessError("remediation_failed")
        if process.returncode != 0 or response.get("success") is not True:
            code = response.get("error_code")
            raise RemediationSubprocessError(
                code if isinstance(code, str) else "remediation_failed"
            )
        output_claim = _claim_output(
            response.pop("output_file", None),
            work_dir=work_dir,
            work_dir_fd=work_dir_fd,
        )
        result_value = SubprocessRemediationResult(response, output_claim)
    except BaseException as exc:
        primary_error = exc
        if process is not None and process.returncode is None:
            await _terminate_process_group(process, termination_grace_seconds)

    cleanup_error: BaseException | None = None
    try:
        _remove_bound_work_directory(
            root_fd=root_fd,
            work_dir_fd=work_dir_fd,
            expected_state=work_dir_state,
        )
    except BaseException as exc:
        cleanup_error = exc
    finally:
        os.close(work_dir_fd)
        os.close(root_fd)
    if cleanup_error is not None:
        if output_claim is not None:
            output_claim.close()
        raise RemediationSubprocessError("remediation_failed") from cleanup_error
    if primary_error is not None:
        if output_claim is not None:
            output_claim.close()
        raise primary_error
    if result_value is None:
        raise RemediationSubprocessError("remediation_failed")
    return result_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    arguments = parser.parse_args()
    return child_main(arguments.request, arguments.response)


if __name__ == "__main__":
    raise SystemExit(main())
