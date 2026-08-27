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
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

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

# Structure types that describe a drawn image rather than a run of text.
FIGURE_TAGS = {"Figure", "Formula"}

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


@dataclass(frozen=True)
class FormulaAssociationResult:
    """Bounded identity needed to reverse-verify one Formula association."""

    success: bool
    error: Optional[str] = None
    page_number: int = 0
    image_xref: int = 0
    occurrence_ordinal: int = 0
    struct_parent: int = -1
    mcid: int = -1
    mathml_sha256: str = ""


@dataclass(frozen=True)
class ScannedRegionAssociationResult:
    """Identity and visual evidence for one clipped Formula association."""

    page_number: int
    image_xref: int
    resource_name: str
    struct_parent: int
    mcid: int
    mathml_sha256: str
    formula_bbox: tuple[float, float, float, float]
    render_signatures: tuple[tuple[int, int, int, int, int, str], ...]
    ocr_resource_name: str = ""
    ocr_struct_parent: int = -1
    ocr_group_owners: tuple[tuple[str, int], ...] = ()
    ocr_before_mcids: tuple[int, ...] = ()
    ocr_after_mcids: tuple[int, ...] = ()
    ocr_payload_sha256: str = ""
    ocr_font_sha256: str = ""
    page_text_sha256: str = ""
    success: bool = True


class ScannedRegionAssociationError(RuntimeError):
    """A scanned-region association failed its fail-closed contract."""


