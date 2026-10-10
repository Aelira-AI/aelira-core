"""Synthetic OCR derivatives: bounded flattening, Unicode and visual fidelity."""

import hashlib
import io
from dataclasses import replace
from decimal import Decimal

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.remediation import pdf_ocr_form as module
from src.education.remediation.pdf_ocr_form import (
    OCRFormFlatteningError,
    apply_ocr_form_flattening,
    plan_ocr_form_flattening,
)

pytestmark = pytest.mark.unit


def _bytes(pdf):
    output = io.BytesIO()
    pdf.save(output, deterministic_id=True, fix_metadata_version=False)
    return output.getvalue()


def _font(pdf, *, cid=False, mapped=True):
    if not cid:
        return pdf.make_indirect(
            Dictionary(
                Type=Name.Font,
                Subtype=Name.Type1,
                BaseFont=Name.Helvetica,
                Encoding=Name.WinAnsiEncoding,
            )
        )
    descriptor = pdf.make_indirect(
        Dictionary(
            Type=Name.FontDescriptor,
            FontName=Name.TestOCR,
            Flags=4,
            FontBBox=Array([0, -200, 1000, 1000]),
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
        )
    )
    descendant = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.CIDFontType2,
            BaseFont=Name.TestOCR,
            CIDSystemInfo=Dictionary(
                Registry="Adobe", Ordering="Identity", Supplement=0
            ),
            CIDToGIDMap=Name.Identity,
            DW=600,
            FontDescriptor=descriptor,
        )
    )
    font = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type0,
            BaseFont=Name.TestOCR,
            Encoding=Name("/Identity-H"),
            DescendantFonts=Array([descendant]),
        )
    )
    if mapped:
        font.ToUnicode = pdf.make_stream(
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
            b"/CMapName /Fixture-UCS def /CMapType 2 def "
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"2 beginbfchar <0037> <0054> <0038> <00660069> endbfchar "
            b"endcmap CMapName currentdict /CMap defineresource pop end end"
        )
    return font


def _pdf(*, cid=False, mapped=True, collision=False):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    image = pdf.make_stream(bytes([40, 120, 200] * 4))
    image.Type, image.Subtype = Name.XObject, Name.Image
    image.Width, image.Height = 2, 2
    image.ColorSpace, image.BitsPerComponent = Name.DeviceRGB, 8
    text = b"<00370038>" if cid else b"(Reviewed OCR text)"
    form = pdf.make_stream(
        b"1 J 1 w q 1 0 0 1 0 0 cm BT /F1 12 Tf 3 Tr 40 220 Td " + text + b" Tj ET Q"
    )
    form.Type, form.Subtype, form.FormType = Name.XObject, Name.Form, 1
    form.BBox = Array([0, 0, 400, 400])
    form.Resources = Dictionary(Font=Dictionary(F1=_font(pdf, cid=cid, mapped=mapped)))
    page.Resources = Dictionary(XObject=Dictionary(Im1=image, OCR=form))
    if collision:
        page.Resources.Font = Dictionary(
            F1=_font(pdf),
            AeliraOCR0F0=_font(pdf),
            AeliraOCR0F0_1=_font(pdf),
        )
    page.Contents = pdf.make_stream(b"q 400 0 0 400 0 0 cm /Im1 Do Q q /OCR Do Q")
    return pdf, page, form


