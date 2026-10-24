"""Behavior tests for final-image Python package verification."""

from __future__ import annotations

from pathlib import Path
import base64
import csv
import hashlib
import importlib.metadata

import pytest

from scripts.verify_final_python_packages import (
    FFMPEG_CLI_CONFIGURATION,
    PYAV_LIBRARIES,
    required_opencv_version,
    required_msgpack_version,
    required_piper_version,
    required_pyav_version,
    required_psycopg2_version,
    required_urllib3_version,
    validate,
    validate_ffmpeg_cli,
    validate_opencv_native,
    validate_pillow,
    validate_pyav_native,
    validate_psycopg2_native,
    psycopg2_record_receipt,
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


def _installed_pip_vendor(root: Path) -> Path:
    (root / "pip-26.2.2.dist-info").mkdir()
    vendor = root / "pip" / "_vendor"
    urllib3 = vendor / "urllib3"
    urllib3.mkdir(parents=True)
    # Frozen independently from the published pip 26.2.2 wheel's vendor.txt.
    (vendor / "vendor.txt").write_text(
        "CacheControl==0.14.4\n"
        "distlib==0.4.2\n"
        "distro==1.9.0\n"
        "msgpack==1.1.2\n"
        "packaging==26.2\n"
        "platformdirs==4.10.0\n"
        "pyproject-hooks==1.2.0\n"
        "requests==2.34.2\n"
        "    certifi==2026.10.19\n"
        "    idna==3.18\n"
        "    urllib3==2.7.0\n"
        "rich==14.2.0\n"
        "    pygments==2.20.0\n"
        "resolvelib==1.2.1\n"
        "setuptools==70.3.0\n"
        "tomli==2.4.1\n"
        "tomli-w==1.2.0\n"
        "truststore==0.10.4\n"
    )
    (urllib3 / "__init__.py").write_text("from ._version import __version__\n")
    (urllib3 / "_version.py").write_text("__version__ = version = '2.7.0'\n")
    return vendor


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
                    "setuptools": "84.2.1",
                    "piper-tts": PIPER_VERSION,
                    "urllib3": URLLIB3_VERSION,
                }
            ),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )


def test_final_package_verifier_accepts_quickstart_with_pip(tmp_path: Path) -> None:
    _installed_pip_vendor(tmp_path)
    assert (
        validate(
            "quickstart",
            version=_versions(
                {
                    "msgpack": MSGPACK_VERSION,
                    "setuptools": "84.2.1",
                    "pip": "26.2.2",
                    "piper-tts": PIPER_VERSION,
                    "urllib3": URLLIB3_VERSION,
                }
            ),
            package_not_found=MissingPackage,
            purelib=tmp_path,
        )
        == []
    )


def test_final_package_verifier_rejects_wrong_quickstart_pip(tmp_path: Path) -> None:
    _installed_pip_vendor(tmp_path)
    errors = validate(
        "quickstart",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.2.1",
                "pip": "26.2.1",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )
    assert "quickstart: pip is 26.2.1; expected 26.2.2" in errors


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("missing_manifest", "unable to read pip vendor manifest"),
        ("old_manifest", "pip vendor manifest differs from pip 26.2.2"),
        ("old_certifi", "pip vendor manifest differs from pip 26.2.2"),
        ("extra_vendor", "pip vendor manifest differs from pip 26.2.2"),
        ("old_code", "pip-vendored urllib3 is not 2.7.0"),
        ("missing_code", "unable to read pip-vendored urllib3"),
    ],
)
def test_quickstart_refuses_unreviewed_pip_vendor_tree(
    tmp_path: Path, change: str, expected: str
) -> None:
    vendor = _installed_pip_vendor(tmp_path)
    manifest = vendor / "vendor.txt"
    code = vendor / "urllib3" / "_version.py"
    if change == "missing_manifest":
        manifest.unlink()
    elif change == "old_manifest":
        manifest.write_text(
            manifest.read_text().replace("urllib3==2.7.0", "urllib3==2.6.3")
        )
    elif change == "old_certifi":
        # pip 26.2.1 vendored certifi 2026.6.17, which trusts the emSign ECC
        # Root CA - G3 root (GHSA-6q48-6c7w-9gw2).
        manifest.write_text(
            manifest.read_text().replace("certifi==2026.10.19", "certifi==2026.6.17")
        )
    elif change == "extra_vendor":
        manifest.write_text(manifest.read_text() + "unreviewed==1.0.0\n")
    elif change == "old_code":
        code.write_text("__version__ = version = '2.6.3'\n")
    elif change == "missing_code":
        code.unlink()

    errors = validate(
        "quickstart",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.2.1",
                "pip": "26.2.2",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )
    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    ("scope", "installed", "expected_error"),
    [
        ("global", {"msgpack": "1.1.2"}, "global: msgpack is 1.1.2"),
        (
            "global",
            {"msgpack": MSGPACK_VERSION, "pip": "26.2.2"},
            "global: pip is 26.2.2; expected absent",
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
            {"msgpack": "1.1.2", "setuptools": "84.2.1"},
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
                "setuptools": "84.2.1",
                "urllib3": "2.7.0",
                "piper-tts": PIPER_VERSION,
            },
            "venv: urllib3 is 2.7.0; expected 2.8.0",
        ),
        (
            "venv",
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.2.1",
                "urllib3": URLLIB3_VERSION,
                "piper-tts": PIPER_VERSION,
                "pip": "26.2.2",
            },
            "venv: pip is 26.2.2; expected absent",
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
                "setuptools": "84.2.1",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert any("forbidden stale metadata present" in error for error in errors)


