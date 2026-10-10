"""Saved review candidates change exactly the accepted structure operation."""

import hashlib
import io

import pikepdf
import pymupdf
import pytest
from pikepdf import Array, Dictionary, Name, String
from PIL import Image

from src.education import pdf_review_candidate as edit
from test_reading_order_tables import build_table_pdf
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


def table_pdf(tmp_path):
    pdf, path = build_table_pdf(tmp_path)
    root = pdf.Root.StructTreeRoot
    table = root.K[0].K[1]
    body = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table))
    body_cells = []
    for mcid, x, label in [(4, 35, "West body"), (5, 260, "East body")]:
        cell = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.TD, P=body, K=mcid)
        )
        body_cells.append(cell)
        root.ParentTree.Nums[1].append(cell)
        pdf.pages[0].Contents = pdf.make_stream(
            pdf.pages[0].Contents.read_bytes()
            + f"\n/P <</MCID {mcid}>> BDC BT /F1 12 Tf {x} 280 Td ({label}) Tj ET EMC".encode()
        )
    body.K = Array(body_cells)
    table.K = Array([table.K, body])
    table.K[0].K[0].A = Dictionary(O=Name.Table, BorderColor=Array([0, 0, 0]))
    table.K[1].K[0].Lang = String("en-US")
    pdf.save(path)
    return path.read_bytes()


def table_operation(content):
    table = next(item for item in targets(content) if item["role"] == "Table")
    return {"kind": "table_column_headers", "target_id": table["target_id"]}


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
        lambda pdf: pdf.docinfo.__setitem__("/Title", String("Synthetic course")),
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


def test_explicit_first_row_column_headers_are_saved_without_other_changes(tmp_path):
    source = table_pdf(tmp_path)
    operation = table_operation(source)
    assert next(item for item in targets(source) if item["role"] == "Table")[
        "can_set_column_headers"
    ]
    result = edit.create_pdf_edit_candidate(source, sha(source), operation)
    assert result.operation == "table_column_headers"
    assert result.source_sha256 == sha(source)
    assert result.sha256 == sha(result.content) != sha(source)
    assert result.needs_review
    assert page_evidence(source)[:3] == page_evidence(result.content)[:3]
    with pikepdf.open(io.BytesIO(result.content)) as saved:
        rows = saved.Root.StructTreeRoot.K[0].K[1].K
        assert all(cell.S == Name.TH for cell in rows[0].K)
        assert all(
            cell.A.O == Name.Table and cell.A.Scope == Name.Column for cell in rows[0].K
        )
        assert list(rows[0].K[0].A.BorderColor) == [0, 0, 0]
        assert all(cell.S == Name.TD for cell in rows[1].K)
        assert rows[1].K[0].Lang == "en-US"
        assert [cell.K for row in rows for cell in row.K] == [1, 2, 4, 5]
        assert [owner.S for owner in saved.Root.StructTreeRoot.ParentTree.Nums[1]] == [
            Name.H1,
            Name.TH,
            Name.TH,
            Name.P,
            Name.TD,
            Name.TD,
        ]


def test_table_header_noop_and_stale_source_are_refused(tmp_path):
    source = table_pdf(tmp_path)
    operation = table_operation(source)
    result = edit.create_pdf_edit_candidate(source, sha(source), operation)
    with pytest.raises(edit.PDFEditRefused, match="no_change"):
        edit.create_pdf_edit_candidate(
            result.content, sha(result.content), table_operation(result.content)
        )
    with pytest.raises(edit.PDFEditRefused, match="source_checksum_mismatch"):
        edit.create_pdf_edit_candidate(result.content, sha(source), operation)
    with pytest.raises(edit.PDFEditRefused, match="stale_or_unknown_target"):
        edit.create_pdf_edit_candidate(result.content, sha(result.content), operation)


def test_shared_cell_attribute_is_not_modified_for_body_cell(tmp_path):
    def share(pdf):
        table = pdf.Root.StructTreeRoot.K[0].K[1]
        shared = pdf.make_indirect(
            Dictionary(O=Name.Table, BorderColor=Array([0, 0, 0]))
        )
        table.K[0].K[0].A = shared
        table.K[1].K[0].A = shared

    source = rewrite(table_pdf(tmp_path), share)
    result = edit.create_pdf_edit_candidate(
        source, sha(source), table_operation(source)
    )
    with pikepdf.open(io.BytesIO(result.content)) as saved:
        first, body = saved.Root.StructTreeRoot.K[0].K[1].K
        assert first.K[0].A.Scope == Name.Column
        assert "/Scope" not in body.K[0].A
        assert list(body.K[0].A.BorderColor) == [0, 0, 0]


