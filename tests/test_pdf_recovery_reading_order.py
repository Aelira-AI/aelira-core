"""Overprinted decoration cannot hide a distinct native text requirement."""

import io

import pikepdf
import pytest
from pikepdf import Dictionary, Name

from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.remediation.pdf_reviewed_semantics import (
    ReviewedOccurrence,
    ReviewedSemanticManifest,
    ReviewedSemanticNode,
    apply_reviewed_semantics,
    inspect_reviewed_semantic_source,
)

pytestmark = pytest.mark.unit


def _candidate(tmp_path, *, decoration=b"Body text", offset=0, reordered=False):
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Resources = Dictionary(
            Font=Dictionary(
                F1=pdf.make_indirect(
                    Dictionary(
                        Type=Name.Font,
                        Subtype=Name.Type1,
                        BaseFont=Name.Helvetica,
                        Encoding=Name.WinAnsiEncoding,
                    )
                )
            )
        )
        page.Contents = pdf.make_stream(
            b"BT /F1 18 Tf 30 350 Td (Study heading) Tj ET\n"
            b"BT /F1 12 Tf 30 300 Td (Body text) Tj ET\n"
            + f"BT /F1 12 Tf {30 + offset} 300 Td (".encode()
            + decoration
            + b") Tj ET\n"
        )
        stream = io.BytesIO()
        pdf.save(stream)
    source = stream.getvalue()
    inventory = inspect_reviewed_semantic_source(source)
    refs = [
        ReviewedOccurrence(o.page_index, o.operator_index, o.text, o.image_sha256)
        for o in inventory.occurrences
    ]
    manifest = ReviewedSemanticManifest(
        inventory.source_sha256,
        "synthetic test",
        "synthetic test only",
        "Study heading",
        "en",
        (
            ReviewedSemanticNode("heading", "H1", occurrences=(refs[0],)),
            ReviewedSemanticNode("body", "P", occurrences=(refs[1],)),
        ),
        ("body", "heading") if reordered else ("heading", "body"),
        artifacts=(refs[2],),
    )
    candidate = apply_reviewed_semantics(source, manifest).pdf_bytes
    path = tmp_path / "candidate.pdf"
    path.write_bytes(candidate)
    return path, source


@pytest.mark.parametrize("offset", [0, 0.025])
def test_same_native_glyph_at_subpixel_overprint_is_excluded(tmp_path, offset):
    path, _ = _candidate(tmp_path, offset=offset)
    result = ReadingOrderVerifier().check(str(path))
    assert result.has_structure_tree
    assert not result.issues


@pytest.mark.parametrize(
    "decoration,offset", [(b"Unique important text", 0), (b"Body text", 1)]
)
def test_artifact_marker_cannot_hide_unique_or_separate_body_text(
    tmp_path, decoration, offset
):
    path, _ = _candidate(tmp_path, decoration=decoration, offset=offset)
    result = ReadingOrderVerifier().check(str(path))
    assert len(result.issues) == 1
    assert result.issues[0].severity == "critical"
    assert not result.issues[0].review_only


def test_duplicate_proof_does_not_accept_reordered_semantics(tmp_path):
    path, _ = _candidate(tmp_path, reordered=True)
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues and not result.issues[0].review_only
    assert "differs" in result.issues[0].recommendation


def test_missing_conformance_identifier_is_reported_before_and_after_tagging(tmp_path):
    path, source = _candidate(tmp_path)
    original = tmp_path / "original.pdf"
    original.write_bytes(source)
    for file in (original, path):
        issues = StructureTreeChecker().check(str(file))
        identifier = [
            i for i in issues if i["issue_type"] == "missing_pdfua_identifier"
        ]
        assert len(identifier) == 1
        assert "independent" in identifier[0]["suggested_fix"]
