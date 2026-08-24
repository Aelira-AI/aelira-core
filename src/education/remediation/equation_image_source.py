"""Bounded source extraction for localized equation images."""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Mapping

from PIL import Image, UnidentifiedImageError

from src.education.pdf_checks.image_checker import _displayed_image_occurrences


class ImageSourceRejected(ValueError):
    """The candidate image could not be validated safely."""


@dataclass(frozen=True)
class ImageSourceLimits:
    max_source_bytes: int = 10 * 1024 * 1024
    max_width: int = 10_000
    max_height: int = 10_000
    max_pixels: int = 25_000_000


@dataclass(frozen=True)
class EquationImageIdentity:
    page_number: int
    image_xref: int
    image_index: int
    occurrence_ordinal: int
    bbox: tuple[float, float, float, float]
    occurrence_id: str


@dataclass(frozen=True)
class ValidatedEquationImage:
    jpeg_bytes: bytes
    mime_type: str
    source_sha256: str
    normalized_sha256: str
    width: int
    height: int
    identity: EquationImageIdentity


def _complete_png(data: bytes) -> bool:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return False
    offset = len(signature)
    while offset < len(data):
        if len(data) - offset < 12:
            return False
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            return False
        if kind == b"IEND":
            return length == 0 and end == len(data)
        offset = end
    return False


def _complete_jpeg(data: bytes) -> bool:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return False
    offset = 2
    entropy = False
    while offset < len(data):
        marker_from_entropy = entropy
        if entropy:
            while offset < len(data) and data[offset] != 0xFF:
                offset += 1
            if offset == len(data):
                return False
        elif data[offset] != 0xFF:
            return False
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset == len(data):
            return False
        marker = data[offset]
        offset += 1
        if marker_from_entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        if marker in {0, 0xD8}:
            return False
        if marker == 0xD9:
            return offset == len(data)
        if 0xD0 <= marker <= 0xD7:
            return False
        if marker == 0x01:
            entropy = marker_from_entropy
            continue
        if offset + 2 > len(data):
            return False
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return False
        offset += length
        entropy = marker == 0xDA
    return False


def _complete_webp(data: bytes) -> bool:
    if (
        len(data) < 20
        or data[:4] != b"RIFF"
        or data[8:12] != b"WEBP"
        or int.from_bytes(data[4:8], "little") + 8 != len(data)
    ):
        return False
    allowed = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ICCP", b"EXIF", b"XMP "}
    seen: set[bytes] = set()
    image_chunks = 0
    image_seen = False
    offset = 12
    while offset < len(data):
        if len(data) - offset < 8:
            return False
        kind = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if image_seen or kind not in allowed or kind in seen:
            return False
        seen.add(kind)
        if kind in {b"VP8 ", b"VP8L"}:
            image_chunks += 1
            image_seen = True
        end = offset + 8 + length
        padded_end = end + (length & 1)
        if end > len(data) or padded_end > len(data):
            return False
        offset = padded_end
    return offset == len(data) and image_chunks == 1