def _view(data):
    with fitz.open(stream=data, filetype="pdf") as pdf:
        page = pdf[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        chars = [
            (char["c"], tuple(char["bbox"]))
            for block in page.get_text("rawdict")["blocks"]
            if block["type"] == 0
            for line in block["lines"]
            for span in line["spans"]
            for char in span["chars"]
        ]
        return (
            page.get_text(),
            chars,
            (pix.width, pix.height, hashlib.sha256(pix.samples).hexdigest()),
        )


@pytest.mark.parametrize("cid", [False, True])
def test_plan_is_read_only_and_apply_preserves_unicode_geometry_and_render(
    cid, tmp_path
):
    pdf, page, form = _pdf(cid=cid)
    source = tmp_path / "original.pdf"
    pdf.save(source)
    original_file = source.read_bytes()
    before = _bytes(pdf)
    plan = plan_ocr_form_flattening(pdf)
    assert _bytes(pdf) == before
    assert plan.pages[0].glyph_count == (2 if cid else len("Reviewed OCR text"))
    assert plan.pages[0].text_sha256
    result = apply_ocr_form_flattening(pdf, plan)
    after = _bytes(pdf)
    assert _view(before) == _view(after)
    assert source.read_bytes() == original_file
    assert result.pages_flattened == (0,)
    assert result.glyph_count == plan.pages[0].glyph_count
    assert "/OCR" not in page.Resources.XObject
    assert not any(
        value.get("/Subtype") == Name.Form
        for _, value in page.Resources.XObject.items()
    )
    assert b" re\nW\nn\n" in page.Contents.read_bytes()
    assert "/F1" in form.Resources.Font  # Original Form resources were not edited.


def test_colliding_font_resources_are_preserved_and_remapped_exactly():
    pdf, page, form = _pdf(cid=True, collision=True)
    originals = {name: tuple(font.objgen) for name, font in page.Resources.Font.items()}
    form_font_id = tuple(form.Resources.Font.F1.objgen)
    plan = plan_ocr_form_flattening(pdf)
    assert plan.pages[0].font_renames == (("/F1", "/AeliraOCR0F0_2"),)
    apply_ocr_form_flattening(pdf, plan)
    for name, identity in originals.items():
        assert tuple(page.Resources.Font[name].objgen) == identity
    assert tuple(page.Resources.Font.AeliraOCR0F0_2.objgen) == form_font_id
    assert b"/AeliraOCR0F0_2 12 Tf" in page.Contents.read_bytes()
    assert _view(_bytes(pdf))[0].strip() == "Tfi"


@pytest.mark.parametrize("rotation", [0, 90])
def test_form_matrix_page_transform_and_bbox_clip_are_preserved(rotation):
    pdf, page, form = _pdf()
    page.Rotate = rotation
    form.BBox = Array([-15, 30, 380, 360])
    form.Matrix = Array([0.8, 0.1, -0.1, 0.8, 15, 20])
    page.Contents = pdf.make_stream(
        b"q 400 0 0 400 0 0 cm /Im1 Do Q "
        b"q 1 0 0 1 5 7 cm /OCR Do Q "
        b"q 10 0 0 10 360 360 cm /Im1 Do Q"
    )
    before = _view(_bytes(pdf))
    plan = plan_ocr_form_flattening(pdf)
    apply_ocr_form_flattening(pdf, plan)
    after = _view(_bytes(pdf))
    assert before[0] == after[0]
    assert before[2] == after[2]
    assert [text for text, _ in before[1]] == [text for text, _ in after[1]]
    for (_, first), (_, second) in zip(before[1], after[1], strict=True):
        assert first == pytest.approx(second, abs=0.0001)
    assert b"-15 30 395 330 re" in page.Contents.read_bytes()


@pytest.mark.parametrize(
    "stream,code",
    [
        (b"BT /F1 12 Tf 0 Tr (visible) Tj 3 Tr ET", "visible_text"),
        (b"BT /F1 12 Tf 3 Tr (ok) Tj 0 Tr ET", "visible_text"),
        (b"BT /F1 12 Tf (no explicit mode) Tj ET", "text_state"),
        (b"BT 3 Tr (no font) Tj ET", "text_state"),
        (b"BT /F1 12 Tf 3 Tr BT (nested) Tj ET ET", "grammar"),
        (b"q BT /F1 12 Tf 3 Tr (unbalanced) Tj ET", "grammar"),
        (b"Q BT /F1 12 Tf 3 Tr (underflow) Tj ET", "graphics_balance"),
        (b"BT /F1 12 Tf 3 Tr /ActualText BMC (marked) Tj EMC ET", "grammar"),
        (b"BT /Missing 12 Tf 3 Tr (unknown font) Tj ET", "font_reference"),
        (b"BT /F1 12 Tf 3 Tr 10 20 Tj ET", "operands"),
        (b"BT /F1 12 Tf 3.5 Tr (mode) Tj ET", "visible_text"),
        (b"BT /F1 12 Tf 3 Tr [true] TJ ET", "operands"),
        (b"BT /F1 12 Tf 3 Tr 0 0 0 0 1 1 Tm (singular) Tj ET", "matrix"),
        (b"0 0 10 10 re W n BT /F1 12 Tf 3 Tr (clip) Tj ET", "grammar"),
        (b"BT /F1 12 Tf 3 Tr (ok) Tj ET /Other Do", "grammar"),
    ],
)
def test_unsupported_form_grammar_refuses_without_mutation(stream, code):
    pdf, _, form = _pdf()
    form.write(stream)
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_" + code):
        plan_ocr_form_flattening(pdf)
    assert _bytes(pdf) == before


@pytest.mark.parametrize(
    "page_stream,code",
    [
        (b"/Im1 Do /OCR Do /OCR Do", "form_reused"),
        (b"/Im1 Do", "form_reused"),
        (b"/OCR Do", "image_required"),
        (b"/Im1 Do /Missing Do /OCR Do", "xobject_reference"),
        (b"/Im1 Do BT ET /OCR Do", "page_grammar"),
        (b"/Im1 Do 0 0 100 100 re W n /OCR Do", "page_grammar"),
        (b"/Im1 Do /OCR Do Q", "graphics_balance"),
        (b"/Im1 Do 1 2 cm /OCR Do", "operands"),
    ],
)
def test_page_draw_contract_is_strict(page_stream, code):
    pdf, page, _ = _pdf()
    page.Contents = pdf.make_stream(page_stream)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_" + code):
        plan_ocr_form_flattening(pdf)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda pdf, page, form: setattr(form, "BBox", Array([0, 0, 0, 100])), "bbox"),
        (
            lambda pdf, page, form: setattr(form, "Matrix", Array([0, 0, 0, 0, 0, 0])),
            "matrix",
        ),
        (lambda pdf, page, form: setattr(form, "StructParents", 0), "semantics"),
        (
            lambda pdf, page, form: setattr(
                form, "Group", Dictionary(S=Name.Transparency)
            ),
            "semantics",
        ),
        (
            lambda pdf, page, form: setattr(
                form.Resources, "XObject", Dictionary(Nested=form)
            ),
            "resources",
        ),
        (
            lambda pdf, page, form: setattr(page.Resources.XObject, "Alias", form),
            "form_count",
        ),
        (lambda pdf, page, form: pdf.pages.append(page), "form_reused"),
        (lambda pdf, page, form: setattr(page, "Annots", Array([])), "semantics"),
        (
            lambda pdf, page, form: setattr(pdf.Root, "StructTreeRoot", Dictionary()),
            "semantics",
        ),
        (
            lambda pdf, page, form: setattr(page.Resources, "ExtGState", Dictionary()),
            "resources",
        ),
    ],
)
def test_semantics_nesting_aliases_reuse_and_boxes_are_refused(change, code):
    pdf, page, form = _pdf()
    change(pdf, page, form)
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_" + code):
        plan_ocr_form_flattening(pdf)
    assert _bytes(pdf) == before


