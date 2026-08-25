"""Verified PDF table detection and accessibility remediation.

Automatic tagging is deliberately narrow: every logical cell must have
non-empty geometric text evidence and a unique matching PDF text object.
Only then is Table/TR/TH/TD structure written with MCID/MCR bindings and a
ParentTree entry. Anything ambiguous remains a manual-review outcome.

WCAG 1.3.1 (Info and Relationships): Information, structure, and
relationships conveyed through presentation can be programmatically
determined.
"""

import io
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Operator, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None  # type: ignore[assignment]
    Array = Any  # type: ignore[assignment,misc]
    Dictionary = Any  # type: ignore[assignment,misc]
    Name = Any  # type: ignore[assignment,misc]
    Operator = Any  # type: ignore[assignment,misc]
    String = Any  # type: ignore[assignment,misc]

from .confidence import ConfidenceCalculator, FixMethod

logger = logging.getLogger(__name__)


MAX_TABLE_COLUMNS = 64
MAX_TABLE_CELLS = 10_000
MAX_TABLES = 200
TABLE_STRUCTURE_NOT_VERIFIED = "table_structure_not_verified"
TABLE_STRUCTURE_TOO_COMPLEX = "table_structure_too_complex"


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
    table_index: int = 0
    rows: int = 0
    cols: int = 0
    cells: List[CellInfo] = field(default_factory=list)
    header_rows: int = 0
    header_cols: int = 0
    has_merged_cells: bool = False
    header_evidence_verified: bool = False


@dataclass
class TableTagResult:
    """Result of table detection or a fail-closed write refusal."""

    success: bool
    tables_found: int = 0
    tables_tagged: int = 0
    total_cells: int = 0
    header_cells: int = 0
    confidence: float = 0.0
    needs_review: bool = True
    error: Optional[str] = None
    error_code: Optional[str] = None
    tables: List[TableInfo] = field(default_factory=list)
    bound_pdf: Optional[Any] = field(default=None, repr=False)


@dataclass(frozen=True)
class _ContentBlock:
    """One unmarked BT/ET block that can be bound to exactly one cell."""

    start: int
    end: int
    text: str
    anchor: Optional[Tuple[float, float]] = None


@dataclass(frozen=True)
class _CellBinding:
    """A proven association between one logical cell and one content block."""

    table_index: int
    cell_index: int
    block: _ContentBlock


@dataclass
class _PageBindingPlan:
    """Complete, write-free binding plan for one page."""

    page_num: int
    struct_parent_key: int
    ops: List[Any]
    tables: List[TableInfo]
    bindings: Dict[Tuple[int, int], _CellBinding]
    starting_mcid: int