@dataclass(frozen=True)
class _OCRTextGroup:
    start: int
    end: int
    owner: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _OCRFormPlan:
    resource_name: str
    form: Any
    groups: tuple[_OCRTextGroup, ...]


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

    def __init__(
        self,
        pdf: Any,
        fitz_doc: Any,
        *,
        excluded_image_occurrences: Optional[List[Any]] = None,
    ) -> None:
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
        self._excluded_image_occurrences: Dict[int, set[tuple[int, int]]] = {}
        for pending in excluded_image_occurrences or []:
            page_idx = int(pending.page_number) - 1
            self._excluded_image_occurrences.setdefault(page_idx, set()).add(
                (int(pending.image_xref), int(pending.occurrence_ordinal))
            )
        self._next_mcid: int = 0
        # Maps StructParents number-tree key -> list of (mcid, struct_elem)
        self._parent_tree_entries: Dict[int, List[Tuple[int, Any]]] = {}
        self._ensure_struct_tree()
        self._preserved_parent_tree_entries: Dict[int, Any] = {}
        self._parent_tree_parse_error: Optional[Exception] = None
        try:
            _, entries = _number_tree_entries(self.pdf.Root[Name.StructTreeRoot])
            self._preserved_parent_tree_entries = dict(entries)
        except Exception as exc:
            self._parent_tree_parse_error = exc

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
        if self._parent_tree_parse_error is not None:
            raise ValueError(
                "Existing ParentTree is malformed; refusing overwrite"
            ) from (self._parent_tree_parse_error)
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

        content_blocks = self._find_content_blocks(ops, page)
        excluded_indices = self._excluded_do_indices(page_idx, page, ops)
        content_blocks = [
            block for block in content_blocks if block[0] not in excluded_indices
        ]
        if not content_blocks:
            return page_stats

        fitz_blocks = self._get_fitz_blocks(page_idx)
        fitz_image_blocks = self._get_fitz_image_blocks(page_idx)
        used_elem_indices: set = set()
        matches: List[MatchedBlock] = []

        existing_struct_parent = page.obj.get(Name.StructParents)
        if existing_struct_parent is None:
            used_keys = set(self._preserved_parent_tree_entries) | set(
                self._parent_tree_entries
            )
            struct_parent = page_idx
            while struct_parent in used_keys:
                struct_parent += 1
        else:
            struct_parent = int(existing_struct_parent)
        if struct_parent in self._parent_tree_entries:
            raise ValueError("Multiple pages share one StructParents key")
        existing_page_array = self._preserved_parent_tree_entries.get(struct_parent)
        existing_mcids: set[int] = set()
        for op in ops:
            if str(op.operator) == "BDC" and len(op.operands) == 2:
                try:
                    existing_mcids.add(int(op.operands[1][Name.MCID]))
                except Exception:
                    pass
        self._next_mcid = (
            max(
                existing_mcids
                | {
                    (
                        len(existing_page_array) - 1
                        if isinstance(existing_page_array, Array)
                        else -1
                    )
                },
                default=-1,
            )
            + 1
        )

        for cb_start, cb_end, kind in content_blocks:
            block_ops = ops[cb_start:cb_end]
            mcid = self._next_mcid

            candidate_boxes = fitz_image_blocks if kind == "image" else fitz_blocks
            result = self._match_element_to_block(
                elements, block_ops, candidate_boxes, used_elem_indices, kind
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
                # Create an element for unmatched blocks, of the right type so
                # an image never ends up described as a paragraph.
                if kind == "image":
                    elem = self._create_figure_element(page.obj)
                else:
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
            logger.error(
                f"Page {page_idx}: failed to write tagged content stream: {exc}"
            )
            return page_stats

        page.obj[Name.StructParents] = struct_parent
        self._parent_tree_entries[struct_parent] = page_entries

        return page_stats

    def _excluded_do_indices(
        self, page_idx: int, page: Any, ops: List[Any]
    ) -> set[int]:
        """Resolve exact direct image draws reserved for Formula association."""
        excluded = self._excluded_image_occurrences.get(page_idx, set())
        if not excluded:
            return set()
        ordinals: Dict[int, int] = {}
        indices: set[int] = set()
        resources = page.obj.get(Name.Resources)
        xobjects = resources.get(Name.XObject) if resources is not None else None
        if xobjects is None:
            return indices
        for index, op in enumerate(ops):
            if str(op.operator) != "Do" or not op.operands:
                continue
            try:
                raw_name = str(op.operands[0])
                resource_name = raw_name if raw_name.startswith("/") else f"/{raw_name}"
                xobject = xobjects.get(Name(resource_name))
                if xobject is None or str(xobject.get(Name.Subtype, "")) != "/Image":
                    continue
                xref = int(xobject.objgen[0])
                ordinal = ordinals.get(xref, 0)
                if (xref, ordinal) in excluded:
                    indices.add(index)
                ordinals[xref] = ordinal + 1
            except Exception:
                continue
        return indices

    # ------------------------------------------------------------------
    # PyMuPDF block extraction
    # ------------------------------------------------------------------

    def _get_fitz_blocks(
        self, page_idx: int
    ) -> List[Tuple[float, float, float, float, str]]:
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

    def _get_fitz_image_blocks(
        self, page_idx: int
    ) -> List[Tuple[float, float, float, float, str]]:
        """Get image rectangles from PyMuPDF, in PDF coordinate space.

        get_text("blocks") only reports text, so image placement has to be
        asked for separately before a Figure can be matched by position.
        """
        blocks: List[Tuple[float, float, float, float, str]] = []
        try:
            fitz_page = self.fitz_doc[page_idx]
            page_height = fitz_page.rect.height

            for img in fitz_page.get_images(full=True):
                xref = img[0]
                try:
                    rects = fitz_page.get_image_rects(xref)
                except Exception:
                    continue
                for rect in rects:
                    # Convert fitz (top-left origin) to PDF (bottom-left origin)
                    blocks.append(
                        (
                            float(rect.x0),
                            float(page_height - rect.y1),
                            float(rect.x1),
                            float(page_height - rect.y0),
                            "",
                        )
                    )
        except Exception as exc:
            logger.warning(f"Page {page_idx}: fitz image extraction failed: {exc}")
        return blocks

    # ------------------------------------------------------------------
    # Content stream block finding
    # ------------------------------------------------------------------

    def _find_content_blocks(
        self, ops: List[Any], page: Any = None
    ) -> List[Tuple[int, int, str]]:
        """Find taggable content blocks in the operator list.

        Two kinds of block are recognised:

        - "text": a BT/ET pair, one run of show-text operators.
        - "image": a single image-drawing operator, either an XObject draw
          (``Do`` naming a /Subtype /Image) or an inline image (BI/ID/EI).

        Images matter as much as text here. A screenshot or scan has no BT at
        all, so a text-only search finds nothing, emits no BDC, and leaves the
        ParentTree empty — which makes any Figure alt text unreachable to a
        screen reader even though the structure element exists.

        Returns (start_index, end_index, kind) with end_index exclusive.
        """
        blocks: List[Tuple[int, int, str]] = []
        marked_depth = 0
        i = 0
        while i < len(ops):
            op_name = str(ops[i].operator)
            if op_name in ("BMC", "BDC"):
                marked_depth += 1
                i += 1
            elif op_name == "EMC":
                if marked_depth <= 0:
                    raise ValueError("Unbalanced EMC in page content stream")
                marked_depth -= 1
                i += 1
            elif op_name == "BT":
                start = i
                j = i + 1
                contains_markers = marked_depth > 0
                while j < len(ops) and str(ops[j].operator) != "ET":
                    if str(ops[j].operator) in ("BMC", "BDC", "EMC"):
                        contains_markers = True
                    j += 1
                end = j + 1 if j < len(ops) else j
                if not contains_markers:
                    blocks.append((start, end, "text"))
                i = end
            elif op_name == "INLINE_IMAGE" and marked_depth == 0:
                blocks.append((i, i + 1, "image"))
                i += 1
            elif (
                op_name == "Do"
                and marked_depth == 0
                and self._is_image_xobject(page, ops[i])
            ):
                blocks.append((i, i + 1, "image"))
                i += 1
            else:
                i += 1
        if marked_depth != 0:
            raise ValueError("Unbalanced marked content in page content stream")
        return blocks

    def _is_image_xobject(self, page: Any, op: Any) -> bool:
        """Check whether a ``Do`` operator draws an image (not a Form XObject)."""
        if page is None:
            return False
        try:
            operands = list(op.operands)
            if not operands:
                return False
            raw = str(operands[0])
            key = raw if raw.startswith("/") else f"/{raw}"

            resources = page.obj.get(Name.Resources)
            if resources is None:
                return False
            xobjects = resources.get(Name.XObject)
            if xobjects is None:
                return False

            xobj = xobjects.get(Name(key))
            if xobj is None:
                return False
            return str(xobj.get(Name.Subtype, "")) == "/Image"
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Element matching
    # ------------------------------------------------------------------

    def _match_element_to_block(
        self,
        elements: List[Any],
        block_ops: List[Any],
        fitz_blocks: List[Tuple[float, float, float, float, str]],
        used_indices: set,
        kind: str = "text",
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

            # Keep the two kinds apart: a text run must not consume the Figure
            # that describes an image, and an image must not be handed a
            # paragraph element.
            is_figure = elem_type_raw in FIGURE_TAGS
            if kind == "image" and not is_figure:
                continue
            if kind == "text" and is_figure:
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

        # --- Ordinal fallback for images ---
        # A Figure written by the alt-text pass may carry no /A /BBox, and an
        # image block has no text to fall back on, so neither strategy above
        # can fire. Binding the next unused Figure in document order keeps the
        # generated alt text attached to the image it describes; creating a
        # fresh Figure instead would orphan it.
        if kind == "image":
            for elem_idx, elem in enumerate(elements):
                if elem_idx in used_indices:
                    continue
                if Name.S not in elem:
                    continue
                if str(elem[Name.S]).lstrip("/") not in FIGURE_TAGS:
                    continue
                used_indices.add(elem_idx)
                return (elem, "ordinal", 0.50)

        return None

    def _get_element_bbox(
        self, elem: Any
    ) -> Optional[Tuple[float, float, float, float]]:
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

    def _create_figure_element(self, page_obj: Any) -> Any:
        """Create a bare /Figure element for an image with no structure element.

        No /Alt is set: alt text is generated upstream, and inventing an empty
        one here would satisfy the tagging check while leaving the image
        undescribed. A Figure without /Alt stays visible to the alt-text
        checker as a real, reportable issue.
        """
        return self._create_struct_element(page_obj, Name.Figure, None)

    def _create_p_element(self, page_obj: Any, text: Optional[str]) -> Any:
        """Create a new /P structure element under StructTreeRoot."""
        return self._create_struct_element(page_obj, Name.P, text)

    def _create_struct_element(
        self, page_obj: Any, struct_type: Any, text: Optional[str]
    ) -> Any:
        """Create a structure element of the given type under StructTreeRoot."""
        struct_root = self.pdf.Root[Name.StructTreeRoot]
        elem_dict: dict = {
            "/Type": Name.StructElem,
            "/S": struct_type,
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
        """Merge selected page arrays without flattening the existing number tree."""
        parent_tree, _ = _number_tree_entries(struct_root)
        for struct_parent, new_entries in sorted(self._parent_tree_entries.items()):
            preserved = self._preserved_parent_tree_entries.get(struct_parent)
            if preserved is not None and not isinstance(preserved, Array):
                raise ValueError(
                    "Existing selected-page ParentTree entry is not an array"
                )
            page_array: List[Any] = list(preserved) if preserved is not None else []
            for mcid_val, elem in sorted(new_entries, key=lambda entry: entry[0]):
                while len(page_array) <= mcid_val:
                    page_array.append(None)
                if page_array[mcid_val] is not None:
                    raise ValueError("ParentTree MCID collision")
                page_array[mcid_val] = elem
            _set_number_tree_value(
                parent_tree,
                struct_parent,
                self.pdf.make_indirect(Array(page_array)),
            )

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


def _association_failure(pending: Any, error: str) -> FormulaAssociationResult:
    return FormulaAssociationResult(
        success=False,
        error=error,
        page_number=int(getattr(pending, "page_number", 0) or 0),
        image_xref=int(getattr(pending, "image_xref", 0) or 0),
        occurrence_ordinal=int(getattr(pending, "occurrence_ordinal", 0) or 0),
    )


def _resolved_image_do_indices(page: Any, ops: List[Any], image_xref: int) -> List[int]:
    """Resolve direct image draws by resource object identity, never by name alone."""
    resources = page.obj.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        return []
    matches: List[int] = []
    for index, op in enumerate(ops):
        if str(op.operator) != "Do" or not op.operands:
            continue
        try:
            resource_name = str(op.operands[0])
            if not resource_name.startswith("/"):
                resource_name = f"/{resource_name}"
            xobject = xobjects.get(Name(resource_name))
            if (
                xobject is not None
                and str(xobject.get(Name.Subtype, "")) == "/Image"
                and int(xobject.objgen[0]) == image_xref
            ):
                matches.append(index)
        except Exception:
            continue
    return matches


def _form_xobject_reaches_image(
    resources: Any, image_xref: int, seen: set[Any], *, inside_form: bool = False
) -> bool:
    """Detect, but deliberately do not resolve, images nested in Form XObjects."""
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        return False
    for _, xobject in xobjects.items():
        try:
            subtype = str(xobject.get(Name.Subtype, ""))
            if (
                inside_form
                and subtype == "/Image"
                and int(xobject.objgen[0]) == image_xref
            ):
                return True
            if subtype == "/Form":
                identity = tuple(xobject.objgen)
                if identity in seen:
                    continue
                seen.add(identity)
                nested = xobject.get(Name.Resources) or resources
                if _form_xobject_reaches_image(
                    nested, image_xref, seen, inside_form=True
                ):
                    return True
        except Exception:
            continue
    return False


def _number_tree_entries(struct_root: Any) -> tuple[Any, List[tuple[int, Any]]]:
    parent_tree = struct_root.get(Name.ParentTree)
    if parent_tree is None:
        parent_tree = Dictionary({"/Nums": Array([])})
        struct_root[Name.ParentTree] = parent_tree
    entries: List[tuple[int, Any]] = []
    seen: set[int] = set()

    def visit(node: Any, *, require_limits: bool = False) -> List[tuple[int, Any]]:
        kids = node.get(Name("/Kids"))
        if kids is not None:
            if Name.Nums in node:
                raise ValueError("invalid_parent_tree")
            if not isinstance(kids, Array) or not kids:
                raise ValueError("invalid_parent_tree")
            node_entries: List[tuple[int, Any]] = []
            previous_max: Optional[int] = None
            for kid in kids:
                child_entries = visit(kid, require_limits=True)
                child_min = child_entries[0][0]
                child_max = child_entries[-1][0]
                if previous_max is not None and child_min <= previous_max:
                    raise ValueError("invalid_parent_tree")
                previous_max = child_max
                node_entries.extend(child_entries)
            limits = node.get(Name("/Limits"))
            if limits is not None and (
                not isinstance(limits, Array)
                or len(limits) != 2
                or int(limits[0]) != node_entries[0][0]
                or int(limits[1]) != node_entries[-1][0]
            ):
                raise ValueError("invalid_parent_tree")
            return node_entries
        nums = node.get(Name.Nums, Array([]))
        if not isinstance(nums, Array) or len(nums) % 2:
            raise ValueError("invalid_parent_tree")
        node_entries: List[tuple[int, Any]] = []
        previous_key: Optional[int] = None
        for index in range(0, len(nums), 2):
            key = int(nums[index])
            if key in seen or (previous_key is not None and key <= previous_key):
                raise ValueError("parent_tree_collision")
            previous_key = key
            seen.add(key)
            item = (key, nums[index + 1])
            entries.append(item)
            node_entries.append(item)
        limits = node.get(Name("/Limits"))
        if require_limits and (
            not node_entries
            or not isinstance(limits, Array)
            or len(limits) != 2
            or int(limits[0]) != node_entries[0][0]
            or int(limits[1]) != node_entries[-1][0]
        ):
            raise ValueError("invalid_parent_tree")
        return node_entries

    visit(parent_tree)
    return parent_tree, entries


def _set_number_tree_value(parent_tree: Any, key: int, value: Any) -> None:
    """Replace an existing number-tree value without flattening legal /Kids."""
    kids = parent_tree.get(Name("/Kids"))
    if kids is not None:
        if not isinstance(kids, Array) or not kids:
            raise ValueError("invalid_parent_tree")
        for kid in kids:
            before = dict(_number_tree_node_entries(kid))
            if key in before:
                _set_number_tree_value(kid, key, value)
                _update_number_tree_limits(parent_tree)
                return
        selected = kids[-1]
        for kid in kids:
            entries = _number_tree_node_entries(kid)
            if entries and key <= max(item[0] for item in entries):
                selected = kid
                break
        _set_number_tree_value(selected, key, value)
        _update_number_tree_limits(parent_tree)
        return
    nums = parent_tree.get(Name.Nums)
    if nums is None:
        nums = Array([])
    if not isinstance(nums, Array) or len(nums) % 2:
        raise ValueError("invalid_parent_tree")
    entries = {int(nums[index]): nums[index + 1] for index in range(0, len(nums), 2)}
    entries[key] = value
    ordered = sorted(entries.items())
    flattened: List[Any] = []
    for entry_key, entry_value in ordered:
        flattened.extend((entry_key, entry_value))
    parent_tree[Name.Nums] = Array(flattened)
    parent_tree[Name("/Limits")] = Array([ordered[0][0], ordered[-1][0]])


def _update_number_tree_limits(node: Any) -> None:
    entries = sorted(_number_tree_node_entries(node), key=lambda item: item[0])
    if not entries:
        raise ValueError("invalid_parent_tree")
    node[Name("/Limits")] = Array([entries[0][0], entries[-1][0]])


def _number_tree_node_entries(node: Any) -> List[tuple[int, Any]]:
    entries: List[tuple[int, Any]] = []
    kids = node.get(Name("/Kids"))
    if kids is not None:
        for kid in kids:
            entries.extend(_number_tree_node_entries(kid))
        return entries
    nums = node.get(Name.Nums, Array([]))
    return [(int(nums[index]), nums[index + 1]) for index in range(0, len(nums), 2)]


def _append_formula_to_structure(pdf: Any, formula: Any) -> None:
    struct_root = pdf.Root[Name.StructTreeRoot]
    parent = struct_root
    kids = struct_root.get(Name.K)
    candidates = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    if (
        len(candidates) == 1
        and hasattr(candidates[0], "keys")
        and str(candidates[0].get(Name.S, "")) == "/Document"
    ):
        parent = candidates[0]
    formula[Name.P] = parent
    parent_kids = parent.get(Name.K)
    if parent_kids is None:
        parent[Name.K] = Array([formula])
    elif isinstance(parent_kids, Array):
        parent_kids.append(formula)
    else:
        parent[Name.K] = Array([parent_kids, formula])


def associate_image_formula(
    pdf: Any, fitz_doc: Any, pending: Any
) -> FormulaAssociationResult:
    """Associate one fully identified displayed occurrence with an exact image ``Do``.

    This is deliberately a post-ContentTagger operation. Any ambiguity or
    collision returns a failure without applying a bbox-only structure element.
    """
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    rollback: Optional[Dict[str, Any]] = None
    try:
        page_number = int(pending.page_number)
        image_xref = int(pending.image_xref)
        image_index = int(pending.image_index)
        ordinal = int(pending.occurrence_ordinal)
        bbox = tuple(float(value) for value in pending.bbox)
        occurrence_id = str(pending.occurrence_id)
        image_stream_sha256 = str(pending.image_stream_sha256)
        alt_text = str(pending.alt_text)
        mathml = str(pending.mathml_string)
        if (
            page_number < 1
            or page_number > len(pdf.pages)
            or len(bbox) != 4
            or not alt_text
            or len(alt_text) > 1024
            or not alt_text.isprintable()
            or not mathml
            or len(mathml.encode("utf-8")) > 65536
            or re.fullmatch(r"[0-9a-f]{64}", image_stream_sha256) is None
        ):
            return _association_failure(pending, "invalid_association_request")
        digest = hashlib.sha256(mathml.encode("utf-8")).hexdigest()
        if digest != pending.verification_evidence.mathml_sha256:
            return _association_failure(pending, "mathml_evidence_mismatch")

        occurrences = _displayed_image_occurrences(
            fitz_doc[page_number - 1], page_number
        )
        exact = [
            item
            for item in occurrences
            if item["page_number"] == page_number
            and item["image_xref"] == image_xref
            and item["image_index"] == image_index
            and item["occurrence_ordinal"] == ordinal
            and item["occurrence_id"] == occurrence_id
            and all(
                abs(left - right) <= 1e-6 for left, right in zip(item["bbox"], bbox)
            )
        ]
        if len(exact) != 1:
            return _association_failure(pending, "occurrence_identity_mismatch")
        current_image = fitz_doc.extract_image(image_xref).get("image")
        if (
            not isinstance(current_image, bytes)
            or hashlib.sha256(current_image).hexdigest() != image_stream_sha256
        ):
            return _association_failure(pending, "image_stream_identity_mismatch")

        page = pdf.pages[page_number - 1]
        ops = list(pikepdf.parse_content_stream(page))
        do_indices = _resolved_image_do_indices(page, ops, image_xref)
        displayed_same_xref = [
            item for item in occurrences if item["image_xref"] == image_xref
        ]
        if len(do_indices) != len(displayed_same_xref) or ordinal >= len(do_indices):
            if _form_xobject_reaches_image(
                page.obj.get(Name.Resources), image_xref, set()
            ):
                return _association_failure(pending, "form_xobject_image_manual")
            return _association_failure(pending, "exact_do_unresolved")
        target_index = do_indices[ordinal]

        root_existed = Name.StructTreeRoot in pdf.Root
        mark_info_existed = Name.MarkInfo in pdf.Root
        struct_parents_existed = Name.StructParents in page.obj
        existing_root = pdf.Root.get(Name.StructTreeRoot)
        existing_kids = existing_root.get(Name.K) if existing_root is not None else None
        rollback = {
            "root_existed": root_existed,
            "mark_info_existed": mark_info_existed,
            "struct_parents_existed": struct_parents_existed,
            "struct_parents": page.obj.get(Name.StructParents),
            "contents": page.obj.get(Name.Contents),
            "root_kids_object": existing_kids,
            "root_kids": (
                list(existing_kids) if isinstance(existing_kids, Array) else None
            ),
            "number_nodes": [],
            "value_arrays": [],
            "structure_arrays": [],
        }
        if existing_root is not None:

            def snapshot_structure_arrays(element: Any) -> None:
                kids = element.get(Name.K) if hasattr(element, "keys") else None
                if isinstance(kids, Array):
                    rollback["structure_arrays"].append((kids, list(kids)))
                    for child in kids:
                        if (
                            hasattr(child, "keys")
                            and str(child.get(Name.Type, "")) != "/MCR"
                        ):
                            snapshot_structure_arrays(child)
                elif hasattr(kids, "keys") and str(kids.get(Name.Type, "")) != "/MCR":
                    snapshot_structure_arrays(kids)

            snapshot_structure_arrays(existing_root)
        if existing_root is not None and existing_root.get(Name.ParentTree) is not None:

            def snapshot_number_arrays(node: Any) -> None:
                kids = node.get(Name("/Kids"))
                if kids is not None:
                    nums = node.get(Name.Nums)
                    limits = node.get(Name("/Limits"))
                    rollback["number_nodes"].append(
                        {
                            "node": node,
                            "nums_existed": Name.Nums in node,
                            "nums_object": nums,
                            "nums_values": (
                                list(nums) if isinstance(nums, Array) else None
                            ),
                            "limits_existed": Name("/Limits") in node,
                            "limits_object": limits,
                            "limits_values": (
                                list(limits) if isinstance(limits, Array) else None
                            ),
                        }
                    )
                    if isinstance(nums, Array):
                        for index in range(1, len(nums), 2):
                            if isinstance(nums[index], Array):
                                rollback["value_arrays"].append(
                                    (nums[index], list(nums[index]))
                                )
                    for kid in kids:
                        snapshot_number_arrays(kid)
                else:
                    nums = node.get(Name.Nums)
                    limits = node.get(Name("/Limits"))
                    rollback["number_nodes"].append(
                        {
                            "node": node,
                            "nums_existed": Name.Nums in node,
                            "nums_object": nums,
                            "nums_values": (
                                list(nums) if isinstance(nums, Array) else None
                            ),
                            "limits_existed": Name("/Limits") in node,
                            "limits_object": limits,
                            "limits_values": (
                                list(limits) if isinstance(limits, Array) else None
                            ),
                        }
                    )
                    if isinstance(nums, Array):
                        for index in range(1, len(nums), 2):
                            if isinstance(nums[index], Array):
                                rollback["value_arrays"].append(
                                    (nums[index], list(nums[index]))
                                )

            snapshot_number_arrays(existing_root[Name.ParentTree])

        if Name.StructTreeRoot not in pdf.Root:
            from src.education.remediation.pdf_structure import PDFStructureTree

            PDFStructureTree(pdf)

        used_mcids: set[int] = set()
        for op in ops:
            if str(op.operator) == "BDC" and len(op.operands) == 2:
                try:
                    used_mcids.add(int(op.operands[1][Name.MCID]))
                except Exception:
                    pass
        struct_root = pdf.Root[Name.StructTreeRoot]
        parent_tree, entries = _number_tree_entries(struct_root)
        existing_struct_parent = page.obj.get(Name.StructParents)
        if existing_struct_parent is None:
            used_keys = {key for key, _ in entries}
            struct_parent = 0
            while struct_parent in used_keys:
                struct_parent += 1
            page.obj[Name.StructParents] = struct_parent
        else:
            struct_parent = int(existing_struct_parent)
        page_entry = next(
            (value for key, value in entries if key == struct_parent), None
        )
        if page_entry is not None and not isinstance(page_entry, Array):
            return _association_failure(pending, "parent_tree_collision")
        page_array = page_entry if page_entry is not None else Array([])
        mcid = max(used_mcids | {len(page_array) - 1}, default=-1) + 1
        while len(page_array) <= mcid:
            page_array.append(None)
        if page_array[mcid] is not None:
            return _association_failure(pending, "mcid_collision")

        from src.education.remediation.pdf_structure import PDFStructureTree

        formula = PDFStructureTree(pdf).create_formula_element(
            page_num=page_number,
            alt_text=alt_text,
            mathml_string=mathml,
            bbox=bbox,
            mcid=mcid,
        )
        _append_formula_to_structure(pdf, formula)
        page_array[mcid] = formula
        if page_entry is None:
            page_array = pdf.make_indirect(page_array)
        _set_number_tree_value(parent_tree, struct_parent, page_array)

        bdc = pikepdf.ContentStreamInstruction(
            [Name("/Formula"), Dictionary({"/MCID": mcid})], Operator("BDC")
        )
        emc = pikepdf.ContentStreamInstruction([], Operator("EMC"))
        new_ops = list(ops)
        new_ops.insert(target_index + 1, emc)
        new_ops.insert(target_index, bdc)
        page.obj[Name.Contents] = pdf.make_stream(
            pikepdf.unparse_content_stream(new_ops)
        )
        return FormulaAssociationResult(
            success=True,
            page_number=page_number,
            image_xref=image_xref,
            occurrence_ordinal=ordinal,
            struct_parent=struct_parent,
            mcid=mcid,
            mathml_sha256=digest,
        )
    except Exception as exc:
        if rollback is not None:
            rollback_page = pdf.pages[int(getattr(pending, "page_number", 1)) - 1]
            rollback_page.obj[Name.Contents] = rollback["contents"]
            if rollback["struct_parents_existed"]:
                rollback_page.obj[Name.StructParents] = rollback["struct_parents"]
            elif Name.StructParents in rollback_page.obj:
                del rollback_page.obj[Name.StructParents]
            if not rollback["root_existed"] and Name.StructTreeRoot in pdf.Root:
                del pdf.Root[Name.StructTreeRoot]
            elif rollback["root_existed"]:
                root = pdf.Root[Name.StructTreeRoot]
                old_kids = rollback["root_kids_object"]
                if isinstance(old_kids, Array):
                    while len(old_kids):
                        del old_kids[-1]
                    for child in rollback["root_kids"]:
                        old_kids.append(child)
                    root[Name.K] = old_kids
                elif old_kids is None and Name.K in root:
                    del root[Name.K]
                else:
                    root[Name.K] = old_kids
                for state in rollback["number_nodes"]:
                    node = state["node"]
                    if state["nums_existed"]:
                        nums = state["nums_object"]
                        if isinstance(nums, Array):
                            while len(nums):
                                del nums[-1]
                            for value in state["nums_values"]:
                                nums.append(value)
                        node[Name.Nums] = nums
                    elif Name.Nums in node:
                        del node[Name.Nums]
                    limits_name = Name("/Limits")
                    if state["limits_existed"]:
                        limits = state["limits_object"]
                        if isinstance(limits, Array):
                            while len(limits):
                                del limits[-1]
                            for value in state["limits_values"]:
                                limits.append(value)
                        node[limits_name] = limits
                    elif limits_name in node:
                        del node[limits_name]
                for array, values in (
                    rollback["value_arrays"] + rollback["structure_arrays"]
                ):
                    while len(array):
                        del array[-1]
                    for value in values:
                        array.append(value)
            if not rollback["mark_info_existed"] and Name.MarkInfo in pdf.Root:
                del pdf.Root[Name.MarkInfo]
        logger.warning("Exact Formula association failed closed: %s", exc)
        return _association_failure(pending, "association_failed")


_REGION_RENDER_DPI = (144, 288)
_REGION_FLOAT_TOLERANCE = 1e-6
_REGION_MAX_RENDER_DIMENSION = 16_384
_REGION_MAX_RENDER_PIXELS = 25_000_000
_REGION_MAX_RENDER_BYTES = 75_000_000
_REGION_MAX_TRANSACTION_RENDER_BYTES = 256 * 1024 * 1024


def _region_render_geometry(
    document: Any, page_number: int, dpi: int
) -> tuple[int, int]:
    """Return conservatively rounded render dimensions within one signature budget."""
    if not 1 <= int(page_number) <= len(document):
        raise ScannedRegionAssociationError("region_page_changed")
    page = document[int(page_number) - 1]
    scale = dpi / 72.0
    expected_width = math.ceil(float(page.rect.width) * scale)
    expected_height = math.ceil(float(page.rect.height) * scale)
    expected_pixels = expected_width * expected_height
    if (
        expected_width <= 0
        or expected_height <= 0
        or expected_width > _REGION_MAX_RENDER_DIMENSION
        or expected_height > _REGION_MAX_RENDER_DIMENSION
        or expected_pixels > _REGION_MAX_RENDER_PIXELS
        or expected_pixels * 3 > _REGION_MAX_RENDER_BYTES
    ):
        raise ScannedRegionAssociationError("region_render_budget_exceeded")
    return expected_width, expected_height


def preflight_scanned_region_render_budget(
    document: Any, page_numbers: Any
) -> tuple[int, ...]:
    """Bound one before/after render phase across unique 1-based region pages."""
    try:
        unique_pages = tuple(sorted({int(value) for value in page_numbers}))
    except (TypeError, ValueError) as exc:
        raise ScannedRegionAssociationError("region_render_budget_invalid") from exc
    aggregate_bytes = 0
    for page_number in unique_pages:
        for dpi in _REGION_RENDER_DPI:
            width, height = _region_render_geometry(document, page_number, dpi)
            aggregate_bytes += width * height * 3
            if aggregate_bytes > _REGION_MAX_TRANSACTION_RENDER_BYTES:
                raise ScannedRegionAssociationError(
                    "region_transaction_render_budget_exceeded"
                )
    return unique_pages


def _page_render_signature(document: Any, page_number: int, dpi: int) -> tuple:
    """Hash one deterministic, annotation-free RGB page rendering."""
    page = document[page_number - 1]
    scale = dpi / 72.0
    _region_render_geometry(document, page_number, dpi)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
        annots=False,
    )
    actual_bytes = int(pixmap.stride) * int(pixmap.height)
    if (
        int(pixmap.width) > _REGION_MAX_RENDER_DIMENSION
        or int(pixmap.height) > _REGION_MAX_RENDER_DIMENSION
        or int(pixmap.width) * int(pixmap.height) > _REGION_MAX_RENDER_PIXELS
        or actual_bytes > _REGION_MAX_RENDER_BYTES
    ):
        raise ScannedRegionAssociationError("region_render_budget_exceeded")
    samples = getattr(pixmap, "samples_mv", None)
    if samples is None:
        samples = memoryview(pixmap.samples)
    return (
        dpi,
        int(pixmap.width),
        int(pixmap.height),
        int(pixmap.stride),
        int(pixmap.n),
        hashlib.sha256(samples).hexdigest(),
    )


def _region_crop_pixel_sha256(source: bytes, pixel_bbox: tuple[int, ...]) -> tuple:
    """Return decoded dimensions and the detector-compatible crop digest."""
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            width, height = image.size
            crop = image.crop(pixel_bbox)
            header = f"{crop.mode}|{crop.width}|{crop.height}|".encode("ascii")
            digest = hashlib.sha256(header + crop.tobytes()).hexdigest()
            crop.close()
            return int(width), int(height), digest
    except Exception as exc:
        raise ScannedRegionAssociationError("region_source_decode_failed") from exc


def _validate_equation_only_region_source(
    source: bytes, pixel_bbox: tuple[int, ...]
) -> None:
    """Refuse to hide meaningful raster content outside the Formula crop."""
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            grayscale = image.convert("L")
            ink = grayscale.point(lambda value: 255 if value < 245 else 0)
            ink.paste(0, pixel_bbox)
            outside_ink = ink.getbbox()
            ink.close()
            grayscale.close()
    except Exception as exc:
        raise ScannedRegionAssociationError("region_source_decode_failed") from exc
    if outside_ink is not None:
        raise ScannedRegionAssociationError("region_nonformula_content_unsupported")


def _validate_equation_only_page_text(
    document: Any, page_number: int, pdf_bbox: tuple[float, ...]
) -> None:
    """Require every searchable word to lie inside the proven Formula region."""
    try:
        words = document[page_number - 1].get_text("words")
    except Exception as exc:
        raise ScannedRegionAssociationError("region_text_geometry_unavailable") from exc
    x0, y0, x1, y1 = pdf_bbox
    tolerance = 2.0
    for word in words:
        if len(word) < 5 or not str(word[4]).strip():
            continue
        wx0, wy0, wx1, wy1 = (float(value) for value in word[:4])
        if (
            wx0 < x0 - tolerance
            or wy0 < y0 - tolerance
            or wx1 > x1 + tolerance
            or wy1 > y1 + tolerance
        ):
            raise ScannedRegionAssociationError("region_nonformula_text_unsupported")


def _region_resource_binding(page: Any, image_xref: int) -> tuple[str, Any]:
    resources = page.obj.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        raise ScannedRegionAssociationError("region_image_resource_missing")
    matches = []
    for raw_name, xobject in xobjects.items():
        try:
            if (
                str(xobject.get(Name.Subtype, "")) == "/Image"
                and int(xobject.objgen[0]) == image_xref
            ):
                matches.append((str(raw_name), xobject))
        except Exception:
            continue
    if len(matches) != 1:
        raise ScannedRegionAssociationError("region_image_resource_ambiguous")
    return matches[0]


def _validate_opaque_region_image(page: Any, image: Any, locator: Any) -> None:
    """Allow only an opaque, mask-free source with no page transparency state."""
    resources = page.obj.get(Name.Resources)
    if resources is None:
        raise ScannedRegionAssociationError("region_page_resources_missing")
    if resources.get(Name("/ExtGState")) is not None:
        raise ScannedRegionAssociationError("region_transparency_state_unsupported")
    if page.obj.get(Name("/Group")) is not None:
        raise ScannedRegionAssociationError("region_page_group_unsupported")
    if page.obj.get(Name("/Annots")) is not None:
        raise ScannedRegionAssociationError("region_page_annotations_unsupported")
    if any(
        key in image for key in (Name("/SMask"), Name("/Mask"), Name("/OC"))
    ) or bool(image.get(Name("/ImageMask"), False)):
        raise ScannedRegionAssociationError("region_masked_image_unsupported")
    if int(image.get(Name("/Width"), -1)) != int(locator.source_width) or int(
        image.get(Name("/Height"), -1)
    ) != int(locator.source_height):
        raise ScannedRegionAssociationError("region_image_dimensions_changed")


def _region_target_draw(
    page: Any,
    ops: List[Any],
    *,
    image_xref: int,
    resource_name: str,
    expected_transform: tuple[float, ...],
    artifact_original_allowed: bool = False,
) -> tuple[int, int, tuple[float, ...]]:
    """Resolve one canonical top-level original raster draw."""
    target_indices = set(_resolved_image_do_indices(page, ops, image_xref))
    if not target_indices:
        raise ScannedRegionAssociationError("region_direct_draw_missing")
    if _form_xobject_reaches_image(page.obj.get(Name.Resources), image_xref, set()):
        raise ScannedRegionAssociationError("region_form_image_unsupported")

    marked: list[str] = []
    q_depth = 0
    candidates: list[tuple[int, int, tuple[float, ...]]] = []
    for index, op in enumerate(ops):
        operator = str(op.operator)
        if operator in {"BMC", "BDC"}:
            marked.append(str(op.operands[0]) if op.operands else "")
            continue
        if operator == "EMC":
            if not marked:
                raise ScannedRegionAssociationError("region_marked_content_unbalanced")
            marked.pop()
            continue
        if operator == "q":
            q_depth += 1
            continue
        if operator == "Q":
            q_depth -= 1
            if q_depth < 0:
                raise ScannedRegionAssociationError("region_graphics_state_unbalanced")
            continue
        if index not in target_indices:
            continue
        if marked and not (artifact_original_allowed and marked == ["/Artifact"]):
            continue
        if index < 2 or index + 1 >= len(ops):
            raise ScannedRegionAssociationError("region_draw_grammar_unsupported")
        q_op, cm_op, close_op = ops[index - 2], ops[index - 1], ops[index + 1]
        if (
            str(q_op.operator) != "q"
            or str(cm_op.operator) != "cm"
            or str(close_op.operator) != "Q"
            or q_depth != 1
            or str(op.operands[0]) != resource_name
            or len(cm_op.operands) != 6
        ):
            raise ScannedRegionAssociationError("region_draw_grammar_unsupported")
        try:
            matrix = tuple(float(value) for value in cm_op.operands)
        except (TypeError, ValueError) as exc:
            raise ScannedRegionAssociationError("region_transform_invalid") from exc
        if (
            not all(math.isfinite(value) for value in matrix)
            or matrix[0] <= 0
            or matrix[3] <= 0
            or abs(matrix[1]) > _REGION_FLOAT_TOLERANCE
            or abs(matrix[2]) > _REGION_FLOAT_TOLERANCE
            or any(
                abs(left - right) > _REGION_FLOAT_TOLERANCE
                for left, right in zip(matrix, expected_transform)
            )
        ):
            raise ScannedRegionAssociationError("region_transform_changed")
        candidates.append((index, index + 1, matrix))
    if marked:
        raise ScannedRegionAssociationError("region_marked_content_unbalanced")
    if q_depth != 0:
        raise ScannedRegionAssociationError("region_graphics_state_unbalanced")
    if len(candidates) != 1:
        raise ScannedRegionAssociationError("region_original_draw_ambiguous")
    return candidates[0]


def _region_draw_ownership(
    page: Any,
    ops: List[Any],
    image_xref: int,
    *,
    artifact_required: bool = True,
    allowed_unmarked_do_names: frozenset[str] = frozenset(),
) -> tuple[int, list[int]]:
    """Require one Artifact original and Formula-only ownership for every replay."""
    target_indices = set(_resolved_image_do_indices(page, ops, image_xref))
    marked: list[tuple[str, int]] = []
    artifact_originals = 0
    formula_mcids: list[int] = []
    for index, op in enumerate(ops):
        operator = str(op.operator)
        if operator in {"BMC", "BDC"}:
            try:
                tag = str(op.operands[0])
                mcid = int(op.operands[1][Name.MCID]) if operator == "BDC" else -1
            except Exception:
                tag, mcid = "", -1
            marked.append((tag, mcid))
        elif operator == "EMC":
            if not marked:
                raise ScannedRegionAssociationError("region_marked_content_unbalanced")
            marked.pop()
        elif operator in {"gs", "ri", "W*"}:
            raise ScannedRegionAssociationError("region_graphics_state_unsupported")
        elif operator == "W":
            if not artifact_required or not (
                len(marked) == 1 and marked[0][0] == "/Formula"
            ):
                raise ScannedRegionAssociationError("region_existing_clip_unsupported")
        elif index in target_indices:
            if not artifact_required and not marked:
                artifact_originals += 1
                continue
            if artifact_required and marked == [("/Artifact", -1)]:
                artifact_originals += 1
                continue
            if any(tag == "/Artifact" for tag, _ in marked):
                raise ScannedRegionAssociationError("region_formula_inside_artifact")
            if len(marked) != 1 or marked[0][0] != "/Formula" or marked[0][1] < 0:
                raise ScannedRegionAssociationError(
                    "region_draw_has_ambiguous_semantic_owner"
                )
            formula_mcids.append(marked[0][1])
        elif (
            artifact_required
            and operator == "Do"
            and not marked
            and op.operands
            and str(op.operands[0]) in allowed_unmarked_do_names
        ):
            continue
        elif artifact_required and operator in {"Do", "Tj", "TJ", "'", '"'}:
            if marked != [("/Artifact", -1)]:
                raise ScannedRegionAssociationError(
                    "region_nonformula_content_owner_invalid"
                )
    if marked:
        raise ScannedRegionAssociationError("region_marked_content_unbalanced")
    if artifact_originals != 1 or len(formula_mcids) != len(set(formula_mcids)):
        raise ScannedRegionAssociationError("region_draw_ownership_ambiguous")
    return artifact_originals, formula_mcids


def _region_ocr_form_plan(
    pdf: Any,
    page: Any,
    fitz_page: Any,
    ops: List[Any],
    *,
    image_resource_name: str,
    region_bbox: tuple[float, float, float, float],
) -> Optional[_OCRFormPlan]:
    """Recognize the narrow OCRmyPDF invisible-text Form grammar."""
    resources = page.obj.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        return None
    forms = [
        (str(name), value)
        for name, value in xobjects.items()
        if str(value.get(Name.Subtype, "")) == "/Form"
    ]
    if not forms:
        return None
    if len(forms) != 1:
        raise ScannedRegionAssociationError("region_ocr_form_ambiguous")
    resource_name, form = forms[0]
    if Name.StructParents in form or any(
        form.get(Name(key)) is not None for key in ("/OC", "/Group", "/Metadata")
    ):
        raise ScannedRegionAssociationError("region_ocr_form_semantics_unsupported")
    document_bindings, document_draws = _document_xobject_usage(pdf, tuple(form.objgen))
    if document_bindings != 1 or document_draws != 1:
        raise ScannedRegionAssociationError("region_ocr_form_reused")
    form_do = [
        index
        for index, op in enumerate(ops)
        if str(op.operator) == "Do"
        and op.operands
        and str(op.operands[0]) == resource_name
    ]
    if len(form_do) != 1:
        raise ScannedRegionAssociationError("region_ocr_form_draw_ambiguous")
    if any(str(op.operator) not in {"q", "Q", "cm", "Do"} for op in ops):
        raise ScannedRegionAssociationError("region_mixed_content_unsupported")
    for index, op in enumerate(ops):
        if str(op.operator) != "Do" or not op.operands:
            continue
        if str(op.operands[0]) not in {resource_name, image_resource_name}:
            raise ScannedRegionAssociationError("region_mixed_content_unsupported")

    form_resources = form.get(Name.Resources)
    if form_resources is None or any(
        str(key) not in {"/Font", "/ProcSet"} for key in form_resources.keys()
    ):
        raise ScannedRegionAssociationError("region_ocr_form_resources_unsupported")
    try:
        form_ops = list(pikepdf.parse_content_stream(form))
    except Exception as exc:
        raise ScannedRegionAssociationError("region_ocr_form_parse_failed") from exc
    if not form_ops or len(form_ops) > 10_000:
        raise ScannedRegionAssociationError("region_ocr_form_ops_unsupported")
    allowed = {
        "BT",
        "ET",
        "Tf",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Ts",
        "Tr",
        "Tj",
        "TJ",
        "'",
        '"',
        "q",
        "Q",
        "cm",
        "g",
        "G",
        "rg",
        "RG",
    }
    groups: list[tuple[int, int]] = []
    start: Optional[int] = None
    render_mode: Optional[int] = None
    has_show = False
    q_depth = 0
    for index, op in enumerate(form_ops):
        operator = str(op.operator)
        if operator not in allowed:
            raise ScannedRegionAssociationError("region_ocr_form_grammar_unsupported")
        if operator == "q":
            if start is not None:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
            q_depth += 1
        elif operator == "Q":
            if start is not None:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
            q_depth -= 1
            if q_depth < 0:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
        elif operator in {"cm", "g", "G", "rg", "RG"}:
            if start is not None or not all(
                math.isfinite(float(value)) for value in op.operands
            ):
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
        elif operator == "BT":
            if start is not None:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
            start, render_mode, has_show = index, None, False
        elif operator == "ET":
            if start is None or render_mode != 3 or not has_show:
                raise ScannedRegionAssociationError("region_ocr_text_not_invisible")
            groups.append((start, index))
            start = None
        elif start is None:
            raise ScannedRegionAssociationError("region_ocr_form_grammar_unsupported")
        elif operator == "Tr":
            if len(op.operands) != 1:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                )
            try:
                render_mode = int(op.operands[0])
            except (TypeError, ValueError) as exc:
                raise ScannedRegionAssociationError(
                    "region_ocr_form_grammar_unsupported"
                ) from exc
        elif operator in {"Tj", "TJ", "'", '"'}:
            has_show = True
    if start is not None or q_depth != 0 or not groups:
        raise ScannedRegionAssociationError("region_ocr_form_grammar_unsupported")

    try:
        traces = list(fitz_page.get_texttrace())
    except Exception as exc:
        raise ScannedRegionAssociationError(
            "region_ocr_text_geometry_unavailable"
        ) from exc
    trace_groups: list[
        tuple[tuple[float, float, float, float], tuple[float, float]]
    ] = []
    by_seqno: dict[int, list[tuple[float, float, float, float]]] = {}
    order: list[int] = []
    for trace in traces:
        if int(trace.get("type", -1)) != 3:
            raise ScannedRegionAssociationError("region_ocr_text_not_invisible")
        seqno = int(trace.get("seqno", -1))
        bbox = tuple(float(value) for value in trace.get("bbox", ()))
        chars = trace.get("chars")
        if (
            seqno < 0
            or len(bbox) != 4
            or not all(math.isfinite(value) for value in bbox)
            or bbox[0] >= bbox[2]
            or bbox[1] >= bbox[3]
            or not chars
        ):
            raise ScannedRegionAssociationError("region_ocr_text_geometry_unavailable")
        if seqno not in by_seqno:
            by_seqno[seqno] = []
            order.append(seqno)
        by_seqno[seqno].append(bbox)
    for seqno in order:
        boxes = by_seqno[seqno]
        first_trace = next(trace for trace in traces if int(trace["seqno"]) == seqno)
        try:
            origin = tuple(float(value) for value in first_trace["chars"][0][2])
        except Exception as exc:
            raise ScannedRegionAssociationError(
                "region_ocr_text_geometry_unavailable"
            ) from exc
        if len(origin) != 2 or not all(math.isfinite(value) for value in origin):
            raise ScannedRegionAssociationError("region_ocr_text_geometry_unavailable")
        trace_groups.append(
            (
                (
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ),
                origin,
            )
        )
    if len(trace_groups) != len(groups):
        raise ScannedRegionAssociationError("region_ocr_text_group_mismatch")

    rx0, ry0, rx1, ry1 = region_bbox
    planned: list[_OCRTextGroup] = []
    inside_indices: list[int] = []
    for index, ((start_index, end_index), trace_group) in enumerate(
        zip(groups, trace_groups)
    ):
        bbox, origin = trace_group
        ox, oy = origin
        if (
            abs(ox - rx0) <= 2.0
            or abs(ox - rx1) <= 2.0
            or abs(oy - ry0) <= 2.0
            or abs(oy - ry1) <= 2.0
        ):
            raise ScannedRegionAssociationError("region_ocr_text_origin_ambiguous")
        contained = rx0 < ox < rx1 and ry0 < oy < ry1
        bx0, by0, bx1, by1 = bbox
        intersects = bx1 > rx0 and rx1 > bx0 and by1 > ry0 and ry1 > by0
        if not contained and intersects:
            raise ScannedRegionAssociationError("region_ocr_text_partial_overlap")
        owner = "/Artifact" if contained else "/P"
        if contained:
            inside_indices.append(index)
        planned.append(_OCRTextGroup(start_index, end_index, owner, bbox))
    if not inside_indices or inside_indices != list(
        range(inside_indices[0], inside_indices[-1] + 1)
    ):
        raise ScannedRegionAssociationError("region_ocr_equation_group_ambiguous")
    return _OCRFormPlan(resource_name, form, tuple(planned))


def _region_structure_parent(pdf: Any) -> Any:
    struct_root = pdf.Root[Name.StructTreeRoot]
    kids = struct_root.get(Name.K)
    candidates = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    if (
        len(candidates) == 1
        and hasattr(candidates[0], "keys")
        and str(candidates[0].get(Name.S, "")) == "/Document"
    ):
        return candidates[0]
    return struct_root


def _xobject_graph_bindings(
    resources: Any,
    target_objgen: tuple[int, int],
    active: frozenset[tuple[int, int]],
) -> int:
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        return 0
    bindings = 0
    for value in xobjects.values():
        identity = tuple(value.objgen)
        if identity == target_objgen:
            bindings += 1
        if str(value.get(Name.Subtype, "")) != "/Form":
            continue
        if identity in active:
            raise ScannedRegionAssociationError("region_ocr_form_cycle")
        bindings += _xobject_graph_bindings(
            value.get(Name.Resources), target_objgen, active | {identity}
        )
    return bindings


def _xobject_graph_draws(
    container: Any,
    resources: Any,
    target_objgen: tuple[int, int],
    active: frozenset[tuple[int, int]],
) -> int:
    xobjects = resources.get(Name.XObject) if resources is not None else None
    if xobjects is None:
        return 0
    try:
        ops = list(pikepdf.parse_content_stream(container))
    except Exception as exc:
        raise ScannedRegionAssociationError("region_ocr_form_parse_failed") from exc
    draws = 0
    for op in ops:
        if str(op.operator) != "Do" or not op.operands:
            continue
        raw_name = str(op.operands[0])
        resource_name = raw_name if raw_name.startswith("/") else f"/{raw_name}"
        value = xobjects.get(Name(resource_name))
        if value is None:
            raise ScannedRegionAssociationError("region_xobject_resource_missing")
        identity = tuple(value.objgen)
        if identity == target_objgen:
            draws += 1
            continue
        if str(value.get(Name.Subtype, "")) != "/Form":
            continue
        if identity in active:
            raise ScannedRegionAssociationError("region_ocr_form_cycle")
        draws += _xobject_graph_draws(
            value,
            value.get(Name.Resources),
            target_objgen,
            active | {identity},
        )
    return draws


def _document_xobject_usage(
    pdf: Any, target_objgen: tuple[int, int]
) -> tuple[int, int]:
    bindings = 0
    draws = 0
    for page in pdf.pages:
        resources = page.obj.get(Name.Resources)
        bindings += _xobject_graph_bindings(resources, target_objgen, frozenset())
        draws += _xobject_graph_draws(
            page,
            resources,
            target_objgen,
            frozenset(),
        )
    return bindings, draws


def _region_ocr_font_signature(form: Any) -> str:
    resources = form.get(Name.Resources)
    fonts = resources.get(Name("/Font")) if resources is not None else None
    if fonts is None:
        raise ScannedRegionAssociationError("region_ocr_fonts_missing")
    parts: list[bytes] = []
    for raw_name, font in sorted(fonts.items(), key=lambda item: str(item[0])):
        to_unicode = font.get(Name("/ToUnicode"))
        unicode_bytes = to_unicode.read_bytes() if to_unicode is not None else b""
        parts.append(
            "|".join(
                (
                    str(raw_name),
                    repr(tuple(font.objgen)),
                    str(font.get(Name.Subtype, "")),
                    str(font.get(Name("/BaseFont"), "")),
                    str(font.get(Name("/Encoding"), "")),
                )
            ).encode("utf-8")
            + b"|"
            + hashlib.sha256(unicode_bytes).hexdigest().encode("ascii")
        )
    return hashlib.sha256(b"\n".join(parts)).hexdigest()


def _region_ocr_payload_signature(ops: List[Any]) -> str:
    payload_ops = [op for op in ops if str(op.operator) not in {"BMC", "BDC", "EMC"}]
    return hashlib.sha256(pikepdf.unparse_content_stream(payload_ops)).hexdigest()


def _structure_element_direct_page(pdf: Any, element: Any) -> Optional[int]:
    if not hasattr(element, "keys"):
        return None
    page_ref = element.get(Name.Pg)
    if page_ref is None:
        return None
    for page_number, page in enumerate(pdf.pages, start=1):
        if tuple(page.obj.objgen) == tuple(page_ref.objgen):
            return page_number
    raise ScannedRegionAssociationError("region_structure_page_missing")


def _structure_element_page_range(
    pdf: Any,
    element: Any,
    seen: frozenset[tuple[int, int]] = frozenset(),
    inherited_page: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    if not hasattr(element, "keys"):
        return (inherited_page, inherited_page) if inherited_page is not None else None
    identity = tuple(getattr(element, "objgen", (0, 0)))
    if identity != (0, 0):
        if identity in seen:
            raise ScannedRegionAssociationError("region_structure_cycle")
        seen = seen | {identity}
    own_page = _structure_element_direct_page(pdf, element)
    pages: list[int] = [own_page] if own_page is not None else []
    default_page = own_page if own_page is not None else inherited_page
    kids = element.get(Name.K)
    children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    for child in children:
        child_range = _structure_element_page_range(
            pdf,
            child,
            seen,
            default_page,
        )
        if child_range is not None:
            pages.extend(child_range)
    return (min(pages), max(pages)) if pages else None


def _verify_region_global_reading_order(
    pdf: Any,
    formula: Any,
    page_number: int,
) -> None:
    parent = formula.get(Name.P)
    siblings_value = parent.get(Name.K) if hasattr(parent, "keys") else None
    siblings = (
        list(siblings_value)
        if isinstance(siblings_value, Array)
        else ([siblings_value] if siblings_value is not None else [])
    )
    formula_id = tuple(formula.objgen)
    formula_indices = [
        index
        for index, sibling in enumerate(siblings)
        if hasattr(sibling, "objgen") and tuple(sibling.objgen) == formula_id
    ]
    if len(formula_indices) != 1:
        raise ValueError("saved_region_global_reading_order_mismatch")
    formula_index = formula_indices[0]
    inherited_page = _structure_element_direct_page(pdf, parent)
    for index, sibling in enumerate(siblings):
        if index == formula_index:
            continue
        sibling_range = _structure_element_page_range(
            pdf,
            sibling,
            inherited_page=inherited_page,
        )
        if sibling_range is None:
            raise ValueError("saved_region_global_reading_order_mismatch")
        first_page, last_page = sibling_range
        if first_page <= page_number <= last_page and first_page != last_page:
            raise ValueError("saved_region_global_reading_order_mismatch")
        if index < formula_index and last_page > page_number:
            raise ValueError("saved_region_global_reading_order_mismatch")
        if index > formula_index and first_page < page_number:
            raise ValueError("saved_region_global_reading_order_mismatch")


def _append_region_structure_sequence(
    pdf: Any, elements: list[Any], page_number: int
) -> None:
    parent = _region_structure_parent(pdf)
    for element in elements:
        element[Name.P] = parent
    kids = parent.get(Name.K)
    current = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    inherited_page = _structure_element_direct_page(pdf, parent)
    insertion_index = 0
    for index, sibling in enumerate(current):
        sibling_range = _structure_element_page_range(
            pdf,
            sibling,
            inherited_page=inherited_page,
        )
        if sibling_range is None:
            raise ScannedRegionAssociationError("region_structure_order_ambiguous")
        first_page, last_page = sibling_range
        if first_page <= page_number <= last_page and first_page != last_page:
            raise ScannedRegionAssociationError("region_structure_order_ambiguous")
        if first_page > page_number:
            insertion_index = index
            break
        insertion_index = index + 1
    parent[Name.K] = Array(
        [*current[:insertion_index], *elements, *current[insertion_index:]]
    )


def _validate_region_source(document: Any, pending: Any) -> bytes:
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.remediation.equation_image_source import (
        EquationRegionSource,
        ImageSourceRejected,
    )

    locator = pending.locator
    occurrence = pending.working_occurrence
    page_number = int(occurrence.page_number)
    if page_number != int(locator.page_number) or not 1 <= page_number <= len(document):
        raise ScannedRegionAssociationError("region_page_changed")
    try:
        infos = list(document[page_number - 1].get_image_info(xrefs=True))
        info = infos[int(occurrence.image_index)]
        bbox = tuple(float(value) for value in info["bbox"])
        transform = tuple(float(value) for value in info["transform"])
        xref = int(info.get("xref") or 0)
    except Exception as exc:
        raise ScannedRegionAssociationError("region_occurrence_changed") from exc
    exact_occurrences = [
        current
        for current in _displayed_image_occurrences(
            document[page_number - 1], page_number
        )
        if current["image_xref"] == occurrence.image_xref
        and current["image_index"] == occurrence.image_index
        and current["occurrence_ordinal"] == occurrence.occurrence_ordinal
        and current["occurrence_id"] == occurrence.occurrence_id
        and all(
            abs(left - right) <= _REGION_FLOAT_TOLERANCE
            for left, right in zip(current["bbox"], occurrence.bbox)
        )
    ]
    if (
        len(exact_occurrences) != 1
        or xref != int(occurrence.image_xref)
        or len(bbox) != 4
        or len(transform) != 6
        or any(
            abs(left - right) > _REGION_FLOAT_TOLERANCE
            for left, right in zip(bbox, occurrence.bbox)
        )
        or any(
            abs(left - right) > _REGION_FLOAT_TOLERANCE
            for left, right in zip(transform, occurrence.transform)
        )
    ):
        raise ScannedRegionAssociationError("region_occurrence_changed")
    source = document.extract_image(xref).get("image")
    if (
        not isinstance(source, bytes)
        or hashlib.sha256(source).hexdigest() != locator.source_sha256
    ):
        raise ScannedRegionAssociationError("region_source_changed")
    width, height, crop_digest = _region_crop_pixel_sha256(
        source, tuple(locator.pixel_bbox)
    )
    try:
        validated = EquationRegionSource().extract(
            document, locator.model_dump(mode="json")
        )
    except ImageSourceRejected as exc:
        raise ScannedRegionAssociationError("region_crop_changed") from exc
    evidence = pending.verification_evidence
    if (
        width != locator.source_width
        or height != locator.source_height
        or crop_digest != locator.crop_pixel_sha256
        or evidence.passed is not True
        or pending.normalized_crop_sha256 != validated.normalized_sha256
        or evidence.source_sha256 != validated.normalized_sha256
    ):
        raise ScannedRegionAssociationError("region_crop_changed")
    return source


def associate_scanned_region_formula(
    pdf: Any, fitz_doc: Any, pending: Any
) -> ScannedRegionAssociationResult:
    """Artifact one scan and add one exact clipped Formula replay.

    The caller must supply a disposable transaction clone. All semantic and
    geometry checks run before mutation, while persistence and post-save
    verification remain the caller's commit boundary.
    """
    locator = pending.locator
    occurrence = pending.working_occurrence
    page_number = int(occurrence.page_number)
    alt_text = str(pending.alt_text)
    mathml = str(pending.mathml_string)
    if (
        not alt_text
        or len(alt_text) > 1024
        or not alt_text.isprintable()
        or not mathml
        or len(mathml.encode("utf-8")) > 65536
    ):
        raise ScannedRegionAssociationError("region_association_request_invalid")
    mathml_sha256 = hashlib.sha256(mathml.encode("utf-8")).hexdigest()
    if mathml_sha256 != pending.verification_evidence.mathml_sha256:
        raise ScannedRegionAssociationError("region_mathml_evidence_mismatch")

    source = _validate_region_source(fitz_doc, pending)
    before_render = tuple(
        _page_render_signature(fitz_doc, page_number, dpi) for dpi in _REGION_RENDER_DPI
    )
    page = pdf.pages[page_number - 1]
    resource_name, image = _region_resource_binding(page, int(occurrence.image_xref))
    _validate_opaque_region_image(page, image, locator)
    ops = list(pikepdf.parse_content_stream(page))
    if any(str(op.operator) in {"BMC", "BDC", "EMC"} for op in ops):
        raise ScannedRegionAssociationError("region_existing_semantics_unsupported")
    target_index, target_close_index, matrix = _region_target_draw(
        page,
        ops,
        image_xref=int(occurrence.image_xref),
        resource_name=resource_name,
        expected_transform=tuple(occurrence.transform),
    )
    _region_draw_ownership(
        page,
        ops,
        int(occurrence.image_xref),
        artifact_required=False,
    )
    ocr_plan = _region_ocr_form_plan(
        pdf,
        page,
        fitz_doc[page_number - 1],
        ops,
        image_resource_name=resource_name,
        region_bbox=tuple(float(value) for value in locator.pdf_bbox),
    )
    if ocr_plan is None:
        _validate_equation_only_region_source(source, tuple(locator.pixel_bbox))
        _validate_equation_only_page_text(
            fitz_doc,
            page_number,
            tuple(float(value) for value in locator.pdf_bbox),
        )
    ocr_payload_sha256 = ""
    ocr_font_sha256 = ""
    page_text_sha256 = ""
    if ocr_plan is not None:
        ocr_payload_sha256 = _region_ocr_payload_signature(
            list(pikepdf.parse_content_stream(ocr_plan.form))
        )
        ocr_font_sha256 = _region_ocr_font_signature(ocr_plan.form)
        page_text_sha256 = hashlib.sha256(
            fitz_doc[page_number - 1].get_text("text").encode("utf-8")
        ).hexdigest()

    if Name.StructTreeRoot not in pdf.Root:
        raise ScannedRegionAssociationError("region_structure_tree_missing")
    struct_root = pdf.Root[Name.StructTreeRoot]
    if struct_root.get(Name.ParentTree) is None:
        raise ScannedRegionAssociationError("region_parent_tree_missing")
    parent_tree, entries = _number_tree_entries(struct_root)
    existing_struct_parent = page.obj.get(Name.StructParents)
    if existing_struct_parent is None:
        used_keys = {key for key, _ in entries}
        struct_parent = 0
        while struct_parent in used_keys:
            struct_parent += 1
    else:
        struct_parent = int(existing_struct_parent)
    page_entry = next((value for key, value in entries if key == struct_parent), None)
    if page_entry is not None and not isinstance(page_entry, Array):
        raise ScannedRegionAssociationError("region_parent_tree_collision")
    page_array = page_entry if page_entry is not None else Array([])
    used_mcids: set[int] = set()
    for op in ops:
        if str(op.operator) == "BDC" and len(op.operands) == 2:
            try:
                used_mcids.add(int(op.operands[1][Name.MCID]))
            except Exception:
                continue
    mcid = max(used_mcids | {len(page_array) - 1}, default=-1) + 1
    if mcid < len(page_array) and page_array[mcid] is not None:
        raise ScannedRegionAssociationError("region_mcid_collision")

    px0, py0, px1, py1 = locator.pixel_bbox
    width = float(locator.source_width)
    height = float(locator.source_height)
    clip = (
        px0 / width,
        1.0 - (py1 / height),
        (px1 - px0) / width,
        (py1 - py0) / height,
    )
    formula_bbox = (
        matrix[4] + matrix[0] * clip[0],
        matrix[5] + matrix[3] * clip[1],
        matrix[4] + matrix[0] * (clip[0] + clip[2]),
        matrix[5] + matrix[3] * (clip[1] + clip[3]),
    )

    from src.education.remediation.pdf_structure import PDFStructureTree

    formula = PDFStructureTree(pdf).create_formula_element(
        page_num=page_number,
        alt_text=alt_text,
        mathml_string=mathml,
        bbox=formula_bbox,
        mcid=mcid,
    )
    ocr_struct_parent = -1
    ocr_group_owners: list[tuple[str, int]] = []
    ocr_before_mcids: list[int] = []
    ocr_after_mcids: list[int] = []
    structure_sequence: list[Any] = []
    form_array: Optional[Any] = None
    if ocr_plan is not None:
        used_keys = {key for key, _ in entries} | {struct_parent}
        ocr_struct_parent = 0
        while ocr_struct_parent in used_keys:
            ocr_struct_parent += 1
        ocr_plan.form[Name.StructParents] = ocr_struct_parent
        form_array = Array([])
        first_equation = next(
            index
            for index, group in enumerate(ocr_plan.groups)
            if group.owner == "/Artifact"
        )
        inserted_formula = False
        next_form_mcid = 0
        parent = _region_structure_parent(pdf)
        for group_index, group in enumerate(ocr_plan.groups):
            if group.owner == "/Artifact":
                ocr_group_owners.append(("/Artifact", -1))
                if not inserted_formula:
                    structure_sequence.append(formula)
                    inserted_formula = True
                continue
            group_mcid = next_form_mcid
            next_form_mcid += 1
            mcr = Dictionary(
                {
                    "/Type": Name("/MCR"),
                    "/Pg": page.obj,
                    "/Stm": ocr_plan.form,
                    "/MCID": group_mcid,
                }
            )
            paragraph = pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/P"),
                        "/P": parent,
                        "/Pg": page.obj,
                        "/K": mcr,
                    }
                )
            )
            while len(form_array) <= group_mcid:
                form_array.append(None)
            form_array[group_mcid] = paragraph
            structure_sequence.append(paragraph)
            ocr_group_owners.append(("/P", group_mcid))
            if group_index < first_equation:
                ocr_before_mcids.append(group_mcid)
            else:
                ocr_after_mcids.append(group_mcid)
        if not inserted_formula:
            raise ScannedRegionAssociationError("region_ocr_equation_group_missing")
        _set_number_tree_value(
            parent_tree,
            ocr_struct_parent,
            pdf.make_indirect(form_array),
        )
    else:
        structure_sequence.append(formula)
    _append_region_structure_sequence(pdf, structure_sequence, page_number)
    if existing_struct_parent is None:
        page.obj[Name.StructParents] = struct_parent
    while len(page_array) <= mcid:
        page_array.append(None)
    page_array[mcid] = formula
    if page_entry is None:
        page_array = pdf.make_indirect(page_array)
    _set_number_tree_value(parent_tree, struct_parent, page_array)

    duplicate = [
        pikepdf.ContentStreamInstruction(
            [Name("/Formula"), Dictionary({"/MCID": mcid})], Operator("BDC")
        ),
        pikepdf.ContentStreamInstruction([], Operator("q")),
        pikepdf.ContentStreamInstruction(list(matrix), Operator("cm")),
        pikepdf.ContentStreamInstruction(list(clip), Operator("re")),
        pikepdf.ContentStreamInstruction([], Operator("W")),
        pikepdf.ContentStreamInstruction([], Operator("n")),
        pikepdf.ContentStreamInstruction([Name(resource_name)], Operator("Do")),
        pikepdf.ContentStreamInstruction([], Operator("Q")),
        pikepdf.ContentStreamInstruction([], Operator("EMC")),
    ]
    if ocr_plan is not None:
        form_ops = list(pikepdf.parse_content_stream(ocr_plan.form))
        for group, owner in reversed(list(zip(ocr_plan.groups, ocr_group_owners))):
            if owner[0] == "/Artifact":
                opening = pikepdf.ContentStreamInstruction(
                    [Name("/Artifact")], Operator("BMC")
                )
            else:
                opening = pikepdf.ContentStreamInstruction(
                    [Name("/P"), Dictionary({"/MCID": owner[1]})],
                    Operator("BDC"),
                )
            form_ops.insert(
                group.end + 1,
                pikepdf.ContentStreamInstruction([], Operator("EMC")),
            )
            form_ops.insert(group.start, opening)
        ocr_plan.form.write(pikepdf.unparse_content_stream(form_ops))

    target_start_index = target_index - 2
    new_ops = [
        *ops[:target_start_index],
        pikepdf.ContentStreamInstruction([Name("/Artifact")], Operator("BMC")),
        *ops[target_start_index : target_close_index + 1],
        pikepdf.ContentStreamInstruction([], Operator("EMC")),
        *duplicate,
        *ops[target_close_index + 1 :],
    ]
    page.obj[Name.Contents] = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
    return ScannedRegionAssociationResult(
        page_number=page_number,
        image_xref=int(occurrence.image_xref),
        resource_name=resource_name,
        struct_parent=struct_parent,
        mcid=mcid,
        mathml_sha256=mathml_sha256,
        formula_bbox=formula_bbox,
        render_signatures=before_render,
        ocr_resource_name=ocr_plan.resource_name if ocr_plan is not None else "",
        ocr_struct_parent=ocr_struct_parent,
        ocr_group_owners=tuple(ocr_group_owners),
        ocr_before_mcids=tuple(ocr_before_mcids),
        ocr_after_mcids=tuple(ocr_after_mcids),
        ocr_payload_sha256=ocr_payload_sha256,
        ocr_font_sha256=ocr_font_sha256,
        page_text_sha256=page_text_sha256,
    )


