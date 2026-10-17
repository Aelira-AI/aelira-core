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
    # The developer image keeps pip for its supported interactive workflow.
    "quickstart": {
        "setuptools": "84.0.0",
        "pip": "26.2.1",
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


def required_opencv_version(requirements: Path = REQUIREMENTS) -> str:
    return required_pinned_version("opencv-python", requirements)


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
    if scope in {"venv", "quickstart"}:
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
OPENCV_IMAGE_LIBRARIES = (
    "libavif",
    "libtiff",
    "libjpeg",
    "libpng",
    "libwebp",
    "libopenjp2",
    "libjasper",
    "libjxl",
    "libspng",
    "libgdcm",
    "libgdal",
)
OPENCV_OPTIONAL_IMAGE_BACKENDS = (
    "AVIF",
    "JPEG",
    "PNG",
    "TIFF",
    "WEBP",
    "JPEG 2000",
    "JPEG XL",
    "OpenEXR",
    "GDCM",
    "GDAL",
    "GIF",
    "HDR",
    "SUNRASTER",
    "PXM",
    "PFM",
)


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


def validate_opencv_native(
    *,
    version: Callable[[str], str] = importlib.metadata.version,
    package_not_found: type[Exception] = importlib.metadata.PackageNotFoundError,
    purelib: Path | None = None,
    requirements: Path = REQUIREMENTS,
    ldd: Callable[[Path], str] = run_ldd,
    realpath: Callable[[str], str] = os.path.realpath,
    build_information: str | None = None,
) -> list[str]:
    """Check source OpenCV identity, FFmpeg linkage, and absent private codecs."""
    errors: list[str] = []
    try:
        expected = required_opencv_version(requirements)
    except (OSError, ValueError) as exc:
        return [f"venv: unable to determine required OpenCV version: {exc}"]
    try:
        installed = version("opencv-python")
    except package_not_found:
        installed = None
    if installed != expected:
        errors.append(
            f"venv: opencv-python is {installed or 'absent'}; expected {expected}"
        )

    root = purelib or Path(sysconfig.get_paths()["purelib"])
    private = sorted(str(path) for path in root.glob("opencv_python*.libs"))
    private.extend(str(path) for path in (root / "cv2").glob("*.libs"))
    if private:
        errors.append(
            f"venv: bundled OpenCV library directory present: {', '.join(private)}"
        )
    extensions = sorted((root / "cv2").glob("cv2*.so"))
    if len(extensions) != 1:
        errors.append(f"venv: expected one cv2 extension; found {len(extensions)}")
        return errors
    try:
        libraries = _resolved_libraries(ldd(extensions[0]))
    except (OSError, subprocess.CalledProcessError) as exc:
        return errors + [f"venv: unable to inspect cv2 with ldd: {exc}"]

    for stem in ("libavcodec", "libavformat", "libavutil", "libswscale"):
        expected_name = f"{stem}.so.{PYAV_LIBRARIES[stem][0]}"
        path = libraries.get(expected_name)
        if path is None:
            errors.append(f"venv: cv2 does not resolve {expected_name}")
        elif not realpath(path).startswith(PYAV_PREFIX):
            errors.append(
                f"venv: cv2 resolves {expected_name} to {path}; expected {PYAV_PREFIX}"
            )
    for name, path in libraries.items():
        stem = name.split(".so", 1)[0]
        if (
            stem.startswith(("libav", "libsw"))
            and stem not in PYAV_LIBRARIES
            and not stem.startswith(OPENCV_IMAGE_LIBRARIES)
        ):
            errors.append(f"venv: unexpected OpenCV FFmpeg library {name} at {path}")
        if stem in PYAV_LIBRARIES:
            if name != f"{stem}.so.{PYAV_LIBRARIES[stem][0]}":
                errors.append(
                    f"venv: cv2 resolves unexpected FFmpeg SONAME {name} at {path}"
                )
            if not path.startswith("/") or not realpath(path).startswith(PYAV_PREFIX):
                errors.append(
                    f"venv: cv2 resolves {name} to {path}; expected {PYAV_PREFIX}"
                )
        if stem.startswith(OPENCV_IMAGE_LIBRARIES):
            errors.append(f"venv: cv2 loads disabled image codec {name} at {path}")
        if stem.startswith(PYAV_TLS_LIBRARIES):
            errors.append(f"venv: cv2 loads TLS library {name} at {path}")
    dav1d = libraries.get("libdav1d.so.7")
    if dav1d is None or not realpath(dav1d).startswith("/usr/lib/"):
        errors.append(f"venv: cv2 lacks Debian libdav1d.so.7: {dav1d}")

    if build_information is None:
        try:
            import cv2

            build_information = cv2.getBuildInformation()
            if cv2.__version__ != expected.rsplit(".", 1)[0]:
                errors.append(
                    f"venv: imported cv2 is {cv2.__version__}; expected {expected}"
                )
        except (ImportError, OSError) as exc:
            return errors + [f"venv: unable to import cv2: {exc}"]
    if not re.search(r"^\s*FFMPEG:\s+YES\s*$", build_information, re.MULTILINE):
        errors.append("venv: cv2 FFmpeg backend is disabled")
    for backend in OPENCV_OPTIONAL_IMAGE_BACKENDS:
        match = re.search(
            rf"^\s*{re.escape(backend)}:\s*(.+)$",
            build_information,
            re.MULTILINE,
        )
        if match and match.group(1).strip() != "NO":
            errors.append(f"venv: cv2 enables optional {backend} image backend")
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
    if args.scope in {"venv", "quickstart"}:
        errors.extend(validate_pillow())
        errors.extend(validate_psycopg2_native())
        errors.extend(validate_pyav_native())
        errors.extend(validate_opencv_native())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Final Python package state valid: {args.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
