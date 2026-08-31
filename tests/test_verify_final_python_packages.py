"""Behavior tests for final-image Python package verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_final_python_packages import validate


class MissingPackage(Exception):
    """Test-only missing-package signal."""


def _versions(installed: dict[str, str]):
    def version(package: str) -> str:
        try:
            return installed[package]
        except KeyError as exc:
            raise MissingPackage(package) from exc

    return version


def test_final_package_verifier_accepts_reviewed_global_and_venv_state(
    tmp_path: Path,
) -> None:
    assert (
        validate(
            "global",
            version=_versions({"msgpack": "1.2.1"}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )
    assert (
        validate(
            "venv",
            version=_versions({"msgpack": "1.2.1", "setuptools": "84.0.0"}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )


@pytest.mark.parametrize(
    ("scope", "installed", "expected_error"),
    [
        ("global", {"msgpack": "1.1.2"}, "global: msgpack is 1.1.2"),
        (
            "global",
            {"msgpack": "1.2.1", "setuptools": "70.3.0"},
            "global: setuptools is 70.3.0",
        ),
        (
            "venv",
            {"msgpack": "1.1.2", "setuptools": "84.0.0"},
            "venv: msgpack is 1.1.2",
        ),
        (
            "venv",
            {"msgpack": "1.2.1", "setuptools": "70.3.0"},
            "venv: setuptools is 70.3.0",
        ),
    ],
)
def test_final_package_verifier_rejects_vulnerable_installed_versions(
    tmp_path: Path,
    scope: str,
    installed: dict[str, str],
    expected_error: str,
) -> None:
    errors = validate(
        scope,
        version=_versions(installed),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert any(error.startswith(expected_error) for error in errors)


@pytest.mark.parametrize(
    "metadata_name",
    ["msgpack-1.1.2.dist-info", "setuptools-70.3.0.egg-info"],
)
def test_final_package_verifier_rejects_stale_metadata_names(
    tmp_path: Path, metadata_name: str
) -> None:
    (tmp_path / metadata_name).mkdir()

    errors = validate(
        "venv",
        version=_versions({"msgpack": "1.2.1", "setuptools": "84.0.0"}),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert any("forbidden stale metadata present" in error for error in errors)
