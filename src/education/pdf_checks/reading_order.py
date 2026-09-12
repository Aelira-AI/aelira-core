"""Reading order verification for PDFs."""

import logging
from bisect import bisect_right
from collections import Counter, defaultdict
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


class _StructureBlocks(list):
    """Text blocks with local completeness state, also retained in partial scans."""

    incomplete = False


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
    return -2


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

                    if structure_blocks or getattr(
                        structure_blocks, "incomplete", False
                    ):
                        has_structure_tree = True

                        # Compare orders (multi-column pages get relaxed threshold)
                        is_multi_col = self._detect_multi_column(visual_blocks)
                        if any("table_id" in block for block in structure_blocks):
                            issue = self._compare_table_reading_orders(
                                page_num + 1, page, visual_blocks, structure_blocks
                            )
                        else:
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
            blocks = _StructureBlocks()
            blocks.incomplete = True
            return blocks

        structure_texts = _StructureBlocks()

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

                from .marked_content import MarkedContentResolver

                resolver = MarkedContentResolver(pdf, file_path)
                visits = 0
                table_count = 0
                role_map = struct_root.get("/RoleMap", {})

                def collect_text(
                    kid, owner=None, inherited_page=-1, depth=0, in_table=False
                ):
                    """Return page-labelled blocks in semantic /K traversal order."""
                    nonlocal visits, table_count
                    visits += 1
                    if depth > 50 or visits > 20000:
                        resolver.fail("structure_depth_limit")
                        return []
                    if isinstance(kid, pikepdf.Array):
                        if len(kid) > 20000:
                            resolver.fail("structure_depth_limit")
                            return []
                        return [
                            block
                            for item in kid
                            for block in collect_text(
                                item, owner, inherited_page, depth + 1, in_table
                            )
                        ]
                    is_mcr = isinstance(kid, pikepdf.Dictionary) and (
                        kid.get("/Type") == Name.MCR or "/MCID" in kid
                    )
                    if isinstance(kid, int) or is_mcr:
                        ref_page = inherited_page
                        if is_mcr:
                            ref_page = _element_page(kid, inherited_page, page_indices)
                            if "/Stm" in kid or "/StmOwn" in kid:
                                resolver.fail("stream_scope")
                                return []
                        key = resolver.resolve(
                            owner,
                            kid.get("/MCID") if is_mcr else kid,
                            ref_page,
                            invalid_page=ref_page == -2,
                        )
                        if key is None:
                            return []
                        # Retain page ownership even for empty/non-target content.
                        block = resolver.text(key) if key[0] == page_num else None
                        return [(key[0], block)]
                    if not isinstance(kid, pikepdf.Dictionary):
                        resolver.fail("structure_reference")
                        return []
                    if kid.get("/Type") == Name.OBJR:
                        resolver.fail("object_reference")
                        return []
                    elem_page = _element_page(kid, inherited_page, page_indices)
                    if elem_page == -2:
                        resolver.fail("page_reference")
                    role_value = kid.get("/S")
                    if "/S" in kid and not isinstance(role_value, pikepdf.Name):
                        resolver.fail("table_role")
                        role = ""
                    else:
                        role = str(role_value) if role_value is not None else ""
                    seen_roles = set()
                    while role in role_map:
                        if role in seen_roles or len(seen_roles) >= 50:
                            resolver.fail("table_role_map")
                            break
                        seen_roles.add(role)
                        mapped_role = role_map[role]
                        if not isinstance(mapped_role, pikepdf.Name):
                            resolver.fail("table_role_map")
                            break
                        role = str(mapped_role)
                    is_table = role == "/Table"
                    descendants = (
                        collect_text(
                            kid.K, kid, elem_page, depth + 1, in_table or is_table
                        )
                        if "/K" in kid
                        else []
                    )
                    on_page = elem_page == page_num or any(
                        number == page_num for number, _ in descendants
                    )
                    if on_page and is_table and in_table:
                        resolver.fail("table_nested")
                    if (
                        on_page
                        and role in {"/TR", "/TH", "/TD", "/THead", "/TBody", "/TFoot"}
                        and not in_table
                    ):
                        resolver.fail("table_orphan")
                    if is_table:
                        table_count += 1
                        page_blocks = [
                            block
                            for number, block in descendants
                            if number == page_num and block
                        ]
                        if (elem_page in (-1, page_num) and not descendants) or (
                            any(number == page_num for number, _ in descendants)
                            and not page_blocks
                        ):
                            resolver.fail("table_empty")
                        for block in page_blocks:
                            block["table_id"] = table_count
                    contains_table = (
                        is_table
                        or in_table
                        or any(
                            block and "table_id" in block for _, block in descendants
                        )
                    )
                    if on_page and contains_table and "/Alt" in kid:
                        resolver.fail("table_replacement")
                    if "/ActualText" in kid:
                        if on_page and contains_table:
                            resolver.fail("table_replacement")
                        # Validate children/ownership but replace their text exactly once.
                        pages = {number for number, _ in descendants}
                        if elem_page >= 0:
                            pages.add(elem_page)
                        if len(pages) != 1 or elem_page == -2:
                            resolver.fail("replacement_page")
                            return []
                        if not isinstance(kid.ActualText, pikepdf.String):
                            resolver.fail("actual_text")
                            return []
                        return [
                            (
                                pages.pop(),
                                {
                                    "text": str(kid.ActualText).strip(),
                                    "source": "ActualText",
                                },
                            )
                        ]
                    if on_page and "/Alt" in kid:
                        descendants.insert(
                            0,
                            (
                                page_num,
                                {"text": str(kid.Alt).strip(), "source": "Alt"},
                            ),
                        )
                    return descendants

                structure_texts = _StructureBlocks(
                    block
                    for number, block in collect_text(struct_root.K)
                    if number == page_num and block and block["text"]
                )
                structure_texts.incomplete = resolver.failed

        except Exception as e:
            record_incomplete_check("reading_order.collect_text")
            structure_texts.incomplete = True
            logger.warning(f"[ReadingOrderVerifier] Error reading structure tree: {e}")

        return structure_texts

    def _compare_table_reading_orders(self, page_num, page, visual, structure):
        """Compare separated, horizontal tables as atomic visual regions.

        Exact, globally unique cell-text matches associate decoded structure
        content with observed word boxes. This intentionally supports only
        unambiguous text tables in a single-column flow. It does not infer cell
        geometry or validate row/cell order, headers, or table semantics.
        """

        def finding(recommendation, severity="critical"):
            return ReadingOrderIssue(
                page_number=page_num,
                expected_order=[b["text"][:100] for b in visual[:5]],
                actual_order=[b["text"][:100] for b in structure[:5]],
                severity=severity,
                recommendation=recommendation,
            )

        def incomplete(reason):
            record_incomplete_check("reading_order.table_" + reason)
            return finding(
                "Table placement and surrounding reading order could not be fully "
                "verified. Review ambiguous or unsupported table layout and text."
            )

        if getattr(structure, "incomplete", False):
            return self._compare_reading_orders(page_num, visual, structure)
        if any(b.get("source") in {"ActualText", "Alt"} for b in structure):
            return incomplete("replacement")
        text_layout = page.get_text("dict")
        if page.rotation or any(
            line.get("dir", (1, 0)) != (1, 0)
            for block in text_layout.get("blocks", [])
            for line in block.get("lines", [])
        ):
            return incomplete("direction")
        words = page.get_text("words", sort=False)
        if (
            len(words) > 20000
            or len(structure) > 500
            or sum(len(b["text"]) for b in structure) > 200000
        ):
            return incomplete("comparison_limit")
        word_text = [word[4].casefold() for word in words]
        starts = defaultdict(list)
        for index, word in enumerate(word_text):
            starts[word].append(index)
        ownership, table_words = {}, defaultdict(list)
        for block in structure:
            if "table_id" not in block:
                continue
            tokens = block["text"].casefold().split()
            matches = [
                index
                for index in starts[tokens[0]]
                if word_text[index : index + len(tokens)] == tokens
            ]
            if len(matches) != 1:
                return incomplete("text_match")
            indices = range(matches[0], matches[0] + len(tokens))
            for index in indices:
                if index in ownership:
                    return incomplete("text_ownership")
                ownership[index] = block["table_id"]
                table_words[block["table_id"]].append(words[index])
        regions = {}
        for table_id, entries in table_words.items():
            regions[table_id] = (
                min(w[0] for w in entries),
                min(w[1] for w in entries),
                max(w[2] for w in entries),
                max(w[3] for w in entries),
            )
        ordered_regions = sorted(regions.items(), key=lambda item: item[1][1])
        if any(a[1][3] > b[1][1] for a, b in zip(ordered_regions, ordered_regions[1:])):
            return incomplete("overlapping_regions")
        for index, word in enumerate(words):
            if index not in ownership and any(
                word[1] < box[3] and word[3] > box[1] for box in regions.values()
            ):
                return incomplete("overlapping_text")
        # Inspect lines, including pages with only two surrounding blocks;
        # the general column heuristic requires at least four blocks.
        line_starts = defaultdict(list)
        for block in text_layout.get("blocks", []):
            for line in block.get("lines", []):
                box = line["bbox"]
                if not any(
                    box[1] < region[3] and box[3] > region[1]
                    for region in regions.values()
                ):
                    line_starts[int(box[1] / 20)].append(box[0])
        for starts_on_line in line_starts.values():
            ordered_starts = sorted(starts_on_line)
            if any(
                right - left > 100
                for left, right in zip(ordered_starts, ordered_starts[1:])
            ):
                return incomplete("columns")
        # Keep every unmatched word; missing/extra surrounding text must not
        # disappear when a table's cells are collapsed to one comparison token.
        visual_items = [
            (word[1], word[0], ("text", word[4].casefold()))
            for index, word in enumerate(words)
            if index not in ownership
        ]
        visual_items.extend(
            (box[1], box[0], ("table", table_id)) for table_id, box in regions.items()
        )
        visual_tokens = [
            item[2]
            for item in sorted(
                visual_items, key=lambda item: (int(item[0] / 10), item[1])
            )
        ]
        structure_tokens, seen_tables = [], set()
        previous_table = None
        for block in structure:
            table_id = block.get("table_id")
            if table_id is None:
                structure_tokens.extend(
                    ("text", word) for word in block["text"].casefold().split()
                )
            elif table_id != previous_table:
                if table_id in seen_tables:
                    return incomplete("discontiguous_structure")
                seen_tables.add(table_id)
                structure_tokens.append(("table", table_id))
            previous_table = table_id
        if visual_tokens == structure_tokens:
            return None
        if Counter(visual_tokens) != Counter(structure_tokens):
            return finding(
                "Structure text does not correspond to all visible text around the "
                "table. Review missing, repeated, or substituted content."
            )
        return finding(
            "Structure tree reading order differs from the visual placement of "
            "tables and surrounding text. Review and correct the reading order.",
            "warning",
        )

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

        if getattr(structure, "incomplete", False):
            return issue(
                "Structure content could not be fully resolved. Review missing, "
                "ambiguous, or unsupported reading-order references.",
                "critical",
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
