"""Independent authored text oracles, not decoder-derived expectations."""

import pikepdf
import pymupdf
import pytest
from pdfminer.psparser import LIT

from pdf_font_fixtures import cmap_bytes, snapshot, truetype_pdf
from src.education.pdf_checks.font_mapping import (
    FontMappingUnavailable,
    simple_encoding_map,
)
from src.education.remediation.pdf_font_text import (
    FontTextBindingError,
    decode_page_text_runs,
)
from src.education.remediation.pdf_text_mapping import inspect_pdf_text_quality

pytestmark = pytest.mark.unit


def test_nonidentity_chain_resolves_actual_glyphs_and_preserves_source():
    with truetype_pdf(gid_map=b"\0\0\0\2\0\1") as pdf:
        original = snapshot(pdf)
        assert decode_page_text_runs(pdf, 0)[0].text == "BA"
        assert snapshot(pdf) == original


def test_identity_unique_chain_and_supplementary_unicode():
    with truetype_pdf() as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "AB"
    with truetype_pdf(supplementary=True) as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "😀B"


def test_explicit_map_is_semantics_even_with_nonidentity_addressing():
    with truetype_pdf(gid_map=b"\0\0\0\2\0\1", mappings={1: "A", 2: "B"}) as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "AB"


@pytest.mark.parametrize(
    "options",
    [
        {"alias": True},
        {"gid_map": b"\0\0\0"},
        {"gid_map": b"\0\0"},
        {"gid_map": b"\0\0\0\0\0\2"},
        {"gid_map": b"\0\0\xff\xff\0\2"},
        {"codes": (65535,)},
    ],
)
def test_ambiguous_missing_or_invalid_used_glyph_is_refused(options):
    with truetype_pdf(**options) as pdf:
        with pytest.raises(
            FontTextBindingError, match="source_text_mapping_unavailable"
        ):
            decode_page_text_runs(pdf, 0)


def test_complete_base_plus_differences_and_unused_unknown_name():
    spec = {
        "Subtype": LIT("Type1"),
        "BaseFont": LIT("Helvetica"),
        "Encoding": {
            "BaseEncoding": LIT("WinAnsiEncoding"),
            "Differences": [65, LIT("B"), 200, LIT("doesNotExist")],
        },
    }
    mapping = simple_encoding_map(spec)
    assert mapping[65] == "B" and mapping[66] == "B" and mapping[127] == "•"
    assert 200 not in mapping
    spec["Encoding"]["Differences"] = [65, LIT("f_f_i")]
    assert simple_encoding_map(spec)[65] == "ffi"


def test_arbitrary_omitted_base_is_not_standard_encoding():
    with pytest.raises(FontMappingUnavailable):
        simple_encoding_map(
            {
                "Subtype": LIT("TrueType"),
                "BaseFont": LIT("Custom"),
                "Encoding": {"Differences": [65, LIT("A")]},
            }
        )


def test_wrong_valid_map_is_decodable_but_has_no_fidelity_claim(tmp_path):
    results, pixels = [], []
    for wrong in (False, True):
        with pikepdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(400, 400))
            font = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                    Encoding=pikepdf.Name.WinAnsiEncoding,
                )
            )
            text = "ACCESSIBLE 105 - 1.5"
            mappings = {ord(char): char for char in text}
            if wrong:
                mappings.update({65: "Z", 49: "7"})
            font.ToUnicode = pdf.make_stream(cmap_bytes(mappings, width=1))
            page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
            page.Contents = pdf.make_stream(
                b"BT /F1 16 Tf 30 300 Td (ACCESSIBLE 105 - 1.5) Tj ET"
            )
            data = snapshot(pdf)
            results.append(decode_page_text_runs(pdf, 0)[0].text)
        path = tmp_path / f"{wrong}.pdf"
        path.write_bytes(data)
        quality = inspect_pdf_text_quality(str(path))
        assert quality.reason is None and quality.fidelity_status == "unassessed"
        assert quality.pages[0].evidence_origins == ("declared_tounicode",)
        with pymupdf.open(stream=data, filetype="pdf") as rendered:
            pixels.append(rendered[0].get_pixmap().samples)
    assert results == ["ACCESSIBLE 105 - 1.5", "ZCCESSIBLE 705 - 7.5"]
    assert pixels[0] == pixels[1]
    # Authorship supplies this oracle; agreement among decoders cannot.
    assert results[1] != "ACCESSIBLE 105 - 1.5"