@pytest.mark.parametrize("kind", ["missing", "partial", "odd", "replacement"])
def test_every_used_composite_glyph_needs_verified_unicode(kind):
    pdf, _, form = _pdf(cid=True, mapped=kind != "missing")
    if kind == "partial":
        form.write(b"BT /F1 12 Tf 3 Tr <0039> Tj ET")
    elif kind == "odd":
        form.write(b"BT /F1 12 Tf 3 Tr <003700> Tj ET")
    elif kind == "replacement":
        cmap = form.Resources.Font.F1.ToUnicode
        cmap.write(cmap.read_bytes().replace(b"<0054>", b"<FFFD>"))
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_font_unicode"):
        plan_ocr_form_flattening(pdf)


@pytest.mark.parametrize(
    "limit,value,code",
    [
        ("MAX_STREAM_BYTES", 10, "stream_limit"),
        ("MAX_TOTAL_DECODED_BYTES", 10, "decoded_limit"),
        ("MAX_OPERATIONS", 3, "operation_limit"),
        ("MAX_GLYPHS", 2, "glyph_limit"),
        ("MAX_IMAGE_PIXELS", 3, "image_limit"),
        ("MAX_PDF_BYTES", 30, "byte_limit"),
    ],
)
def test_work_budgets_are_enforced_before_apply(monkeypatch, limit, value, code):
    pdf, _, _ = _pdf()
    before = _bytes(pdf)
    monkeypatch.setattr(module, limit, value)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_" + code):
        plan_ocr_form_flattening(pdf)
    assert _bytes(pdf) == before


