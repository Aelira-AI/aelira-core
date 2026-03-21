"""Table accessibility checking for PDFs."""

import logging
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF for fitz-based table extraction fallback
import pikepdf
from pikepdf import Name

from .models import TableCell, PDFTable, TableHeaderDetectionResult, TableFix

logger = logging.getLogger(__name__)


class TableAccessibilityChecker:
    """Check PDF table accessibility (headers, structure tags, scope)."""

    def check(self, file_path: str) -> List[Dict]:
        """Run table accessibility checks. Returns list of issue dicts.

        Extracts tables using pdfplumber (if available) for better accuracy,
        falling back to fitz-based extraction if needed.
        Detects headers and generates fix recommendations.

        Also checks the PDF structure tree for existing Table/TH tags so that
        already-remediated tables are not flagged again.

        Args:
            file_path: Path to the PDF file

        Returns:
            List of standard issue dicts for tables with accessibility problems
        """
        results: List[Tuple[PDFTable, TableHeaderDetectionResult, TableFix]] = []

        # Check how many tables already have proper structure tags
        tagged_table_count = self._count_struct_tree_tables(file_path)
        if tagged_table_count > 0:
            logger.info(
                f"[TableAccessibilityChecker] Structure tree has {tagged_table_count} "
                f"Table elements with TH tags"
            )

        # Try pdfplumber first for better table detection
        tables = self._extract_tables_with_pdfplumber(file_path)

        if tables:
            logger.info(
                f"[TableAccessibilityChecker] Using pdfplumber tables: found {len(tables)} tables"
            )
            for table in tables:
                header_detection = self._detect_table_headers_heuristic(table.cells)
                # If structure tree has enough tagged tables, mark as already tagged
                if tagged_table_count > 0:
                    header_detection.has_th_tags = True
                    tagged_table_count -= 1
                fix = self._generate_table_fix(table, header_detection)
                results.append((table, header_detection, fix))
        else:
            # Fall back to fitz-based extraction
            try:
                with fitz.open(file_path) as doc:
                    for page_num, page in enumerate(doc, 1):
                        # Extract text with font information
                        blocks = page.get_text(
                            "dict", flags=fitz.TEXT_PRESERVE_WHITESPACE
                        )["blocks"]

                        # Find potential tables by looking for aligned text blocks
                        page_tables = self._extract_tables_from_page(page_num, blocks)

                        for table in page_tables:
                            # Detect headers using heuristics
                            header_detection = self._detect_table_headers_heuristic(
                                table.cells
                            )

                            # If structure tree has enough tagged tables, mark as already tagged
                            if tagged_table_count > 0:
                                header_detection.has_th_tags = True
                                tagged_table_count -= 1

                            # Generate fix recommendations
                            fix = self._generate_table_fix(table, header_detection)

                            results.append((table, header_detection, fix))

            except Exception as e:
                logger.warning(
                    f"[TableAccessibilityChecker] Error analyzing tables in {file_path}: {e}"
                )

        # Convert tuples into standard issue dicts
        issues: List[Dict] = []
        if results:
            logger.info(
                f"[TableAccessibilityChecker] Found {len(results)} tables to check "
                f"for accessibility"
            )
            for table, header_detection, fix in results:
                # Check if this table already has proper structure tags
                if header_detection.has_th_tags:
                    continue  # Already tagged, skip

                severity = "high"  # Tables without headers are WCAG 1.3.1 violations
                header_text = ", ".join(
                    h.text.strip()[:30] for h in header_detection.detected_headers[:5]
                )
                issues.append(
                    {
                        "severity": severity,
                        "rule": "WCAG 1.3.1",
                        "message": (
                            f"Table on page {table.page_number} "
                            f"({table.row_count}x{table.col_count}) "
                            f"missing structure tags (TH/TD/TR)"
                        ),
                        "impact": "Screen readers cannot identify table headers "
                        "and data cell relationships",
                        "page_number": table.page_number,
                        "location": fix.table_location,
                        "element": f"Table ({table.row_count} rows x {table.col_count} cols)",
                        "suggested_fix": fix.fix_instructions,
                        "issue_type": "missing_table_structure",
                        "detected_headers": header_text,
                        "bbox": list(table.bbox) if table.bbox else None,
                    }
                )

        return issues

    def _detect_table_headers_heuristic(
        self, table_cells: List[TableCell]
    ) -> TableHeaderDetectionResult:
        """
        Detect table headers using visual and content heuristics.

        Uses multiple signals to identify header cells:
        1. Bold text styling
        2. Background shading/fill
        3. Different font size (typically larger)
        4. Header-like keywords (ID, Name, Date, Total, etc.)

        Args:
            table_cells: List of TableCell objects representing all cells in a table

        Returns:
            TableHeaderDetectionResult with detected headers and confidence
        """
        if not table_cells:
            return TableHeaderDetectionResult(
                detected_headers=[],
                detection_method="none",
                confidence=0.0,
            )

        # Get first row cells (potential column headers)
        first_row = [c for c in table_cells if c.row == 0]

        # Get first column cells (potential row headers)
        first_col = [c for c in table_cells if c.col == 0]

        detected_headers = []
        header_row_indices = []
        header_col_indices = []
        detection_method = "none"
        confidence = 0.0

        # Header keywords commonly found in table headers
        header_keywords = {
            "id",
            "name",
            "date",
            "total",
            "description",
            "status",
            "type",
            "category",
            "amount",
            "price",
            "quantity",
            "value",
            "number",
            "count",
            "sum",
            "average",
            "percent",
            "year",
            "month",
            "day",
            "title",
            "label",
            "code",
            "unit",
            "notes",
            "comments",
            "action",
            "email",
            "phone",
            "address",
            "city",
            "state",
            "country",
            "zip",
        }

        # Method 1: Check for bold text in first row
        if first_row and all(c.is_bold for c in first_row if c.text.strip()):
            detected_headers = first_row
            header_row_indices = [0]
            detection_method = "bold"
            confidence = 0.9
            logger.debug(
                f"[TableHeaderDetection] Detected headers by bold text: "
                f"{len(first_row)} cells"
            )

        # Method 2: Check for background shading in first row
        elif first_row and all(c.has_background for c in first_row if c.text.strip()):
            detected_headers = first_row
            header_row_indices = [0]
            detection_method = "background"
            confidence = 0.85
            logger.debug(
                f"[TableHeaderDetection] Detected headers by background: "
                f"{len(first_row)} cells"
            )

        # Method 3: Check for different font size in first row
        elif first_row:
            first_row_sizes = [
                c.font_size for c in first_row if c.font_size is not None
            ]
            other_sizes = [
                c.font_size
                for c in table_cells
                if c.row > 0 and c.font_size is not None
            ]

            if first_row_sizes and other_sizes:
                avg_first_row = sum(first_row_sizes) / len(first_row_sizes)
                avg_other = sum(other_sizes) / len(other_sizes)

                # First row has notably larger font (10% or more)
                if avg_first_row > avg_other * 1.1:
                    detected_headers = first_row
                    header_row_indices = [0]
                    detection_method = "font_size"
                    confidence = 0.75
                    logger.debug(
                        f"[TableHeaderDetection] Detected headers by font size: "
                        f"avg {avg_first_row:.1f} vs {avg_other:.1f}"
                    )

        # Method 4: Check for header keywords in first row
        if not detected_headers and first_row:
            keyword_matches = sum(
                1
                for c in first_row
                if any(kw in c.text.lower().split() for kw in header_keywords)
            )
            # If more than half of first row cells contain header keywords
            if keyword_matches >= len(first_row) / 2 and keyword_matches >= 2:
                detected_headers = first_row
                header_row_indices = [0]
                detection_method = "keywords"
                confidence = 0.7
                logger.debug(
                    f"[TableHeaderDetection] Detected headers by keywords: "
                    f"{keyword_matches} matches"
                )

        # Also check first column for row headers (common in data tables)
        if first_col and len(first_col) > 1:
            # Check if first column is all bold (row headers)
            if all(
                c.is_bold for c in first_col[1:] if c.text.strip()
            ):  # Skip cell [0,0]
                header_col_indices = [0]
                if detection_method == "none":
                    detection_method = "bold_column"
                    confidence = 0.7

        return TableHeaderDetectionResult(
            detected_headers=detected_headers,
            header_row_indices=header_row_indices,
            header_col_indices=header_col_indices,
            detection_method=detection_method,
            confidence=confidence,
            has_th_tags=False,  # Will be set by structure tree analysis
        )

    def _generate_table_fix(
        self, table: PDFTable, header_detection: TableHeaderDetectionResult
    ) -> TableFix:
        """
        Generate fix recommendation for table structure accessibility.

        Creates actionable recommendations for adding proper table headers
        with scope attributes to meet WCAG 1.3.1 requirements.

        Args:
            table: The PDFTable being analyzed
            header_detection: Result from _detect_table_headers_heuristic

        Returns:
            TableFix with specific recommendations
        """
        location = f"Page {table.page_number}, Table {table.table_index + 1}"

        # Extract text from detected headers
        header_texts = [
            h.text.strip()[:50]
            for h in header_detection.detected_headers
            if h.text.strip()
        ]

        # Determine recommended scope based on header location
        recommended_scope = None
        fix_instructions = ""

        if header_detection.header_row_indices and header_detection.header_col_indices:
            # Both row and column headers detected
            recommended_scope = "both"
            fix_instructions = (
                f"Add <TH> tags with scope='col' to row "
                f"{header_detection.header_row_indices[0] + 1} "
                f"and scope='row' to column "
                f"{header_detection.header_col_indices[0] + 1}. "
                "For complex tables, consider using headers and id attributes."
            )
        elif header_detection.header_row_indices:
            # Column headers only
            recommended_scope = "col"
            fix_instructions = (
                f"Add <TH> tags with scope='col' to cells in row "
                f"{header_detection.header_row_indices[0] + 1}. "
                f"Detected {len(header_texts)} header cells: "
                f"{', '.join(header_texts[:5])}"
                + ("..." if len(header_texts) > 5 else "")
            )
        elif header_detection.header_col_indices:
            # Row headers only
            recommended_scope = "row"
            fix_instructions = (
                f"Add <TH> tags with scope='row' to cells in column "
                f"{header_detection.header_col_indices[0] + 1}."
            )
        else:
            # No headers detected - provide general guidance
            fix_instructions = (
                "Unable to automatically detect table headers. Manually identify "
                "header cells and add <TH> tags with appropriate scope attributes "
                "(scope='col' for column headers, scope='row' for row headers)."
            )

        # Set priority based on table size and confidence
        priority = "high"
        if header_detection.confidence < 0.5:
            priority = "medium"  # Low confidence, needs manual review
        elif table.row_count > 10 or table.col_count > 5:
            priority = "critical"  # Large tables are more important

        return TableFix(
            table_location=location,
            detected_headers=header_texts,
            recommended_scope=recommended_scope,
            fix_instructions=fix_instructions,
            priority=priority,
            wcag_criterion="1.3.1",
        )

    def _extract_tables_with_pdfplumber(self, file_path: str) -> List[PDFTable]:
        """
        Extract tables using pdfplumber for more accurate table detection.

        pdfplumber uses visual layout analysis to detect table structures,
        which is more reliable than text-based heuristics for complex tables.

        Args:
            file_path: Path to the PDF file

        Returns:
            List of PDFTable objects detected in the document
        """
        tables = []

        try:
            import pdfplumber
        except ImportError:
            logger.warning(
                "[TableAccessibilityChecker] pdfplumber not installed, "
                "falling back to fitz-based extraction"
            )
            return []

        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_tables = page.extract_tables()

                    for table_idx, table_data in enumerate(page_tables):
                        if not table_data or len(table_data) == 0:
                            continue

                        # Convert pdfplumber table to PDFTable format
                        cells = []
                        row_count = len(table_data)
                        col_count = (
                            max(len(row) for row in table_data) if table_data else 0
                        )

                        for row_idx, row in enumerate(table_data):
                            for col_idx, cell_text in enumerate(row):
                                cells.append(
                                    TableCell(
                                        row=row_idx,
                                        col=col_idx,
                                        text=cell_text or "",
                                        is_bold=False,  # pdfplumber doesn't provide styling
                                        has_background=False,
                                    )
                                )

                        # Determine if first row appears to be headers
                        first_row = table_data[0] if table_data else []
                        has_header_row = len(first_row) > 0 and all(
                            cell and len(str(cell).strip()) < 50
                            for cell in first_row
                            if cell
                        )

                        tables.append(
                            PDFTable(
                                page_number=page_num,
                                table_index=table_idx,
                                cells=cells,
                                row_count=row_count,
                                col_count=col_count,
                                has_header_row=has_header_row,
                            )
                        )

                        logger.debug(
                            f"[TableAccessibilityChecker] pdfplumber found table on "
                            f"page {page_num}: {row_count}x{col_count}"
                        )

        except Exception as e:
            logger.warning(
                f"[TableAccessibilityChecker] pdfplumber table extraction failed: {e}"
            )

        return tables

    def _count_struct_tree_tables(self, pdf_path: str) -> int:
        """Count Table elements with TH children in the PDF structure tree.

        Recursively walks the entire structure tree (Tables may be nested
        under a Document element, not direct children of StructTreeRoot).

        Returns the number of Table elements that already have proper TH tags.
        Used to avoid flagging already-tagged tables as accessibility issues.
        """
        try:
            with pikepdf.open(pdf_path) as pdf:
                if Name.StructTreeRoot not in pdf.Root:
                    return 0
                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K not in struct_root:
                    return 0

                tagged_tables = 0

                def _has_th(elem, depth=0):
                    """Check if an element contains TH tags (for Table validation)."""
                    if depth > 10:
                        return False
                    try:
                        s = str(elem.get(Name.S, "")) if hasattr(elem, "get") else ""
                        if s == "/TH":
                            return True
                        if Name.K in elem:
                            children = elem[Name.K]
                            if not hasattr(children, "__iter__"):
                                children = [children]
                            for child in children:
                                if hasattr(child, "get") and _has_th(child, depth + 1):
                                    return True
                    except Exception:
                        pass
                    return False

                def _walk(elem, depth=0):
                    """Walk the tree, counting Table elements with TH children."""
                    nonlocal tagged_tables
                    if depth > 30:
                        return
                    try:
                        if not hasattr(elem, "get"):
                            return
                        s = str(elem.get(Name.S, ""))
                        if s == "/Table":
                            if _has_th(elem):
                                tagged_tables += 1
                            return  # Don't recurse into Table children
                        if Name.K in elem:
                            children = elem[Name.K]
                            if not hasattr(children, "__iter__"):
                                children = [children]
                            for child in children:
                                _walk(child, depth + 1)
                    except Exception:
                        pass

                kids = struct_root[Name.K]
                if hasattr(kids, "__iter__"):
                    for kid in kids:
                        _walk(kid)
                elif hasattr(kids, "get"):
                    _walk(kids)

                return tagged_tables
        except Exception as e:
            logger.debug(
                f"[TableAccessibilityChecker] Structure tree table check failed: {e}"
            )
            return 0

    def _extract_tables_from_page(
        self, page_number: int, blocks: List[Dict]
    ) -> List[PDFTable]:
        """
        Extract table structures from page text blocks.

        Uses layout analysis to identify table regions and extract cell data.

        Args:
            page_number: 1-indexed page number
            blocks: Text blocks from PyMuPDF

        Returns:
            List of PDFTable objects detected on the page
        """
        tables = []

        # Group text spans by approximate y-position (rows)
        rows = {}
        for block in blocks:
            if block.get("type") != 0:  # Skip non-text blocks
                continue

            for line in block.get("lines", []):
                y = round(line["bbox"][1], 0)  # Round to group nearby lines

                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue

                    if y not in rows:
                        rows[y] = []

                    rows[y].append(
                        {
                            "text": text,
                            "bbox": span["bbox"],
                            "font": span.get("font", ""),
                            "size": span.get("size", 12),
                            "flags": span.get("flags", 0),  # 2^4 = bold
                        }
                    )

        # Find rows with multiple columns (potential table rows)
        sorted_rows = sorted(rows.items())
        table_rows = []
        current_table_start = None

        for y, spans in sorted_rows:
            # Sort spans by x-position
            spans.sort(key=lambda s: s["bbox"][0])

            # Check if this row has multiple distinct columns
            if len(spans) >= 2:
                # Check spacing between spans to confirm table structure
                col_gaps = []
                for i in range(1, len(spans)):
                    gap = spans[i]["bbox"][0] - spans[i - 1]["bbox"][2]
                    col_gaps.append(gap)

                # If gaps are consistent, likely a table row
                if col_gaps and min(col_gaps) > 5:  # At least 5pt gap between columns
                    if current_table_start is None:
                        current_table_start = len(table_rows)
                    table_rows.append((y, spans))
            else:
                # End of potential table
                if (
                    current_table_start is not None
                    and len(table_rows) - current_table_start >= 2
                ):
                    # Extract table from collected rows
                    table = self._create_table_from_rows(
                        page_number, len(tables), table_rows[current_table_start:]
                    )
                    if table:
                        tables.append(table)
                current_table_start = None
                table_rows = []

        # Handle table at end of page
        if (
            current_table_start is not None
            and len(table_rows) - current_table_start >= 2
        ):
            table = self._create_table_from_rows(
                page_number, len(tables), table_rows[current_table_start:]
            )
            if table:
                tables.append(table)

        return tables

    def _create_table_from_rows(
        self,
        page_number: int,
        table_index: int,
        rows: List[Tuple[float, List[Dict]]],
    ) -> Optional[PDFTable]:
        """
        Create a PDFTable object from extracted row data.

        Args:
            page_number: 1-indexed page number
            table_index: Index of this table on the page
            rows: List of (y-position, spans) tuples

        Returns:
            PDFTable object or None if invalid
        """
        if not rows or len(rows) < 2:
            return None

        cells = []
        col_positions = set()

        # First pass: collect all column x-positions
        for _, spans in rows:
            for span in spans:
                col_positions.add(round(span["bbox"][0], 0))

        col_positions = sorted(col_positions)

        # Create column index mapping
        def get_col_index(x):
            for i, pos in enumerate(col_positions):
                if abs(x - pos) < 20:  # 20pt tolerance
                    return i
            return len(col_positions)

        # Second pass: create cells
        for row_idx, (y, spans) in enumerate(rows):
            for span in spans:
                col_idx = get_col_index(round(span["bbox"][0], 0))

                # Check if text is bold (flag bit 4)
                is_bold = bool(span.get("flags", 0) & 16)

                cell = TableCell(
                    row=row_idx,
                    col=col_idx,
                    text=span["text"],
                    is_bold=is_bold,
                    has_background=False,  # Would need additional analysis
                    font_size=span.get("size"),
                    font_name=span.get("font"),
                    bbox=(
                        span["bbox"][0],
                        span["bbox"][1],
                        span["bbox"][2],
                        span["bbox"][3],
                    ),
                )
                cells.append(cell)

        if not cells:
            return None

        # Calculate table dimensions
        max_row = max(c.row for c in cells) + 1
        max_col = max(c.col for c in cells) + 1

        return PDFTable(
            page_number=page_number,
            table_index=table_index,
            cells=cells,
            row_count=max_row,
            col_count=max_col,
        )
