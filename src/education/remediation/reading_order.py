"""
Reading Order Auto-Fix for PDF Remediation.

Implements heuristic-based reading order correction for PDF documents.
Reorders the structure tree so that content reads in the correct visual
order (top-to-bottom, left-to-right for LTR documents, column-aware).

Supports:
- Single-column layout (top-to-bottom ordering)
- Two-column layout (left column top-to-bottom, then right column)
- Header/footer/page-number detection across pages
- Structure tree reordering via pikepdf

WCAG 1.3.2 (Meaningful Sequence): When the sequence in which content is
presented affects its meaning, a correct reading sequence can be
programmatically determined.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF - for reading/analyzing PDFs

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf
    from pikepdf import Array, Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None

from .confidence import ConfidenceCalculator, FixMethod

logger = logging.getLogger(__name__)


class LayoutType(str, Enum):
    """Detected page layout type."""

    SINGLE_COLUMN = "single_column"
    TWO_COLUMN = "two_column"
    MULTI_COLUMN = "multi_column"
    COMPLEX = "complex"


@dataclass
class ContentBlock:
    """A content block extracted from a PDF page.

    Attributes:
        index: Original index in the extraction order.
        bbox: Bounding box as (x0, y0, x1, y1) in PDF coordinates.
        text: Text content of the block (first ~200 chars).
        page_num: 0-indexed page number.
        block_type: Type string from PyMuPDF (e.g. "text", "image").
        is_header: Whether this block was detected as a running header.
        is_footer: Whether this block was detected as a running footer.
        is_page_number: Whether this block appears to be a page number.
    """

    index: int
    bbox: Tuple[float, float, float, float]
    text: str
    page_num: int
    block_type: str = "text"
    is_header: bool = False
    is_footer: bool = False
    is_page_number: bool = False


@dataclass
class ReadingOrderFixResult:
    """Result of a reading order fix operation.

    Attributes:
        success: Whether the fix was applied without errors.
        reordered_count: Number of structure elements reordered.
        artifacts_marked: Number of elements marked as artifacts (headers/footers).
        layout_type: Detected layout type.
        confidence: Confidence score for this fix (0.0-1.0).
        needs_review: Whether a human should review the result.
        original_order: Original element indices.
        new_order: Reordered element indices.
        error: Error message if the fix failed.
    """

    success: bool
    reordered_count: int = 0
    artifacts_marked: int = 0
    layout_type: LayoutType = LayoutType.SINGLE_COLUMN
    confidence: float = 0.0
    needs_review: bool = True
    original_order: List[int] = field(default_factory=list)
    new_order: List[int] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Heuristic Strategy
# ---------------------------------------------------------------------------


class HeuristicStrategy:
    """Heuristic-based reading order correction.

    Uses geometric analysis (bounding boxes, column clustering, margin
    detection) to determine the intended reading order without AI.
    """

    # Fraction of page height from top/bottom considered header/footer zone
    HEADER_ZONE_RATIO = 0.08
    FOOTER_ZONE_RATIO = 0.08

    # Minimum pages required to detect repeating headers/footers
    MIN_PAGES_FOR_HEADER_DETECTION = 3

    # Fraction of pages that must contain the same text for header/footer
    REPEAT_THRESHOLD = 0.6

    # Gap between column clusters as fraction of page width
    COLUMN_GAP_RATIO = 0.20

    def __init__(self) -> None:
        self._confidence_calc = ConfidenceCalculator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self, pdf_path: str) -> ReadingOrderFixResult:
        """Analyse a PDF and reorder its structure tree.

        Args:
            pdf_path: Path to the PDF file (will be modified in place via pikepdf).

        Returns:
            ReadingOrderFixResult with details of what was changed.
        """
        if not HAS_PYMUPDF:
            return ReadingOrderFixResult(
                success=False, error="PyMuPDF (fitz) is required"
            )
        if not HAS_PIKEPDF:
            return ReadingOrderFixResult(
                success=False, error="pikepdf is required for structure tree reordering"
            )

        try:
            # 1. Extract content blocks using PyMuPDF
            doc = fitz.open(pdf_path)
            try:
                blocks = self._extract_blocks(doc)
            finally:
                doc.close()

            if not blocks:
                return ReadingOrderFixResult(
                    success=True,
                    layout_type=LayoutType.SINGLE_COLUMN,
                    confidence=1.0,
                    needs_review=False,
                )

            # 2. Detect headers, footers, page numbers
            self._detect_headers_footers(blocks)

            # 3. Detect layout type
            layout = self._detect_layout(blocks)

            # 4. Compute correct reading order
            new_order = self._compute_reading_order(blocks, layout)

            # 5. Check if reordering is needed
            original_order = list(range(len(blocks)))
            if new_order == original_order:
                # Already in correct order
                signal, context = self._layout_signals(layout)
                confidence = self._confidence_calc.calculate(
                    FixMethod.HEURISTIC,
                    signal_strength=signal,
                    context_quality=context,
                )
                return ReadingOrderFixResult(
                    success=True,
                    reordered_count=0,
                    layout_type=layout,
                    confidence=confidence,
                    needs_review=self._confidence_calc.needs_review(confidence),
                    original_order=original_order,
                    new_order=new_order,
                )

            # 6. Reorder the structure tree via pikepdf
            reorder_map = {
                old_idx: new_pos for new_pos, old_idx in enumerate(new_order)
            }
            reordered, artifacts = self._reorder_structure_tree(
                pdf_path, reorder_map, blocks
            )

            # 7. Compute confidence
            signal, context = self._layout_signals(layout)
            confidence = self._confidence_calc.calculate(
                FixMethod.HEURISTIC,
                signal_strength=signal,
                context_quality=context,
            )

            return ReadingOrderFixResult(
                success=True,
                reordered_count=reordered,
                artifacts_marked=artifacts,
                layout_type=layout,
                confidence=confidence,
                needs_review=self._confidence_calc.needs_review(confidence),
                original_order=original_order,
                new_order=new_order,
            )

        except Exception as exc:
            logger.error("Reading order fix failed: %s", exc, exc_info=True)
            return ReadingOrderFixResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Block extraction
    # ------------------------------------------------------------------

    def _extract_blocks(self, doc) -> List[ContentBlock]:
        """Extract content blocks from every page of a PyMuPDF document.

        Uses ``page.get_text("dict")`` which returns blocks with bounding
        boxes.  Image blocks are included as well.
        """
        blocks: List[ContentBlock] = []
        global_idx = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_dict = page.get_text("dict")

            for blk in page_dict.get("blocks", []):
                bbox = (blk["bbox"][0], blk["bbox"][1], blk["bbox"][2], blk["bbox"][3])
                block_type = "image" if blk.get("type") == 1 else "text"

                # Collect text from spans
                text = ""
                if "lines" in blk:
                    for line in blk["lines"]:
                        for span in line.get("spans", []):
                            text += span.get("text", "")
                        text += " "
                text = text.strip()[:200]

                # Skip empty text blocks
                if block_type == "text" and not text:
                    continue

                blocks.append(
                    ContentBlock(
                        index=global_idx,
                        bbox=bbox,
                        text=text,
                        page_num=page_num,
                        block_type=block_type,
                    )
                )
                global_idx += 1

        return blocks

    # ------------------------------------------------------------------
    # Header / footer / page-number detection
    # ------------------------------------------------------------------

    def _detect_headers_footers(self, blocks: List[ContentBlock]) -> None:
        """Detect running headers, footers, and page numbers in-place.

        A piece of text is considered a running header/footer if:
        - It appears in the header/footer zone on >= REPEAT_THRESHOLD of all
          pages.
        - The document has at least MIN_PAGES_FOR_HEADER_DETECTION pages.

        Page numbers are short numeric strings in the footer zone that appear
        on multiple pages.
        """
        if not blocks:
            return

        total_pages = max(b.page_num for b in blocks) + 1
        if total_pages < self.MIN_PAGES_FOR_HEADER_DETECTION:
            return

        # Determine page dimensions from block bboxes (approximate)
        page_heights: Dict[int, float] = {}
        for b in blocks:
            _, _, _, y1 = b.bbox
            cur = page_heights.get(b.page_num, 0.0)
            if y1 > cur:
                page_heights[b.page_num] = y1

        # Default page height if we can't determine it
        default_page_height = 792.0  # US Letter
        if page_heights:
            default_page_height = max(page_heights.values())

        header_threshold = default_page_height * self.HEADER_ZONE_RATIO
        footer_threshold = default_page_height * (1.0 - self.FOOTER_ZONE_RATIO)

        # --- Header detection ---
        self._detect_repeating_zone(blocks, total_pages, header_threshold, is_top=True)

        # --- Footer detection ---
        self._detect_repeating_zone(blocks, total_pages, footer_threshold, is_top=False)

        # --- Page number detection ---
        self._detect_page_numbers(
            blocks, total_pages, header_threshold, footer_threshold
        )

    def _detect_repeating_zone(
        self,
        blocks: List[ContentBlock],
        total_pages: int,
        threshold: float,
        is_top: bool,
    ) -> None:
        """Mark blocks in header/footer zones that repeat across pages."""
        # Group text in the zone by page
        zone_texts_by_page: Dict[int, List[Tuple[int, str]]] = {}
        for idx, b in enumerate(blocks):
            if b.block_type != "text" or not b.text:
                continue
            _, y0, _, y1 = b.bbox
            if is_top and y0 < threshold:
                zone_texts_by_page.setdefault(b.page_num, []).append(
                    (idx, b.text.strip().lower())
                )
            elif not is_top and y1 > threshold:
                zone_texts_by_page.setdefault(b.page_num, []).append(
                    (idx, b.text.strip().lower())
                )

        if len(zone_texts_by_page) < self.MIN_PAGES_FOR_HEADER_DETECTION:
            return

        # Count occurrences of each text across pages
        text_page_count: Dict[str, int] = {}
        text_block_indices: Dict[str, List[int]] = {}
        for page_num, items in zone_texts_by_page.items():
            seen_this_page: set = set()
            for idx, txt in items:
                if txt not in seen_this_page:
                    text_page_count[txt] = text_page_count.get(txt, 0) + 1
                    seen_this_page.add(txt)
                text_block_indices.setdefault(txt, []).append(idx)

        # Mark texts that appear on enough pages
        min_count = int(total_pages * self.REPEAT_THRESHOLD)
        for txt, count in text_page_count.items():
            if count >= min_count:
                for idx in text_block_indices[txt]:
                    if is_top:
                        blocks[idx].is_header = True
                    else:
                        blocks[idx].is_footer = True

    def _detect_page_numbers(
        self,
        blocks: List[ContentBlock],
        total_pages: int,
        header_threshold: float,
        footer_threshold: float,
    ) -> None:
        """Mark blocks that look like page numbers.

        Only marks blocks as page numbers if the numeric values across pages
        form an ascending sequential pattern (e.g. 1, 2, 3 or 3, 4, 5).
        """
        page_num_pattern = re.compile(r"^\d{1,4}$")

        # Collect candidates: numeric text in margin zones, grouped by page
        candidates_by_page: Dict[int, List[ContentBlock]] = {}
        for b in blocks:
            if b.block_type != "text":
                continue
            text = b.text.strip()
            if not page_num_pattern.match(text):
                continue
            _, y0, _, y1 = b.bbox
            if y0 < header_threshold or y1 > footer_threshold:
                candidates_by_page.setdefault(b.page_num, []).append(b)

        # Need candidates on at least MIN_PAGES_FOR_HEADER_DETECTION pages
        if len(candidates_by_page) < self.MIN_PAGES_FOR_HEADER_DETECTION:
            return

        # Check each candidate per page for sequential pattern
        # Pick the best candidate per page (prefer one per page)
        page_nums_sorted = sorted(candidates_by_page.keys())
        best_per_page: Dict[int, ContentBlock] = {}
        for pg in page_nums_sorted:
            cands = candidates_by_page[pg]
            if len(cands) == 1:
                best_per_page[pg] = cands[0]
            else:
                # Pick the one whose numeric value most closely matches page+1
                best = min(cands, key=lambda c: abs(int(c.text.strip()) - (pg + 1)))
                best_per_page[pg] = best

        # Verify ascending sequential pattern
        values = [
            (pg, int(best_per_page[pg].text.strip()))
            for pg in sorted(best_per_page.keys())
        ]
        if len(values) < self.MIN_PAGES_FOR_HEADER_DETECTION:
            return

        is_sequential = all(
            values[i + 1][1] - values[i][1] == values[i + 1][0] - values[i][0]
            for i in range(len(values) - 1)
        )

        if is_sequential:
            for pg, block in best_per_page.items():
                block.is_page_number = True

    # ------------------------------------------------------------------
    # Layout detection
    # ------------------------------------------------------------------

    def _detect_layout(self, blocks: List[ContentBlock]) -> LayoutType:
        """Detect whether the document uses single-column, two-column, etc.

        Uses X-position clustering: if there are two distinct groups of
        left-edge X positions separated by > COLUMN_GAP_RATIO of the page
        width, we consider it two-column.
        """
        if len(blocks) <= 1:
            return LayoutType.SINGLE_COLUMN

        # Filter to content blocks only (no headers/footers)
        content_blocks = [
            b
            for b in blocks
            if not b.is_header and not b.is_footer and not b.is_page_number
        ]
        if len(content_blocks) <= 1:
            return LayoutType.SINGLE_COLUMN

        # Estimate page width from bboxes
        all_x1 = [b.bbox[2] for b in content_blocks]
        page_width = max(all_x1) if all_x1 else 612.0
        gap_threshold = page_width * self.COLUMN_GAP_RATIO

        # Cluster left-edge X positions
        left_xs = sorted(set(round(b.bbox[0], 0) for b in content_blocks))

        if len(left_xs) < 2:
            return LayoutType.SINGLE_COLUMN

        # Find largest gap between consecutive X positions
        max_gap = 0.0
        split_point = 0.0
        for i in range(1, len(left_xs)):
            gap = left_xs[i] - left_xs[i - 1]
            if gap > max_gap:
                max_gap = gap
                split_point = (left_xs[i - 1] + left_xs[i]) / 2.0

        if max_gap < gap_threshold:
            return LayoutType.SINGLE_COLUMN

        # Count blocks in each cluster to confirm two columns
        left_count = sum(1 for b in content_blocks if b.bbox[0] < split_point)
        right_count = sum(1 for b in content_blocks if b.bbox[0] >= split_point)

        # Both columns must have at least 2 blocks
        if left_count >= 2 and right_count >= 2:
            # Check for a third column
            right_blocks = [b for b in content_blocks if b.bbox[0] >= split_point]
            right_xs = sorted(set(round(b.bbox[0], 0) for b in right_blocks))
            if len(right_xs) >= 2:
                max_right_gap = 0.0
                for i in range(1, len(right_xs)):
                    g = right_xs[i] - right_xs[i - 1]
                    if g > max_right_gap:
                        max_right_gap = g
                if max_right_gap > gap_threshold:
                    return LayoutType.MULTI_COLUMN

            return LayoutType.TWO_COLUMN

        return LayoutType.SINGLE_COLUMN

    # ------------------------------------------------------------------
    # Reading order computation
    # ------------------------------------------------------------------

    def _compute_reading_order(
        self, blocks: List[ContentBlock], layout: LayoutType
    ) -> List[int]:
        """Compute the correct reading order for all blocks.

        Returns a list of block indices in the correct reading order.
        The approach:
        1. Headers first (top of each page, page order).
        2. Content blocks ordered by layout rules.
        3. Footers last (bottom of each page, page order).
        """
        headers = [
            b
            for b in blocks
            if b.is_header or (b.is_page_number and self._is_top(b, blocks))
        ]
        footers = [
            b
            for b in blocks
            if b.is_footer or (b.is_page_number and not self._is_top(b, blocks))
        ]
        content = [
            b
            for b in blocks
            if not b.is_header and not b.is_footer and not b.is_page_number
        ]

        # Sort headers by page then Y position
        headers.sort(key=lambda b: (b.page_num, b.bbox[1]))

        # Sort content based on layout
        if layout == LayoutType.TWO_COLUMN:
            content = self._order_two_column(content)
        elif layout == LayoutType.MULTI_COLUMN:
            # Fall back to two-column for now
            content = self._order_two_column(content)
        else:
            # Single column: sort by page, then Y position (top to bottom)
            content.sort(key=lambda b: (b.page_num, b.bbox[1]))

        # Sort footers by page then Y position
        footers.sort(key=lambda b: (b.page_num, b.bbox[1]))

        ordered = headers + content + footers
        return [b.index for b in ordered]

    def _is_top(self, block: ContentBlock, all_blocks: List[ContentBlock]) -> bool:
        """Check whether a block is in the top half of its page."""
        page_blocks = [b for b in all_blocks if b.page_num == block.page_num]
        if not page_blocks:
            return True
        max_y = max(b.bbox[3] for b in page_blocks)
        return block.bbox[1] < max_y / 2.0

    def _order_two_column(self, blocks: List[ContentBlock]) -> List[ContentBlock]:
        """Order blocks for a two-column layout.

        Finds the column split point, then orders: left column top-to-bottom,
        then right column top-to-bottom, per page.
        """
        if not blocks:
            return blocks

        # Find split point (midpoint of the largest X gap)
        left_xs = sorted(set(round(b.bbox[0], 0) for b in blocks))
        max_gap = 0.0
        split = 0.0
        for i in range(1, len(left_xs)):
            gap = left_xs[i] - left_xs[i - 1]
            if gap > max_gap:
                max_gap = gap
                split = (left_xs[i - 1] + left_xs[i]) / 2.0

        # If no clear split, just sort top-to-bottom
        if max_gap < 20:
            blocks.sort(key=lambda b: (b.page_num, b.bbox[1]))
            return blocks

        # Group by page
        pages: Dict[int, List[ContentBlock]] = {}
        for b in blocks:
            pages.setdefault(b.page_num, []).append(b)

        ordered: List[ContentBlock] = []
        for page_num in sorted(pages.keys()):
            page_blocks = pages[page_num]
            left = sorted(
                [b for b in page_blocks if b.bbox[0] < split],
                key=lambda b: b.bbox[1],
            )
            right = sorted(
                [b for b in page_blocks if b.bbox[0] >= split],
                key=lambda b: b.bbox[1],
            )
            ordered.extend(left)
            ordered.extend(right)

        return ordered

    # ------------------------------------------------------------------
    # Structure tree reordering
    # ------------------------------------------------------------------

    def _reorder_structure_tree(
        self,
        pdf_path: str,
        reorder_map: Dict[int, int],
        blocks: List[ContentBlock],
    ) -> Tuple[int, int]:
        """Reorder the /K array in StructTreeRoot according to *reorder_map*.

        Also marks detected headers/footers as Artifact structure elements.

        Args:
            pdf_path: Path to the PDF file.
            reorder_map: Mapping from original index to new position.
            blocks: Content blocks (used to identify artifacts).

        Returns:
            (reordered_count, artifacts_marked)
        """
        reordered = 0
        artifacts = 0

        with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
            if Name.StructTreeRoot not in pdf.Root:
                logger.warning("No StructTreeRoot found, cannot reorder")
                return 0, 0

            struct_root = pdf.Root[Name.StructTreeRoot]
            if Name.K not in struct_root:
                logger.warning("StructTreeRoot has no /K array")
                return 0, 0

            kids = struct_root[Name.K]
            if not isinstance(kids, Array):
                # Single element, wrap in array
                kids = Array([kids])
                struct_root[Name.K] = kids

            num_kids = len(kids)
            if num_kids == 0:
                return 0, 0

            # Build the new order for the kids array.
            # The reorder_map maps block index -> new position.
            # We can only reorder up to min(num_kids, len(reorder_map)) elements.
            max_reorder = min(num_kids, len(reorder_map))

            # Create sorted list of (new_position, original_index) pairs
            sortable = []
            for orig_idx, new_pos in reorder_map.items():
                if orig_idx < max_reorder:
                    sortable.append((new_pos, orig_idx))
            sortable.sort()

            # Build new kids array in correct order
            new_kids = Array([])
            reordered_indices = set()
            for new_pos, orig_idx in sortable:
                if orig_idx < num_kids:
                    new_kids.append(kids[orig_idx])
                    reordered_indices.add(orig_idx)
                    reordered += 1

            # Append any kids not in the reorder map (preserve them at the end)
            for i in range(num_kids):
                if i not in reordered_indices:
                    new_kids.append(kids[i])

            struct_root[Name.K] = new_kids

            # Mark header/footer blocks as Artifact using original kids
            # (block indices refer to original positions, not reordered)
            artifact_indices = set()
            for b in blocks:
                if (
                    b.is_header or b.is_footer or b.is_page_number
                ) and b.index < num_kids:
                    artifact_indices.add(b.index)

            for idx in artifact_indices:
                try:
                    elem = kids[idx]
                    if hasattr(elem, "keys"):
                        elem[Name.S] = Name("/Artifact")
                        artifacts += 1
                except Exception:
                    pass

            pdf.save(pdf_path)

        return reordered, artifacts

    # ------------------------------------------------------------------
    # Confidence helpers
    # ------------------------------------------------------------------

    def _layout_signals(self, layout: LayoutType) -> Tuple[float, float]:
        """Return (signal_strength, context_quality) for a layout type."""
        if layout == LayoutType.SINGLE_COLUMN:
            return 0.9, 0.9
        elif layout == LayoutType.TWO_COLUMN:
            return 0.6, 0.7
        elif layout == LayoutType.MULTI_COLUMN:
            return 0.3, 0.7
        else:  # COMPLEX
            return 0.3, 0.7


# ---------------------------------------------------------------------------
# Vision Strategy
# ---------------------------------------------------------------------------


class VisionStrategy:
    """AI vision-based reading order correction.

    Uses a vision-capable LLM (Gemini) to analyze page images and determine
    the intended reading order for complex multi-column or mixed layouts.
    """

    # DPI for page rendering
    RENDER_DPI = 300
    # IoU threshold for matching AI blocks to structure elements
    IOU_THRESHOLD = 0.5

    def __init__(self) -> None:
        self._confidence_calc = ConfidenceCalculator()

    def fix(self, pdf_path: str, page_num: int = 0) -> ReadingOrderFixResult:
        """Fix reading order for a single page using AI vision.

        Args:
            pdf_path: Path to the PDF file.
            page_num: 0-indexed page to analyze.

        Returns:
            ReadingOrderFixResult with details of changes made.
        """
        if not HAS_PYMUPDF:
            return ReadingOrderFixResult(success=False, error="PyMuPDF required")
        if not HAS_PIKEPDF:
            return ReadingOrderFixResult(success=False, error="pikepdf required")

        try:
            from src.ai.providers import get_provider_manager

            ai_client = get_provider_manager()
        except Exception as exc:
            return ReadingOrderFixResult(
                success=False, error=f"AI provider unavailable: {exc}"
            )

        try:
            # 1. Render page to image
            doc = fitz.open(pdf_path)
            try:
                if page_num >= len(doc):
                    return ReadingOrderFixResult(
                        success=False, error=f"Page {page_num} out of range"
                    )
                page = doc[page_num]
                pixmap = page.get_pixmap(dpi=self.RENDER_DPI)
                image_bytes = pixmap.tobytes("png")

                # Also extract blocks for mapping
                heuristic = HeuristicStrategy()
                blocks = heuristic._extract_blocks(doc)
                page_blocks = [b for b in blocks if b.page_num == page_num]
            finally:
                doc.close()

            if not page_blocks:
                return ReadingOrderFixResult(
                    success=True, confidence=1.0, needs_review=False
                )

            # 2. Send to AI vision with structured prompt
            prompt = self._build_vision_prompt(page_blocks)
            result = ai_client.analyze_image_sync(
                image_data=image_bytes,
                prompt=prompt,
                max_tokens=1000,
            )

            if not result.get("success") or not result.get("content"):
                return ReadingOrderFixResult(
                    success=False,
                    error=f"AI analysis failed: {result.get('error', 'no content')}",
                )

            # 3. Parse AI response
            ai_order = self._parse_ai_response(result["content"], len(page_blocks))
            if ai_order is None:
                return ReadingOrderFixResult(
                    success=False, error="Could not parse AI response"
                )

            # 4. Map AI block numbers to structure tree elements via IoU
            block_mapping = self._map_ai_to_blocks(ai_order, page_blocks)
            match_ratio = len(block_mapping) / max(len(page_blocks), 1)

            # 5. Reorder structure tree
            if block_mapping:
                reorder_map = {
                    old_idx: new_pos
                    for new_pos, old_idx in enumerate(
                        b.index
                        for b in [
                            page_blocks[m]
                            for m in block_mapping
                            if m < len(page_blocks)
                        ]
                    )
                }
                heuristic_strategy = HeuristicStrategy()
                reordered, artifacts = heuristic_strategy._reorder_structure_tree(
                    pdf_path, reorder_map, page_blocks
                )
            else:
                reordered, artifacts = 0, 0

            # 6. Calculate confidence based on mapping quality
            context_quality = min(1.0, match_ratio)
            confidence = self._confidence_calc.calculate(
                FixMethod.AI_VISION,
                signal_strength=match_ratio,
                context_quality=context_quality,
            )

            return ReadingOrderFixResult(
                success=True,
                reordered_count=reordered,
                artifacts_marked=artifacts,
                layout_type=LayoutType.COMPLEX,
                confidence=confidence,
                needs_review=self._confidence_calc.needs_review(confidence),
                original_order=[b.index for b in page_blocks],
                new_order=list(block_mapping) if block_mapping else [],
            )

        except Exception as exc:
            logger.error("Vision reading order fix failed: %s", exc, exc_info=True)
            return ReadingOrderFixResult(success=False, error=str(exc))

    def _build_vision_prompt(self, blocks: List[ContentBlock]) -> str:
        """Build the vision analysis prompt."""
        return (
            "Analyze this PDF page image and determine the correct reading order.\n"
            "Return a JSON object with:\n"
            '{"order": [1, 2, 3, ...], "headers": [<indices of header/footer blocks>]}\n'
            f"There are {len(blocks)} content blocks on this page.\n"
            "Number them 1 through N in the order they should be read "
            "(top-to-bottom, left-to-right, following column flow).\n"
            "Identify any running headers, footers, or page numbers.\n"
            "Return ONLY the JSON object, no other text."
        )

    def _parse_ai_response(self, content: str, num_blocks: int) -> Optional[List[int]]:
        """Parse the AI response to extract reading order."""
        import json

        # Try to extract JSON from the response
        content = content.strip()
        # Handle markdown code blocks
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
            order = data.get("order", [])
            if isinstance(order, list) and all(isinstance(x, int) for x in order):
                # Convert 1-indexed to 0-indexed
                return [x - 1 for x in order if 1 <= x <= num_blocks]
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _map_ai_to_blocks(
        self, ai_order: List[int], blocks: List[ContentBlock]
    ) -> List[int]:
        """Map AI-ordered indices to actual block indices.

        Returns list of block indices in the AI-determined order.
        """
        result = []
        for ai_idx in ai_order:
            if 0 <= ai_idx < len(blocks):
                result.append(ai_idx)
        return result

    @staticmethod
    def _compute_iou(
        bbox1: Tuple[float, float, float, float],
        bbox2: Tuple[float, float, float, float],
    ) -> float:
        """Compute Intersection over Union for two bounding boxes."""
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def get_reading_order_strategy(blocks: List[ContentBlock]) -> str:
    """Select the best reading order strategy for the given blocks.

    Returns "heuristic" for simple layouts (1-2 columns, no floating elements),
    "vision" for complex layouts.

    Args:
        blocks: Extracted content blocks (may be empty).

    Returns:
        Strategy name: ``"heuristic"`` or ``"vision"``.
    """
    if not blocks:
        return "heuristic"

    strategy = HeuristicStrategy()
    # Get blocks from first page for layout detection
    first_page_blocks = [b for b in blocks if b.page_num == 0]
    content_blocks = [
        b
        for b in first_page_blocks
        if not b.is_header and not b.is_footer and not b.is_page_number
    ]

    layout = strategy._detect_layout(content_blocks)

    # Check for floating elements (blocks spanning > 60% of page width in multi-column)
    has_floating = False
    if layout == LayoutType.TWO_COLUMN and len(content_blocks) >= 2:
        page_width = max((b.bbox[2] for b in content_blocks), default=612)
        for b in content_blocks:
            block_width = b.bbox[2] - b.bbox[0]
            if block_width > page_width * 0.6:
                has_floating = True
                break

    if layout in (LayoutType.SINGLE_COLUMN, LayoutType.TWO_COLUMN) and not has_floating:
        return "heuristic"
    return "vision"
