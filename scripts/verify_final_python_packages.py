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
from typing import Any

EXPECTED = {
    "global": {"setuptools": None, "pip": None, "urllib3": None},
    # pi-heif bundles libheif and libde265 and nothing in the image uses it.
    # pybind11 is only a build requirement of the source-built Pillow.
    "venv": {"setuptools": "84.0.0", "pip": None, "pi-heif": None, "pybind11": None},
}
# Pillow is built from source against libtiff 4.7.2 (docker/build-pillow-wheel.sh)
# and must keep every feature its published wheel had.
PILLOW_LIBTIFF = (4, 7, 2)
PILLOW_FEATURES = (
    "jpg",
    "jpg_2000",
    "zlib",
    "libtiff",
    "webp",
    "avif",
    "freetype2",
    "littlecms2",
    "xcb",
)
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


def validate_pillow(
    *,
    features: Any | None = None,
    purelib: Path | None = None,
) -> list[str]:
    """Return every violation of the source-built Pillow contract."""
    if features is None:
        from PIL import features as pillow_features

        features = pillow_features
    errors: list[str] = []
    root = purelib or Path(sysconfig.get_paths()["purelib"])

    if (root / "pillow.libs").exists():
        errors.append(f"venv: wheel-private library directory remains at {root}")

    reported = features.version("libtiff")
    try:
        libtiff = tuple(int(part) for part in str(reported).split("."))
    except ValueError:
        libtiff = ()
    if libtiff < PILLOW_LIBTIFF:
        required = ".".join(map(str, PILLOW_LIBTIFF))
        errors.append(
            f"venv: Pillow libtiff is {reported}; expected at least {required}"
        )

    for name in PILLOW_FEATURES:
        if not features.check(name):
            errors.append(f"venv: Pillow feature {name} is unavailable")

    # The TIFF library must be linked statically into the extension. A dynamic
    # dependency would load whichever libtiff the system provides instead.
    extensions = sorted((root / "PIL").glob("_imaging.*.so"))
    if not extensions:
        errors.append(f"venv: Pillow core extension not found under {root / 'PIL'}")
    for extension in extensions:
        if b"libtiff.so" in extension.read_bytes():
            errors.append(f"venv: {extension.name} links a shared libtiff")
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
        errors.extend(validate_pillow())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Final Python package state valid: {args.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
