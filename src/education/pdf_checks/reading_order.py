"""Reading order verification for PDFs."""

import logging
from bisect import bisect_right
from collections import Counter
from .completeness import record_incomplete_check
from typing import Dict, List, Optional

import fitz  # PyMuPDF for visual text extraction

try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

from .models import ReadingOrderIssue, ReadingOrderResult

logger = logging.getLogger(__name__)


def _structure_children(kids):
    """Yield dictionary children from either legal form of a structure /K."""
    if isinstance(kids, pikepdf.Dictionary):
        yield kids
    elif isinstance(kids, pikepdf.Array):
        for kid in kids:
            if isinstance(kid, pikepdf.Dictionary):
                yield kid


def _element_page(elem, inherited_page, page_indices):
    """Resolve /Pg within the current document, inheriting only when absent."""
    if Name.Pg not in elem:
        return inherited_page
    try:
        reference = elem[Name.Pg]
        if reference.is_indirect:
            page_index = page_indices.get(reference.objgen)
            if page_index is not None:
                return page_index
    except (AttributeError, TypeError, ValueError):
        pass
    # An explicit but invalid reference cannot borrow its parent's page.
    record_incomplete_check("reading_order.page_reference")
    return -1


class ReadingOrderVerifier:
    """Verify that PDF reading order matches visual layout order.

    Multi-column PDFs often have incorrect reading order when the structure
    tree doesn't properly account for column layout.  This checker compares
    the visual order (sorted by y-position, then x-position) with the
    structure tree order.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, file_path: str, max_pages: int = 10) -> ReadingOrderResult:
        """Verify PDF reading order against visual layout.

        Args:
            file_path: Path to PDF file
            max_pages: Maximum pages to analyze (for performance)

        Returns:
            ReadingOrderResult with issues found and compliance score
        """
        issues: List[ReadingOrderIssue] = []
        has_structure_tree = False
        multi_column_detected = False
        pages_analyzed = 0
        total_pages = 0

        try:
            with fitz.open(file_path) as doc:
                total_pages = len(doc)
                pages_to_check = min(total_pages, max_pages)

                for page_num in range(pages_to_check):
                    page = doc[page_num]
                    pages_analyzed += 1

                    # Get visual text order (sorted by position)
                    visual_blocks = self._get_visual_text_order(page)

                    # Detect multi-column layout
                    if self._detect_multi_column(visual_blocks):
                        multi_column_detected = True

                    # Get structure tree order if available
                    structure_blocks = self._get_structure_tree_order(
                        page, file_path, page_num
                    )

                    if structure_blocks:
                        has_structure_tree = True

                        # Skip reading order comparison for pages with
                        # table structures — tables are correctly stored
                        # row-by-row in the structure tree, which differs
                        # from visual y-sort (column-by-column).  This is
                        # intentional, not a reading order bug.
                        if self._page_has_tables(file_path, page_num):
                            logger.debug(
                                "[ReadingOrderVerifier] Skipping page %d "
                                "(has table structures)",
                                page_num + 1,
                            )
                            continue

                        # Compare orders (multi-column pages get relaxed threshold)
                        is_multi_col = self._detect_multi_column(visual_blocks)
                        issue = self._compare_reading_orders(
                            page_num + 1,
                            visual_blocks,
                            structure_blocks,
                            multi_column=is_multi_col,
                        )
                        if issue:
                            issues.append(issue)
                    elif visual_blocks:
                        # No structure tree - this is a problem for accessibility
                        issues.append(
                            ReadingOrderIssue(
                                page_number=page_num + 1,
                                expected_order=[
                                    b["text"][:50] for b in visual_blocks[:5]
                                ],
                                actual_order=[],
                                severity="critical",
                                recommendation="Add structure tags to define reading order for screen readers",
                            )
                        )

        except Exception as e:
            record_incomplete_check("reading_order.check")
            logger.error(f"[ReadingOrderVerifier] Error verifying reading order: {e}")
            return ReadingOrderResult(
                total_pages=total_pages,
                pages_analyzed=pages_analyzed,
                issues=[
                    ReadingOrderIssue(
                        page_number=0,
                        expected_order=[],
                        actual_order=[],
                        severity="critical",
                        recommendation=f"Error reading PDF: {str(e)}",
                    )
                ],
                compliance_score=0.0,
                has_structure_tree=False,
            )

        # Calculate compliance score
        if pages_analyzed == 0:
            compliance_score = 0.0
        elif not has_structure_tree:
            compliance_score = 0.0  # No structure = no accessibility
        else:
            # Deduct points for each issue
            critical_count = sum(1 for i in issues if i.severity == "critical")
            warning_count = sum(1 for i in issues if i.severity == "warning")
            deductions = (critical_count * 20) + (warning_count * 5)
            compliance_score = max(0.0, 100.0 - deductions)

        return ReadingOrderResult(
            total_pages=total_pages,
            pages_analyzed=pages_analyzed,
            issues=issues,
            compliance_score=compliance_score,
            has_structure_tree=has_structure_tree,
            multi_column_detected=multi_column_detected,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_visual_text_order(self, page) -> List[Dict]:
        """Extract text blocks from page sorted by visual position.

        Sorting: Primary by y-position (top to bottom), secondary by x-position
        (left to right). This matches natural reading order for single-column layouts.

        Args:
            page: PyMuPDF page object

        Returns:
            List of dicts with 'text', 'bbox' (x0, y0, x1, y1) sorted by position
        """
        blocks: List[Dict] = []

        try:
            # Get text blocks with position info
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    bbox = block.get("bbox", (0, 0, 0, 0))
                    # Collect all text from spans in the block
                    text_parts = []
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text_parts.append(span.get("text", ""))

                    text = " ".join(text_parts).strip()
                    if text:  # Skip empty blocks
                        blocks.append(
                            {
                                "text": text,
                                "bbox": bbox,
                                "y": bbox[1],  # Top y-coordinate
                                "x": bbox[0],  # Left x-coordinate
                            }
                        )

            # Sort by y-position first (with tolerance for same line), then x
            # Use a tolerance of 5 points for "same line" detection
            def sort_key(b):
                y_bucket = int(b["y"] / 10) * 10  # Group by ~10pt lines
                return (y_bucket, b["x"])

            blocks.sort(key=sort_key)

        except Exception as e:
            record_incomplete_check("reading_order.sort_key")
            logger.warning(
                f"[ReadingOrderVerifier] Error getting visual text order: {e}"
            )

        return blocks

    def _get_structure_tree_order(
        self, page, file_path: str, page_num: int
    ) -> List[Dict]:
        """Get text order from PDF structure tree for a specific page.

        Args:
            page: PyMuPDF page object (for fallback)
            file_path: Path to PDF file
            page_num: 0-indexed page number

        Returns:
            List of dicts with 'text' in structure tree order
        """
        if not HAS_PIKEPDF:
            record_incomplete_check("reading_order.dependency")
            return []

        structure_texts: List[Dict] = []

        try:
            with pikepdf.open(file_path) as pdf:
                if Name.StructTreeRoot not in pdf.Root:
                    return []

                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K not in struct_root:
                    return []

                # Object number and generation are stable across wrappers;
                # this lookup belongs only to the currently opened document.
                page_indices = {pg.obj.objgen: idx for idx, pg in enumerate(pdf.pages)}

                # Collect structure elements with their content,
                # only for elements that belong to *page_num*.
                def collect_text(elem, inherited_page=-1, depth=0):
                    """Recursively collect text from structure elements."""
                    if depth > 50:
                        record_incomplete_check("reading_order.structure_depth_limit")
                        return

                    # Determine which page this element belongs to.
                    elem_page = _element_page(elem, inherited_page, page_indices)

                    on_target_page = elem_page == page_num

                    # ActualText replaces this element and its descendants;
                    # collecting both would duplicate the represented content.
                    if hasattr(elem, "ActualText"):
                        try:
                            actual_text = str(elem.ActualText)
                            if on_target_page and actual_text.strip():
                                structure_texts.append(
                                    {
                                        "text": actual_text.strip(),
                                        "source": "ActualText",
                                    }
                                )
                        except Exception:
                            record_incomplete_check("reading_order.collect_text")
                        if elem_page < 0:
                            record_incomplete_check("reading_order.replacement_page")
                        return

                    # Check for Alt text
                    if on_target_page and hasattr(elem, "Alt"):
                        try:
                            alt_text = str(elem.Alt)
                            if alt_text.strip():
                                structure_texts.append(
                                    {
                                        "text": alt_text.strip(),
                                        "source": "Alt",
                                    }
                                )
                        except Exception:
                            record_incomplete_check("reading_order.collect_text")
                            pass

                    # Recurse into children (pass page context down)
                    if hasattr(elem, "K"):
                        for kid in _structure_children(elem.K):
                            collect_text(kid, elem_page, depth + 1)

                # Start collection from root kids
                for kid in _structure_children(struct_root[Name.K]):
                    collect_text(kid)

        except Exception as e:
            record_incomplete_check("reading_order.collect_text")
            logger.warning(f"[ReadingOrderVerifier] Error reading structure tree: {e}")

        return structure_texts

    def _page_has_tables(self, file_path: str, page_num: int) -> bool:
        """Check if a page's structure tree contains table elements.

        Tables are stored row-by-row in the structure tree, which
        intentionally differs from visual y-sorted order.  Reading order
        comparison is not meaningful for table content.

        Args:
            file_path: Path to PDF file
            page_num: 0-indexed page number

        Returns:
            True if page has table structures
        """
        if not HAS_PIKEPDF:
            record_incomplete_check("reading_order.dependency")
            return False

        table_types = {"/Table", "/TR", "/TD", "/TH", "/THead", "/TBody", "/TFoot"}

        try:
            with pikepdf.open(file_path) as pdf:
                if Name.StructTreeRoot not in pdf.Root:
                    return False

                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K not in struct_root:
                    return False

                page_indices = {pg.obj.objgen: idx for idx, pg in enumerate(pdf.pages)}

                def _has_table(elem, inherited_page=-1, depth=0):
                    if depth > 50:
                        record_incomplete_check("reading_order.structure_depth_limit")
                        return False

                    elem_page = _element_page(elem, inherited_page, page_indices)

                    # Check if this element is a table type on our page
                    if hasattr(elem, "S") and elem_page == page_num:
                        if str(elem.S) in table_types:
                            return True

                    # Recurse into children
                    if hasattr(elem, "K"):
                        for kid in _structure_children(elem.K):
                            if _has_table(kid, elem_page, depth + 1):
                                return True

                    return False

                for kid in _structure_children(struct_root[Name.K]):
                    if _has_table(kid):
                        return True

        except Exception as e:
            record_incomplete_check("reading_order._has_table")
            logger.warning("[ReadingOrderVerifier] Error checking for tables: %s", e)

        return False

    def _detect_multi_column(self, blocks: List[Dict]) -> bool:
        """Detect if page has multi-column layout.

        Heuristic: If there are text blocks at similar y-positions but
        significantly different x-positions, it's likely multi-column.

        Args:
            blocks: List of text blocks with 'y' and 'x' coordinates

        Returns:
            True if multi-column layout detected
        """
        if len(blocks) < 4:
            return False

        # Group blocks by approximate y-position (within 20pts)
        y_groups: Dict[int, List[float]] = {}
        for block in blocks:
            y_bucket = int(block["y"] / 20) * 20
            if y_bucket not in y_groups:
                y_groups[y_bucket] = []
            y_groups[y_bucket].append(block["x"])

        # Check if any y-group has blocks at very different x positions
        for y_pos, x_values in y_groups.items():
            if len(x_values) >= 2:
                x_values.sort()
                # If there's a gap > 100pts between x values, likely multi-column
                for i in range(len(x_values) - 1):
                    if x_values[i + 1] - x_values[i] > 100:
                        return True

        return False

    def _compare_reading_orders(
        self,
        page_num: int,
        visual: List[Dict],
        structure: List[Dict],
        multi_column: bool = False,
    ) -> Optional[ReadingOrderIssue]:
        """Compare complete text sequences, not positions of extraction blocks.

        Args:
            page_num: 1-indexed page number
            visual: Visual order text blocks
            structure: Structure tree order text blocks
            multi_column: Whether this page has multi-column layout

        Returns:
            ReadingOrderIssue if comparable sequences differ, None otherwise.

        PDF blocks and structure elements need not have matching boundaries.
        Whitespace-normalized words retain order and multiplicity across those
        boundaries. A short substring cannot stand in for a whole paragraph.
        Nonvisual alternatives and unequal ActualText substitutions need review;
        they are not sufficient evidence of a successful reading-order check.
        """
        visual_texts = [b["text"][:100] for b in visual[:5]]
        structure_texts = [b["text"][:100] for b in structure[:5]]

        def issue(recommendation, severity="warning"):
            return ReadingOrderIssue(
                page_number=page_num,
                expected_order=visual_texts,
                actual_order=structure_texts,
                severity=severity,
                recommendation=recommendation,
                visual_positions=(
                    [{"x": b.get("x", 0), "y": b.get("y", 0)} for b in visual[:5]]
                    if visual
                    else None
                ),
            )

        # Bound work without silently certifying an unchecked page suffix.
        if any(
            len(blocks) > 20000 or sum(len(b["text"]) for b in blocks) > 200000
            for blocks in (visual, structure)
        ):
            record_incomplete_check("reading_order.comparison_limit")
            return issue("Reading-order comparison limit exceeded; review this page.")

        nonvisual_review = None
        if any(b.get("source") == "Alt" for b in structure):
            # Image alternatives are not rendered page text. Their placement
            # needs human review, not a failed text-extraction check.
            nonvisual_review = issue(
                "Review the placement of image descriptions in the reading order. "
                "A text-only comparison cannot verify their position relative to images."
            )
            structure = [b for b in structure if b.get("source") != "Alt"]

        def words(blocks):
            return [
                word for block in blocks for word in block["text"].casefold().split()
            ]

        visual_words = words(visual)
        structure_words = words(structure)
        if visual_words == structure_words:
            return nonvisual_review

        if Counter(visual_words) != Counter(structure_words):
            return issue(
                "Structure text does not correspond to all visible text. Review "
                "missing, repeated, or substituted content before verifying reading order.",
                "critical",
            )

        if multi_column:
            column_order = self._column_reading_order(visual)
            if column_order is not None and words(column_order) == structure_words:
                return nonvisual_review

        return issue(
            "Structure tree reading order differs from visual layout. "
            "Review and correct the reading order for screen reader users."
        )

    def _column_reading_order(self, blocks: List[Dict]) -> Optional[List[Dict]]:
        """Offer a two-column alternative only where bounding boxes support it.

        Full-width headings separate vertical bands. Within a band, preserve
        top-to-bottom order in each column and read the left column first.
        Ambiguous layouts retain the original comparison, never a wider index
        tolerance that could also accept missing or arbitrarily reordered text.
        """
        if not blocks or any("bbox" not in block for block in blocks):
            return None
        starts = sorted({block["bbox"][0] for block in blocks})
        gaps = [(right - left, right) for left, right in zip(starts, starts[1:])]
        if not gaps:
            return None
        gap, right_start = max(gaps)
        if gap <= 100:
            return None

        left, right, spanning = [], [], []
        for block in blocks:
            x0, _, x1, _ = block["bbox"]
            if x0 >= right_start:
                right.append(block)
            elif x1 < right_start:
                left.append(block)
            else:
                spanning.append(block)
        if len(left) < 2 or len(right) < 2:
            return None
        if max(min(b["bbox"][1] for b in column) for column in (left, right)) >= min(
            max(b["bbox"][3] for b in column) for column in (left, right)
        ):
            return None

        spanning.sort(key=lambda b: b["bbox"][1])
        if any(a["bbox"][3] > b["bbox"][1] for a, b in zip(spanning, spanning[1:])):
            return None
        bands = [[[], []] for _ in range(len(spanning) + 1)]
        span_bottoms = [span["bbox"][3] for span in spanning]
        for column_index, column in enumerate((left, right)):
            for block in column:
                band_index = bisect_right(span_bottoms, block["bbox"][1])
                if (
                    band_index < len(spanning)
                    and block["bbox"][3] > spanning[band_index]["bbox"][1]
                ):
                    return None
                bands[band_index][column_index].append(block)
        ordered = []
        for index, band in enumerate(bands):
            for column in band:
                ordered.extend(
                    sorted(column, key=lambda b: (b["bbox"][1], b["bbox"][0]))
                )
            if index < len(spanning):
                ordered.append(spanning[index])
        return ordered
