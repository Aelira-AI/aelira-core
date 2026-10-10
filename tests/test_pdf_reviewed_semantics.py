"""Complete source-bound operator classification and saved structure evidence."""

import hashlib
import io
from dataclasses import replace

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import require_complete_pdf_scan
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.remediation.pdf_reviewed_semantics import (
    ReviewedOccurrence,
    ReviewedSemanticManifest,
    ReviewedSemanticNode,
    ReviewedSemanticsError,
    apply_reviewed_semantics,
    inspect_reviewed_semantic_source,
    inspect_tagged_semantic_source,
)

pytestmark = pytest.mark.unit


def _bytes(pdf):
    buffer = io.BytesIO()
    pdf.save(
        buffer,
        compress_streams=False,
        stream_decode_level=pikepdf.StreamDecodeLevel.none,
    )
    return buffer.getvalue()


def _source(*, images=False, decorative=False):
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
            b"q 1 0 0 1 0.000000001 0 cm\n"
            b"BT /F1 18 Tf 30 350 Td (Study title) Tj\n"
            b"/F1 12 Tf 0 -40 Td [(First) -300 (paragraph.)] TJ\n"
            b"0 -40 Td (1.) Tj 20 0 Td (Eligible students.) Tj\n"
            b"0 -30 Td (Participation details.) Tj ET Q\n"
            b"10 10 m 20 10 l 20 20 l h f\n"
        )
        suffix = b""
        if images:
            image = pdf.make_stream(bytes([30, 100, 200] * 4))
            image.Type, image.Subtype = Name.XObject, Name.Image
            image.Width, image.Height, image.BitsPerComponent, image.ColorSpace = (
                2,
                2,
                8,
                Name.DeviceRGB,
            )
            page.Resources.XObject = Dictionary(Im=image)
            suffix += (
                b"q 20 0 0 20 300 300 cm /Im Do Q q 20 0 0 20 320 300 cm /Im Do Q\n"
            )
            suffix += b"q 10 0 0 10 10 380 cm /Im Do Q\n"
        if decorative:
            suffix += b"BT /F1 12 Tf 380 380 Td (x) Tj ET\n"
        if suffix:
            page.Contents = Array([page.Contents, pdf.make_stream(suffix)])
        return _bytes(pdf)


def _ref(occurrence):
    return ReviewedOccurrence(
        occurrence.page_index,
        occurrence.operator_index,
        occurrence.text,
        occurrence.image_sha256,
    )


def _manifest(source, *, images=False, decorative=False):
    inventory = inspect_reviewed_semantic_source(source)
    text = [o for o in inventory.occurrences if o.kind == "text"]
    nodes = [
        ReviewedSemanticNode("heading", "H1", occurrences=(_ref(text[0]),)),
        ReviewedSemanticNode("paragraph", "P", occurrences=(_ref(text[1]),)),
        ReviewedSemanticNode("list", "L", children=("item",)),
        ReviewedSemanticNode("item", "LI", children=("label", "body")),
        ReviewedSemanticNode("label", "Lbl", occurrences=(_ref(text[2]),)),
        ReviewedSemanticNode(
            "body", "LBody", occurrences=(_ref(text[3]), _ref(text[4]))
        ),
    ]
    roots = ["heading", "paragraph", "list"]
    artifacts = []
    if images:
        draws = [o for o in inventory.occurrences if o.kind == "image"]
        nodes.append(
            ReviewedSemanticNode(
                "photo",
                "Figure",
                occurrences=(_ref(draws[0]), _ref(draws[1])),
                alt="A blue demonstration swatch assembled from two pieces.",
            )
        )
        roots.append("photo")
        artifacts.append(_ref(draws[2]))
    if decorative:
        artifacts.append(_ref(text[-1]))
    return ReviewedSemanticManifest(
        inventory.source_sha256,
        "synthetic author",
        "fixture content and semantic roles",
        "Study title",
        "en",
        tuple(nodes),
        tuple(roots),
        tuple(artifacts),
        vector_artifacts_reviewed=True,
    )


def _pixels(data):
    with fitz.open(stream=data, filetype="pdf") as doc:
        return tuple(page.get_pixmap(matrix=fitz.Matrix(2, 2)).samples for page in doc)


def _nodes(element):
    yield element
    for child in element.K:
        if child.get("/Type") == Name.StructElem:
            yield from _nodes(child)