def _collect_formula_elements(element: Any, found: List[Any]) -> None:
    if not hasattr(element, "keys"):
        return
    if str(element.get(Name.S, "")) == "/Formula":
        found.append(element)
    kids = element.get(Name.K)
    children = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    for child in children:
        if hasattr(child, "keys") and str(child.get(Name.Type, "")) != "/MCR":
            _collect_formula_elements(child, found)


def _verify_region_ocr_form(
    pdf: Any,
    page: Any,
    expected: ScannedRegionAssociationResult,
    formula: Any,
) -> None:
    """Reverse-verify Form-internal ownership and its /Stm ParentTree links."""
    if not expected.ocr_resource_name:
        return
    resources = page.obj.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if resources is not None else None
    form = (
        xobjects.get(Name(expected.ocr_resource_name)) if xobjects is not None else None
    )
    if (
        form is None
        or str(form.get(Name.Subtype, "")) != "/Form"
        or int(form.get(Name.StructParents, -1)) != expected.ocr_struct_parent
    ):
        raise ValueError("saved_region_ocr_form_changed")
    bindings, draws = _document_xobject_usage(pdf, tuple(form.objgen))
    if bindings != 1 or draws != 1:
        raise ValueError("saved_region_ocr_form_reused")
    form_resources = form.get(Name.Resources)
    if form_resources is None or any(
        str(key) not in {"/Font", "/ProcSet"} for key in form_resources.keys()
    ):
        raise ValueError("saved_region_ocr_resources_changed")
    ops = list(pikepdf.parse_content_stream(form))
    if (
        _region_ocr_payload_signature(ops) != expected.ocr_payload_sha256
        or _region_ocr_font_signature(form) != expected.ocr_font_sha256
    ):
        raise ValueError("saved_region_ocr_payload_changed")
    stack: list[tuple[str, int]] = []
    group_owner: Optional[tuple[str, int]] = None
    owners: list[tuple[str, int]] = []
    render_mode: Optional[int] = None
    allowed = {
        "BMC",
        "BDC",
        "EMC",
        "BT",
        "ET",
        "Tf",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Ts",
        "Tr",
        "Tj",
        "TJ",
        "'",
        '"',
        "q",
        "Q",
        "cm",
        "g",
        "G",
        "rg",
        "RG",
    }
    for op in ops:
        operator = str(op.operator)
        if operator not in allowed:
            raise ValueError("saved_region_ocr_grammar_changed")
        if operator in {"BMC", "BDC"}:
            tag = str(op.operands[0]) if op.operands else ""
            mcid = -1
            if operator == "BDC":
                try:
                    mcid = int(op.operands[1][Name.MCID])
                except Exception as exc:
                    raise ValueError("saved_region_ocr_mcid_invalid") from exc
            stack.append((tag, mcid))
        elif operator == "EMC":
            if not stack:
                raise ValueError("saved_region_ocr_marked_content_unbalanced")
            stack.pop()
        elif operator == "BT":
            if group_owner is not None or len(stack) != 1:
                raise ValueError("saved_region_ocr_owner_ambiguous")
            group_owner = stack[0]
            render_mode = None
        elif operator == "Tr":
            try:
                render_mode = int(op.operands[0])
            except Exception as exc:
                raise ValueError("saved_region_ocr_render_mode_invalid") from exc
        elif operator == "ET":
            if (
                group_owner is None
                or len(stack) != 1
                or stack[0] != group_owner
                or render_mode != 3
            ):
                raise ValueError("saved_region_ocr_owner_ambiguous")
            owners.append(group_owner)
            group_owner = None
    if stack or group_owner is not None or tuple(owners) != expected.ocr_group_owners:
        raise ValueError("saved_region_ocr_owners_changed")

    _, entries = _number_tree_entries(pdf.Root[Name.StructTreeRoot])
    form_array = next(
        (value for key, value in entries if key == expected.ocr_struct_parent), None
    )
    p_by_mcid: dict[int, Any] = {}
    for owner in expected.ocr_group_owners:
        if owner[0] != "/P":
            continue
        group_mcid = owner[1]
        if (
            not isinstance(form_array, Array)
            or group_mcid >= len(form_array)
            or not hasattr(form_array[group_mcid], "keys")
        ):
            raise ValueError("saved_region_ocr_parent_tree_mismatch")
        paragraph = form_array[group_mcid]
        mcr = paragraph.get(Name.K)
        if (
            str(paragraph.get(Name.S, "")) != "/P"
            or not hasattr(mcr, "keys")
            or str(mcr.get(Name.Type, "")) != "/MCR"
            or int(mcr.get(Name.MCID, -1)) != group_mcid
            or tuple(mcr.get(Name.Pg).objgen) != tuple(page.obj.objgen)
            or tuple(mcr.get(Name("/Stm")).objgen) != tuple(form.objgen)
        ):
            raise ValueError("saved_region_ocr_mcr_mismatch")
        p_by_mcid[group_mcid] = paragraph

    parent = formula.get(Name.P)
    siblings_value = parent.get(Name.K) if hasattr(parent, "keys") else None
    siblings = (
        list(siblings_value)
        if isinstance(siblings_value, Array)
        else ([siblings_value] if siblings_value is not None else [])
    )
    wanted = [p_by_mcid[value] for value in expected.ocr_before_mcids]
    wanted.append(formula)
    wanted.extend(p_by_mcid[value] for value in expected.ocr_after_mcids)
    wanted_ids = [tuple(value.objgen) for value in wanted]
    sibling_ids = [
        tuple(value.objgen) if hasattr(value, "objgen") else None for value in siblings
    ]
    match_indices = [
        index
        for index in range(0, len(sibling_ids) - len(wanted_ids) + 1)
        if sibling_ids[index : index + len(wanted_ids)] == wanted_ids
    ]
    if len(match_indices) != 1:
        raise ValueError("saved_region_ocr_reading_order_mismatch")


