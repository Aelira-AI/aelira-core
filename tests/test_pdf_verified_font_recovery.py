"""Reviewed synthetic recovery through strict decoding and semantic tagging."""

import hashlib
import io
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import require_complete_pdf_scan
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.remediation import pdf_verified_font_recovery as module
from src.education.remediation.content_tagger_v2 import ContentTaggerV2
from src.education.remediation.pdf_font_text import decode_page_text_runs
from src.education.remediation.pdf_structure import PDFStructureTree
from src.education.remediation.pdf_verified_font_recovery import (
    FontRecoveryManifest,
    ReviewedFontMap,
    ReviewedTextRun,
    VerifiedFontRecoveryError,
    inspect_font_recovery_source,
    recover_verified_font_maps,
)

pytestmark = pytest.mark.unit


def _bytes(pdf):
    out = io.BytesIO()
    pdf.save(
        out, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none
    )
    return out.getvalue()


def _source(
    texts=(
        "Study invitation",
        "Eligible students can participate.",
        "Contact the study team.",
    ),
    *,
    image=False,
):
    font = fitz.Font("helv")
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=400)
        page.insert_font(fontname="Recovery", fontbuffer=font.buffer)
        for index, text in enumerate(texts):
            page.insert_text(
                (30, 50 + index * 45),
                text,
                fontname="Recovery",
                fontsize=16 if index == 0 else 12,
            )
        if image:
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), 0)
            pixmap.clear_with(90)
            page.insert_image(fitz.Rect(300, 300, 340, 340), pixmap=pixmap)
        original = doc.tobytes()
    with pikepdf.open(io.BytesIO(original)) as pdf:
        for _, value in pdf.pages[0].Resources.Font.items():
            del value.ToUnicode
        source = _bytes(pdf)
    inventory = inspect_font_recovery_source(source)
    known = {font.has_glyph(ord(char)): char for text in texts for char in text}
    # Synthetic fixture authors know the text they drew. No source inference.
    reviewed_fonts = tuple(
        ReviewedFontMap(
            item.objgen,
            item.fingerprint,
            tuple((cid, known[cid]) for cid in item.used_cids),
        )
        for item in inventory.fonts
    )
    reviewed_runs = tuple(
        ReviewedTextRun(run.page_index, run.start, run.end, text)
        for run, text in zip(inventory.runs, texts, strict=True)
    )
    manifest = FontRecoveryManifest(
        inventory.source_sha256,
        "synthetic fixture author",
        "fixture source strings",
        reviewed_fonts,
        reviewed_runs,
    )
    return source, manifest, inventory


def _pixels(data):
    with fitz.open(stream=data, filetype="pdf") as doc:
        return tuple(
            (
                page.rect,
                page.rotation,
                page.get_pixmap(matrix=fitz.Matrix(2, 2)).samples,
            )
            for page in doc
        )


def test_source_inventory_has_no_guessed_unicode_and_repair_preserves_artwork():
    source, manifest, inventory = _source(image=True)
    original = source
    assert inventory.page_count == 1
    assert inventory.fonts[0].used_cids
    assert all(
        not hasattr(glyph, "text") for run in inventory.runs for glyph in run.glyphs
    )
    recovered = recover_verified_font_maps(source, manifest)
    assert source == original
    assert recovered.source_sha256 == hashlib.sha256(source).hexdigest()
    assert recovered.output_sha256 == hashlib.sha256(recovered.pdf_bytes).hexdigest()
    assert recovered.glyph_count == sum(len(run.text) for run in manifest.runs)
    assert _pixels(source) == _pixels(recovered.pdf_bytes)
    with pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf:
        assert [run.text for run in decode_page_text_runs(pdf, 0)] == [
            run.text for run in manifest.runs
        ]
        assert "/StructTreeRoot" not in pdf.Root
        font = next(value for _, value in pdf.pages[0].Resources.Font.items())
        assert (
            module._fingerprint(font, ignore_unicode=True)
            == inventory.fonts[0].fingerprint
        )