class EquationImageSource:
    """Re-resolve, validate, and normalize one exact embedded image occurrence."""

    def __init__(self, limits: ImageSourceLimits | None = None) -> None:
        self.limits = limits or ImageSourceLimits()

    def extract(
        self, document: Any, identity: Mapping[str, Any]
    ) -> ValidatedEquationImage:
        expected = self._identity(identity)
        try:
            page = document[expected.page_number - 1]
        except Exception as exc:
            raise ImageSourceRejected("occurrence_identity_mismatch") from exc
        current = _displayed_image_occurrences(page, expected.page_number)
        matches = [item for item in current if item["occurrence_id"] == expected.occurrence_id]
        if len(matches) != 1 or self._identity(matches[0]) != expected:
            raise ImageSourceRejected("occurrence_identity_mismatch")

        try:
            extracted = document.extract_image(expected.image_xref)
            source = extracted["image"]
        except Exception as exc:
            raise ImageSourceRejected("image_extraction_failed") from exc
        if not isinstance(source, bytes) or not source:
            raise ImageSourceRejected("malformed_image")
        if len(source) > self.limits.max_source_bytes:
            raise ImageSourceRejected("source_byte_limit")

        image = self._decode(source)
        try:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ImageSourceRejected("dimension_limit")
            if width > self.limits.max_width or height > self.limits.max_height:
                raise ImageSourceRejected("dimension_limit")
            if width * height > self.limits.max_pixels:
                raise ImageSourceRejected("pixel_limit")
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
            else:
                rgb = image.convert("RGB")
            output = BytesIO()
            rgb.save(
                output,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=False,
                progressive=False,
            )
            jpeg = output.getvalue()
        finally:
            image.close()

        if not _complete_jpeg(jpeg):
            raise ImageSourceRejected("normalization_failed")
        return ValidatedEquationImage(
            jpeg_bytes=jpeg,
            mime_type="image/jpeg",
            source_sha256=hashlib.sha256(source).hexdigest(),
            normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
            width=width,
            height=height,
            identity=expected,
        )

    def _decode(self, source: bytes) -> Image.Image:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(source)) as probe:
                    fmt = probe.format
                    frames = getattr(probe, "n_frames", 1)
                    self._check_dimensions(probe.size)
                    probe.verify()
                if frames != 1:
                    raise ImageSourceRejected("multiframe_image")
                if fmt == "PNG" and not _complete_png(source):
                    raise ImageSourceRejected("trailing_image_data")
                if fmt == "JPEG" and not _complete_jpeg(source):
                    raise ImageSourceRejected("trailing_image_data")
                if fmt == "WEBP" and not _complete_webp(source):
                    raise ImageSourceRejected("trailing_image_data")
                if fmt not in {"PNG", "JPEG", "WEBP"}:
                    raise ImageSourceRejected("unsupported_image_format")
                image = Image.open(BytesIO(source))
                self._check_dimensions(image.size)
                image.load()
                if getattr(image, "n_frames", 1) != 1:
                    image.close()
                    raise ImageSourceRejected("multiframe_image")
                return image
        except ImageSourceRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageSourceRejected("decompression_bomb") from exc
        except (UnidentifiedImageError, EOFError, OSError, SyntaxError, ValueError) as exc:
            raise ImageSourceRejected("malformed_image") from exc

    def _check_dimensions(self, size: tuple[int, int]) -> None:
        width, height = size
        if width <= 0 or height <= 0:
            raise ImageSourceRejected("dimension_limit")
        if width > self.limits.max_width or height > self.limits.max_height:
            raise ImageSourceRejected("dimension_limit")
        if width * height > self.limits.max_pixels:
            raise ImageSourceRejected("pixel_limit")

    @staticmethod
    def _identity(identity: Mapping[str, Any]) -> EquationImageIdentity:
        try:
            bbox = tuple(float(value) for value in identity["bbox"])
            if len(bbox) != 4:
                raise ValueError
            return EquationImageIdentity(
                page_number=int(identity["page_number"]),
                image_xref=int(identity["image_xref"]),
                image_index=int(identity["image_index"]),
                occurrence_ordinal=int(identity["occurrence_ordinal"]),
                bbox=bbox,
                occurrence_id=str(identity["occurrence_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ImageSourceRejected("occurrence_identity_mismatch") from exc


def prepare_equation_image(
    document: Any,
    identity: Mapping[str, Any],
    *,
    downstream: Callable[[ValidatedEquationImage], Any],
    mutate: Callable[[], Any],
) -> Any | None:
    """Run downstream work only after source validation; mutate only on a result."""
    try:
        validated = EquationImageSource().extract(document, identity)
    except ImageSourceRejected:
        return None
    result = downstream(validated)
    if result is None:
        return None
    mutate()
    return result
