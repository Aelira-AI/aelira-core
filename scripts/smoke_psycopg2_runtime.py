#!/usr/bin/env python3
"""Exercise the final image's source-built psycopg2 against a real PostgreSQL.

The driver must connect with the operating system's libpq and TLS libraries,
not with copies bundled in a wheel. This probe proves both halves from inside
the running image: it reads the libraries the process actually mapped, then runs
real queries (Unicode, JSON, parameters, savepoint recovery, COPY and a pooled
SQLAlchemy engine) over a connection described by ``DATABASE_URL``.
"""

from __future__ import annotations

import io
import json
import os
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


def exercise_database(dsn: str) -> dict[str, object]:
    import psycopg2
    import psycopg2.extras
    from sqlalchemy import create_engine, text

    document = {"title": UNICODE_TEXT, "tags": ["a11y", "wcag"], "score": 3.5}
    with psycopg2.connect(dsn) as connection:
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
        dsn.replace("postgresql://", "postgresql+psycopg2://", 1),
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
        "pool": pool_status,
    }


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or not dsn.startswith("postgresql://"):
        print("DATABASE_URL must be a postgresql:// URL", file=sys.stderr)
        return 2
    import psycopg2

    module = Path(psycopg2.__file__).resolve()
    loaded = check_loaded_libraries(mapped_libraries())
    report = {
        "psycopg2": psycopg2.__version__,
        "module": str(module),
        "loaded_libraries": loaded,
        **exercise_database(dsn),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
