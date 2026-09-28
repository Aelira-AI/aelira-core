"""Saved review candidates change exactly the accepted structure operation."""

import hashlib
import io

import pikepdf
import pymupdf
import pytest
from pikepdf import Array, Dictionary, Name, String
from PIL import Image

from src.education import pdf_review_candidate as edit
from test_reading_order_snapshot import make_pdf

pytestmark = pytest.mark.unit


def sha(content):
    return hashlib.sha256(content).hexdigest()


def targets(content):
    return edit.inspect_pdf_edit_targets(content, sha(content))


def rewrite(content, change):
    with pikepdf.open(io.BytesIO(content)) as pdf:
        change(pdf)
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue()


def heading(content, **kwargs):
    target = next(item for item in targets(content) if item["can_set_heading"])
    return {"kind": "heading", "target_id": target["target_id"], "level": 2, **kwargs}


def page_evidence(content):
    with pikepdf.open(io.BytesIO(content)) as pdf:
        streams = [page.obj.Contents.read_bytes() for page in pdf.pages]
        parent_ids = [
            [str(item.S) for item in pdf.Root.StructTreeRoot.ParentTree.Nums[index]]
            for index in range(1, len(pdf.Root.StructTreeRoot.ParentTree.Nums), 2)
        ]
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        text = [page.get_text() for page in pdf]
        pixels = [sha(page.get_pixmap().samples) for page in pdf]
    return streams, text, pixels, parent_ids


def test_heading_candidate_changes_saved_tag_and_preserves_pages_and_metadata():
    source = rewrite(
        make_pdf(),
        lambda pdf: pdf.docinfo.update({"/Title": String("Synthetic course")}),
    )
    original = source[:]
    result = edit.create_pdf_edit_candidate(source, sha(source), heading(source))
    assert result.source_sha256 == sha(source)
    assert result.sha256 == sha(result.content) != sha(source)
    assert result.needs_review
    assert source == original
    before, after = page_evidence(source), page_evidence(result.content)
    assert before[:3] == after[:3]
    with pikepdf.open(io.BytesIO(result.content)) as saved:
        assert saved.Root.StructTreeRoot.K[0].S == Name.H2
        assert saved.Root.StructTreeRoot.K[1].S == Name.P
        assert saved.docinfo.Title == "Synthetic course"
        # The same ParentTree owner now carries the reviewed role.
        assert saved.Root.StructTreeRoot.ParentTree.Nums[1][1].S == Name.H2
    assert not hasattr(result, "approved")


def test_saved_sibling_order_preserves_content_and_parent_tree():
    source = make_pdf()
    parent = targets(source)[0]
    result = edit.create_pdf_edit_candidate(
        source,
        sha(source),
        {
            "kind": "order",
            "target_id": parent["target_id"],
            "children": parent["children"][::-1],
        },
    )
    assert page_evidence(source) == page_evidence(result.content)
    with pikepdf.open(io.BytesIO(result.content)) as saved:
        assert [node.K for node in saved.Root.StructTreeRoot.K] == [0, 1]


def test_nested_structure_targets_and_single_child_are_unambiguous():
    def nest(pdf):
        root = pdf.Root.StructTreeRoot
        section = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Sect, P=root, K=root.K)
        )
        for child in section.K:
            child.P = section
        root.K = section

    source = rewrite(make_pdf(), nest)
    descriptors = targets(source)
    assert not descriptors[0]["can_reorder"]
    section = next(item for item in descriptors if item["role"] == "Sect")
    result = edit.create_pdf_edit_candidate(
        source,
        sha(source),
        {
            "kind": "order",
            "target_id": section["target_id"],
            "children": section["children"][::-1],
        },
    )
    with pikepdf.open(io.BytesIO(result.content)) as saved:
        assert [node.K for node in saved.Root.StructTreeRoot.K.K] == [0, 1]


@pytest.mark.parametrize("level", [0, 7, True, "2", None, 2.0])
def test_invalid_heading_level_is_refused(level):
    source = make_pdf()
    with pytest.raises(edit.PDFEditRefused, match="unsupported_heading_edit"):
        edit.create_pdf_edit_candidate(
            source, sha(source), heading(source, level=level)
        )


@pytest.mark.parametrize("kind", ["missing", "duplicate", "foreign", "unchanged"])
def test_order_requires_exact_child_permutation(kind):
    source = make_pdf()
    parent = targets(source)[0]
    children = parent["children"][:]
    if kind == "missing":
        children.pop()
    elif kind == "duplicate":
        children[1] = children[0]
    elif kind == "foreign":
        children[1] = parent["target_id"]
    with pytest.raises(edit.PDFEditRefused):
        edit.create_pdf_edit_candidate(
            source,
            sha(source),
            {"kind": "order", "target_id": parent["target_id"], "children": children},
        )


