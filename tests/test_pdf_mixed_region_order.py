"""Independent invented sequences; planner agreement alone is insufficient."""

from hashlib import sha256
import io
import random

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.layout_order import Atom, LayoutAmbiguous, plan_order
from src.education.remediation.source_order import repair_page, OrderRefusal
from src.education.pdf_review_candidate import (
    create_pdf_edit_candidate,
    inspect_pdf_edit_targets,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from test_reading_order_mcid import texts

pytestmark = pytest.mark.unit


def atoms(columns, scale=1, footer=True):
    # Known column sequence is defined before shuffling; IDs are occurrences.
    result = [Atom("heading", (20, 10, columns * 150, 35), 25)]
    for col in range(columns):
        x = 20 + col * 150
        # Prose columns: paragraph tops differ between columns by more than
        # half a line. Tops that line up across columns, within that tolerance,
        # are a table (see the matrix tests).
        dy = 11 * col
        result.extend(
            [
                Atom(f"{col}:label", (x, 80 + dy, x + 80, 100 + dy), 20),
                Atom(f"{col}:body", (x, 112 + dy, x + 110, 165 + col * 2 + dy), 16, 3),
            ]
        )
    if footer:
        result.extend(
            [
                Atom("trailing", (20, 230, 120, 260), 15, 2),
                Atom(
                    "ordinal",
                    (columns * 150 - 20, 330, columns * 150, 345),
                    15,
                    trailing=True,
                ),
            ]
        )
    return [
        Atom(
            a.identity,
            tuple(x * scale for x in a.bbox),
            a.line_height * scale,
            a.lines,
            a.trailing,
        )
        for a in result
    ]


@pytest.mark.parametrize("columns", [1, 2, 3, 4])
@pytest.mark.parametrize("scale", [0.6, 1, 1.7])
def test_known_complete_columns_and_narrow_trailing_region(columns, scale):
    expected = atoms(columns, scale)
    shuffled = expected[:]
    random.Random(42).shuffle(shuffled)
    assert plan_order(shuffled, columns * 150 * scale).order == tuple(
        a.identity for a in expected
    )


def test_changing_columns_with_midpage_spanning_boundary():
    first = atoms(3, footer=False)
    second = [
        Atom(
            "lower:" + a.identity,
            tuple(x + (300 if i % 2 else 0) for i, x in enumerate(a.bbox)),
            a.line_height,
            a.lines,
        )
        for a in atoms(2, footer=False)
    ]
    expected = first + second
    assert plan_order(list(reversed(expected)), 500).order == tuple(
        a.identity for a in expected
    )


@pytest.mark.parametrize("damage", ["matrix", "overlap", "duplicate", "limit", "nan"])
def test_ambiguity_and_work_refusal(damage):
    sample = atoms(2)
    if damage == "matrix":
        sample = [
            Atom(f"{x}-{y}", (x, y, x + 60, y + 10), 10)
            for x in (20, 220)
            for y in (20, 60)
        ]
    elif damage == "overlap":
        sample = [Atom("a", (10, 10, 100, 50)), Atom("b", (20, 20, 100, 60))]
    elif damage == "duplicate":
        sample.append(sample[0])
    elif damage == "limit":
        sample = [Atom(i, (10, i * 20, 100, i * 20 + 10)) for i in range(2001)]
    elif damage == "nan":
        sample = [Atom("a", (0, 0, float("nan"), 20))]
    with pytest.raises(LayoutAmbiguous):
        plan_order(sample, 500)


# Per-column offset (points) for prose rows; more than half a 10pt line.
STAGGER = 9


def column_pdf(
    tmp_path,
    columns=4,
    reverse=True,
    repeat=False,
    stagger=True,
    *,
    offset=None,
    rows=2,
    skip=None,
):
    # offset overrides the per-column stagger (0 gives an exact table); skip
    # omits one (column, row) cell to model an empty table cell.
    if offset is None:
        offset = STAGGER if stagger else 0
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(columns * 150 + 40, 450))
    page.obj.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    document = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=page.obj)
    )
    root.K = Array([document])
    page.obj.Resources = Dictionary(
        Font=Dictionary(
            F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )
    )
    owners = []
    ops = []
    expected = []
    for col in range(columns):
        # Wrapped body is one MCID, preserving a complete indivisible owner.
        for row in range(rows):
            if (col, row) == skip:
                continue
            i = len(owners)
            x = 20 + 150 * col
            # Staggered paragraph tops keep this a prose column layout; without
            # the stagger the rows align across columns and it is a table.
            y = 360 - row * 35 - offset * col
            label = (
                "Repeated label"
                if repeat and row == 0
                else f"Column {col} segment {row}"
            )
            text = label + (f"\nContinued column {col}" if row else "")
            ops.append(
                f"/P <</MCID {i}>> BDC BT /F1 10 Tf {x} {y} Td ({label}) Tj "
                + (f"0 -14 Td (Continued column {col}) Tj " if row else "")
                + "ET EMC"
            )
            owners.append(
                pdf.make_indirect(
                    Dictionary(
                        Type=Name.StructElem, S=Name.P, P=document, Pg=page.obj, K=i
                    )
                )
            )
            expected.append(text)
    document.K = Array(list(reversed(owners)) if reverse else owners)
    root.ParentTree = Dictionary(Nums=Array([0, Array(owners)]))
    page.obj.Contents = pdf.make_stream("\n".join(reversed(ops)).encode())
    path = tmp_path / "invented-columns.pdf"
    pdf.save(path)
    return pdf, path, expected


