"""Behavior tests for final-image Python package verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_final_python_packages import required_piper_version, validate

PIPER_VERSION = required_piper_version()


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
            version=_versions({"msgpack": "1.2.2"}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )
    assert (
        validate(
            "venv",
            version=_versions(
                {"msgpack": "1.2.2", "setuptools": "84.0.0", "piper-tts": PIPER_VERSION}
            ),
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
            {"msgpack": "1.2.2", "setuptools": "70.3.0"},
            "global: setuptools is 70.3.0",
        ),
        (
            "venv",
            {"msgpack": "1.1.2", "setuptools": "84.0.0"},
            "venv: msgpack is 1.1.2",
        ),
        (
            "venv",
            {"msgpack": "1.2.2", "setuptools": "70.3.0"},
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
        version=_versions(
            {"msgpack": "1.2.2", "setuptools": "84.0.0", "piper-tts": PIPER_VERSION}
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert any("forbidden stale metadata present" in error for error in errors)


@pytest.mark.parametrize("piper_version", [None, "1.6.0"])
def test_final_package_verifier_rejects_missing_or_overridden_piper(
    tmp_path: Path, piper_version: str | None
) -> None:
    installed = {"msgpack": "1.2.2", "setuptools": "84.0.0"}
    if piper_version is not None:
        installed["piper-tts"] = piper_version
    assert validate(
        "venv",
        version=_versions(installed),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    ) == [f"venv: piper-tts is {piper_version or 'absent'}; expected {PIPER_VERSION}"]


def test_final_package_verifier_follows_canonical_piper_pin(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("piper-tts==2.0.0 # updated canonical pin\n")
    assert (
        validate(
            "venv",
            version=_versions(
                {"msgpack": "1.2.2", "setuptools": "84.0.0", "piper-tts": "2.0.0"}
            ),
            package_not_found=MissingPackage,
            purelib=tmp_path,
            requirements=requirements,
        )
        == []
    )


@pytest.mark.parametrize(
    "contents",
    ["", "piper-tts>=1.7.0\n", "piper-tts==1.7.0\npiper-tts==1.6.0\n"],
)
def test_final_package_verifier_rejects_ambiguous_piper_requirement(
    tmp_path: Path, contents: str
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(contents)
    errors = validate(
        "venv",
        version=_versions({"msgpack": "1.2.2", "setuptools": "84.0.0"}),
        package_not_found=MissingPackage,
        purelib=tmp_path,
        requirements=requirements,
    )
    assert len(errors) == 1
    assert "exactly one exact piper-tts pin" in errors[0]


def test_dockerfile_installs_piper_only_through_requirements() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    dependency_install = dockerfile.read_text().split("# Pa11y needs Node", 1)[0]
    assert "pip install --no-cache-dir -r requirements.txt" in dependency_install
    assert "piper-tts" not in dependency_install
