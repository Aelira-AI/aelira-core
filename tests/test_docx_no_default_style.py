"""DOCX scan must survive documents with no default paragraph style.

python-docx resolves ``paragraph.style`` via the paragraph's ``w:pStyle``; when
a paragraph has none, it falls back to ``styles.default(WD_STYLE_TYPE.PARAGRAPH)``,
which returns ``None`` when styles.xml defines no style marked ``w:default="1"``.
Word always writes one, but pandoc / Google Docs export / LibreOffice / raw
python-docx output can omit it — every unstyled body paragraph then has
``para.style is None`` and any ``para.style.name`` dereference raises
``AttributeError: 'NoneType' object has no attribute 'name'``.

Observed with real-world documents: a DOCX defining many styles but marking
none as default, with several unstyled body paragraphs, kills the scan mid-run.

The fixture strips the default flag explicitly — a plain python-docx document
does NOT reproduce this because the bundled template marks Normal as default.
"""

import re
import zipfile
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

docx = pytest.importorskip("docx")

from src.education.docx_processor import DocxProcessor  # noqa: E402


def _build_no_default_style_docx(path: Path) -> None:
    """A DOCX with explicit Heading styles but NO default paragraph style."""
    doc = docx.Document()
    doc.add_heading("AZ-900 Flashcards", level=1)  # explicit w:pStyle
    doc.add_paragraph("Cloud concepts overview.")  # no w:pStyle
    doc.add_heading("Pricing", level=2)  # explicit w:pStyle
    doc.add_paragraph("Pay as you go.")  # no w:pStyle
    doc.add_paragraph("Reserved instances.")  # no w:pStyle
    staged = path.with_suffix(".staged.docx")
    doc.save(str(staged))

    # Rewrite word/styles.xml with every w:default="1"/"true" flag removed.
    with zipfile.ZipFile(staged) as zin, zipfile.ZipFile(path, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                data = re.sub(rb'\sw:default="(?:1|true)"', b"", data)
            zout.writestr(item, data)
    staged.unlink()


@pytest.fixture()
def no_default_docx(tmp_path):
    p = tmp_path / "no_default_style.docx"
    _build_no_default_style_docx(p)
    # Sanity: the fixture actually reproduces the python-docx condition.
    d = docx.Document(str(p))
    unstyled = [para for para in d.paragraphs if para.style is None]
    assert unstyled, (
        "fixture failed to reproduce: every paragraph still resolves a style "
        "(default paragraph style not stripped?)"
    )
    return p


@pytest.mark.unit
def test_extract_document_context_survives_none_style(no_default_docx):
    proc = DocxProcessor()
    d = docx.Document(str(no_default_docx))
    ctx = proc._extract_document_context(d, "no_default_style.docx")
    headings = [h["text"] for h in ctx.get("headings", [])]
    assert "AZ-900 Flashcards" in headings
    assert "Pricing" in headings


@pytest.mark.unit
def test_process_docx_completes_with_none_style(no_default_docx):
    proc = DocxProcessor()  # all AI flags off
    result = proc.process_docx(str(no_default_docx))
    assert result is not None