def test_repaired_pdf_tags_heading_paragraphs_and_verifies_saved_reading_order(
    tmp_path,
):
    source, manifest, _ = _source()
    recovered = recover_verified_font_maps(source, manifest)
    saved_path = tmp_path / "tagged.pdf"
    with (
        pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf,
        fitz.open(stream=recovered.pdf_bytes, filetype="pdf") as doc,
    ):
        structure = PDFStructureTree(pdf)
        structure.set_document_language("en")
        structure.set_document_title("Study invitation")
        structure.add_heading(1, 1, "Study invitation")
        tagger = ContentTaggerV2(pdf, doc, order_generated_structure=True)
        assert tagger.source_text_bindings(0, ["Study invitation"], set()) == [
            manifest.runs[0].start
        ]
        tagger.tag_all_pages()
        pdf.save(saved_path)
    assert _pixels(source) == _pixels(saved_path.read_bytes())
    with require_complete_pdf_scan(True):
        checked = ReadingOrderVerifier().check(str(saved_path))
    assert checked.has_structure_tree
    assert checked.pages_analyzed == 1
    assert checked.issues == []
    with pikepdf.open(saved_path) as pdf:
        children = list(pdf.Root.StructTreeRoot.K[0].K)
        assert [str(child.S) for child in children] == ["/H1", "/P", "/P"]
        assert [str(child.ActualText) for child in children] == [
            run.text for run in manifest.runs
        ]
        assert [run.text for run in decode_page_text_runs(pdf, 0)] == [
            run.text for run in manifest.runs
        ]


def test_recovery_runs_through_full_remediator_and_saved_output_verification(tmp_path):
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    source, manifest, _ = _source()
    original = tmp_path / "original.pdf"
    original.write_bytes(source)
    recovered = recover_verified_font_maps(source, manifest)
    candidate = tmp_path / "reviewed.pdf"
    candidate.write_bytes(recovered.pdf_bytes)
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(candidate)
    )
    result = PdfRemediator(
        str(candidate),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            allow_legacy_nested_ai=False,
            output_directory=str(tmp_path / "output"),
        ),
    ).remediate()
    try:
        assert result.output_file
        assert result.verification_passed, result.verification_result
        assert original.read_bytes() == source
        assert candidate.read_bytes() == recovered.pdf_bytes
        saved = Path(result.output_file).read_bytes()
        assert _pixels(source) == _pixels(saved)
        with require_complete_pdf_scan(True):
            reading = ReadingOrderVerifier().check(result.output_file)
        assert reading.has_structure_tree and not reading.issues
        with pikepdf.open(io.BytesIO(saved)) as pdf:
            assert [run.text for run in decode_page_text_runs(pdf, 0)] == [
                run.text for run in manifest.runs
            ]
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("wrong", ["cid", "transcript"])
def test_wrong_reused_cid_or_run_transcript_refuses(wrong):
    source, manifest, _ = _source(("Repeated R", "Repeated R"))
    if wrong == "cid":
        font = manifest.fonts[0]
        mapping = tuple(
            (cid, "X" if text == "R" else text) for cid, text in font.mappings
        )
        manifest = replace(manifest, fonts=(replace(font, mappings=mapping),))
    else:
        manifest = replace(
            manifest,
            runs=(manifest.runs[0], replace(manifest.runs[1], text="Repeated X")),
        )
    with pytest.raises(VerifiedFontRecoveryError, match="transcript_mismatch"):
        recover_verified_font_maps(source, manifest)


@pytest.mark.parametrize("missing", ["cid", "run", "font"])
def test_omitted_review_coverage_refuses(missing):
    source, manifest, _ = _source()
    if missing == "cid":
        manifest = replace(
            manifest,
            fonts=(
                replace(manifest.fonts[0], mappings=manifest.fonts[0].mappings[:-1]),
            ),
        )
    elif missing == "run":
        manifest = replace(manifest, runs=manifest.runs[:-1])
    else:
        manifest = replace(manifest, fonts=())
    with pytest.raises(VerifiedFontRecoveryError, match="coverage"):
        recover_verified_font_maps(source, manifest)


@pytest.mark.parametrize("changed", ["source", "content", "image", "fingerprint"])
def test_changed_source_or_font_fingerprint_refuses(changed):
    source, manifest, _ = _source(image=True)
    if changed == "fingerprint":
        manifest = replace(
            manifest, fonts=(replace(manifest.fonts[0], fingerprint="0" * 64),)
        )
    elif changed == "source":
        source += b"\n% changed source\n"
    else:
        with pikepdf.open(io.BytesIO(source)) as pdf:
            if changed == "content":
                pdf.pages[0].Contents[0].write(
                    b"q Q\n" + pdf.pages[0].Contents[0].read_bytes()
                )
            else:
                image = next(
                    value for _, value in pdf.pages[0].Resources.XObject.items()
                )
                image.write(bytes([180] * 12))
            source = _bytes(pdf)
    with pytest.raises(VerifiedFontRecoveryError, match="changed"):
        recover_verified_font_maps(source, manifest)