@pytest.mark.parametrize("scope", ["global", "venv"])
def test_final_package_verifier_rejects_pip_vendor_tree(
    tmp_path: Path, scope: str
) -> None:
    _installed_pip_vendor(tmp_path)
    installed = {"msgpack": MSGPACK_VERSION}
    if scope == "venv":
        installed.update(
            {
                "setuptools": "84.2.1",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
            }
        )
    errors = validate(
        scope,
        version=_versions(installed),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )
    assert any("pip-vendored urllib3 remains" in error for error in errors)


def test_quickstart_keeps_top_level_urllib3_guards(tmp_path: Path) -> None:
    _installed_pip_vendor(tmp_path)
    (tmp_path / "urllib3-2.7.0.dist-info").mkdir()
    errors = validate(
        "quickstart",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.2.1",
                "pip": "26.2.2",
                "piper-tts": PIPER_VERSION,
                "urllib3": "2.7.0",
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )
    assert "quickstart: urllib3 is 2.7.0; expected 2.8.0" in errors
    assert any("forbidden stale metadata present" in error for error in errors)
    assert not any("pip vendor manifest" in error for error in errors)


def test_final_package_verifier_follows_canonical_urllib3_pin(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        f"msgpack=={MSGPACK_VERSION}\npiper-tts=={PIPER_VERSION}\nurllib3==2.8.1\n"
    )
    installed = {
        "msgpack": MSGPACK_VERSION,
        "setuptools": "84.2.1",
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
        "setuptools": "84.2.1",
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
                    "setuptools": "84.2.1",
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
                "setuptools": "84.2.1",
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
        "pil",
        "jpg",
        "jpg_2000",
        "zlib",
        "libtiff",
        "webp",
        "avif",
        "freetype2",
        "raqm",
        "fribidi",
        "harfbuzz",
        "libjpeg_turbo",
        "littlecms2",
        "xcb",
    ],
)
def test_final_package_verifier_requires_each_codec_and_text_feature(
    tmp_path: Path, feature: str
) -> None:
    _pillow_tree(tmp_path)

    errors = validate_pillow(
        features=FakeFeatures(missing=(feature,)), purelib=tmp_path
    )

    assert f"venv: Pillow feature {feature} is unavailable" in errors


@pytest.mark.parametrize("package", ["pi-heif", "pybind11", "Cython"])
def test_final_package_verifier_rejects_packages_the_image_must_not_ship(
    tmp_path: Path, package: str
) -> None:
    errors = validate(
        "venv",
        version=_versions(
            {
                "msgpack": MSGPACK_VERSION,
                "setuptools": "84.2.1",
                "piper-tts": PIPER_VERSION,
                "urllib3": URLLIB3_VERSION,
                package: "1.0.0",
            }
        ),
        package_not_found=MissingPackage,
        purelib=tmp_path,
    )

    assert f"venv: {package} is 1.0.0; expected absent" in errors


PSYCOPG2_VERSION = required_psycopg2_version()
SYSTEM_LDD = """\
\tlinux-vdso.so.1 (0x0000e5e5c3a2d000)
\tlibpq.so.5 => /lib/aarch64-linux-gnu/libpq.so.5 (0x0000e5e5c2f10000)
\tlibc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000e5e5c2d60000)
\tlibssl.so.3 => /lib/aarch64-linux-gnu/libssl.so.3 (0x0000e5e5c2c20000)
\tlibcrypto.so.3 => /lib/aarch64-linux-gnu/libcrypto.so.3 (0x0000e5e5c2800000)
\t/lib/ld-linux-aarch64.so.1 (0x0000e5e5c3a00000)
"""


def _usr_lib_realpath(path: str) -> str:
    """Model Debian's usrmerge, where /lib is a link to /usr/lib."""
    return path.replace("/lib/", "/usr/lib/", 1) if path.startswith("/lib/") else path


