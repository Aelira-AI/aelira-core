"""The build-input cache lists, keys and prefetches exactly the pinned downloads."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import build_inputs
from scripts.build_inputs import Pin
from scripts.download_piper_voice import VOICE_ASSETS

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "scripts" / "chrome-for-testing.json").read_text())
HEX64 = re.compile(r"[0-9a-f]{64}")


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These standalone build helpers need no application settings or database."""
    yield


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chrome_digest(arch: str) -> str:
    return next(
        entry["sha256"]
        for entry in MANIFEST["platforms"].values()
        if entry["docker_arch"] == arch
    )


def _builder_fetches(name: str) -> list[tuple[str, str]]:
    """The (url, sha256) of each `fetch https://...` call, read without the tool."""
    text = (ROOT / name).read_text().replace("\\\n", " ")
    return re.findall(r"^fetch\s+(https://\S+)\s+([0-9a-f]{64})\s", text, re.MULTILINE)


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_pins_are_unique_digests_with_https_addresses(arch: str) -> None:
    found = build_inputs.pins(arch)

    digests = [pin.sha256 for pin in found]
    assert len(digests) == len(set(digests))
    assert digests == sorted(digests)
    for pin in found:
        assert HEX64.fullmatch(pin.sha256), pin
        assert pin.urls and all(url.startswith("https://") for url in pin.urls), pin


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_pins_cover_every_source_builder_fetch(arch: str) -> None:
    by_digest = {pin.sha256: pin for pin in build_inputs.pins(arch)}

    total = 0
    for name in build_inputs.SOURCE_BUILDERS:
        fetches = _builder_fetches(name)
        assert fetches, f"{name} has no fetch calls"
        for url, sha256 in fetches:
            total += 1
            assert url in by_digest[sha256].urls, (name, url)
    assert total >= 12


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_pins_include_the_architectures_chrome_and_both_piper_assets(
    arch: str,
) -> None:
    by_digest = {pin.sha256: pin for pin in build_inputs.pins(arch)}

    chrome = by_digest[_chrome_digest(arch)]
    assert chrome.name.startswith("chrome-") and chrome.name.endswith(".zip")
    assert chrome.urls[0].startswith(MANIFEST["base_url"])
    assert len(VOICE_ASSETS) == 2
    for filename, url, sha256 in VOICE_ASSETS:
        assert by_digest[sha256].urls == (url,)
        assert by_digest[sha256].name == filename


def test_each_architecture_holds_only_its_own_chrome() -> None:
    amd64 = {pin.sha256 for pin in build_inputs.pins("amd64")}
    arm64 = {pin.sha256 for pin in build_inputs.pins("arm64")}

    assert _chrome_digest("amd64") in amd64
    assert _chrome_digest("amd64") not in arm64
    assert _chrome_digest("arm64") in arm64
    assert _chrome_digest("arm64") not in amd64
    # Everything else is shared.
    assert amd64 - {_chrome_digest("amd64")} == arm64 - {_chrome_digest("arm64")}


def test_unsupported_architecture_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported architecture"):
        build_inputs.pins("riscv64")


def test_every_script_using_the_fetch_helper_is_a_listed_source_builder() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "docker/*.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # Untracked scripts are still part of the change under test.
    users = {
        name
        for name in {
            *tracked,
            *(str(p.relative_to(ROOT)) for p in ROOT.glob("docker/*.sh")),
        }
        if re.search(
            r"^\s*\.\s+.*fetch-pinned\.sh", (ROOT / name).read_text(), re.MULTILINE
        )
    }

    assert users == set(build_inputs.SOURCE_BUILDERS)


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "docker" / "build-fixture.sh"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    return path


def test_builder_pins_join_continuations_and_keep_the_fallback_address(
    tmp_path: Path,
) -> None:
    first, second = "a" * 64, "b" * 64
    path = _script(
        tmp_path,
        f"""\
echo fetch https://not.a.pin/ignored {first} nothing
# fetch https://commented.example/out.tar {first} out.tar
fetch https://primary.example/one.tar.gz \\
    {first} \\
    work/one.tar.gz
    fetch https://zlib.example/zlib-1.3.2.tar.gz \\
    {second} \\
    work/zlib.tar.gz \\
    https://mirror.example/v1.3.2/zlib-1.3.2.tar.gz
""",
    )

    found = build_inputs.builder_pins(path, root=tmp_path)

    assert found == [
        Pin(
            first,
            ("https://primary.example/one.tar.gz",),
            "one.tar.gz",
            "docker/build-fixture.sh",
        ),
        Pin(
            second,
            (
                "https://zlib.example/zlib-1.3.2.tar.gz",
                "https://mirror.example/v1.3.2/zlib-1.3.2.tar.gz",
            ),
            "zlib-1.3.2.tar.gz",
            "docker/build-fixture.sh",
        ),
    ]