def test_compressed_content_bomb_is_bounded(monkeypatch):
    import zlib

    pdf, _, form = _pdf()
    form.write(zlib.compress(b" " * 10000), filter=Name.FlateDecode)
    monkeypatch.setattr(module, "MAX_STREAM_BYTES", 1000)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_stream_limit"):
        plan_ocr_form_flattening(pdf)


@pytest.mark.parametrize("change", ["source", "forged"])
def test_apply_revalidates_source_and_plan_before_any_write(change):
    pdf, page, form = _pdf()
    plan = plan_ocr_form_flattening(pdf)
    if change == "source":
        form.write(form.read_bytes().replace(b"Reviewed", b"Changed!"))
    else:
        plan = replace(plan, pages=(replace(plan.pages[0], content=b""),))
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_plan_changed"):
        apply_ocr_form_flattening(pdf, plan)
    assert _bytes(pdf) == before
    assert "/OCR" in page.Resources.XObject


def test_pdf_decoded_after_flattening_has_no_unavailable_scope():
    from pdfminer.pdfpage import PDFPage
    from src.education.pdf_checks.marked_content import (
        _FontResourceManager,
        _MarkedTextDevice,
        _PageInterpreter,
    )

    pdf, _, _ = _pdf(cid=True)
    apply_ocr_form_flattening(pdf, plan_ocr_form_flattening(pdf))
    errors = []
    manager = _FontResourceManager(errors.append)
    device = _MarkedTextDevice(manager, errors.append)
    with io.BytesIO(_bytes(pdf)) as source:
        _PageInterpreter(manager, device).process_page(next(PDFPage.get_pages(source)))
    assert errors == []
    assert device.character_count == 2


def test_decimal_form_matrix_and_clip_keep_source_precision():
    pdf, page, form = _pdf()
    form.Matrix = Array([Decimal("0.123456789"), 0, 0, 1, 0, 0])
    form.BBox = Array([Decimal("0.000123456"), 0, Decimal("399.123456789"), 400])
    plan = plan_ocr_form_flattening(pdf)
    apply_ocr_form_flattening(pdf, plan)
    ops = list(pikepdf.parse_content_stream(page))
    assert any(
        str(op.operator) == "cm" and op.operands[0] == Decimal("0.123456789")
        for op in ops
    )
    clip = next(op for op in ops if str(op.operator) == "re")
    assert clip.operands[0] == Decimal("0.000123456")
    assert clip.operands[2] == Decimal("399.123333333")


def test_later_page_refusal_does_not_partially_flatten_first_page():
    pdf, page, _ = _pdf()
    pdf.add_blank_page()
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError):
        plan_ocr_form_flattening(pdf)
    assert _bytes(pdf) == before
    assert "/OCR" in page.Resources.XObject