def _psycopg2_tree(root: Path) -> Path:
    package = root / "psycopg2"
    package.mkdir()
    (package / "_psycopg.cpython-314-aarch64-linux-gnu.so").write_bytes(b"")
    return root


def _validate_psycopg2(root: Path, ldd_output: str = SYSTEM_LDD, **overrides):
    options = {
        "version": _versions({"psycopg2-binary": PSYCOPG2_VERSION}),
        "package_not_found": MissingPackage,
        "purelib": root,
        "ldd": lambda _extension: ldd_output,
        "realpath": _usr_lib_realpath,
    }
    options.update(overrides)
    return validate_psycopg2_native(**options)


def test_psycopg2_verifier_accepts_a_system_linked_extension(tmp_path: Path) -> None:
    assert _validate_psycopg2(_psycopg2_tree(tmp_path)) == []


@pytest.mark.parametrize("installed", [None, "2.9.12"])
def test_psycopg2_verifier_requires_the_reviewed_distribution(
    tmp_path: Path, installed: str | None
) -> None:
    versions = {} if installed is None else {"psycopg2-binary": installed}
    errors = _validate_psycopg2(_psycopg2_tree(tmp_path), version=_versions(versions))
    assert errors == [
        f"venv: psycopg2-binary is {installed or 'absent'}; "
        f"expected {PSYCOPG2_VERSION}"
    ]


@pytest.mark.parametrize(
    "directory", ["psycopg2_binary.libs", "psycopg2/.libs", "psycopg2.libs"]
)
def test_psycopg2_verifier_rejects_every_bundled_library_directory(
    tmp_path: Path, directory: str
) -> None:
    root = _psycopg2_tree(tmp_path)
    (root / directory).mkdir(parents=True, exist_ok=True)
    errors = _validate_psycopg2(root)
    assert len(errors) == 1
    assert "bundled psycopg2 library directory present" in errors[0]
    assert directory in errors[0]


def _ldd_line(library: str) -> str:
    return next(
        line for line in SYSTEM_LDD.splitlines() if line.strip().startswith(library)
    )


@pytest.mark.parametrize("library", ["libpq", "libssl", "libcrypto"])
def test_psycopg2_verifier_rejects_a_library_outside_usr_lib(
    tmp_path: Path, library: str
) -> None:
    original = _ldd_line(f"{library}.so")
    bundled = (
        f"\t{library}.so.9 => /opt/venv/lib/python3.14/site-packages/"
        f"psycopg2_binary.libs/{library}-abc123.so.9 (0x0000e5e5c2f10000)"
    )
    errors = _validate_psycopg2(
        _psycopg2_tree(tmp_path), SYSTEM_LDD.replace(original, bundled)
    )
    assert any(
        f"resolves {library}.so.9" in error and "under /usr/lib" in error
        for error in errors
    )


@pytest.mark.parametrize("library", ["libpq", "libssl", "libcrypto"])
def test_psycopg2_verifier_rejects_unresolved_or_missing_libraries(
    tmp_path: Path, library: str
) -> None:
    original = _ldd_line(f"{library}.so")
    missing = SYSTEM_LDD.replace(original + "\n", "")
    unresolved = SYSTEM_LDD.replace(original, f"\t{original.split()[0]} => not found")
    root = _psycopg2_tree(tmp_path)
    for output in (missing, unresolved):
        errors = _validate_psycopg2(root, output)
        assert f"venv: psycopg2 does not resolve {library} through ldd" in errors


def test_psycopg2_verifier_requires_one_extension_module(tmp_path: Path) -> None:
    (tmp_path / "psycopg2").mkdir()
    errors = _validate_psycopg2(tmp_path)
    assert len(errors) == 1
    assert "expected exactly one psycopg2 extension module" in errors[0]


def _installed_psycopg2_fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    package = root / "psycopg2"
    package.mkdir()
    extension = package / "_psycopg.cpython-314-x86_64-linux-gnu.so"
    extension.write_bytes(b"native extension bytes")
    (package / "__init__.py").write_text("version = 'fixture'\n")
    metadata = root / "psycopg2_binary-2.9.13.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: psycopg2-binary\nVersion: 2.9.13\n")
    rows = []
    for path in (extension, package / "__init__.py", metadata / "METADATA"):
        data = path.read_bytes()
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        rows.append((str(path.relative_to(root)), f"sha256={digest}", str(len(data))))
    rows.append((str((metadata / "RECORD").relative_to(root)), "", ""))
    record = metadata / "RECORD"
    with record.open("w", newline="") as output:
        csv.writer(output).writerows(rows)
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda _: importlib.metadata.PathDistribution(metadata),
    )
    return extension, record


