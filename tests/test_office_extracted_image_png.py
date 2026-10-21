"""Office scanners keep 32-bit greyscale images analysable.

The Word, PowerPoint and Excel scanners write each embedded image to a
temporary PNG before analysing it. Pillow 13 no longer writes mode ``I``
images as PNG, so a 32-bit or signed 16-bit TIFF in a document must be
converted first, with the same 16-bit clipping earlier Pillow releases applied
when they wrote it directly.
"""

import io
import os
from types import SimpleNamespace

import pytest
from docx.oxml.ns import qn
from PIL import Image

from src.education.docx_processor import DocxProcessor
from src.education.office_images import save_extracted_png
from src.education.pptx_processor import PowerPointProcessor
from src.education.scan_completeness import require_complete_scan
from src.education.xlsx_processor import XlsxProcessor

pytestmark = pytest.mark.unit

VALUES = (-5, 0, 40000, 70000)
CLIPPED = [0, 0, 40000, 65535]


def _mode_i_tiff() -> bytes:
    image = Image.new("I", (len(VALUES), 1))
    for x, value in enumerate(VALUES):
        image.putpixel((x, 0), value)
    output = io.BytesIO()
    image.save(output, format="TIFF")
    return output.getvalue()


def _png_pixels(payload: bytes) -> list[int]:
    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert image.mode == "I;16"
        return [image.getpixel((x, 0)) for x in range(image.width)]


def test_mode_i_image_is_written_with_sixteen_bit_clipping():
    with Image.open(io.BytesIO(_mode_i_tiff())) as image:
        assert image.mode == "I"
        output = io.BytesIO()
        save_extracted_png(image, output)
    assert _png_pixels(output.getvalue()) == CLIPPED


def test_other_modes_are_written_as_before():
    image = Image.new("RGB", (2, 1), (10, 20, 30))
    output = io.BytesIO()
    save_extracted_png(image, output)
    expected = io.BytesIO()
    image.save(expected, format="PNG")
    assert output.getvalue() == expected.getvalue()


def _from_docx(blob: bytes):
    part = SimpleNamespace(related_parts={"rId7": SimpleNamespace(blob=blob)})
    run = SimpleNamespace(part=part)
    return DocxProcessor()._extract_image_from_run(run, {qn("r:embed"): "rId7"})


def _from_pptx(blob: bytes):
    shape = SimpleNamespace(image=SimpleNamespace(blob=blob))
    return PowerPointProcessor()._extract_image_from_shape(shape)


def _from_xlsx(blob: bytes):
    return XlsxProcessor()._extract_image(SimpleNamespace(_data=lambda: blob))


@pytest.mark.parametrize(
    "extract", (_from_docx, _from_pptx, _from_xlsx), ids=("docx", "pptx", "xlsx")
)
def test_office_scanners_extract_mode_i_images(extract):
    # A failed extraction is recorded as an incomplete check, which this
    # context turns into an error.
    with require_complete_scan(True):
        path = extract(_mode_i_tiff())
    assert path is not None
    try:
        with open(path, "rb") as stream:
            assert _png_pixels(stream.read()) == CLIPPED
    finally:
        os.unlink(path)
