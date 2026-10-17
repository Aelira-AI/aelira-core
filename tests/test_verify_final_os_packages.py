"""Behavior tests for final-image Debian package verification."""

from __future__ import annotations

from scripts.verify_final_os_packages import (
    ABSENT_EXECUTABLES,
    ABSENT_PACKAGES,
    unmet_dependencies,
    validate,
)

CLEAN_RECORDS = [
    ("libc6", "", ""),
    ("texlive-base", "libc6 (>= 2.36), debconf | debconf-2.0", ""),
    ("debconf", "", "debconf-2.0"),
]
RESIDUE_RECORDS = [
    *CLEAN_RECORDS,
    ("texlive-base", "xdg-utils, libc6", ""),
]


def _validate(
    installed: set[str] | None = None,
    executables: dict[str, str] | None = None,
    records=CLEAN_RECORDS,
):
    present = installed or set()
    paths = executables or {}
    return validate(
        installed=lambda name: name in present,
        executable=paths.get,
        records=lambda: records,
    )


def test_absent_package_state_is_accepted() -> None:
    assert _validate() == []


def test_removed_packages_and_executables_are_pinned() -> None:
    assert ABSENT_PACKAGES == ("xdg-utils", "libxml2-utils")
    assert {"xdg-email", "xdg-open", "xmllint", "xmlcatalog"} <= set(ABSENT_EXECUTABLES)


def test_each_reintroduced_package_is_reported() -> None:
    errors = _validate(installed={"xdg-utils", "libxml2-utils"})

    assert errors == [
        "package must be absent: xdg-utils",
        "package must be absent: libxml2-utils",
    ]


def test_each_reintroduced_executable_is_reported() -> None:
    errors = _validate(executables={"xdg-email": "/usr/bin/xdg-email"})

    assert errors == ["executable must be absent: xdg-email at /usr/bin/xdg-email"]


def test_the_single_documented_unmet_dependency_is_allowed() -> None:
    assert unmet_dependencies(RESIDUE_RECORDS) == {("texlive-base", "xdg-utils")}
    assert _validate(records=RESIDUE_RECORDS) == []


def test_any_other_unmet_dependency_is_reported() -> None:
    records = [*RESIDUE_RECORDS, ("latexml", "libxml-libxml-perl", "")]

    assert _validate(records=records) == [
        "unexpected unmet dependency: latexml needs libxml-libxml-perl"
    ]


def test_virtual_package_provides_satisfy_dependencies() -> None:
    assert unmet_dependencies(CLEAN_RECORDS) == set()