def test_installed_psycopg2_record_covers_native_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension, record = _installed_psycopg2_fixture(tmp_path, monkeypatch)
    receipt = psycopg2_record_receipt(tmp_path)
    assert receipt["record_hashes_verified"] == 3
    assert (
        receipt["extension_sha256"]
        == hashlib.sha256(extension.read_bytes()).hexdigest()
    )
    assert b"\r\n" in record.read_bytes()
    assert receipt["record_sha256"] == hashlib.sha256(record.read_bytes()).hexdigest()


def test_installed_psycopg2_record_rejects_mutated_native_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension, _ = _installed_psycopg2_fixture(tmp_path, monkeypatch)
    extension.write_bytes(b"modified")
    with pytest.raises(ValueError, match="installed bytes differ"):
        psycopg2_record_receipt(tmp_path)


def test_installed_psycopg2_record_requires_native_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, record = _installed_psycopg2_fixture(tmp_path, monkeypatch)
    rows = list(csv.reader(record.read_text().splitlines()))
    rows[0][1:] = ["", ""]
    with record.open("w", newline="") as output:
        csv.writer(output).writerows(rows)
    with pytest.raises(ValueError, match="unhashed installed file"):
        psycopg2_record_receipt(tmp_path)


def test_installed_psycopg2_record_rejects_malformed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, record = _installed_psycopg2_fixture(tmp_path, monkeypatch)
    record.write_text("psycopg2/_psycopg.so,sha256=bad\n")
    with pytest.raises(ValueError, match="three fields"):
        psycopg2_record_receipt(tmp_path)


def test_installed_psycopg2_record_requires_the_reviewed_pin_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed_psycopg2_fixture(tmp_path, monkeypatch)
    requirements = tmp_path / "requirements.txt"
    with pytest.raises(FileNotFoundError):
        psycopg2_record_receipt(tmp_path, requirements)
    requirements.write_text("psycopg2-binary==2.9.12\n")
    with pytest.raises(ValueError, match="distribution version differs"):
        psycopg2_record_receipt(tmp_path, requirements)
    requirements.write_text("psycopg2-binary>=2.9.13\n")
    with pytest.raises(ValueError, match="one exact psycopg2-binary pin"):
        psycopg2_record_receipt(tmp_path, requirements)


