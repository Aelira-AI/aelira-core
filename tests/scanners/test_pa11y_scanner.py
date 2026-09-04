from __future__ import annotations

import asyncio

import pytest

from src.scanners.pa11y_scanner import Pa11yScanner


class _CompletedProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"[]", b""


@pytest.mark.asyncio
async def test_scan_passes_explicit_config_through_argument_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    async def fake_create_subprocess_exec(
        *args: str, **_kwargs: object
    ) -> _CompletedProcess:
        captured.extend(args)
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await Pa11yScanner(
        timeout=12,
        pa11y_bin="/usr/local/bin/pa11y",
        config_path="/app/config/pa11y.json",
    ).scan("https://example.test", runner="axe")

    assert result.total_issues == 0
    assert captured == [
        "/usr/local/bin/pa11y",
        "--config",
        "/app/config/pa11y.json",
        "--reporter",
        "json",
        "--runner",
        "axe",
        "--standard",
        "WCAG2AA",
        "--timeout",
        "12000",
        "https://example.test",
    ]
