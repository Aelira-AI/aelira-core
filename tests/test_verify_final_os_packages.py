"""Behavior tests for final-image Debian package verification."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from scripts.verify_final_os_packages import (
    ABSENT_EXECUTABLES,
    ABSENT_PACKAGES,
    CHECKS,
    DEBIAN_FFMPEG_PACKAGES,
    DISTRUSTED_ROOT_HASH,
    DISTRUSTED_ROOT_NAME,
    DISTRUSTED_ROOT_SHA256,
    PLACEHOLDER_PACKAGE,
    PLACEHOLDER_PROVIDES,
    RETAINED_ROOTS,
    main,
    pem_fingerprints,
    trust_store_errors,
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
    assert ABSENT_PACKAGES == ("xdg-utils", "libxml2-utils", *DEBIAN_FFMPEG_PACKAGES)
    assert set(DEBIAN_FFMPEG_PACKAGES) == {
        "ffmpeg",
        "libavcodec61",
        "libavdevice61",
        "libavfilter10",
        "libavformat61",
        "libavutil59",
        "libpostproc58",
        "libswresample5",
        "libswscale8",
        "librist4",
        "libcjson1",
        "libsndfile1",
    }
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


@pytest.mark.parametrize("package", DEBIAN_FFMPEG_PACKAGES)
def test_each_reintroduced_debian_ffmpeg_package_is_reported(package: str) -> None:
    # The 0.9.14 image carried every one of these through Debian's ffmpeg.
    assert _validate(installed={package}) == [f"package must be absent: {package}"]


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


# --- System trust store -----------------------------------------------------

# The four emSign roots as published in Mozilla's CCADB included-certificate
# report (retrieved 2026-10-21), frozen here independently of the verifier.
# They are public certificates, so the tests exercise real fingerprints.
EMSIGN_ECC_G3_BODY = (
    "MIICTjCCAdOgAwIBAgIKPPYHqWhwDtqLhDAKBggqhkjOPQQDAzBrMQswCQYDVQQG\n"
    "EwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNo\n"
    "bm9sb2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0g\n"
    "RzMwHhcNMTgwMjE4MTgzMDAwWhcNNDMwMjE4MTgzMDAwWjBrMQswCQYDVQQGEwJJ\n"
    "TjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNobm9s\n"
    "b2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0gRzMw\n"
    "djAQBgcqhkjOPQIBBgUrgQQAIgNiAAQjpQy4LRL1KPOxst3iAhKAnjlfSU2fySU0\n"
    "WXTsuwYc58Byr+iuL+FBVIcUqEqy6HyC5ltqtdyzdc6LBtCGI79G1Y4PPwT01xyS\n"
    "fvalY8L1X44uT6EYGQIrMgqCZH0Wk9GjQjBAMB0GA1UdDgQWBBR8XQKEE9TMipuB\n"
    "zhccLikenEhjQjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggq\n"
    "hkjOPQQDAwNpADBmAjEAvvNhzwIQHWSVB7gYboiFBS+DCBeQyh+KTOgNG3qxrdWB\n"
    "CUfvO6wIBHxcmbHtRwfSAjEAnbpV/KlK6O3t5nYBQnvI+GDZjVGLVTv7jHvrZQnD\n"
    "+JbNR6iC8hZVdyR+EhCVBCyj\n"
)

EMSIGN_G1_BODY = (
    "MIIDlDCCAnygAwIBAgIKMfXkYgxsWO3W2DANBgkqhkiG9w0BAQsFADBnMQswCQYD\n"
    "VQQGEwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBU\n"
    "ZWNobm9sb2dpZXMgTGltaXRlZDEcMBoGA1UEAxMTZW1TaWduIFJvb3QgQ0EgLSBH\n"
    "MTAeFw0xODAyMTgxODMwMDBaFw00MzAyMTgxODMwMDBaMGcxCzAJBgNVBAYTAklO\n"
    "MRMwEQYDVQQLEwplbVNpZ24gUEtJMSUwIwYDVQQKExxlTXVkaHJhIFRlY2hub2xv\n"
    "Z2llcyBMaW1pdGVkMRwwGgYDVQQDExNlbVNpZ24gUm9vdCBDQSAtIEcxMIIBIjAN\n"
    "BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAk0u76WaK7p1b1TST0Bsew+eeuGQz\n"
    "f2N4aLTNLnF115sgxk0pvLZoYIr3IZpWNVrzdr3YzZr/k1ZLpVkGoZM0Kd0WNHVO\n"
    "8oG0x5ZOrRkVUkr+PHB1cM2vK6sVmjM8qrOLqs1D/fXqcP/tzxE7lM5OMhbTI0Aq\n"
    "d7OvPAEsbO2ZLIvZTmmYsvePQbAyeGHWDV/D+qJAkh1cF+ZwPjXnorfCYuKrpDhM\n"
    "tTk1b+oDafo6VGiFbdbyL0NVHpENDtjVaqSW0RM8LHhQ6DqS0hdW5TUaQBw+jSzt\n"
    "Od9C4INBdN+jzcKGYEho42kLVACL5HZpIQ15TjQIXhTCzLG3rdd8cIrHhQIDAQAB\n"
    "o0IwQDAdBgNVHQ4EFgQU++8Nhp6w492pufEhF38+/PB3KxowDgYDVR0PAQH/BAQD\n"
    "AgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAFn/8oz1h31x\n"
    "PaOfG1vR2vjTnGs2vZupYeveFix0PZ7mddrXuqe8QhfnPZHr5X3dPpzxz5KsbEjM\n"
    "wiI/aTvFthUvozXGaCocV685743QNcMYDHsAVhzNixl03r4PEuDQqqE/AjSxcM6d\n"
    "GNYIAwlG7mDgfrbESQRRfXBgvKqy/3lyeqYdPV8q+Mri/Tm3R7nrft8EI6/6nAYH\n"
    "6ftjk4BAtcZsCjEozgyfz7MjNYBBjWzEN3uBL4ChQEKF6dk4jeihU80Bv2noWgby\n"
    "RQuQ+q7hv53yrlc8pa6yVvSLZUDp/TGBLPQ5Cdjua6e0ph0VpZj3AYHYhX3zUVxx\n"
    "iN66zB+Afko=\n"
)

EMSIGN_C1_BODY = (
    "MIIDczCCAlugAwIBAgILAK7PALrEzzL4Q7IwDQYJKoZIhvcNAQELBQAwVjELMAkG\n"
    "A1UEBhMCVVMxEzARBgNVBAsTCmVtU2lnbiBQS0kxFDASBgNVBAoTC2VNdWRocmEg\n"
    "SW5jMRwwGgYDVQQDExNlbVNpZ24gUm9vdCBDQSAtIEMxMB4XDTE4MDIxODE4MzAw\n"
    "MFoXDTQzMDIxODE4MzAwMFowVjELMAkGA1UEBhMCVVMxEzARBgNVBAsTCmVtU2ln\n"
    "biBQS0kxFDASBgNVBAoTC2VNdWRocmEgSW5jMRwwGgYDVQQDExNlbVNpZ24gUm9v\n"
    "dCBDQSAtIEMxMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAz+upufGZ\n"
    "BczYKCFK83M0UYRWEPWgTywS4/oTmifQz/l5GnRfHXk5/Fv4cI7gklL35CX5VIPZ\n"
    "HdPIWoU/Xse2B+4+wM6ar6xWQio5JXDWv7V7Nq2s9nPczdcdioOl+yuQFTdrHCZH\n"
    "3DspVpNqs8FqOp099cGXOFgFixwR4+S0uF2FHYP+eF8LRWgYSKVGczQ7/g/IdrvH\n"
    "GPMF0Ybzhe3nudkyrVWIzqa2kbBPrH4VI5b2P/AgNBbeCsbEBEV5f6f9vtKppa+c\n"
    "xSMq9zwhbL2vj07FOrLzNBL834AaSaTUqZX3noleoomslMuoaJuvimUnzYnu3Yy1\n"
    "aylwQ6BpC+S5DwIDAQABo0IwQDAdBgNVHQ4EFgQU/qHgcB4qAzlSWkK+XJGFehiq\n"
    "TbUwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL\n"
    "BQADggEBAMJKVvoVIXsoounlHfv4LcQ5lkFMOycsxGwYFYDGrK9HWS8mC+M2sO87\n"
    "/kOXSTKZEhVb3xEp/6tT+LvBeA+snFOvV71ojD1pM/CjoCNjO2RnIkSt1XHLVip4\n"
    "kqNPEjE2NuLe/gDEo2APJ62gsIq1NnpSob0n9CAnYuhNlCQT5AoE6TyrLshDCUrG\n"
    "YQTlSTR+08TI9Q/Aqum6VF7zYytPT1DU/rl7mYw9wC68AivTxEDkigcxHpvOJpkT\n"
    "+xHqmiIMERnHXhuBUDDIlhJu58tBf5E7oke3VIAb3ADMmpDqw8NQBmIMMMAVSKeo\n"
    "WXzhriKi4gp6D/piq1JM4fHfyr6DDUI=\n"
)

EMSIGN_ECC_C3_BODY = (
    "MIICKzCCAbGgAwIBAgIKe3G2gla4EnycqDAKBggqhkjOPQQDAzBaMQswCQYDVQQG\n"
    "EwJVUzETMBEGA1UECxMKZW1TaWduIFBLSTEUMBIGA1UEChMLZU11ZGhyYSBJbmMx\n"
    "IDAeBgNVBAMTF2VtU2lnbiBFQ0MgUm9vdCBDQSAtIEMzMB4XDTE4MDIxODE4MzAw\n"
    "MFoXDTQzMDIxODE4MzAwMFowWjELMAkGA1UEBhMCVVMxEzARBgNVBAsTCmVtU2ln\n"
    "biBQS0kxFDASBgNVBAoTC2VNdWRocmEgSW5jMSAwHgYDVQQDExdlbVNpZ24gRUND\n"
    "IFJvb3QgQ0EgLSBDMzB2MBAGByqGSM49AgEGBSuBBAAiA2IABP2lYa57JhAd6bci\n"
    "MK4G9IGzsUJxlTm801Ljr6/58pc1kjZGDoeVjbk5Wum739D+yAdBPLtVb4Ojavti\n"
    "sIGJAnB9SMVK4+kiVCJNk7tCDK93nCOmfddhEc5lx/h//vXyqaNCMEAwHQYDVR0O\n"
    "BBYEFPtaSNCAIEDyqOkAB2kZd6fmw/TPMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMB\n"
    "Af8EBTADAQH/MAoGCCqGSM49BAMDA2gAMGUCMQC02C8Cif22TGK6Q04ThHK1rt0c\n"
    "3ta13FaPWEBaLd4gTCKDypOofu4SQMfWh0/434UCMBwUZOR8loMRnLDRWmFLpg9J\n"
    "0wD8ofzkpf9/rdcw0Md3f76BB1UwUCAU9Vc4CqgxUQ==\n"
)


PUBLISHED_ROOTS = {
    "g3": (DISTRUSTED_ROOT_NAME, EMSIGN_ECC_G3_BODY),
    "g1": ("emSign Root CA - G1", EMSIGN_G1_BODY),
    "c1": ("emSign Root CA - C1", EMSIGN_C1_BODY),
    "c3": ("emSign ECC Root CA - C3", EMSIGN_ECC_C3_BODY),
}
# Stand-ins for the other roots in Mozilla's list: a well-formed DER SEQUENCE.
OTHER_ROOT_BODY = base64.b64encode(bytes.fromhex("3003020101")).decode() + "\n"
# Hash names as update-ca-certificates writes them. Only the distrusted root's
# is real; the rest just need to be distinct.
LINK_HASHES = {
    "g3": DISTRUSTED_ROOT_HASH,
    "g1": "2923b3f9",
    "c1": "406c9bb1",
    "c3": "4b718d9b",
}
RETAINED = ("g1", "c1", "c3")


def _pem(body: str) -> str:
    return f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n"


def _store(
    tmp_path: Path,
    *,
    bundle: tuple[str, ...] = RETAINED,
    linked: tuple[str, ...] | None = None,
) -> tuple[Path, Path]:
    """Lay out a trust store the way update-ca-certificates does.

    The bundle concatenates certificates; the directory holds a named link and
    a hashed link for each one, pointing at files under a separate share dir.
    """
    share = tmp_path / "share"
    directory = tmp_path / "certs"
    share.mkdir()
    directory.mkdir()
    for key in RETAINED if linked is None else linked:
        crt = share / f"{key}.crt"
        crt.write_text(_pem(PUBLISHED_ROOTS[key][1]))
        (directory / f"{key}.pem").symlink_to(crt)
        (directory / f"{LINK_HASHES[key]}.0").symlink_to(f"{key}.pem")
    bundle_path = directory / "ca-certificates.crt"
    bundle_path.write_text(
        _pem(OTHER_ROOT_BODY) + "".join(_pem(PUBLISHED_ROOTS[key][1]) for key in bundle)
    )
    return bundle_path, directory


def _check(tmp_path: Path, **layout: tuple[str, ...]) -> list[str]:
    bundle, directory = _store(tmp_path, **layout)
    return trust_store_errors(bundle, directory)


def _fingerprint(body: str) -> str:
    return hashlib.sha256(base64.b64decode(body)).hexdigest()


def test_pinned_fingerprints_match_the_published_certificates() -> None:
    assert _fingerprint(EMSIGN_ECC_G3_BODY) == DISTRUSTED_ROOT_SHA256
    assert DISTRUSTED_ROOT_SHA256 == (
        "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
    )
    assert DISTRUSTED_ROOT_HASH == "14bc7599"
    assert dict(RETAINED_ROOTS) == {
        PUBLISHED_ROOTS[key][0]: _fingerprint(PUBLISHED_ROOTS[key][1])
        for key in RETAINED
    }


def test_only_the_three_unaffected_emsign_roots_are_retained() -> None:
    assert sorted(name for name, _ in RETAINED_ROOTS) == [
        "emSign ECC Root CA - C3",
        "emSign Root CA - C1",
        "emSign Root CA - G1",
    ]
    assert DISTRUSTED_ROOT_SHA256 not in {value for _, value in RETAINED_ROOTS}


def test_trust_store_without_the_distrusted_root_is_accepted(tmp_path: Path) -> None:
    assert _check(tmp_path) == []


def test_distrusted_root_in_the_bundle_is_reported(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path, bundle=(*RETAINED, "g3"))

    assert trust_store_errors(bundle, directory) == [
        f"trust store: {bundle} contains {DISTRUSTED_ROOT_NAME} "
        f"(sha256 {DISTRUSTED_ROOT_SHA256})"
    ]


def test_distrusted_root_is_found_wherever_it_sits_in_the_bundle(
    tmp_path: Path,
) -> None:
    bundle, directory = _store(tmp_path)
    bundle.write_text(
        _pem(PUBLISHED_ROOTS["g3"][1])
        + "".join(_pem(PUBLISHED_ROOTS[key][1]) for key in RETAINED)
    )

    assert len(trust_store_errors(bundle, directory)) == 1


def test_hashed_link_for_the_distrusted_root_is_reported(tmp_path: Path) -> None:
    # The bundle is clean, but OpenSSL's capath lookup would still find it.
    bundle, directory = _store(tmp_path, linked=(*RETAINED, "g3"))

    assert trust_store_errors(bundle, directory) == [
        f"trust store: hashed link for {DISTRUSTED_ROOT_NAME} remains: "
        f"{directory / (DISTRUSTED_ROOT_HASH + '.0')}",
        f"trust store: {directory / 'g3.pem'} is a certificate for "
        f"{DISTRUSTED_ROOT_NAME} (sha256 {DISTRUSTED_ROOT_SHA256})",
    ]


def test_dangling_hashed_link_for_the_distrusted_root_is_reported(
    tmp_path: Path,
) -> None:
    bundle, directory = _store(tmp_path)
    (directory / f"{DISTRUSTED_ROOT_HASH}.0").symlink_to("emSign_ECC_Root_CA_-_G3.pem")

    assert trust_store_errors(bundle, directory) == [
        f"trust store: hashed link for {DISTRUSTED_ROOT_NAME} remains: "
        f"{directory / (DISTRUSTED_ROOT_HASH + '.0')}"
    ]


def test_distrusted_root_is_found_by_content_under_any_name(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    (directory / "renamed-by-someone.0").write_text(_pem(PUBLISHED_ROOTS["g3"][1]))

    assert trust_store_errors(bundle, directory) == [
        f"trust store: {directory / 'renamed-by-someone.0'} is a certificate for "
        f"{DISTRUSTED_ROOT_NAME} (sha256 {DISTRUSTED_ROOT_SHA256})"
    ]


@pytest.mark.parametrize("missing", RETAINED)
def test_each_unaffected_root_missing_from_the_bundle_is_reported(
    tmp_path: Path, missing: str
) -> None:
    name = PUBLISHED_ROOTS[missing][0]
    bundle, directory = _store(
        tmp_path, bundle=tuple(key for key in RETAINED if key != missing)
    )

    assert trust_store_errors(bundle, directory) == [
        f"trust store: {bundle} lacks {name} "
        f"(sha256 {_fingerprint(PUBLISHED_ROOTS[missing][1])})"
    ]


@pytest.mark.parametrize("missing", RETAINED)
def test_each_unaffected_root_missing_from_the_directory_is_reported(
    tmp_path: Path, missing: str
) -> None:
    name = PUBLISHED_ROOTS[missing][0]
    bundle, directory = _store(
        tmp_path, linked=tuple(key for key in RETAINED if key != missing)
    )

    assert trust_store_errors(bundle, directory) == [
        f"trust store: {directory} has no certificate for {name}"
    ]


def test_an_emptied_bundle_cannot_pass(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    bundle.write_text("")

    errors = trust_store_errors(bundle, directory)

    assert len(errors) == len(RETAINED_ROOTS)
    assert all(
        f"lacks {name}" in error for (name, _), error in zip(RETAINED_ROOTS, errors)
    )


def test_an_emptied_directory_cannot_pass(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path, linked=())

    errors = trust_store_errors(bundle, directory)

    assert len(errors) == len(RETAINED_ROOTS)
    assert all("has no certificate for" in error for error in errors)


def test_bundle_without_any_certificate_markers_cannot_pass(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    bundle.write_text("this is not a certificate bundle\n")

    assert len(trust_store_errors(bundle, directory)) == len(RETAINED_ROOTS)


def test_missing_bundle_fails_closed(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    bundle.unlink()

    errors = trust_store_errors(bundle, directory)

    assert errors[0].startswith(f"trust store: unable to read {bundle}: ")


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("-----BEGIN CERTIFICATE-----\nMIIB\n", "unbalanced certificate markers"),
        ("-----END CERTIFICATE-----\n", "unbalanced certificate markers"),
        (
            "-----BEGIN CERTIFICATE-----\n-----BEGIN CERTIFICATE-----\n"
            "-----END CERTIFICATE-----\n-----END CERTIFICATE-----\n",
            "unbalanced certificate markers",
        ),
        (
            "-----BEGIN CERTIFICATE-----\n!!!not base64!!!\n-----END CERTIFICATE-----\n",
            "certificate is not valid base64",
        ),
        (
            "-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----\n",
            "certificate is not valid base64",
        ),
        (
            _pem(base64.b64encode(b"hello world").decode() + "\n"),
            "certificate body is not DER",
        ),
        ("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n", "not DER"),
    ],
)
def test_malformed_bundle_fails_closed(
    tmp_path: Path, content: str, reason: str
) -> None:
    bundle, directory = _store(tmp_path)
    bundle.write_text(content)

    errors = trust_store_errors(bundle, directory)

    assert len(errors) == 1
    assert errors[0].startswith(f"trust store: {bundle} is malformed: ")
    assert reason in errors[0]


def test_bundle_cut_off_after_the_distrusted_root_fails_closed(
    tmp_path: Path,
) -> None:
    bundle, directory = _store(tmp_path)
    full = _pem(PUBLISHED_ROOTS["g3"][1])
    bundle.write_text(full + full[: len(full) // 2])

    errors = trust_store_errors(bundle, directory)

    assert errors == [
        f"trust store: {bundle} is malformed: unbalanced certificate markers"
    ]


def test_bundle_with_corrupted_bytes_fails_closed(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    good = _pem(PUBLISHED_ROOTS["g3"][1]).encode()
    bundle.write_bytes(good.replace(b"MIICTj", b"MIIC\xffj", 1))

    errors = trust_store_errors(bundle, directory)

    assert len(errors) == 1 and "is malformed" in errors[0]


def test_malformed_certificate_in_the_directory_fails_closed(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    (directory / "broken.pem").write_text(
        "-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n"
    )

    errors = trust_store_errors(bundle, directory)

    assert len(errors) == 1
    assert errors[0].startswith(
        f"trust store: {directory / 'broken.pem'} is malformed: "
        "certificate is not valid base64"
    )


def test_unrelated_entries_in_the_directory_are_ignored(tmp_path: Path) -> None:
    bundle, directory = _store(tmp_path)
    (directory / "java").mkdir()
    (directory / "README").write_text("not a certificate\n")
    (directory / "binary.der").write_bytes(bytes.fromhex("3003020101"))
    (directory / "dangling.pem").symlink_to(tmp_path / "gone.crt")

    assert trust_store_errors(bundle, directory) == []


def test_missing_directory_fails_closed(tmp_path: Path) -> None:
    bundle, _ = _store(tmp_path)
    missing = tmp_path / "nowhere"

    errors = trust_store_errors(bundle, missing)

    assert errors[0].startswith(f"trust store: unable to inspect {missing}: ")
    assert len(errors) == 1 + len(RETAINED_ROOTS)


def test_pem_fingerprints_returns_lowercase_sha256_of_each_certificate() -> None:
    text = _pem(EMSIGN_ECC_G3_BODY) + _pem(EMSIGN_G1_BODY)

    assert pem_fingerprints(text) == {
        _fingerprint(EMSIGN_ECC_G3_BODY),
        _fingerprint(EMSIGN_G1_BODY),
    }
    assert pem_fingerprints("") == set()


def test_default_paths_are_the_system_store() -> None:
    from scripts.verify_final_os_packages import CA_BUNDLE, CA_DIRECTORY

    assert CA_DIRECTORY == Path("/etc/ssl/certs")
    assert CA_BUNDLE == Path("/etc/ssl/certs/ca-certificates.crt")


def test_main_runs_both_checks_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(CHECKS, "packages", lambda: ["package must be absent: x"])
    monkeypatch.setitem(CHECKS, "trust-store", lambda: [])

    assert main([]) == 1

    captured = capsys.readouterr()
    assert "package must be absent: x" in captured.err
    assert "Final image system trust store is valid" in captured.out


def test_main_trust_store_selector_runs_only_that_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def packages() -> list[str]:
        raise AssertionError("the package check must not run")

    monkeypatch.setitem(CHECKS, "packages", packages)
    monkeypatch.setitem(CHECKS, "trust-store", lambda: ["trust store: bad"])

    assert main(["trust-store"]) == 1
    assert capsys.readouterr().err == "trust store: bad\n"


def test_main_passes_when_every_selected_check_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(CHECKS, "packages", lambda: [])
    monkeypatch.setitem(CHECKS, "trust-store", lambda: [])

    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Final image OS package state is valid",
        "Final image system trust store is valid",
    ]


@pytest.mark.parametrize("arguments", [["unknown"], ["packages", "trust-store"]])
def test_main_rejects_unknown_arguments(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) == 2
    assert "usage: verify_final_os_packages.py" in capsys.readouterr().err
