#!/usr/bin/env python3
"""Verify the reviewed Python package state in a final API image filesystem."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import os
import re
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path
from typing import Any

EXPECTED = {
    "global": {"setuptools": None, "pip": None, "urllib3": None, "av": None},
    # pi-heif bundles libheif and libde265 and nothing in the image uses it.
    # pybind11 is only a build requirement of the source-built Pillow.
    "venv": {
        "setuptools": "84.0.0",
        "pip": None,
        "pi-heif": None,
        "pybind11": None,
        "Cython": None,
    },
}
# Pillow is built from source against libtiff 4.7.2 (docker/build-pillow-wheel.sh)
# and must keep the wheel's codec and text-shaping capabilities.
PILLOW_LIBTIFF = (4, 7, 2)
PILLOW_FEATURES = (
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


def required_psycopg2_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("psycopg2-binary", requirements)


def required_pyav_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("av", requirements)


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
    # Prove the embedded loader paths without help from container-wide
    # overrides. The same clean environment is used for both native gates.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD"}
    }
    return subprocess.run(
        ["ldd", str(extension)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


EXPAT_DEBIAN_VERSION = "2.9.0-1~deb13u1"
EXPAT_REPORTED_VERSION = "expat_2.9.0"


def validate_expat_native(
    *,
    ldd: Callable[[Path], str] = run_ldd,
    realpath: Callable[[str], str] = os.path.realpath,
) -> list[str]:
    """Prove the matching CPython extension pair uses Debian's shared Expat."""
    errors: list[str] = []
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not isinstance(suffix, str) or not suffix.startswith(".cpython-314-"):
        return [f"expat: unexpected CPython extension suffix {suffix!r}"]
    module_dir = Path("/usr/local/lib/python3.14/lib-dynload")
    expected_paths = {
        "pyexpat": module_dir / f"pyexpat{suffix}",
        "_elementtree": module_dir / f"_elementtree{suffix}",
    }
    for name, path in expected_paths.items():
        matches = sorted(module_dir.glob(f"{name}*.so"))
        if matches != [path]:
            errors.append(
                f"expat: expected only {path}; found "
                + ", ".join(str(item) for item in matches)
            )
    if errors:
        return errors

    try:
        import _elementtree
        import pyexpat
        from xml.dom import minidom
        from xml.etree import ElementTree
        from xml.sax import make_parser
    except (ImportError, OSError) as exc:
        return [f"expat: unable to import matching parser modules: {exc}"]

    for name, module in (("pyexpat", pyexpat), ("_elementtree", _elementtree)):
        if Path(module.__file__).resolve() != expected_paths[name]:
            errors.append(f"expat: {name} loaded from {module.__file__}")
    if pyexpat.EXPAT_VERSION != EXPAT_REPORTED_VERSION:
        errors.append(
            f"expat: loaded {pyexpat.EXPAT_VERSION}; expected {EXPAT_REPORTED_VERSION}"
        )
    if ElementTree.Element is not _elementtree.Element:
        errors.append("expat: ElementTree is not using the matching C extension")
    try:
        if ElementTree.fromstring("<root><item>ok</item></root>")[0].text != "ok":
            errors.append("expat: ElementTree normal XML result is wrong")
        if minidom.parseString("<root/>").documentElement.tagName != "root":
            errors.append("expat: minidom normal XML result is wrong")
        make_parser()
    except (ImportError, OSError, ValueError, pyexpat.ExpatError) as exc:
        errors.append(f"expat: normal XML consumer failed: {exc}")

    try:
        installed = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", "libexpat1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"expat: unable to query Debian libexpat1: {exc}")
    else:
        if installed != EXPAT_DEBIAN_VERSION:
            errors.append(
                f"expat: Debian libexpat1 is {installed}; "
                f"expected {EXPAT_DEBIAN_VERSION}"
            )

    try:
        libraries = _resolved_libraries(ldd(expected_paths["pyexpat"]))
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"expat: unable to inspect pyexpat loader paths: {exc}")
    else:
        resolved = libraries.get("libexpat.so.1")
        if resolved is None:
            errors.append("expat: pyexpat does not link libexpat.so.1")
        elif not re.fullmatch(
            r"/usr/lib/[^/]+-linux-gnu/libexpat\.so\.1\.13\.0",
            realpath(resolved),
        ):
            errors.append(
                f"expat: libexpat.so.1 resolves to {resolved}; "
                "expected Debian's libexpat.so.1.13.0"
            )
    if not list(Path("/usr/lib").glob("*-linux-gnu/libexpatw.so.1.13.0")):
        errors.append("expat: Debian's wide Expat shared library is absent")
    try:
        cache = subprocess.run(
            ["ldconfig", "-p"], check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"expat: unable to inspect loader cache: {exc}")
    else:
        if not re.search(
            r"libpython3\.14\.so\.1\.0 .* => /usr/local/lib/libpython3\.14\.so\.1\.0",
            cache,
        ):
            errors.append("expat: official libpython is absent from loader cache")
    return errors


