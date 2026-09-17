#!/usr/bin/env python3
"""Check dashboard icon, SPA fallback and API routing with disposable nginx.

Requires Docker; uses dashboard/Dockerfile's digest-pinned runtime, a synthetic
index/API and the real public favicon. Only unique fixture containers/network
and temporary files are removed. Published ports are bound to loopback.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
INDEX = b"<!doctype html><title>Dashboard routing fixture</title>"
BACKEND = """
server {
    listen 8000;
    default_type application/json;
    location / { return 200 '{"uri":"$request_uri"}'; }
}
"""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, check=check, text=True, capture_output=True, timeout=180
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def pinned_image() -> str:
    match = re.search(
        r"^FROM (nginx:[^\s]+@sha256:[a-f0-9]{64})(?:\s|$)",
        (ROOT / "dashboard/Dockerfile").read_text(),
        re.M,
    )
    require(match is not None, "dashboard/Dockerfile must pin the nginx runtime digest")
    return match.group(1)


def request(port: int, path: str) -> tuple[int, list[tuple[str, str]], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers={"Host": "dashboard.example.com"})
        response = conn.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        conn.close()


def verify(port: int) -> None:
    status, headers, body = request(port, "/favicon.ico")
    require(status == 200, f"/favicon.ico returned {status}, expected 200")
    require(
        body == (ROOT / "dashboard/public/favicon.ico").read_bytes(),
        "/favicon.ico must serve the actual public ICO bytes",
    )
    content_types = [value for key, value in headers if key.lower() == "content-type"]
    require(
        content_types == ["image/x-icon"],
        f"Expected one ICO Content-Type, got {content_types}",
    )
    for path in ("/", "/settings/profile?fixture=1"):
        status, _, body = request(port, path)
        require(status == 200 and body == INDEX, f"SPA fallback failed for {path}")
    for path, upstream in (
        ("/api/health", "/health"),
        (
            "/api/education/scans?fixture=one%20two&fixture=three",
            "/education/scans?fixture=one%20two&fixture=three",
        ),
        ("/api/api/jobs/worker-status", "/api/jobs/worker-status"),
    ):
        status, _, body = request(port, path)
        require(status == 200, f"API fixture returned {status} for {path}")
        require(
            json.loads(body) == {"uri": upstream},
            f"API prefix stripping/query preservation failed for {path}",
        )


def main() -> None:
    image = pinned_image()
    network = "aelira-dashboard-fixture-" + uuid.uuid4().hex[:12]
    backend, proxy = network + "-api", network + "-proxy"
    created: list[str] = []
    network_created = False
    with tempfile.TemporaryDirectory(prefix="aelira-dashboard-fixture-") as directory:
        fixture = Path(directory)
        fixture.chmod(0o755)
        public = fixture / "public"
        public.mkdir()
        (public / "index.html").write_bytes(INDEX)
        shutil.copyfile(ROOT / "dashboard/public/favicon.ico", public / "favicon.ico")
        (fixture / "backend.conf").write_text(BACKEND)
        print(f"Checking dashboard nginx configuration with {image}", flush=True)
        try:
            run("docker", "network", "create", network)
            network_created = True
            run(
                "docker",
                "create",
                "--name",
                backend,
                "--network",
                network,
                "--network-alias",
                "api",
                "--mount",
                f"type=bind,src={fixture / 'backend.conf'},dst=/etc/nginx/conf.d/default.conf,readonly",
                image,
            )
            created.append(backend)
            run("docker", "start", backend)
            run(
                "docker",
                "create",
                "--name",
                proxy,
                "--network",
                network,
                "-p",
                "127.0.0.1::80",
                "--mount",
                f"type=bind,src={ROOT / 'dashboard/nginx.conf'},dst=/etc/nginx/conf.d/default.conf,readonly",
                "--mount",
                f"type=bind,src={public},dst=/usr/share/nginx/html,readonly",
                image,
            )
            created.append(proxy)
            run("docker", "start", proxy)
            run("docker", "exec", proxy, "nginx", "-t")
            binding = run("docker", "port", proxy, "80/tcp").stdout.strip()
            require(binding.startswith("127.0.0.1:"), "Fixture must bind to loopback")
            port = int(binding.rsplit(":", 1)[1])
            for _ in range(30):
                try:
                    if request(port, "/health")[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Dashboard fixture did not become ready")
            verify(port)
            print(
                "PASS: nginx syntax, real ICO bytes/status/MIME, SPA fallback and API prefix/query routing"
            )
        finally:
            cleanup_errors = []
            for container in reversed(created):
                result = run("docker", "rm", "-f", container, check=False)
                if result.returncode:
                    cleanup_errors.append(
                        f"Could not remove {container}: {result.stderr}"
                    )
            if network_created:
                result = run("docker", "network", "rm", network, check=False)
                if result.returncode:
                    cleanup_errors.append(
                        f"Could not remove {network}: {result.stderr}"
                    )
            require(not cleanup_errors, "\n".join(cleanup_errors))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Fixture command failed: {exc.cmd}\n{exc.stderr}") from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