def test_builder_pins_drop_the_query_string_from_the_name(tmp_path: Path) -> None:
    path = _script(
        tmp_path, f"fetch https://host.example/f.tar?download=true {'c' * 64} f.tar\n"
    )

    assert build_inputs.builder_pins(path, root=tmp_path)[0].name == "f.tar"


@pytest.mark.parametrize("digest", ["not-a-hex-pin", "A" * 64, "a" * 63, "g" * 64])
def test_builder_pins_reject_a_pin_that_is_not_lowercase_sha256(
    tmp_path: Path, digest: str
) -> None:
    path = _script(tmp_path, f"fetch https://host.example/f.tar {digest} f.tar\n")

    with pytest.raises(ValueError, match="not a SHA-256"):
        build_inputs.builder_pins(path, root=tmp_path)


@pytest.mark.parametrize("scheme", ["http", "ftp"])
def test_builder_pins_reject_a_non_https_address(tmp_path: Path, scheme: str) -> None:
    path = _script(tmp_path, f"fetch {scheme}://host.example/f.tar {'a' * 64} f.tar\n")

    with pytest.raises(ValueError, match="not HTTPS"):
        build_inputs.builder_pins(path, root=tmp_path)


def test_builder_pins_reject_a_non_https_fallback(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        f"fetch https://host.example/f.tar {'a' * 64} f.tar http://mirror.example/f.tar\n",
    )

    with pytest.raises(ValueError, match="not HTTPS"):
        build_inputs.builder_pins(path, root=tmp_path)


def _fake_pins(monkeypatch: pytest.MonkeyPatch, *pins: Pin) -> None:
    monkeypatch.setattr(build_inputs, "pins", lambda _arch: list(pins))


def test_cache_key_is_stable_and_names_the_architecture() -> None:
    first = build_inputs.cache_key("amd64")

    assert first == build_inputs.cache_key("amd64")
    assert re.fullmatch(r"build-inputs-v1-amd64-[0-9a-f]{64}", first)
    assert build_inputs.cache_key("arm64").startswith("build-inputs-v1-arm64-")
    assert build_inputs.cache_key("arm64") != first


def test_cache_key_changes_with_the_pin_set(monkeypatch: pytest.MonkeyPatch) -> None:
    one = Pin(_digest(b"one"), ("https://x.example/one",), "one", "s")
    two = Pin(_digest(b"two"), ("https://x.example/two",), "two", "s")

    _fake_pins(monkeypatch, one)
    only_one = build_inputs.cache_key("amd64")
    _fake_pins(monkeypatch, one, two)
    both = build_inputs.cache_key("amd64")
    # Only the digests are keyed, so a mirror or name change reuses the cache.
    _fake_pins(monkeypatch, Pin(one.sha256, ("https://y.example/other",), "o", "t"))
    renamed = build_inputs.cache_key("amd64")

    assert only_one != both
    assert renamed == only_one


def _pin(data: bytes) -> Pin:
    return Pin(_digest(data), (f"https://x.example/{_digest(data)[:6]}",), "f", "s")


def _writer(payloads: dict[str, bytes], calls: list[str]):
    def fetch(pin: Pin, target: Path) -> bool:
        calls.append(pin.sha256)
        target.write_bytes(payloads[pin.sha256])
        return True

    return fetch


def test_prefetch_fetches_missing_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = b"alpha", b"beta"
    _fake_pins(monkeypatch, _pin(a), _pin(b))
    calls: list[str] = []

    counts = build_inputs.prefetch(
        tmp_path / "cache",
        "amd64",
        fetch=_writer({_digest(a): a, _digest(b): b}, calls),
    )

    store = tmp_path / "cache" / "sha256"
    assert (store / _digest(a)).read_bytes() == a
    assert (store / _digest(b)).read_bytes() == b
    assert sorted(calls) == sorted([_digest(a), _digest(b)])
    assert counts == {"kept": 0, "fetched": 2, "failed": 0, "removed": 0}


def test_prefetch_keeps_valid_cached_files_without_fetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = b"already here"
    _fake_pins(monkeypatch, _pin(data))
    store = tmp_path / "sha256"
    store.mkdir()
    (store / _digest(data)).write_bytes(data)

    def fetch(*_args: object) -> bool:
        raise AssertionError("a valid cached file must not be fetched again")

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=fetch)

    assert (store / _digest(data)).read_bytes() == data
    assert counts == {"kept": 1, "fetched": 0, "failed": 0, "removed": 0}


