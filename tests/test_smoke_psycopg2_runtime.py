"""Offline checks for the final-image psycopg2 smoke probe's library gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.smoke_psycopg2_runtime import (
    check_loaded_libraries,
    mapped_libraries,
)

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
