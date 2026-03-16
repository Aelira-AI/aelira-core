"""Reading order verification for PDFs."""

import logging
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
            return []

        structure_texts: List[Dict] = []

        try:
            with pikepdf.open(file_path) as pdf:
                if Name.StructTreeRoot not in pdf.Root:
                    return []

                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K not in struct_root:
                    return []

                # Build a page-object -> index lookup so we can filter
                # structure elements by the page they reference.
                page_obj_ids = {}
                for idx, pg in enumerate(pdf.pages):
                    page_obj_ids[id(pg.obj)] = idx

                def _page_index_of(pg_ref) -> int:
                    """Resolve a /Pg reference to a 0-based page index."""
                    try:
                        return page_obj_ids.get(id(pg_ref), -1)
                    except Exception:
                        return -1

                # Collect structure elements with their content,
                # only for elements that belong to *page_num*.
                def collect_text(elem, inherited_page=-1, depth=0):
                    """Recursively collect text from structure elements."""
                    if depth > 50:
                        return

                    # Determine which page this element belongs to.
                    elem_page = inherited_page
                    if hasattr(elem, "Pg"):
                        resolved = _page_index_of(elem.Pg)
                        if resolved >= 0:
                            elem_page = resolved

                    on_target_page = elem_page == page_num

                    # Check for ActualText (explicit text content)
                    if on_target_page and hasattr(elem, "ActualText"):
                        try:
                            actual_text = str(elem.ActualText)
                            if actual_text.strip():
                                structure_texts.append(
                                    {
                                        "text": actual_text.strip(),
                                        "source": "ActualText",
                                    }
                                )
                        except Exception:
                            pass

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
                            pass

                    # Recurse into children (pass page context down)
                    if hasattr(elem, "K"):
                        kids = elem.K
                        if hasattr(kids, "__iter__") and not isinstance(
                            kids, (str, bytes)
                        ):
                            for kid in kids:
                                if hasattr(kid, "S") or hasattr(kid, "K"):
                                    collect_text(kid, elem_page, depth + 1)
                        elif hasattr(kids, "S") or hasattr(kids, "K"):
                            collect_text(kids, elem_page, depth + 1)

                # Start collection from root kids
                kids = struct_root[Name.K]
                if hasattr(kids, "__iter__") and not isinstance(kids, (str, bytes)):
                    for kid in kids:
                        collect_text(kid)
                elif hasattr(kids, "S") or hasattr(kids, "K"):
                    collect_text(kids)

        except Exception as e:
            logger.warning(f"[ReadingOrderVerifier] Error reading structure tree: {e}")

        return structure_texts

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
        """Compare visual and structure reading orders.

        Args:
            page_num: 1-indexed page number
            visual: Visual order text blocks
            structure: Structure tree order text blocks
            multi_column: Whether this page has multi-column layout

        Returns:
            ReadingOrderIssue if orders differ significantly, None otherwise
        """
        if not visual or not structure:
            return None

        # Extract text snippets for comparison
        visual_texts = [b["text"][:100] for b in visual[:10]]
        structure_texts = [b["text"][:100] for b in structure[:10]]

        # Count how many visual items appear *somewhere* in the structure
        # texts (regardless of position) -- this tells us if the content is
        # present at all vs genuinely missing/reordered.
        matches = 0
        mismatches = 0
        # For multi-column, allow wider positional tolerance since column
        # order inherently differs from pure top-to-bottom visual sort.
        pos_tolerance = 5 if multi_column else 3

        for i, v_text in enumerate(visual_texts[:5]):
            v_text_lower = v_text.lower().strip()
            if not v_text_lower:
                continue
            found_match = False

            for j, s_text in enumerate(structure_texts):
                s_text_lower = s_text.lower().strip()
                # Check for significant text overlap
                if v_text_lower in s_text_lower or s_text_lower in v_text_lower:
                    if abs(i - j) <= pos_tolerance:
                        matches += 1
                        found_match = True
                        break

            if not found_match:
                mismatches += 1

        # Multi-column layouts have inherently different visual vs structure
        # order (left-col then right-col vs top-to-bottom).  Require a
        # higher mismatch ratio before flagging.
        threshold = 0.7 if multi_column else 0.4

        total = matches + mismatches
        if total > 0 and mismatches / total > threshold:
            severity = "critical" if mismatches / total > 0.8 else "warning"
            return ReadingOrderIssue(
                page_number=page_num,
                expected_order=visual_texts[:5],
                actual_order=structure_texts[:5],
                severity=severity,
                recommendation="Structure tree reading order differs from visual layout. "
                "Review and correct the reading order for screen reader users.",
                visual_positions=(
                    [{"x": b["x"], "y": b["y"]} for b in visual[:5]] if visual else None
                ),
            )

        return None