def test_prefetch_replaces_a_cached_file_whose_bytes_do_not_match_its_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = b"the real bytes"
    _fake_pins(monkeypatch, _pin(data))
    store = tmp_path / "sha256"
    store.mkdir()
    (store / _digest(data)).write_bytes(b"corrupted on disk")
    calls: list[str] = []

    counts = build_inputs.prefetch(
        tmp_path, "amd64", fetch=_writer({_digest(data): data}, calls)
    )

    assert (store / _digest(data)).read_bytes() == data
    assert calls == [_digest(data)]
    assert counts == {"kept": 0, "fetched": 1, "failed": 0, "removed": 1}


def test_prefetch_removes_stale_files_symlinks_and_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = b"current"
    _fake_pins(monkeypatch, _pin(data))
    store = tmp_path / "sha256"
    store.mkdir()
    (store / _digest(data)).write_bytes(data)
    (store / _digest(b"retired pin")).write_bytes(b"retired pin")
    (store / ".fetch-leftover").mkdir()
    (store / ".fetch-leftover" / "partial").write_bytes(b"half")
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    (store / "link").symlink_to(outside)
    (store / "dirlink").symlink_to(tmp_path)

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=lambda *_a: False)

    assert [entry.name for entry in store.iterdir()] == [_digest(data)]
    assert outside.read_bytes() == b"must survive"
    assert counts == {"kept": 1, "fetched": 0, "failed": 0, "removed": 4}


def test_prefetch_removes_a_symlink_named_like_a_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = b"payload"
    _fake_pins(monkeypatch, _pin(data))
    store = tmp_path / "sha256"
    store.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(data)
    (store / _digest(data)).symlink_to(outside)

    counts = build_inputs.prefetch(
        tmp_path, "amd64", fetch=_writer({_digest(data): data}, [])
    )

    target = store / _digest(data)
    assert target.is_file() and not target.is_symlink()
    assert counts["removed"] == 1 and counts["fetched"] == 1


def test_prefetch_counts_a_failed_fetch_and_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = b"unreachable"
    _fake_pins(monkeypatch, _pin(data))

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=lambda *_a: False)

    assert list((tmp_path / "sha256").iterdir()) == []
    assert counts == {"kept": 0, "fetched": 0, "failed": 1, "removed": 0}
    assert "not cached" in capsys.readouterr().err


def test_prefetch_counts_a_fetch_that_writes_wrong_bytes_as_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    good, bad = b"wanted", b"unwanted"
    _fake_pins(monkeypatch, _pin(good))

    def fetch(pin: Pin, target: Path) -> bool:
        target.write_bytes(bad)
        return True

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=fetch)

    assert list((tmp_path / "sha256").iterdir()) == []
    assert counts == {"kept": 0, "fetched": 0, "failed": 1, "removed": 0}


def test_prefetch_counts_a_fetch_that_claims_success_but_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_pins(monkeypatch, _pin(b"never written"))

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=lambda *_a: True)

    assert counts == {"kept": 0, "fetched": 0, "failed": 1, "removed": 0}


def test_prefetch_reports_a_mixed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kept, fetched, failed = b"kept", b"fetched", b"failed"
    _fake_pins(monkeypatch, _pin(kept), _pin(fetched), _pin(failed))
    store = tmp_path / "sha256"
    store.mkdir()
    (store / _digest(kept)).write_bytes(kept)
    (store / "stale").write_bytes(b"stale")

    def fetch(pin: Pin, target: Path) -> bool:
        if pin.sha256 == _digest(failed):
            return False
        target.write_bytes(fetched)
        return True

    counts = build_inputs.prefetch(tmp_path, "amd64", fetch=fetch)

    assert counts == {"kept": 1, "fetched": 1, "failed": 1, "removed": 1}
    assert sorted(entry.name for entry in store.iterdir()) == sorted(
        [_digest(kept), _digest(fetched)]
    )


@pytest.mark.parametrize(
    ("failed", "flag", "expected"),
    [(1, True, 3), (0, True, 0), (1, False, 0), (0, False, 0)],
)
def test_require_complete_makes_prefetch_exit_3_only_when_a_pin_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failed: int,
    flag: bool,
    expected: int,
) -> None:
    counts = {"kept": 1, "fetched": 2, "failed": failed, "removed": 0}
    monkeypatch.setattr(build_inputs, "prefetch", lambda *_a, **_k: counts)
    argv = ["prefetch", "--arch", "amd64", "--dest", str(tmp_path)]

    assert (
        build_inputs.main([*argv, "--require-complete"] if flag else argv) == expected
    )
    assert f"failed={failed}" in capsys.readouterr().out
