"""Real tagged mixed-content pages exercise table placement and text coverage."""

import hashlib

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit


def build_table_pdf(
    tmp_path, *, order=("heading", "table", "paragraph"), pages=1, defect=None
):
    """Return an unsaved (Pdf, Path); root.K contains one Document per page.

    Each Document.K contains heading, Table, paragraph in the requested order.
    Cells have multiline content whose visual line order differs from cell order.
    """
    pdf = pikepdf.new()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    documents, numbers = [], []
    for index in range(pages):
        page = pdf.add_blank_page(page_size=(500, 500))
        page.obj.StructParents = index
        page.obj.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(
                    Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
                )
            )
        )
        document = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=page.obj)
        )
        table = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Table, P=document)
        )
        row = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table))
        owners = [
            pdf.make_indirect(
                Dictionary(
                    Type=Name.StructElem,
                    S=Name.H1 if mcid == 0 else Name.P if mcid == 3 else Name.TD,
                    P=document if mcid in (0, 3) else row,
                    K=mcid,
                )
            )
            for mcid in range(4)
        ]
        row.K = Array(owners[1:3])
        table.K = row
        mapping = {"heading": owners[0], "table": table, "paragraph": owners[3]}
        document.K = Array([mapping[name] for name in order])
        documents.append(document)
        numbers.extend([index, Array(owners)])
        content = [
            (0, [(35, 440, f"Heading page {index}")]),
            (1, [(35, 330, "North entry"), (35, 310, "continued north")]),
            (2, [(260, 330, "South entry"), (260, 310, "continued south")]),
            (3, [(35, 210, f"Closing paragraph {index}")]),
        ]
        if defect == "ambiguous_text":
            content[2] = (2, [(260, 330, "North entry"), (260, 310, "continued north")])
        elif defect == "surrounding_repeat":
            content[3] = (3, [(35, 210, "North entry"), (35, 190, "continued north")])
        elif defect == "side_text":
            content[3] = (3, [(390, 320, "Side note")])
        elif defect == "empty_table":
            table.K = Array([])
        elif defect == "role_map":
            table.S = Name.CustomTable
            root.RoleMap = Dictionary(CustomTable=Name.Table)
        elif defect == "nested_table":
            row.S = Name.Table
        elif defect == "replacement":
            owners[1].ActualText = "Alternate cell text"
        elif defect == "missing_page":
            del document.Pg
        elif defect is not None:
            raise ValueError(defect)
        page.obj.Contents = pdf.make_stream(
            "\n".join(
                f"/P <</MCID {mcid}>> BDC "
                + " ".join(
                    f"BT /F1 12 Tf {x} {y} Td ({label}) Tj ET" for x, y, label in lines
                )
                + " EMC"
                for mcid, lines in content
            ).encode()
        )
    root.K = Array(documents)
    root.ParentTree = Dictionary(Nums=Array(numbers))
    return pdf, tmp_path / "table-reading-order.pdf"


def test_mixed_table_page_compares_surrounding_text_and_preserves_source(tmp_path):
    pdf, path = build_table_pdf(tmp_path)
    pdf.save(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(path))
    assert result.issues == []
    assert result.has_structure_tree
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.parametrize(
    "order",
    [
        ("paragraph", "table", "heading"),
        ("heading", "paragraph", "table"),
        ("table", "heading", "paragraph"),
        ("heading", "table"),
        ("heading", "table", "paragraph", "paragraph"),
    ],
)
def test_table_page_does_not_hide_moved_missing_or_repeated_text(tmp_path, order):
    pdf, path = build_table_pdf(tmp_path, order=order)
    pdf.save(path)
    before = path.read_bytes()
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues
    assert result.compliance_score < 100
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "defect",
    [
        "ambiguous_text",
        "surrounding_repeat",
        "side_text",
        "empty_table",
        "nested_table",
        "replacement",
    ],
)
def test_unsupported_table_layout_is_incomplete_and_visible(tmp_path, defect):
    pdf, path = build_table_pdf(tmp_path, defect=defect)
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues
    assert result.compliance_score < 100


@pytest.mark.parametrize("defect", [None, "role_map", "missing_page"])
def test_table_page_ownership_and_multipage_findings(tmp_path, defect):
    pdf, path = build_table_pdf(tmp_path, pages=2, defect=defect)
    children = pdf.Root.StructTreeRoot.K[1].K
    pdf.Root.StructTreeRoot.K[1].K = Array([children[0], children[2], children[1]])
    pdf.save(path)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(path))
    assert result.pages_analyzed == 2
    assert [issue.page_number for issue in result.issues] == [2]