def test_unicode_expansion_is_bounded():
    pdf, _, form = _pdf(cid=True)
    cmap = form.Resources.Font.F1.ToUnicode
    cmap.write(cmap.read_bytes().replace(b"<0054>", b"<" + b"0054" * 17 + b">"))
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_font_unicode"):
        plan_ocr_form_flattening(pdf)


def test_cumulative_transform_overflow_is_refused():
    pdf, page, _ = _pdf()
    page.Contents = pdf.make_stream(
        b"/Im1 Do 1000000 0 0 1000000 0 0 cm 1000000 0 0 1000000 0 0 cm /OCR Do"
    )
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_numeric_bounds"):
        plan_ocr_form_flattening(pdf)


def test_separate_document_with_identical_bytes_can_apply_bound_plan():
    pdf, _, _ = _pdf()
    source = _bytes(pdf)
    with pikepdf.open(io.BytesIO(source)) as first_copy:
        plan = plan_ocr_form_flattening(first_copy)
    with pikepdf.open(io.BytesIO(source)) as private_copy:
        apply_ocr_form_flattening(private_copy, plan)
        assert _view(_bytes(private_copy)) == _view(source)
    assert _bytes(pdf) == source


def test_unrepresentable_clip_precision_is_refused():
    pdf, _, form = _pdf()
    form.BBox = pikepdf.Object.parse(b"[0.000000001 0 400 400]")
    assert form.BBox[0] == Decimal("0.000000001")
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_numeric_precision"):
        plan_ocr_form_flattening(pdf)


@pytest.mark.parametrize(
    "mapping",
    [
        b"1 beginbfrange <00000000> <7fffffff> <0041> endbfrange",
        b"1 beginbfrange <0037> <0038> [<0054>] endbfrange",
        b"1 beginbfchar <0037> <005> endbfchar",
        b"2 beginbfchar <0037> <0054> endbfchar",
        b"1 beginbfchar <0037> <0054> endbfrange",
    ],
)
def test_invalid_or_unbounded_cmap_ranges_are_refused(mapping):
    pdf, _, form = _pdf(cid=True)
    form.Resources.Font.F1.ToUnicode.write(b"begincmap " + mapping + b" endcmap")
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_font_unicode"):
        plan_ocr_form_flattening(pdf)


def test_cmap_expansion_budget_is_aggregate(monkeypatch):
    pdf, _, form = _pdf(cid=True)
    form.Resources.Font.F1.ToUnicode.write(
        b"begincmap 1 beginbfrange <0000> <ffff> <0000> endbfrange endcmap"
    )
    monkeypatch.setattr(module, "MAX_CMAP_ENTRIES", 100)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_cmap_limit"):
        plan_ocr_form_flattening(pdf)


@pytest.mark.parametrize(
    "mapping",
    [
        b"1 beginbfrange <0037> <0038> <0054> endbfrange",
        b"1 beginbfrange <0037> <0038> [<0054> <00660069>] endbfrange",
    ],
)
def test_bounded_scalar_and_array_cmap_ranges_are_supported(mapping):
    pdf, _, form = _pdf(cid=True)
    form.Resources.Font.F1.ToUnicode.write(b"begincmap " + mapping + b" endcmap")
    plan = plan_ocr_form_flattening(pdf)
    assert plan.pages[0].glyph_count == 2
    apply_ocr_form_flattening(pdf, plan)


def test_reusable_font_preflight_is_read_only_and_shares_expansion_budget(monkeypatch):
    pdf, _, form = _pdf(cid=True)
    second = Dictionary(F1=_font(pdf, cid=True))
    before = _bytes(pdf)
    module.require_bounded_font_resources([form.Resources.Font, second])
    assert _bytes(pdf) == before
    monkeypatch.setattr(module, "MAX_CMAP_ENTRIES", 3)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_cmap_limit"):
        module.require_bounded_font_resources([form.Resources.Font, second])