def test_nested_table_inside_data_cell_is_not_a_simple_grid(tmp_path):
    def nest(pdf):
        root = pdf.Root.StructTreeRoot
        outer_cell = root.K[0].K[1].K[0].K[0]
        inner_table = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Table, P=outer_cell)
        )
        inner_row = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.TR, P=inner_table)
        )
        inner_cell = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.TD, P=inner_row, K=1)
        )
        inner_row.K = inner_cell
        inner_table.K = inner_row
        outer_cell.K = inner_table
        root.ParentTree.Nums[1][1] = inner_cell

    source = rewrite(table_pdf(tmp_path), nest)
    outer = next(item for item in targets(source) if item["role"] == "Table")
    assert not outer["can_set_column_headers"]
    with pytest.raises(edit.PDFEditRefused, match="unsupported_table_header_edit"):
        edit.create_pdf_edit_candidate(
            source,
            sha(source),
            {"kind": "table_column_headers", "target_id": outer["target_id"]},
        )


def test_no_page_inheritance_with_root_parent_cycle_is_refused(tmp_path):
    def cycle(pdf):
        root = pdf.Root.StructTreeRoot
        del root.K[0].Pg
        root.P = root

    source = rewrite(table_pdf(tmp_path), cycle)
    outer = next(item for item in targets(source) if item["role"] == "Table")
    assert not outer["can_set_column_headers"]
    with pytest.raises(edit.PDFEditRefused, match="unsupported_table_header_edit"):
        edit.create_pdf_edit_candidate(
            source,
            sha(source),
            {"kind": "table_column_headers", "target_id": outer["target_id"]},
        )


@pytest.mark.parametrize(
    "defect",
    [
        "ragged",
        "nested",
        "span",
        "headers",
        "id",
        "class",
        "array_attr",
        "wrong_owner",
        "row_header",
        "scope",
        "cross_page",
        "mcr_page",
        "extra_key",
    ],
)
def test_complex_table_header_edits_are_refused(tmp_path, defect):
    source = table_pdf(tmp_path)
    operation = table_operation(source)

    def change(pdf):
        table = pdf.Root.StructTreeRoot.K[0].K[1]
        first, body = table.K
        if defect == "ragged":
            body.K = Array([body.K[0]])
        elif defect == "nested":
            first.K[0].S = Name.Table
        elif defect == "span":
            first.K[0].A.RowSpan = 2
        elif defect == "headers":
            body.K[0].A = Dictionary(O=Name.Table, Headers=Array([String("h1")]))
        elif defect == "id":
            first.K[0].ID = String("h1")
        elif defect == "class":
            first.K[0].C = Name.HeaderClass
            pdf.Root.StructTreeRoot.ClassMap = Dictionary(
                HeaderClass=Dictionary(O=Name.Table)
            )
        elif defect == "array_attr":
            first.K[0].A = Array([first.K[0].A])
        elif defect == "wrong_owner":
            first.K[0].A.O = Name.Layout
        elif defect == "row_header":
            body.K[0].S = Name.TH
        elif defect == "scope":
            first.K[0].S = Name.TH
            first.K[0].A.Scope = Name.Row
        elif defect == "cross_page":
            page = pdf.add_blank_page(page_size=(500, 500))
            body.K[0].Pg = page.obj
        elif defect == "mcr_page":
            first.K[0].K = Dictionary(Type=Name.MCR, MCID=1, Pg=pdf.Root.Pages)

    if defect == "extra_key":
        operation["approval"] = True
    else:
        source = rewrite(source, change)
        operation = {"kind": "table_column_headers", "target_id": sha(source) + ":0/1"}
    with pytest.raises(edit.PDFEditRefused):
        edit.create_pdf_edit_candidate(source, sha(source), operation)


def test_table_header_saved_corruption_is_detected(tmp_path, monkeypatch):
    source = table_pdf(tmp_path)
    operation = table_operation(source)
    serialize = edit._serialize

    def corrupt(pdf):
        pdf.Root.StructTreeRoot.K[0].K[1].K[1].K[0].Lang = String("fr-FR")
        return serialize(pdf)

    monkeypatch.setattr(edit, "_serialize", corrupt)
    with pytest.raises(edit.PDFEditRefused, match="saved_content_changed"):
        edit.create_pdf_edit_candidate(source, sha(source), operation)


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
        source, lambda pdf: pdf.docinfo.__setitem__("/Title", String("New revision"))
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