PYAV_PREFIX = "/opt/ffmpeg9/lib/"
PYAV_LIBRARIES = {
    "libavutil": (61, 1, 102),
    "libavcodec": (63, 1, 102),
    "libavformat": (63, 1, 102),
    "libavdevice": (63, 1, 102),
    "libavfilter": (12, 1, 102),
    "libswscale": (10, 1, 102),
    "libswresample": (7, 1, 102),
}
PYAV_TLS_LIBRARIES = ("libssl", "libcrypto", "libgnutls", "libmbedtls", "libtls")


def validate_pyav_native(
    *,
    version: Callable[[str], str] = importlib.metadata.version,
    package_not_found: type[Exception] = importlib.metadata.PackageNotFoundError,
    purelib: Path | None = None,
    requirements: Path = REQUIREMENTS,
    ldd: Callable[[Path], str] = run_ldd,
    realpath: Callable[[str], str] = os.path.realpath,
    library_versions: dict[str, tuple[int, int, int]] | None = None,
) -> list[str]:
    """Check the installed PyAV bindings and every resolved FFmpeg library.

    ``ldd`` walks the native dependency closure, including FFmpeg siblings.
    The expected path and SONAME prevent an old wheel's private copies or
    Debian's separate FFmpeg libraries from satisfying the version check.
    """
    errors: list[str] = []
    try:
        expected = required_pyav_version(requirements)
    except (OSError, ValueError) as exc:
        return [f"venv: unable to determine required av version: {exc}"]
    try:
        installed = version("av")
    except package_not_found:
        installed = None
    if installed != expected:
        errors.append(f"venv: av is {installed or 'absent'}; expected {expected}")

    root = purelib or Path(sysconfig.get_paths()["purelib"])
    private = sorted(
        str(path)
        for pattern in ("av.libs", "av/.libs", "av*/*.libs")
        for path in root.glob(pattern)
    )
    if private:
        errors.append(
            f"venv: bundled av library directory present: {', '.join(private)}"
        )
    extensions = sorted((root / "av").rglob("*.so"))
    if not extensions:
        errors.append(f"venv: no PyAV extension modules under {root / 'av'}")
        return errors

    found: set[str] = set()
    for extension in extensions:
        try:
            libraries = _resolved_libraries(ldd(extension))
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"venv: unable to inspect {extension.name} with ldd: {exc}")
            continue
        for name, path in libraries.items():
            stem = name.split(".so", 1)[0]
            if stem in PYAV_LIBRARIES:
                expected_name = f"{stem}.so.{PYAV_LIBRARIES[stem][0]}"
                if name != expected_name:
                    errors.append(
                        f"venv: {extension.name} resolves {name}; expected {expected_name}"
                    )
                elif not path.startswith("/") or not realpath(path).startswith(
                    PYAV_PREFIX
                ):
                    errors.append(
                        f"venv: {extension.name} resolves {name} to {path}; "
                        f"expected {PYAV_PREFIX}"
                    )
                else:
                    found.add(stem)
            elif stem.startswith(("libav", "libsw")):
                errors.append(f"venv: unexpected FFmpeg library {name} at {path}")
            if stem.startswith(PYAV_TLS_LIBRARIES):
                errors.append(
                    f"venv: {extension.name} loads TLS library {name} at {path}"
                )
    for stem in PYAV_LIBRARIES.keys() - found:
        errors.append(f"venv: PyAV does not resolve {stem} from {PYAV_PREFIX}")

    if library_versions is None:
        try:
            import av

            library_versions = av.library_versions
            if av.__version__ != expected:
                errors.append(
                    f"venv: imported av is {av.__version__}; expected {expected}"
                )
        except (ImportError, OSError) as exc:
            errors.append(f"venv: unable to import PyAV native bindings: {exc}")
            return errors
    for stem, expected_version in PYAV_LIBRARIES.items():
        if library_versions.get(stem) != expected_version:
            errors.append(
                f"venv: PyAV reports {stem} {library_versions.get(stem)}; "
                f"expected {expected_version}"
            )
    return sorted(set(errors))


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