def test_psycopg2_verifier_follows_the_canonical_pin(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("psycopg2-binary==2.9.14 # reviewed pin\n")
    site = tmp_path / "site"
    site.mkdir()
    root = _psycopg2_tree(site)
    assert (
        _validate_psycopg2(
            root,
            requirements=requirements,
            version=_versions({"psycopg2-binary": "2.9.14"}),
        )
        == []
    )
    assert any(
        f"psycopg2-binary is {PSYCOPG2_VERSION}; expected 2.9.14" in error
        for error in _validate_psycopg2(root, requirements=requirements)
    )


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "psycopg2-binary>=2.9\n",
        "psycopg2-binary==2.9.13\npsycopg2-binary==2.9.12\n",
    ],
)
def test_psycopg2_verifier_rejects_an_ambiguous_requirement(
    tmp_path: Path, contents: str
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(contents)
    site = tmp_path / "site"
    site.mkdir()
    errors = _validate_psycopg2(_psycopg2_tree(site), requirements=requirements)
    assert len(errors) == 1
    assert "exactly one exact psycopg2-binary pin" in errors[0]


def test_dockerfile_builds_psycopg2_from_source_before_requirements() -> None:
    root = Path(__file__).resolve().parents[1]
    dependency_install = (
        (root / "Dockerfile").read_text().split("# Pa11y needs Node", 1)[0]
    )
    source_build = "sh /tmp/build-psycopg2-wheel.sh /tmp/psycopg2-wheel"
    requirements_install = "pip install --no-cache-dir -r requirements.txt"
    assert source_build in dependency_install
    assert dependency_install.index(source_build) < dependency_install.index(
        requirements_install
    )
    # The pre-installed source build must be the exact reviewed pin, or the
    # requirements install would silently fetch the bundled-library wheel.
    assert f"psycopg2_binary-{PSYCOPG2_VERSION}-*.whl" in dependency_install
    script = (root / "docker" / "build-psycopg2-wheel.sh").read_text()
    assert f"psycopg2_binary-{PSYCOPG2_VERSION}.tar.gz" in script


def test_quickstart_builds_all_native_pins_before_dev_requirements() -> None:
    source = (Path(__file__).resolve().parents[1] / "Dockerfile.dev").read_text()
    builds = (
        "sh /tmp/build-lxml-wheel.sh /tmp/lxml-wheel",
        "sh /tmp/build-pillow-wheel.sh /tmp/pillow-wheel",
        "sh /tmp/build-psycopg2-wheel.sh /tmp/psycopg2-wheel",
        "sh /tmp/build-pyav-wheel.sh /tmp/pyav-wheel",
        "sh /tmp/build-opencv-wheel.sh /tmp/opencv-wheel",
        "pip install --no-cache-dir -r requirements-dev.txt",
    )
    assert list(map(source.index, builds)) == sorted(map(source.index, builds))
    for wheel in (
        "lxml-6.1.3-*.whl",
        "pillow-13.0.0-*.whl",
        "psycopg2_binary-2.9.13-*.whl",
        "av-18.1.0-*.whl",
        "opencv_python-5.0.0.93-*.whl",
    ):
        assert source.index(wheel) < source.index(builds[-1])
    assert "libpq-dev" in source
    assert "libavif-dev" in source
    assert "libraqm-dev" in source
    assert "libtiff-dev" not in source


PYAV_VERSION = required_pyav_version()


def _pyav_tree(root: Path) -> Path:
    package = root / "av"
    package.mkdir()
    (package / "_core.cpython-314-aarch64-linux-gnu.so").write_bytes(b"")
    return root


def _pyav_ldd(path_prefix: str = "/opt/ffmpeg9/lib") -> str:
    return "\n".join(
        f"{stem}.so.{numbers[0]} => {path_prefix}/{stem}.so.{numbers[0]} (0x1234)"
        for stem, numbers in PYAV_LIBRARIES.items()
    )


def _validate_pyav(root: Path, ldd_output: str | None = None, **overrides):
    options = {
        "version": _versions({"av": PYAV_VERSION}),
        "package_not_found": MissingPackage,
        "purelib": root,
        "ldd": lambda _extension: ldd_output or _pyav_ldd(),
        "realpath": lambda path: path,
        "library_versions": PYAV_LIBRARIES,
    }
    options.update(overrides)
    return validate_pyav_native(**options)


def test_pyav_verifier_accepts_all_seven_source_linked_libraries(
    tmp_path: Path,
) -> None:
    assert _validate_pyav(_pyav_tree(tmp_path)) == []


def test_pyav_verifier_rejects_published_wheel_private_libraries(
    tmp_path: Path,
) -> None:
    root = _pyav_tree(tmp_path)
    (root / "av.libs").mkdir()
    errors = _validate_pyav(root, _pyav_ldd(str(root / "av.libs")))
    assert any("bundled av library directory" in error for error in errors)
    assert any("expected /opt/ffmpeg9/lib/" in error for error in errors)


@pytest.mark.parametrize("path", ["/usr/lib/aarch64-linux-gnu", "/tmp/ffmpeg"])
def test_pyav_verifier_rejects_wrong_library_prefix(tmp_path: Path, path: str) -> None:
    errors = _validate_pyav(_pyav_tree(tmp_path), _pyav_ldd(path))
    assert (
        len([error for error in errors if "expected /opt/ffmpeg9/lib/" in error]) >= 7
    )


def test_pyav_verifier_rejects_missing_or_old_ffmpeg_library(tmp_path: Path) -> None:
    root = _pyav_tree(tmp_path)
    good = _pyav_ldd()
    old = good.replace("libavcodec.so.63", "libavcodec.so.61")
    errors = _validate_pyav(root, old)
    assert any("expected libavcodec.so.63" in error for error in errors)
    assert any("does not resolve libavcodec" in error for error in errors)


def test_pyav_verifier_rejects_tls_and_wrong_native_version(tmp_path: Path) -> None:
    root = _pyav_tree(tmp_path)
    linked = (
        _pyav_ldd()
        + "\nlibssl-deadbeef.so.3 => /tmp/av.libs/libssl-deadbeef.so.3 (0x1234)"
    )
    versions = {**PYAV_LIBRARIES, "libavcodec": (61, 1, 102)}
    errors = _validate_pyav(root, linked, library_versions=versions)
    assert any("loads TLS library libssl-deadbeef.so.3" in error for error in errors)
    assert any("reports libavcodec (61, 1, 102)" in error for error in errors)


def test_dockerfile_builds_pyav_before_installing_requirements() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    builder = dockerfile.split("AS builder", 1)[1].split("AS pa11y-node", 1)[0]
    source = "sh /tmp/build-pyav-wheel.sh /tmp/pyav-wheel"
    assert source in builder
    assert builder.index(source) < builder.index(
        "pip install --no-cache-dir -r requirements.txt"
    )
    assert f"/tmp/pyav-wheel/av-{PYAV_VERSION}-*.whl" in builder
    assert "COPY --from=builder /opt/ffmpeg9/lib /opt/ffmpeg9/lib" in dockerfile


OPENCV_VERSION = required_opencv_version()


def _opencv_tree(root: Path) -> Path:
    package = root / "cv2"
    package.mkdir()
    (package / "cv2.cpython-314-aarch64-linux-gnu.so").write_bytes(b"")
    return root


def _opencv_ldd(path_prefix: str = "/opt/ffmpeg9/lib") -> str:
    return "\n".join(
        [
            *(
                f"{stem}.so.{PYAV_LIBRARIES[stem][0]} => {path_prefix}/{stem}.so.{PYAV_LIBRARIES[stem][0]} (0x1234)"
                for stem in (
                    "libavcodec",
                    "libavformat",
                    "libavutil",
                    "libswscale",
                    "libswresample",
                )
            ),
            "libdav1d.so.7 => /usr/lib/aarch64-linux-gnu/libdav1d.so.7 (0x1234)",
        ]
    )


def _validate_opencv(root: Path, ldd_output: str | None = None, **overrides):
    options = {
        "version": _versions({"opencv-python": OPENCV_VERSION}),
        "package_not_found": MissingPackage,
        "purelib": root,
        "ldd": lambda _extension: ldd_output or _opencv_ldd(),
        "realpath": lambda path: path,
        "build_information": "  FFMPEG: YES\n  TIFF: NO\n",
    }
    options.update(overrides)
    return validate_opencv_native(**options)


def test_opencv_verifier_accepts_shared_ffmpeg_and_debian_dav1d(tmp_path: Path):
    assert _validate_opencv(_opencv_tree(tmp_path)) == []


def test_opencv_verifier_rejects_published_private_wheel(tmp_path: Path):
    root = _opencv_tree(tmp_path)
    (root / "opencv_python.libs").mkdir()
    errors = _validate_opencv(root, _opencv_ldd(str(root / "opencv_python.libs")))
    assert any("bundled OpenCV library directory" in error for error in errors)
    assert any("expected /opt/ffmpeg9/lib/" in error for error in errors)


def test_opencv_verifier_rejects_missing_cpu_av1_and_tiff(tmp_path: Path):
    root = _opencv_tree(tmp_path)
    errors = _validate_opencv(
        root,
        _opencv_ldd().replace("libdav1d.so.7", "libdav1d.so.6")
        + "\nlibssl-deadbeef.so.3 => /tmp/opencv_python.libs/libssl-deadbeef.so.3 (0x1234)",
        build_information="FFMPEG: YES\nTIFF: build (4.7.1)\n",
    )
    assert any("lacks Debian libdav1d.so.7" in error for error in errors)
    assert any("optional TIFF image backend" in error for error in errors)
    assert any("loads TLS library" in error for error in errors)


def test_opencv_verifier_rejects_versioned_image_libraries(tmp_path: Path):
    root = _opencv_tree(tmp_path)
    errors = _validate_opencv(
        root,
        _opencv_ldd()
        + "\nlibpng16.so.16 => /usr/lib/aarch64-linux-gnu/libpng16.so.16 (0x1234)"
        + "\nlibavif.so.16 => /usr/lib/aarch64-linux-gnu/libavif.so.16 (0x1234)",
    )
    assert any("disabled image codec libpng16.so.16" in error for error in errors)
    assert any("disabled image codec libavif.so.16" in error for error in errors)


def test_opencv_verifier_rejects_nonprefix_indirect_ffmpeg(tmp_path: Path):
    root = _opencv_tree(tmp_path)
    errors = _validate_opencv(
        root,
        _opencv_ldd().replace(
            "/opt/ffmpeg9/lib/libswresample.so.7",
            "/usr/lib/aarch64-linux-gnu/libswresample.so.7",
        ),
    )
    assert any(
        "libswresample.so.7" in error and "expected /opt/ffmpeg9/lib/" in error
        for error in errors
    )


def test_opencv_verifier_rejects_built_optional_image_backends(tmp_path: Path):
    root = _opencv_tree(tmp_path)
    errors = _validate_opencv(
        root,
        build_information=(
            "FFMPEG: YES\n"
            "AVIF: /usr/lib/libavif.so\n"
            "JPEG: build (libjpeg-turbo)\n"
            "PNG: build (ver 1.6.0)\n"
            "WEBP: /usr/lib/libwebp.so\n"
        ),
    )
    for backend in ("AVIF", "JPEG", "PNG", "WEBP"):
        assert any(f"optional {backend} image backend" in error for error in errors)


def test_dockerfiles_build_opencv_before_requirements():
    root = Path(__file__).resolve().parents[1]
    for name, requirements in (
        ("Dockerfile", "requirements.txt"),
        ("Dockerfile.dev", "requirements-dev.txt"),
    ):
        source = (root / name).read_text()
        build = "sh /tmp/build-opencv-wheel.sh /tmp/opencv-wheel /tmp/opencv-ffmpeg9-pr29533.diff"
        install = f"/tmp/opencv-wheel/opencv_python-{OPENCV_VERSION}-*.whl"
        requirements_install = f"pip install --no-cache-dir -r {requirements}"
        assert source.index("sh /tmp/build-pyav-wheel.sh") < source.index(build)
        assert source.index(build) < source.index(install)
        assert source.index(install) < source.index(requirements_install)


def test_opencv_source_disables_optional_image_discovery():
    source = (
        Path(__file__).resolve().parents[1] / "docker/build-opencv-wheel.sh"
    ).read_text()
    for backend in ("AVIF", "JPEG", "PNG", "SPNG", "TIFF", "WEBP", "GDCM", "GDAL"):
        assert f"-DWITH_{backend}=OFF" in source


def _cli_ldd(
    prefix: str = "/opt/ffmpeg9/lib",
    extra: str = "",
) -> str:
    rows = [
        f"\t{stem}.so.{numbers[0]} => {prefix}/{stem}.so.{numbers[0]} (0x1234)"
        for stem, numbers in PYAV_LIBRARIES.items()
    ]
    rows += [
        "\tlibmp3lame.so.0 => /usr/lib/aarch64-linux-gnu/libmp3lame.so.0 (0x1234)",
        "\tlibvorbisenc.so.2 => /usr/lib/aarch64-linux-gnu/libvorbisenc.so.2 (0x1234)",
        "\tlibdav1d.so.7 => /usr/lib/aarch64-linux-gnu/libdav1d.so.7 (0x1234)",
    ]
    return "\n".join(rows) + extra


_CLI_PROTOCOLS = (
    "Supported file protocols:\nInput:\n  file\n  pipe\nOutput:\n  file\n  pipe\n"
)
_CLI_ENCODERS = (
    "Encoders:\n V..... = Video\n ------\n"
    " A....D aac                  AAC (Advanced Audio Coding)\n"
    " A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3)\n"
    " A....D libvorbis            libvorbis\n"
)


def _cli_run(
    version: str = "9.0.2",
    configuration: str = FFMPEG_CLI_CONFIGURATION,
    protocols: str = _CLI_PROTOCOLS,
    encoders: str = _CLI_ENCODERS,
):
    def run(command: list[str]) -> str:
        tool = Path(command[0]).name
        if command[-1] == "-version":
            return (
                f"{tool} version {version} Copyright (c) 2007-2026 the FFmpeg developers\n"
                "built with gcc 14 (Debian 14.2.0-19)\n"
                f"configuration: {configuration}\n"
            )
        if command[-1] == "-protocols":
            return protocols
        if command[-1] == "-encoders":
            return encoders
        raise AssertionError(command)

    return run


def _validate_cli(**overrides):
    links = {
        "/usr/local/bin/ffmpeg": "/opt/ffmpeg9/bin/ffmpeg",
        "/usr/local/bin/ffprobe": "/opt/ffmpeg9/bin/ffprobe",
    }
    options = {
        "which": lambda tool: f"/usr/local/bin/{tool}",
        "realpath": lambda path: links.get(path, path),
        "run": _cli_run(),
        "ldd": lambda _path: _cli_ldd(),
    }
    options.update(overrides)
    return validate_ffmpeg_cli(**options)


def test_ffmpeg_cli_verifier_accepts_the_source_built_programs() -> None:
    assert _validate_cli() == []


def test_ffmpeg_cli_configuration_pins_local_protocols_and_encoders() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "build-pyav-wheel.sh"
    ).read_text()
    for flag in FFMPEG_CLI_CONFIGURATION.split(" --"):
        flag = flag if flag.startswith("--") else f"--{flag}"
        if flag.startswith(("--prefix", "--extra-")):
            continue
        assert flag in script, flag
    assert "--disable-network --disable-protocols" in FFMPEG_CLI_CONFIGURATION
    assert "--enable-gpl" not in FFMPEG_CLI_CONFIGURATION
    assert "--enable-nonfree" not in FFMPEG_CLI_CONFIGURATION


