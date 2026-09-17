#!/usr/bin/env python3
"""Smoke-test the root nginx template with a disposable nginx echo upstream.

Requires Docker and OpenSSL, uses no application services or credentials, and
removes only its uniquely named containers/network and temporary fixture files.
The default image is the digest-pinned nginx runtime from dashboard/Dockerfile.
"""

from __future__ import annotations

import argparse
import ast
import http.client
import json
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROBES = ("/health", "/api/health", "/live", "/ready")
# Only synthetic fixture data is returned; this is not the application API.
FIXTURE = r"""
server {
    listen 8000;
    default_type application/json;
    location = /ready { return 503 '{"status":"fixture-not-ready"}'; }
    location / {
        return 200 '{"uri":"$request_uri","method":"$request_method","host":"$http_host","proto":"$http_x_forwarded_proto","real_ip":"$http_x_real_ip","forwarded_for":"$http_x_forwarded_for","connection":"$http_connection","request_id":"$request_id"}';
    }
}
"""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, check=check, text=True, capture_output=True, timeout=180
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def default_image() -> str:
    dockerfile = (ROOT / "dashboard/Dockerfile").read_text()
    match = re.search(r"^FROM (nginx:[^\s]+@sha256:[a-f0-9]{64})", dockerfile, re.M)
    require(match is not None, "dashboard/Dockerfile must pin the nginx runtime digest")
    return match.group(1)


