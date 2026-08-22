"""Table structure remediation for PDF accessibility.

Detects table boundaries and structure in PDF documents, then creates
proper structure tags (THead, TBody, TR, TH, TD) with Scope attributes
for PDF/UA compliance.

WCAG 1.3.1 (Info and Relationships): Information, structure, and
relationships conveyed through presentation can be programmatically
determined.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False

from .confidence import ConfidenceCalculator, FixMethod

logger = logging.getLogger(__name__)


@dataclass
class CellInfo:
    """Information about a single table cell."""

    row: int
    col: int
    text: str
    bbox: Tuple[float, float, float, float]
    is_header: bool = False
    scope: Optional[str] = None  # "Column", "Row", or None
    col_span: int = 1
    row_span: int = 1
    font_size: float = 0.0
    is_bold: bool = False


@dataclass
class TableInfo:
    """Detected table information."""

    page_num: int
    bbox: Tuple[float, float, float, float]
    rows: int = 0
    cols: int = 0
    cells: List[CellInfo] = field(default_factory=list)
    header_rows: int = 0
    header_cols: int = 0
    has_merged_cells: bool = False


@dataclass
class TableTagResult:
    """Result of table tagging operation."""

    success: bool
    tables_found: int = 0
    tables_tagged: int = 0
    total_cells: int = 0
    header_cells: int = 0
    confidence: float = 0.0
    needs_review: bool = True
    error: Optional[str] = None


class TableTagger:
    """Detect and tag table structures in PDF documents.

    Uses PyMuPDF's built-in table detection (page.find_tables()) to identify
    table boundaries and cell structure, then optionally uses AI vision to
    confirm header identification.
    """

    def __init__(
        self,
        use_ai: bool = True,
        *,
        ai_client: Any = None,
        allow_legacy_provider_manager: bool = False,
    ) -> None:
        self._confidence_calc = ConfidenceCalculator()
        self._use_ai = use_ai
        self._ai_client = ai_client
        self._allow_legacy_provider_manager = allow_legacy_provider_manager

    def tag_tables(self, pdf_path: str) -> TableTagResult:
        """Detect and tag all tables in a PDF.

        Args:
            pdf_path: Path to the PDF file (modified in place).

        Returns:
            TableTagResult with details of changes made.
        """
        if not HAS_PYMUPDF:
            return TableTagResult(success=False, error="PyMuPDF required")
        if not HAS_PIKEPDF:
            return TableTagResult(success=False, error="pikepdf required")

        try:
            # 1. Detect tables using PyMuPDF
            doc = fitz.open(pdf_path)
            try:
                all_tables = self._detect_tables(doc)
            finally:
                doc.close()

            if not all_tables:
                return TableTagResult(
                    success=True, tables_found=0, confidence=1.0, needs_review=False
                )

            # 2. Identify headers using heuristics
            for table in all_tables:
                self._identify_headers_heuristic(table)

            # 3. Optionally use AI vision for header confirmation
            if self._use_ai:
                self._confirm_headers_with_ai(pdf_path, all_tables)

            # 4. Apply structure tags via pikepdf
            tagged_count = self._apply_table_tags(pdf_path, all_tables)

            # 5. Calculate confidence
            total_cells = sum(len(t.cells) for t in all_tables)
            header_cells = sum(
                sum(1 for c in t.cells if c.is_header) for t in all_tables
            )

            # Simple tables (regular grid, clear headers) get higher confidence
            complexity_scores = [self._assess_complexity(t) for t in all_tables]
            avg_complexity = (
                sum(complexity_scores) / len(complexity_scores)
                if complexity_scores
                else 0.5
            )

            confidence = self._confidence_calc.calculate(
                FixMethod.HEURISTIC,
                signal_strength=avg_complexity,
                context_quality=0.7,
            )

            return TableTagResult(
                success=True,
                tables_found=len(all_tables),
                tables_tagged=tagged_count,
                total_cells=total_cells,
                header_cells=header_cells,
                confidence=confidence,
                needs_review=self._confidence_calc.needs_review(confidence),
            )

        except Exception as exc:
            logger.error("Table tagging failed: %s", exc, exc_info=True)
            return TableTagResult(success=False, error=str(exc))

    def tag_table(
        self,
        pdf_path: str,
        page_num: int,
        table_bbox: Tuple[float, float, float, float],
    ) -> TableTagResult:
        """Tag a single table identified by page and bounding box.

        Args:
            pdf_path: Path to PDF file.
            page_num: 0-indexed page number.
            table_bbox: Bounding box of the table.

        Returns:
            TableTagResult for this single table.
        """
        if not HAS_PYMUPDF or not HAS_PIKEPDF:
            return TableTagResult(success=False, error="PyMuPDF and pikepdf required")

        try:
            doc = fitz.open(pdf_path)
            try:
                if page_num >= len(doc):
                    return TableTagResult(
                        success=False, error=f"Page {page_num} out of range"
                    )

                page = doc[page_num]
                tables = page.find_tables()

                # Find the table matching the bbox
                target_table = None
                for table in tables.tables:
                    if self._bbox_overlap(table.bbox, table_bbox) > 0.5:
                        target_table = table
                        break

                if target_table is None:
                    return TableTagResult(
                        success=False,
                        error="Table not found at specified location",
                    )

                table_info = self._parse_table(target_table, page_num)
            finally:
                doc.close()

            self._identify_headers_heuristic(table_info)
            tagged = self._apply_table_tags(pdf_path, [table_info])

            total_cells = len(table_info.cells)
            header_cells = sum(1 for c in table_info.cells if c.is_header)
            complexity = self._assess_complexity(table_info)

            confidence = self._confidence_calc.calculate(
                FixMethod.HEURISTIC,
                signal_strength=complexity,
                context_quality=0.7,
            )

            return TableTagResult(
                success=True,
                tables_found=1,
                tables_tagged=tagged,
                total_cells=total_cells,
                header_cells=header_cells,
                confidence=confidence,
                needs_review=self._confidence_calc.needs_review(confidence),
            )

        except Exception as exc:
            logger.error("Single table tagging failed: %s", exc, exc_info=True)
            return TableTagResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _detect_tables(self, doc) -> List[TableInfo]:
        """Detect all tables across all pages."""
        tables: List[TableInfo] = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            try:
                found = page.find_tables()
                for table in found.tables:
                    table_info = self._parse_table(table, page_num)
                    if table_info.rows >= 2 and table_info.cols >= 2:
                        tables.append(table_info)
            except Exception as exc:
                logger.warning("Table detection failed on page %d: %s", page_num, exc)
        return tables

    def _parse_table(self, table, page_num: int) -> TableInfo:
        """Parse a PyMuPDF table into our TableInfo structure."""
        cells: List[CellInfo] = []
        rows_data = table.extract()

        for row_idx, row in enumerate(rows_data):
            for col_idx, cell_text in enumerate(row):
                text = cell_text if cell_text else ""
                # Get cell bbox if available
                try:
                    cell = table.cells[row_idx * len(row) + col_idx]
                    bbox = cell[:4] if cell else table.bbox
                except (IndexError, TypeError):
                    bbox = table.bbox

                cells.append(
                    CellInfo(
                        row=row_idx,
                        col=col_idx,
                        text=str(text).strip(),
                        bbox=bbox,
                    )
                )

        return TableInfo(
            page_num=page_num,
            bbox=table.bbox,
            rows=len(rows_data),
            cols=len(rows_data[0]) if rows_data else 0,
            cells=cells,
        )

    # ------------------------------------------------------------------
    # Header identification
    # ------------------------------------------------------------------

    def _identify_headers_heuristic(self, table: TableInfo) -> None:
        """Identify header cells using heuristics.

        Rules:
        1. First row is header if all cells are non-empty and short
        2. First column is header if all cells are non-empty and short
        3. Cells with bold text or larger font are likely headers
        """
        if not table.cells:
            return

        # Check first row
        first_row = [c for c in table.cells if c.row == 0]
        if first_row and all(c.text for c in first_row):
            # First row has all non-empty cells -- likely header
            avg_len = sum(len(c.text) for c in first_row) / len(first_row)
            if avg_len < 50:  # Short text = likely headers
                for c in first_row:
                    c.is_header = True
                    c.scope = "Column"
                table.header_rows = 1

        # Check first column
        first_col = [c for c in table.cells if c.col == 0]
        if first_col and all(c.text for c in first_col):
            avg_len = sum(len(c.text) for c in first_col) / len(first_col)
            if avg_len < 30:  # Row headers tend to be shorter
                for c in first_col:
                    c.is_header = True
                    c.scope = "Row"
                table.header_cols = 1

    def _confirm_headers_with_ai(self, pdf_path: str, tables: List[TableInfo]) -> None:
        """Use AI vision to confirm or adjust header identification."""
        ai_client = self._ai_client
        if ai_client is None and self._allow_legacy_provider_manager:
            try:
                from src.ai.providers import get_provider_manager

                ai_client = get_provider_manager()
            except Exception:
                return  # AI unavailable, keep heuristic results
        if ai_client is None:
            return

        doc = fitz.open(pdf_path)
        try:
            for table in tables:
                if not table.cells:
                    continue
                try:
                    page = doc[table.page_num]
                    # Clip to table region
                    clip = fitz.Rect(table.bbox)
                    pixmap = page.get_pixmap(dpi=150, clip=clip)
                    image_bytes = pixmap.tobytes("png")

                    prompt = (
                        f"This is a table with {table.rows} rows and {table.cols} columns.\n"
                        "Identify which rows are header rows and which columns are header columns.\n"
                        'Return JSON: {"header_rows": [0], "header_cols": [], "merged_cells": []}\n'
                        "Row/column indices are 0-based. Return ONLY JSON."
                    )

                    result = ai_client.analyze_image_sync(
                        image_data=image_bytes,
                        prompt=prompt,
                        max_tokens=300,
                    )

                    if result.get("success") and result.get("content"):
                        self._apply_ai_headers(table, result["content"])

                except Exception as exc:
                    logger.debug("AI header confirmation failed for table: %s", exc)
        finally:
            doc.close()

    def _apply_ai_headers(self, table: TableInfo, ai_content: str) -> None:
        """Apply AI-detected headers to table cells."""
        import json

        content = ai_content.strip()
        if "```" in content:
            parts = content.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    content = part
                    break

        try:
            data = json.loads(content)
            header_rows = data.get("header_rows", [])
            header_cols = data.get("header_cols", [])
            merged_cells = data.get("merged_cells", [])

            # Apply header rows
            if isinstance(header_rows, list):
                for row_idx in header_rows:
                    if isinstance(row_idx, int) and 0 <= row_idx < table.rows:
                        for c in table.cells:
                            if c.row == row_idx:
                                c.is_header = True
                                c.scope = "Column"
                        table.header_rows = max(table.header_rows, row_idx + 1)

            # Apply header columns
            if isinstance(header_cols, list):
                for col_idx in header_cols:
                    if isinstance(col_idx, int) and 0 <= col_idx < table.cols:
                        for c in table.cells:
                            if c.col == col_idx:
                                c.is_header = True
                                c.scope = "Row"
                        table.header_cols = max(table.header_cols, col_idx + 1)

            # Apply merged cells
            if isinstance(merged_cells, list):
                for merge in merged_cells:
                    if isinstance(merge, dict):
                        row = merge.get("row", -1)
                        col = merge.get("col", -1)
                        col_span = merge.get("col_span", 1)
                        row_span = merge.get("row_span", 1)
                        for c in table.cells:
                            if c.row == row and c.col == col:
                                c.col_span = col_span
                                c.row_span = row_span
                                table.has_merged_cells = True
                                break

        except (json.JSONDecodeError, ValueError):
            pass  # Keep heuristic results

    # ------------------------------------------------------------------
    # Structure tag application
    # ------------------------------------------------------------------

    def _apply_table_tags(self, pdf_path: str, tables: List[TableInfo]) -> int:
        """Apply PDF structure tags for tables via pikepdf."""
        tagged = 0

        with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
            # Ensure StructTreeRoot exists
            if Name.StructTreeRoot not in pdf.Root:
                struct_root = pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructTreeRoot,
                            "/K": Array([]),
                            "/ParentTree": Dictionary({}),
                        }
                    )
                )
                pdf.Root[Name.StructTreeRoot] = struct_root
                pdf.Root[Name.MarkInfo] = Dictionary({"/Marked": True})

            struct_root = pdf.Root[Name.StructTreeRoot]
            if Name.K not in struct_root:
                struct_root[Name.K] = Array([])

            kids = struct_root[Name.K]
            if not isinstance(kids, Array):
                kids = Array([kids])
                struct_root[Name.K] = kids

            for table in tables:
                try:
                    table_elem = self._build_table_element(pdf, table)
                    if table_elem:
                        table_elem[Name("/P")] = struct_root
                        kids.append(table_elem)
                        tagged += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to tag table on page %d: %s",
                        table.page_num,
                        exc,
                    )

            pdf.save(pdf_path)

        return tagged

    def _build_table_element(self, pdf, table: TableInfo):
        """Build a Table structure element with THead/TBody/TR/TH/TD."""
        page_obj = (
            pdf.pages[table.page_num].obj if table.page_num < len(pdf.pages) else None
        )

        # Create Table element
        table_dict = {
            "/Type": Name.StructElem,
            "/S": Name.Table,
            "/K": Array([]),
        }
        if page_obj:
            table_dict["/Pg"] = page_obj
        table_elem = pdf.make_indirect(Dictionary(table_dict))

        # Build THead if there are header rows
        if table.header_rows > 0:
            thead = pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name.THead,
                        "/P": table_elem,
                        "/K": Array([]),
                    }
                )
            )
            for row_idx in range(table.header_rows):
                tr = self._build_row(
                    pdf, table, row_idx, page_obj, thead, is_header_row=True
                )
                if tr:
                    thead[Name.K].append(tr)
            table_elem[Name.K].append(thead)

        # Build TBody for data rows
        tbody = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name.TBody,
                    "/P": table_elem,
                    "/K": Array([]),
                }
            )
        )
        for row_idx in range(table.header_rows, table.rows):
            tr = self._build_row(
                pdf, table, row_idx, page_obj, tbody, is_header_row=False
            )
            if tr:
                tbody[Name.K].append(tr)
        table_elem[Name.K].append(tbody)

        return table_elem

    def _build_row(
        self,
        pdf,
        table: TableInfo,
        row_idx: int,
        page_obj,
        parent,
        is_header_row: bool,
    ):
        """Build a TR element with TH/TD children."""
        row_cells = sorted(
            [c for c in table.cells if c.row == row_idx],
            key=lambda c: c.col,
        )
        if not row_cells:
            return None

        tr = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name.TR,
                    "/P": parent,
                    "/K": Array([]),
                }
            )
        )

        for cell in row_cells:
            cell_tag = Name.TH if (cell.is_header or is_header_row) else Name.TD
            cell_elem = pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": cell_tag,
                        "/P": tr,
                    }
                )
            )

            # Add text content
            if cell.text:
                cell_elem[Name.Alt] = String(cell.text)

            # Add Scope for TH cells
            if cell_tag == Name.TH and cell.scope:
                attrs = Dictionary(
                    {
                        "/O": Name.Table,
                        "/Scope": Name(f"/{cell.scope}"),
                    }
                )
                if cell.col_span > 1:
                    attrs[Name("/ColSpan")] = cell.col_span
                if cell.row_span > 1:
                    attrs[Name("/RowSpan")] = cell.row_span
                cell_elem[Name.A] = pdf.make_indirect(attrs)

            # Add page reference
            if page_obj:
                cell_elem[Name.Pg] = page_obj

            tr[Name.K].append(cell_elem)

        return tr

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _assess_complexity(self, table: TableInfo) -> float:
        """Assess table complexity (0.0 = complex, 1.0 = simple).

        Returns signal strength for confidence calculation.
        """
        score = 1.0

        # Regular grid (all rows same number of cols)
        row_counts: Dict[int, int] = {}
        for c in table.cells:
            row_counts[c.row] = row_counts.get(c.row, 0) + 1
        if row_counts and len(set(row_counts.values())) > 1:
            score -= 0.2  # Irregular column counts

        # Merged cells reduce confidence
        if table.has_merged_cells:
            score -= 0.3

        # Very large tables are harder
        if table.rows > 20 or table.cols > 10:
            score -= 0.15

        # No headers detected
        if table.header_rows == 0 and table.header_cols == 0:
            score -= 0.2

        return max(0.1, min(1.0, score))

    @staticmethod
    def _bbox_overlap(bbox1, bbox2) -> float:
        """Compute overlap ratio between two bounding boxes."""
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])

        return intersection / area1 if area1 > 0 else 0.0
