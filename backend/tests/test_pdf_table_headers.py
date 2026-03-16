"""
Tests for Improved Table Header Detection (Task 14)

Tests cover:
- TableCell model
- PDFTable model
- TableHeaderDetectionResult model
- TableFix model
- Heuristic header detection methods
- Table extraction and analysis
"""

import pytest

from src.education.pdf_processor import (
    PDFProcessor,
    TableCell,
    PDFTable,
    TableHeaderDetectionResult,
    TableFix,
)


class TestTableCellModel:
    """Test TableCell Pydantic model."""

    def test_table_cell_creation(self):
        """Test creating a basic table cell."""
        cell = TableCell(
            row=0,
            col=0,
            text="Header",
            is_bold=True,
            has_background=False,
            font_size=12.0,
        )

        assert cell.row == 0
        assert cell.col == 0
        assert cell.text == "Header"
        assert cell.is_bold is True
        assert cell.has_background is False
        assert cell.font_size == 12.0

    def test_table_cell_defaults(self):
        """Test TableCell default values."""
        cell = TableCell(row=1, col=2, text="Data")

        assert cell.is_bold is False
        assert cell.has_background is False
        assert cell.font_size is None
        assert cell.font_name is None
        assert cell.bbox is None

    def test_table_cell_with_bbox(self):
        """Test TableCell with bounding box."""
        cell = TableCell(
            row=0,
            col=0,
            text="Cell",
            bbox=(100.0, 200.0, 150.0, 220.0),
        )

        assert cell.bbox == (100.0, 200.0, 150.0, 220.0)