def test_old_pdfminer_reverse_cmap_is_never_constructed(monkeypatch):
    from pdfminer.pdffont import TrueTypeFont

    def forbidden(_self):
        raise AssertionError("unsafe implicit inverse cmap called")

    monkeypatch.setattr(TrueTypeFont, "create_unicode_map", forbidden)
    with truetype_pdf() as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "AB"


def test_defined_character_collection_remains_supported():
    with truetype_pdf() as pdf:
        font = pdf.pages[0].Resources.Font.F1
        font.DescendantFonts[0].CIDSystemInfo = pikepdf.Dictionary(
            Registry="Adobe", Ordering="Japan1", Supplement=0
        )
        pdf.pages[0].Contents = pdf.make_stream(b"BT /F1 12 Tf <0022> Tj ET")
        assert decode_page_text_runs(pdf, 0)[0].text == "A"


def test_cmap_overlapping_groups_and_nonunicode_duplicate_offset_are_rejected():
    import struct
    from src.education.pdf_checks.font_mapping import _bound_unicode_cmap

    groups = [(65, 65, 1), (65, 65, 2), (66, 66, 1)]
    subtable = struct.pack(
        ">HHIII", 12, 0, 16 + 12 * len(groups), 0, len(groups)
    ) + b"".join(struct.pack(">III", *group) for group in groups)
    raw = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + subtable
    with pytest.raises(FontMappingUnavailable):
        _bound_unicode_cmap(raw)
    # The malformed non-Unicode record would be materialized by fontTools
    # when two records share its offset, even before table.isUnicode().
    normal = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(">III", 65, 65, 1)
    huge = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(
        ">III", 0, 0xFFFFFFFF, 0
    )
    raw = (
        struct.pack(">HH", 0, 3)
        + struct.pack(">HHI", 3, 10, 28)
        + struct.pack(">HHI", 1, 0, 56)
        + struct.pack(">HHI", 1, 1, 56)
        + normal
        + huge
    )
    with pytest.raises(FontMappingUnavailable):
        _bound_unicode_cmap(raw)


@pytest.mark.parametrize("signature", [b"wOFF", b"wOF2", b"ttcf", b"OTTO"])
def test_unsupported_font_container_rejected_before_fonttools(signature, monkeypatch):
    from src.education.pdf_checks import font_mapping
    from pdf_font_fixtures import tiny_truetype

    def forbidden(*args, **kwargs):
        raise AssertionError("container reached fontTools")

    with truetype_pdf() as pdf:
        program = signature + tiny_truetype()[4:]
        pdf.pages[0].Resources.Font.F1.DescendantFonts[0].FontDescriptor.FontFile2 = (
            pdf.make_stream(program)
        )
        monkeypatch.setattr(font_mapping, "TTFont", forbidden)
        with pytest.raises(FontTextBindingError):
            decode_page_text_runs(pdf, 0)


def test_invalid_font_evidence_is_cached_without_poisoning_valid_alias_neighbors(
    monkeypatch,
):
    from src.education.pdf_checks import font_mapping

    calls = []
    original = font_mapping._bound_sfnt

    def bound(data):
        calls.append(1)
        return original(data)

    monkeypatch.setattr(font_mapping, "_bound_sfnt", bound)
    with truetype_pdf() as pdf:
        descriptor = pdf.pages[0].Resources.Font.F1.DescendantFonts[0].FontDescriptor
        descriptor.FontFile2 = pdf.make_stream(b"invalid")
        from src.education.remediation.pdf_text_inventory import (
            inspect_pdf_text_inventory,
        )

        inventory = inspect_pdf_text_inventory(snapshot(pdf))
    assert not inventory.complete and len(calls) == 1