def test_ffmpeg_cli_verifier_rejects_debian_ffmpeg_on_path() -> None:
    # Debian's package installs real binaries in /usr/bin.
    errors = _validate_cli(
        which=lambda tool: f"/usr/bin/{tool}", realpath=lambda path: path
    )
    assert (
        "cli: ffmpeg on PATH is /usr/bin/ffmpeg -> /usr/bin/ffmpeg; expected /opt/ffmpeg9/bin/ffmpeg"
        in errors
    )
    assert (
        "cli: ffprobe on PATH is /usr/bin/ffprobe -> /usr/bin/ffprobe; expected /opt/ffmpeg9/bin/ffprobe"
        in errors
    )


def test_ffmpeg_cli_verifier_rejects_missing_programs() -> None:
    errors = _validate_cli(which=lambda _tool: None)
    assert errors == ["cli: ffmpeg is not on PATH", "cli: ffprobe is not on PATH"]


def test_ffmpeg_cli_verifier_rejects_wrong_version_and_configuration() -> None:
    errors = _validate_cli(
        run=_cli_run(
            version="7.1.5-0+deb13u1",
            configuration=FFMPEG_CLI_CONFIGURATION.replace("--disable-network ", ""),
        )
    )
    assert any(
        "ffmpeg reports" in error and "expected version 9.0.2" in error
        for error in errors
    )
    assert any("ffprobe configuration differs" in error for error in errors)


