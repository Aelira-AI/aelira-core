"""Behavior tests for final-image Debian package verification."""

from __future__ import annotations

from scripts.verify_final_os_packages import (
    ABSENT_EXECUTABLES,
    ABSENT_PACKAGES,
    PLACEHOLDER_PACKAGE,
    PLACEHOLDER_PROVIDES,
    unmet_dependencies,
    validate,
)

LIBC = ("libc6", "", "")
DEBCONF = ("debconf", "", "debconf-2.0")
TEXLIVE = ("texlive-base", "libc6 (>= 2.36), debconf | debconf-2.0, xdg-utils", "")
PLACEHOLDER = (PLACEHOLDER_PACKAGE, "", "xdg-utils")
# What dpkg lists for a package with no payload: only the root directory.
NO_PAYLOAD = ["/."]

# The shipped state: xdg-utils removed, placeholder installed in its place.
CLEAN_RECORDS = [LIBC, TEXLIVE, DEBCONF, PLACEHOLDER]
# The v0.9.13 state: xdg-utils purged with no placeholder.
UNMET_RECORDS = [LIBC, TEXLIVE, DEBCONF]


def _validate(
    installed: set[str] | None = None,
    executables: dict[str, str] | None = None,
    records=CLEAN_RECORDS,
    files: dict[str, list[str]] | None = None,
):
    present = installed or set()
    paths = executables or {}
    owned = files or {PLACEHOLDER_PACKAGE: NO_PAYLOAD}
    return validate(
        installed=lambda name: name in present,
        executable=paths.get,
        records=lambda: records,
        files=lambda name: owned[name],
    )


def test_absent_package_state_is_accepted() -> None:
    assert _validate() == []


def test_removed_packages_and_executables_are_pinned() -> None:
    assert ABSENT_PACKAGES == ("xdg-utils", "libxml2-utils")
    assert {"xdg-email", "xdg-open", "xmllint", "xmlcatalog"} <= set(ABSENT_EXECUTABLES)


def test_placeholder_contract_is_pinned() -> None:
    assert PLACEHOLDER_PACKAGE == "aelira-xdg-utils-absent"
    assert PLACEHOLDER_PROVIDES == ("xdg-utils",)
    # A scanner that matches the removed package by name must not match this.
    assert PLACEHOLDER_PACKAGE not in ABSENT_PACKAGES


def test_each_reintroduced_package_is_reported() -> None:
    errors = _validate(installed={"xdg-utils", "libxml2-utils"})

    assert errors == [
        "package must be absent: xdg-utils",
        "package must be absent: libxml2-utils",
    ]


def test_real_xdg_utils_fails_even_when_the_placeholder_is_installed() -> None:
    records = [*CLEAN_RECORDS, ("xdg-utils", "", "")]

    assert _validate(installed={"xdg-utils"}, records=records) == [
        "package must be absent: xdg-utils"
    ]


def test_real_xdg_utils_fails_in_place_of_the_placeholder() -> None:
    # The v0.9.12 layout satisfied texlive-base with the real package.
    records = [*UNMET_RECORDS, ("xdg-utils", "", "")]

    assert unmet_dependencies(records) == set()
    assert _validate(installed={"xdg-utils"}, records=records) == [
        "package must be absent: xdg-utils",
        f"placeholder must be installed: {PLACEHOLDER_PACKAGE}",
    ]


def test_each_reintroduced_executable_is_reported() -> None:
    errors = _validate(executables={"xdg-email": "/usr/bin/xdg-email"})

    assert errors == ["executable must be absent: xdg-email at /usr/bin/xdg-email"]


def test_placeholder_satisfies_the_texlive_base_dependency() -> None:
    assert unmet_dependencies(UNMET_RECORDS) == {("texlive-base", "xdg-utils")}
    assert unmet_dependencies(CLEAN_RECORDS) == set()
    assert _validate(records=CLEAN_RECORDS) == []


def test_unmet_texlive_base_dependency_is_now_rejected() -> None:
    # The release that purged xdg-utils without a replacement allowed exactly
    # this residue. It broke apt in derived images, so nothing is allowed now.
    errors = _validate(records=UNMET_RECORDS)

    assert "unmet dependency: texlive-base needs xdg-utils" in errors
    assert errors == [
        f"placeholder must be installed: {PLACEHOLDER_PACKAGE}",
        "unmet dependency: texlive-base needs xdg-utils",
    ]


def test_any_other_unmet_dependency_is_reported() -> None:
    records = [*CLEAN_RECORDS, ("latexml", "libxml-libxml-perl", "")]

    assert _validate(records=records) == [
        "unmet dependency: latexml needs libxml-libxml-perl"
    ]


def test_placeholder_that_ships_a_file_is_rejected() -> None:
    errors = _validate(
        files={PLACEHOLDER_PACKAGE: ["/.", "/usr", "/usr/bin", "/usr/bin/xdg-email"]}
    )

    assert errors == [
        "placeholder must own no files: /usr",
        "placeholder must own no files: /usr/bin",
        "placeholder must own no files: /usr/bin/xdg-email",
    ]


def test_placeholder_that_provides_more_than_xdg_utils_is_rejected() -> None:
    records = [LIBC, TEXLIVE, DEBCONF, (PLACEHOLDER_PACKAGE, "", "xdg-utils, mailx")]

    assert _validate(records=records) == [
        "placeholder must provide exactly xdg-utils: "
        f"{PLACEHOLDER_PACKAGE} provides xdg-utils, mailx"
    ]


def test_placeholder_that_provides_something_else_is_rejected() -> None:
    records = [LIBC, TEXLIVE, DEBCONF, (PLACEHOLDER_PACKAGE, "", "xdg-email")]

    assert _validate(records=records) == [
        "placeholder must provide exactly xdg-utils: "
        f"{PLACEHOLDER_PACKAGE} provides xdg-email",
        "unmet dependency: texlive-base needs xdg-utils",
    ]


def test_placeholder_with_a_versioned_provide_is_rejected() -> None:
    records = [
        LIBC,
        TEXLIVE,
        DEBCONF,
        (PLACEHOLDER_PACKAGE, "", "xdg-utils (= 1.0)"),
    ]

    assert _validate(records=records) == [
        "placeholder must provide exactly xdg-utils: "
        f"{PLACEHOLDER_PACKAGE} provides xdg-utils (= 1.0)"
    ]


def test_placeholder_that_provides_nothing_is_rejected() -> None:
    records = [LIBC, TEXLIVE, DEBCONF, (PLACEHOLDER_PACKAGE, "", "")]

    assert _validate(records=records) == [
        "placeholder must provide exactly xdg-utils: "
        f"{PLACEHOLDER_PACKAGE} provides nothing",
        "unmet dependency: texlive-base needs xdg-utils",
    ]


def test_virtual_package_provides_satisfy_dependencies() -> None:
    assert unmet_dependencies(CLEAN_RECORDS) == set()
    assert unmet_dependencies([("a", "debconf-2.0", ""), DEBCONF]) == set()
