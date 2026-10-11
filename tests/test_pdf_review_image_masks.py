"""Raster image alpha is bounded; executable transparency remains refused."""

import io
import zlib
from hashlib import sha256

import pikepdf
import pytest
from pikepdf import Dictionary, Name, Array

from src.education import reading_order_snapshot as snapshot
from src.education.pdf_review_candidate import (
    inspect_pdf_edit_targets,
    create_pdf_edit_candidate,
    PDFEditRefused,
)
from test_reading_order_snapshot import make_pdf

pytestmark = pytest.mark.unit


def masked_pdf(damage=None):
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        page = pdf.pages[0]
        mask = pdf.make_stream(bytes([180]) * 600)
        mask.Type = Name.XObject
        mask.Subtype = Name.Image
        mask.Width = 30
        mask.Height = 20
        mask.ColorSpace = Name.DeviceGray
        mask.BitsPerComponent = 8
        mask.Decode = Array([1, 0])
        image = pdf.make_stream(bytes([60, 100, 200]) * 600)
        image.Type = Name.XObject
        image.Subtype = Name.Image
        image.Width = 30
        image.Height = 20
        image.ColorSpace = Name.DeviceRGB
        image.BitsPerComponent = 8
        image.SMask = mask
        page.Resources.XObject = Dictionary(Im=image)
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes() + b"\nq 30 0 0 20 200 50 cm /Im Do Q"
        )
        if damage == "dimensions":
            mask.Width = 31
        elif damage == "nested":
            mask.SMask = image
        elif damage == "form":
            mask.Subtype = Name.Form
        elif damage == "bits":
            mask.BitsPerComponent = 16
        elif damage == "color":
            mask.ColorSpace = Name.DeviceRGB
        elif damage == "payload":
            mask.write(bytes([180]) * 599)
        elif damage == "predictor":
            mask.write(
                zlib.compress(bytes([180]) * 600),
                filter=Name.FlateDecode,
                decode_parms=Dictionary(Predictor=12),
            )
        elif damage == "bomb":
            mask.write(b" " * (snapshot.MAX_DECODED_STREAM_BYTES + 1))
        elif damage == "graphics_state":
            page.Resources.ExtGState = Dictionary(
                GS=Dictionary(SMask=Dictionary(S=Name.Luminosity, G=mask))
            )
        elif damage == "signed":
            pdf.Root.Perms = Dictionary(
                DocMDP=pdf.make_indirect(
                    Dictionary(Type=Name.Sig, ByteRange=Array([0, 1, 2, 3]))
                )
            )
        output = io.BytesIO()
        pdf.save(output, stream_decode_level=pikepdf.StreamDecodeLevel.none)
        return output.getvalue()


def test_bounded_image_mask_allows_snapshot_target_and_saved_edit():
    source = masked_pdf()
    digest = sha256(source).hexdigest()
    target = next(
        t for t in inspect_pdf_edit_targets(source, digest) if t["can_set_heading"]
    )
    result = create_pdf_edit_candidate(
        source, digest, dict(kind="heading", target_id=target["target_id"], level=2)
    )
    assert result.needs_review
    with (
        pikepdf.open(io.BytesIO(source)) as before,
        pikepdf.open(io.BytesIO(result.content)) as after,
    ):
        assert (
            before.pages[0].Resources.XObject.Im.SMask.read_bytes()
            == after.pages[0].Resources.XObject.Im.SMask.read_bytes()
        )
    import pymupdf as fitz

    with (
        fitz.open(stream=source, filetype="pdf") as before,
        fitz.open(stream=result.content, filetype="pdf") as after,
    ):
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples


@pytest.mark.parametrize(
    "damage",
    [
        "dimensions",
        "nested",
        "form",
        "bits",
        "color",
        "payload",
        "predictor",
        "bomb",
        "graphics_state",
        "signed",
    ],
)
def test_invalid_masks_refused_before_render(damage, monkeypatch):
    source = masked_pdf(damage)

    def no_render(*args, **kwargs):
        raise AssertionError("renderer must not execute")

    monkeypatch.setattr(snapshot.fitz.Page, "get_pixmap", no_render)
    with pytest.raises(PDFEditRefused):
        inspect_pdf_edit_targets(source, sha256(source).hexdigest())


def test_mask_counts_on_each_image_execution(monkeypatch):
    source = masked_pdf()
    monkeypatch.setattr(
        snapshot, "MAX_IMAGE_PIXELS", 1500
    )  # two unique images: 1200; two invocations: 2400
    with pikepdf.open(io.BytesIO(source)) as pdf:
        pdf.pages[0].Contents = pdf.make_stream(
            pdf.pages[0].Contents.read_bytes() + b"\n/Im Do"
        )
        output = io.BytesIO()
        pdf.save(output)
        source = output.getvalue()
    with pytest.raises(PDFEditRefused):
        inspect_pdf_edit_targets(source, sha256(source).hexdigest())