def test_ffmpeg_cli_verifier_rejects_network_protocols() -> None:
    errors = _validate_cli(
        run=_cli_run(
            protocols=_CLI_PROTOCOLS.replace(
                "  pipe\nOutput", "  pipe\n  https\nOutput"
            )
        )
    )
    assert errors == [
        "cli: ffmpeg input protocols are ['file', 'https', 'pipe']; expected ['file', 'pipe']"
    ]


def test_ffmpeg_cli_verifier_requires_speech_encoders() -> None:
    errors = _validate_cli(
        run=_cli_run(encoders=_CLI_ENCODERS.replace("libvorbis ", "vorbis "))
    )
    assert errors == ["cli: ffmpeg lacks the libvorbis encoder"]


@pytest.mark.parametrize("prefix", ["/usr/lib/aarch64-linux-gnu", "/tmp/ffmpeg"])
def test_ffmpeg_cli_verifier_rejects_libraries_outside_the_prefix(prefix: str) -> None:
    errors = _validate_cli(ldd=lambda _path: _cli_ldd(prefix))
    assert (
        len([error for error in errors if "expected /opt/ffmpeg9/lib/" in error]) == 14
    )


@pytest.mark.parametrize(
    "row",
    [
        "\tlibrist.so.4 => /usr/lib/aarch64-linux-gnu/librist.so.4 (0x1)",
        "\tlibcjson.so.1 => /usr/lib/aarch64-linux-gnu/libcjson.so.1 (0x1)",
        "\tlibgnutls.so.30 => /usr/lib/aarch64-linux-gnu/libgnutls.so.30 (0x1)",
        "\tlibSDL2-2.0.so.0 => /usr/lib/aarch64-linux-gnu/libSDL2-2.0.so.0 (0x1)",
        "\tlibavcodec.so.61 => /usr/lib/aarch64-linux-gnu/libavcodec.so.61 (0x1)",
    ],
)
def test_ffmpeg_cli_verifier_rejects_debian_ffmpeg_libraries(row: str) -> None:
    errors = _validate_cli(ldd=lambda _path: _cli_ldd(extra=f"\n{row}"))
    name = row.split()[0]
    assert any(name in error for error in errors), errors