def verify_image_formula_association(
    path: str | Path, pending: Any, expected: FormulaAssociationResult
) -> bool:
    """Reopen and reverse-verify Formula→MCR→marked Do and ParentTree."""
    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    if not expected.success:
        return False
    try:
        with fitz.open(str(path)) as fitz_doc, pikepdf.open(str(path)) as pdf:
            occurrences = _displayed_image_occurrences(
                fitz_doc[pending.page_number - 1], pending.page_number
            )
            exact = [
                item
                for item in occurrences
                if item["page_number"] == pending.page_number
                and item["image_index"] == pending.image_index
                and item["occurrence_ordinal"] == pending.occurrence_ordinal
                and all(
                    abs(left - right) <= 1e-6
                    for left, right in zip(item["bbox"], pending.bbox)
                )
            ]
            if len(exact) != 1:
                raise ValueError("saved_occurrence_identity_mismatch")
            saved_image_xref = exact[0]["image_xref"]
            saved_image = fitz_doc.extract_image(saved_image_xref).get("image")
            if (
                not isinstance(saved_image, bytes)
                or hashlib.sha256(saved_image).hexdigest()
                != pending.image_stream_sha256
            ):
                raise ValueError("saved_image_stream_identity_mismatch")
            formulas: List[Any] = []
            root_kids = pdf.Root[Name.StructTreeRoot].get(Name.K)
            roots = (
                list(root_kids)
                if isinstance(root_kids, Array)
                else ([root_kids] if root_kids else [])
            )
            for root in roots:
                _collect_formula_elements(root, formulas)
            semantic_formulas = []
            for formula in formulas:
                mcr = formula.get(Name.K)
                if (
                    hasattr(mcr, "keys")
                    and str(mcr.get(Name.Type, "")) == "/MCR"
                    and int(mcr.get(Name.MCID, -1)) == expected.mcid
                    and str(formula.get(Name.Alt, "")) == pending.alt_text
                    and tuple(formula.get(Name.Pg).objgen)
                    == tuple(pdf.pages[pending.page_number - 1].obj.objgen)
                ):
                    semantic_formulas.append(formula)
            if len(semantic_formulas) != 1:
                raise ValueError("saved_formula_not_unique")
            formula = semantic_formulas[0]
            mcr = formula.get(Name.K)
            parent = formula.get(Name.P)
            parent_kids = parent.get(Name.K) if hasattr(parent, "keys") else None
            siblings = (
                list(parent_kids)
                if isinstance(parent_kids, Array)
                else ([parent_kids] if parent_kids is not None else [])
            )
            backlink_count = sum(
                1
                for sibling in siblings
                if hasattr(sibling, "objgen")
                and tuple(sibling.objgen) == tuple(formula.objgen)
            )
            attributes = formula.get(Name.A)
            saved_bbox = (
                attributes.get(Name("/BBox")) if hasattr(attributes, "keys") else None
            )
            if (
                not hasattr(mcr, "keys")
                or str(mcr.get(Name.Type, "")) != "/MCR"
                or int(mcr.get(Name.MCID, -1)) != expected.mcid
                or tuple(mcr.get(Name.Pg).objgen)
                != tuple(pdf.pages[pending.page_number - 1].obj.objgen)
                or str(formula.get(Name.Alt, "")) != pending.alt_text
                or backlink_count != 1
                or not isinstance(saved_bbox, Array)
                or len(saved_bbox) != 4
                or any(
                    abs(float(saved) - float(wanted)) > 1e-6
                    for saved, wanted in zip(saved_bbox, pending.bbox)
                )
            ):
                raise ValueError("saved_mcr_or_formula_contract_mismatch")
            af = formula.get(Name("/AF"))
            if not isinstance(af, Array) or len(af) != 1:
                raise ValueError("saved_af_mismatch")
            filespec = af[0]
            if (
                str(filespec.get(Name.Type, "")) != "/Filespec"
                or str(filespec.get(Name("/AFRelationship"), "")) != "/Supplement"
            ):
                raise ValueError("saved_filespec_mismatch")
            ef = filespec.get(Name("/EF"))
            embedded = ef.get(Name.F) if hasattr(ef, "keys") else None
            if (
                embedded is None
                or str(embedded.get(Name.Type, "")) != "/EmbeddedFile"
                or str(embedded.get(Name.Subtype, "")) != "/application#2Fmathml+xml"
            ):
                raise ValueError("saved_embedded_file_contract_mismatch")
            embedded_bytes = embedded.read_bytes()
            params = embedded.get(Name("/Params"))
            checksum = (
                params.get(Name("/CheckSum")) if hasattr(params, "keys") else None
            )
            if (
                not hasattr(params, "keys")
                or int(params.get(Name("/Size"), -1)) != len(embedded_bytes)
                or len(embedded_bytes) > 65536
                or checksum is None
                or bytes(checksum)
                != hashlib.md5(embedded_bytes, usedforsecurity=False).digest()
                or hashlib.sha256(embedded_bytes).hexdigest() != expected.mathml_sha256
            ):
                raise ValueError("saved_mathml_mismatch")
            parent_tree, entries = _number_tree_entries(pdf.Root[Name.StructTreeRoot])
            del parent_tree
            page_array = next(
                (value for key, value in entries if key == expected.struct_parent), None
            )
            if (
                not isinstance(page_array, Array)
                or expected.mcid >= len(page_array)
                or tuple(page_array[expected.mcid].objgen) != tuple(formula.objgen)
            ):
                raise ValueError("saved_parent_tree_mismatch")
            page = pdf.pages[pending.page_number - 1]
            if int(page.obj.get(Name.StructParents, -1)) != expected.struct_parent:
                raise ValueError("saved_struct_parents_mismatch")
            ops = list(pikepdf.parse_content_stream(page))
            target_indices = _resolved_image_do_indices(page, ops, saved_image_xref)
            draw_ordinal = 0
            stack: List[tuple[str, int]] = []
            matched_target = 0
            formula_marked_draws = 0
            for index, op in enumerate(ops):
                operator = str(op.operator)
                if operator in {"BMC", "BDC"}:
                    try:
                        tag = str(op.operands[0])
                        mcid_value = (
                            int(op.operands[1][Name.MCID]) if operator == "BDC" else -1
                        )
                        stack.append((tag, mcid_value))
                    except Exception:
                        stack.append(("", -1))
                elif operator == "EMC":
                    if not stack:
                        raise ValueError("saved_marked_content_unbalanced")
                    stack.pop()
                elif operator == "Do":
                    formula_owner = ("/Formula", expected.mcid)
                    has_formula = stack.count(formula_owner) == 1
                    semantic_owners = [
                        owner for owner in stack if owner[0] != "/Artifact"
                    ]
                    if has_formula and semantic_owners != [formula_owner]:
                        raise ValueError("saved_draw_has_additional_semantic_owner")
                    formula_marked_draws += int(has_formula)
                    if index in target_indices:
                        if draw_ordinal == pending.occurrence_ordinal:
                            matched_target += int(has_formula)
                        elif has_formula:
                            raise ValueError("saved_formula_marks_wrong_draw")
                        draw_ordinal += 1
            return matched_target == 1 and formula_marked_draws == 1 and not stack
    except Exception as exc:
        logger.warning("Post-save Formula association verification failed: %s", exc)
        return False


