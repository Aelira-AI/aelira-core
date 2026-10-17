"""Offline checks for the final-image psycopg2 smoke probe's library gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.smoke_psycopg2_runtime import (
    check_loaded_libraries,
    classify_tls_refusal,
    database_library_paths,
    library_receipt,
    mapped_libraries,
    require_tls_refusal,
    trusted_interpreter_extensions,
)
import scripts.smoke_psycopg2_runtime as runtime

SYSTEM = {
    "libpq.so.5.17": "/usr/lib/x86_64-linux-gnu/libpq.so.5.17",
    "libssl.so.3": "/usr/lib/x86_64-linux-gnu/libssl.so.3",
    "libcrypto.so.3": "/usr/lib/x86_64-linux-gnu/libcrypto.so.3",
}


def test_library_gate_accepts_the_system_stack() -> None:
    assert check_loaded_libraries(SYSTEM) == SYSTEM


@pytest.mark.parametrize("missing", ["libpq.so", "libssl.so", "libcrypto.so"])
def test_library_gate_requires_every_system_library(missing: str) -> None:
    libraries = {n: p for n, p in SYSTEM.items() if not n.startswith(missing)}
    with pytest.raises(RuntimeError, match="is not loaded"):
        check_loaded_libraries(libraries)


def test_library_gate_rejects_a_bundled_copy() -> None:
    bundled = dict(SYSTEM)
    bundled["libssl.so.3"] = (
        "/opt/venv/lib/python3.14/site-packages/psycopg2_binary.libs/libssl-1.so.3"
    )
    with pytest.raises(RuntimeError, match="not /usr/lib/"):
        check_loaded_libraries(bundled)


def test_library_gate_rejects_a_bundled_library_with_a_system_name() -> None:
    libraries = dict(SYSTEM)
    libraries["libssl-abc.so.3"] = (
        "/opt/venv/lib/python3.14/site-packages/psycopg2_binary.libs/libssl-abc.so.3"
    )
    with pytest.raises(RuntimeError, match="bundled wheel libraries"):
        check_loaded_libraries({**libraries, "libz.so.1": "/usr/lib/libz.so.1"})


def test_mapped_libraries_reads_resolved_shared_objects(tmp_path: Path) -> None:
    maps = tmp_path / "maps"
    maps.write_text(
        "7f00-7f10 r-xp 00000000 08:01 12 /usr/lib/x86_64-linux-gnu/libpq.so.5.17\n"
        "7f10-7f20 r--p 00000000 08:01 13 /usr/lib/x86_64-linux-gnu/libssl.so.3\n"
        "7f20-7f30 rw-p 00000000 00:00 0\n"
        "7f30-7f40 r-xp 00000000 08:01 14 /usr/local/bin/python3.14\n"
        "7f40-7f50 rw-p 00000000 00:00 0 [heap]\n"
    )
    assert mapped_libraries(maps) == {
        "libpq.so.5.17": "/usr/lib/x86_64-linux-gnu/libpq.so.5.17",
        "libssl.so.3": "/usr/lib/x86_64-linux-gnu/libssl.so.3",
    }


def test_database_closure_includes_static_and_connection_loaded_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "libpq.so.5"
    static.write_bytes(b"pq")
    extension = tmp_path / "_psycopg.so"
    extension.write_bytes(b"native")
    dynamic = tmp_path / "libldap.so.2"
    dynamic.write_bytes(b"ldap")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=f"libpq.so.5 => {static} (0x1)\n"
        ),
    )
    monkeypatch.setattr(runtime, "SYSTEM_LIBRARY_PREFIX", str(tmp_path) + "/")
    observed = {
        "_psycopg.so": str(extension),
        "libpq.so.5": str(static),
        "libldap.so.2": str(dynamic),
    }
    assert database_library_paths(extension, {}, observed, set()) == {
        "libpq.so.5": str(static),
        "libldap.so.2": str(dynamic),
    }


def test_database_closure_rejects_an_unmapped_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "libpq.so.5"
    static.write_bytes(b"pq")
    extension = tmp_path / "_psycopg.so"
    extension.write_bytes(b"native")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=f"libpq.so.5 => {static} (0x1)\n"
        ),
    )
    with pytest.raises(RuntimeError, match="absent from process maps"):
        database_library_paths(extension, {}, {"_psycopg.so": str(extension)}, set())


def test_database_closure_excludes_preexisting_unrelated_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "libpq.so.5"
    static.write_bytes(b"pq")
    extension = tmp_path / "_psycopg.so"
    extension.write_bytes(b"native")
    unrelated = tmp_path / "libunrelated.so.1"
    unrelated.write_bytes(b"other")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=f"libpq.so.5 => {static} (0x1)\n"
        ),
    )
    monkeypatch.setattr(runtime, "SYSTEM_LIBRARY_PREFIX", str(tmp_path) + "/")
    observed = {
        "_psycopg.so": str(extension),
        "libpq.so.5": str(static),
        "libunrelated.so.1": str(unrelated),
    }
    assert database_library_paths(
        extension, {"libunrelated.so.1": str(unrelated)}, observed, set()
    ) == {"libpq.so.5": str(static)}


def test_database_closure_rejects_new_unexplained_off_prefix_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "_psycopg.so"
    extension.write_bytes(b"native")
    static = tmp_path / "libpq.so.5"
    static.write_bytes(b"pq")
    unexpected = tmp_path / "private.so"
    unexpected.write_bytes(b"untrusted")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=f"libpq.so.5 => {static} (0x1)\n"
        ),
    )
    with pytest.raises(RuntimeError, match="unexplained off-prefix"):
        database_library_paths(
            extension,
            {},
            {
                "_psycopg.so": str(extension),
                "libpq.so.5": str(static),
                "private.so": str(unexpected),
            },
            set(),
        )


def test_only_actual_stdlib_extension_module_is_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "lib-dynload" / "_decimal.so"
    extension.parent.mkdir()
    extension.write_bytes(b"stdlib")
    unrelated = tmp_path / "third-party" / "other.so"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"other")
    monkeypatch.setattr(
        runtime.sysconfig, "get_config_var", lambda _: str(extension.parent)
    )
    monkeypatch.setitem(
        runtime.sys.modules, "fixture_stdlib", SimpleNamespace(__file__=str(extension))
    )
    monkeypatch.setitem(
        runtime.sys.modules,
        "fixture_third_party",
        SimpleNamespace(__file__=str(unrelated)),
    )
    trusted = trusted_interpreter_extensions()
    assert str(extension) in trusted
    assert str(unrelated) not in trusted


def test_database_library_receipt_rejects_off_prefix() -> None:
    with pytest.raises(RuntimeError, match="outside /usr/lib"):
        library_receipt({"libpq.so.5": "/opt/private/libpq.so.5"})


def test_database_library_receipt_requires_one_debian_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)
    monkeypatch.setattr(
        runtime.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="")
    )
    with pytest.raises(RuntimeError, match="missing or ambiguous Debian owner"):
        library_receipt({"libpq.so.5": "/usr/lib/libpq.so.5"})


def test_database_library_receipt_records_binary_and_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"mapped bytes")
    replies = iter(
        [
            "libpq5:amd64: /usr/lib/libpq.so.5\n",
            "libpq5:amd64\t17.11-0+deb13u1\tpostgresql-17\t17.11-0+deb13u1",
        ]
    )
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(replies)),
    )
    receipt = library_receipt({"libpq.so.5": "/usr/lib/libpq.so.5"})
    assert receipt["libpq.so.5"]["source_package"] == "postgresql-17"
    assert (
        receipt["libpq.so.5"]["sha256"]
        == runtime.hashlib.sha256(b"mapped bytes").hexdigest()
    )


def test_database_library_receipt_requires_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)
    replies = iter(
        [
            "libpq5:amd64: /usr/lib/libpq.so.5\n",
            "libpq5:amd64\t17.11-0+deb13u1\t\t",
        ]
    )
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(replies)),
    )
    with pytest.raises(RuntimeError, match="incomplete Debian package identity"):
        library_receipt({"libpq.so.5": "/usr/lib/libpq.so.5"})


@pytest.mark.parametrize(
    ("control", "detail"),
    [
        ("wrong-host", 'server certificate does not match host name "wrong.invalid"'),
        ("untrusted-ca", "SSL error: certificate verify failed"),
    ],
)
def test_certificate_refusals_have_expected_reason(control: str, detail: str) -> None:
    assert classify_tls_refusal(control, RuntimeError(detail)) == control


@pytest.mark.parametrize(
    ("control", "detail"),
    [
        ("wrong-host", "could not translate host name wrong.invalid"),
        ("wrong-host", "connection timed out"),
        ("wrong-host", "certificate verify failed"),
        ("untrusted-ca", "connection refused"),
        ("untrusted-ca", "server certificate does not match host name"),
    ],
)
def test_other_failures_cannot_pass_as_certificate_refusals(
    control: str, detail: str
) -> None:
    with pytest.raises(RuntimeError, match="certificate"):
        classify_tls_refusal(control, RuntimeError(detail))


def test_refusal_failure_does_not_expose_connection_detail() -> None:
    private_detail = "connection timed out password=synthetic-secret"
    with pytest.raises(RuntimeError) as failure:
        classify_tls_refusal("wrong-host", RuntimeError(private_detail))
    assert "synthetic-secret" not in str(failure.value)
    assert failure.value.__cause__ is None


def test_accepted_negative_peer_fails_and_is_closed() -> None:
    class AcceptedPeer:
        closed = False

        def close(self) -> None:
            self.closed = True

    peer = AcceptedPeer()
    with pytest.raises(RuntimeError, match="peer was accepted"):
        require_tls_refusal(lambda **_args: peer, "wrong-host", {})
    assert peer.closed
