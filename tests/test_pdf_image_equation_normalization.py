from dataclasses import FrozenInstanceError
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from src.education.pdf_checks.image_checker import _displayed_image_occurrences


BBOX = (10.0, 20.0, 90.0, 55.0)


class Page:
    def get_images(self, full=True):
        assert full is True
        return [(7,)]

    def get_image_info(self, xrefs=True):
        assert xrefs is True
        return [{"xref": 7, "bbox": BBOX}]


IDENTITY = _displayed_image_occurrences(Page(), 1)[0]


class Document:
    def __init__(self, payload, ext):
        self.payload = payload
        self.ext = ext
        self.extract_calls = []

    def __getitem__(self, index):
        assert index == 0
        return Page()

    def extract_image(self, xref):
        self.extract_calls.append(xref)
        return {"image": self.payload, "ext": self.ext}


def encoded(fmt, mode="RGB", color="white", size=(24, 12), **save_kwargs):
    output = BytesIO()
    Image.new(mode, size, color).save(output, format=fmt, **save_kwargs)
    return output.getvalue()


@pytest.mark.parametrize(
    ("fmt", "mode", "color"),
    [
        ("PNG", "RGB", "white"),
        ("PNG", "RGBA", (255, 0, 0, 0)),
        ("JPEG", "RGB", "white"),
        ("JPEG", "CMYK", (0, 0, 0, 0)),
        ("WEBP", "RGB", "white"),
    ],
)
def test_exact_occurrence_normalizes_supported_sources_to_real_deterministic_jpeg(
    fmt, mode, color
):
    from src.education.remediation.equation_image_source import EquationImageSource

    source = encoded(fmt, mode, color)
    first = EquationImageSource().extract(Document(source, fmt.lower()), IDENTITY)
    second = EquationImageSource().extract(Document(source, fmt.lower()), IDENTITY)

    assert first.jpeg_bytes[:2] == b"\xff\xd8"
    assert first.jpeg_bytes[-2:] == b"\xff\xd9"
    assert first.mime_type == "image/jpeg"
    assert first.width == 24 and first.height == 12
    assert first.source_sha256 == sha256(source).hexdigest()
    assert first.normalized_sha256 == sha256(first.jpeg_bytes).hexdigest()
    assert first.jpeg_bytes == second.jpeg_bytes
    assert first.identity.occurrence_id == IDENTITY["occurrence_id"]
    with Image.open(BytesIO(first.jpeg_bytes)) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert not image.getexif()


def test_payload_and_identity_are_immutable():
    from src.education.remediation.equation_image_source import EquationImageSource

    result = EquationImageSource().extract(Document(encoded("PNG"), "png"), IDENTITY)
    with pytest.raises(FrozenInstanceError):
        result.width = 99
    with pytest.raises(FrozenInstanceError):
        result.identity.image_xref = 99


@pytest.mark.parametrize(
    "changed",
    [
        {"image_xref": 8},
        {"image_index": 1},
        {"occurrence_ordinal": 1},
        {"bbox": (10.0, 20.0, 91.0, 55.0)},
        {"occurrence_id": "different"},
    ],
)
def test_occurrence_drift_rejects_before_extraction(changed):
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceRejected,
    )

    identity = {**IDENTITY, **changed}
    doc = Document(encoded("PNG"), "png")
    with pytest.raises(ImageSourceRejected, match="occurrence_identity_mismatch"):
        EquationImageSource().extract(doc, identity)
    assert doc.extract_calls == []


@pytest.mark.parametrize(
    "payload,ext,error",
    [
        (b"not an image", "png", "malformed_image"),
        (encoded("PNG") + b"trailing", "png", "trailing_image_data"),
        (encoded("JPEG") + b"trailing", "jpeg", "trailing_image_data"),
        (encoded("WEBP") + b"trailing", "webp", "trailing_image_data"),
    ],
)
def test_malformed_and_polyglot_sources_fail_closed(payload, ext, error):
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceRejected,
    )

    with pytest.raises(ImageSourceRejected, match=error):
        EquationImageSource().extract(Document(payload, ext), IDENTITY)


def test_multiframe_source_is_rejected():
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceRejected,
    )

    output = BytesIO()
    frames = [Image.new("RGB", (8, 8), "white"), Image.new("RGB", (8, 8), "black")]
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(ImageSourceRejected, match="multiframe_image"):
        EquationImageSource().extract(Document(output.getvalue(), "webp"), IDENTITY)


def test_byte_dimension_and_pixel_bounds_are_checked():
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceLimits,
        ImageSourceRejected,
    )

    payload = encoded("PNG", size=(24, 12))
    for limits, error in [
        (ImageSourceLimits(max_source_bytes=len(payload) - 1), "source_byte_limit"),
        (ImageSourceLimits(max_width=23), "dimension_limit"),
        (ImageSourceLimits(max_pixels=287), "pixel_limit"),
    ]:
        with pytest.raises(ImageSourceRejected, match=error):
            EquationImageSource(limits).extract(Document(payload, "png"), IDENTITY)


def test_dimension_limit_rejects_before_full_pixel_load(monkeypatch):
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceLimits,
        ImageSourceRejected,
    )

    payload = encoded("PNG", size=(24, 12))

    def forbidden_load(self):
        raise AssertionError("pixel load must not run after probe exceeds bounds")

    monkeypatch.setattr(Image.Image, "load", forbidden_load)
    with pytest.raises(ImageSourceRejected, match="dimension_limit"):
        EquationImageSource(ImageSourceLimits(max_width=23)).extract(
            Document(payload, "png"), IDENTITY
        )


def test_decompression_bomb_warning_is_rejected(monkeypatch):
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceRejected,
    )

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ImageSourceRejected, match="decompression_bomb"):
        EquationImageSource().extract(Document(encoded("PNG", size=(24, 12)), "png"), IDENTITY)


def test_webp_unknown_chunk_is_rejected_even_when_riff_length_is_adjusted():
    from src.education.remediation.equation_image_source import (
        EquationImageSource,
        ImageSourceRejected,
    )

    source = bytearray(encoded("WEBP"))
    source.extend(b"EVIL\x04\x00\x00\x00data")
    source[4:8] = (len(source) - 8).to_bytes(4, "little")

    with pytest.raises(ImageSourceRejected, match="trailing_image_data"):
        EquationImageSource().extract(Document(bytes(source), "webp"), IDENTITY)


def test_rejected_source_never_invokes_downstream_or_mutates():
    from src.education.remediation.equation_image_source import prepare_equation_image

    ai_calls = []
    mutation_calls = []
    result = prepare_equation_image(
        Document(b"bad", "png"),
        IDENTITY,
        downstream=lambda payload: ai_calls.append(payload),
        mutate=lambda: mutation_calls.append(True),
    )
    assert result is None
    assert ai_calls == []
    assert mutation_calls == []
