"""Behavior tests for governed Trivy vulnerability exemptions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verify_trivy_allowlist.py"


def _validate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(path)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_allowlist_accepts_governed_cve_and_ghsa_entries(
    tmp_path: Path,
) -> None:
    comments_only = tmp_path / "comments-only"
    comments_only.write_text("# No exemptions approved.\n\n")
    valid = tmp_path / "valid"
    valid.write_text(
        "# owner: security@example.com\n"
        "# justification: Upstream fix is not yet released.\n"
        "# expires: 2999-12-31\n"
        "CVE-2026-12345\n"
        "# owner: security@example.com\n"
        "# justification: Scanner reports a removed lower layer.\n"
        "# expires: 2999-12-31\n"
        "GHSA-6v7p-g79w-8964\n"
    )

    assert _validate(comments_only).returncode == 0
    assert _validate(valid).returncode == 0


def test_allowlist_rejects_ghsa_outside_github_alphabet(tmp_path: Path) -> None:
    allowlist = tmp_path / "invalid-ghsa-alphabet"
    allowlist.write_text(
        "# owner: security@example.com\n"
        "# justification: Temporary exemption.\n"
        "# expires: 2999-12-31\n"
        "GHSA-abcd-efgh-ijkl\n"
    )

    result = _validate(allowlist)

    assert result.returncode != 0
    assert "malformed" in result.stderr.lower()


def test_allowlist_rejects_malformed_and_expired_entries(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed"
    malformed.write_text(
        "# owner: security@example.com\n"
        "# justification: Temporary exemption.\n"
        "# expires: 2999-12-31\n"
        "GHSA-abcd-efgh-ijk\n"
    )
    expired = tmp_path / "expired"
    expired.write_text(
        "# owner: security@example.com\n"
        "# justification: Temporary exemption.\n"
        "# expires: 2000-01-01\n"
        "CVE-2020-1234\n"
    )

    malformed_result = _validate(malformed)
    expired_result = _validate(expired)
    assert malformed_result.returncode != 0
    assert "malformed" in malformed_result.stderr.lower()
    assert expired_result.returncode != 0
    assert "expired" in expired_result.stderr.lower()


@pytest.mark.parametrize("interruption", ["\n", "# unrelated comment\n"])
def test_allowlist_rejects_interrupted_metadata_record(
    tmp_path: Path, interruption: str
) -> None:
    allowlist = tmp_path / "interrupted"
    allowlist.write_text(
        "# owner: security@example.com\n"
        f"{interruption}"
        "# justification: Upstream fix is not yet released.\n"
        "# expires: 2999-12-31\n"
        "CVE-2026-12345\n"
    )

    result = _validate(allowlist)

    assert result.returncode != 0
    assert "metadata is not followed by a vulnerability exemption" in result.stderr


@pytest.mark.parametrize("vulnerability_id", ["CVE-2026-12345", "GHSA-6v7p-g79w-8964"])
def test_allowlist_rejects_duplicate_vulnerability_id(
    tmp_path: Path, vulnerability_id: str
) -> None:
    metadata = (
        "# owner: security@example.com\n"
        "# justification: Upstream fix is not yet released.\n"
        "# expires: 2999-12-31\n"
    )
    allowlist = tmp_path / "duplicate"
    allowlist.write_text(
        f"{metadata}{vulnerability_id}\n{metadata}{vulnerability_id}\n"
    )

    result = _validate(allowlist)

    assert result.returncode != 0
    assert "duplicate vulnerability exemption" in result.stderr