def test_ffmpeg_cli_verifier_rejects_unresolved_or_missing_encoder_library() -> None:
    ldd = _cli_ldd().replace(
        "/usr/lib/aarch64-linux-gnu/libmp3lame.so.0 (0x1234)", "not found"
    )
    errors = _validate_cli(ldd=lambda _path: ldd)
    assert "cli: ffmpeg cannot resolve libmp3lame.so.0: not found" in errors
    assert "cli: ffmpeg lacks Debian libmp3lame.so.0: not found" in errors


def test_api_dockerfile_ships_the_source_built_cli_instead_of_debian_ffmpeg() -> None:
    root = Path(__file__).resolve().parents[1]
    api = (root / "Dockerfile").read_text()
    runtime = api.split("AS runtime", 1)[1]
    assert "COPY --from=builder /opt/ffmpeg9/bin /opt/ffmpeg9/bin" in runtime
    assert "ln -s /opt/ffmpeg9/bin/ffmpeg /usr/local/bin/ffmpeg" in runtime
    assert "ln -s /opt/ffmpeg9/bin/ffprobe /usr/local/bin/ffprobe" in runtime
    apt_lines = {line.strip() for line in runtime.splitlines()}
    assert "ffmpeg \\" not in apt_lines
    for pin in (
        "libmp3lame0=3.100-6+b3",
        "libvorbis0a=1.3.7-3",
        "libvorbisenc2=1.3.7-3",
        "libogg0=1.3.5-3+b2",
    ):
        assert f"{pin} \\" in apt_lines, pin
    opencv = (root / "docker" / "build-opencv-wheel.sh").read_text()
    assert '"$prefix/bin"' not in opencv
