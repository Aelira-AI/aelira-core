"""Invented source-owned headings; tag names alone never establish headings."""

import io
from hashlib import sha256

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name, String

from src.education.pdf_checks.bound_headings import nominate_title
from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.pdf_review_candidate import (
    inspect_pdf_edit_targets,
    create_pdf_edit_candidate,
    PDFEditRefused,
)

pytestmark = pytest.mark.unit


def title_pdf(tmp_path, *, change=None):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(600, 800))
    page.obj.StructParents = 0
    page.obj.Resources = Dictionary(
        Font=Dictionary(
            F1=Dictionary(
                Type=Name.Font,
                Subtype=Name.Type1,
                BaseFont=Name.Helvetica,
                Encoding=Name.WinAnsiEncoding,
            )
        )
    )
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    document = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=root)
    )
    group = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Div, P=document, Pg=page.obj)
    )
    labels = [
        "R\xe9sum\xe9 of methods",
        "for invented courses",
        "Ordinary source paragraph",
    ]
    owners = [
        pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem,
                S=Name.P,
                P=group if i < 2 else document,
                Pg=page.obj,
                K=i,
            )
        )
        for i in range(3)
    ]
    group.K = Array(owners[:2])
    document.K = Array([group, owners[2]])
    root.K = Array([document])
    root.ParentTree = Dictionary(Nums=Array([0, Array(owners)]))
    pdf.Root.StructTreeRoot = root
    page.obj.Contents = pdf.make_stream(
        b"\n".join(
            f"/P <</MCID {i}>> BDC BT /F1 {24 if i<2 else 12} Tf 45 {710-i*32 if i<2 else 540} Td ({label}) Tj ET EMC".encode(
                "cp1252"
            )
            for i, label in enumerate(labels)
        )
    )
    if change:
        change(pdf, group, owners)
    path = tmp_path / "invented-title.pdf"
    pdf.save(path)
    pdf.close()
    return path


def scan(path):
    return StructureTreeChecker().heading_inventory(str(path))


@pytest.mark.parametrize(
    "role,expected", [("H1", True), ("H2", False), ("H6", False), ("H", False)]
)
def test_only_nonempty_bound_h1_counts(tmp_path, role, expected):
    path = title_pdf(
        tmp_path, change=lambda p, g, o: setattr(o[0], "S", Name("/" + role))
    )
    assert scan(path).has_h1 is expected


@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "pg_only",
        "actual_only",
        "dangling",
        "duplicate",
        "wrong_parent_tree",
        "role_cycle",
        "namespace",
    ],
)
def test_unbound_or_ambiguous_heading_cannot_clear_finding(tmp_path, damage):
    def change(pdf, group, owners):
        owner = owners[0]
        owner.S = Name.H1
        if damage in {"empty", "pg_only", "actual_only"}:
            del owner.K
            if damage == "actual_only":
                owner.ActualText = String("Unbound title")
        elif damage == "dangling":
            owner.K = 99
        elif damage == "duplicate":
            group.K.append(owner)
        elif damage == "wrong_parent_tree":
            pdf.Root.StructTreeRoot.ParentTree.Nums[1][0] = owners[1]
        elif damage == "role_cycle":
            owner.S = Name("/TitleRole")
            pdf.Root.StructTreeRoot.RoleMap = Dictionary(
                TitleRole=Name("/OtherRole"), OtherRole=Name("/TitleRole")
            )
        elif damage == "namespace":
            pdf.Root.StructTreeRoot.Namespaces = Array([])

    assert not scan(title_pdf(tmp_path, change=change)).has_h1


def test_later_h1_does_not_clear_first_heading_rule(tmp_path):
    def change(pdf, group, owners):
        owners[0].S = Name.H2
        owners[2].S = Name.H1

    found = scan(title_pdf(tmp_path, change=change))
    assert found.has_h1 and not found.first_is_h1


def test_bounded_rolemap_chain_resolves_h1(tmp_path):
    def change(pdf, group, owners):
        owners[0].S = Name("/CourseTitle")
        pdf.Root.StructTreeRoot.RoleMap = Dictionary(
            CourseTitle=Name("/TitleRole"), TitleRole=Name.H1
        )

    assert scan(title_pdf(tmp_path, change=change)).has_h1


@pytest.mark.parametrize("ancestor", ["group", "document"])
@pytest.mark.parametrize("replacement", ["ActualText", "Alt"])
def test_ancestor_replacement_cannot_clear_heading_findings(
    tmp_path, ancestor, replacement
):
    def change(pdf, group, owners):
        owners[0].S = Name.H1
        parent = group if ancestor == "group" else group.P
        parent["/" + replacement] = String("Different accessible content")

    found = scan(title_pdf(tmp_path, change=change))
    assert not found.complete
    assert not found.has_h1 and not found.first_is_h1
    assert found.reason == "heading_replacement_content_ambiguous"