def test_partial_bt_roles_lists_grouped_images_and_artifacts_preserve_exact_source():
    source = _source(images=True, decorative=True)
    manifest = _manifest(source, images=True, decorative=True)
    result = apply_reviewed_semantics(source, manifest)
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.output_sha256 == hashlib.sha256(result.pdf_bytes).hexdigest()
    assert result.semantic_occurrences == 7
    assert result.artifact_occurrences == 2
    assert _pixels(source) == _pixels(result.pdf_bytes)
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
        root = pdf.Root.StructTreeRoot
        document = root.K[0]
        assert [str(node.S) for node in document.K] == ["/H1", "/P", "/L", "/Figure"]
        assert [str(node.S) for node in document.K[2].K[0].K] == ["/Lbl", "/LBody"]
        assert len(document.K[2].K[0].K[1].K) == 1
        assert len(document.K[3].K) == 2
        assert len(root.ParentTree.Nums[1]) == 6
        assert all("/ActualText" not in node for node in _nodes(document))
        raw = pdf.pages[0].Contents.read_bytes()
        assert b"0.000000001" in raw
        assert (
            raw.count(b"/Artifact BMC") == 3
        )  # Two reviewed occurrences + vector paint.
        assert str(pdf.Root.Lang) == "en"
        assert str(pdf.docinfo.Title) == "Study title"
        assert pdf.Root.ViewerPreferences.DisplayDocTitle


def test_saved_partial_bt_semantics_pass_strict_reading_order(tmp_path):
    source = _source()
    result = apply_reviewed_semantics(source, _manifest(source))
    output = tmp_path / "reviewed.pdf"
    output.write_bytes(result.pdf_bytes)
    with require_complete_pdf_scan(True):
        reading = ReadingOrderVerifier().check(str(output))
    assert reading.has_structure_tree and not reading.issues
    assert _pixels(source) == _pixels(result.pdf_bytes)


@pytest.mark.parametrize(
    "change", ["omitted", "duplicate", "wrong_text", "wrong_index", "wrong_image"]
)
def test_incomplete_or_changed_occurrence_review_refuses(change):
    source = _source(images=True, decorative=True)
    manifest = _manifest(source, images=True, decorative=True)
    if change == "omitted":
        manifest = replace(manifest, artifacts=manifest.artifacts[:-1])
    elif change == "duplicate":
        manifest = replace(
            manifest, artifacts=manifest.artifacts + manifest.nodes[0].occurrences
        )
    else:
        node = manifest.nodes[-1] if change == "wrong_image" else manifest.nodes[0]
        occurrence = node.occurrences[0]
        if change == "wrong_text":
            occurrence = replace(occurrence, text="Invented title")
        elif change == "wrong_index":
            occurrence = replace(occurrence, operator_index=-1)
        else:
            occurrence = replace(occurrence, image_sha256="0" * 64)
        changed = replace(node, occurrences=(occurrence, *node.occurrences[1:]))
        manifest = replace(
            manifest,
            nodes=tuple(
                changed if n.node_id == node.node_id else n for n in manifest.nodes
            ),
        )
    with pytest.raises(ReviewedSemanticsError, match="occurrence"):
        apply_reviewed_semantics(source, manifest)


@pytest.mark.parametrize(
    "change", ["cycle", "orphan", "list", "duplicate", "figure_role"]
)
def test_invalid_hierarchy_refuses(change):
    source = _source()
    manifest = _manifest(source)
    if change == "cycle":
        changed = replace(manifest.nodes[4], children=("list",), occurrences=())
        manifest = replace(
            manifest, nodes=(*manifest.nodes[:4], changed, manifest.nodes[5])
        )
    elif change == "orphan":
        manifest = replace(manifest, root_ids=manifest.root_ids[:-1])
    elif change == "list":
        changed = replace(manifest.nodes[2], children=("body",))
        manifest = replace(
            manifest, nodes=(*manifest.nodes[:2], changed, *manifest.nodes[3:])
        )
    elif change == "duplicate":
        manifest = replace(manifest, root_ids=(*manifest.root_ids, "heading"))
    else:
        changed = replace(
            manifest.nodes[0],
            role="Figure",
            alt="Cannot turn source words into an image.",
        )
        manifest = replace(manifest, nodes=(changed, *manifest.nodes[1:]))
    with pytest.raises(ReviewedSemanticsError):
        apply_reviewed_semantics(source, manifest)


def test_stale_source_and_existing_marked_source_refuse():
    source = _source()
    manifest = _manifest(source)
    with pytest.raises(ReviewedSemanticsError, match="source_changed"):
        apply_reviewed_semantics(source + b"\n% changed\n", manifest)
    tagged = apply_reviewed_semantics(source, manifest)
    with pytest.raises(ReviewedSemanticsError, match="document_scope"):
        inspect_reviewed_semantic_source(tagged.pdf_bytes)