def _add_native_page(pdf):
    page = pdf.add_blank_page(page_size=(400, 400))
    page.Resources = Dictionary(Font=Dictionary(F1=_font(pdf)))
    page.Contents = pdf.make_stream(
        b"BT /F1 12 Tf 40 220 Td (Untouched native text) Tj ET"
    )
    return page


def test_selected_ocr_page_preserves_native_page_and_binds_whole_document():
    pdf, _, _ = _pdf(cid=True)
    native = _add_native_page(pdf)
    native_content = native.Contents.read_bytes()
    native_resources = str(native.Resources)
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError):
        plan_ocr_form_flattening(pdf)  # Default still validates every page.
    plan = plan_ocr_form_flattening(pdf, page_indices=(0,))
    assert len(plan.pages) == 1 and plan.pages[0].page_index == 0
    assert _bytes(pdf) == before
    result = apply_ocr_form_flattening(pdf, plan)
    assert result.pages_flattened == (0,)
    assert native.Contents.read_bytes() == native_content
    assert str(native.Resources) == native_resources
    with (
        fitz.open(stream=before, filetype="pdf") as first,
        fitz.open(stream=_bytes(pdf), filetype="pdf") as second,
    ):
        for old, new in zip(first, second, strict=True):
            assert old.get_text("rawdict") == new.get_text("rawdict")
            assert old.get_pixmap().samples == new.get_pixmap().samples


def test_selection_can_follow_native_pages():
    pdf, _, _ = _pdf()
    _add_native_page(pdf)
    pdf.pages.reverse()
    plan = plan_ocr_form_flattening(pdf, page_indices=(1,))
    assert apply_ocr_form_flattening(pdf, plan).pages_flattened == (1,)
    assert "/OCR" not in pdf.pages[1].Resources.XObject
    assert b"Untouched native text" in pdf.pages[0].Contents.read_bytes()


@pytest.mark.parametrize("indices", [(), (0, 0), (True,), (-1,), (1,), (0.0,), ("0",)])
def test_invalid_page_selection_is_refused_without_mutation(indices):
    pdf, _, _ = _pdf()
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_page_indices"):
        plan_ocr_form_flattening(pdf, page_indices=indices)
    assert _bytes(pdf) == before


@pytest.mark.parametrize(
    "sharing", ["direct", "nested", "appearance", "contents", "resources"]
)
def test_selected_form_cannot_be_shared_with_a_nonselected_page(sharing):
    pdf, page, form = _pdf()
    native = _add_native_page(pdf)
    if sharing == "direct":
        native.Resources.XObject = Dictionary(Alias=form)
    elif sharing == "nested":
        outer = pdf.make_stream(b"/Alias Do")
        outer.Subtype = Name.Form
        outer.BBox = Array([0, 0, 400, 400])
        outer.Resources = Dictionary(XObject=Dictionary(Alias=form))
        native.Resources.XObject = Dictionary(Outer=outer)
    elif sharing == "appearance":
        native.Annots = Array(
            [Dictionary(Type=Name.Annot, Subtype=Name.Stamp, AP=Dictionary(N=form))]
        )
    elif sharing == "contents":
        native.Contents = form
    else:
        native.Resources = page.Resources
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_form_reused"):
        plan_ocr_form_flattening(pdf, page_indices=(0,))
    assert _bytes(pdf) == before


def test_change_on_nonselected_page_invalidates_plan():
    pdf, _, _ = _pdf()
    native = _add_native_page(pdf)
    plan = plan_ocr_form_flattening(pdf, page_indices=(0,))
    native.Contents = pdf.make_stream(b"BT /F1 12 Tf (Changed native text) Tj ET")
    before = _bytes(pdf)
    with pytest.raises(OCRFormFlatteningError, match="ocr_form_plan_changed"):
        apply_ocr_form_flattening(pdf, plan)
    assert _bytes(pdf) == before
