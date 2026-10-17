"""Behavior tests for final-image Python package verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_final_python_packages import (
    required_msgpack_version,
    required_piper_version,
    required_urllib3_version,
    validate,
    validate_pillow,
)

PIPER_VERSION = required_piper_version()
MSGPACK_VERSION = required_msgpack_version()
URLLIB3_VERSION = required_urllib3_version()


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
            version=_versions({"msgpack": MSGPACK_VERSION}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )
    assert (
        validate(
            "venv",
            version=_versions(
                {
                    "msgpack": MSGPACK_VERSION,
                    "setuptools": "84.0.0",
                    "piper-tts": PIPER_VERSION,
                    "urllib3": URLLIB3_VERSION,
                }
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
            {"msgpack": MSGPACK_VERSION, "pip": "26.2.1"},
            "global: pip is 26.2.1; expected absent",
        ),
        (
            "global",
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "70.3.0",
                "urllib3": URLLIB3_VERSION,
            },
            "global: setuptools is 70.3.0",
        ),
        (
            "venv",
            {"msgpack": "1.1.2", "setuptools": "84.0.0"},
            "venv: msgpack is 1.1.2",
        ),
        (
            "venv",
            {"msgpack": MSGPACK_VERSION, "setuptools": "70.3.0"},
            "venv: setuptools is 70.3.0",
        ),
        (
            "venv",
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.0.0",
                "urllib3": "2.7.0",
                "piper-tts": PIPER_VERSION,
            },
            "venv: urllib3 is 2.7.0; expected 2.8.0",
        ),
        (
            "venv",
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.0.0",
                "urllib3": URLLIB3_VERSION,
                "piper-tts": PIPER_VERSION,
                "pip": "26.2.1",
            },
            "venv: pip is 26.2.1; expected absent",
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
    [
        "msgpack-1.1.2.dist-info",
        "setuptools-70.3.0.egg-info",
        "urllib3-2.7.0.dist-info",
    ],
)
def test_final_package_verifier_rejects_stale_metadata_names(
    tmp_path: Path, metadata_name: str
) -> None:
    (tmp_path / metadata_name).mkdir()

    errors = validate(
        "venv",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.0.0",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert any("forbidden stale metadata present" in error for error in errors)


def test_final_package_verifier_rejects_pip_vendor_tree(tmp_path: Path) -> None:
    (tmp_path / "pip" / "_vendor" / "urllib3").mkdir(parents=True)
    errors = validate(
        "global",
        version=_versions({"msgpack": MSGPACK_VERSION}),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )
    assert any("pip-vendored urllib3 remains" in error for error in errors)


def test_final_package_verifier_follows_canonical_urllib3_pin(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        f"msgpack=={MSGPACK_VERSION}\npiper-tts=={PIPER_VERSION}\nurllib3==2.8.1\n"
    )
    installed = {
        "msgpack": MSGPACK_VERSION,
        "setuptools": "84.0.0",
        "piper-tts": PIPER_VERSION,
        "urllib3": "2.8.1",
    }
    assert (
        validate(
            "venv",
            version=_versions(installed),
            package_not_found=MissingPackage,
            purelib=tmp_path,
            requirements=requirements,
        )
        == []
    )
    installed["urllib3"] = URLLIB3_VERSION
    assert any(
        "urllib3 is 2.8.0; expected 2.8.1" in error
        for error in validate(
            "venv",
            version=_versions(installed),
            package_not_found=MissingPackage,
            purelib=tmp_path,
            requirements=requirements,
        )
    )


@pytest.mark.parametrize("piper_version", [None, "1.6.0"])
def test_final_package_verifier_rejects_missing_or_overridden_piper(
    tmp_path: Path, piper_version: str | None
) -> None:
    installed = {
        "msgpack": MSGPACK_VERSION,
        "setuptools": "84.0.0",
        "urllib3": URLLIB3_VERSION,
    }
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
    requirements.write_text(
        f"msgpack=={MSGPACK_VERSION}\nurllib3=={URLLIB3_VERSION}\npiper-tts==2.0.0 # updated canonical pin\n"
    )
    assert (
        validate(
            "venv",
            version=_versions(
                {
                    "msgpack": MSGPACK_VERSION,
                    "setuptools": "84.0.0",
                    "piper-tts": "2.0.0",
                    "urllib3": URLLIB3_VERSION,
                }
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
    requirements.write_text(
        f"msgpack=={MSGPACK_VERSION}\nurllib3=={URLLIB3_VERSION}\n{contents}"
    )
    errors = validate(
        "venv",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.0.0",
                "urllib3": URLLIB3_VERSION,
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
        requirements=requirements,
    )
    assert len(errors) == 1
    assert "exactly one exact piper-tts pin" in errors[0]


def test_final_package_verifier_follows_canonical_msgpack_pin(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("msgpack==1.2.4 # reviewed pin\npiper-tts==1.8.0\n")
    assert (
        validate(
            "global",
            version=_versions({"msgpack": "1.2.4"}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
            requirements=requirements,
        )
        == []
    )
    assert any(
        "msgpack is 1.2.2; expected 1.2.4" in error
        for error in validate(
            "global",
            version=_versions({"msgpack": "1.2.2"}),
            package_not_found=MissingPackage,
            purelib=tmp_path,
            requirements=requirements,
        )
    )


@pytest.mark.parametrize(
    "contents",
    ["", "msgpack>=1.2.3\n", "msgpack==1.2.2\nmsgpack==1.2.3\n"],
)
def test_final_package_verifier_rejects_ambiguous_msgpack_requirement(
    tmp_path: Path, contents: str
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(contents)
    errors = validate(
        "global",
        version=_versions({"msgpack": MSGPACK_VERSION}),
        package_not_found=MissingPackage,
        purelib=tmp_path,
        requirements=requirements,
    )
    assert len(errors) == 1
    assert "exactly one exact msgpack pin" in errors[0]


def test_dockerfile_installs_piper_only_through_requirements() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    dependency_install = dockerfile.read_text().split("# Pa11y needs Node", 1)[0]
    assert "pip install --no-cache-dir -r requirements.txt" in dependency_install
    assert "piper-tts" not in dependency_install


class FakeFeatures:
    """Stand-in for PIL.features with a controllable libtiff and feature set."""

    def __init__(self, libtiff: str = "4.7.2", missing: tuple[str, ...] = ()) -> None:
        self.libtiff = libtiff
        self.missing = missing

    def version(self, name: str) -> str:
        assert name == "libtiff"
        return self.libtiff

    def check(self, name: str) -> bool:
        return name not in self.missing


def _pillow_tree(
    root: Path, extension_bytes: bytes = b"LIBTIFF, Version 4.7.2"
) -> None:
    package = root / "PIL"
    package.mkdir()
    (package / "_imaging.cpython-314-x86_64-linux-gnu.so").write_bytes(extension_bytes)


def test_final_package_verifier_accepts_source_built_pillow(tmp_path: Path) -> None:
    _pillow_tree(tmp_path)

    assert validate_pillow(features=FakeFeatures(), purelib=tmp_path) == []


def test_final_package_verifier_rejects_the_wheel_bundled_libtiff(
    tmp_path: Path,
) -> None:
    _pillow_tree(tmp_path)

    errors = validate_pillow(features=FakeFeatures("4.7.1"), purelib=tmp_path)

    assert any("Pillow libtiff is 4.7.1" in error for error in errors)


def test_final_package_verifier_rejects_the_wheel_private_library_directory(
    tmp_path: Path,
) -> None:
    _pillow_tree(tmp_path)
    (tmp_path / "pillow.libs").mkdir()

    errors = validate_pillow(features=FakeFeatures(), purelib=tmp_path)

    assert any("wheel-private library directory" in error for error in errors)


def test_final_package_verifier_rejects_a_shared_libtiff_dependency(
    tmp_path: Path,
) -> None:
    _pillow_tree(tmp_path, b"\x00libtiff.so.6\x00")

    errors = validate_pillow(features=FakeFeatures(), purelib=tmp_path)

    assert any("links a shared libtiff" in error for error in errors)


@pytest.mark.parametrize(
    "feature",
    [
        "jpg",
        "jpg_2000",
        "zlib",
        "libtiff",
        "webp",
        "avif",
        "freetype2",
        "littlecms2",
        "xcb",
    ],
)
def test_final_package_verifier_requires_every_wheel_feature(
    tmp_path: Path, feature: str
) -> None:
    _pillow_tree(tmp_path)

    errors = validate_pillow(
        features=FakeFeatures(missing=(feature,)), purelib=tmp_path
    )

    assert f"venv: Pillow feature {feature} is unavailable" in errors


@pytest.mark.parametrize("package", ["pi-heif", "pybind11"])
def test_final_package_verifier_rejects_packages_the_image_must_not_ship(
    tmp_path: Path, package: str
) -> None:
    errors = validate(
        "venv",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.0.0",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
                package: "1.0.0",
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert f"venv: {package} is 1.0.0; expected absent" in errors