def test_vector_artifacts_require_explicit_source_review():
    source = _source()
    manifest = _manifest(source)
    assert inspect_reviewed_semantic_source(source).vector_paint_count == 1
    with pytest.raises(ReviewedSemanticsError, match="vector_review_required"):
        apply_reviewed_semantics(
            source, replace(manifest, vector_artifacts_reviewed=False)
        )


def test_reviewed_heading_bookmarks_and_metadata_are_saved_exactly():
    source = _source()
    manifest = _manifest(source)
    manifest = replace(
        manifest,
        nodes=(
            replace(manifest.nodes[0], outline_title="Study title"),
            replace(manifest.nodes[1], role="H2", outline_title="First paragraph."),
            *manifest.nodes[2:],
        ),
    )
    result = apply_reviewed_semantics(source, manifest)
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
        outlines = pdf.Root.Outlines
        assert outlines.Count == 2
        assert str(outlines.First.Title) == "Study title"
        assert outlines.First.Count == 1
        assert str(outlines.First.First.Title) == "First paragraph."
        assert outlines.First.First.Parent.objgen == outlines.First.objgen
        assert outlines.First.Dest[0].objgen == pdf.pages[0].obj.objgen
        assert "/Metadata" not in pdf.Root  # No invented PDF/UA declaration.
    assert _pixels(source) == _pixels(result.pdf_bytes)


def test_outline_title_cannot_invent_source_words():
    source = _source()
    manifest = _manifest(source)
    manifest = replace(
        manifest,
        nodes=(
            replace(manifest.nodes[0], outline_title="Invented heading"),
            *manifest.nodes[1:],
        ),
    )
    with pytest.raises(ReviewedSemanticsError, match="outline_title"):
        apply_reviewed_semantics(source, manifest)


def test_verified_font_recovery_then_reviewed_semantics_completes_strict_saved_check(
    tmp_path,
):
    from test_pdf_verified_font_recovery import _source as unmapped_source
    from src.education.remediation.pdf_verified_font_recovery import (
        recover_verified_font_maps,
    )

    source, font_review, _ = unmapped_source(
        (
            "Study title",
            "First paragraph.",
            "1.",
            "Eligible students.",
            "Participation details.",
        )
    )
    recovered = recover_verified_font_maps(source, font_review)
    semantic_review = _manifest(recovered.pdf_bytes)
    semantic_review = replace(
        semantic_review,
        nodes=(
            replace(semantic_review.nodes[0], outline_title="Study title"),
            *semantic_review.nodes[1:],
        ),
    )
    result = apply_reviewed_semantics(recovered.pdf_bytes, semantic_review)
    output = tmp_path / "complete.pdf"
    output.write_bytes(result.pdf_bytes)
    assert _pixels(source) == _pixels(result.pdf_bytes)
    with require_complete_pdf_scan(True):
        reading = ReadingOrderVerifier().check(str(output))
    assert reading.has_structure_tree and not reading.issues


def test_coalesced_fragments_preserve_real_word_boundaries(tmp_path):
    source = _source()
    with pikepdf.open(io.BytesIO(source)) as pdf:
        pdf.pages[0].Contents = pdf.make_stream(
            b"BT /F1 12 Tf 30 300 Td (app) Tj 0 Tc (rov) Tj (ed) Tj ET"
        )
        source = _bytes(pdf)
    inventory = inspect_reviewed_semantic_source(source)
    manifest = ReviewedSemanticManifest(
        inventory.source_sha256,
        "fixture author",
        "split-word fixture",
        "Reviewed word",
        "en",
        (
            ReviewedSemanticNode(
                "body", "P", occurrences=tuple(_ref(o) for o in inventory.occurrences)
            ),
        ),
        ("body",),
    )
    result = apply_reviewed_semantics(source, manifest)
    output = tmp_path / "word.pdf"
    output.write_bytes(result.pdf_bytes)
    assert _pixels(source) == _pixels(result.pdf_bytes)
    with pikepdf.open(output) as pdf:
        assert len(pdf.Root.StructTreeRoot.K[0].K[0].K) == 1
    with require_complete_pdf_scan(True):
        verifier = ReadingOrderVerifier()
        with fitz.open(output) as doc:
            assert [
                b["text"]
                for b in verifier._get_structure_tree_order(doc[0], str(output), 0)
            ] == ["approved"]
        assert not verifier.check(str(output)).issues
    shows = inspect_tagged_semantic_source(result.pdf_bytes).occurrences
    assert [o.text for o in shows] == ["app", "rov", "ed"]
    assert all(not o.artifact for o in shows)
    assert "".join(g.text for o in shows for g in o.glyphs) == "approved"