def test_cff_defined_character_collection_remains_supported():
    with truetype_pdf() as pdf:
        descendant = pdf.pages[0].Resources.Font.F1.DescendantFonts[0]
        descendant.Subtype = pikepdf.Name.CIDFontType0
        descendant.CIDSystemInfo = pikepdf.Dictionary(
            Registry="Adobe", Ordering="Japan1", Supplement=0
        )
        del descendant.FontDescriptor.FontFile2
        pdf.pages[0].Contents = pdf.make_stream(b"BT /F1 12 Tf <0022> Tj ET")
        assert decode_page_text_runs(pdf, 0)[0].text == "A"


@pytest.mark.parametrize("limit", [131072, 2 * 1024 * 1024])
@pytest.mark.parametrize("filter_key", ["Filter", "F"])
def test_compressed_font_evidence_is_bounded_before_pdfminer_expansion(
    limit, filter_key, monkeypatch
):
    import zlib
    from pdfminer.pdftypes import PDFStream
    from src.education.pdf_checks.font_mapping import _bounded_stream_data

    delegated = []

    def forbidden(_stream):
        delegated.append(1)
        raise AssertionError("unbounded pdfminer stream decode")

    monkeypatch.setattr(PDFStream, "get_data", forbidden)
    raw = zlib.compress(b"x" * (limit + 1))
    stream = PDFStream({filter_key: LIT("FlateDecode")}, raw)
    with pytest.raises(FontMappingUnavailable):
        _bounded_stream_data(stream, limit)
    assert not delegated and stream.rawdata == raw and stream.data is None


@pytest.mark.parametrize(
    "attributes",
    [{"Filter": LIT("FlateDecode")}, {"F": LIT("Fl"), "DP": {}}],
)
def test_bounded_font_evidence_decodes_without_mutating_stream_or_cache(attributes):
    import zlib
    from pdfminer.pdftypes import PDFStream
    from src.education.pdf_checks.font_mapping import _bounded_stream_data

    raw = zlib.compress(b"\0\0\0\2\0\1")
    stream = PDFStream(attributes, raw)
    assert _bounded_stream_data(stream, 131072) == b"\0\0\0\2\0\1"
    assert stream.rawdata == raw and stream.data is None
    for bad in (raw[:-1], raw + b"trailing"):
        with pytest.raises(FontMappingUnavailable):
            _bounded_stream_data(PDFStream({"Filter": LIT("FlateDecode")}, bad), 131072)


def test_resolver_bounds_addressing_stream_without_caller_preflight(monkeypatch):
    import zlib
    from pdfminer.pdftypes import PDFStream
    from pdf_font_fixtures import tiny_truetype
    from src.education.pdf_checks.font_mapping import TrueTypeUnicodeResolver

    delegated = []

    def forbidden(_stream):
        delegated.append(1)
        raise AssertionError("unbounded pdfminer stream decode")

    monkeypatch.setattr(PDFStream, "get_data", forbidden)
    resolver = TrueTypeUnicodeResolver(
        {
            "DescendantFonts": [
                {
                    "Subtype": LIT("CIDFontType2"),
                    "FontDescriptor": {"FontFile2": PDFStream({}, tiny_truetype())},
                    "CIDToGIDMap": PDFStream(
                        {"Filter": LIT("FlateDecode")},
                        zlib.compress(b"\0" * 131074),
                    ),
                }
            ]
        }
    )
    with pytest.raises(FontMappingUnavailable):
        resolver.resolve(1)
    assert not delegated


def test_bounded_font_filter_parameters_cannot_silently_drop_a_filter():
    import zlib
    from pdfminer.pdftypes import PDFStream
    from src.education.pdf_checks.font_mapping import _bounded_stream_data

    for parameters in ([], [{}, {}], {"Predictor": 12}):
        stream = PDFStream(
            {"F": LIT("FlateDecode"), "DP": parameters}, zlib.compress(b"AB")
        )
        with pytest.raises(FontMappingUnavailable):
            _bounded_stream_data(stream, 10)
