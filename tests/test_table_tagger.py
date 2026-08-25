"""Tests for PDF table detection and the unbound-write safety gate."""

from types import SimpleNamespace

import pytest

from src.education.remediation.confidence import ConfidenceCalculator, FixMethod
from src.education.remediation.table_tagger import (
    TABLE_STRUCTURE_NOT_VERIFIED,
    CellInfo,
    TableInfo,
    TableTagResult,
    TableTagger,
)

pytestmark = pytest.mark.unit


def _grid(rows=3, columns=3):
    table = TableInfo(
        page_num=0,
        bbox=(0, 0, 500, 300),
        rows=rows,
        cols=columns,
    )
    table.cells = [
        CellInfo(
            row,
            column,
            f"R{row}C{column}",
            (column * 100, row * 20, (column + 1) * 100, (row + 1) * 20),
        )
        for row in range(rows)
        for column in range(columns)
    ]
    return table


def test_cell_and_table_defaults():
    cell = CellInfo(row=0, col=0, text="Header", bbox=(0, 0, 100, 20))
    table = TableInfo(page_num=0, bbox=(72, 100, 540, 400))

    assert cell.is_header is False
    assert cell.scope is None
    assert cell.col_span == 1
    assert cell.row_span == 1
    assert table.rows == 0
    assert table.cols == 0
    assert table.cells == []
    assert table.header_rows == 0
    assert table.header_evidence_verified is False


def test_result_defaults_and_error_code():
    result = TableTagResult(success=False, error=TABLE_STRUCTURE_NOT_VERIFIED)

    assert result.tables_found == 0
    assert result.tables_tagged == 0
    assert result.needs_review is True
    assert result.error == TABLE_STRUCTURE_NOT_VERIFIED


def _detected_header(table, *, names=None, cells=None, external=False):
    first_row = sorted(
        (cell for cell in table.cells if cell.row == 0), key=lambda cell: cell.col
    )
    return SimpleNamespace(
        header=SimpleNamespace(
            names=names or [cell.text for cell in first_row],
            cells=cells or [cell.bbox for cell in first_row],
            external=external,
        )
    )


def test_exact_detected_header_marks_only_first_row_with_column_scope():
    table = _grid()

    TableTagger(use_ai=False)._apply_detected_header_evidence(
        _detected_header(table), table
    )

    assert table.header_evidence_verified is True
    assert table.header_rows == 1
    assert table.header_cols == 0
    assert all(
        cell.is_header and cell.scope == "Column"
        for cell in table.cells
        if cell.row == 0
    )
    assert all(not cell.is_header for cell in table.cells if cell.row != 0)


@pytest.mark.parametrize(
    "detected",
    [
        lambda table: _detected_header(table, names=["wrong"] * table.cols),
        lambda table: _detected_header(
            table, cells=[(900, 900, 901, 901)] * table.cols
        ),
        lambda table: _detected_header(table, external=True),
    ],
)
def test_unmatched_or_external_header_evidence_is_rejected(detected):
    table = _grid(rows=2, columns=2)

    TableTagger(use_ai=False)._apply_detected_header_evidence(detected(table), table)

    assert table.header_evidence_verified is False
    assert table.header_rows == 0
    assert all(not cell.is_header and cell.scope is None for cell in table.cells)


def test_complexity_penalizes_merged_large_and_headerless_tables():
    tagger = TableTagger(use_ai=False)
    simple = _grid()
    simple.header_rows = 1
    complex_table = _grid(rows=25, columns=12)
    complex_table.has_merged_cells = True

    assert tagger._assess_complexity(simple) > tagger._assess_complexity(complex_table)
    assert tagger._assess_complexity(complex_table) >= 0.1


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ((0, 0, 100, 100), (0, 0, 100, 100), 1.0),
        ((0, 0, 50, 50), (100, 100, 200, 200), 0.0),
        ((0, 0, 0, 0), (0, 0, 100, 100), 0.0),
    ],
)
def test_bbox_overlap_boundaries(first, second, expected):
    assert TableTagger._bbox_overlap(first, second) == pytest.approx(expected)


def test_bbox_partial_overlap_is_fractional():
    overlap = TableTagger._bbox_overlap((0, 0, 100, 100), (50, 50, 150, 150))

    assert 0.0 < overlap < 1.0


def test_confidence_increases_with_signal_strength():
    calculator = ConfidenceCalculator()
    low = calculator.calculate(
        FixMethod.HEURISTIC, signal_strength=0.2, context_quality=0.5
    )
    high = calculator.calculate(
        FixMethod.HEURISTIC, signal_strength=0.9, context_quality=0.5
    )

    assert high > low


def test_missing_pymupdf_fails_closed():
    import src.education.remediation.table_tagger as table_tagger_module

    original = table_tagger_module.HAS_PYMUPDF
    table_tagger_module.HAS_PYMUPDF = False
    try:
        result = TableTagger(use_ai=False).tag_tables("unused.pdf")
    finally:
        table_tagger_module.HAS_PYMUPDF = original

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED


def test_no_unbound_structure_builder_remains_reachable():
    tagger = TableTagger(use_ai=False)

    assert hasattr(tagger, "_apply_table_tags") is False
    assert hasattr(tagger, "_build_table_element") is False
    assert hasattr(tagger, "_build_row") is False
