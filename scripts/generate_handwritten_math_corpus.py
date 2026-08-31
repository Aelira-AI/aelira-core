#!/usr/bin/env python3
"""Generate the project-authored handwritten-math suitability corpus."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.education.handwritten_math_suitability import POLICY_VERSION

WIDTH = 320
HEIGHT = 120
AUTHOR = "Aelira project contributors"
METHOD = "deterministic synthetic stroke drawing"
LICENSE = "AGPL-3.0-only"
SOURCE_CLASS = "project_authored_synthetic"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _stored_zlib(payload: bytes) -> bytes:
    """Encode a deterministic no-compression zlib stream."""
    result = bytearray(b"\x78\x01")
    offset = 0
    while offset < len(payload):
        block = payload[offset : offset + 65_535]
        offset += len(block)
        result.append(1 if offset == len(payload) else 0)
        length = len(block)
        result.extend(struct.pack("<H", length))
        result.extend(struct.pack("<H", length ^ 0xFFFF))
        result.extend(block)
    result.extend(struct.pack(">I", zlib.adler32(payload) & 0xFFFFFFFF))
    return bytes(result)


def _png_bytes(image: Image.Image) -> bytes:
    grayscale = image.convert("L")
    raw = b"".join(
        b"\x00" + grayscale.crop((0, y, grayscale.width, y + 1)).tobytes()
        for y in range(grayscale.height)
    )
    ihdr = struct.pack(">IIBBBBB", grayscale.width, grayscale.height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", _stored_zlib(raw))
        + _chunk(b"IEND", b"")
    )


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    return image, ImageDraw.Draw(image)


def _glyph_x(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line(
        ((x, y - 13), (x + 4, y - 6), (x + 11, y + 5), (x + 18, y + 13)),
        fill=ink,
        width=4,
        joint="curve",
    )
    draw.line(
        ((x + 19, y - 15), (x + 13, y - 5), (x + 7, y + 5), (x - 1, y + 14)),
        fill=ink,
        width=4,
        joint="curve",
    )


def _glyph_y(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line(
        ((x, y - 12), (x + 4, y - 5), (x + 9, y + 1), (x + 17, y - 13)),
        fill=ink,
        width=4,
        joint="curve",
    )
    draw.line(
        ((x + 10, y), (x + 8, y + 7), (x + 4, y + 14)),
        fill=ink,
        width=4,
        joint="curve",
    )


def _glyph_plus(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line((x - 1, y + 1, x + 21, y - 1), fill=ink, width=4)
    draw.line((x + 9, y - 11, x + 11, y + 11), fill=ink, width=4)


def _glyph_one(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line(
        ((x + 1, y - 7), (x + 7, y - 13), (x + 8, y), (x + 6, y + 13)),
        fill=ink,
        width=4,
        joint="curve",
    )


def _glyph_zero(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line(
        (
            (x + 10, y - 14),
            (x + 4, y - 12),
            (x, y - 4),
            (x + 1, y + 8),
            (x + 7, y + 14),
            (x + 14, y + 12),
            (x + 19, y + 3),
            (x + 18, y - 8),
            (x + 10, y - 14),
        ),
        fill=ink,
        width=4,
        joint="curve",
    )


def _glyph_two(draw: ImageDraw.ImageDraw, x: int, y: int, ink: int) -> None:
    draw.line(
        (
            (x + 1, y - 7),
            (x + 4, y - 13),
            (x + 13, y - 15),
            (x + 20, y - 9),
            (x + 18, y - 2),
            (x + 11, y + 4),
            (x + 3, y + 10),
            (x, y + 14),
            (x + 21, y + 13),
        ),
        fill=ink,
        width=4,
        joint="curve",
    )


def _equation(
    draw: ImageDraw.ImageDraw,
    *,
    y: int = 60,
    ink: int = 18,
    offset: int = 0,
) -> None:
    _glyph_x(draw, 28 + offset, y, ink)
    _glyph_two(draw, 59 + offset, y - 10, ink)
    _glyph_plus(draw, 100 + offset, y, ink)
    _glyph_y(draw, 137 + offset, y, ink)
    draw.line((180 + offset, y - 7, 209 + offset, y - 5), fill=ink, width=4)
    draw.line((181 + offset, y + 6, 207 + offset, y + 7), fill=ink, width=4)
    _glyph_one(draw, 236 + offset, y, ink)
    _glyph_zero(draw, 260 + offset, y, ink)


def _legible(offset: int = 0) -> Image.Image:
    image, draw = _canvas()
    _equation(draw, offset=offset)
    return image


def _low_contrast(ink: int = 190) -> Image.Image:
    image, draw = _canvas()
    _equation(draw, ink=ink)
    return image


def _strike_through() -> Image.Image:
    image, draw = _canvas()
    _equation(draw)
    draw.line((16, 88, 304, 32), fill=24, width=5)
    return image


def _annotation() -> Image.Image:
    image, draw = _canvas()
    _equation(draw)
    draw.ellipse((12, 24, 306, 96), outline=30, width=4)
    draw.line((285, 30, 310, 12), fill=30, width=4)
    return image


def _multiple_lines(offset: int = 0) -> Image.Image:
    image = Image.new("L", (WIDTH, 150), 255)
    draw = ImageDraw.Draw(image)
    _equation(draw, y=43, offset=offset)
    _equation(draw, y=108, offset=offset)
    return image


def _diagram() -> Image.Image:
    image, draw = _canvas()
    draw.rectangle((28, 38, 82, 78), outline=20, width=4)
    draw.rectangle((226, 38, 282, 78), outline=20, width=4)
    draw.line((84, 58, 220, 58), fill=20, width=4)
    draw.line((220, 58, 205, 48), fill=20, width=4)
    draw.line((220, 58, 205, 68), fill=20, width=4)
    return image


def _unsupported_style(seed: int = 0) -> Image.Image:
    image, draw = _canvas()
    points = []
    for index in range(44):
        x = 20 + ((index * 47 + seed * 13) % 280)
        y = 18 + ((index * 31 + seed * 7) % 84)
        points.append((x, y))
    draw.line(points, fill=22, width=7, joint="curve")
    return image


def _non_math_word(variant: int = 0) -> Image.Image:
    image, draw = _canvas()
    baseline = 70 + variant
    x = 35
    draw.line(
        (x, 34, x, baseline, x, 54, x + 18, 48, x + 20, baseline), fill=24, width=4
    )
    x += 38
    draw.arc((x, 48, x + 30, 76), 20, 330, fill=24, width=4)
    x += 42
    draw.line((x, 34, x, baseline), fill=24, width=4)
    x += 18
    draw.line((x, 34, x, baseline), fill=24, width=4)
    x += 24
    draw.ellipse((x, 48, x + 28, 75), outline=24, width=4)
    return image


def _non_math_note() -> Image.Image:
    image, draw = _canvas()
    draw.line((35, 44, 50, 72, 64, 44, 78, 72, 92, 44), fill=28, width=4)
    draw.arc((108, 45, 140, 75), 15, 330, fill=28, width=4)
    draw.line((156, 42, 156, 74, 174, 52, 190, 74), fill=28, width=4)
    draw.line((212, 48, 245, 48), fill=28, width=4)
    return image


def _similar_equals(dots: int = 1) -> Image.Image:
    image, draw = _canvas()
    draw.line((126, 51, 194, 51), fill=20, width=5)
    draw.line((126, 68, 194, 68), fill=20, width=5)
    for index in range(dots):
        x = 90 + index * 130
        draw.ellipse((x, 57, x + 5, 62), fill=20)
    return image


def _similar_table() -> Image.Image:
    image, draw = _canvas()
    for y in (32, 58, 84):
        draw.line((55, y, 265, y), fill=20, width=4)
    for x in (55, 125, 195, 265):
        draw.line((x, 30, x, 86), fill=20, width=4)
    return image


FixtureFactory = Callable[[], Image.Image]

FIXTURES: tuple[tuple[str, str, str, bool, FixtureFactory], ...] = (
    ("legible-linear", "legible_handwriting", "eligible", True, _legible),
    ("legible-shifted", "legible_handwriting", "eligible", True, lambda: _legible(3)),
    ("low-contrast", "low_contrast", "human_review", True, _low_contrast),
    ("strike-through", "strike_through", "human_review", True, _strike_through),
    ("annotated", "annotation", "human_review", True, _annotation),
    ("multiple-lines", "multiple_lines", "human_review", True, _multiple_lines),
    ("diagram-only", "diagram", "unsupported", True, _diagram),
    ("unsupported-dense", "unsupported_style", "unsupported", True, _unsupported_style),
    ("non-math-word", "non_math_handwriting", "unsupported", True, _non_math_word),
    ("non-math-note", "non_math_handwriting", "unsupported", True, _non_math_note),
    (
        "similar-equals",
        "visually_similar_non_math",
        "unsupported",
        True,
        _similar_equals,
    ),
    ("similar-table", "visually_similar_non_math", "unsupported", True, _similar_table),
    (
        "legible-full-variant",
        "legible_handwriting",
        "eligible",
        False,
        lambda: _legible(6),
    ),
    (
        "low-contrast-full",
        "low_contrast",
        "human_review",
        False,
        lambda: _low_contrast(184),
    ),
    (
        "non-math-full",
        "non_math_handwriting",
        "unsupported",
        False,
        lambda: _non_math_word(2),
    ),
    (
        "similar-full",
        "visually_similar_non_math",
        "unsupported",
        False,
        lambda: _similar_equals(2),
    ),
)


def generate(output_dir: Path) -> None:
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    fixtures = []
    ci_subset = []
    for fixture_id, category, disposition, ci, factory in FIXTURES:
        relative_path = f"images/{fixture_id}.png"
        payload = _png_bytes(factory())
        (output_dir / relative_path).write_bytes(payload)
        fixtures.append(
            {
                "always_human_review": disposition != "eligible",
                "author": AUTHOR,
                "category": category,
                "ci": ci,
                "expected_disposition": disposition,
                "generation_method": METHOD,
                "id": fixture_id,
                "license": LICENSE,
                "path": relative_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "source_class": SOURCE_CLASS,
            }
        )
        if ci:
            ci_subset.append(fixture_id)
    manifest = {
        "ci_subset": ci_subset,
        "corpus_version": "synthetic-strokes-v1",
        "fixtures": fixtures,
        "generated_by": "scripts/generate_handwritten_math_corpus.py",
        "license": LICENSE,
        "metric_gates": {
            "eligible_false_positives_max": 0,
            "expected_disposition_accuracy_min_ppm": 1_000_000,
        },
        "policy_version": POLICY_VERSION,
        "schema_version": "handwritten-math-corpus-v1",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/handwritten_math"),
    )
    args = parser.parse_args()
    generate(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