@pytest.mark.parametrize("columns", [1, 2, 3, 4])
@pytest.mark.parametrize("repeat", [False, True])
def test_saved_source_occurrences_in_independent_expected_order(
    tmp_path, columns, repeat
):
    pdf, path, expected = column_pdf(tmp_path, columns, repeat=repeat)
    source = path.read_bytes()
    with fitz.open(path) as before:
        render = before[0].get_pixmap().samples
        plain = before[0].get_text()
    count, method = repair_page(pdf, 1)
    assert count and method == "regional_siblings"
    pdf.save(path)
    assert texts(path) == expected
    assert not ReadingOrderVerifier().check(str(path)).issues
    with fitz.open(path) as after:
        assert after[0].get_pixmap().samples == render and after[0].get_text() == plain
    with pikepdf.open(io.BytesIO(source)) as before, pikepdf.open(path) as after:
        assert (
            before.pages[0].Contents.read_bytes()
            == after.pages[0].Contents.read_bytes()
        )
        assert [n.K for n in after.Root.StructTreeRoot.ParentTree.Nums[1]] == list(
            range(2 * columns)
        )


def test_ambiguous_matrix_has_explicit_reviewer_permutation(tmp_path):
    pdf, path, _ = column_pdf(tmp_path, 2, stagger=False)
    pdf.pages[0].Contents = pdf.make_stream(
        pdf.pages[0]
        .Contents.read_bytes()
        .replace(b"0 -14 Td (Continued column 0) Tj ", b"")
        .replace(b"0 -14 Td (Continued column 1) Tj ", b"")
    )
    pdf.save(path)
    with pytest.raises(OrderRefusal, match="reading_order_ambiguous_matrix"):
        repair_page(pdf, 1)
    content = path.read_bytes()
    digest = sha256(content).hexdigest()
    parent = next(
        t for t in inspect_pdf_edit_targets(content, digest) if t["role"] == "Document"
    )
    result = create_pdf_edit_candidate(
        content,
        digest,
        {
            "kind": "order",
            "target_id": parent["target_id"],
            "children": list(reversed(parent["children"])),
        },
    )
    path.write_bytes(result.content)
    assert texts(path) == [
        "Column 0 segment 0",
        "Column 0 segment 1",
        "Column 1 segment 0",
        "Column 1 segment 1",
    ]
    assert result.needs_review
    assert ReadingOrderVerifier().check(str(path)).issues[0].review_only


@pytest.mark.parametrize("damage", ["rotation", "crop", "owner", "discontiguous"])
def test_source_refusals_preserve_in_memory_graph(tmp_path, damage):
    pdf, path, _ = column_pdf(tmp_path, 2)
    if damage == "rotation":
        pdf.pages[0].obj.Rotate = 90
    elif damage == "crop":
        pdf.pages[0].obj.CropBox = Array([1, 1, 330, 440])
    elif damage == "owner":
        pdf.Root.StructTreeRoot.ParentTree.Nums[1][0] = (
            pdf.Root.StructTreeRoot.ParentTree.Nums[1][1]
        )
    else:
        doc = pdf.Root.StructTreeRoot.K[0]
        old = list(doc.K)
        a = pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem, S=Name.Div, P=doc, K=Array([old[0], old[2]])
            )
        )
        b = pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem, S=Name.Div, P=doc, K=Array([old[1], old[3]])
            )
        )
        for group in (a, b):
            for node in group.K:
                node.P = group
        doc.K = Array([a, b])
    from src.education.pdf_review_candidate import _graph_digest

    original = _graph_digest(pdf)
    with pytest.raises(OrderRefusal):
        repair_page(pdf, 1)
    assert _graph_digest(pdf) == original


