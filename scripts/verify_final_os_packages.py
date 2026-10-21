#!/usr/bin/env python3
"""Verify the Debian package state and system trust store of the final image.

xdg-utils is removed, yet texlive-base still depends on it. An empty local
placeholder package provides the name so the dpkg database stays consistent
and apt keeps working in images built from this one.

The system trust store must not trust emSign ECC Root CA - G3 and must keep the
three emSign roots that are not affected (GHSA-6q48-6c7w-9gw2).

Usage: verify_final_os_packages.py [packages | trust-store]
Without an argument both checks run. The developer image has no placeholder
package, so it runs only the trust-store check.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

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
# texlive-base declares a hard dependency on xdg-utils. The Dockerfile satisfies
# it with an empty package that Provides xdg-utils. Its name differs from
# xdg-utils so vulnerability scanners do not inventory the removed package.
PLACEHOLDER_PACKAGE = "aelira-xdg-utils-absent"
PLACEHOLDER_PROVIDES = ("xdg-utils",)
# dpkg lists the root directory of every package, even one with no payload.
ROOT_DIRECTORY_ENTRY = "/."

FIELD_SPLIT = re.compile(r"\s*,\s*")

# emSign ECC Root CA - G3: eight intermediate CAs that chain to it have
# compromised private keys (GHSA-6q48-6c7w-9gw2). Debian trixie ca-certificates
# 20250419 ships it as mozilla/emSign_ECC_Root_CA_-_G3.crt; the security update
# 20250419+deb13u1 removes it. The Dockerfiles deselect it with a "!" line in
# /etc/ca-certificates.conf for images that still install 20250419. The check
# is by SHA-256 of the DER certificate, not by file name, so it does not depend
# on how Debian or the Dockerfile removed the root.
#
# The fingerprint was computed from that file in the 20250419 package (openssl
# x509 -fingerprint -sha256, read from the published 0.9.13 test image) and
# cross-checked against Mozilla's CCADB "Included CA Certificate Report (PEM)"
# retrieved 2026-10-21, whose PEM column for this root hashes to the same
# value. pip 26.2.2's vendored certifi 2026.10.19 bundle does not contain it
# and pip 26.2.1's 2026.6.17 bundle does.
DISTRUSTED_ROOT_NAME = "emSign ECC Root CA - G3"
DISTRUSTED_ROOT_SHA256 = (
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
)
# OpenSSL subject hash of that root: the hashed lookup link in /etc/ssl/certs
# is named <hash>.<n>, and it makes OpenSSL trust the root without the bundle.
DISTRUSTED_ROOT_HASH = "14bc7599"
# The other emSign roots stay trusted: they are not affected, and requiring
# them proves the bundle was not emptied to satisfy the check. Fingerprints
# come from the same Debian package files and match the same CCADB report. If
# Debian or Mozilla later drops one of them, update this list deliberately.
RETAINED_ROOTS = (
    (
        "emSign Root CA - G1",
        "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367",
    ),
    (
        "emSign Root CA - C1",
        "125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f",
    ),
    (
        "emSign ECC Root CA - C3",
        "bc4d809b15189d78db3e1d8cf4f9726a795da1643ca5f1358e1ddb0edc0d7eb3",
    ),
)
CA_DIRECTORY = Path("/etc/ssl/certs")
CA_BUNDLE = CA_DIRECTORY / "ca-certificates.crt"

PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
PEM_END = "-----END CERTIFICATE-----"
PEM_BLOCK = re.compile(re.escape(PEM_BEGIN) + r"(.*?)" + re.escape(PEM_END), re.DOTALL)
# A DER certificate is an ASN.1 SEQUENCE.
DER_SEQUENCE_TAG = 0x30


def package_installed(name: str) -> bool:
    """Return whether dpkg records the package as present in any form."""
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "not-installed"}


def package_files(name: str) -> list[str]:
    """Return every path dpkg records for an installed package."""
    result = subprocess.run(
        ["dpkg-query", "-L", name],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


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


def placeholder_errors(
    records: Iterable[tuple[str, str, str]],
    files: Callable[[str], list[str]],
) -> list[str]:
    """Return every way the xdg-utils placeholder deviates from its contract."""
    fields = [
        provides for package, _, provides in records if package == PLACEHOLDER_PACKAGE
    ]
    if not fields:
        return [f"placeholder must be installed: {PLACEHOLDER_PACKAGE}"]
    errors = []
    provides = tuple(item.strip() for item in fields[0].split(",") if item.strip())
    if provides != PLACEHOLDER_PROVIDES:
        errors.append(
            f"placeholder must provide exactly {', '.join(PLACEHOLDER_PROVIDES)}: "
            f"{PLACEHOLDER_PACKAGE} provides {', '.join(provides) or 'nothing'}"
        )
    errors.extend(
        f"placeholder must own no files: {path}"
        for path in files(PLACEHOLDER_PACKAGE)
        if path != ROOT_DIRECTORY_ENTRY
    )
    return errors


def validate(
    *,
    installed: Callable[[str], bool] = package_installed,
    executable: Callable[[str], str | None] = shutil.which,
    records: Callable[[], Iterable[tuple[str, str, str]]] = dpkg_records,
    files: Callable[[str], list[str]] = package_files,
    packages: Iterable[str] = ABSENT_PACKAGES,
    executables: Iterable[str] = ABSENT_EXECUTABLES,
) -> list[str]:
    """Return every violation of the final-image package state."""
    errors = [f"package must be absent: {name}" for name in packages if installed(name)]
    for name in executables:
        path = executable(name)
        if path:
            errors.append(f"executable must be absent: {name} at {path}")
    installed_records = list(records())
    errors.extend(placeholder_errors(installed_records, files))
    for package, dependency in sorted(unmet_dependencies(installed_records)):
        errors.append(f"unmet dependency: {package} needs {dependency}")
    return errors


def pem_fingerprints(text: str) -> set[str]:
    """Return the SHA-256 of every certificate in PEM text, as lowercase hex.

    Anything that is not a balanced run of well-formed certificates raises
    ValueError, so a truncated or corrupted bundle cannot pass for one that
    merely lacks the distrusted root.
    """
    begins = text.count(PEM_BEGIN)
    blocks = PEM_BLOCK.findall(text)
    if begins != text.count(PEM_END) or begins != len(blocks):
        raise ValueError("unbalanced certificate markers")
    fingerprints = set()
    for body in blocks:
        try:
            der = base64.b64decode("".join(body.split()), validate=True)
        except binascii.Error as exc:
            raise ValueError(f"certificate is not valid base64: {exc}") from exc
        if not der or der[0] != DER_SEQUENCE_TAG:
            raise ValueError("certificate body is not DER")
        fingerprints.add(hashlib.sha256(der).hexdigest())
    return fingerprints


def _read_pem_text(path: Path) -> str:
    # Undecodable bytes become U+FFFD, which never forms valid base64, so a
    # corrupted certificate is reported as malformed instead of ignored.
    return path.read_bytes().decode("ascii", errors="replace")


def _same_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return False


def trust_store_errors(
    bundle: Path | None = None, directory: Path | None = None
) -> list[str]:
    """Return every way the system trust store trusts the distrusted root.

    The bundle and every certificate reachable through the hashed lookup links
    in the directory are both consulted by clients (curl and Python read the
    bundle, OpenSSL's capath lookup reads the links), so both must be clean and
    both must still hold the retained emSign roots. A bundle or certificate
    that cannot be read or parsed is an error: this check fails closed.
    """
    bundle = CA_BUNDLE if bundle is None else bundle
    directory = CA_DIRECTORY if directory is None else directory
    errors: list[str] = []

    bundled: set[str] | None = None
    try:
        bundled = pem_fingerprints(_read_pem_text(bundle))
    except OSError as exc:
        errors.append(f"trust store: unable to read {bundle}: {exc}")
    except ValueError as exc:
        errors.append(f"trust store: {bundle} is malformed: {exc}")
    if bundled is not None and DISTRUSTED_ROOT_SHA256 in bundled:
        errors.append(
            f"trust store: {bundle} contains {DISTRUSTED_ROOT_NAME} "
            f"(sha256 {DISTRUSTED_ROOT_SHA256})"
        )

    linked: set[str] = set()
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        errors.append(f"trust store: unable to inspect {directory}: {exc}")
        entries = []
    for entry in entries:
        if entry.name.startswith(f"{DISTRUSTED_ROOT_HASH}."):
            errors.append(
                f"trust store: hashed link for {DISTRUSTED_ROOT_NAME} remains: "
                f"{entry}"
            )
            continue
        if _same_file(entry, bundle) or entry.is_dir():
            continue
        try:
            text = _read_pem_text(entry)
        except FileNotFoundError:
            continue  # a dangling link trusts nothing
        except OSError as exc:
            errors.append(f"trust store: unable to read {entry}: {exc}")
            continue
        if PEM_BEGIN not in text:
            continue
        try:
            fingerprints = pem_fingerprints(text)
        except ValueError as exc:
            errors.append(f"trust store: {entry} is malformed: {exc}")
            continue
        linked |= fingerprints
        if DISTRUSTED_ROOT_SHA256 in fingerprints:
            errors.append(
                f"trust store: {entry} is a certificate for {DISTRUSTED_ROOT_NAME} "
                f"(sha256 {DISTRUSTED_ROOT_SHA256})"
            )

    for name, fingerprint in RETAINED_ROOTS:
        if bundled is not None and fingerprint not in bundled:
            errors.append(f"trust store: {bundle} lacks {name} (sha256 {fingerprint})")
        if fingerprint not in linked:
            errors.append(f"trust store: {directory} has no certificate for {name}")
    return errors


CHECKS: dict[str, Callable[[], list[str]]] = {
    "packages": validate,
    "trust-store": trust_store_errors,
}
SUCCESS_MESSAGES = {
    "packages": "Final image OS package state is valid",
    "trust-store": "Final image system trust store is valid",
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        selected = list(CHECKS)
    elif len(arguments) == 1 and arguments[0] in CHECKS:
        selected = arguments
    else:
        print(
            f"usage: verify_final_os_packages.py [{' | '.join(CHECKS)}]",
            file=sys.stderr,
        )
        return 2
    failed = False
    for name in selected:
        errors = CHECKS[name]()
        if errors:
            failed = True
            for error in errors:
                print(error, file=sys.stderr)
        else:
            print(SUCCESS_MESSAGES[name])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
