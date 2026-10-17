#!/usr/bin/env python3
"""Exercise the final image's source-built psycopg2 against a real PostgreSQL.

The driver must connect with the operating system's libpq and TLS libraries,
not with copies bundled in a wheel. This probe proves both halves from inside
the running image: it reads the libraries the process actually mapped, then runs
real queries (Unicode, JSON, parameters, savepoint recovery, COPY and a pooled
SQLAlchemy engine) over a verified TLS connection to a synthetic PostgreSQL 16.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SYSTEM_LIBRARY_PREFIX = "/usr/lib/"
REQUIRED_LIBRARIES = ("libpq.so", "libssl.so", "libcrypto.so")
UNICODE_TEXT = "café 日本語 \U0001f9ea Aelira"


def mapped_libraries(maps_path: Path = Path("/proc/self/maps")) -> dict[str, str]:
    """Return the resolved file behind every mapped shared library."""
    libraries: dict[str, str] = {}
    for line in maps_path.read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) < 6 or not fields[5].startswith("/"):
            continue
        path = os.path.realpath(fields[5])
        name = Path(fields[5]).name
        if ".so" in name or ".so" in Path(path).name:
            libraries[name] = path
    return libraries


def check_loaded_libraries(libraries: dict[str, str]) -> dict[str, str]:
    """Require libpq, libssl and libcrypto from /usr/lib and no bundled copy."""
    selected: dict[str, str] = {}
    for prefix in REQUIRED_LIBRARIES:
        matches = {n: p for n, p in libraries.items() if n.startswith(prefix)}
        if not matches:
            raise RuntimeError(f"{prefix} is not loaded in the psycopg2 process")
        for name, path in matches.items():
            if not path.startswith(SYSTEM_LIBRARY_PREFIX):
                raise RuntimeError(
                    f"{name} loaded from {path}, not {SYSTEM_LIBRARY_PREFIX}"
                )
            selected[name] = path
    bundled = sorted(p for p in libraries.values() if ".libs/" in p)
    if bundled:
        raise RuntimeError(f"bundled wheel libraries are loaded: {bundled}")
    return selected


def classify_tls_refusal(reason: str, error: Exception) -> str:
    """Accept only a certificate failure for the requested negative control."""
    detail = str(error).lower()
    if "certificate" not in detail:
        raise RuntimeError(f"{reason} failed without a certificate refusal") from None
    expected = {
        "wrong-host": ("does not match", "mismatch"),
        "untrusted-ca": ("verify failed", "self-signed", "unknown ca"),
    }
    if not any(marker in detail for marker in expected[reason]):
        raise RuntimeError(f"{reason} lacked its expected certificate reason") from None
    return reason


def require_tls_refusal(connect: object, reason: str, args: dict[str, object]) -> str:
    """Reject an accepted peer and any failure unrelated to certificate checking."""
    import psycopg2

    try:
        rejected = connect(**args)
    except psycopg2.OperationalError as error:
        return classify_tls_refusal(reason, error)
    rejected.close()
    raise RuntimeError(f"{reason} peer was accepted")


def library_receipt(libraries: dict[str, str]) -> dict[str, dict[str, str]]:
    """Hash mapped files and record their installed Debian owners."""
    receipt: dict[str, dict[str, str]] = {}
    for name, path in libraries.items():
        owner = subprocess.run(
            ["dpkg-query", "--search", path],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        package = owner.split(": ", 1)[0]
        if package.split(":", 1)[0] not in {"libpq5", "libssl3t64"}:
            raise RuntimeError(f"unexpected owner of {name}: {package}")
        version = subprocess.run(
            ["dpkg-query", "--show", "--showformat=${Version}", package],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not version:
            raise RuntimeError(f"missing package version for {name}")
        receipt[name] = {
            "path": path,
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "package": package,
            "package_version": version,
        }
    return receipt


def exercise_database(args: dict[str, object]) -> dict[str, object]:
    import psycopg2
    import psycopg2.extras
    from sqlalchemy import create_engine, text

    document = {"title": UNICODE_TEXT, "tags": ["a11y", "wcag"], "score": 3.5}
    with psycopg2.connect(**args) as connection:
        if not connection.info.ssl_in_use:
            raise RuntimeError("positive connection did not negotiate TLS")
        protocol = connection.info.ssl_attribute("protocol")
        if protocol not in {"TLSv1.2", "TLSv1.3"}:
            raise RuntimeError("positive connection used an unexpected TLS protocol")
        major = connection.server_version // 10000
        if major != 16:
            raise RuntimeError(f"expected PostgreSQL 16, connected to {major}")
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE smoke "
                "(id integer PRIMARY KEY, body text NOT NULL, doc jsonb NOT NULL)"
            )
            cursor.execute(
                "INSERT INTO smoke VALUES (%s, %s, %s)",
                (1, UNICODE_TEXT, psycopg2.extras.Json(document)),
            )
            cursor.execute("SELECT body, doc FROM smoke WHERE id = %s", (1,))
            body, doc = cursor.fetchone()
            if body != UNICODE_TEXT or doc != document:
                raise RuntimeError("Unicode or JSONB did not round-trip")

            # A failed statement must be recoverable without losing the
            # transaction's earlier work.
            cursor.execute("SAVEPOINT before_conflict")
            try:
                cursor.execute(
                    "INSERT INTO smoke VALUES (%s, %s, %s)", (1, "dup", "{}")
                )
            except psycopg2.errors.UniqueViolation:
                cursor.execute("ROLLBACK TO SAVEPOINT before_conflict")
            else:
                raise RuntimeError("duplicate key was accepted")
            cursor.execute("SELECT count(*) FROM smoke")
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("savepoint recovery lost or duplicated rows")

            cursor.copy_expert(
                "COPY smoke FROM STDIN WITH (FORMAT csv)",
                io.StringIO('2,"line\nbreak",{}\n3,"über","{""k"": 1}"\n'),
            )
            exported = io.StringIO()
            cursor.copy_expert(
                "COPY (SELECT id, body FROM smoke ORDER BY id) TO STDOUT WITH (FORMAT csv)",
                exported,
            )
            if (
                exported.getvalue().count("\n") != 4
                or "über" not in exported.getvalue()
            ):
                raise RuntimeError("COPY did not round-trip rows")
        connection.rollback()
        libpq_version = psycopg2.__libpq_version__

    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(**args),
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as first:
            pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with engine.connect() as second:
            reused = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        if pid != reused:
            raise RuntimeError("SQLAlchemy pool did not reuse its connection")
        pool_status = engine.pool.status()
    finally:
        engine.dispose()
    return {
        "server_major": major,
        "libpq_version": libpq_version,
        "runtime_libpq_version": psycopg2.extensions.libpq_version(),
        "tls_protocol": protocol,
        "pool": pool_status,
        "controls": [
            "verify-full-query",
            "unicode-jsonb",
            "parameterized-query",
            "savepoint-recovery",
            "copy-in-out",
            "sqlalchemy-pool",
        ],
    }


def main() -> int:
    required = (
        "AELIRA_PG_HOST",
        "AELIRA_PG_HOSTADDR",
        "AELIRA_PG_USER",
        "AELIRA_PG_DATABASE",
        "AELIRA_PG_PASSWORD_FILE",
        "AELIRA_PG_CA_FILE",
        "AELIRA_PG_UNTRUSTED_CA_FILE",
        "AELIRA_RUNTIME_IMAGE_ID",
        "AELIRA_EXPECT_ARCH",
    )
    if any(not os.environ.get(key) for key in required):
        print("missing psycopg2 TLS smoke configuration", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2._psycopg

    if importlib.metadata.version("psycopg2-binary") != "2.9.13":
        raise RuntimeError("the installed psycopg2-binary is not 2.9.13")
    architecture = platform.machine()
    expected_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(
        os.environ["AELIRA_EXPECT_ARCH"]
    )
    if architecture != expected_arch:
        raise RuntimeError("runtime architecture does not match the CI matrix")
    args: dict[str, object] = {
        "host": os.environ["AELIRA_PG_HOST"],
        "hostaddr": os.environ["AELIRA_PG_HOSTADDR"],
        "user": os.environ["AELIRA_PG_USER"],
        "dbname": os.environ["AELIRA_PG_DATABASE"],
        "password": Path(os.environ["AELIRA_PG_PASSWORD_FILE"]).read_text().strip(),
        "sslmode": "verify-full",
        "sslrootcert": os.environ["AELIRA_PG_CA_FILE"],
        "connect_timeout": 5,
    }
    if not args["password"]:
        raise RuntimeError("empty synthetic database password")

    positive = exercise_database(args)
    refusals = {
        "wrong_host": require_tls_refusal(
            psycopg2.connect, "wrong-host", {**args, "host": "wrong.invalid"}
        ),
        "untrusted_ca": require_tls_refusal(
            psycopg2.connect,
            "untrusted-ca",
            {**args, "sslrootcert": os.environ["AELIRA_PG_UNTRUSTED_CA_FILE"]},
        ),
    }

    module = Path(psycopg2.__file__).resolve()
    loaded = check_loaded_libraries(mapped_libraries())
    report = {
        "psycopg2": psycopg2.__version__,
        "module": str(module),
        "extension_sha256": hashlib.sha256(
            Path(psycopg2._psycopg.__file__).read_bytes()
        ).hexdigest(),
        "image_id": os.environ["AELIRA_RUNTIME_IMAGE_ID"],
        "architecture": architecture,
        "loaded_libraries": library_receipt(loaded),
        "refusals": refusals,
        **positive,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
