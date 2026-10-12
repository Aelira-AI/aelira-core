"""Behavior and binding checks for an installed Pillow 12.3.0 candidate.

Run inside a disposable, network-disabled, read-only container as UID 1000.
Uses only synthetic image bytes; no customer inputs or external services.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import hashlib
import importlib.metadata
import io
import json
import os
import platform
from pathlib import Path
import re
import stat
import subprocess
import sys
import traceback
import zipfile

# Direct NEEDED entries independently checked against both accepted libtiff6
# DEBs. The ARM64 package also names its architecture's runtime loader.
TIFF_DEPENDENCIES = (
    "libwebp.so.7",
    "libzstd.so.1",
    "liblzma.so.5",
    "libLerc.so.4",
    "libjbig.so.0",
    "libjpeg.so.8",
    "libdeflate.so.0",
    "libz.so.1",
    "libm.so.6",
    "libc.so.6",
)
TIFF_TARGETS = {
    "arm64": (
        "aarch64-linux-gnu",
        592088,
        "63e0add472fc1c99cbcfadcf58df8dc11d8da6516f224ecb57ff12e06029f384",
    ),
    "amd64": (
        "x86_64-linux-gnu",
        612592,
        "f45a58ee6e6d76dd51da58b0d721b3cf4d49d3a434f224340a7f30646b21d340",
    ),
}
TIFF_LOADER = {"arm64": ("ld-linux-aarch64.so.1",), "amd64": ()}
APPROVED_SONAMES = frozenset(
    ("libtiff.so.6",)
    + TIFF_DEPENDENCIES
    + TIFF_LOADER["arm64"]
    + (
        "libopenjp2.so.7",
        "libxcb.so.1",
        "libavif.so.16",
        "libwebpmux.so.3",
        "libwebpdemux.so.2",
        "liblcms2.so.2",
        "libfreetype.so.6",
        "libraqm.so.0",
    )
)


def loader_category(error: Exception) -> tuple[str, str | None]:
    """Classify a loader error without returning its text, path or symbol."""
    message = str(error)
    if isinstance(error, PermissionError) or "Permission denied" in message:
        category = "permission"
    elif isinstance(error, FileNotFoundError) or "No such file or directory" in message:
        category = "missing_file"
    elif any(
        phrase in message
        for phrase in ("invalid ELF header", "wrong ELF class", "file too short")
    ):
        category = "invalid_elf"
    elif "undefined symbol:" in message:
        category = "undefined_symbol"
    elif "version `" in message and "not found" in message:
        category = "version_mismatch"
    elif any(
        phrase in message
        for phrase in ("failed to map segment", "cannot allocate memory")
    ):
        category = "mapping_failure"
    else:
        category = "other"
    found = [
        name
        for name in APPROVED_SONAMES
        if re.search(
            r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_.-])",
            message,
        )
    ]
    return category, found[0] if len(found) == 1 else None


def tiff_file_identity(directory: Path, arch: str) -> dict[str, object]:
    """Inspect fixed package paths only; never follow an unexpected symlink."""
    _, accepted_size, accepted_sha = TIFF_TARGETS[arch]
    try:
        directory_canonical = directory.resolve(strict=True) == directory
    except (OSError, RuntimeError):
        directory_canonical = False
    if not directory_canonical:
        return {
            "directory_canonical": False,
            "soname_entry": "unavailable",
            "target_entry": "unavailable",
            "size": None,
            "mode": None,
            "owner_root": None,
            "sha256": None,
            "matches_accepted": False,
        }
    link = directory / "libtiff.so.6"
    target = directory / "libtiff.so.6.3.0"
    link_state = "unavailable"
    try:
        link_info = link.lstat()
        if stat.S_ISLNK(link_info.st_mode):
            link_state = (
                "expected" if os.readlink(link) == target.name else "unexpected_target"
            )
        else:
            link_state = "unexpected_type"
    except FileNotFoundError:
        link_state = "missing"
    except OSError:
        pass
    target_state = "unavailable"
    size = mode = owner_root = sha = None
    try:
        info = target.lstat()
        if stat.S_ISREG(info.st_mode):
            target_state = "regular"
            size = min(info.st_size, 2_000_001)
            mode = stat.S_IMODE(info.st_mode)
            owner_root = info.st_uid == 0
            if 0 <= size <= 2_000_000:
                try:
                    with target.open("rb") as stream:
                        content = stream.read(2_000_001)
                    if len(content) == size:
                        sha = hashlib.sha256(content).hexdigest()
                except OSError:
                    pass
        else:
            target_state = "unexpected_type"
    except FileNotFoundError:
        target_state = "missing"
    except OSError:
        pass
    return {
        "directory_canonical": directory_canonical,
        "soname_entry": link_state,
        "target_entry": target_state,
        "size": size,
        "mode": mode,
        "owner_root": owner_root,
        "sha256": sha,
        "matches_accepted": (
            directory_canonical
            and link_state == "expected"
            and target_state == "regular"
            and size == accepted_size
            and mode == 0o644
            and owner_root is True
            and sha == accepted_sha
        ),
    }


def tiff_package_identity(arch: str) -> dict[str, str]:
    """Return fixed states only; command output never enters the receipt."""
    package = "unavailable"
    files = "unavailable"
    try:
        query = subprocess.run(
            [
                "dpkg-query",
                "-W",
                "-f=${Status}\t${Version}\t${Architecture}",
                "libtiff6",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if query.returncode == 1 and not query.stderr:
            package = "missing"
        elif query.returncode == 0 and not query.stderr:
            expected = f"install ok installed\t4.7.2-0aelira1\t{arch}".encode()
            package = "expected" if query.stdout == expected else "other"
        verification = subprocess.run(
            ["dpkg", "-V", "libtiff6"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if (
            verification.returncode == 0
            and not verification.stdout
            and not verification.stderr
        ):
            files = "clean"
        elif verification.returncode == 1 and not verification.stderr:
            files = "mismatch"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"package": package, "files": files}


def import_diagnostic(image_id: str) -> None:
    """Emit only fixed observations; never copy an import exception or path."""
    import importlib

    names = ("Image", "ImageCms", "ImageFile", "ImageFont", "features")
    native = ("_imaging", "_imagingcms", "_imagingft")
    libraries = (
        "libtiff.so.6",
        "libjpeg.so.8",
        "libopenjp2.so.7",
        "libz.so.1",
        "libxcb.so.1",
        "libavif.so.16",
        "libwebp.so.7",
        "libwebpmux.so.3",
        "libwebpdemux.so.2",
        "liblcms2.so.2",
        "libfreetype.so.6",
        "libraqm.so.0",
    )
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if arch is None:
        raise SystemExit(2)

    def category(error: Exception) -> str:
        if isinstance(error, ModuleNotFoundError):
            return "module_missing"
        if isinstance(error, ImportError):
            return "import_error"
        if isinstance(error, PermissionError):
            return "permission_error"
        if isinstance(error, OSError):
            return "os_error"
        return "other_exception"

    imports = []
    first_failed = None
    for name in names:
        if first_failed is not None:
            imports.append({"name": name, "state": "not_run", "exception": "none"})
            continue
        try:
            importlib.import_module(f"PIL.{name}")
        except Exception as error:
            imports.append(
                {"name": name, "state": "failed", "exception": category(error)}
            )
            first_failed = name
        else:
            imports.append({"name": name, "state": "pass", "exception": "none"})

    modules = []
    for name in native:
        try:
            importlib.import_module(f"PIL.{name}")
        except Exception as error:
            modules.append(
                {"name": name, "state": "failed", "exception": category(error)}
            )
        else:
            modules.append({"name": name, "state": "pass", "exception": "none"})

    links = []
    for name in libraries:
        try:
            ctypes.CDLL(name)
        except OSError as error:
            state = "load_failed"
            loader, related = loader_category(error)
        except Exception:
            state = "unavailable"
            loader, related = "other", None
        else:
            state = "loaded"
            loader, related = "none", None
        links.append(
            {"name": name, "state": state, "loader": loader, "related_soname": related}
        )

    dependencies = []
    for name in TIFF_DEPENDENCIES + TIFF_LOADER[arch]:
        try:
            ctypes.CDLL(name)
        except OSError as error:
            state = "load_failed"
            loader, related = loader_category(error)
        except Exception:
            state = "unavailable"
            loader, related = "other", None
        else:
            state = "loaded"
            loader, related = "none", None
        dependencies.append(
            {"name": name, "state": state, "loader": loader, "related_soname": related}
        )
    triplet = TIFF_TARGETS[arch][0]
    file_identity = tiff_file_identity(Path("/usr/lib") / triplet, arch)
    package_identity = tiff_package_identity(arch)

    receipt = {
        "schema": "pillow-import-diagnostic-v2",
        "image_id": image_id,
        "architecture": arch,
        "probe_pid": os.getpid(),
        "imports": imports,
        "native_modules": modules,
        "libraries": links,
        "tiff_dependencies": dependencies,
        "tiff_dependencies_loaded": sum(
            item["state"] == "loaded" for item in dependencies
        ),
        "tiff_file": file_identity,
        "tiff_package": package_identity,
        "first_failed": first_failed,
        "import_passed": sum(item["state"] == "pass" for item in imports),
        "native_passed": sum(item["state"] == "pass" for item in modules),
        "libraries_loaded": sum(item["state"] == "loaded" for item in links),
    }
    print(json.dumps(receipt, allow_nan=False, separators=(",", ":")))


if len(sys.argv) == 3 and sys.argv[1] == "--import-diagnostic":
    import_diagnostic(sys.argv[2])
    raise SystemExit(0)

# These are check locations, not exception or root-cause classifications. The
# enclosing CI reports them only for an installed_verifier failure.
CHECK_EXIT_CODES = {
    "import_setup": 40,
    "record": 41,
    "wheel_payloads": 42,
    "feature_jpg": 43,
    "feature_zlib": 44,
    "feature_libtiff": 45,
    "feature_webp": 46,
    "feature_jpg_2000": 47,
    "feature_freetype2": 48,
    "feature_littlecms2": 49,
    "feature_raqm": 50,
    "feature_avif": 51,
    "feature_xcb": 52,
    "disabled_libimagequant": 53,
    "disabled_tkinter": 54,
    "truncated_images": 55,
    "roundtrip_png": 56,
    "roundtrip_jpeg": 57,
    "roundtrip_webp": 58,
    "roundtrip_jpeg2000": 59,
    "roundtrip_avif": 60,
    "roundtrip_tiff": 61,
    "tiff_pages": 62,
    "tiff_library": 63,
    "font": 64,
    "colour": 65,
    "malformed_png": 66,
    "malformed_jpeg": 67,
    "malformed_tiff": 68,
    "malformed_random": 69,
    "aelira_pdf": 70,
    "aelira_suitability": 71,
    "private_directory": 72,
}

try:
    from PIL import Image, ImageCms, ImageFile, ImageFont, features
except Exception:
    traceback.print_exc()  # Private installed-consumer log only.
    raise SystemExit(CHECK_EXIT_CODES["import_setup"]) from None


@contextmanager
def diagnosed(name: str):
    """Encode the executing check; leave signals and explicit exits unmapped."""
    try:
        yield
    except Exception:
        traceback.print_exc()  # Private installed-consumer log only.
        raise SystemExit(CHECK_EXIT_CODES[name]) from None


def check_record() -> int:
    distribution = importlib.metadata.distribution("pillow")
    assert distribution.version == "12.3.0"
    count = 0
    for entry in distribution.files or ():
        if entry.hash is None:
            continue
        assert entry.hash.mode == "sha256", entry
        content = Path(distribution.locate_file(entry)).read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        assert digest.decode("ascii") == entry.hash.value, entry
        count += 1
    assert count == 122, count
    print(f"RECORD verified: {count} SHA-256 entries")
    return count


def check_wheel_payloads() -> tuple[int, str]:
    wheels = list(Path("/wheel").glob("pillow-12.3.0-*.whl"))
    assert len(wheels) == 1, wheels
    wheel = wheels[0]
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    distribution = importlib.metadata.distribution("pillow")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        record = "pillow-12.3.0.dist-info/RECORD"
        assert record in names
        payloads = [name for name in names if name != record]
        assert len(payloads) == 119, len(payloads)
        for name in payloads:
            assert not name.startswith("/") and ".." not in Path(name).parts, name
            assert (
                archive.read(name) == Path(distribution.locate_file(name)).read_bytes()
            ), name
    print(f"installed_wheel_payloads=pass entries=119 wheel_sha256={wheel_sha}")
    return len(payloads), wheel_sha


def check_features() -> tuple[dict[str, bool], dict[str, bool]]:
    required = (
        "jpg",
        "zlib",
        "libtiff",
        "webp",
        "jpg_2000",
        "freetype2",
        "littlecms2",
        "raqm",
        "avif",
        "xcb",
    )
    for feature in required:
        with diagnosed(f"feature_{feature}"):
            assert features.check(feature), feature
            print(f"feature {feature}: {features.version(feature)}")
    for feature in ("libimagequant", "tkinter"):
        with diagnosed(f"disabled_{feature}"):
            assert not features.check(feature), feature
            print(f"baseline-disabled feature {feature}: absent")
    with diagnosed("truncated_images"):
        assert not ImageFile.LOAD_TRUNCATED_IMAGES
    return (
        {feature: True for feature in required},
        {feature: False for feature in ("libimagequant", "tkinter")},
    )


def round_trip(fmt: str, **options: object) -> bytes:
    source = Image.new("RGB", (24, 16), (80, 120, 190))
    output = io.BytesIO()
    source.save(output, format=fmt, **options)
    payload = output.getvalue()
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.format == fmt, (fmt, decoded.format)
        decoded.load()
        assert decoded.size == source.size
    print(f"roundtrip {fmt}: {len(payload)} bytes")
    return payload


def check_tiff() -> str:
    with diagnosed("tiff_pages"):
        first = Image.new("RGB", (24, 16), (255, 0, 0))
        second = Image.new("RGB", (24, 16), (0, 255, 0))
        output = io.BytesIO()
        first.save(
            output,
            format="TIFF",
            save_all=True,
            append_images=[second],
            compression="tiff_deflate",
        )
        with Image.open(io.BytesIO(output.getvalue())) as decoded:
            assert decoded.n_frames == 2
            decoded.seek(0)
            decoded.load()
            assert decoded.getpixel((0, 0)) == (255, 0, 0)
            decoded.seek(1)
            decoded.load()
            assert decoded.getpixel((0, 0)) == (0, 255, 0)
        print("multiframe TIFF Deflate: 2 pages decoded")

    # The actual loaded library must be the exact installed candidate.
    with diagnosed("tiff_library"):
        lib = ctypes.CDLL("libtiff.so.6")
        lib.TIFFGetVersion.restype = ctypes.c_char_p
        version = lib.TIFFGetVersion().decode("ascii")
        assert "Version 4.7.2" in version, version
        arch = platform.machine()
        assert arch in ("aarch64", "x86_64"), arch
        library = Path(
            {
                "aarch64": "/usr/lib/aarch64-linux-gnu/libtiff.so.6.3.0",
                "x86_64": "/usr/lib/x86_64-linux-gnu/libtiff.so.6.3.0",
            }[arch]
        )
        assert library.exists()
        expected_hash = {
            "aarch64": "63e0add472fc1c99cbcfadcf58df8dc11d8da6516f224ecb57ff12e06029f384",
            "x86_64": "f45a58ee6e6d76dd51da58b0d721b3cf4d49d3a434f224340a7f30646b21d340",
        }[arch]
        assert hashlib.sha256(library.read_bytes()).hexdigest() == expected_hash
        mappings = Path("/proc/self/maps").read_text()
        assert str(library) in mappings
        print(f"loaded TIFF: {version.splitlines()[0]}; installed sha256 matched")
    return expected_hash


def check_font_and_colour() -> bool:
    with diagnosed("font"):
        font = ImageFont.truetype(
            "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf",
            18,
            layout_engine=ImageFont.Layout.RAQM,
        )
        assert font.getlength("AV") > 0
    with diagnosed("colour"):
        rgb = ImageCms.createProfile("sRGB")
        image = Image.new("RGB", (4, 4), (20, 40, 60))
        converted = ImageCms.profileToProfile(image, rgb, rgb)
        assert converted.getpixel((0, 0)) == (20, 40, 60)
    print("FreeType/Raqm text and LittleCMS transform: ok")
    return True


def check_rejection(png: bytes, jpeg: bytes, tiff: bytes) -> dict[str, bool]:
    for name, payload in (
        ("PNG", png[:20]),
        ("JPEG", jpeg[:32]),
        ("TIFF", tiff[:24]),
        ("random", b"not-an-image"),
    ):
        with diagnosed(f"malformed_{name.lower()}"):
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    image.load()
            except (OSError, SyntaxError, ValueError):
                print(f"refused malformed {name}")
            else:
                raise AssertionError(f"malformed {name} accepted")

    # Exercise the application's structural and full-decode validation path.
    with diagnosed("aelira_pdf"):
        from src.education.remediation.pdf_remediator import _is_verified_image

        assert _is_verified_image(png, "png")
        assert not _is_verified_image(png[:20], "png")
        assert not _is_verified_image(jpeg[:32], "jpeg")
        print("Aelira PDF image verifier: accepted valid and refused truncation")

    with diagnosed("aelira_suitability"):
        from src.education.handwritten_math_suitability import (
            SuitabilityInputRejected,
            _decode_image,
        )

        decoded, image_format = _decode_image(png)
        assert decoded.size == (24, 16) and image_format == "PNG"
        try:
            _decode_image(png[:20])
        except SuitabilityInputRejected:
            pass
        else:
            raise AssertionError("Aelira suitability decoder accepted truncated PNG")
        print("Aelira handwritten math decoder: accepted valid and refused truncation")
    return {name: True for name in ("PNG", "JPEG", "TIFF", "random")}


def main() -> None:
    with diagnosed("record"):
        record_entries = check_record()
    with diagnosed("wheel_payloads"):
        wheel_payloads, wheel_sha = check_wheel_payloads()
    enabled, disabled = check_features()
    with diagnosed("roundtrip_png"):
        png = round_trip("PNG")
    with diagnosed("roundtrip_jpeg"):
        jpeg = round_trip("JPEG")
    with diagnosed("roundtrip_webp"):
        webp = round_trip("WEBP")
    with diagnosed("roundtrip_jpeg2000"):
        jpeg2000 = round_trip("JPEG2000")
    with diagnosed("roundtrip_avif"):
        avif = round_trip("AVIF")
    with diagnosed("roundtrip_tiff"):
        tiff = round_trip("TIFF")
    tiff_sha = check_tiff()
    font_colour = check_font_and_colour()
    refused = check_rejection(png, jpeg, tiff)
    private = Path("/opt/venv/lib/python3.14/site-packages/pillow.libs")
    with diagnosed("private_directory"):
        assert not private.exists(), private
        print("Pillow wheel-private library directory absent")
    result = {
        "schema": "pillow-installed-semantics-v1",
        "record_entries": record_entries,
        "wheel_payload_entries": wheel_payloads,
        "wheel_sha256": wheel_sha,
        "features_enabled": enabled,
        "features_disabled": disabled,
        "roundtrip_bytes": {
            "PNG": len(png),
            "JPEG": len(jpeg),
            "WEBP": len(webp),
            "JPEG2000": len(jpeg2000),
            "AVIF": len(avif),
            "TIFF": len(tiff),
        },
        "tiff_pages": 2,
        "tiff_pixels": [[255, 0, 0], [0, 255, 0]],
        "loaded_tiff_sha256": tiff_sha,
        "font_colour": font_colour,
        "malformed_refused": refused,
        "aelira_pdf": True,
        "aelira_suitability": True,
        "private_directory_absent": True,
    }
    print(
        "pillow_installed_receipt="
        + json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
