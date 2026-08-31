"""Killable process boundary for CPU-bound locally submitted scans."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


class LocalScanSubprocessError(RuntimeError):
    """Bounded local-scan child failure."""


def _bind_parent_death(expected_parent_pid: int) -> None:
    """Kill this isolated process group if its owning worker dies uncleanly."""
    if sys.platform != "linux":
        return

    def terminate_group(_signum: int, _frame: Any) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, terminate_group)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    if os.getppid() != expected_parent_pid:
        terminate_group(signal.SIGTERM, None)


async def _terminate_process(
    process: asyncio.subprocess.Process, grace_seconds: float
) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


async def _run_process(
    arguments: Sequence[str],
    *,
    timeout_seconds: float | None,
    termination_grace_seconds: float,
) -> int:
    """Run one process group and guarantee it is reaped on timeout/cancellation."""
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if timeout_seconds is None:
            return await process.wait()
        return await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        await _terminate_process(process, termination_grace_seconds)
        raise
    except TimeoutError as exc:
        await _terminate_process(process, termination_grace_seconds)
        raise LocalScanSubprocessError("local_scan_execution_timeout") from exc


async def run_local_scan_subprocess(
    *,
    job_id: str,
    claim_token: str,
    worker_id: str,
    scan_id: str,
    department_id: str,
    scan_kind: str,
    options: dict[str, Any],
    work_path: str | None,
    input_sha256: str | None,
    termination_grace_seconds: float,
) -> None:
    """Execute one local scan in a child that can be killed without late effects."""
    request = {
        "job_id": job_id,
        "claim_token": claim_token,
        "worker_id": worker_id,
        "scan_id": scan_id,
        "department_id": department_id,
        "scan_kind": scan_kind,
        "options": options,
        "work_path": work_path,
        "input_sha256": input_sha256,
    }
    encoded = json.dumps(
        request, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if len(encoded) > 262_144:
        raise LocalScanSubprocessError("local_scan_payload_invalid")
    with tempfile.TemporaryDirectory(prefix="aelira-local-scan-") as temp_dir:
        root = Path(temp_dir)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_bytes(encoded)
        return_code = await _run_process(
            (
                sys.executable,
                "-m",
                "src.jobs.local_scan_subprocess",
                "--request",
                str(request_path),
                "--response",
                str(response_path),
                "--parent-pid",
                str(os.getpid()),
            ),
            timeout_seconds=None,
            termination_grace_seconds=termination_grace_seconds,
        )
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalScanSubprocessError("local_scan_execution_failed") from exc
        if (
            return_code != 0
            or not isinstance(response, dict)
            or response.get("success") is not True
        ):
            raise LocalScanSubprocessError("local_scan_execution_failed")


def _execute_request(request: dict[str, Any]) -> None:
    from src.db.database import SessionLocal
    from src.db.models import Scan
    from src.jobs.local_scan_job import (
        _FILE_SCAN_KINDS,
        _run_local_processor,
        _validate_web_targets,
        normalize_local_scan_options,
    )

    required = {
        "job_id",
        "claim_token",
        "worker_id",
        "scan_id",
        "department_id",
        "scan_kind",
        "options",
        "work_path",
        "input_sha256",
    }
    if set(request) != required:
        raise LocalScanSubprocessError("local_scan_payload_invalid")
    job_id = request["job_id"]
    claim_token = request["claim_token"]
    worker_id = request["worker_id"]
    scan_id = request["scan_id"]
    department_id = request["department_id"]
    scan_kind = request["scan_kind"]
    if not all(
        isinstance(value, str) and value
        for value in (
            job_id,
            claim_token,
            worker_id,
            scan_id,
            department_id,
            scan_kind,
        )
    ):
        raise LocalScanSubprocessError("local_scan_scope_invalid")
    options = normalize_local_scan_options(scan_kind, request["options"])
    work_path = request["work_path"]
    input_sha256 = request["input_sha256"]
    content: bytes | None = None
    if scan_kind in _FILE_SCAN_KINDS:
        if not isinstance(work_path, str) or not isinstance(input_sha256, str):
            raise LocalScanSubprocessError("local_scan_input_invalid")
        content = Path(work_path).read_bytes()
        if hashlib.sha256(content).hexdigest() != input_sha256:
            raise LocalScanSubprocessError("local_scan_input_invalid")
    elif work_path is not None or input_sha256 is not None:
        raise LocalScanSubprocessError("local_scan_input_invalid")
    else:
        _validate_web_targets(scan_kind, options)
    from src.jobs.execution_authority import (
        acquire_child_execution_lock,
        claim_is_current,
        install_child_commit_fence,
    )

    with SessionLocal() as db:
        authority = acquire_child_execution_lock(
            db, job_id=job_id, claim_token=claim_token
        )
        remove_fence = install_child_commit_fence(
            job_id=job_id,
            claim_token=claim_token,
            worker_id=worker_id,
        )
        try:
            if not claim_is_current(
                db,
                job_id=job_id,
                claim_token=claim_token,
                worker_id=worker_id,
                lock_row=False,
            ):
                raise LocalScanSubprocessError("local_scan_scope_invalid")
            scan = db.get(Scan, scan_id)
            if scan is None or scan.department_id != department_id:
                raise LocalScanSubprocessError("local_scan_scope_invalid")
            _run_local_processor(scan_kind, scan, options, work_path, content)
        finally:
            remove_fence()
            authority.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    arguments = parser.parse_args()
    _bind_parent_death(arguments.parent_pid)
    response = {"success": False, "error_code": "local_scan_execution_failed"}
    return_code = 1
    try:
        request = json.loads(Path(arguments.request).read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise LocalScanSubprocessError("local_scan_payload_invalid")
        _execute_request(request)
        response = {"success": True}
        return_code = 0
    except Exception:
        pass
    Path(arguments.response).write_text(
        json.dumps(response, separators=(",", ":")), encoding="utf-8"
    )
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