def upload_paths() -> list[str]:
    """Discover multipart handlers without importing the app or its dependencies."""
    paths = []
    for source in sorted((ROOT / "src/api/education").glob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            defaults = [*node.args.defaults, *node.args.kw_defaults]
            if not any(
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "File"
                for value in defaults
            ):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "post"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    paths.append("/education" + decorator.args[0].value)
    require(bool(paths), "No multipart routes discovered; check the source contract")
    return sorted(set(paths))


def check_template(uploads: list[str]) -> None:
    config = (ROOT / "nginx.conf").read_text()
    active = re.sub(r"#.*", "", config)
    require(
        "proxy_cache off;" in active, "API proxy caching must be explicitly disabled"
    )
    require(
        not re.search(r"proxy_cache_path\s|proxy_cache\s+(?!off\s*;)", active),
        "The API template must not define or enable proxy caches",
    )
    locations = re.findall(r"location\s+~\s+(\S+)\s*\{([^{}]*)\}", active)
    upload_patterns = [
        pattern for pattern, body in locations if "zone=upload_limit" in body
    ]
    for path in uploads:
        require(
            any(re.search(pattern, path) for pattern in upload_patterns),
            f"Multipart route lacks the upload rate limit: {path}",
        )
    require("client_max_body_size 100M;" in active, "Expected a 100 MiB body ceiling")


def request(
    port: int,
    path: str,
    *,
    tls: bool = True,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict | None = None,
) -> tuple[int, dict, bytes]:
    # Only the short-lived, locally generated fixture certificate is untrusted.
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(
            "127.0.0.1", port, context=context, timeout=5
        )
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(
            method,
            path,
            body=body,
            headers={"Host": "api.example.com", **(headers or {})},
        )
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def published_port(container: str, port: int) -> int:
    binding = run("docker", "port", container, f"{port}/tcp").stdout.strip()
    require(
        binding.startswith("127.0.0.1:"),
        f"Fixture port was not loopback-bound: {binding}",
    )
    return int(binding.rsplit(":", 1)[1])


def verify(http_port: int, https_port: int, uploads: list[str]) -> None:
    status, headers, _ = request(http_port, "/auth/login?fixture=1", tls=False)
    require(
        status == 301
        and headers.get("Location") == "https://api.example.com/auth/login?fixture=1",
        "HTTP redirect did not preserve host, path and query",
    )
    status, _, body = request(
        http_port, "/.well-known/acme-challenge/fixture", tls=False
    )
    require(
        status == 200 and body == b"fixture-acme", "HTTP ACME challenge was not served"
    )
    paths = [
        "/",
        "/education/scans?fixture=one%20two&fixture=three",
        "/auth/me",
        "/canvas/courses",
        "/api/jobs/worker-status",
        "/api/reviews",
        "/docs",
        "/openapi.json",
    ]
    for path in paths:
        status, headers, body = request(https_port, path)
        require(status == 200, f"Forwarding failed for {path}: {status}")
        data = json.loads(body)
        require(
            data["uri"] == path and data["method"] == "GET", f"URI changed for {path}"
        )
        require(
            data["host"] == "api.example.com" and data["proto"] == "https",
            "Proxy origin headers changed",
        )
        require(
            bool(data["real_ip"]) and data["forwarded_for"].endswith(data["real_ip"]),
            "Client forwarding headers missing",
        )
        require(data["connection"] == "", "Upstream Connection header must be cleared")
        require("Strict-Transport-Security" in headers, "TLS security header missing")
        time.sleep(0.12)
    for path in PROBES:
        status, _, body = request(https_port, path)
        if path == "/ready":
            require(
                json.loads(body) == {"status": "fixture-not-ready"},
                "Readiness error body was replaced by the proxy",
            )
        require(
            status == (503 if path == "/ready" else 200),
            f"Probe status changed for {path}",
        )
    # Every discovered multipart endpoint must reach the upstream unchanged.
    for path in uploads:
        status, _, body = request(
            https_port, path, method="POST", body=b"fixture-upload"
        )
        require(status == 200, f"Upload forwarding failed for {path}: {status}")
        data = json.loads(body)
        require(
            data["uri"] == path and data["method"] == "POST",
            f"Upload URI/method changed: {path}",
        )
        time.sleep(0.55)
    for path in ("/api/reviews", "/education/scans", "/docs", "/openapi.json"):
        ids = []
        for _ in range(2):
            status, _, body = request(https_port, path)
            require(status == 200, f"Uncached fixture request failed for {path}")
            ids.append(json.loads(body)["request_id"])
            time.sleep(0.12)
        require(ids[0] != ids[1], f"Fixture response was cached for {path}")
    # This oversized declared body is rejected before transmitting payload bytes.
    status, _, _ = request(
        https_port,
        uploads[0],
        method="POST",
        headers={"Content-Length": str(100 * 1024 * 1024 + 1)},
    )
    require(status == 413, "The 100 MiB upload ceiling was not enforced")
    # A bounded burst of tiny fixture requests checks the stricter upload zone.
    statuses = [
        request(https_port, "/education/pdf/scan", method="POST", body=b"x")[0]
        for _ in range(12)
    ]
    require(
        200 in statuses and 503 in statuses,
        f"Upload rate limit did not engage: {statuses}",
    )
    for path in PROBES:
        status, _, body = request(https_port, path)
        if path == "/ready":
            require(
                json.loads(body) == {"status": "fixture-not-ready"},
                "Readiness error body was replaced by the proxy",
            )
        require(
            status == (503 if path == "/ready" else 200),
            f"Upload limiting affected {path}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default=default_image(),
        help="nginx image (default: dashboard's pinned runtime)",
    )
    args = parser.parse_args()
    uploads = upload_paths()
    check_template(uploads)
    name = "aelira-nginx-fixture-" + uuid.uuid4().hex[:12]
    network, backend, proxy = name, name + "-api", name + "-proxy"
    created = []
    network_created = False
    with tempfile.TemporaryDirectory(prefix="aelira-nginx-fixture-") as directory:
        fixture = Path(directory)
        (fixture / "backend.conf").write_text(FIXTURE)
        challenge = fixture / "acme/.well-known/acme-challenge"
        challenge.mkdir(parents=True)
        (challenge / "fixture").write_bytes(b"fixture-acme")
        # nginx workers need to traverse the temporary directory's mounts.
        fixture.chmod(0o755)
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=api.example.com",
            "-keyout",
            str(fixture / "privkey.pem"),
            "-out",
            str(fixture / "fullchain.pem"),
        )
        print(f"Checking exact nginx template with {args.image}", flush=True)
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
                args.image,
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
                "-p",
                "127.0.0.1::443",
                "--mount",
                f"type=bind,src={ROOT / 'nginx.conf'},dst=/etc/nginx/nginx.conf,readonly",
                "--mount",
                f"type=bind,src={fixture},dst=/etc/nginx/ssl,readonly",
                "--mount",
                f"type=bind,src={fixture / 'acme'},dst=/var/www/certbot,readonly",
                args.image,
            )
            created.append(proxy)
            run("docker", "start", proxy)
            run("docker", "exec", proxy, "nginx", "-t")
            http_port, https_port = published_port(proxy, 80), published_port(
                proxy, 443
            )
            for attempt in range(30):
                try:
                    if request(https_port, "/live")[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Fixture proxy did not become ready")
            verify(http_port, https_port, uploads)
            print(
                f"PASS: nginx syntax, TLS/HTTP routing, {len(uploads)} upload routes, probe statuses, body/rate limits and uncached fixture responses"
            )
        finally:
            cleanup_errors = []
            for container in reversed(created):
                result = run("docker", "rm", "-f", container, check=False)
                if result.returncode:
                    cleanup_errors.append(
                        f"Failed to remove fixture container {container}: {result.stderr}"
                    )
            if network_created:
                result = run("docker", "network", "rm", network, check=False)
                if result.returncode:
                    cleanup_errors.append(
                        f"Failed to remove fixture network {network}: {result.stderr}"
                    )
            require(not cleanup_errors, "\n".join(cleanup_errors))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Fixture command failed: {exc.cmd}\n{exc.stderr}") from exc
