#!/usr/bin/env python3
"""Verify the reviewed Python package state in a final API image filesystem."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

EXPECTED = {
    "global": {"setuptools": None},
    "venv": {"setuptools": "84.0.0"},
}
FORBIDDEN_METADATA = {
    "msgpack": "1.1.2",
    "setuptools": "70.3.0",
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


def required_piper_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("piper-tts", requirements)


def required_msgpack_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("msgpack", requirements)


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
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Final Python package state valid: {args.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