def test_checksum_and_target_identity_do_not_transfer_to_changed_source():
    source = make_pdf()
    operation = heading(source)
    changed = rewrite(
        source, lambda pdf: pdf.docinfo.update({"/Title": String("New revision")})
    )
    with pytest.raises(edit.PDFEditRefused, match="source_checksum_mismatch"):
        edit.create_pdf_edit_candidate(changed, sha(source), operation)
    with pytest.raises(edit.PDFEditRefused, match="stale_or_unknown_target"):
        edit.create_pdf_edit_candidate(changed, sha(changed), operation)


@pytest.mark.parametrize(
    "defect",
    [
        "untagged",
        "owner",
        "missing_content",
        "object_reference",
        "empty",
        "cycle",
        "missing_tag",
    ],
)
def test_malformed_or_unbound_structure_is_not_editable(defect):
    source = make_pdf(defect=defect)
    with pytest.raises(edit.PDFEditRefused):
        targets(source)


@pytest.mark.parametrize(
    "kind", ["signature", "form", "rolemap", "namespace", "wrong_parent", "shared_node"]
)
def test_unsupported_structure_and_signed_documents_are_refused(kind):
    def damage(pdf):
        root = pdf.Root.StructTreeRoot
        if kind == "signature":
            pdf.Root.Perms = Dictionary(
                DocMDP=Dictionary(Type=Name.Sig, ByteRange=Array([0, 10, 20, 30]))
            )
        elif kind == "form":
            pdf.Root.AcroForm = Dictionary(Fields=Array([]))
        elif kind == "rolemap":
            root.RoleMap = Dictionary(Custom=Name.P)
        elif kind == "namespace":
            root.K[0].NS = Dictionary()
        elif kind == "wrong_parent":
            root.K[0].P = root.K[1]
        else:
            root.K = Array([root.K[0], root.K[0]])

    source = rewrite(make_pdf(), damage)
    with pytest.raises(edit.PDFEditRefused):
        targets(source)


@pytest.mark.parametrize("damage", ["page_stream", "role", "metadata", "order"])
def test_serialization_damage_is_detected(monkeypatch, damage):
    source = make_pdf()
    serialize = edit._serialize

    def corrupt(pdf):
        if damage == "page_stream":
            pdf.pages[0].obj.Contents = pdf.make_stream(b"q Q")
        elif damage == "role":
            pdf.Root.StructTreeRoot.K[1].S = Name.H6
        elif damage == "metadata":
            pdf.docinfo.Title = String("Unexpected metadata change")
        else:
            pdf.Root.StructTreeRoot.K = Array(list(pdf.Root.StructTreeRoot.K)[::-1])
        return serialize(pdf)

    monkeypatch.setattr(edit, "_serialize", corrupt)
    with pytest.raises(edit.PDFEditRefused):
        edit.create_pdf_edit_candidate(source, sha(source), heading(source))


def test_errors_never_expose_parser_details(monkeypatch):
    def fail(_pdf):
        raise RuntimeError("private path and document text")

    source = make_pdf()
    operation = heading(source)
    monkeypatch.setattr(edit, "_serialize", fail)
    with pytest.raises(edit.PDFEditRefused, match="^unsupported_or_invalid_pdf$"):
        edit.create_pdf_edit_candidate(source, sha(source), operation)


def test_noop_heading_and_unknown_operations_are_refused():
    source = rewrite(
        make_pdf(), lambda pdf: setattr(pdf.Root.StructTreeRoot.K[0], "S", Name.H2)
    )
    with pytest.raises(edit.PDFEditRefused, match="no_change"):
        edit.create_pdf_edit_candidate(source, sha(source), heading(source))
    with pytest.raises(edit.PDFEditRefused, match="unsupported_operation"):
        edit.create_pdf_edit_candidate(
            source, sha(source), heading(source, kind="approve")
        )


@pytest.mark.parametrize("kind", ["empty_tag", "replacement_parent"])
def test_unbound_tags_and_masked_child_order_are_refused(kind):
    def change(pdf):
        root = pdf.Root.StructTreeRoot
        if kind == "empty_tag":
            root.K.append(
                pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=root))
            )
        else:
            root.ActualText = String("Replacement would hide child reading order")

    source = rewrite(make_pdf(), change)
    with pytest.raises(edit.PDFEditRefused):
        targets(source)


