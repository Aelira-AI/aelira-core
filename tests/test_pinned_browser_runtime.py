"""The scanner's browser and Node runtime are pinned, hash-verified and patched."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path

import pytest

from scripts import install_chrome_for_testing as cft
from src.security.browser_runtime import (
    CHROMIUM_EXECUTABLE_ENV,
    chromium_launch_options,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "scripts" / "chrome-for-testing.json").read_text())
# Chrome stable 156.0.8078.47 (2026-10-20) is the first build with the fixes
# for the CVEs published that day. Playwright's 1.64.x bundles 156.0.8078.4.
PATCHED_CHROME = (156, 0, 8078, 47)
# Node 24.22.1 (2026-10-21) is the security release; its index digest below
# carries NODE_VERSION=24.22.1 for linux/amd64 and linux/arm64.
NODE_DIGEST = "sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205"
SUPERSEDED_NODE_DIGEST = (
    "sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6"
)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_pinned_chrome_is_patched_and_covers_both_image_architectures() -> None:
    assert _version(MANIFEST["version"]) >= PATCHED_CHROME
    architectures = {entry["docker_arch"] for entry in MANIFEST["platforms"].values()}
    assert architectures == {"amd64", "arm64"}
    for name, entry in MANIFEST["platforms"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), name
        assert entry["size"] > 100_000_000, name
        assert entry["archive"] == f"chrome-{name}.zip"
    assert MANIFEST["base_url"].startswith("https://")


@pytest.mark.parametrize(
    ("host", "platform_name"),
    [
        ("amd64", "linux64"),
        ("x86_64", "linux64"),
        ("arm64", "linux-arm64"),
        ("aarch64", "linux-arm64"),
    ],
)
def test_platform_selection_follows_the_image_architecture(
    host: str, platform_name: str
) -> None:
    assert cft.select_platform(MANIFEST, host)[0] == platform_name


def test_unknown_architecture_has_no_pinned_build() -> None:
    with pytest.raises(RuntimeError, match="No pinned Chrome for Testing build"):
        cft.select_platform(MANIFEST, "riscv64")


def test_archive_url_names_the_pinned_version_and_platform() -> None:
    name, entry = cft.select_platform(MANIFEST, "arm64")

    assert cft.archive_url(MANIFEST, name, entry) == (
        "https://storage.googleapis.com/chrome-for-testing-public/"
        f"{MANIFEST['version']}/linux-arm64/chrome-linux-arm64.zip"
    )


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _entry(payload: bytes) -> dict:
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def test_download_that_matches_the_pin_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"archive bytes"
    monkeypatch.setattr(
        cft.urllib.request, "urlopen", lambda *_a, **_k: _Response(payload)
    )
    target = tmp_path / "chrome.zip"

    cft.download_verified("https://example.invalid/c.zip", _entry(payload), target)

    assert target.read_bytes() == payload


@pytest.mark.parametrize("served", [b"tampered bytes", b"archive byte", b""])
def test_download_that_differs_from_the_pin_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: bytes
) -> None:
    monkeypatch.setattr(
        cft.urllib.request, "urlopen", lambda *_a, **_k: _Response(served)
    )

    with pytest.raises(RuntimeError, match="does not match the pin"):
        cft.download_verified(
            "https://example.invalid/c.zip", _entry(b"archive bytes"), tmp_path / "x"
        )


def _zip(tmp_path: Path, files: dict[str, tuple[bytes, int]]) -> Path:
    archive = tmp_path / "chrome.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, (data, mode) in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 19, 16, 11, 44))
            info.external_attr = mode << 16
            bundle.writestr(info, data)
    return archive


def test_extract_strips_the_top_directory_and_keeps_the_executable_bit(
    tmp_path: Path,
) -> None:
    archive = _zip(
        tmp_path,
        {
            "chrome-linux64/chrome": (b"\x7fELF", 0o100755),
            "chrome-linux64/resources.pak": (b"data", 0o100644),
            "chrome-linux64/locales/en-US.pak": (b"data", 0o100600),
        },
    )
    dest = tmp_path / "out"

    cft.extract(archive, dest)

    assert (dest / "chrome").stat().st_mode & stat.S_IXUSR
    assert not (dest / "resources.pak").stat().st_mode & stat.S_IXUSR
    assert (dest / "locales" / "en-US.pak").stat().st_mode & 0o777 == 0o644
    assert int((dest / "chrome").stat().st_mtime) == 1_792_426_304


def test_extract_refuses_entries_that_escape_the_destination(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        {
            "chrome-linux64/chrome": (b"x", 0o100755),
            "chrome-linux64/../../escaped": (b"x", 0o100644),
        },
    )

    with pytest.raises(RuntimeError, match="escapes the destination"):
        cft.extract(archive, tmp_path / "out")
    assert not (tmp_path / "escaped").exists()


def test_extract_refuses_symlinks(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        {
            "chrome-linux64/chrome": (b"x", 0o100755),
            "chrome-linux64/link": (b"/etc/passwd", 0o120777),
        },
    )

    with pytest.raises(RuntimeError, match="Unexpected symlink"):
        cft.extract(archive, tmp_path / "out")


def test_extract_requires_an_executable_chrome(tmp_path: Path) -> None:
    archive = _zip(tmp_path, {"chrome-linux64/other": (b"x", 0o100755)})

    with pytest.raises(RuntimeError, match="missing or not executable"):
        cft.extract(archive, tmp_path / "out")


def test_unset_executable_leaves_playwright_on_its_own_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CHROMIUM_EXECUTABLE_ENV, raising=False)

    assert chromium_launch_options() == {}


def test_configured_executable_is_passed_to_playwright(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chrome = tmp_path / "chrome"
    chrome.write_text("")
    monkeypatch.setenv(CHROMIUM_EXECUTABLE_ENV, str(chrome))

    assert chromium_launch_options() == {"executable_path": str(chrome)}


def test_missing_configured_executable_is_an_error_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CHROMIUM_EXECUTABLE_ENV, str(tmp_path / "absent"))

    with pytest.raises(RuntimeError, match="does not point to a file"):
        chromium_launch_options()


def test_every_scanner_browser_launch_uses_the_pinned_executable() -> None:
    launches = 0
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        for match in re.finditer(r"chromium\.launch\(", text):
            launches += 1
            call = text[match.start() : match.start() + 400]
            assert (
                "chromium_launch_options()" in call or "executable_path" in call
            ), f"{path.relative_to(ROOT)} launches Chromium without the pinned build"
    assert launches >= 7


@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.dev"])
def test_images_run_the_pinned_chrome_and_not_playwrights_chromium(name: str) -> None:
    dockerfile = (ROOT / name).read_text()

    assert "AS chrome-for-testing" in dockerfile
    assert "scripts/install_chrome_for_testing.py" in dockerfile
    assert "COPY --from=chrome-for-testing /opt/chrome-for-testing" in dockerfile
    assert "ENV AELIRA_CHROMIUM_EXECUTABLE=/opt/chrome-for-testing/chrome" in dockerfile
    assert "playwright install" not in dockerfile
    assert "PLAYWRIGHT_BROWSERS_PATH" not in dockerfile


@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.dev"])
def test_pa11y_node_base_is_the_current_security_release(name: str) -> None:
    dockerfile = (ROOT / name).read_text()

    assert f"FROM node:24-bookworm-slim@{NODE_DIGEST} AS pa11y-node" in dockerfile
    assert SUPERSEDED_NODE_DIGEST not in dockerfile


def test_node_trust_store_is_checked_in_every_api_image() -> None:
    production = (ROOT / "Dockerfile").read_text()
    development = (ROOT / "Dockerfile.dev").read_text()

    # The production build runs every check; the developer build names them.
    assert production.count("verify_final_os_packages.py\n") >= 1
    assert "verify_final_os_packages.py node-trust-store" in development


def test_smoke_gate_requires_patched_node_and_the_pinned_browser() -> None:
    smoke = (ROOT / "scripts" / "smoke_pa11y_runtime.py").read_text()

    assert "MINIMUM_NODE = (24, 22, 1)" in smoke
    assert "MINIMUM_NODE_OPENSSL = (3, 5, 9)" in smoke
    assert "chrome-for-testing.json" in smoke
    assert "launched_version != _pinned_chrome_version()" in smoke


def test_release_smoke_runs_the_pinned_chrome() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    job = workflow.split("  e2e-release:", 1)[1].split("\n  user-journeys:", 1)[0]

    assert "install_chrome_for_testing.py" in job
    assert "AELIRA_CHROMIUM_EXECUTABLE" in job
    assert "playwright install --with-deps chromium" not in job
    for config in ("release", "remediation-policy"):
        text = (ROOT / "dashboard" / f"playwright.{config}.config.ts").read_text()
        assert "AELIRA_CHROMIUM_EXECUTABLE" in text