def test_multicharacter_ligature_survives_strict_saved_decoding():
    source, manifest, _ = _source(("\ufb01",))
    font = manifest.fonts[0]
    manifest = replace(
        manifest,
        fonts=(replace(font, mappings=((font.mappings[0][0], "fi"),)),),
        runs=(replace(manifest.runs[0], text="fi"),),
    )
    recovered = recover_verified_font_maps(source, manifest)
    assert recovered.glyph_count == 1
    assert _pixels(source) == _pixels(recovered.pdf_bytes)
    with pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "fi"


@pytest.mark.parametrize(
    "scope", ["form", "signed", "xfa", "actualtext", "missing_program"]
)
def test_unsupported_or_ambiguous_source_scope_refuses(scope):
    source, _, _ = _source()
    with pikepdf.open(io.BytesIO(source)) as pdf:
        page = pdf.pages[0]
        if scope == "form":
            form = pdf.make_stream(b"BT /Recovery 12 Tf <0035> Tj ET")
            form.Subtype, form.BBox, form.Resources = (
                Name.Form,
                Array([0, 0, 400, 400]),
                page.Resources,
            )
            page.Resources = Dictionary(XObject=Dictionary(Fm=form))
            page.Contents = pdf.make_stream(b"/Fm Do")
        elif scope in {"signed", "xfa"}:
            pdf.Root.AcroForm = Dictionary(Fields=Array([]))
            if scope == "xfa":
                pdf.Root.AcroForm.XFA = pdf.make_stream(b"xfa")
            else:
                pdf.Root.AcroForm.SigFlags = 3
        elif scope == "actualtext":
            page.Contents = pdf.make_stream(
                b"/Span << /ActualText (Trust me) >> BDC EMC"
            )
        else:
            font = next(value for _, value in page.Resources.Font.items())
            del font.DescendantFonts[0].FontDescriptor.FontFile3
        unsupported = _bytes(pdf)
    with pytest.raises(VerifiedFontRecoveryError):
        inspect_font_recovery_source(unsupported)


@pytest.mark.parametrize("text", ["", "\ufffd", "\ud800", "a" * 17, "\x00"])
def test_invalid_reviewed_unicode_refuses(text):
    source, manifest, _ = _source(("R",))
    font = manifest.fonts[0]
    manifest = replace(
        manifest, fonts=(replace(font, mappings=((font.mappings[0][0], text),)),)
    )
    with pytest.raises(VerifiedFontRecoveryError, match="unicode"):
        recover_verified_font_maps(source, manifest)