@pytest.mark.parametrize("barrier", ["artifact", "node", "graphics", "text_object"])
def test_coalescing_stops_at_source_and_semantic_barriers(barrier):
    source = _source()
    between = {
        "artifact": b"(x) Tj",
        "node": b"(x) Tj",
        "graphics": b"q Q",
        "text_object": b"ET BT /F1 12 Tf 50 300 Td",
    }[barrier]
    with pikepdf.open(io.BytesIO(source)) as pdf:
        pdf.pages[0].Contents = pdf.make_stream(
            b"BT /F1 12 Tf 30 300 Td (app) Tj " + between + b" (rov) Tj (ed) Tj ET"
        )
        source = _bytes(pdf)
    inventory = inspect_reviewed_semantic_source(source)
    body = tuple(_ref(o) for o in inventory.occurrences if o.text != "x")
    other = tuple(_ref(o) for o in inventory.occurrences if o.text == "x")
    nodes = (ReviewedSemanticNode("body", "P", occurrences=body),)
    roots = ("body",)
    if barrier == "node":
        nodes += (ReviewedSemanticNode("other", "P", occurrences=other),)
        roots += ("other",)
    manifest = ReviewedSemanticManifest(
        inventory.source_sha256,
        "fixture author",
        "barrier fixture",
        "Title",
        "en",
        nodes,
        roots,
        other if barrier == "artifact" else (),
    )
    result = apply_reviewed_semantics(source, manifest)
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
        assert len(pdf.Root.StructTreeRoot.K[0].K[0].K) == 2
    assert _pixels(source) == _pixels(result.pdf_bytes)


def test_tagged_source_glyphs_distinguish_artifacts_without_using_actualtext():
    source = _source(decorative=True)
    result = apply_reviewed_semantics(source, _manifest(source, decorative=True))
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
        pdf.pages[0].Contents.write(
            pdf.pages[0]
            .Contents.read_bytes()
            .replace(b"/MCID 0", b"/MCID 0 /ActualText (Invented replacement)")
        )
        tagged = _bytes(pdf)
    inventory = inspect_tagged_semantic_source(tagged)
    text = [o for o in inventory.occurrences if o.kind == "text"]
    assert text[0].text == "Study title"
    assert "".join(g.text for g in text[0].glyphs) == "Study title"
    assert not text[0].artifact
    assert text[-1].text == "x" and text[-1].artifact
    assert len(text[-1].glyphs[0].origin) == 2


@pytest.mark.parametrize("corrupt", ["nested", "owner"])
def test_tagged_source_inspection_refuses_ambiguous_scope_and_ownership(corrupt):
    source = _source()
    result = apply_reviewed_semantics(source, _manifest(source))
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as pdf:
        if corrupt == "nested":
            raw = pdf.pages[0].Contents.read_bytes()
            raw = raw.replace(
                b"(Study title) Tj", b"/Artifact BMC (Study title) Tj EMC"
            )
            pdf.pages[0].Contents.write(raw)
        else:
            owners = pdf.Root.StructTreeRoot.ParentTree.Nums[1]
            owners[0] = owners[1]
        corrupted = _bytes(pdf)
    with pytest.raises(ReviewedSemanticsError):
        inspect_tagged_semantic_source(corrupted)


@pytest.mark.parametrize("target", ["content", "structure", "font"])
def test_save_corruption_cannot_return_verified_candidate(monkeypatch, target):
    source = _source()
    manifest = _manifest(source)
    original_save = pikepdf.Pdf.save

    def corrupt(pdf, *args, **kwargs):
        if "/StructTreeRoot" in pdf.Root:
            if target == "content":
                pdf.pages[0].Contents.write(
                    pdf.pages[0]
                    .Contents.read_bytes()
                    .replace(b"Study title", b"Wrong title")
                )
            elif target == "structure":
                document = pdf.Root.StructTreeRoot.K[0]
                document.K = Array(list(reversed(list(document.K))))
            else:
                pdf.pages[0].Resources.Font.F1.BaseFont = Name.Courier
        return original_save(pdf, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", corrupt)
    with pytest.raises(ReviewedSemanticsError):
        apply_reviewed_semantics(source, manifest)