class TestPDFTableModel:
    """Test PDFTable Pydantic model."""

    def test_pdf_table_creation(self):
        """Test creating a PDFTable with cells."""
        cells = [
            TableCell(row=0, col=0, text="Name", is_bold=True),
            TableCell(row=0, col=1, text="Value", is_bold=True),
            TableCell(row=1, col=0, text="Item 1"),
            TableCell(row=1, col=1, text="100"),
        ]

        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=cells,
            row_count=2,
            col_count=2,
        )

        assert table.page_number == 1
        assert table.table_index == 0
        assert len(table.cells) == 4
        assert table.row_count == 2
        assert table.col_count == 2

    def test_pdf_table_defaults(self):
        """Test PDFTable default values."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[],
            row_count=0,
            col_count=0,
        )

        assert table.has_header_row is False
        assert table.has_header_column is False
        assert table.bbox is None


class TestTableHeaderDetectionResultModel:
    """Test TableHeaderDetectionResult model."""

    def test_header_detection_result_creation(self):
        """Test creating header detection result."""
        headers = [
            TableCell(row=0, col=0, text="ID", is_bold=True),
            TableCell(row=0, col=1, text="Name", is_bold=True),
        ]

        result = TableHeaderDetectionResult(
            detected_headers=headers,
            header_row_indices=[0],
            detection_method="bold",
            confidence=0.9,
        )

        assert len(result.detected_headers) == 2
        assert result.header_row_indices == [0]
        assert result.detection_method == "bold"
        assert result.confidence == 0.9

    def test_header_detection_result_defaults(self):
        """Test default values for header detection result."""
        result = TableHeaderDetectionResult(
            detected_headers=[],
            detection_method="none",
            confidence=0.0,
        )

        assert result.header_row_indices == []
        assert result.header_col_indices == []
        assert result.has_th_tags is False


class TestTableFixModel:
    """Test TableFix model."""

    def test_table_fix_creation(self):
        """Test creating a table fix recommendation."""
        fix = TableFix(
            table_location="Page 1, Table 1",
            detected_headers=["Name", "Date", "Amount"],
            recommended_scope="col",
            fix_instructions="Add TH tags with scope='col' to row 1",
        )

        assert fix.table_location == "Page 1, Table 1"
        assert len(fix.detected_headers) == 3
        assert fix.recommended_scope == "col"
        assert fix.priority == "high"
        assert fix.wcag_criterion == "1.3.1"

    def test_table_fix_with_priority(self):
        """Test table fix with custom priority."""
        fix = TableFix(
            table_location="Page 2, Table 1",
            detected_headers=["ID"],
            recommended_scope="col",
            fix_instructions="Add TH tags",
            priority="critical",
        )

        assert fix.priority == "critical"


class TestHeuristicHeaderDetection:
    """Test _detect_table_headers_heuristic method."""

    @pytest.fixture
    def processor(self):
        """Create PDFProcessor instance for testing."""
        return PDFProcessor()

    def test_detect_headers_empty_cells(self, processor):
        """Test header detection with empty cell list."""
        result = processor._detect_table_headers_heuristic([])

        assert result.detected_headers == []
        assert result.detection_method == "none"
        assert result.confidence == 0.0

    def test_detect_headers_by_bold(self, processor):
        """Test detecting headers by bold text."""
        cells = [
            # Header row (bold)
            TableCell(row=0, col=0, text="Name", is_bold=True),
            TableCell(row=0, col=1, text="Date", is_bold=True),
            TableCell(row=0, col=2, text="Amount", is_bold=True),
            # Data rows (not bold)
            TableCell(row=1, col=0, text="John"),
            TableCell(row=1, col=1, text="2024-01-01"),
            TableCell(row=1, col=2, text="100"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        assert len(result.detected_headers) == 3
        assert result.header_row_indices == [0]
        assert result.detection_method == "bold"
        assert result.confidence >= 0.9

    def test_detect_headers_by_background(self, processor):
        """Test detecting headers by background shading."""
        cells = [
            # Header row (with background)
            TableCell(row=0, col=0, text="ID", has_background=True),
            TableCell(row=0, col=1, text="Status", has_background=True),
            # Data rows (no background)
            TableCell(row=1, col=0, text="001"),
            TableCell(row=1, col=1, text="Active"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        assert len(result.detected_headers) == 2
        assert result.detection_method == "background"
        assert result.confidence >= 0.8

    def test_detect_headers_by_font_size(self, processor):
        """Test detecting headers by larger font size."""
        cells = [
            # Header row (larger font)
            TableCell(row=0, col=0, text="Category", font_size=14.0),
            TableCell(row=0, col=1, text="Value", font_size=14.0),
            # Data rows (smaller font)
            TableCell(row=1, col=0, text="A", font_size=11.0),
            TableCell(row=1, col=1, text="10", font_size=11.0),
            TableCell(row=2, col=0, text="B", font_size=11.0),
            TableCell(row=2, col=1, text="20", font_size=11.0),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        assert len(result.detected_headers) == 2
        assert result.detection_method == "font_size"
        assert result.confidence >= 0.7

    def test_detect_headers_by_keywords(self, processor):
        """Test detecting headers by header keywords."""
        cells = [
            # Header row (contains keywords)
            TableCell(row=0, col=0, text="ID"),
            TableCell(row=0, col=1, text="Name"),
            TableCell(row=0, col=2, text="Date"),
            TableCell(row=0, col=3, text="Total"),
            # Data rows
            TableCell(row=1, col=0, text="001"),
            TableCell(row=1, col=1, text="Test Item"),
            TableCell(row=1, col=2, text="2024-01-15"),
            TableCell(row=1, col=3, text="$500"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        assert len(result.detected_headers) == 4
        assert result.detection_method == "keywords"
        assert result.confidence >= 0.7

    def test_detect_headers_no_headers_found(self, processor):
        """Test when no headers can be detected."""
        cells = [
            # All cells look like data
            TableCell(row=0, col=0, text="Alpha"),
            TableCell(row=0, col=1, text="100"),
            TableCell(row=1, col=0, text="Beta"),
            TableCell(row=1, col=1, text="200"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Should either find no headers or detect with low confidence
        assert result.confidence < 0.8 or len(result.detected_headers) == 0

    def test_detect_row_headers_by_bold_column(self, processor):
        """Test detecting row headers by bold first column."""
        cells = [
            # First column is bold (row headers)
            TableCell(row=0, col=0, text="Category", is_bold=True),
            TableCell(row=0, col=1, text="Q1"),
            TableCell(row=0, col=2, text="Q2"),
            TableCell(row=1, col=0, text="Sales", is_bold=True),
            TableCell(row=1, col=1, text="100"),
            TableCell(row=1, col=2, text="150"),
            TableCell(row=2, col=0, text="Costs", is_bold=True),
            TableCell(row=2, col=1, text="50"),
            TableCell(row=2, col=2, text="75"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Should detect either the bold first row or the bold column
        assert len(result.detected_headers) > 0 or result.header_col_indices == [0]


class TestGenerateTableFix:
    """Test _generate_table_fix method."""

    @pytest.fixture
    def processor(self):
        """Create PDFProcessor instance for testing."""
        return PDFProcessor()

    def test_generate_fix_for_column_headers(self, processor):
        """Test fix generation for column headers."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[
                TableCell(row=0, col=0, text="Name", is_bold=True),
                TableCell(row=0, col=1, text="Value", is_bold=True),
                TableCell(row=1, col=0, text="Item"),
                TableCell(row=1, col=1, text="100"),
            ],
            row_count=2,
            col_count=2,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=table.cells[:2],
            header_row_indices=[0],
            detection_method="bold",
            confidence=0.9,
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.table_location == "Page 1, Table 1"
        assert fix.recommended_scope == "col"
        assert "scope='col'" in fix.fix_instructions
        assert fix.wcag_criterion == "1.3.1"

    def test_generate_fix_for_row_headers(self, processor):
        """Test fix generation for row headers."""
        table = PDFTable(
            page_number=2,
            table_index=1,
            cells=[
                TableCell(row=0, col=0, text="Q1"),
                TableCell(row=0, col=1, text="100"),
                TableCell(row=1, col=0, text="Q2"),
                TableCell(row=1, col=1, text="200"),
            ],
            row_count=2,
            col_count=2,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=[],
            header_col_indices=[0],
            detection_method="bold_column",
            confidence=0.7,
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.recommended_scope == "row"
        assert "scope='row'" in fix.fix_instructions

    def test_generate_fix_for_both_headers(self, processor):
        """Test fix generation for both row and column headers."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[],
            row_count=3,
            col_count=3,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=[TableCell(row=0, col=0, text="Test")],
            header_row_indices=[0],
            header_col_indices=[0],
            detection_method="bold",
            confidence=0.85,
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.recommended_scope == "both"
        assert "scope='col'" in fix.fix_instructions
        assert "scope='row'" in fix.fix_instructions

    def test_generate_fix_no_headers_detected(self, processor):
        """Test fix generation when no headers detected."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[],
            row_count=5,
            col_count=3,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=[],
            detection_method="none",
            confidence=0.0,
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.recommended_scope is None
        assert "Manually identify" in fix.fix_instructions

    def test_generate_fix_priority_large_table(self, processor):
        """Test that large tables get critical priority."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[],
            row_count=15,  # Large table
            col_count=6,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=[TableCell(row=0, col=0, text="Header")],
            header_row_indices=[0],
            detection_method="bold",
            confidence=0.9,
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.priority == "critical"

    def test_generate_fix_priority_low_confidence(self, processor):
        """Test that low confidence detection gets medium priority."""
        table = PDFTable(
            page_number=1,
            table_index=0,
            cells=[],
            row_count=3,
            col_count=2,
        )

        header_detection = TableHeaderDetectionResult(
            detected_headers=[TableCell(row=0, col=0, text="Maybe Header")],
            header_row_indices=[0],
            detection_method="keywords",
            confidence=0.4,  # Low confidence
        )

        fix = processor._generate_table_fix(table, header_detection)

        assert fix.priority == "medium"


class TestCreateTableFromRows:
    """Test _create_table_from_rows method."""

    @pytest.fixture
    def processor(self):
        """Create PDFProcessor instance for testing."""
        return PDFProcessor()

    def test_create_table_empty_rows(self, processor):
        """Test creating table from empty rows."""
        result = processor._create_table_from_rows(1, 0, [])
        assert result is None

    def test_create_table_single_row(self, processor):
        """Test creating table from single row (should fail)."""
        rows = [
            (100.0, [{"text": "A", "bbox": (10, 100, 30, 120), "flags": 0, "size": 12}])
        ]
        result = processor._create_table_from_rows(1, 0, rows)
        assert result is None

    def test_create_table_valid_rows(self, processor):
        """Test creating table from valid rows."""
        rows = [
            (
                100.0,
                [
                    {
                        "text": "Name",
                        "bbox": (10, 100, 50, 120),
                        "flags": 16,
                        "size": 12,
                        "font": "Arial",
                    },
                    {
                        "text": "Value",
                        "bbox": (100, 100, 150, 120),
                        "flags": 16,
                        "size": 12,
                        "font": "Arial",
                    },
                ],
            ),
            (
                140.0,
                [
                    {
                        "text": "Item 1",
                        "bbox": (10, 140, 60, 160),
                        "flags": 0,
                        "size": 12,
                        "font": "Arial",
                    },
                    {
                        "text": "100",
                        "bbox": (100, 140, 130, 160),
                        "flags": 0,
                        "size": 12,
                        "font": "Arial",
                    },
                ],
            ),
        ]

        table = processor._create_table_from_rows(1, 0, rows)

        assert table is not None
        assert table.page_number == 1
        assert table.table_index == 0
        assert table.row_count == 2
        assert len(table.cells) == 4
        # First row should be bold (flags=16)
        first_row_cells = [c for c in table.cells if c.row == 0]
        assert all(c.is_bold for c in first_row_cells)


class TestKeywordDetection:
    """Test keyword-based header detection."""

    @pytest.fixture
    def processor(self):
        """Create PDFProcessor instance for testing."""
        return PDFProcessor()

    def test_common_header_keywords(self, processor):
        """Test that common header keywords are detected."""
        # Test various common keywords
        keyword_tests = [
            ["ID", "Name", "Date"],
            ["Total", "Amount", "Price"],
            ["Category", "Type", "Status"],
            ["Email", "Phone", "Address"],
        ]

        for keywords in keyword_tests:
            cells = [
                TableCell(row=0, col=i, text=kw) for i, kw in enumerate(keywords)
            ] + [
                TableCell(row=1, col=i, text=f"Value {i}") for i in range(len(keywords))
            ]

            result = processor._detect_table_headers_heuristic(cells)

            assert (
                result.detection_method == "keywords"
            ), f"Failed for keywords: {keywords}"
            assert len(result.detected_headers) == len(keywords)

    def test_partial_keyword_match(self, processor):
        """Test that partial keyword matches work."""
        cells = [
            TableCell(row=0, col=0, text="Customer Name"),  # Contains "name"
            TableCell(row=0, col=1, text="Order Date"),  # Contains "date"
            TableCell(row=0, col=2, text="Random"),
            TableCell(row=1, col=0, text="John Doe"),
            TableCell(row=1, col=1, text="2024-01-15"),
            TableCell(row=1, col=2, text="XYZ"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Should detect keywords in phrases
        assert len(result.detected_headers) > 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def processor(self):
        """Create PDFProcessor instance for testing."""
        return PDFProcessor()

    def test_table_with_empty_cells(self, processor):
        """Test handling of empty cells in table."""
        cells = [
            TableCell(row=0, col=0, text="Name", is_bold=True),
            TableCell(row=0, col=1, text="", is_bold=True),  # Empty header
            TableCell(row=1, col=0, text="Item"),
            TableCell(row=1, col=1, text="Value"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Should still detect headers despite empty cell
        assert result.detection_method == "bold"

    def test_single_column_table(self, processor):
        """Test header detection for single column table."""
        cells = [
            TableCell(row=0, col=0, text="Items", is_bold=True),
            TableCell(row=1, col=0, text="Apple"),
            TableCell(row=2, col=0, text="Orange"),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        assert len(result.detected_headers) == 1
        assert result.detection_method == "bold"

    def test_single_row_table(self, processor):
        """Test header detection for single row (no data rows)."""
        cells = [
            TableCell(row=0, col=0, text="Col1", is_bold=True),
            TableCell(row=0, col=1, text="Col2", is_bold=True),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Single row could be headers but no data to confirm
        assert len(result.detected_headers) == 2

    def test_all_cells_bold(self, processor):
        """Test when all cells are bold (not just headers)."""
        cells = [
            TableCell(row=0, col=0, text="A", is_bold=True),
            TableCell(row=0, col=1, text="B", is_bold=True),
            TableCell(row=1, col=0, text="C", is_bold=True),
            TableCell(row=1, col=1, text="D", is_bold=True),
        ]

        result = processor._detect_table_headers_heuristic(cells)

        # Should still detect first row as headers when all are bold
        assert result.header_row_indices == [0]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
