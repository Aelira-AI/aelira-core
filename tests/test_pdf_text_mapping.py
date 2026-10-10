"""Synthetic CID-font regressions; no customer PDFs or model calls."""

from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pdf_text_mapping import require_decodable_pdf_text
from src.education.remediation.score_measurement import MeasurementError
from src.education.remediation.score_reporting import score_fields

pytestmark = pytest.mark.unit


def test_blank_qpdf_empty_flate_page_is_absent_not_invalid(tmp_path):
    from src.education.remediation.pdf_text_mapping import inspect_pdf_text_quality

    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(300, 300))
        path = tmp_path / "blank.pdf"
        pdf.save(path)
    quality = inspect_pdf_text_quality(str(path))
    assert quality.reason is None
    assert quality.pages[0].status == "absent"
    assert quality.pages[0].glyph_count == 0


def test_compressed_page_limit_precedes_pdfminer_decompression(tmp_path, monkeypatch):
    from src.education.remediation import pdf_text_mapping as module
    from src.education.remediation.pdf_font_text import MAX_DECODED_STREAM_BYTES

    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Contents = pdf.make_stream(b" " * (MAX_DECODED_STREAM_BYTES + 1))
        path = tmp_path / "compressed_limit.pdf"
        pdf.save(path, compress_streams=True)
    stream_decoder = MagicMock()
    monkeypatch.setattr(module, "stream_value", stream_decoder)
    assert module.inspect_pdf_text_quality(str(path)).reason == "original_scan_failed"
    stream_decoder.assert_not_called()


def cid_pdf(tmp_path, *, mapped=False, encoding="Identity-H", name="CustomCID"):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    descendant = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.CIDFontType2,
            BaseFont=Name("/" + name),
            CIDSystemInfo=Dictionary(
                Registry="Adobe", Ordering="Identity", Supplement=0
            ),
            CIDToGIDMap=Name.Identity,
            DW=600,
        )
    )
    font = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type0,
            BaseFont=Name("/" + name),
            Encoding=Name("/" + encoding),
            DescendantFonts=Array([descendant]),
        )
    )
    if mapped:
        font.ToUnicode = pdf.make_stream(
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
            b"/CMapName /Fixture-UCS def /CMapType 2 def "
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"1 beginbfchar <0037> <0054> endbfchar "
            b"endcmap CMapName currentdict /CMap defineresource pop end end"
        )
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 30 300 Td <0037> Tj ET")
    path = tmp_path / "synthetic.pdf"
    pdf.save(path)
    pdf.close()
    return path


@pytest.mark.parametrize("encoding", ["Identity-H", "Identity-V"])
@pytest.mark.parametrize("name", ["CustomCID", "Helvetica", "NotHelvetica"])
def test_identity_encoding_is_not_a_unicode_map(tmp_path, encoding, name):
    path = cid_pdf(tmp_path, encoding=encoding, name=name)
    findings = StructureTreeChecker()._check_font_and_role_mapping(str(path))
    assert any(item.get("issue_type") == "missing_tounicode" for item in findings)


def test_used_unmapped_cid_is_refused_without_modifying_source(tmp_path):
    path = cid_pdf(tmp_path)
    original = path.read_bytes()
    with pytest.raises(MeasurementError) as error:
        require_decodable_pdf_text(str(path))
    assert error.value.code == "source_text_mapping_unavailable"
    assert str(error.value) == "source_text_mapping_unavailable"
    assert path.read_bytes() == original


def test_real_supplied_unicode_map_passes_read_only_preflight(tmp_path):
    path = cid_pdf(tmp_path, mapped=True)
    original = path.read_bytes()
    require_decodable_pdf_text(str(path))
    findings = StructureTreeChecker()._check_font_and_role_mapping(str(path))
    assert not any(item.get("issue_type") == "missing_tounicode" for item in findings)
    assert path.read_bytes() == original


def test_standard_simple_font_keeps_its_defined_encoding(tmp_path):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    page.Resources = Dictionary(
        Font=Dictionary(
            F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )
    )
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 30 300 Td (Course overview) Tj ET")
    path = tmp_path / "simple.pdf"
    pdf.save(path)
    pdf.close()
    require_decodable_pdf_text(str(path))
    assert not StructureTreeChecker()._check_font_and_role_mapping(str(path))