class TableTagger:
    """Detect and tag only tables whose real content can be proven.

    PyMuPDF supplies the visual grid and word geometry. Pikepdf supplies the
    actual text-showing content objects. A cell is accepted only when those
    independent views agree and its content block is unique and unmarked.
    """

    def __init__(
        self,
        use_ai: bool = True,
        *,
        ai_client: Any = None,
        allow_legacy_provider_manager: bool = False,
    ) -> None:
        self._confidence_calc = ConfidenceCalculator()
        self._ai_client = ai_client
        self._allow_legacy_provider_manager = allow_legacy_provider_manager
        # Retained for API compatibility. AI is never semantic header proof.
        _ = use_ai

    def _confirm_headers_with_ai(self, pdf_path: str, tables: List[TableInfo]) -> None:
        """Retain the legacy seam without treating AI as header evidence."""
        ai_client = self._ai_client
        if ai_client is None and self._allow_legacy_provider_manager:
            from src.ai.providers import get_provider_manager

            ai_client = get_provider_manager()
        if ai_client is None:
            return

        document = fitz.open(pdf_path)
        document.close()
        _ = tables

    def tag_tables(self, pdf_path: str) -> TableTagResult:
        """Detect tables and transactionally tag every verified table.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            A successful bound-table result or a deterministic manual result.
        """
        detection = self.detect_tables(pdf_path)
        if not detection.success:
            return detection
        safety_error = self._safety_error_code(detection.tables)
        if safety_error:
            return TableTagResult(
                success=False,
                tables_found=len(detection.tables),
                total_cells=self._logical_cell_count(detection.tables),
                error=safety_error,
                error_code=safety_error,
            )
        return self.write_tables(pdf_path, detection.tables)

    def detect_tables(self, pdf_path: str) -> TableTagResult:
        """Detect and validate tables without modifying the PDF."""
        if not HAS_PYMUPDF:
            return TableTagResult(
                success=False,
                error="PyMuPDF required",
                error_code=TABLE_STRUCTURE_NOT_VERIFIED,
            )
        try:
            doc = fitz.open(pdf_path)
            try:
                all_tables = self._detect_tables(doc)
            finally:
                doc.close()
        except Exception as exc:
            logger.error("Table detection failed: %s", exc, exc_info=True)
            return TableTagResult(
                success=False,
                error=str(exc),
                error_code=TABLE_STRUCTURE_NOT_VERIFIED,
            )

        if not all_tables:
            return TableTagResult(
                success=True,
                tables_found=0,
                confidence=1.0,
                needs_review=False,
            )

        safety_error = self._safety_error_code(all_tables)
        if safety_error:
            return TableTagResult(
                success=False,
                tables_found=len(all_tables),
                total_cells=self._logical_cell_count(all_tables),
                error=safety_error,
                error_code=safety_error,
            )

        try:
            total_cells = sum(len(table.cells) for table in all_tables)
            header_cells = sum(
                sum(1 for cell in table.cells if cell.is_header) for table in all_tables
            )
            complexity_scores = [self._assess_complexity(t) for t in all_tables]
            avg_complexity = sum(complexity_scores) / len(complexity_scores)
            confidence = self._confidence_calc.calculate(
                FixMethod.HEURISTIC,
                signal_strength=avg_complexity,
                context_quality=0.7,
            )
        except Exception as exc:
            logger.error("Table evidence processing failed: %s", exc, exc_info=True)
            return TableTagResult(
                success=False,
                tables_found=len(all_tables),
                error=str(exc),
                error_code=TABLE_STRUCTURE_NOT_VERIFIED,
            )

        return TableTagResult(
            success=True,
            tables_found=len(all_tables),
            total_cells=total_cells,
            header_cells=header_cells,
            confidence=confidence,
            needs_review=self._confidence_calc.needs_review(confidence),
            tables=all_tables,
        )

    def write_tables(self, pdf_path: str, tables: List[TableInfo]) -> TableTagResult:
        """Transactionally bind verified tables and replace ``pdf_path``.

        The input file is not touched until a complete clone has been written,
        reopened, and its table bindings verified.
        """
        safety_error = self._safety_error_code(tables)
        if safety_error:
            return TableTagResult(
                success=False,
                tables_found=len(tables),
                total_cells=self._logical_cell_count(tables),
                error=safety_error,
                error_code=safety_error,
            )

        if not HAS_PIKEPDF or not HAS_PYMUPDF or not tables:
            return self._not_verified(tables)

        source_pdf = None
        fitz_doc = None
        bound_pdf = None
        temporary_path: Optional[str] = None
        try:
            source_pdf = pikepdf.open(pdf_path)
            fitz_doc = fitz.open(pdf_path)
            result = self.bind_tables(source_pdf, fitz_doc, tables)
            bound_pdf = result.bound_pdf
            if not result.success or bound_pdf is None:
                return result

            destination = Path(pdf_path)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{destination.name}.table-",
                suffix=".pdf",
                dir=destination.parent,
            )
            os.close(descriptor)
            bound_pdf.save(temporary_path)
            if not self.verify_file(temporary_path, result.total_cells):
                return self._not_verified(tables)
            os.replace(temporary_path, pdf_path)
            temporary_path = None
            result.bound_pdf = None
            return result
        except Exception as exc:
            logger.error("Verified table write failed: %s", exc, exc_info=True)
            return self._not_verified(tables)
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            for document in (bound_pdf, source_pdf, fitz_doc):
                if document is not None:
                    try:
                        document.close()
                    except Exception:
                        pass

    def bind_tables(
        self, pdf: Any, fitz_doc: Any, tables: List[TableInfo]
    ) -> TableTagResult:
        """Return a modified clone only after all table bindings validate.

        ``pdf`` is never mutated. This lets the remediator keep other pending
        changes while a failed table attempt remains a true no-op.
        """
        safety_error = self._safety_error_code(tables)
        if safety_error:
            return self._failure(tables, safety_error)
        if not HAS_PIKEPDF or not HAS_PYMUPDF or not tables:
            return self._not_verified(tables)

        clone = None
        try:
            snapshot = io.BytesIO()
            pdf.save(snapshot)
            snapshot.seek(0)
            clone = pikepdf.open(snapshot)
            plans = self._build_binding_plans(clone, fitz_doc, tables)
            self._apply_binding_plans(clone, plans)
            total_cells = sum(len(table.cells) for table in tables)
            if not self._verify_pdf_bindings(clone, total_cells):
                raise ValueError("saved table bindings did not verify")
            return TableTagResult(
                success=True,
                tables_found=len(tables),
                tables_tagged=len(tables),
                total_cells=total_cells,
                header_cells=sum(
                    sum(1 for cell in table.cells if cell.is_header) for table in tables
                ),
                confidence=1.0,
                needs_review=False,
                tables=tables,
                bound_pdf=clone,
            )
        except Exception as exc:
            logger.info("Table content binding refused: %s", exc)
            if clone is not None:
                try:
                    clone.close()
                except Exception:
                    pass
            return self._not_verified(tables)

    @classmethod
    def verify_file(cls, pdf_path: str, expected_cells: int) -> bool:
        """Verify content, MCR, and ParentTree ownership in a saved PDF."""
        if not HAS_PIKEPDF:
            return False
        try:
            with pikepdf.open(pdf_path) as pdf:
                return cls._verify_pdf_bindings(pdf, expected_cells)
        except Exception:
            return False

    def tag_table(
        self,
        pdf_path: str,
        page_num: int,
        table_bbox: Tuple[float, float, float, float],
    ) -> TableTagResult:
        """Detect one table and refuse unbound automatic structure writing.

        Args:
            pdf_path: Path to PDF file.
            page_num: 0-indexed page number.
            table_bbox: Bounding box of the table.

        Returns:
            TableTagResult for this single table.
        """
        if not HAS_PYMUPDF:
            return TableTagResult(
                success=False,
                error="PyMuPDF required",
                error_code=TABLE_STRUCTURE_NOT_VERIFIED,
            )

        try:
            doc = fitz.open(pdf_path)
            try:
                if page_num >= len(doc):
                    return TableTagResult(
                        success=False,
                        error=f"Page {page_num} out of range",
                        error_code=TABLE_STRUCTURE_NOT_VERIFIED,
                    )

                page = doc[page_num]
                tables = page.find_tables()

                # Find the table matching the bbox
                target_table = None
                for table_index, table in enumerate(tables.tables):
                    if self._bbox_overlap(table.bbox, table_bbox) > 0.5:
                        target_table = table
                        break

                if target_table is None:
                    return TableTagResult(
                        success=False,
                        error="Table not found at specified location",
                        error_code=TABLE_STRUCTURE_NOT_VERIFIED,
                    )

                table_info = self._parse_table(target_table, page_num, table_index)
            finally:
                doc.close()

            safety_error = self._safety_error_code([table_info])
            if safety_error:
                return TableTagResult(
                    success=False,
                    tables_found=1,
                    total_cells=self._logical_cell_count([table_info]),
                    error=safety_error,
                    error_code=safety_error,
                )

            return self.write_tables(pdf_path, [table_info])

        except Exception as exc:
            logger.error("Single table tagging failed: %s", exc, exc_info=True)
            return TableTagResult(
                success=False,
                error=str(exc),
                error_code=TABLE_STRUCTURE_NOT_VERIFIED,
            )

    # ------------------------------------------------------------------
    # Verified content binding
    # ------------------------------------------------------------------

    @classmethod
    def _failure(cls, tables: List[TableInfo], error_code: str) -> TableTagResult:
        return TableTagResult(
            success=False,
            tables_found=len(tables),
            total_cells=cls._logical_cell_count(tables),
            header_cells=sum(
                sum(1 for cell in table.cells if cell.is_header) for table in tables
            ),
            error=error_code,
            error_code=error_code,
            tables=tables,
        )

    @classmethod
    def _not_verified(cls, tables: List[TableInfo]) -> TableTagResult:
        return cls._failure(tables, TABLE_STRUCTURE_NOT_VERIFIED)

    def _build_binding_plans(
        self, pdf: Any, fitz_doc: Any, tables: List[TableInfo]
    ) -> List[_PageBindingPlan]:
        """Prove every association without changing the cloned PDF."""
        if self._count_struct_type(pdf, "Table"):
            raise ValueError("pre-existing table structure cannot be merged safely")

        by_page: Dict[int, List[TableInfo]] = {}
        for table in tables:
            self._validate_regular_grid(table)
            if table.page_num < 0 or table.page_num >= len(pdf.pages):
                raise ValueError("table page is out of range")
            by_page.setdefault(table.page_num, []).append(table)

        parent_entries = self._read_parent_tree(pdf)
        used_parent_keys = set(parent_entries)
        for page in pdf.pages:
            if Name.StructParents in page.obj:
                used_parent_keys.add(int(page.obj[Name.StructParents]))
        next_parent_key = max(used_parent_keys, default=-1) + 1

        plans: List[_PageBindingPlan] = []
        for page_num, page_tables in sorted(by_page.items()):
            page = pdf.pages[page_num]
            ops = list(pikepdf.parse_content_stream(page))
            existing_mcids = self._existing_mcids(ops)

            if Name.StructParents in page.obj:
                struct_parent_key = int(page.obj[Name.StructParents])
            else:
                while next_parent_key in used_parent_keys:
                    next_parent_key += 1
                struct_parent_key = next_parent_key
                used_parent_keys.add(struct_parent_key)
                next_parent_key += 1

            existing_array = parent_entries.get(struct_parent_key, [])
            if not isinstance(existing_array, list):
                raise ValueError("page StructParents key does not map to an array")
            for mcid in existing_mcids:
                if mcid >= len(existing_array) or existing_array[mcid] is None:
                    raise ValueError("existing marked content has no ParentTree owner")

            content_blocks = self._unmarked_text_blocks(ops)
            bindings = self._associate_page_cells(
                fitz_doc[page_num], page_tables, content_blocks
            )
            starting_mcid = (
                max(max(existing_mcids, default=-1), len(existing_array) - 1) + 1
            )
            plans.append(
                _PageBindingPlan(
                    page_num=page_num,
                    struct_parent_key=struct_parent_key,
                    ops=ops,
                    tables=page_tables,
                    bindings=bindings,
                    starting_mcid=starting_mcid,
                )
            )
        return plans

    @staticmethod
    def _validate_regular_grid(table: TableInfo) -> None:
        if table.has_merged_cells or len(table.cells) != table.rows * table.cols:
            raise ValueError("ragged or merged table geometry is unsupported")
        positions = {(cell.row, cell.col) for cell in table.cells}
        expected = {
            (row, col) for row in range(table.rows) for col in range(table.cols)
        }
        if positions != expected:
            raise ValueError("table does not contain one cell per grid position")
        first_row = sorted(
            (cell for cell in table.cells if cell.row == 0), key=lambda cell: cell.col
        )
        if (
            not table.header_evidence_verified
            or table.header_rows != 1
            or len(first_row) != table.cols
            or any(not cell.is_header or cell.scope != "Column" for cell in first_row)
            or any(cell.is_header for cell in table.cells if cell.row != 0)
        ):
            raise ValueError("source-grounded column header evidence is missing")
        for cell in table.cells:
            if cell.row_span != 1 or cell.col_span != 1:
                raise ValueError("cell spans are unsupported")
            if not TableTagger._normalize_text(cell.text):
                raise ValueError("empty table cells cannot be content-bound")
            if len(cell.bbox) < 4 or not all(
                math.isfinite(float(value)) for value in cell.bbox[:4]
            ):
                raise ValueError("cell geometry is invalid")
            x0, y0, x1, y1 = (float(value) for value in cell.bbox[:4])
            if x1 <= x0 or y1 <= y0:
                raise ValueError("cell geometry is empty")

    @classmethod
    def _associate_page_cells(
        cls,
        fitz_page: Any,
        tables: List[TableInfo],
        content_blocks: List[_ContentBlock],
    ) -> Dict[Tuple[int, int], _CellBinding]:
        words = list(fitz_page.get_text("words"))
        normalized_blocks: Dict[str, List[_ContentBlock]] = {}
        for block in content_blocks:
            normalized = cls._normalize_text(block.text)
            if normalized:
                normalized_blocks.setdefault(normalized, []).append(block)

        used_words: Set[int] = set()
        used_blocks: Set[int] = set()
        bindings: Dict[Tuple[int, int], _CellBinding] = {}
        for table_index, table in enumerate(tables):
            for cell_index, cell in enumerate(
                sorted(table.cells, key=lambda item: (item.row, item.col))
            ):
                x0, y0, x1, y1 = (float(value) for value in cell.bbox[:4])
                selected: List[Tuple[int, Any]] = []
                for word_index, word in enumerate(words):
                    if len(word) < 5:
                        continue
                    center_x = (float(word[0]) + float(word[2])) / 2
                    center_y = (float(word[1]) + float(word[3])) / 2
                    if x0 <= center_x <= x1 and y0 <= center_y <= y1:
                        selected.append((word_index, word))
                if not selected or any(index in used_words for index, _ in selected):
                    raise ValueError("cell word geometry is missing or overlaps")
                selected.sort(
                    key=lambda item: (
                        item[1][5] if len(item[1]) > 5 else 0,
                        item[1][6] if len(item[1]) > 6 else 0,
                        item[1][7] if len(item[1]) > 7 else 0,
                    )
                )
                geometry_text = cls._normalize_text(
                    " ".join(str(word[4]) for _, word in selected)
                )
                cell_text = cls._normalize_text(cell.text)
                if geometry_text != cell_text:
                    raise ValueError("cell text does not match its geometric content")

                candidates = [
                    block
                    for block in normalized_blocks.get(cell_text, [])
                    if block.start not in used_blocks
                    and cls._block_belongs_to_cell(block, cell, fitz_page.rect.height)
                ]
                if len(candidates) != 1:
                    raise ValueError("cell content object is missing or ambiguous")
                block = candidates[0]
                used_words.update(index for index, _ in selected)
                used_blocks.add(block.start)
                bindings[(table_index, cell_index)] = _CellBinding(
                    table_index=table_index,
                    cell_index=cell_index,
                    block=block,
                )
        return bindings

    @classmethod
    def _unmarked_text_blocks(cls, ops: List[Any]) -> List[_ContentBlock]:
        blocks: List[_ContentBlock] = []
        marked_depth = 0
        index = 0
        while index < len(ops):
            op_name = str(ops[index].operator)
            if op_name in ("BMC", "BDC"):
                marked_depth += 1
                index += 1
                continue
            if op_name == "EMC":
                if marked_depth <= 0:
                    raise ValueError("unbalanced marked-content operators")
                marked_depth -= 1
                index += 1
                continue
            if op_name != "BT":
                index += 1
                continue

            start = index
            end = index + 1
            nested_mark = marked_depth > 0
            while end < len(ops) and str(ops[end].operator) != "ET":
                if str(ops[end].operator) in ("BMC", "BDC", "EMC"):
                    nested_mark = True
                end += 1
            if end >= len(ops):
                raise ValueError("unterminated text object")
            end += 1
            if not nested_mark:
                text = cls._extract_text(ops[start:end])
                if cls._normalize_text(text):
                    blocks.append(
                        _ContentBlock(
                            start=start,
                            end=end,
                            text=text,
                            anchor=cls._text_anchor(ops[start:end]),
                        )
                    )
            index = end
        if marked_depth != 0:
            raise ValueError("unbalanced marked-content operators")
        return blocks

    @staticmethod
    def _extract_text(ops: List[Any]) -> str:
        parts: List[str] = []
        for instruction in ops:
            op_name = str(instruction.operator)
            try:
                if op_name == "Tj" and instruction.operands:
                    parts.append(str(instruction.operands[0]))
                elif op_name == "TJ" and instruction.operands:
                    parts.extend(
                        str(item)
                        for item in instruction.operands[0]
                        if isinstance(item, String)
                    )
                elif op_name in ("'", '"') and instruction.operands:
                    parts.append(str(instruction.operands[-1]))
            except Exception:
                continue
        return "".join(parts)

    @staticmethod
    def _text_anchor(ops: List[Any]) -> Optional[Tuple[float, float]]:
        """Return the explicit PDF-space text origin when one is present."""
        anchor: Optional[Tuple[float, float]] = None
        for instruction in ops:
            try:
                op_name = str(instruction.operator)
                if op_name == "Tm" and len(instruction.operands) >= 6:
                    anchor = (
                        float(instruction.operands[4]),
                        float(instruction.operands[5]),
                    )
                elif op_name == "Td" and len(instruction.operands) >= 2:
                    delta_x = float(instruction.operands[0])
                    delta_y = float(instruction.operands[1])
                    anchor = (
                        (anchor[0] if anchor else 0.0) + delta_x,
                        (anchor[1] if anchor else 0.0) + delta_y,
                    )
            except Exception:
                continue
        return anchor

    @staticmethod
    def _block_belongs_to_cell(
        block: _ContentBlock, cell: CellInfo, page_height: float
    ) -> bool:
        """Use a real text origin to disambiguate repeated cell text."""
        if block.anchor is None:
            return True
        x, pdf_y = block.anchor
        fitz_y = float(page_height) - pdf_y
        x0, y0, x1, y1 = (float(value) for value in cell.bbox[:4])
        return x0 <= x <= x1 and y0 <= fitz_y <= y1

    @staticmethod
    def _normalize_text(text: Any) -> str:
        return " ".join(str(text or "").split()).casefold()

    @staticmethod
    def _existing_mcids(ops: List[Any]) -> Set[int]:
        mcids: Set[int] = set()
        for instruction in ops:
            if str(instruction.operator) != "BDC" or len(instruction.operands) < 2:
                continue
            properties = instruction.operands[1]
            if not hasattr(properties, "get"):
                continue
            raw_mcid = properties.get(Name("/MCID"))
            if raw_mcid is None:
                continue
            mcid = int(raw_mcid)
            if mcid < 0 or mcid in mcids:
                raise ValueError("duplicate or invalid MCID")
            mcids.add(mcid)
        return mcids

    def _apply_binding_plans(self, pdf: Any, plans: List[_PageBindingPlan]) -> None:
        from src.education.remediation.pdf_structure import PDFStructureTree

        tree = PDFStructureTree(pdf)
        parent_entries = self._read_parent_tree(pdf)

        for plan in plans:
            page = pdf.pages[plan.page_num]
            page_obj = page.obj
            next_mcid = plan.starting_mcid
            markers: List[Tuple[_ContentBlock, int, str]] = []
            existing_page_entry = parent_entries.get(plan.struct_parent_key, [])
            if not isinstance(existing_page_entry, list):
                raise ValueError("page StructParents key does not map to an array")
            page_array = list(existing_page_entry)

            for table_index, table in enumerate(plan.tables):
                table_kids = Array([])
                table_elem = pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.Table,
                            "/P": tree.struct_root,
                            "/Pg": page_obj,
                            "/K": table_kids,
                        }
                    )
                )
                self._append_top_level(tree.struct_root, table_elem)

                sorted_cells = sorted(
                    table.cells, key=lambda item: (item.row, item.col)
                )
                cells_by_row: Dict[int, List[Tuple[int, CellInfo]]] = {}
                for cell_index, cell in enumerate(sorted_cells):
                    cells_by_row.setdefault(cell.row, []).append((cell_index, cell))

                for row in range(table.rows):
                    row_kids = Array([])
                    row_elem = pdf.make_indirect(
                        Dictionary(
                            {
                                "/Type": Name.StructElem,
                                "/S": Name.TR,
                                "/P": table_elem,
                                "/Pg": page_obj,
                                "/K": row_kids,
                            }
                        )
                    )
                    table_kids.append(row_elem)
                    for cell_index, cell in cells_by_row[row]:
                        mcid = next_mcid
                        next_mcid += 1
                        cell_type = Name.TH if cell.is_header else Name.TD
                        mcr = Dictionary(
                            {"/Type": Name("/MCR"), "/MCID": mcid, "/Pg": page_obj}
                        )
                        cell_dict: Dict[str, Any] = {
                            "/Type": Name.StructElem,
                            "/S": cell_type,
                            "/P": row_elem,
                            "/Pg": page_obj,
                            "/K": mcr,
                        }
                        if cell.is_header:
                            scope = cell.scope or ("Column" if cell.row == 0 else "Row")
                            cell_dict["/A"] = Dictionary(
                                {"/O": Name.Table, "/Scope": Name(f"/{scope}")}
                            )
                        cell_elem = pdf.make_indirect(Dictionary(cell_dict))
                        row_kids.append(cell_elem)
                        while len(page_array) <= mcid:
                            page_array.append(None)
                        if page_array[mcid] is not None:
                            raise ValueError("ParentTree MCID slot is already owned")
                        page_array[mcid] = cell_elem
                        binding = plan.bindings[(table_index, cell_index)]
                        markers.append(
                            (binding.block, mcid, str(cell_type).lstrip("/"))
                        )

            new_ops = list(plan.ops)
            for block, mcid, tag_name in sorted(
                markers, key=lambda item: item[0].start, reverse=True
            ):
                new_ops.insert(
                    block.end,
                    pikepdf.ContentStreamInstruction([], Operator("EMC")),
                )
                new_ops.insert(
                    block.start,
                    pikepdf.ContentStreamInstruction(
                        [Name(f"/{tag_name}"), Dictionary({"/MCID": mcid})],
                        Operator("BDC"),
                    ),
                )
            page_obj[Name.Contents] = pdf.make_stream(
                pikepdf.unparse_content_stream(new_ops)
            )
            page_obj[Name.StructParents] = plan.struct_parent_key
            parent_entries[plan.struct_parent_key] = page_array

        self._write_parent_tree(tree.struct_root, parent_entries, pdf)
        if Name.MarkInfo not in pdf.Root:
            pdf.Root[Name.MarkInfo] = Dictionary({})
        pdf.Root[Name.MarkInfo][Name.Marked] = True

    @staticmethod
    def _append_top_level(struct_root: Any, element: Any) -> None:
        kids = struct_root.get(Name.K)
        children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
        if len(children) == 1 and hasattr(children[0], "get"):
            document = children[0]
            if str(document.get(Name.S, "")) == "/Document":
                doc_kids = document.get(Name.K)
                if doc_kids is None:
                    document[Name.K] = Array([element])
                elif isinstance(doc_kids, Array):
                    doc_kids.append(element)
                else:
                    document[Name.K] = Array([doc_kids, element])
                element[Name.P] = document
                return
        if kids is None:
            struct_root[Name.K] = Array([element])
        elif isinstance(kids, Array):
            kids.append(element)
        else:
            struct_root[Name.K] = Array([kids, element])

    @staticmethod
    def _read_parent_tree(pdf: Any) -> Dict[int, Any]:
        if Name.StructTreeRoot not in pdf.Root:
            return {}
        parent_tree = pdf.Root[Name.StructTreeRoot].get(Name.ParentTree)
        if parent_tree is None:
            return {}
        if parent_tree.get(Name("/Kids")) is not None:
            raise ValueError("nested ParentTree number trees are unsupported")
        nums = parent_tree.get(Name.Nums, Array([]))
        if len(nums) % 2:
            raise ValueError("ParentTree /Nums is malformed")
        result: Dict[int, Any] = {}
        for index in range(0, len(nums), 2):
            key = int(nums[index])
            if key in result:
                raise ValueError("ParentTree key is duplicated")
            value = nums[index + 1]
            result[key] = list(value) if isinstance(value, Array) else value
        return result

    @staticmethod
    def _write_parent_tree(struct_root: Any, entries: Dict[int, Any], pdf: Any) -> None:
        nums: List[Any] = []
        for key in sorted(entries):
            value = entries[key]
            serialized = (
                pdf.make_indirect(Array(value)) if isinstance(value, list) else value
            )
            nums.extend([key, serialized])
        parent_tree = struct_root.get(Name.ParentTree)
        if parent_tree is None:
            parent_tree = Dictionary({"/Nums": Array([])})
            struct_root[Name.ParentTree] = parent_tree
        parent_tree[Name.Nums] = Array(nums)

    @classmethod
    def _verify_pdf_bindings(cls, pdf: Any, expected_cells: int) -> bool:
        try:
            if Name.StructTreeRoot not in pdf.Root:
                return False
            parent_entries = cls._read_parent_tree(pdf)
            content_by_page: Dict[int, Dict[int, Tuple[str, str]]] = {}
            for page_num, page in enumerate(pdf.pages):
                seen: Dict[int, Tuple[str, str]] = {}
                stack: List[Optional[Tuple[int, str, List[Any]]]] = []
                for instruction in pikepdf.parse_content_stream(page):
                    op_name = str(instruction.operator)
                    if op_name == "BDC":
                        marker: Optional[Tuple[int, str, List[Any]]] = None
                        if len(instruction.operands) >= 2:
                            properties = instruction.operands[1]
                            raw_mcid = (
                                properties.get(Name("/MCID"))
                                if hasattr(properties, "get")
                                else None
                            )
                            if raw_mcid is not None:
                                mcid = int(raw_mcid)
                                if mcid in seen:
                                    return False
                                marker = (
                                    mcid,
                                    str(instruction.operands[0]).lstrip("/"),
                                    [],
                                )
                        stack.append(marker)
                    elif op_name == "BMC":
                        stack.append(None)
                    elif op_name == "EMC":
                        if not stack:
                            return False
                        marker = stack.pop()
                        if marker is not None:
                            text = cls._extract_text(marker[2])
                            seen[marker[0]] = (marker[1], text)
                    else:
                        for marker in stack:
                            if marker is not None:
                                marker[2].append(instruction)
                if stack:
                    return False
                content_by_page[page_num] = seen

            cells: List[Any] = []
            table_count = 0

            def collect(element: Any, parent: Any = None) -> bool:
                nonlocal table_count
                if not hasattr(element, "get"):
                    return True
                element_type = str(element.get(Name.S, "")).lstrip("/")
                parent_type = (
                    str(parent.get(Name.S, "")).lstrip("/")
                    if hasattr(parent, "get")
                    else ""
                )
                declared_parent = element.get(Name.P)
                if parent is not None and not cls._same_object(declared_parent, parent):
                    return False
                if element_type == "Table":
                    table_count += 1
                    kids = element.get(Name.K)
                    if not isinstance(kids, Array) or not kids:
                        return False
                    if any(
                        not hasattr(kid, "get")
                        or str(kid.get(Name.S, "")).lstrip("/") != "TR"
                        for kid in kids
                    ):
                        return False
                elif element_type == "TR":
                    if parent_type != "Table":
                        return False
                    kids = element.get(Name.K)
                    if not isinstance(kids, Array) or not kids:
                        return False
                    if any(
                        not hasattr(kid, "get")
                        or str(kid.get(Name.S, "")).lstrip("/") not in ("TH", "TD")
                        for kid in kids
                    ):
                        return False
                if element_type in ("TH", "TD"):
                    if parent_type != "TR":
                        return False
                    cells.append(element)
                kids = element.get(Name.K)
                if isinstance(kids, Array):
                    for kid in kids:
                        if (
                            hasattr(kid, "get")
                            and kid.get(Name.Type) == Name.StructElem
                        ):
                            if not collect(kid, element):
                                return False
                elif hasattr(kids, "get") and kids.get(Name.Type) == Name.StructElem:
                    if not collect(kids, element):
                        return False
                return True

            root_kids = pdf.Root[Name.StructTreeRoot].get(Name.K, Array([]))
            for root_kid in root_kids if isinstance(root_kids, Array) else [root_kids]:
                if not collect(root_kid):
                    return False
            if table_count <= 0 or len(cells) != expected_cells:
                return False

            for cell in cells:
                mcr = cell.get(Name.K)
                if not hasattr(mcr, "get") or str(mcr.get(Name.Type, "")) != "/MCR":
                    return False
                mcid = int(mcr[Name("/MCID")])
                page_ref = mcr.get(Name.Pg) or cell.get(Name.Pg)
                page_num = cls._page_index_for_ref(pdf, page_ref)
                if page_num is None:
                    return False
                struct_parent_key = int(pdf.pages[page_num].obj[Name.StructParents])
                owners = parent_entries.get(struct_parent_key, [])
                if not isinstance(owners, list):
                    return False
                if mcid >= len(owners) or not cls._same_object(owners[mcid], cell):
                    return False
                content = content_by_page.get(page_num, {}).get(mcid)
                expected_tag = str(cell[Name.S]).lstrip("/")
                if content is None or content[0] != expected_tag:
                    return False
                if not cls._normalize_text(content[1]):
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def _page_index_for_ref(pdf: Any, page_ref: Any) -> Optional[int]:
        for page_num, page in enumerate(pdf.pages):
            try:
                if page.obj.objgen == page_ref.objgen:
                    return page_num
            except Exception:
                continue
        return None

    @staticmethod
    def _same_object(first: Any, second: Any) -> bool:
        try:
            first_objgen = first.objgen
            second_objgen = second.objgen
            if first_objgen == (0, 0) or second_objgen == (0, 0):
                return first is second
            return first_objgen == second_objgen
        except Exception:
            return first is second

    @staticmethod
    def _count_struct_type(pdf: Any, struct_type: str) -> int:
        if Name.StructTreeRoot not in pdf.Root:
            return 0
        count = 0

        def visit(element: Any) -> None:
            nonlocal count
            if not hasattr(element, "get"):
                return
            if str(element.get(Name.S, "")).lstrip("/") == struct_type:
                count += 1
            kids = element.get(Name.K)
            if isinstance(kids, Array):
                for kid in kids:
                    if hasattr(kid, "get") and kid.get(Name.Type) == Name.StructElem:
                        visit(kid)
            elif hasattr(kids, "get") and kids.get(Name.Type) == Name.StructElem:
                visit(kids)

        kids = pdf.Root[Name.StructTreeRoot].get(Name.K, Array([]))
        for kid in kids if isinstance(kids, Array) else [kids]:
            visit(kid)
        return count

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
                for table_index, table in enumerate(found.tables):
                    table_info = self._parse_table(table, page_num, table_index)
                    if table_info.rows >= 2 and table_info.cols >= 2:
                        tables.append(table_info)
            except Exception as exc:
                raise ValueError(
                    f"table detection incomplete on page {page_num + 1}"
                ) from exc
        return tables

    def _parse_table(self, table, page_num: int, table_index: int = 0) -> TableInfo:
        """Parse a PyMuPDF table into our TableInfo structure."""
        cells: List[CellInfo] = []
        rows_data = table.extract()

        for row_idx, row in enumerate(rows_data):
            for col_idx, cell_text in enumerate(row):
                text = cell_text if cell_text else ""
                # Get cell bbox if available
                try:
                    # PyMuPDF's flat ``table.cells`` is column-major in some
                    # versions. ``table.rows`` is the stable row/column view.
                    cell = table.rows[row_idx].cells[col_idx]
                    bbox = cell[:4] if cell else table.bbox
                except (AttributeError, IndexError, TypeError):
                    bbox = table.bbox

                cells.append(
                    CellInfo(
                        row=row_idx,
                        col=col_idx,
                        text=str(text).strip(),
                        bbox=bbox,
                    )
                )

        table_info = TableInfo(
            page_num=page_num,
            bbox=table.bbox,
            table_index=table_index,
            rows=len(rows_data),
            cols=len(rows_data[0]) if rows_data else 0,
            cells=cells,
        )
        self._apply_detected_header_evidence(table, table_info)
        return table_info

    @classmethod
    def _apply_detected_header_evidence(cls, detected: Any, table: TableInfo) -> None:
        """Accept only PyMuPDF headers that exactly match the real first row."""
        header = getattr(detected, "header", None)
        if header is None or bool(getattr(header, "external", True)):
            return
        names = list(getattr(header, "names", []) or [])
        header_cells = list(getattr(header, "cells", []) or [])
        first_row = sorted(
            (cell for cell in table.cells if cell.row == 0), key=lambda cell: cell.col
        )
        if len(names) != table.cols or len(header_cells) != table.cols:
            return
        if len(first_row) != table.cols:
            return
        for column, cell in enumerate(first_row):
            detected_bbox = header_cells[column]
            if detected_bbox is None:
                return
            if cls._normalize_text(names[column]) != cls._normalize_text(cell.text):
                return
            if not cls._bbox_matches(detected_bbox, cell.bbox):
                return
        for cell in first_row:
            cell.is_header = True
            cell.scope = "Column"
        table.header_rows = 1
        table.header_cols = 0
        table.header_evidence_verified = True

    @staticmethod
    def _bbox_matches(first: Any, second: Any, tolerance: float = 0.5) -> bool:
        try:
            return all(
                abs(float(first[index]) - float(second[index])) <= tolerance
                for index in range(4)
            )
        except (IndexError, TypeError, ValueError):
            return False

    @staticmethod
    def _effective_grid(table: TableInfo) -> Optional[Tuple[int, int]]:
        """Return conservative dimensions from declarations and cell evidence."""
        try:
            declared_rows = int(table.rows)
            declared_cols = int(table.cols)
        except (TypeError, ValueError):
            return None
        if declared_rows <= 0 or declared_cols <= 0:
            return None

        effective_rows = declared_rows
        effective_cols = declared_cols
        for cell in table.cells:
            try:
                row = int(cell.row)
                col = int(cell.col)
                row_span = max(1, int(cell.row_span))
                col_span = max(1, int(cell.col_span))
            except (TypeError, ValueError):
                return None
            if row < 0 or col < 0:
                return None
            effective_rows = max(effective_rows, row + row_span)
            effective_cols = max(effective_cols, col + col_span)

        return effective_rows, effective_cols

    @classmethod
    def _logical_cell_count(cls, tables: List[TableInfo]) -> int:
        """Count each conservative evidenced grid without trusting ragged metadata."""
        total = 0
        for table in tables:
            grid = cls._effective_grid(table)
            grid_cells = grid[0] * grid[1] if grid else 0
            total += max(len(table.cells), grid_cells)
        return total

    @classmethod
    def _safety_error_code(cls, tables: List[TableInfo]) -> Optional[str]:
        """Classify invalid or amplified grids before any PDF write occurs."""
        if len(tables) > MAX_TABLES:
            return TABLE_STRUCTURE_TOO_COMPLEX

        total_cells = 0
        for table in tables:
            grid = cls._effective_grid(table)
            if grid is None:
                return TABLE_STRUCTURE_NOT_VERIFIED
            rows, columns = grid
            if columns > MAX_TABLE_COLUMNS:
                return TABLE_STRUCTURE_TOO_COMPLEX
            total_cells += max(len(table.cells), rows * columns)
            if total_cells > MAX_TABLE_CELLS:
                return TABLE_STRUCTURE_TOO_COMPLEX
        return None

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