def test_encrypted_and_oversized_inputs_are_refused(monkeypatch):
    encrypted = make_pdf(encrypted=True)
    with pytest.raises(edit.PDFEditRefused):
        targets(encrypted)
    source = make_pdf()
    monkeypatch.setattr(edit, "MAX_SOURCE_BYTES", len(source) - 1)
    with pytest.raises(edit.PDFEditRefused, match="invalid_or_oversized_source"):
        targets(source)


def test_object_and_structure_work_limits_are_enforced(monkeypatch):
    source = make_pdf()
    operation = heading(source)
    monkeypatch.setattr(edit, "MAX_GRAPH_VISITS", 1)
    with pytest.raises(edit.PDFEditRefused, match="graph_limit_exceeded"):
        edit.create_pdf_edit_candidate(source, sha(source), operation)
    monkeypatch.setattr(edit, "MAX_NODES", 1)
    with pytest.raises(edit.PDFEditRefused, match="structure_limit_exceeded"):
        targets(source)


@pytest.mark.parametrize("kind", ["missing", "wrong"])
def test_structure_root_requires_its_type(kind):
    def change(pdf):
        root = pdf.Root.StructTreeRoot
        if kind == "missing":
            del root.Type
        else:
            root.Type = Name.Catalog

    source = rewrite(make_pdf(), change)
    with pytest.raises(edit.PDFEditRefused):
        targets(source)


def test_jpeg_image_document_has_consistent_inspection_and_edit_support(monkeypatch):
    encoded = io.BytesIO()
    Image.new("RGB", (4, 4), (30, 100, 160)).save(encoded, format="JPEG")

    def image(pdf):
        page = pdf.pages[0]
        stream = pdf.make_stream(encoded.getvalue())
        stream.Type, stream.Subtype = Name.XObject, Name.Image
        stream.Width, stream.Height = 4, 4
        stream.ColorSpace, stream.BitsPerComponent = Name.DeviceRGB, 8
        stream.Filter = Name.DCTDecode
        page.Resources.XObject = Dictionary(Im1=stream)
        page.obj.Contents = pdf.make_stream(
            page.obj.Contents.read_bytes() + b" q 30 0 0 30 200 100 cm /Im1 Do Q"
        )

    source = rewrite(make_pdf(), image)
    result = edit.create_pdf_edit_candidate(source, sha(source), heading(source))
    assert page_evidence(source)[:3] == page_evidence(result.content)[:3]
    with pikepdf.open(io.BytesIO(result.content)) as pdf:
        assert pdf.pages[0].Resources.XObject.Im1.read_raw_bytes() == encoded.getvalue()
    serialize = edit._serialize

    def corrupt(pdf):
        pdf.pages[0].Resources.XObject.Im1.DecodeParms = Dictionary(ColorTransform=0)
        return serialize(pdf)

    operation = heading(source)
    monkeypatch.setattr(edit, "_serialize", corrupt)
    with pytest.raises(edit.PDFEditRefused, match="saved_content_changed"):
        edit.create_pdf_edit_candidate(source, sha(source), operation)


@pytest.mark.parametrize("damage", ["annotation", "shared_font"])
def test_unrelated_objects_and_shared_reference_identity_are_preserved(
    monkeypatch, damage
):
    def prepare(pdf):
        page = pdf.pages[0]
        font = pdf.make_indirect(page.Resources.Font.F1)
        page.Resources.Font = Dictionary(F1=font, F2=font)
        page.obj.Annots = Array(
            [
                pdf.make_indirect(
                    Dictionary(
                        Type=Name.Annot,
                        Subtype=Name.Link,
                        Rect=Array([10, 10, 20, 20]),
                        A=Dictionary(
                            S=Name.URI, URI=String("https://example.org/lesson")
                        ),
                    )
                )
            ]
        )

    source = rewrite(make_pdf(), prepare)
    operation = heading(source)
    positive = edit.create_pdf_edit_candidate(source, sha(source), operation)
    assert positive.content
    serialize = edit._serialize

    def corrupt(pdf):
        page = pdf.pages[0]
        if damage == "annotation":
            page.obj.Annots[0].A.URI = String("https://example.org/changed")
        else:
            page.Resources.Font.F2 = pdf.make_indirect(
                Dictionary(page.Resources.Font.F1)
            )
        return serialize(pdf)

    monkeypatch.setattr(edit, "_serialize", corrupt)
    with pytest.raises(edit.PDFEditRefused, match="saved_content_changed"):
        edit.create_pdf_edit_candidate(source, sha(source), operation)