def test_refusal_precedes_document_mutation_and_provider_calls(tmp_path, monkeypatch):
    path = cid_pdf(tmp_path)
    original = path.read_bytes()
    provider = MagicMock()
    remediator = PdfRemediator(
        str(path),
        [{"type": "alt_text", "message": "Image needs a description"}],
        RemediationConfig(
            use_ai=True, create_backup=False, output_directory=str(tmp_path / "out")
        ),
        ai_client=provider,
        alt_text_client=provider,
    )
    # Isolate the sequence from OCR: an image-heavy source with an existing
    # readable extraction layer is staged without invoking OCR in production.
    monkeypatch.setattr(remediator, "_stage_working_copy", lambda: None)
    load = MagicMock()
    monkeypatch.setattr(remediator, "_load_document", load)
    result = remediator.remediate()
    assert result.success is False
    assert result.score_verification_reason == "source_text_mapping_unavailable"
    assert result.fixed_count == 0 and result.manual_count == 1
    assert result.ai_calls_made == 0
    assert result.output_file is None and not result.has_output_claim()
    assert provider.mock_calls == []
    load.assert_not_called()
    assert path.read_bytes() == original
    fields = score_fields(result.model_dump(), original_score=49)
    assert fields["score_verification_reason"] == "source_text_mapping_unavailable"
    assert fields["score_verified"] is False
    assert fields["remediated_compliance_score"] is None


def test_unknown_parser_failure_never_leaks_its_message(tmp_path, monkeypatch):
    from src.education.remediation import pdf_text_mapping

    path = cid_pdf(tmp_path, mapped=True)

    def fail(*args, **kwargs):
        raise ValueError("private document text and /private/path")

    monkeypatch.setattr(pdf_text_mapping._PageInterpreter, "process_page", fail)
    with pytest.raises(MeasurementError) as error:
        require_decodable_pdf_text(str(path))
    assert str(error.value) == "original_scan_failed"


def test_decoded_stream_limit_refuses_before_text_execution(tmp_path, monkeypatch):
    from src.education.remediation import pdf_text_mapping

    path = cid_pdf(tmp_path, mapped=True)
    monkeypatch.setattr(pdf_text_mapping, "MAX_DECODED_PAGE_BYTES", 1)
    interpreter = MagicMock()
    monkeypatch.setattr(pdf_text_mapping._PageInterpreter, "process_page", interpreter)
    with pytest.raises(MeasurementError, match="original_scan_failed"):
        require_decodable_pdf_text(str(path))
    interpreter.assert_not_called()


def test_quality_reports_glyphs_and_missing_mapping_without_source_text(tmp_path):
    from src.education.remediation.pdf_text_mapping import inspect_pdf_text_quality

    mapped = inspect_pdf_text_quality(str(cid_pdf(tmp_path, mapped=True)))
    assert mapped.reason is None
    assert mapped.pages[0].status == "decoded"
    assert mapped.pages[0].glyph_count == 1
    unmapped = inspect_pdf_text_quality(str(cid_pdf(tmp_path)))
    assert unmapped.reason == "source_text_mapping_unavailable"
    assert unmapped.pages[0].status == "mapping_unavailable"
    assert not hasattr(unmapped.pages[0], "text")


def test_quality_reports_form_scope_instead_of_absent_text(tmp_path):
    from src.education.remediation.pdf_text_mapping import inspect_pdf_text_quality

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    form = pdf.make_stream(b"BT /F1 12 Tf (Text inside a Form) Tj ET")
    form.Subtype = Name.Form
    form.BBox = Array([0, 0, 400, 400])
    form.Resources = Dictionary(
        Font=Dictionary(
            F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )
    )
    page.Resources = Dictionary(XObject=Dictionary(OCR=form))
    page.Contents = pdf.make_stream(b"/OCR Do")
    path = tmp_path / "form.pdf"
    pdf.save(path)
    pdf.close()
    quality = inspect_pdf_text_quality(str(path))
    assert quality.pages[0].status == "scope_unsupported"
    assert quality.reason == "source_text_scope_unsupported"
    with pytest.raises(MeasurementError, match="source_text_scope_unsupported"):
        require_decodable_pdf_text(str(path))
