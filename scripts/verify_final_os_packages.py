#!/usr/bin/env python3
"""Verify that unused Debian packages are absent from the final API image."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable

# Packages the image does not need and must not carry. xdg-utils ships
# xdg-email (CVE-2022-4055); no supported feature opens a desktop handler or
# mail client. libxml2-utils ships xmllint and xmlcatalog, which nothing runs.
ABSENT_PACKAGES = ("xdg-utils", "libxml2-utils")
ABSENT_EXECUTABLES = (
    "xdg-desktop-icon",
    "xdg-desktop-menu",
    "xdg-email",
    "xdg-icon-resource",
    "xdg-mime",
    "xdg-open",
    "xdg-screensaver",
    "xdg-settings",
    "xmlcatalog",
    "xmllint",
)
# texlive-base declares a hard dependency on xdg-utils, so removing the
# package leaves exactly this one unmet dependency and nothing else.
ALLOWED_UNMET = frozenset({("texlive-base", "xdg-utils")})

FIELD_SPLIT = re.compile(r"\s*,\s*")


def package_installed(name: str) -> bool:
    """Return whether dpkg records the package as present in any form."""
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "not-installed"}


def dpkg_records() -> list[tuple[str, str, str]]:
    """Return (package, depends, provides) for every configured package."""
    result = subprocess.run(
        [
            "dpkg-query",
            "-W",
            "-f=${db:Status-Status}\t${Package}\t${Pre-Depends}, ${Depends}\t${Provides}\n",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    records = []
    for line in result.stdout.splitlines():
        status, package, depends, provides = line.split("\t")
        if status == "installed":
            records.append((package, depends, provides))
    return records


def dependency_name(alternative: str) -> str:
    """Strip version and architecture qualifiers from one dependency atom."""
    return re.split(r"[\s(:]", alternative.strip(), maxsplit=1)[0]


def unmet_dependencies(
    records: Iterable[tuple[str, str, str]],
) -> set[tuple[str, str]]:
    """Return (package, dependency) pairs whose alternatives are all missing."""
    records = list(records)
    available = set()
    for package, _, provides in records:
        available.add(package)
        available.update(
            dependency_name(item) for item in FIELD_SPLIT.split(provides) if item
        )
    unmet = set()
    for package, depends, _ in records:
        for group in FIELD_SPLIT.split(depends):
            alternatives = [
                dependency_name(item) for item in group.split("|") if item.strip()
            ]
            if alternatives and not any(name in available for name in alternatives):
                unmet.add((package, alternatives[0]))
    return unmet


def validate(
    *,
    installed: Callable[[str], bool] = package_installed,
    executable: Callable[[str], str | None] = shutil.which,
    records: Callable[[], Iterable[tuple[str, str, str]]] = dpkg_records,
    packages: Iterable[str] = ABSENT_PACKAGES,
    executables: Iterable[str] = ABSENT_EXECUTABLES,
) -> list[str]:
    """Return every violation of the final-image package state."""
    errors = [f"package must be absent: {name}" for name in packages if installed(name)]
    for name in executables:
        path = executable(name)
        if path:
            errors.append(f"executable must be absent: {name} at {path}")
    for package, dependency in sorted(unmet_dependencies(records()) - ALLOWED_UNMET):
        errors.append(f"unexpected unmet dependency: {package} needs {dependency}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Final image OS package state is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