def test_empty_table_without_any_text_is_still_incomplete(tmp_path):
    pdf, path = build_table_pdf(tmp_path, order=("table",), defect="empty_table")
    pdf.pages[0].Contents = pdf.make_stream(b"")
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table_empty"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


def test_unsupported_second_page_does_not_mark_first_page_incomplete(tmp_path):
    pdf, path = build_table_pdf(tmp_path, pages=2)
    pdf.Root.StructTreeRoot.K[1].K[1].K.S = Name.Table
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert ReadingOrderVerifier().check(str(path), max_pages=1).issues == []
    result = ReadingOrderVerifier().check(str(path))
    assert [issue.page_number for issue in result.issues] == [2]


def test_one_table_spanning_pages_keeps_page_local_placement(tmp_path):
    pdf, path = build_table_pdf(tmp_path, pages=2)
    root = pdf.Root.StructTreeRoot
    first, second = root.K
    table = first.K[1]
    rows = [first.K[1].K, second.K[1].K]
    for index, row in enumerate(rows):
        row.Pg = pdf.pages[index].obj
        row.P = table
    table.K = Array(rows)
    root.K = Array([first.K[0], second.K[0], table, first.K[2], second.K[2]])
    for child in root.K:
        child.P = root
    first.K[0].Pg = first.K[2].Pg = pdf.pages[0].obj
    second.K[0].Pg = second.K[2].Pg = pdf.pages[1].obj
    pdf.save(path)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(path))
    assert result.issues == []


@pytest.mark.parametrize("swapped", [False, True])
def test_multiple_tables_keep_distinct_positions(tmp_path, swapped):
    pdf, path = build_table_pdf(tmp_path)
    root = pdf.Root.StructTreeRoot
    document = root.K[0]
    table = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Table, P=document)
    )
    row = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table))
    cells = []
    extra_content = []
    for mcid, x, label in [(4, 35, "West entry"), (5, 260, "East entry")]:
        owner = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.TD, P=row, K=mcid)
        )
        root.ParentTree.Nums[1].append(owner)
        cells.append(owner)
        extra_content.append(
            f"/P <</MCID {mcid}>> BDC BT /F1 12 Tf {x} 260 Td ({label}) Tj ET EMC"
        )
    row.K = Array(cells)
    table.K = row
    heading, first_table, paragraph = document.K
    tables = [table, first_table] if swapped else [first_table, table]
    document.K = Array([heading, *tables, paragraph])
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        page.Contents.read_bytes() + b"\n" + "\n".join(extra_content).encode()
    )
    pdf.save(path)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(path))
    assert bool(result.issues) is swapped


def test_cells_are_not_sorted_as_surrounding_paragraphs(tmp_path):
    pdf, path = build_table_pdf(tmp_path)
    row = pdf.Root.StructTreeRoot.K[0].K[1].K
    row.K = Array(list(reversed(row.K)))
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert ReadingOrderVerifier().check(str(path)).issues == []


def test_two_surrounding_columns_are_explicitly_unsupported(tmp_path):
    pdf, path = build_table_pdf(tmp_path, order=("table", "heading", "paragraph"))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        page.Contents.read_bytes()
        .replace(b"35 440 Td", b"35 210 Td")
        .replace(b"35 210 Td (Closing", b"260 210 Td (Closing")
    )
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table_columns"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


def test_table_alt_uses_descendant_page_ownership(tmp_path):
    pdf, path = build_table_pdf(tmp_path, defect="missing_page")
    pdf.Root.StructTreeRoot.K[0].K[1].Alt = pikepdf.String(
        "Meaningful alternate table description"
    )
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table_replacement"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


def test_table_role_map_requires_pdf_name(tmp_path):
    pdf, path = build_table_pdf(tmp_path, defect="role_map")
    pdf.Root.StructTreeRoot.RoleMap.CustomTable = pikepdf.String("/Table")
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table_role_map"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


@pytest.mark.parametrize("mapped", [False, True])
def test_table_structure_role_requires_pdf_name(tmp_path, mapped):
    pdf, path = build_table_pdf(tmp_path, defect="role_map" if mapped else None)
    pdf.Root.StructTreeRoot.K[0].K[1].S = pikepdf.String(
        "/CustomTable" if mapped else "/Table"
    )
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table_role"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues
