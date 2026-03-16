"""Tests for table structure remediation."""

import json
import os
import tempfile

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name
from unittest.mock import patch, MagicMock

from src.education.remediation.table_tagger import (
    CellInfo,
    TableInfo,
    TableTagResult,
    TableTagger,
)
from src.education.remediation.confidence import ConfidenceCalculator, FixMethod

pytestmark = pytest.mark.unit


class TestCellInfo:
    """Test CellInfo dataclass."""

    def test_defaults(self):
        cell = CellInfo(row=0, col=0, text="Header", bbox=(0, 0, 100, 20))
        assert not cell.is_header
        assert cell.scope is None
        assert cell.col_span == 1
        assert cell.row_span == 1

    def test_header_cell(self):
        cell = CellInfo(
            row=0,
            col=0,
            text="Name",
            bbox=(0, 0, 100, 20),
            is_header=True,
            scope="Column",
        )
        assert cell.is_header
        assert cell.scope == "Column"


class TestTableInfo:
    """Test TableInfo dataclass."""

    def test_defaults(self):
        table = TableInfo(page_num=0, bbox=(72, 100, 540, 400))
        assert table.rows == 0
        assert table.cols == 0
        assert table.cells == []
        assert table.header_rows == 0
        assert not table.has_merged_cells


class TestTableTagResult:
    """Test TableTagResult dataclass."""

    def test_success_defaults(self):
        result = TableTagResult(success=True)
        assert result.tables_found == 0
        assert result.tables_tagged == 0
        assert result.confidence == 0.0
        assert result.needs_review is True

    def test_failed_result(self):
        result = TableTagResult(success=False, error="test error")
        assert not result.success
        assert result.error == "test error"


class TestHeaderIdentification:
    """Test heuristic header identification."""

    def test_first_row_all_non_empty_is_header(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=3, cols=3
        )
        table.cells = [
            CellInfo(0, 0, "Name", (0, 0, 100, 20)),
            CellInfo(0, 1, "Age", (100, 0, 200, 20)),
            CellInfo(0, 2, "City", (200, 0, 300, 20)),
            CellInfo(1, 0, "Alice", (0, 20, 100, 40)),
            CellInfo(1, 1, "30", (100, 20, 200, 40)),
            CellInfo(1, 2, "NYC", (200, 20, 300, 40)),
            CellInfo(2, 0, "Bob", (0, 40, 100, 60)),
            CellInfo(2, 1, "25", (100, 40, 200, 60)),
            CellInfo(2, 2, "LA", (200, 40, 300, 60)),
        ]
        tagger._identify_headers_heuristic(table)

        header_cells = [c for c in table.cells if c.is_header and c.row == 0]
        assert len(header_cells) == 3
        assert table.header_rows == 1
        # Cells not in column 0 keep Column scope; cell (0,0) may have
        # been overwritten to Row scope by the column-header heuristic
        non_col0 = [c for c in header_cells if c.col != 0]
        assert all(c.scope == "Column" for c in non_col0)

    def test_first_row_with_empty_cell_not_header(self):
        """Empty cell in first row prevents row-header detection, but
        column-header heuristic may still mark first column cells."""
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=2, cols=3
        )
        table.cells = [
            CellInfo(0, 0, "Name", (0, 0, 100, 20)),
            CellInfo(0, 1, "", (100, 0, 200, 20)),  # Empty cell
            CellInfo(0, 2, "City", (200, 0, 300, 20)),
            CellInfo(1, 0, "Alice", (0, 20, 100, 40)),
            CellInfo(1, 1, "30", (100, 20, 200, 40)),
            CellInfo(1, 2, "NYC", (200, 20, 300, 40)),
        ]
        tagger._identify_headers_heuristic(table)

        # Row-header detection should NOT fire (empty cell in first row)
        assert table.header_rows == 0
        # Column-scope header cells in row 0 should be 0
        col_scope_row0 = [
            c for c in table.cells
            if c.is_header and c.row == 0 and c.scope == "Column"
        ]
        assert len(col_scope_row0) == 0

    def test_first_column_header_detection(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=3, cols=3
        )
        table.cells = [
            CellInfo(0, 0, "Item", (0, 0, 100, 20)),
            CellInfo(0, 1, "100", (100, 0, 200, 20)),
            CellInfo(0, 2, "200", (200, 0, 300, 20)),
            CellInfo(1, 0, "Price", (0, 20, 100, 40)),
            CellInfo(1, 1, "50", (100, 20, 200, 40)),
            CellInfo(1, 2, "75", (200, 20, 300, 40)),
            CellInfo(2, 0, "Count", (0, 40, 100, 60)),
            CellInfo(2, 1, "10", (100, 40, 200, 60)),
            CellInfo(2, 2, "20", (200, 40, 300, 60)),
        ]
        tagger._identify_headers_heuristic(table)

        col0_cells = [c for c in table.cells if c.col == 0]
        assert all(c.is_header for c in col0_cells)
        assert table.header_cols == 1

    def test_empty_table_no_crash(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=0, cols=0
        )
        # Should not raise
        tagger._identify_headers_heuristic(table)
        assert table.header_rows == 0
        assert table.header_cols == 0

    def test_long_text_not_header(self):
        """First row with very long text should not be detected as header."""
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=2, cols=2
        )
        long_text = "A" * 60  # > 50 chars average
        table.cells = [
            CellInfo(0, 0, long_text, (0, 0, 250, 20)),
            CellInfo(0, 1, long_text, (250, 0, 500, 20)),
            CellInfo(1, 0, "data", (0, 20, 250, 40)),
            CellInfo(1, 1, "data", (250, 20, 500, 40)),
        ]
        tagger._identify_headers_heuristic(table)
        assert table.header_rows == 0


class TestComplexityAssessment:
    """Test table complexity scoring."""

    def test_simple_table_high_score(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0,
            bbox=(0, 0, 500, 200),
            rows=3,
            cols=3,
            header_rows=1,
        )
        table.cells = [
            CellInfo(0, 0, "H1", (0, 0, 100, 20), is_header=True),
            CellInfo(0, 1, "H2", (100, 0, 200, 20), is_header=True),
            CellInfo(0, 2, "H3", (200, 0, 300, 20), is_header=True),
            CellInfo(1, 0, "A", (0, 20, 100, 40)),
            CellInfo(1, 1, "B", (100, 20, 200, 40)),
            CellInfo(1, 2, "C", (200, 20, 300, 40)),
            CellInfo(2, 0, "D", (0, 40, 100, 60)),
            CellInfo(2, 1, "E", (100, 40, 200, 60)),
            CellInfo(2, 2, "F", (200, 40, 300, 60)),
        ]
        score = tagger._assess_complexity(table)
        assert score >= 0.8  # Simple regular table

    def test_merged_cells_lower_score(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0,
            bbox=(0, 0, 500, 200),
            rows=3,
            cols=3,
            header_rows=1,
            has_merged_cells=True,
        )
        table.cells = [CellInfo(0, 0, "H", (0, 0, 100, 20))]
        score = tagger._assess_complexity(table)
        assert score < 0.8  # Merged cells reduce confidence

    def test_no_headers_lower_score(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 200), rows=3, cols=3
        )
        table.cells = [CellInfo(0, 0, "A", (0, 0, 100, 20))]
        score = tagger._assess_complexity(table)
        assert score < 0.9  # No headers reduces confidence

    def test_large_table_lower_score(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0,
            bbox=(0, 0, 500, 2000),
            rows=25,
            cols=5,
            header_rows=1,
        )
        table.cells = [
            CellInfo(r, c, f"R{r}C{c}", (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20))
            for r in range(25)
            for c in range(5)
        ]
        score = tagger._assess_complexity(table)
        assert score < 0.9  # Large table reduces confidence

    def test_score_clamped(self):
        """Score should never go below 0.1 or above 1.0."""
        tagger = TableTagger(use_ai=False)
        # Worst case: merged, no headers, large, irregular
        table = TableInfo(
            page_num=0,
            bbox=(0, 0, 500, 2000),
            rows=25,
            cols=12,
            has_merged_cells=True,
        )
        table.cells = [CellInfo(0, 0, "A", (0, 0, 100, 20))]
        # Add irregular row counts
        table.cells.append(CellInfo(1, 0, "B", (0, 20, 100, 40)))
        score = tagger._assess_complexity(table)
        assert score >= 0.1


class TestBboxOverlap:
    """Test bounding box overlap computation."""

    def test_identical_boxes(self):
        assert TableTagger._bbox_overlap(
            (0, 0, 100, 100), (0, 0, 100, 100)
        ) == pytest.approx(1.0)

    def test_no_overlap(self):
        assert (
            TableTagger._bbox_overlap(
                (0, 0, 50, 50), (100, 100, 200, 200)
            )
            == 0.0
        )

    def test_partial_overlap(self):
        overlap = TableTagger._bbox_overlap(
            (0, 0, 100, 100), (50, 50, 150, 150)
        )
        assert 0.0 < overlap < 1.0

    def test_contained_box(self):
        overlap = TableTagger._bbox_overlap(
            (0, 0, 200, 200), (50, 50, 100, 100)
        )
        assert 0.0 < overlap < 1.0

    def test_zero_area_box(self):
        assert (
            TableTagger._bbox_overlap((0, 0, 0, 0), (0, 0, 100, 100)) == 0.0
        )


class TestAIHeaderParsing:
    """Test AI header response parsing."""

    def test_apply_ai_headers_valid(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=3, cols=3
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(3)
            for c in range(3)
        ]

        ai_content = json.dumps(
            {"header_rows": [0], "header_cols": [], "merged_cells": []}
        )
        tagger._apply_ai_headers(table, ai_content)

        row0 = [c for c in table.cells if c.row == 0]
        assert all(c.is_header for c in row0)
        assert table.header_rows == 1

    def test_apply_ai_headers_with_merged(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=3, cols=3
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(3)
            for c in range(3)
        ]

        ai_content = json.dumps(
            {
                "header_rows": [0],
                "header_cols": [],
                "merged_cells": [
                    {"row": 0, "col": 0, "col_span": 2, "row_span": 1}
                ],
            }
        )
        tagger._apply_ai_headers(table, ai_content)

        cell_00 = next(
            c for c in table.cells if c.row == 0 and c.col == 0
        )
        assert cell_00.col_span == 2
        assert table.has_merged_cells

    def test_apply_ai_headers_invalid_json(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=2, cols=2
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(2)
            for c in range(2)
        ]

        # Should not raise, just keep existing state
        tagger._apply_ai_headers(table, "not valid json")
        assert table.header_rows == 0

    def test_apply_ai_headers_with_code_fence(self):
        """AI response wrapped in code fences should still parse."""
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=2, cols=2
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(2)
            for c in range(2)
        ]

        ai_content = '```json\n{"header_rows": [0], "header_cols": [], "merged_cells": []}\n```'
        tagger._apply_ai_headers(table, ai_content)

        row0 = [c for c in table.cells if c.row == 0]
        assert all(c.is_header for c in row0)
        assert table.header_rows == 1

    def test_apply_ai_headers_column_headers(self):
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=3, cols=3
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(3)
            for c in range(3)
        ]

        ai_content = json.dumps(
            {"header_rows": [], "header_cols": [0], "merged_cells": []}
        )
        tagger._apply_ai_headers(table, ai_content)

        col0 = [c for c in table.cells if c.col == 0]
        assert all(c.is_header for c in col0)
        assert all(c.scope == "Row" for c in col0)
        assert table.header_cols == 1

    def test_apply_ai_headers_out_of_range(self):
        """Out-of-range row/col indices should be ignored safely."""
        tagger = TableTagger(use_ai=False)
        table = TableInfo(
            page_num=0, bbox=(0, 0, 500, 300), rows=2, cols=2
        )
        table.cells = [
            CellInfo(
                r,
                c,
                f"R{r}C{c}",
                (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            )
            for r in range(2)
            for c in range(2)
        ]

        ai_content = json.dumps(
            {"header_rows": [99], "header_cols": [-1], "merged_cells": []}
        )
        tagger._apply_ai_headers(table, ai_content)
        # Should not crash, and no headers set
        assert table.header_rows == 0


class TestTableTagging:
    """Test PDF structure tag generation."""

    def test_tag_simple_table(self):
        """Tag a simple 2x2 table in a PDF."""
        tagger = TableTagger(use_ai=False)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf = pikepdf.new()
            page = pikepdf.Page(
                pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.Page,
                            "/MediaBox": Array([0, 0, 612, 792]),
                        }
                    )
                )
            )
            pdf.pages.append(page)
            pdf.save(f.name)

            try:
                table = TableInfo(
                    page_num=0,
                    bbox=(72, 100, 540, 200),
                    rows=2,
                    cols=2,
                    header_rows=1,
                )
                table.cells = [
                    CellInfo(
                        0,
                        0,
                        "Name",
                        (72, 100, 300, 120),
                        is_header=True,
                        scope="Column",
                    ),
                    CellInfo(
                        0,
                        1,
                        "Value",
                        (300, 100, 540, 120),
                        is_header=True,
                        scope="Column",
                    ),
                    CellInfo(1, 0, "Alice", (72, 120, 300, 140)),
                    CellInfo(1, 1, "100", (300, 120, 540, 140)),
                ]

                tagged = tagger._apply_table_tags(f.name, [table])
                assert tagged == 1

                # Verify structure
                pdf2 = pikepdf.open(f.name)
                struct_root = pdf2.Root.get(Name.StructTreeRoot)
                assert struct_root is not None
                kids = struct_root[Name.K]
                assert len(kids) >= 1

                # First kid should be Table
                table_elem = kids[0]
                assert str(table_elem[Name.S]) == "/Table"
                pdf2.close()
            finally:
                os.unlink(f.name)

    def test_tag_creates_struct_tree_root(self):
        """Tagging should create StructTreeRoot if missing."""
        tagger = TableTagger(use_ai=False)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf = pikepdf.new()
            page = pikepdf.Page(
                pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.Page,
                            "/MediaBox": Array([0, 0, 612, 792]),
                        }
                    )
                )
            )
            pdf.pages.append(page)
            pdf.save(f.name)

            try:
                # Verify no StructTreeRoot initially
                pdf_check = pikepdf.open(f.name)
                assert Name.StructTreeRoot not in pdf_check.Root
                pdf_check.close()

                table = TableInfo(
                    page_num=0,
                    bbox=(72, 100, 540, 200),
                    rows=2,
                    cols=2,
                    header_rows=1,
                )
                table.cells = [
                    CellInfo(0, 0, "H1", (72, 100, 300, 120), is_header=True),
                    CellInfo(0, 1, "H2", (300, 100, 540, 120), is_header=True),
                    CellInfo(1, 0, "D1", (72, 120, 300, 140)),
                    CellInfo(1, 1, "D2", (300, 120, 540, 140)),
                ]

                tagged = tagger._apply_table_tags(f.name, [table])
                assert tagged == 1

                # Verify StructTreeRoot was created
                pdf2 = pikepdf.open(f.name)
                assert Name.StructTreeRoot in pdf2.Root
                assert Name.MarkInfo in pdf2.Root
                pdf2.close()
            finally:
                os.unlink(f.name)

    def test_tag_table_with_thead_tbody(self):
        """Verify THead and TBody are created properly."""
        tagger = TableTagger(use_ai=False)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf = pikepdf.new()
            page = pikepdf.Page(
                pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.Page,
                            "/MediaBox": Array([0, 0, 612, 792]),
                        }
                    )
                )
            )
            pdf.pages.append(page)
            pdf.save(f.name)

            try:
                table = TableInfo(
                    page_num=0,
                    bbox=(72, 100, 540, 200),
                    rows=3,
                    cols=2,
                    header_rows=1,
                )
                table.cells = [
                    CellInfo(0, 0, "Name", (72, 100, 300, 120), is_header=True, scope="Column"),
                    CellInfo(0, 1, "Score", (300, 100, 540, 120), is_header=True, scope="Column"),
                    CellInfo(1, 0, "Alice", (72, 120, 300, 140)),
                    CellInfo(1, 1, "95", (300, 120, 540, 140)),
                    CellInfo(2, 0, "Bob", (72, 140, 300, 160)),
                    CellInfo(2, 1, "87", (300, 140, 540, 160)),
                ]

                tagger._apply_table_tags(f.name, [table])

                pdf2 = pikepdf.open(f.name)
                table_elem = pdf2.Root[Name.StructTreeRoot][Name.K][0]

                # Table should have THead and TBody children
                table_kids = table_elem[Name.K]
                tag_names = [str(k[Name.S]) for k in table_kids]
                assert "/THead" in tag_names
                assert "/TBody" in tag_names

                # THead should have 1 TR with 2 TH cells
                thead = [k for k in table_kids if str(k[Name.S]) == "/THead"][0]
                thead_rows = thead[Name.K]
                assert len(thead_rows) == 1  # 1 header row
                header_row = thead_rows[0]
                assert str(header_row[Name.S]) == "/TR"
                header_cells = header_row[Name.K]
                assert len(header_cells) == 2
                assert all(str(c[Name.S]) == "/TH" for c in header_cells)

                # TBody should have 2 TR with 2 TD cells each
                tbody = [k for k in table_kids if str(k[Name.S]) == "/TBody"][0]
                tbody_rows = tbody[Name.K]
                assert len(tbody_rows) == 2  # 2 data rows
                for row in tbody_rows:
                    assert str(row[Name.S]) == "/TR"
                    data_cells = row[Name.K]
                    assert len(data_cells) == 2
                    assert all(str(c[Name.S]) == "/TD" for c in data_cells)

                pdf2.close()
            finally:
                os.unlink(f.name)


class TestTableTaggerConfidence:
    """Test confidence scoring for table tagging."""

    def test_simple_table_confidence(self):
        calc = ConfidenceCalculator()
        # Simple table: high signal
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.9, context_quality=0.7
        )
        assert 0.55 <= confidence <= 0.95

    def test_complex_table_confidence(self):
        calc = ConfidenceCalculator()
        # Complex table: low signal
        confidence = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.4, context_quality=0.7
        )
        assert 0.50 <= confidence <= 0.80

    def test_higher_signal_gives_higher_confidence(self):
        calc = ConfidenceCalculator()
        low = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.2, context_quality=0.5
        )
        high = calc.calculate(
            FixMethod.HEURISTIC, signal_strength=0.9, context_quality=0.5
        )
        assert high > low


class TestMissingDeps:
    """Test graceful handling of missing dependencies."""

    def test_missing_pymupdf(self):
        import src.education.remediation.table_tagger as tt_mod

        original = tt_mod.HAS_PYMUPDF
        tt_mod.HAS_PYMUPDF = False
        try:
            tagger = TableTagger(use_ai=False)
            result = tagger.tag_tables("/tmp/test.pdf")
            assert not result.success
            assert "PyMuPDF" in result.error
        finally:
            tt_mod.HAS_PYMUPDF = original

    def test_missing_pikepdf(self):
        import src.education.remediation.table_tagger as tt_mod

        original = tt_mod.HAS_PIKEPDF
        tt_mod.HAS_PIKEPDF = False
        try:
            tagger = TableTagger(use_ai=False)
            result = tagger.tag_tables("/tmp/test.pdf")
            assert not result.success
            assert "pikepdf" in result.error
        finally:
            tt_mod.HAS_PIKEPDF = original

    def test_missing_deps_single_table(self):
        import src.education.remediation.table_tagger as tt_mod

        original = tt_mod.HAS_PYMUPDF
        tt_mod.HAS_PYMUPDF = False
        try:
            tagger = TableTagger(use_ai=False)
            result = tagger.tag_table("/tmp/test.pdf", 0, (0, 0, 100, 100))
            assert not result.success
        finally:
            tt_mod.HAS_PYMUPDF = original
