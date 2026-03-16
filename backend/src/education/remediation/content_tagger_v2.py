"""
ContentTagger v2 — Position-Based BDC/EMC Content Stream Marking.

This module replaces the fragile NFC text matching of v1 with a position-based
approach using PyMuPDF bounding boxes to match content stream blocks to
structure elements.

Key differences from v1 (content_tagger.py):
- Uses PyMuPDF (fitz) bounding boxes for spatial matching (IoU >= 0.7)
- Falls back to NFKD + ligature-expanded text matching (confidence 0.75)
- Processes ops by injecting BDC/EMC in reverse order to preserve indices
- Skips TABLE_TAGS (Table, TR, TH, TD) — owned by TableTagger
- Stores bounding boxes in /A attribute with /BBox per spec Section 3.1

Usage:
    import fitz
    import pikepdf

    fitz_doc = fitz.open('input.pdf')
    with pikepdf.open('input.pdf') as pdf:
        tagger = ContentTaggerV2(pdf, fitz_doc)
        stats = tagger.tag_all_pages()
        pdf.save('output.pdf')
    fitz_doc.close()
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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

try:
    import fitz  # PyMuPDF

    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    fitz = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Structure element types managed by TableTagger — skip them here
TABLE_TAGS = {"Table", "TR", "TH", "TD", "THead", "TBody", "TFoot"}

# Ligature expansion table for text normalization fallback
_LIGATURES: Dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
    "\u00e6": "ae",
    "\u00c6": "AE",
    "\u0153": "oe",
    "\u0152": "OE",
}


@dataclass
class MatchedBlock:
    """Result of matching a structure element to a content stream block.

    Attributes:
        mcid: Marked content identifier assigned to this block.
        struct_elem: The pikepdf structure element dictionary.
        block_start: Index of the first operator in the content stream block.
        block_end: Index one past the last operator in the block.
        match_type: How the match was made — "position" (IoU) or "text" (fallback).
        confidence: Confidence score (0.90 for position, 0.75 for text).
    """

    mcid: int
    struct_elem: Any  # pikepdf Dictionary
    block_start: int
    block_end: int
    match_type: str = "position"
    confidence: float = 0.90


def _expand_ligatures(text: str) -> str:
    """Expand typographic ligatures to their component characters."""
    for lig, expansion in _LIGATURES.items():
        text = text.replace(lig, expansion)
    return text


def _normalize_nfkd(text: str) -> str:
    """Normalize text with NFKD + ligature expansion for fuzzy matching."""
    text = _expand_ligatures(text)
    text = unicodedata.normalize("NFKD", text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_text_from_ops(operators: list) -> Optional[str]:
    """Extract readable text from content stream operators (Tj, TJ, ', ")."""
    parts: List[str] = []
    for op in operators:
        op_name = str(op.operator)
        if op_name == "Tj" and op.operands:
            try:
                parts.append(str(op.operands[0]))
            except Exception:
                pass
        elif op_name == "TJ" and op.operands:
            try:
                arr = op.operands[0]
                for item in arr:
                    if isinstance(item, String):
                        parts.append(str(item))
            except Exception:
                pass
        elif op_name in ("'", '"') and op.operands:
            try:
                parts.append(str(op.operands[-1]))
            except Exception:
                pass
    return "".join(parts) if parts else None


class ContentTaggerV2:
    """Tags PDF content streams using position-based BDC/EMC matching.

    Uses PyMuPDF bounding boxes to spatially match content blocks to structure
    elements (IoU >= 0.7), falling back to NFKD-normalized text matching when
    position data is unavailable.

    Args:
        pdf: An open pikepdf.Pdf object with a StructTreeRoot.
        fitz_doc: The same document opened by PyMuPDF (fitz.Document).
    """

    def __init__(self, pdf: Any, fitz_doc: Any) -> None:
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for content tagging. "
                "Install with: pip install pikepdf"
            )
        if not HAS_FITZ:
            raise ImportError(
                "PyMuPDF (fitz) is required for position-based tagging. "
                "Install with: pip install pymupdf"
            )
        self.pdf = pdf
        self.fitz_doc = fitz_doc
        self._next_mcid: int = 0
        # Maps page_index -> list of (mcid, struct_elem)
        self._parent_tree_entries: Dict[int, List[Tuple[int, Any]]] = {}
        self._ensure_struct_tree()

    def _ensure_struct_tree(self) -> None:
        """Ensure StructTreeRoot and MarkInfo are present."""
        if Name.StructTreeRoot not in self.pdf.Root:
            from src.education.remediation.pdf_structure import PDFStructureTree

            PDFStructureTree(self.pdf)
        if Name.MarkInfo not in self.pdf.Root:
            self.pdf.Root[Name.MarkInfo] = Dictionary({})
        self.pdf.Root[Name.MarkInfo][Name.Marked] = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tag_all_pages(self) -> Dict[str, int]:
        """Tag all pages with BDC/EMC markers using position-based matching.

        Returns:
            Stats dict with keys: pages_processed, blocks_matched, blocks_created.
        """
        struct_root = self.pdf.Root[Name.StructTreeRoot]

        # Collect structure elements by page index
        by_page: Dict[int, List[Any]] = {}
        kids = struct_root.get(Name.K, Array([]))
        if not isinstance(kids, Array):
            kids = Array([kids])
        for kid in kids:
            if hasattr(kid, "keys"):
                self._collect_elements(kid, by_page)

        stats = {"pages_processed": 0, "blocks_matched": 0, "blocks_created": 0}

        for page_idx in range(len(self.pdf.pages)):
            page = self.pdf.pages[page_idx]
            page_elems = by_page.get(page_idx, [])

            page_stats = self._tag_page(page, page_idx, page_elems)
            stats["pages_processed"] += 1
            stats["blocks_matched"] += page_stats.get("matched", 0)
            stats["blocks_created"] += page_stats.get("created", 0)

        self._build_parent_tree(struct_root)
        self._ensure_document_root(struct_root)
        self._set_pdfua_identifier()

        return stats

    # ------------------------------------------------------------------
    # Element collection
    # ------------------------------------------------------------------

    def _collect_elements(self, element: Any, by_page: Dict[int, List[Any]]) -> None:
        """Recursively collect StructElem elements grouped by page index."""
        if not hasattr(element, "keys"):
            return

        elem_type_raw = ""
        if Name.S in element:
            elem_type_raw = str(element[Name.S]).lstrip("/")

        # Skip TABLE_TAGS — TableTagger owns them
        if elem_type_raw in TABLE_TAGS:
            return

        # If this element has a /Pg reference, assign it to the correct page
        if Name("/Pg") in element or Name.Pg in element:
            pg_ref = element.get(Name("/Pg")) or element.get(Name.Pg)
            page_idx = self._page_index(pg_ref)
            if page_idx is not None and Name.S in element:
                by_page.setdefault(page_idx, []).append(element)

        # Recurse into /K children
        kids = element.get(Name.K)
        if kids is None:
            return
        if not isinstance(kids, Array):
            kids = Array([kids])
        for kid in kids:
            if hasattr(kid, "keys"):
                self._collect_elements(kid, by_page)

    def _page_index(self, page_ref: Any) -> Optional[int]:
        """Resolve a page object reference to a 0-based page index."""
        if page_ref is None:
            return None
        try:
            for idx, page in enumerate(self.pdf.pages):
                try:
                    if page.obj.objgen == page_ref.objgen:
                        return idx
                except Exception:
                    pass
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Per-page tagging
    # ------------------------------------------------------------------

    def _tag_page(
        self,
        page: Any,
        page_idx: int,
        elements: List[Any],
    ) -> Dict[str, int]:
        """Tag a single page's content stream with BDC/EMC markers.

        Processes ops in reverse order when injecting markers so that earlier
        indices remain valid.
        """
        page_stats = {"matched": 0, "created": 0}

        try:
            ops = list(pikepdf.parse_content_stream(page))
        except Exception as exc:
            logger.warning(f"Page {page_idx}: failed to parse content stream: {exc}")
            return page_stats

        if not ops:
            return page_stats

        content_blocks = self._find_content_blocks(ops)
        if not content_blocks:
            return page_stats

        fitz_blocks = self._get_fitz_blocks(page_idx)
        used_elem_indices: set = set()
        matches: List[MatchedBlock] = []

        # Reset MCID counter per page so ParentTree arrays are compact
        self._next_mcid = 0

        for cb_start, cb_end in content_blocks:
            block_ops = ops[cb_start:cb_end]
            mcid = self._next_mcid

            result = self._match_element_to_block(
                elements, block_ops, fitz_blocks, used_elem_indices
            )

            if result is not None:
                elem, match_type, confidence = result
                matches.append(
                    MatchedBlock(
                        mcid=mcid,
                        struct_elem=elem,
                        block_start=cb_start,
                        block_end=cb_end,
                        match_type=match_type,
                        confidence=confidence,
                    )
                )
                page_stats["matched"] += 1
            else:
                # Create a new P element for unmatched text blocks
                block_text = _extract_text_from_ops(block_ops)
                elem = self._create_p_element(page.obj, block_text)
                matches.append(
                    MatchedBlock(
                        mcid=mcid,
                        struct_elem=elem,
                        block_start=cb_start,
                        block_end=cb_end,
                        match_type="created",
                        confidence=0.0,
                    )
                )
                page_stats["created"] += 1

            self._next_mcid += 1

        if not matches:
            return page_stats

        # Inject markers in reverse order to preserve indices
        new_ops = list(ops)
        page_entries: List[Tuple[int, Any]] = []

        for match in reversed(matches):
            new_ops = self._inject_markers(new_ops, match)

        # Record parent tree entries (forward order)
        for match in matches:
            self._set_mcid_on_element(match.struct_elem, match.mcid, page.obj)
            page_entries.append((match.mcid, match.struct_elem))

        # Write new content stream
        try:
            new_bytes = pikepdf.unparse_content_stream(new_ops)
            page.obj[Name.Contents] = self.pdf.make_stream(new_bytes)
        except Exception as exc:
            logger.error(f"Page {page_idx}: failed to write tagged content stream: {exc}")
            return page_stats

        page.obj[Name.StructParents] = page_idx
        self._parent_tree_entries[page_idx] = page_entries

        return page_stats

    # ------------------------------------------------------------------
    # PyMuPDF block extraction
    # ------------------------------------------------------------------

    def _get_fitz_blocks(self, page_idx: int) -> List[Tuple[float, float, float, float, str]]:
        """Get text blocks with bounding boxes from PyMuPDF.

        Returns list of (x0, y0, x1, y1, text) tuples in PDF coordinate space.
        PyMuPDF uses top-left origin; we convert to PDF bottom-left origin.
        """
        blocks: List[Tuple[float, float, float, float, str]] = []
        try:
            fitz_page = self.fitz_doc[page_idx]
            page_height = fitz_page.rect.height

            for block in fitz_page.get_text("blocks"):
                # block: (x0, y0, x1, y1, text, block_no, block_type)
                if len(block) < 5:
                    continue
                x0, y0, x1, y1 = block[0], block[1], block[2], block[3]
                text = block[4] if len(block) > 4 else ""

                # Convert fitz (top-left origin) to PDF (bottom-left origin)
                pdf_y0 = page_height - y1
                pdf_y1 = page_height - y0
                blocks.append((x0, pdf_y0, x1, pdf_y1, str(text)))
        except Exception as exc:
            logger.warning(f"Page {page_idx}: fitz block extraction failed: {exc}")
        return blocks

    # ------------------------------------------------------------------
    # Content stream block finding
    # ------------------------------------------------------------------

    def _find_content_blocks(
        self, ops: List[Any]
    ) -> List[Tuple[int, int]]:
        """Find BT/ET boundaries in the operator list.

        Returns list of (start_index, end_index) pairs where end_index is
        exclusive (one past the ET operator).
        """
        blocks: List[Tuple[int, int]] = []
        i = 0
        while i < len(ops):
            op_name = str(ops[i].operator)
            if op_name == "BT":
                start = i
                j = i + 1
                while j < len(ops) and str(ops[j].operator) != "ET":
                    j += 1
                end = j + 1 if j < len(ops) else j
                blocks.append((start, end))
                i = end
            else:
                i += 1
        return blocks

    # ------------------------------------------------------------------
    # Element matching
    # ------------------------------------------------------------------

    def _match_element_to_block(
        self,
        elements: List[Any],
        block_ops: List[Any],
        fitz_blocks: List[Tuple[float, float, float, float, str]],
        used_indices: set,
    ) -> Optional[Tuple[Any, str, float]]:
        """Match a content block to a structure element.

        Strategy:
        1. Position match: get element /A /BBox, compute IoU with fitz blocks.
           Accept if IoU >= 0.7 (confidence = 0.90).
        2. Text fallback: NFKD + ligature normalization (confidence = 0.75).

        Args:
            elements: Candidate structure elements for this page.
            block_ops: Operators for this content block.
            fitz_blocks: PyMuPDF text blocks (x0, y0, x1, y1, text) in PDF coords.
            used_indices: Set of already-matched element indices (by list position).

        Returns:
            (struct_elem, match_type, confidence) or None if no match found.
        """
        for elem_idx, elem in enumerate(elements):
            if elem_idx in used_indices:
                continue

            elem_type_raw = ""
            if Name.S in elem:
                elem_type_raw = str(elem[Name.S]).lstrip("/")

            # TABLE_TAGS are owned by TableTagger
            if elem_type_raw in TABLE_TAGS:
                continue

            # --- Position match ---
            elem_bbox = self._get_element_bbox(elem)
            if elem_bbox is not None and fitz_blocks:
                best_iou = 0.0
                for fb in fitz_blocks:
                    fb_bbox = (fb[0], fb[1], fb[2], fb[3])
                    iou = self._compute_iou(elem_bbox, fb_bbox)
                    if iou > best_iou:
                        best_iou = iou

                if best_iou >= 0.7:
                    used_indices.add(elem_idx)
                    return (elem, "position", 0.90)

            # --- Text fallback ---
            block_text = _extract_text_from_ops(block_ops)
            if block_text:
                elem_text = self._get_element_text(elem)
                if elem_text and self._text_matches(block_text, elem_text):
                    used_indices.add(elem_idx)
                    return (elem, "text", 0.75)

        return None

    def _get_element_bbox(self, elem: Any) -> Optional[Tuple[float, float, float, float]]:
        """Extract bounding box from a structure element's /A attribute."""
        try:
            attr = elem.get(Name.A)
            if attr is None:
                return None

            # /A can be a dict or an array of dicts
            if isinstance(attr, Array):
                for a in attr:
                    if hasattr(a, "keys"):
                        bbox = a.get(Name("/BBox"))
                        if bbox is not None:
                            return self._parse_bbox(bbox)
                return None
            elif hasattr(attr, "keys"):
                bbox = attr.get(Name("/BBox"))
                if bbox is not None:
                    return self._parse_bbox(bbox)
        except Exception:
            pass
        return None

    def _parse_bbox(self, bbox: Any) -> Optional[Tuple[float, float, float, float]]:
        """Parse a pikepdf /BBox array into a float tuple."""
        try:
            if isinstance(bbox, Array) and len(bbox) >= 4:
                return (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )
        except Exception:
            pass
        return None

    def _get_element_text(self, elem: Any) -> Optional[str]:
        """Get the text associated with a structure element."""
        for key in (Name.ActualText, Name.Alt, Name("/ActualText"), Name("/Alt")):
            try:
                val = elem.get(key)
                if val is not None:
                    return str(val)
            except Exception:
                pass
        return None

    def _text_matches(self, block_text: str, elem_text: str) -> bool:
        """Check if block text matches element text using NFKD normalization."""
        if not block_text or not elem_text:
            return False
        nb = _normalize_nfkd(block_text)
        ne = _normalize_nfkd(elem_text)
        if not nb or not ne:
            return False
        return nb == ne or nb in ne or ne in nb

    # ------------------------------------------------------------------
    # IoU computation
    # ------------------------------------------------------------------

    def _compute_iou(
        self,
        bbox1: Tuple[float, float, float, float],
        bbox2: Tuple[float, float, float, float],
    ) -> float:
        """Compute Intersection over Union for two axis-aligned bounding boxes.

        Each bbox is (x0, y0, x1, y1) in PDF coordinates.
        """
        x0 = max(bbox1[0], bbox2[0])
        y0 = max(bbox1[1], bbox2[1])
        x1 = min(bbox1[2], bbox2[2])
        y1 = min(bbox1[3], bbox2[3])

        if x1 <= x0 or y1 <= y0:
            return 0.0

        intersection = (x1 - x0) * (y1 - y0)
        area1 = max(0.0, (bbox1[2] - bbox1[0])) * max(0.0, (bbox1[3] - bbox1[1]))
        area2 = max(0.0, (bbox2[2] - bbox2[0])) * max(0.0, (bbox2[3] - bbox2[1]))
        union = area1 + area2 - intersection

        if union <= 0.0:
            return 0.0
        return intersection / union

    # ------------------------------------------------------------------
    # Marker injection
    # ------------------------------------------------------------------

    def _inject_markers(self, ops: List[Any], match: "MatchedBlock") -> List[Any]:
        """Insert BDC before block_start and EMC after block_end - 1.

        Inserts in-place into a copy of ops (so caller must use the return value).
        Processes end first, then start, since we're building a reversed list.
        """
        new_ops = list(ops)
        elem_type = "P"
        if Name.S in match.struct_elem:
            elem_type = str(match.struct_elem[Name.S]).lstrip("/")

        emc_op = pikepdf.ContentStreamInstruction([], Operator("EMC"))
        bdc_dict = Dictionary({"/MCID": match.mcid})
        bdc_op = pikepdf.ContentStreamInstruction(
            [Name(f"/{elem_type}"), bdc_dict], Operator("BDC")
        )

        # Insert EMC after last operator of block (end is exclusive, so insert at end)
        new_ops.insert(match.block_end, emc_op)
        # Insert BDC before first operator (start index is still valid since we added at end)
        new_ops.insert(match.block_start, bdc_op)

        return new_ops

    # ------------------------------------------------------------------
    # MCID linking
    # ------------------------------------------------------------------

    def _set_mcid_on_element(self, elem: Any, mcid: int, page_obj: Any) -> None:
        """Attach an MCR (Marked Content Reference) to a structure element."""
        mcr = Dictionary(
            {
                "/Type": Name("/MCR"),
                "/MCID": mcid,
                "/Pg": page_obj,
            }
        )

        existing = elem.get(Name.K)
        if existing is None:
            elem[Name.K] = mcr
        elif isinstance(existing, Array):
            existing.append(mcr)
        else:
            elem[Name.K] = Array([existing, mcr])

    def _create_p_element(self, page_obj: Any, text: Optional[str]) -> Any:
        """Create a new /P structure element under StructTreeRoot."""
        struct_root = self.pdf.Root[Name.StructTreeRoot]
        elem_dict: dict = {
            "/Type": Name.StructElem,
            "/S": Name.P,
            "/P": struct_root,
            "/Pg": page_obj,
        }
        if text:
            elem_dict["/ActualText"] = String(text)

        elem = self.pdf.make_indirect(Dictionary(elem_dict))

        # Add to StructTreeRoot kids
        kids = struct_root.get(Name.K)
        if kids is None:
            struct_root[Name.K] = Array([elem])
        elif isinstance(kids, Array):
            kids.append(elem)
        else:
            struct_root[Name.K] = Array([kids, elem])

        return elem

    # ------------------------------------------------------------------
    # ParentTree
    # ------------------------------------------------------------------

    def _build_parent_tree(self, struct_root: Any) -> None:
        """Build /ParentTree /Nums array from MCID->element mappings."""
        nums_list: List[Any] = []

        for page_idx in sorted(self._parent_tree_entries.keys()):
            entries = self._parent_tree_entries[page_idx]
            if not entries:
                continue

            # Sort by MCID
            entries_sorted = sorted(entries, key=lambda e: e[0])
            max_mcid = entries_sorted[-1][0]

            # pikepdf accepts None as null in Array
            page_array: List[Any] = [None] * (max_mcid + 1)
            for mcid_val, elem in entries_sorted:
                page_array[mcid_val] = elem

            nums_list.append(page_idx)
            nums_list.append(self.pdf.make_indirect(Array(page_array)))

        parent_tree = struct_root.get(Name.ParentTree)
        if parent_tree is None:
            parent_tree = Dictionary({"/Nums": Array([])})
            struct_root[Name.ParentTree] = parent_tree

        parent_tree[Name.Nums] = Array(nums_list)

    # ------------------------------------------------------------------
    # Document root
    # ------------------------------------------------------------------

    def _ensure_document_root(self, struct_root: Any) -> Any:
        """Wrap all StructTreeRoot /K kids under a /Document element if missing."""
        kids = struct_root.get(Name.K)

        # Check if Document element already exists as the sole child
        if kids is not None:
            if isinstance(kids, Array) and len(kids) == 1:
                candidate = kids[0]
                if (
                    hasattr(candidate, "keys")
                    and Name.S in candidate
                    and str(candidate[Name.S]) == "/Document"
                ):
                    return candidate
            elif (
                not isinstance(kids, Array)
                and hasattr(kids, "keys")
                and Name.S in kids
                and str(kids[Name.S]) == "/Document"
            ):
                return kids

        # Collect current kids
        current_kids: list = []
        if kids is not None:
            if isinstance(kids, Array):
                current_kids = list(kids)
            else:
                current_kids = [kids]

        doc_kids = Array(current_kids)
        doc_elem = self.pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name("/Document"),
                    "/P": struct_root,
                    "/K": doc_kids,
                }
            )
        )

        # Re-parent existing children
        for child in current_kids:
            if hasattr(child, "keys") and Name.P in child:
                child[Name.P] = doc_elem

        struct_root[Name.K] = Array([doc_elem])
        return doc_elem

    # ------------------------------------------------------------------
    # PDF/UA identifier
    # ------------------------------------------------------------------

    def _set_pdfua_identifier(self) -> None:
        """Set pdfua:part=1 in XMP metadata."""
        try:
            with self.pdf.open_metadata() as meta:
                meta["{http://www.aiim.org/pdfua/ns/id/}part"] = "1"
        except Exception as exc:
            logger.warning(f"Could not set PDF/UA-1 identifier: {exc}")


__all__ = ["ContentTaggerV2", "MatchedBlock", "TABLE_TAGS"]
