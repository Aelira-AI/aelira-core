"""Inventory visits all independent pages and keeps replacement layers separate."""

import hashlib

import pytest
from pikepdf import Array, Dictionary, Name

from pdf_font_fixtures import snapshot, truetype_pdf
from src.education.remediation import pdf_text_inventory as module
from test_pdf_font_text import simple_pdf

pytestmark = pytest.mark.unit


def test_mixed_pages_continue_after_mapping_failure_and_are_source_bound():
    with (
        truetype_pdf(alias=True) as pdf,
        simple_pdf(b"BT /F1 12 Tf (Healthy) Tj ET") as healthy,
    ):
        pdf.pages.append(healthy.pages[0])
        source = snapshot(pdf)
    inventory = module.inspect_pdf_text_inventory(source)
    assert inventory.source_sha256 == hashlib.sha256(source).hexdigest()
    assert len(inventory.pages) == 2 and not inventory.pages[0].complete
    assert inventory.pages[1].complete
    assert inventory.runs[0].glyphs[0].unicode is None
    assert (
        "".join(glyph.unicode or "?" for glyph in inventory.runs[1].glyphs) == "Healthy"
    )
    assert inventory.summary()["unresolved_glyph_count"] == 1
    assert not inventory.complete and inventory.fidelity_status == "unassessed"
    assert "Healthy" not in repr(inventory.summary())


def test_shared_font_repeated_occurrences_and_independent_code_identities():
    with truetype_pdf() as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Resources = pdf.pages[0].Resources
        page.Contents = pdf.make_stream(b"BT /F1 12 Tf <00020001> Tj ET")
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert inventory.complete
    assert len(inventory.fonts) == 1 and len(inventory.fonts[0].resource_paths) == 2
    assert inventory.fonts[0].used_codes == (1, 2)
    assert [
        "".join(glyph.unicode for glyph in run.glyphs) for run in inventory.runs
    ] == ["AB", "BA"]


def test_actualtext_and_invisible_text_are_independent_evidence():
    with simple_pdf(
        b"/Span << /ActualText (Replacement) >> BDC BT /F1 12 Tf 3 Tr (Native) Tj ET EMC"
    ) as pdf:
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert inventory.complete
    assert "".join(glyph.unicode for glyph in inventory.runs[0].glyphs) == "Native"
    assert all(glyph.rendering_mode == 3 for glyph in inventory.runs[0].glyphs)
    assert inventory.replacements[0].text == "Replacement"
    assert (
        inventory.replacements[0].first_glyph,
        inventory.replacements[0].end_glyph,
    ) == (0, 6)


def test_repeated_forms_are_explicit_exclusions_without_recursion():
    with simple_pdf(b"/X Do /X Do") as pdf:
        form = pdf.make_stream(b"/X Do")
        form.Subtype = Name.Form
        form.BBox = Array([0, 0, 400, 400])
        resources = Dictionary(XObject=Dictionary(X=form))
        form.Resources = resources  # cycle must never be followed in v1
        pdf.pages[0].Resources = resources
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert not inventory.complete
    assert inventory.pages[0].form_invocations == 2
    assert "form_scope_unsupported" in inventory.exclusions


def test_unused_unsupported_font_is_not_a_used_glyph_failure():
    with simple_pdf(b"BT /F1 12 Tf (Healthy) Tj ET") as pdf:
        pdf.pages[0].Resources.Font.Bad = pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.TrueType, BaseFont=Name.Custom)
        )
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert inventory.complete
    assert inventory.summary()["unused_font_count"] == 1
    assert inventory.summary()["unresolved_glyph_count"] == 0


def test_document_glyph_budget_cannot_reset_on_each_page(monkeypatch):
    monkeypatch.setattr(module, "MAX_GLYPHS", 3)
    with truetype_pdf() as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Resources = pdf.pages[0].Resources
        page.Contents = pdf.pages[0].Contents
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert not inventory.complete
    assert "document_budget_exhausted" in inventory.exclusions
    assert len(inventory.pages) == 1


def test_document_operation_budget_and_source_limit(monkeypatch):
    monkeypatch.setattr(module, "MAX_OPERATIONS", 2)
    with truetype_pdf() as pdf:
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert (
        not inventory.complete and "document_budget_exhausted" in inventory.exclusions
    )
    assert module.inspect_pdf_text_inventory(b"not a pdf").exclusions == (
        "document_inspection_incomplete",
    )


def test_repeated_actualtext_charged_before_expansion_and_across_pages(monkeypatch):
    monkeypatch.setattr(module, "MAX_REPLACEMENT_BYTES", 12)
    with simple_pdf(b"/Span /P BDC EMC /Span /P BDC EMC") as pdf:
        pdf.pages[0].Resources.Properties = Dictionary(
            P=Dictionary(ActualText="12345678")
        )
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert (
        not inventory.complete and "document_budget_exhausted" in inventory.exclusions
    )
    with simple_pdf(b"/Span << /ActualText (12345678) >> BDC EMC") as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Resources = pdf.pages[0].Resources
        page.Contents = pdf.pages[0].Contents
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert (
        not inventory.complete and "document_budget_exhausted" in inventory.exclusions
    )
    assert len(inventory.pages) == 1


def test_graphics_state_font_change_is_explicitly_uninspected():
    with simple_pdf(b"BT /F1 12 Tf (A) Tj /GS gs (A) Tj ET") as pdf:
        font = pdf.make_indirect(
            Dictionary(
                Type=Name.Font,
                Subtype=Name.Type1,
                BaseFont=Name.Helvetica,
                Encoding=Dictionary(
                    BaseEncoding=Name.WinAnsiEncoding, Differences=Array([65, Name.B])
                ),
            )
        )
        pdf.pages[0].Resources.ExtGState = Dictionary(
            GS=Dictionary(Font=Array([font, 12]))
        )
        inventory = module.inspect_pdf_text_inventory(snapshot(pdf))
    assert not inventory.complete
    assert "graphics_font_scope_unsupported" in inventory.exclusions