@pytest.mark.parametrize("columns", [2, 3, 4])
def test_generated_whole_run_owners_use_same_regional_plan(tmp_path, columns):
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2

    pdf, path, expected = column_pdf(tmp_path, columns)
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        pikepdf.unparse_content_stream(
            [
                op
                for op in pikepdf.parse_content_stream(page)
                if str(op.operator) not in {"BDC", "EMC"}
            ]
        )
    )
    del pdf.Root.StructTreeRoot
    del page.obj.StructParents
    pdf.save(path)
    with fitz.open(path) as document:
        tagger = ContentTaggerV2(pdf, document, order_generated_structure=True)
        tagger.tag_all_pages()
    pdf.save(path)
    assert [" ".join(t.split()) for t in texts(path)] == [
        " ".join(t.split()) for t in expected
    ]
    assert not ReadingOrderVerifier().check(str(path)).issues


def test_wrapped_table_cells_are_not_planned_column_by_column():
    # A 2x3 table: header row, then two rows whose cells wrap to three lines.
    # Reading it column by column would give h1, r1c1, r2c1, h2, r1c2, r2c2.
    sample = [
        Atom("h1", (20, 20, 120, 32), 12),
        Atom("h2", (220, 20, 320, 32), 12),
    ]
    for row, y in enumerate((60, 110), 1):
        for col, x in enumerate((20, 220), 1):
            sample.append(Atom(f"r{row}c{col}", (x, y, x + 100, y + 36), 12, 3))
    with pytest.raises(LayoutAmbiguous, match="reading_order_ambiguous_matrix"):
        plan_order(sample, 400)


@pytest.mark.parametrize("columns", [2, 3])
def test_wrapped_cell_table_tagged_as_paragraphs_is_not_reordered(tmp_path, columns):
    from src.education.pdf_review_candidate import _graph_digest

    pdf, path, _expected = column_pdf(tmp_path, columns, stagger=False)
    original = _graph_digest(pdf)
    with pytest.raises(OrderRefusal, match="reading_order_ambiguous_matrix"):
        repair_page(pdf, 1)
    assert _graph_digest(pdf) == original


def _table_atoms(skip=None, jitter=0.0):
    # Header row, then three rows of two-line cells (line height 12).
    sample = [
        Atom("h1", (72, 100, 250, 112), 12),
        Atom("h2", (300, 100, 500, 112), 12),
    ]
    for row, y in enumerate((120, 150, 180), 1):
        for col, x in enumerate((72, 300), 1):
            if (row, col) == skip:
                continue
            top = y + (jitter if col == 2 else 0)
            sample.append(Atom(f"r{row}c{col}", (x, top, x + 178, top + 24), 12, 2))
    return sample


@pytest.mark.parametrize(
    "variant", [{}, {"skip": (2, 2)}, {"jitter": 1.6}], ids=["exact", "empty", "1.6pt"]
)
def test_tolerant_table_guard_refuses_imperfect_tables(variant):
    with pytest.raises(LayoutAmbiguous, match="reading_order_ambiguous_matrix"):
        plan_order(_table_atoms(**variant), 612)


@pytest.mark.parametrize(
    "variant",
    [{"offset": 0}, {"offset": 0, "skip": (1, 2)}, {"offset": 1.6}],
    ids=["exact", "empty", "1.6pt"],
)
def test_imperfect_tables_tagged_as_paragraphs_are_not_reordered(tmp_path, variant):
    from src.education.pdf_review_candidate import _graph_digest

    pdf, _path, _expected = column_pdf(tmp_path, 2, rows=3, **variant)
    original = _graph_digest(pdf)
    with pytest.raises(OrderRefusal, match="reading_order_ambiguous_matrix"):
        repair_page(pdf, 1)
    assert _graph_digest(pdf) == original


@pytest.mark.parametrize("columns", [2, 3, 4])
def test_staggered_prose_columns_still_plan_and_repair(tmp_path, columns):
    pdf, path, expected = column_pdf(tmp_path, columns, rows=3)
    count, method = repair_page(pdf, 1)
    assert count and method == "regional_siblings"
    pdf.save(path)
    assert [" ".join(t.split()) for t in texts(path)] == [
        " ".join(t.split()) for t in expected
    ]
