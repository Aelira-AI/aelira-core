#!/usr/bin/env python3
"""Verify the reviewed Python package state in a final API image filesystem."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import re
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

EXPECTED = {
    "global": {"setuptools": None, "pip": None, "urllib3": None},
    "venv": {"setuptools": "84.0.0", "pip": None},
}
FORBIDDEN_METADATA = {
    "msgpack": "1.1.2",
    "setuptools": "70.3.0",
    "urllib3": "2.7.0",
}
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def required_pinned_version(package: str, requirements: Path = REQUIREMENTS) -> str:
    """Read one exact canonical pin without importing runtime dependencies."""
    name_pattern = "piper[-_.]tts" if package == "piper-tts" else re.escape(package)
    entries = [
        line.split("#", 1)[0].strip()
        for line in requirements.read_text().splitlines()
        if re.match(rf"^{name_pattern}\b", line.strip(), re.IGNORECASE)
    ]
    if len(entries) != 1 or not re.fullmatch(
        rf"{name_pattern}==[0-9]+(?:\.[0-9]+)+",
        entries[0],
        re.IGNORECASE,
    ):
        raise ValueError(
            f"requirements.txt must contain exactly one exact {package} pin"
        )
    return entries[0].split("==", 1)[1]


def required_psycopg2_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("psycopg2-binary", requirements)


def required_piper_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("piper-tts", requirements)


def required_msgpack_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("msgpack", requirements)


def required_urllib3_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("urllib3", requirements)


def validate(
    scope: str,
    *,
    version: Callable[[str], str] = importlib.metadata.version,
    package_not_found: type[Exception] = importlib.metadata.PackageNotFoundError,
    purelib: Path | None = None,
    requirements: Path = REQUIREMENTS,
) -> list[str]:
    """Return every package-state violation for one Python installation."""
    errors: list[str] = []
    expected = EXPECTED[scope].copy()
    try:
        expected["msgpack"] = required_msgpack_version(requirements)
    except (OSError, ValueError) as exc:
        errors.append(f"{scope}: unable to determine required msgpack version: {exc}")
    if scope == "venv":
        try:
            expected["urllib3"] = required_urllib3_version(requirements)
        except (OSError, ValueError) as exc:
            errors.append(
                f"{scope}: unable to determine required urllib3 version: {exc}"
            )
        try:
            expected["piper-tts"] = required_piper_version(requirements)
        except (OSError, ValueError) as exc:
            errors.append(f"{scope}: unable to determine required Piper version: {exc}")

    for package, expected_version in expected.items():
        try:
            installed_version = version(package)
        except package_not_found:
            installed_version = None

        if installed_version != expected_version:
            rendered = installed_version if installed_version is not None else "absent"
            required = expected_version if expected_version is not None else "absent"
            errors.append(f"{scope}: {package} is {rendered}; expected {required}")

    metadata_root = purelib or Path(sysconfig.get_paths()["purelib"])
    try:
        metadata_names = [
            entry.name.casefold().replace("_", "-") for entry in metadata_root.iterdir()
        ]
    except OSError as exc:
        errors.append(f"{scope}: unable to inspect {metadata_root}: {exc}")
        return errors

    for package, forbidden_version in FORBIDDEN_METADATA.items():
        prefixes = (
            f"{package}-{forbidden_version}.dist-info",
            f"{package}-{forbidden_version}.egg-info",
        )
        matches = sorted(name for name in metadata_names if name.startswith(prefixes))
        if matches:
            errors.append(
                f"{scope}: forbidden stale metadata present: {', '.join(matches)}"
            )

    vendored_urllib3 = metadata_root / "pip" / "_vendor" / "urllib3"
    if vendored_urllib3.exists():
        errors.append(f"{scope}: pip-vendored urllib3 remains at {vendored_urllib3}")

    return errors


SYSTEM_LIBRARY_PREFIX = "/usr/lib/"
SYSTEM_LIBRARIES = ("libpq", "libssl", "libcrypto")


def _resolved_libraries(ldd_output: str) -> dict[str, str]:
    """Map each dependency name from ``ldd`` output to its resolved path."""
    libraries: dict[str, str] = {}
    for line in ldd_output.splitlines():
        name, arrow, rest = line.strip().partition(" => ")
        if not arrow:
            continue
        path = rest.split(" (", 1)[0].strip()
        libraries[name] = path
    return libraries


def run_ldd(extension: Path) -> str:
    return subprocess.run(
        ["ldd", str(extension)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def validate_psycopg2_native(
    *,
    version: Callable[[str], str] = importlib.metadata.version,
    package_not_found: type[Exception] = importlib.metadata.PackageNotFoundError,
    purelib: Path | None = None,
    requirements: Path = REQUIREMENTS,
    ldd: Callable[[Path], str] = run_ldd,
    realpath: Callable[[str], str] = os.path.realpath,
) -> list[str]:
    """Return every violation of the source-built psycopg2 contract.

    The distribution keeps the reviewed ``psycopg2-binary`` name and version,
    but it must be built from source: no wheel-bundled ``.libs`` directory may
    exist and its extension module must resolve libpq, libssl and libcrypto to
    the operating system's copies under /usr/lib.
    """
    errors: list[str] = []
    try:
        expected = required_psycopg2_version(requirements)
    except (OSError, ValueError) as exc:
        return [f"venv: unable to determine required psycopg2-binary version: {exc}"]
    try:
        installed = version("psycopg2-binary")
    except package_not_found:
        installed = None
    if installed != expected:
        rendered = installed if installed is not None else "absent"
        errors.append(f"venv: psycopg2-binary is {rendered}; expected {expected}")

    metadata_root = purelib or Path(sysconfig.get_paths()["purelib"])
    bundled = sorted(
        str(path)
        for pattern in ("psycopg2*.libs", "psycopg2/.libs", "psycopg2*/*.libs")
        for path in metadata_root.glob(pattern)
    )
    if bundled:
        errors.append(
            f"venv: bundled psycopg2 library directory present: {', '.join(bundled)}"
        )

    extensions = sorted((metadata_root / "psycopg2").glob("_psycopg*.so"))
    if len(extensions) != 1:
        errors.append(
            f"venv: expected exactly one psycopg2 extension module in "
            f"{metadata_root / 'psycopg2'}; found {len(extensions)}"
        )
        return errors
    try:
        libraries = _resolved_libraries(ldd(extensions[0]))
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"venv: unable to inspect {extensions[0].name} with ldd: {exc}")
        return errors
    for prefix in SYSTEM_LIBRARIES:
        if not any(
            name.startswith(f"{prefix}.so") and path.startswith("/")
            for name, path in libraries.items()
        ):
            errors.append(f"venv: psycopg2 does not resolve {prefix} through ldd")
    for name, path in sorted(libraries.items()):
        if path.startswith("/") and not realpath(path).startswith(
            SYSTEM_LIBRARY_PREFIX
        ):
            errors.append(
                f"venv: psycopg2 resolves {name} to {path}; "
                "expected a system path under /usr/lib"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=[*sorted(EXPECTED), "msgpack-pin"])
    args = parser.parse_args()
    if args.scope == "msgpack-pin":
        try:
            print(required_msgpack_version())
        except (OSError, ValueError) as exc:
            print(
                f"unable to determine required msgpack version: {exc}",
                file=sys.stderr,
            )
            return 1
        return 0
    errors = validate(args.scope)
    if args.scope == "venv":
        errors.extend(validate_psycopg2_native())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Final Python package state valid: {args.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