def verify_scanned_region_formula_association(
    path: str | Path,
    pending: Any,
    expected: ScannedRegionAssociationResult,
    *,
    allowed_region_mcids: Optional[set[int]] = None,
) -> bool:
    """Reopen and reverse-verify provenance, structure, clip, and pixel parity."""
    try:
        with fitz.open(str(path)) as fitz_doc, pikepdf.open(str(path)) as pdf:
            page_number = int(pending.page_number)
            page = pdf.pages[page_number - 1]
            locator = pending.locator

            infos = list(fitz_doc[page_number - 1].get_image_info(xrefs=True))
            source_matches = []
            for image_index, info in enumerate(infos):
                try:
                    xref = int(info.get("xref") or 0)
                    if xref <= 0:
                        continue
                    source = fitz_doc.extract_image(xref).get("image")
                    if (
                        isinstance(source, bytes)
                        and hashlib.sha256(source).hexdigest() == locator.source_sha256
                        and image_index == pending.image_index
                    ):
                        source_matches.append((xref, source, info))
                except Exception:
                    continue
            if len(source_matches) != 1:
                raise ValueError("saved_region_source_ambiguous")
            saved_xref, source, info = source_matches[0]
            width, height, crop_digest = _region_crop_pixel_sha256(
                source, tuple(locator.pixel_bbox)
            )
            if (
                width != locator.source_width
                or height != locator.source_height
                or crop_digest != locator.crop_pixel_sha256
                or any(
                    abs(float(left) - float(right)) > _REGION_FLOAT_TOLERANCE
                    for left, right in zip(info["bbox"], pending.working_parent_bbox)
                )
                or any(
                    abs(float(left) - float(right)) > _REGION_FLOAT_TOLERANCE
                    for left, right in zip(
                        info["transform"], pending.working_occurrence.transform
                    )
                )
            ):
                raise ValueError("saved_region_source_changed")

            formulas: List[Any] = []
            root_kids = pdf.Root[Name.StructTreeRoot].get(Name.K)
            roots = (
                list(root_kids)
                if isinstance(root_kids, Array)
                else ([root_kids] if root_kids else [])
            )
            for root in roots:
                _collect_formula_elements(root, formulas)
            semantic_formulas = []
            for formula in formulas:
                mcr = formula.get(Name.K)
                if (
                    hasattr(mcr, "keys")
                    and str(mcr.get(Name.Type, "")) == "/MCR"
                    and int(mcr.get(Name.MCID, -1)) == expected.mcid
                    and str(formula.get(Name.Alt, "")) == pending.alt_text
                    and tuple(formula.get(Name.Pg).objgen) == tuple(page.obj.objgen)
                ):
                    semantic_formulas.append(formula)
            if len(semantic_formulas) != 1:
                raise ValueError("saved_region_formula_not_unique")
            formula = semantic_formulas[0]
            if (
                expected.ocr_resource_name
                and hashlib.sha256(
                    fitz_doc[page_number - 1].get_text("text").encode("utf-8")
                ).hexdigest()
                != expected.page_text_sha256
            ):
                raise ValueError("saved_region_ocr_text_changed")
            _verify_region_ocr_form(pdf, page, expected, formula)
            mcr = formula.get(Name.K)
            attributes = formula.get(Name.A)
            saved_bbox = (
                attributes.get(Name("/BBox")) if hasattr(attributes, "keys") else None
            )
            parent = formula.get(Name.P)
            parent_kids = parent.get(Name.K) if hasattr(parent, "keys") else None
            siblings = (
                list(parent_kids)
                if isinstance(parent_kids, Array)
                else ([parent_kids] if parent_kids is not None else [])
            )
            backlink_count = sum(
                1
                for sibling in siblings
                if hasattr(sibling, "objgen")
                and tuple(sibling.objgen) == tuple(formula.objgen)
            )
            if (
                not hasattr(mcr, "keys")
                or str(mcr.get(Name.Type, "")) != "/MCR"
                or int(mcr.get(Name.MCID, -1)) != expected.mcid
                or tuple(mcr.get(Name.Pg).objgen) != tuple(page.obj.objgen)
                or backlink_count != 1
                or not isinstance(saved_bbox, Array)
                or len(saved_bbox) != 4
                or any(
                    abs(float(saved) - float(wanted)) > _REGION_FLOAT_TOLERANCE
                    for saved, wanted in zip(saved_bbox, expected.formula_bbox)
                )
            ):
                raise ValueError("saved_region_formula_contract_mismatch")
            _verify_region_global_reading_order(pdf, formula, page_number)

            af = formula.get(Name("/AF"))
            if not isinstance(af, Array) or len(af) != 1:
                raise ValueError("saved_region_af_mismatch")
            filespec = af[0]
            ef = filespec.get(Name("/EF"))
            embedded = ef.get(Name.F) if hasattr(ef, "keys") else None
            if (
                str(filespec.get(Name.Type, "")) != "/Filespec"
                or str(filespec.get(Name("/AFRelationship"), "")) != "/Supplement"
                or embedded is None
                or str(embedded.get(Name.Type, "")) != "/EmbeddedFile"
                or str(embedded.get(Name.Subtype, "")) != "/application#2Fmathml+xml"
            ):
                raise ValueError("saved_region_embedded_file_mismatch")
            embedded_bytes = embedded.read_bytes()
            params = embedded.get(Name("/Params"))
            checksum = (
                params.get(Name("/CheckSum")) if hasattr(params, "keys") else None
            )
            if (
                not hasattr(params, "keys")
                or int(params.get(Name("/Size"), -1)) != len(embedded_bytes)
                or len(embedded_bytes) > 65536
                or checksum is None
                or bytes(checksum)
                != hashlib.md5(embedded_bytes, usedforsecurity=False).digest()
                or hashlib.sha256(embedded_bytes).hexdigest() != expected.mathml_sha256
            ):
                raise ValueError("saved_region_mathml_mismatch")

            _, entries = _number_tree_entries(pdf.Root[Name.StructTreeRoot])
            page_array = next(
                (value for key, value in entries if key == expected.struct_parent),
                None,
            )
            if (
                not isinstance(page_array, Array)
                or expected.mcid >= len(page_array)
                or tuple(page_array[expected.mcid].objgen) != tuple(formula.objgen)
                or int(page.obj.get(Name.StructParents, -1)) != expected.struct_parent
            ):
                raise ValueError("saved_region_parent_tree_mismatch")

            resource_name, _ = _region_resource_binding(page, saved_xref)
            if resource_name != expected.resource_name:
                raise ValueError("saved_region_resource_changed")
            ops = list(pikepdf.parse_content_stream(page))
            _region_target_draw(
                page,
                ops,
                image_xref=saved_xref,
                resource_name=resource_name,
                expected_transform=tuple(pending.working_occurrence.transform),
                artifact_original_allowed=True,
            )
            allowed_unmarked = (
                frozenset({expected.ocr_resource_name})
                if expected.ocr_resource_name
                else frozenset()
            )
            _, formula_mcids = _region_draw_ownership(
                page,
                ops,
                saved_xref,
                allowed_unmarked_do_names=allowed_unmarked,
            )
            allowed_mcids = allowed_region_mcids or {expected.mcid}
            if set(formula_mcids) != allowed_mcids:
                raise ValueError("saved_region_draw_set_mismatch")
            px0, py0, px1, py1 = locator.pixel_bbox
            wanted_clip = (
                px0 / float(locator.source_width),
                1.0 - (py1 / float(locator.source_height)),
                (px1 - px0) / float(locator.source_width),
                (py1 - py0) / float(locator.source_height),
            )
            matched_sequences = 0
            for index in range(0, len(ops) - 8):
                sequence = ops[index : index + 9]
                if [str(item.operator) for item in sequence] != [
                    "BDC",
                    "q",
                    "cm",
                    "re",
                    "W",
                    "n",
                    "Do",
                    "Q",
                    "EMC",
                ]:
                    continue
                try:
                    if (
                        str(sequence[0].operands[0]) != "/Formula"
                        or int(sequence[0].operands[1][Name.MCID]) != expected.mcid
                        or str(sequence[6].operands[0]) != resource_name
                        or any(
                            abs(float(left) - float(right)) > _REGION_FLOAT_TOLERANCE
                            for left, right in zip(
                                sequence[2].operands,
                                pending.working_occurrence.transform,
                            )
                        )
                        or any(
                            abs(float(left) - float(right)) > _REGION_FLOAT_TOLERANCE
                            for left, right in zip(sequence[3].operands, wanted_clip)
                        )
                    ):
                        continue
                except Exception:
                    continue
                matched_sequences += 1
            if matched_sequences != 1:
                raise ValueError("saved_region_clip_sequence_mismatch")

            after_render = tuple(
                _page_render_signature(fitz_doc, page_number, dpi)
                for dpi in _REGION_RENDER_DPI
            )
            if after_render != expected.render_signatures:
                raise ValueError("saved_region_render_changed")
            return True
    except Exception as exc:
        logger.warning("Post-save scanned-region Formula verification failed: %s", exc)
        return False


__all__ = [
    "ContentTaggerV2",
    "FormulaAssociationResult",
    "MatchedBlock",
    "ScannedRegionAssociationError",
    "ScannedRegionAssociationResult",
    "TABLE_TAGS",
    "associate_image_formula",
    "associate_scanned_region_formula",
    "preflight_scanned_region_render_budget",
    "verify_image_formula_association",
    "verify_scanned_region_formula_association",
]
