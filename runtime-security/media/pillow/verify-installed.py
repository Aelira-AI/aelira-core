"""Behavior and binding checks for an installed Pillow 12.3.0 candidate.

Run inside a disposable, network-disabled, read-only container as UID 1000.
Uses only synthetic image bytes; no customer inputs or external services.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.metadata
import io
import json
import platform
from pathlib import Path
import zipfile

from PIL import Image, ImageCms, ImageFile, ImageFont, features


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
        assert features.check(feature), feature
        print(f"feature {feature}: {features.version(feature)}")
    for feature in ("libimagequant", "tkinter"):
        assert not features.check(feature), feature
        print(f"baseline-disabled feature {feature}: absent")
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
    font = ImageFont.truetype(
        "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf",
        18,
        layout_engine=ImageFont.Layout.RAQM,
    )
    assert font.getlength("AV") > 0
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
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
        except (OSError, SyntaxError, ValueError):
            print(f"refused malformed {name}")
        else:
            raise AssertionError(f"malformed {name} accepted")

    # Exercise the application's structural and full-decode validation path.
    from src.education.remediation.pdf_remediator import _is_verified_image

    assert _is_verified_image(png, "png")
    assert not _is_verified_image(png[:20], "png")
    assert not _is_verified_image(jpeg[:32], "jpeg")
    print("Aelira PDF image verifier: accepted valid and refused truncation")

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
    record_entries = check_record()
    wheel_payloads, wheel_sha = check_wheel_payloads()
    enabled, disabled = check_features()
    png = round_trip("PNG")
    jpeg = round_trip("JPEG")
    webp = round_trip("WEBP")
    jpeg2000 = round_trip("JPEG2000")
    avif = round_trip("AVIF")
    tiff = round_trip("TIFF")
    tiff_sha = check_tiff()
    font_colour = check_font_and_colour()
    refused = check_rejection(png, jpeg, tiff)
    private = Path("/opt/venv/lib/python3.14/site-packages/pillow.libs")
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
