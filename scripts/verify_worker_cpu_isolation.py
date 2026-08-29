#!/usr/bin/env python3
"""Prove kernel-quota worker saturation leaves a same-core API responsive."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def _run(
    *arguments: str, capture: bool = False, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )


def _response_code(port: int, path: str, *, timeout: float = 1.5) -> int:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout
        ) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_for_api(container: str, port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if (
            _run(
                "docker", "inspect", "-f", "{{.State.Running}}", container, capture=True
            ).stdout.strip()
            != "true"
        ):
            raise RuntimeError("API container exited before readiness")
        try:
            if _response_code(port, "/health", timeout=0.2) == 200:
                return
        except (OSError, TimeoutError):
            pass
        time.sleep(0.25)
    logs = _run("docker", "logs", container, capture=True)
    detail = (logs.stdout + logs.stderr)[-4000:]
    raise RuntimeError(f"API container did not become ready:\n{detail}")


def verify(image: str) -> None:
    if sys.platform != "linux":
        raise RuntimeError("kernel CPU isolation gate requires Linux")
    _run("docker", "info", capture=True)
    allowed_cpus = os.sched_getaffinity(0)
    if not allowed_cpus:
        raise RuntimeError("runner exposes no schedulable CPU")
    cpu = str(min(allowed_cpus))
    suffix = uuid.uuid4().hex[:12]
    api_name = f"aelira-api-quota-{suffix}"
    worker_name = f"aelira-worker-quota-{suffix}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    worker_code = """
import asyncio
from unittest.mock import MagicMock
from src.jobs.contracts import JobSuccess
from src.jobs.job_processor import ClaimedJob, JobProcessor
from src.jobs.local_scan_subprocess import _run_process
from src.jobs.registry import JobRegistry

async def heavy(_context, _db, _tokens):
    code = '''import pathlib,time
pathlib.Path("/probe/started").write_text("started")
end=time.monotonic()+6
value=0
while time.monotonic()<end:
    value=(value+1)%1000003
'''
    await _run_process(("python", "-c", code), timeout_seconds=8)
    return JobSuccess({"success": True})

class Probe(JobProcessor):
    def _owns_claim(self, _claim): return True
    def _cancellation_requested(self, _claim): return False
    def _fenced_update(self, _claim, _values): return True
    def _finish(self, _claim, _result): return True
    def _record_outcome(self, *, completed): pass

registry=JobRegistry()
registry.register("scan", heavy)
worker=Probe(
    registry=registry,
    session_factory=MagicMock(),
    heartbeat_interval=60,
    max_execution_seconds=10,
)
worker._token_manager=MagicMock()
claim=ClaimedJob("heavy-job", "scan", {}, "claim", "worker", 1, 1)
raise SystemExit(0 if asyncio.run(worker.process_claim(claim)) else 1)
"""
    with tempfile.TemporaryDirectory(prefix="aelira-quota-") as probe_dir:
        os.chmod(probe_dir, 0o777)
        started = Path(probe_dir) / "started"
        try:
            _run(
                "docker",
                "run",
                "-d",
                "--name",
                api_name,
                "--cpuset-cpus",
                cpu,
                "-p",
                f"127.0.0.1:{port}:8000",
                "-e",
                "SKIP_MIGRATIONS=true",
                "-e",
                "ENV=test",
                "-e",
                "DATABASE_URL=sqlite:///:memory:",
                "-e",
                "JWT_SECRET=kernel-quota-test-secret-at-least-32-bytes",
                image,
            )
            _wait_for_api(api_name, port)
            _run(
                "docker",
                "run",
                "-d",
                "--name",
                worker_name,
                "--cpuset-cpus",
                cpu,
                "--cpus",
                "0.75",
                "-v",
                f"{probe_dir}:/probe",
                "-e",
                "ENV=test",
                "-e",
                "SKIP_MIGRATIONS=true",
                "-e",
                "DATABASE_URL=sqlite:///:memory:",
                "-e",
                "JWT_SECRET=kernel-quota-test-secret-at-least-32-bytes",
                image,
                "python",
                "-c",
                worker_code,
            )
            nano_cpus = _run(
                "docker",
                "inspect",
                "-f",
                "{{.HostConfig.NanoCpus}}",
                worker_name,
                capture=True,
            ).stdout.strip()
            pinned = _run(
                "docker",
                "inspect",
                "-f",
                "{{.HostConfig.CpusetCpus}}",
                worker_name,
                capture=True,
            ).stdout.strip()
            if nano_cpus != "750000000" or pinned != cpu:
                raise RuntimeError(
                    f"worker quota not enforced: NanoCpus={nano_cpus}, cpuset={pinned}"
                )
            for _ in range(200):
                if started.exists():
                    break
                time.sleep(0.025)
            else:
                logs = _run("docker", "logs", worker_name, capture=True)
                detail = (logs.stdout + logs.stderr)[-4000:]
                raise RuntimeError(
                    f"representative queued worker did not start:\n{detail}"
                )

            maximum_latency = 0.0
            for _ in range(12):
                for path, expected in (
                    ("/health", 200),
                    ("/auth/health", 200),
                    ("/definitely-unrelated", 404),
                ):
                    before = time.perf_counter()
                    actual = _response_code(port, path)
                    elapsed = time.perf_counter() - before
                    maximum_latency = max(maximum_latency, elapsed)
                    if actual != expected:
                        raise RuntimeError(
                            f"{path} returned {actual}, expected {expected}"
                        )
                    if elapsed >= 1.5:
                        raise RuntimeError(
                            f"{path} latency {elapsed:.3f}s exceeded 1.5s"
                        )
                time.sleep(0.1)
            exit_code = int(
                _run(
                    "docker",
                    "wait",
                    worker_name,
                    capture=True,
                    timeout=15,
                ).stdout.strip()
            )
            if exit_code != 0:
                logs = _run("docker", "logs", worker_name, capture=True)
                detail = (logs.stdout + logs.stderr)[-4000:]
                raise RuntimeError(
                    f"representative queued worker exited {exit_code}:\n{detail}"
                )
            print(
                "kernel worker isolation verified: "
                f"cpu={cpu}, quota=0.75, max_api_latency={maximum_latency:.3f}s"
            )
        finally:
            subprocess.run(
                ("docker", "rm", "-f", worker_name, api_name),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    verify(parser.parse_args().image)


if __name__ == "__main__":
    main()
