"""Real PostgreSQL 16 consumer controls with synthetic values only."""

from __future__ import annotations

import io
import json
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from sqlalchemy import URL, create_engine, text

password = Path("/run/secrets/pg_password").read_text().strip()
assert password
socket = "/var/run/postgresql"
with psycopg2.connect(
    host=socket,
    dbname="postgres",
    user="postgres",
    password=password,
    connect_timeout=5,
) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE driver_probe (id int, label text, data jsonb)"
        )
        cursor.execute(
            "INSERT INTO driver_probe VALUES (%s, %s, %s)",
            (1, "Accessible Ελληνικά 日本語", Json({"source": "synthetic", "n": 3})),
        )
        cursor.execute("SELECT label, data FROM driver_probe WHERE id = %s", (1,))
        assert cursor.fetchone() == (
            "Accessible Ελληνικά 日本語",
            {"source": "synthetic", "n": 3},
        )
        cursor.execute("SAVEPOINT preserved")
        try:
            cursor.execute("SELECT 1 / 0")
        except psycopg2.errors.DivisionByZero:
            cursor.execute("ROLLBACK TO SAVEPOINT preserved")
        else:
            raise AssertionError("expected division-by-zero refusal")
        cursor.execute("SELECT count(*) FROM driver_probe")
        assert cursor.fetchone() == (1,)
        cursor.execute("CREATE TEMP TABLE copy_probe (id int, label text)")
        cursor.copy_expert(
            "COPY copy_probe FROM STDIN WITH (FORMAT csv)",
            io.StringIO('2,"quoted, text"\n3,"Ελληνικά"\n'),
        )
        cursor.execute("SELECT id, label FROM copy_probe ORDER BY id")
        assert cursor.fetchall() == [(2, "quoted, text"), (3, "Ελληνικά")]
        output = io.StringIO()
        cursor.copy_expert("COPY copy_probe TO STDOUT WITH (FORMAT csv)", output)
        assert "quoted, text" in output.getvalue() and "Ελληνικά" in output.getvalue()
        cursor.execute("SHOW server_version_num")
        server = int(cursor.fetchone()[0])
        assert 160000 <= server < 170000

engine = create_engine(
    URL.create(
        "postgresql+psycopg2",
        username="postgres",
        password=password,
        database="postgres",
        query={"host": socket},
    ),
    pool_pre_ping=True,
)
with engine.begin() as connection:
    assert (
        connection.execute(
            text("SELECT :value"), {"value": "source-linked"}
        ).scalar_one()
        == "source-linked"
    )
engine.dispose()


def connect(host: str, ca: str):
    return psycopg2.connect(
        host=host,
        hostaddr="127.0.0.1",
        user="postgres",
        password=password,
        dbname="postgres",
        sslmode="verify-full",
        sslrootcert=ca,
        connect_timeout=5,
    )


with connect("localhost", "/tls/server.crt") as connection:
    assert connection.info.ssl_in_use
    with connection.cursor() as cursor:
        cursor.execute("SELECT 42")
        assert cursor.fetchone() == (42,)
    protocol = connection.info.ssl_attribute("protocol")
    assert protocol in {"TLSv1.2", "TLSv1.3"}

for refusal, host, ca in (
    ("wrong-host", "wrong.invalid", "/tls/server.crt"),
    ("unrelated-ca", "localhost", "/tls/unrelated.crt"),
):
    try:
        rejected = connect(host, ca)
    except psycopg2.OperationalError as error:
        detail = str(error).lower()
        assert "certificate" in detail
        if refusal == "wrong-host":
            assert "does not match" in detail or "mismatch" in detail
        else:
            assert "verify failed" in detail or "self-signed" in detail
    else:
        rejected.close()
        raise AssertionError("untrusted TLS peer was accepted")

print(
    json.dumps(
        {
            "passed": True,
            "driver_version": psycopg2.__version__,
            "compiled_libpq": psycopg2.__libpq_version__,
            "runtime_libpq": psycopg2.extensions.libpq_version(),
            "postgres_server_major": server // 10000,
            "tls_protocol": protocol,
            "controls": [
                "unicode-jsonb",
                "parameterized-query",
                "error-savepoint-recovery",
                "copy-in-out",
                "sqlalchemy-pool",
                "verify-full-localhost-query",
                "verify-full-wrong-host-refusal",
                "verify-full-unrelated-ca-refusal",
            ],
        },
        sort_keys=True,
    )
)
