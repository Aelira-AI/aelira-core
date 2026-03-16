"""
PDF Content Stream Tagger for PDF/UA compliance.

This module bridges the gap between the structure tree (created by pdf_structure.py)
and the actual page content streams. Without marked content operators (BDC/EMC) wrapping
content blocks, screen readers cannot navigate the document even if a structure tree exists.

Key operations:
1. Parse each page's content stream to find text blocks (BT/ET) and image references (Do)
2. Insert BDC/EMC markers with MCIDs around each content block
3. Link existing structure elements to MCIDs via /K entries
4. Populate the ParentTree with reverse mappings
5. Set /StructParents on each page
6. Wrap existing structure elements under a Document root
7. Set the PDF/UA-1 identifier

Usage:
    with pikepdf.open('input.pdf') as pdf:
        # ... add structure elements via PDFStructureTree ...
        tagger = ContentTagger(pdf)
        tagger.tag_all_pages()
        pdf.save('output.pdf')
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Operator, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Array = Any
    Dictionary = Any
    Name = Any
    Operator = Any
    String = Any

logger = logging.getLogger(__name__)


class BlockType(str, Enum):
    """Type of content block found in a page content stream."""

    TEXT = "text"
    IMAGE = "image"


@dataclass
class ContentBlock:
    """A parsed content block from a page content stream.

    Attributes:
        block_type: Whether this is a text or image block.
        start_index: Index of the first operator in this block
            (within the parsed operator list).
        end_index: Index one past the last operator in this block.
        operators: The raw pikepdf operator instructions for this block.
    """

    block_type: BlockType
    start_index: int
    end_index: int
    operators: list = field(default_factory=list)


def parse_content_blocks(page) -> List[ContentBlock]:
    """Parse a page's content stream and return identified content blocks.

    Finds:
    - Text blocks: BT ... ET operator pairs
    - Image blocks: sequences containing a Do operator (with surrounding q/Q if present)

    Args:
        page: A pikepdf page object (``pdf.pages[n]``).

    Returns:
        List of ``ContentBlock`` objects in stream order.
    """
    try:
        ops = list(pikepdf.parse_content_stream(page))
    except Exception:
        return []

    if not ops:
        return []

    blocks: List[ContentBlock] = []
    i = 0

    while i < len(ops):
        op_name = str(ops[i].operator)

        if op_name == "BT":
            # Find matching ET
            start = i
            j = i + 1
            while j < len(ops) and str(ops[j].operator) != "ET":
                j += 1
            end = j + 1 if j < len(ops) else j
            blocks.append(
                ContentBlock(
                    block_type=BlockType.TEXT,
                    start_index=start,
                    end_index=end,
                    operators=ops[start:end],
                )
            )
            i = end

        elif op_name == "Do":
            # Bare Do operator (image XObject invocation)
            blocks.append(
                ContentBlock(
                    block_type=BlockType.IMAGE,
                    start_index=i,
                    end_index=i + 1,
                    operators=ops[i : i + 1],
                )
            )
            i += 1

        elif op_name == "q":
            # Look ahead for Do inside this q...Q group
            start = i
            j = i + 1
            depth = 1
            has_do = False
            while j < len(ops) and depth > 0:
                inner = str(ops[j].operator)
                if inner == "q":
                    depth += 1
                elif inner == "Q":
                    depth -= 1
                elif inner == "Do":
                    has_do = True
                j += 1

            end = j
            if has_do:
                blocks.append(
                    ContentBlock(
                        block_type=BlockType.IMAGE,
                        start_index=start,
                        end_index=end,
                        operators=ops[start:end],
                    )
                )
                i = end
            else:
                i += 1
        else:
            i += 1

    return blocks


def _extract_text_from_ops(operators: list) -> Optional[str]:
    """Extract readable text from a sequence of content stream operators.

    Handles Tj (single string) and TJ (array of strings/kerning) operators.

    Returns:
        Extracted text or ``None`` if no text operators found.
    """
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
            # ' and " operators also show text
            try:
                parts.append(str(op.operands[-1]))
            except Exception:
                pass

    return "".join(parts) if parts else None


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    # NFC normalization, lowercase, collapse whitespace
    text = unicodedata.normalize("NFC", text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _text_match(block_text: str, elem_text: str) -> bool:
    """Check if block text matches element text using normalized substring matching."""
    if not block_text or not elem_text:
        return False
    nb = _normalize(block_text)
    ne = _normalize(elem_text)
    if not nb or not ne:
        return False
    # Exact match or substring containment in either direction
    return nb == ne or nb in ne or ne in nb


def _same_page(pg_ref, page_obj) -> bool:
    """Check if two pikepdf page references point to the same page."""
    try:
        return pg_ref.same_owner_as(page_obj) and pg_ref.objgen == page_obj.objgen
    except Exception:
        return False


def _is_already_tagged(page) -> bool:
    """Check if a page content stream already contains BDC/EMC markers."""
    try:
        ops = list(pikepdf.parse_content_stream(page))
        return any(str(op.operator) == "BDC" for op in ops)
    except Exception:
        return False


class ContentTagger:
    """Tags PDF content streams with marked content operators for PDF/UA compliance.

    This class connects the structure tree to actual page content by inserting
    BDC (Begin Marked Content) / EMC (End Marked Content) operators around
    content blocks and linking them to structure elements via MCIDs.

    Args:
        pdf: An open pikepdf.Pdf object with a structure tree.
    """

    def __init__(self, pdf: "pikepdf.Pdf"):
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for content tagging. "
                "Install with: pip install pikepdf"
            )
        self.pdf = pdf
        self._ensure_struct_tree()
        # Tracks page_index -> list of (mcid, struct_elem) for ParentTree
        self._parent_tree_map: Dict[int, List] = {}

    def _ensure_struct_tree(self):
        """Make sure StructTreeRoot and MarkInfo exist."""
        if Name.StructTreeRoot not in self.pdf.Root:
            from src.education.remediation.pdf_structure import PDFStructureTree

            PDFStructureTree(self.pdf)

        if Name.MarkInfo not in self.pdf.Root:
            self.pdf.Root[Name.MarkInfo] = Dictionary({})
        self.pdf.Root[Name.MarkInfo][Name.Marked] = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tag_all_pages(self):
        """Tag all pages in the PDF with marked content operators.

        This is the main entry point. It:
        1. Creates/finds a Document root element
        2. Collects existing structure elements
        3. Tags each page's content stream with BDC/EMC + MCIDs
        4. Links structure elements to MCIDs
        5. Builds the ParentTree
        6. Sets the PDF/UA-1 identifier
        """
        struct_root = self.pdf.Root[Name.StructTreeRoot]

        doc_elem = self._ensure_document_root(struct_root)
        existing_elems = self._collect_existing_elements(doc_elem)

        for page_idx in range(len(self.pdf.pages)):
            page = self.pdf.pages[page_idx]

            # Skip pages already tagged
            if _is_already_tagged(page):
                # Still need to record parent tree entries from existing tags
                continue

            page_obj = page.obj

            # Gather elements assigned to this page
            page_elems = [
                e
                for e in existing_elems
                if Name("/Pg") in e and _same_page(e[Name("/Pg")], page_obj)
            ]

            self._tag_page(page_idx, page, doc_elem, page_elems)

        self._build_parent_tree(struct_root)
        self._set_pdfua_identifier()

    # ------------------------------------------------------------------
    # Document root
    # ------------------------------------------------------------------

    def _ensure_document_root(self, struct_root) -> "Dictionary":
        """Wrap all StructTreeRoot /K kids under a single Document element.

        If a Document element already exists as the sole child, return it.
        """
        kids = struct_root.get(Name.K)

        # Check if Document element already exists
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

        # Create Document element
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

        # Set Document as sole child of StructTreeRoot
        struct_root[Name.K] = Array([doc_elem])

        return doc_elem

    # ------------------------------------------------------------------
    # Element collection
    # ------------------------------------------------------------------

    def _collect_existing_elements(self, doc_elem) -> list:
        """Recursively collect all StructElem descendants of the Document element."""
        elems: list = []
        kids = doc_elem.get(Name.K)
        if kids is None:
            return elems

        if not isinstance(kids, Array):
            kids = Array([kids])

        table_types = {"/Table", "/TR", "/TH", "/TD", "/THead", "/TBody", "/TFoot"}
        for kid in kids:
            if not hasattr(kid, "keys"):
                continue
            if Name.S in kid:
                elem_type = str(kid[Name.S])
                # Skip table structure elements entirely — TableTagger handles these
                if elem_type in table_types:
                    continue
                elems.append(kid)
        return elems

    # ------------------------------------------------------------------
    # Per-page tagging
    # ------------------------------------------------------------------

    def _tag_page(
        self,
        page_idx: int,
        page,
        doc_elem: "Dictionary",
        page_elems: list,
    ):
        """Tag a single page's content stream."""
        blocks = parse_content_blocks(page)
        if not blocks:
            return

        page_obj = page.obj

        # Parse original operators
        try:
            original_ops = list(pikepdf.parse_content_stream(page))
        except Exception:
            return

        # Build tagged operator list
        new_ops: list = []
        mcid = 0
        page_parent_entries: list = []
        used_elems: set = set()

        # Create a set of block boundaries for quick lookup
        block_map: Dict[int, ContentBlock] = {}
        block_ends: Dict[int, ContentBlock] = {}
        for block in blocks:
            block_map[block.start_index] = block
            block_ends[block.end_index - 1] = block

        # Walk through operators and insert BDC/EMC around blocks
        active_block: Optional[ContentBlock] = None

        for idx, op in enumerate(original_ops):
            if idx in block_map and active_block is None:
                block = block_map[idx]
                active_block = block

                # Match to an existing structure element
                tag_name, elem = self._match_element(
                    block, page_elems, page_obj, doc_elem, mcid, used_elems
                )

                # Insert BDC
                bdc_dict = Dictionary({"/MCID": mcid})
                bdc_op = pikepdf.ContentStreamInstruction(
                    [Name(f"/{tag_name}"), bdc_dict], Operator("BDC")
                )
                new_ops.append(bdc_op)

                page_parent_entries.append((mcid, elem))
                mcid += 1

            new_ops.append(op)

            if active_block is not None and idx == active_block.end_index - 1:
                # Insert EMC after the last operator of the block
                emc_op = pikepdf.ContentStreamInstruction([], Operator("EMC"))
                new_ops.append(emc_op)
                active_block = None

        # Write the new content stream
        new_bytes = pikepdf.unparse_content_stream(new_ops)

        # Merge Contents array into single stream if needed
        page_obj[Name.Contents] = self.pdf.make_stream(new_bytes)

        # Set StructParents on the page
        page_obj[Name.StructParents] = page_idx

        # Store parent tree data for this page
        self._parent_tree_map[page_idx] = page_parent_entries

    # ------------------------------------------------------------------
    # Element matching
    # ------------------------------------------------------------------

    def _match_element(
        self,
        block: ContentBlock,
        page_elems: list,
        page_obj,
        doc_elem: "Dictionary",
        mcid: int,
        used_elems: set,
    ) -> Tuple[str, "Dictionary"]:
        """Match a content block to an existing structure element or create a new one.

        Returns:
            Tuple of (tag_name, structure_element).
        """
        if block.block_type == BlockType.TEXT:
            block_text = _extract_text_from_ops(block.operators)
            return self._match_text_block(
                block_text, page_elems, page_obj, doc_elem, mcid, used_elems
            )
        else:
            return self._match_image_block(
                page_elems, page_obj, doc_elem, mcid, used_elems
            )

    def _match_text_block(
        self,
        block_text: Optional[str],
        page_elems: list,
        page_obj,
        doc_elem: "Dictionary",
        mcid: int,
        used_elems: set,
    ) -> Tuple[str, "Dictionary"]:
        """Match a text block to a structure element by comparing text content."""
        if block_text:
            for elem in page_elems:
                elem_id = id(elem)
                if elem_id in used_elems:
                    continue

                # Get element text
                elem_text = None
                if Name.ActualText in elem:
                    elem_text = str(elem[Name.ActualText])
                elif Name.Alt in elem:
                    elem_text = str(elem[Name.Alt])

                if elem_text and _text_match(block_text, elem_text):
                    elem_type = str(elem[Name.S])
                    # Skip Figure elements for text blocks
                    if elem_type == "/Figure":
                        continue
                    # Skip table structure elements (handled by TableTagger)
                    if elem_type in ("/Table", "/TR", "/TH", "/TD", "/THead", "/TBody", "/TFoot"):
                        continue

                    used_elems.add(elem_id)
                    tag_name = elem_type.lstrip("/")
                    self._set_mcid_on_element(elem, mcid, page_obj)
                    return tag_name, elem

        # No match found -- create a new P element
        elem = self._create_element(
            Name.P, doc_elem, page_obj, mcid, actual_text=block_text
        )
        return "P", elem

    def _match_image_block(
        self,
        page_elems: list,
        page_obj,
        doc_elem: "Dictionary",
        mcid: int,
        used_elems: set,
    ) -> Tuple[str, "Dictionary"]:
        """Match an image block to a Figure structure element."""
        for elem in page_elems:
            elem_id = id(elem)
            if elem_id in used_elems:
                continue

            if Name.S in elem and str(elem[Name.S]) == "/Figure":
                used_elems.add(elem_id)
                self._set_mcid_on_element(elem, mcid, page_obj)
                return "Figure", elem

        # No Figure element found -- create one
        elem = self._create_element(Name.Figure, doc_elem, page_obj, mcid)
        return "Figure", elem

    def _set_mcid_on_element(self, elem, mcid: int, page_obj):
        """Set /K on a structure element to point to the given MCID."""
        mcr = Dictionary(
            {
                "/Type": Name("/MCR"),
                "/MCID": mcid,
                "/Pg": page_obj,
            }
        )

        if Name.K not in elem:
            elem[Name.K] = mcr
        else:
            existing = elem[Name.K]
            if isinstance(existing, Array):
                existing.append(mcr)
            else:
                elem[Name.K] = Array([existing, mcr])

    def _create_element(
        self,
        struct_type,
        doc_elem: "Dictionary",
        page_obj,
        mcid: int,
        actual_text: Optional[str] = None,
    ) -> "Dictionary":
        """Create a new structure element under the Document root."""
        elem_dict: dict = {
            "/Type": Name.StructElem,
            "/S": struct_type,
            "/P": doc_elem,
            "/Pg": page_obj,
            "/K": Dictionary(
                {
                    "/Type": Name("/MCR"),
                    "/MCID": mcid,
                    "/Pg": page_obj,
                }
            ),
        }
        if actual_text:
            elem_dict["/ActualText"] = String(actual_text)

        elem = self.pdf.make_indirect(Dictionary(elem_dict))

        # Add to Document's kids
        doc_kids = doc_elem.get(Name.K)
        if doc_kids is None:
            doc_elem[Name.K] = Array([elem])
        elif isinstance(doc_kids, Array):
            doc_kids.append(elem)
        else:
            doc_elem[Name.K] = Array([doc_kids, elem])

        return elem

    # ------------------------------------------------------------------
    # ParentTree
    # ------------------------------------------------------------------

    def _build_parent_tree(self, struct_root):
        """Build the ParentTree /Nums array from collected MCID mappings."""
        nums_list: list = []

        for page_idx in sorted(self._parent_tree_map.keys()):
            entries = self._parent_tree_map[page_idx]
            if not entries:
                continue

            # Sort by MCID
            entries.sort(key=lambda e: e[0])

            # Build array: element at index i corresponds to MCID i
            max_mcid = entries[-1][0]
            page_array = Array([None] * (max_mcid + 1))
            for mcid, elem in entries:
                page_array[mcid] = elem

            nums_list.append(page_idx)
            nums_list.append(self.pdf.make_indirect(page_array))

        parent_tree = struct_root.get(Name.ParentTree)
        if parent_tree is None:
            parent_tree = Dictionary({"/Nums": Array([])})
            struct_root[Name.ParentTree] = parent_tree

        parent_tree[Name.Nums] = Array(nums_list)

    # ------------------------------------------------------------------
    # PDF/UA identifier
    # ------------------------------------------------------------------

    def _set_pdfua_identifier(self):
        """Set PDF/UA-1 identifier in XMP metadata."""
        try:
            with self.pdf.open_metadata() as meta:
                meta["{http://www.aiim.org/pdfua/ns/id/}part"] = "1"
        except Exception as e:
            logger.warning(f"Could not set PDF/UA-1 identifier: {e}")


__all__ = [
    "BlockType",
    "ContentBlock",
    "ContentTagger",
    "parse_content_blocks",
]