def test_injected_artwork_change_during_save_is_caught(monkeypatch):
    source, manifest, _ = _source(image=True)
    original_save = pikepdf.Pdf.save

    def corrupt_save(pdf, *args, **kwargs):
        image = next(value for _, value in pdf.pages[0].Resources.XObject.items())
        image.write(bytes([200] * 12))
        return original_save(pdf, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", corrupt_save)
    with pytest.raises(VerifiedFontRecoveryError, match="artwork_changed"):
        recover_verified_font_maps(source, manifest)


def test_bounds_refuse_before_decoding(monkeypatch):
    source, _, _ = _source()
    monkeypatch.setattr(module, "MAX_PDF_BYTES", 10)
    with pytest.raises(VerifiedFontRecoveryError, match="byte_limit"):
        inspect_font_recovery_source(source)


@pytest.mark.parametrize(
    "content",
    [
        b"BT /Recovery 12 Tf <01> Tj ET",
        b"BT /Recovery Tf <0035> Tj ET",
        b"BT /Recovery 12 Tf [<0035> /Wrong] TJ ET",
        b"BT /Recovery 12 Tf <0035> UnsupportedText ET",
        b"BT /Recovery 12 Tf <0035> Tj ET Q",
        b"BT /Recovery 12 Tf <0035> Tj ET BT",
    ],
)
def test_malformed_text_operations_refuse(content):
    source, _, _ = _source()
    with pikepdf.open(io.BytesIO(source)) as pdf:
        pdf.pages[0].Contents = pdf.make_stream(content)
        malformed = _bytes(pdf)
    with pytest.raises(VerifiedFontRecoveryError):
        inspect_font_recovery_source(malformed)


@pytest.mark.parametrize(
    "target,size", [("content", 9 * 1024 * 1024), ("font", 3 * 1024 * 1024)]
)
def test_compressed_stream_limits_precede_font_interpretation(
    monkeypatch, target, size
):
    source, _, _ = _source()
    with pikepdf.open(io.BytesIO(source)) as pdf:
        stream = pdf.make_stream(zlib.compress(b" " * size), Filter=Name.FlateDecode)
        if target == "content":
            pdf.pages[0].Contents = stream
        else:
            font = next(value for _, value in pdf.pages[0].Resources.Font.items())
            font.DescendantFonts[0].FontDescriptor.FontFile3 = stream
        oversized = _bytes(pdf)
    interpreter = Mock(side_effect=AssertionError("decoder must not run"))
    monkeypatch.setattr(module._InventoryInterpreter, "process_page", interpreter)
    with pytest.raises(VerifiedFontRecoveryError):
        inspect_font_recovery_source(oversized)
    interpreter.assert_not_called()


@pytest.mark.parametrize(
    "limit,value", [("MAX_OPERATIONS", 1), ("MAX_OBJECTS", 1), ("MAX_GLYPHS", 1)]
)
def test_bounded_object_operation_and_glyph_counts(monkeypatch, limit, value):
    source, _, _ = _source()
    monkeypatch.setattr(module, limit, value)
    with pytest.raises(VerifiedFontRecoveryError):
        inspect_font_recovery_source(source)


def test_saved_font_numbering_is_not_the_font_fingerprint():
    source, manifest, inventory = _source()
    recovered = recover_verified_font_maps(source, manifest)
    with pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf:
        with pikepdf.new() as reordered:
            reordered.add_blank_page()
            reordered.pages.append(pdf.pages[0])
            renumbered = _bytes(reordered)
    with pikepdf.open(io.BytesIO(renumbered)) as pdf:
        font = next(value for _, value in pdf.pages[1].Resources.Font.items())
        assert font.objgen != inventory.fonts[0].objgen
        assert (
            module._fingerprint(font, ignore_unicode=True)
            == inventory.fonts[0].fingerprint
        )


def test_vector_heavy_source_retains_independent_text_bound():
    source, _, _ = _source(("Reviewed title",))
    with pikepdf.open(io.BytesIO(source)) as pdf:
        # 30,000 ordinary path operations; no extra text or glyph complexity.
        artwork = pdf.make_stream(b"0 0 m 100 0 l 100 100 l h n\n" * 6000)
        pdf.pages[0].Contents = Array([artwork, *pdf.pages[0].Contents])
        source = _bytes(pdf)
    inventory = inspect_font_recovery_source(source)
    font = fitz.Font("helv")
    known = {font.has_glyph(ord(c)): c for c in "Reviewed title"}
    reviewed = inventory.fonts[0]
    run = inventory.runs[0]
    manifest = FontRecoveryManifest(
        inventory.source_sha256,
        "fixture author",
        "vector-heavy fixture",
        (
            ReviewedFontMap(
                reviewed.objgen,
                reviewed.fingerprint,
                tuple((cid, known[cid]) for cid in reviewed.used_cids),
            ),
        ),
        (ReviewedTextRun(0, run.start, run.end, "Reviewed title"),),
    )
    recovered = recover_verified_font_maps(source, manifest)
    assert _pixels(source) == _pixels(recovered.pdf_bytes)
    with pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf:
        assert decode_page_text_runs(pdf, 0)[0].text == "Reviewed title"


@pytest.mark.parametrize("budget", ["MAX_OPERATIONS", "MAX_TEXT_OPERATIONS"])
def test_both_recovery_and_strict_binding_enforce_operator_budgets(monkeypatch, budget):
    from src.education.remediation import pdf_font_text as binding

    source, manifest, _ = _source()
    recovered = recover_verified_font_maps(source, manifest)
    monkeypatch.setattr(module, budget, 1)
    with pytest.raises(VerifiedFontRecoveryError):
        inspect_font_recovery_source(source)
    monkeypatch.setattr(binding, budget, 1)
    with pikepdf.open(io.BytesIO(recovered.pdf_bytes)) as pdf:
        with pytest.raises(
            binding.FontTextBindingError, match="source_text_binding_limit"
        ):
            decode_page_text_runs(pdf, 0)
