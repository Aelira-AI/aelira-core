"""Synthetic font-code regressions; no customer material or provider calls."""

import io

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.remediation.content_tagger_v2 import ContentTaggerV2
from src.education.remediation.pdf_font_text import (
    FontTextBindingError,
    decode_page_text_runs,
)

pytestmark = pytest.mark.unit


def simple_pdf(content, *, encoding=Name.WinAnsiEncoding):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    font = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    if encoding is not None:
        font.Encoding = encoding
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(content)
    return pdf


def composite_pdf(*, mapped=True, unicode_hex=b"0054"):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    font = Dictionary(
        Type=Name.Font,
        Subtype=Name.Type0,
        BaseFont=Name.CustomCID,
        Encoding=Name("/Identity-H"),
        DescendantFonts=Array(
            [
                Dictionary(
                    Type=Name.Font,
                    Subtype=Name.CIDFontType2,
                    BaseFont=Name.CustomCID,
                    CIDSystemInfo=Dictionary(
                        Registry="Adobe", Ordering="Identity", Supplement=0
                    ),
                    CIDToGIDMap=Name.Identity,
                    DW=600,
                )
            ]
        ),
    )
    if mapped:
        font.ToUnicode = pdf.make_stream(
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
            b"/CMapName /Fixture-UCS def /CMapType 2 def "
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"1 beginbfchar <0037> <" + unicode_hex + b"> endbfchar "
            b"endcmap CMapName currentdict /CMap defineresource pop end end"
        )
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 30 300 Td <0037> Tj ET")
    return pdf


def snapshot(pdf):
    output = io.BytesIO()
    pdf.save(output, deterministic_id=True)
    return output.getvalue()


def test_winansi_codes_are_decoded_as_font_text_and_source_is_unchanged():
    with simple_pdf(b"BT /F1 12 Tf 30 300 Td <809193> Tj ET") as pdf:
        original = snapshot(pdf)
        assert decode_page_text_runs(pdf, 0)[0].text == "€‘“"
        assert snapshot(pdf) == original


def test_cid_character_code_is_not_pdfdoc_string():
    with composite_pdf() as pdf:
        run = decode_page_text_runs(pdf, 0)[0]
        assert (run.start, run.end, run.text) == (0, 5, "T")


def test_standard_font_default_and_font_state_across_runs():
    with simple_pdf(
        b"BT /F1 12 Tf 30 300 Td (First) Tj ET BT 30 280 Td (Second) Tj ET",
        encoding=None,
    ) as pdf:
        assert [run.text for run in decode_page_text_runs(pdf, 0)] == [
            "First",
            "Second",
        ]


def test_array_and_quote_show_operations_use_selected_font():
    with simple_pdf(
        b"BT /F1 12 Tf 14 TL 30 300 Td [(One) -300 (two)] TJ (Three) ' 0 0 (Four) \" ET"
    ) as pdf:
        text = decode_page_text_runs(pdf, 0)[0].text
        assert "One two" in text and "Three" in text and "Four" in text


@pytest.mark.parametrize("unicode_hex", [b"FFFD", b"D800"])
def test_unusable_unicode_map_is_refused(unicode_hex):
    with composite_pdf(unicode_hex=unicode_hex) as pdf:
        with pytest.raises(FontTextBindingError) as error:
            decode_page_text_runs(pdf, 0)
        assert str(error.value) == "source_text_mapping_unavailable"


def test_actualtext_cannot_hide_missing_glyph_map():
    with composite_pdf(mapped=False) as pdf:
        pdf.pages[0].Contents = pdf.make_stream(
            b"/Span << /ActualText (Trust me) >> BDC BT /F1 12 Tf 30 300 Td <0037> Tj ET EMC"
        )
        with pytest.raises(FontTextBindingError):
            decode_page_text_runs(pdf, 0)


@pytest.mark.parametrize(
    "content", [b"BT BT ET ET", b"ET", b"BT", b"Q", b"q", b"/F1 12 Tf (Outside) Tj"]
)
def test_unbalanced_or_out_of_scope_text_is_refused(content):
    with simple_pdf(content) as pdf:
        with pytest.raises(FontTextBindingError):
            decode_page_text_runs(pdf, 0)


def test_form_text_is_not_silently_omitted():
    with simple_pdf(b"/OCR Do") as pdf:
        form = pdf.make_stream(b"BT /F1 12 Tf (Hidden in form) Tj ET")
        form.Subtype = Name.Form
        form.BBox = Array([0, 0, 400, 400])
        form.Resources = pdf.pages[0].Resources
        pdf.pages[0].Resources = Dictionary(XObject=Dictionary(OCR=form))
        with pytest.raises(FontTextBindingError) as error:
            decode_page_text_runs(pdf, 0)
        assert error.value.code == "source_text_scope_unsupported"


def test_missing_font_reference_is_refused():
    with simple_pdf(b"BT /F2 12 Tf (Text) Tj ET") as pdf:
        with pytest.raises(FontTextBindingError):
            decode_page_text_runs(pdf, 0)


def test_generated_paragraph_uses_font_decoded_text():
    with simple_pdf(b"BT /F1 12 Tf 30 300 Td <80> Tj ET") as pdf:
        with fitz.open(stream=snapshot(pdf), filetype="pdf") as fitz_doc:
            tagger = ContentTaggerV2(pdf, fitz_doc)
            tagger.tag_all_pages()
        root = pdf.Root.StructTreeRoot
        document = root.K[0]
        paragraph = document.K[0]
        assert str(paragraph.ActualText) == "€"


def test_source_binding_matches_unicode_not_cid_bytes():
    with composite_pdf() as pdf:
        with fitz.open(stream=snapshot(pdf), filetype="pdf") as fitz_doc:
            tagger = ContentTaggerV2(pdf, fitz_doc)
            assert tagger.source_text_bindings(0, ["T"], set()) == [0]
            assert tagger.source_text_bindings(0, ["7"], set()) is None


@pytest.mark.parametrize("verify_fixes", [False, True])
def test_authoritative_binding_refusal_never_falls_back_or_publishes(
    tmp_path, monkeypatch, verify_fixes
):
    from unittest.mock import MagicMock
    from src.education.remediation import pdf_remediator as module
    from src.education.remediation.base import RemediationConfig

    source = tmp_path / "source.pdf"
    with simple_pdf(b"BT /F1 12 Tf 30 300 Td (Course notes) Tj ET") as pdf:
        pdf.save(source)
    original = source.read_bytes()
    fallback = MagicMock()
    monkeypatch.setattr(module, "ContentTagger", fallback)

    def refuse_after_mutation(tagger):
        # Even a refusal after a prior mutation must not enter either legacy
        # writer or publish the partially modified in-memory document.
        tagger.pdf.pages[0].Contents = tagger.pdf.make_stream(b"")
        raise FontTextBindingError()

    monkeypatch.setattr(module.ContentTaggerV2, "tag_all_pages", refuse_after_mutation)
    remediator = module.PdfRemediator(
        str(source),
        [],
        RemediationConfig(
            use_ai=False,
            verify_fixes=verify_fixes,
            create_backup=False,
            output_directory=str(tmp_path / "out"),
        ),
    )
    result = remediator.remediate()
    assert not result.success
    assert result.score_verification_reason == "source_text_mapping_unavailable"
    assert not result.has_output_claim()
    assert result.output_file is None
    assert not (tmp_path / "out" / "source_remediated.pdf").exists()
    assert source.read_bytes() == original
    fallback.assert_not_called()
