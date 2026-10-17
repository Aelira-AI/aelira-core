#!/usr/bin/env python3
"""Exercise the image libraries and OCR of the final image with real decoding.

Pillow is built from source against libtiff 4.7.2 and the Debian image
libraries, and ``pi_heif`` is no longer installed. This proves, inside the
built image, that every codec the published wheel provided still encodes and
decodes, that malformed input is refused, and that ocrmypdf still recognises
text in an image-only PDF without ``pi_heif``.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from PIL import (
    Image,
    ImageChops,
    ImageCms,
    ImageDraw,
    ImageFont,
    ImageStat,
    UnidentifiedImageError,
    features,
)

EXPECTED_LIBTIFF = "4.7.2"
REQUIRED_FEATURES = (
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
TIFF_COMPRESSIONS = (
    "raw",
    "tiff_lzw",
    "tiff_adobe_deflate",
    "tiff_jpeg",
    "zstd",
    "lzma",
)


def _sample() -> Image.Image:
    """Return a deterministic image with enough structure to expose codec damage."""
    image = Image.new("RGB", (96, 64))
    image.putdata(
        [
            ((x * 5) % 256, (y * 7) % 256, ((x + y) * 3) % 256)
            for y in range(64)
            for x in range(96)
        ]
    )
    return image


def _roundtrip(image: Image.Image, fmt: str, tolerance: int, **options: object) -> None:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        decoded.load()
        assert decoded.size == image.size, (fmt, options, decoded.size)
        difference = max(
            ImageStat.Stat(
                ImageChops.difference(image.convert("RGB"), decoded.convert("RGB"))
            ).mean
        )
    assert difference <= tolerance, (fmt, options, difference)


def check_features() -> str:
    for name in REQUIRED_FEATURES:
        assert features.check(name), f"Pillow feature {name} is unavailable"
    libtiff = features.version("libtiff")
    assert libtiff == EXPECTED_LIBTIFF, f"libtiff is {libtiff}"
    return libtiff


def check_codecs() -> None:
    image = _sample()
    _roundtrip(image, "PNG", 0)
    _roundtrip(image, "JPEG", 20, quality=95)
    _roundtrip(image, "WEBP", 0, lossless=True)
    _roundtrip(image, "JPEG2000", 0, irreversible=False)
    _roundtrip(image, "AVIF", 25, quality=95)
    for compression in TIFF_COMPRESSIONS:
        tolerance = 20 if compression == "tiff_jpeg" else 0
        _roundtrip(image, "TIFF", tolerance, compression=compression)


def check_multipage_tiff() -> None:
    pages = [_sample(), _sample().transpose(Image.Transpose.ROTATE_180)]
    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        "TIFF",
        save_all=True,
        append_images=pages[1:],
        compression="tiff_adobe_deflate",
    )
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        assert decoded.n_frames == 2
        for index, expected in enumerate(pages):
            decoded.seek(index)
            assert (
                ImageChops.difference(decoded.convert("RGB"), expected).getbbox()
                is None
            )


def check_malformed_input_is_refused() -> None:
    valid = io.BytesIO()
    _sample().save(valid, "TIFF", compression="tiff_lzw")
    data = valid.getvalue()
    for malformed in (data[: len(data) // 2], b"II*\x00" + b"\xff" * 64, b"\x00" * 64):
        try:
            with Image.open(io.BytesIO(malformed)) as decoded:
                decoded.load()
        except (OSError, SyntaxError, UnidentifiedImageError, ValueError):
            continue
        raise AssertionError("malformed TIFF input was decoded without an error")


def check_text_and_colour() -> Image.Image:
    font = ImageFont.load_default(size=72)
    assert isinstance(font, ImageFont.FreeTypeFont), "FreeType font unavailable"
    page = Image.new("L", (1700, 160), 255)
    ImageDraw.Draw(page).text(
        (20, 30), "Accessible documents for everyone", font=font, fill=0
    )
    assert page.getextrema() == (0, 255), "text was not rendered"

    srgb = ImageCms.createProfile("sRGB")
    lab = ImageCms.createProfile("LAB")
    transform = ImageCms.buildTransform(srgb, lab, "RGB", "LAB")
    converted = ImageCms.applyTransform(_sample(), transform)
    assert converted.mode == "LAB" and converted.size == (96, 64)
    return page


def check_ocr(page: Image.Image) -> str:
    import ocrmypdf
    from pdfminer.high_level import extract_text

    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "scan.pdf"
        target = Path(scratch) / "ocr.pdf"
        page.save(source, "PDF", resolution=150.0)
        ocrmypdf.ocr(
            source,
            target,
            language="eng",
            output_type="pdf",
            jobs=1,
            progress_bar=False,
        )
        text = extract_text(str(target)).lower()
    assert "accessible" in text and "documents" in text and "everyone" in text, text
    return text.strip()


def main() -> None:
    libtiff = check_features()
    check_codecs()
    check_multipage_tiff()
    check_malformed_input_is_refused()
    page = check_text_and_colour()
    text = check_ocr(page)
    try:
        import pi_heif  # noqa: F401
    except ImportError:
        pass
    else:
        raise AssertionError("pi_heif must not be installed")
    print(f"image libraries verified: libtiff {libtiff}; OCR text: {text!r}")


if __name__ == "__main__":
    main()