def test_complete_unicode_group_promotes_with_exact_saved_content(tmp_path):
    path = title_pdf(tmp_path)
    source = path.read_bytes()
    digest = sha256(source).hexdigest()
    found = scan(path)
    title = nominate_title(found)
    assert title.refs == ((0, 0), (0, 1)) and "\xe9" in title.text
    descriptors = inspect_pdf_edit_targets(source, digest)
    target = next(t for t in descriptors if t["can_set_heading_group"])
    result = create_pdf_edit_candidate(
        source,
        digest,
        dict(kind="heading_group", target_id=target["target_id"], level=1),
    )
    assert result.needs_review and result.source_sha256 == digest
    out = tmp_path / "saved.pdf"
    out.write_bytes(result.content)
    with pikepdf.open(out) as pdf:
        title = pdf.Root.StructTreeRoot.K[0].K[0]
        assert title.S == Name.H1
        assert [n.S for n in title.K] == [Name.Span, Name.Span]
        assert [n.K for n in title.K] == [0, 1]
        assert all(n.P == title for n in title.K)
        assert [n.objgen for n in pdf.Root.StructTreeRoot.ParentTree.Nums[1]][:2] == [
            n.objgen for n in title.K
        ]
    assert scan(out).has_h1 and scan(out).first_is_h1
    with fitz.open(path) as before, fitz.open(out) as after:
        assert before[0].get_text() == after[0].get_text()
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples
    with pytest.raises(PDFEditRefused, match="source_checksum_mismatch"):
        create_pdf_edit_candidate(
            result.content,
            digest,
            dict(kind="heading_group", target_id=target["target_id"], level=2),
        )


@pytest.mark.parametrize(
    "damage",
    [
        "body",
        "subtitle",
        "nested_heading",
        "link",
        "attribute",
        "replacement",
        "shared",
        "parent_replacement",
    ],
)
def test_compound_target_refuses_contamination_and_semantics(tmp_path, damage):
    def change(pdf, group, owners):
        if damage == "body":
            owners[2].P = group
            group.K.append(owners[2])
            pdf.Root.StructTreeRoot.K[0].K = Array([group])
        elif damage == "subtitle":
            pdf.pages[0].Contents = pdf.make_stream(
                pdf.pages[0]
                .Contents.read_bytes()
                .replace(b"/F1 24 Tf 45 678", b"/F1 12 Tf 45 678")
            )
        elif damage == "nested_heading":
            owners[1].S = Name.H2
        elif damage == "link":
            owners[1].S = Name.Link
        elif damage == "attribute":
            group.A = Dictionary(O=Name.Layout)
        elif damage == "replacement":
            owners[0].ActualText = String("Replacement")
        elif damage == "shared":
            group.K.append(owners[0])
        elif damage == "parent_replacement":
            group.P.ActualText = String("Replacement")

    path = title_pdf(tmp_path, change=change)
    found = scan(path)
    assert not found.complete or not any(
        t.eligible and t.role == "/Div" for t in found.targets
    )


def test_saved_serialization_damage_rejected(tmp_path, monkeypatch):
    import src.education.pdf_review_candidate as edit

    path = title_pdf(tmp_path)
    source = path.read_bytes()
    digest = sha256(source).hexdigest()
    target = next(
        t
        for t in inspect_pdf_edit_targets(source, digest)
        if t["can_set_heading_group"]
    )
    original = edit._serialize

    def damaged(pdf):
        pdf.pages[0].Contents = pdf.make_stream(
            pdf.pages[0].Contents.read_bytes().replace(b"invented", b"altered!")
        )
        return original(pdf)

    monkeypatch.setattr(edit, "_serialize", damaged)
    with pytest.raises(PDFEditRefused, match="saved_content_changed"):
        create_pdf_edit_candidate(
            source,
            digest,
            dict(kind="heading_group", target_id=target["target_id"], level=1),
        )


def test_existing_compound_heading_level_change_preserves_spans(tmp_path):
    path = title_pdf(tmp_path)
    content = path.read_bytes()

    def edit_group(content, level):
        digest = sha256(content).hexdigest()
        target = next(
            t
            for t in inspect_pdf_edit_targets(content, digest)
            if t["can_set_heading_group"]
        )
        return create_pdf_edit_candidate(
            content,
            digest,
            dict(kind="heading_group", target_id=target["target_id"], level=level),
        )

    first = edit_group(content, 3)
    second = edit_group(first.content, 2)
    with pikepdf.open(io.BytesIO(second.content)) as pdf:
        group = pdf.Root.StructTreeRoot.K[0].K[0]
        assert group.S == Name.H2 and all(n.S == Name.Span for n in group.K)
    with pytest.raises(PDFEditRefused, match="no_change"):
        edit_group(second.content, 2)


def test_mixed_size_leaf_does_not_nominate_body_as_title(tmp_path):
    def change(pdf, group, owners):
        # Keep all three marked occurrences in a single paragraph owner.
        group.S = Name.P
        group.K = Array([0, 1, 2])
        pdf.Root.StructTreeRoot.K[0].K = Array([group])
        pdf.Root.StructTreeRoot.ParentTree.Nums[1] = Array([group, group, group])

    found = scan(title_pdf(tmp_path, change=change))
    assert found.complete
    assert nominate_title(found) is None