def psycopg2_record_receipt(
    root: Path | None = None, requirements: Path = REQUIREMENTS
) -> dict[str, object]:
    """Verify installed bytes against RECORD, including the native extension.

    This extends the hashed-entry check in the frozen component verifier without
    changing that recipe input. RECORD and signature files may be unhashed;
    every other installed package file must have a SHA-256 digest and size.
    """
    distribution = importlib.metadata.distribution("psycopg2-binary")
    site = root or Path(distribution.locate_file(""))
    metadata_path = Path(distribution._path)
    if metadata_path.parent.resolve() != site.resolve():
        raise ValueError("distribution metadata is outside the selected site-packages")
    if distribution.version != required_psycopg2_version(requirements):
        raise ValueError("distribution version differs from requirements")
    if any(site.glob("psycopg2*.libs")) or (site / "psycopg2" / ".libs").exists():
        raise ValueError("bundled psycopg2 library directory is installed")
    installed = [
        (item.metadata.get("Name") or "").lower().replace("_", "-")
        for item in importlib.metadata.distributions(path=[str(site)])
    ]
    if installed.count("psycopg2-binary") != 1 or "psycopg2" in installed:
        raise ValueError("ambiguous psycopg2 distribution metadata")
    record_bytes = (metadata_path / "RECORD").read_bytes()
    if not record_bytes:
        raise ValueError("missing or empty RECORD")
    try:
        record = record_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("RECORD is not UTF-8") from exc
    verified: dict[str, str] = {}
    unhashed: list[str] = []
    for row in csv.reader(record.splitlines(), strict=True):
        if len(row) != 3:
            raise ValueError("RECORD row must have three fields")
        filename, digest, size = row
        path = (site / filename).resolve(strict=True)
        if (
            not path.is_relative_to(site.resolve())
            or filename in verified
            or filename in unhashed
        ):
            raise ValueError("unsafe or duplicate RECORD path")
        if not digest or not size:
            permissible_metadata = (
                path.name in {"RECORD", "RECORD.jws", "RECORD.p7s"}
                and path.parent == metadata_path
            )
            permissible_bytecode = path.suffix == ".pyc" and "__pycache__" in path.parts
            if digest or size or not (permissible_metadata or permissible_bytecode):
                raise ValueError(f"unhashed installed file: {filename}")
            unhashed.append(filename)
            continue
        if not digest.startswith("sha256=") or not size.isdecimal():
            raise ValueError(f"invalid hash or size: {filename}")
        data = path.read_bytes()
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        if digest[7:] != actual or int(size) != len(data):
            raise ValueError(f"installed bytes differ from RECORD: {filename}")
        verified[filename] = hashlib.sha256(data).hexdigest()
    package_files = {
        str(path.relative_to(site))
        for path in (site / "psycopg2").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    missing = package_files - verified.keys()
    if missing:
        raise ValueError(f"package files lack hashed RECORD entries: {sorted(missing)}")
    extensions = sorted((site / "psycopg2").glob("_psycopg*.so"))
    if len(extensions) != 1 or str(extensions[0].relative_to(site)) not in verified:
        raise ValueError("expected one hashed native extension")
    metadata_relative = str(metadata_path.relative_to(site))
    if (
        f"{metadata_relative}/METADATA" not in verified
        or f"{metadata_relative}/RECORD" not in unhashed
    ):
        raise ValueError("required package metadata is not hashed")
    return {
        "package": "psycopg2-binary",
        "version": distribution.version,
        "record_hashes_verified": len(verified),
        "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "extension_path": str(extensions[0].resolve(strict=True)),
        "extension_sha256": verified[str(extensions[0].relative_to(site))],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=[*sorted(EXPECTED), "expat", "msgpack-pin"])
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
    errors = [] if args.scope == "expat" else validate(args.scope)
    errors.extend(validate_expat_native())
    if args.scope == "venv":
        errors.extend(validate_pillow())
        errors.extend(validate_psycopg2_native())
        errors.extend(validate_pyav_native())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Final Python package state valid: {args.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
