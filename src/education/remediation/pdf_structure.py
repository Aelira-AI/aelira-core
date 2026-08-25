"""
PDF Structure Tree Manipulation using pikepdf.

This module provides functions to create and manipulate PDF structure trees
for accessibility compliance (PDF/UA-1, PDF/UA-2, WCAG 2.1).

Key capabilities:
- Create/access StructTreeRoot
- Add /Alt entries directly to images (Figure elements)
- Create structure elements (H1-H6, P, Table, L, etc.)
- Set document metadata (language, title)
- Establish reading order via /StructParents
- PDF/UA-2 structure elements (DocumentFragment, Aside, FENote, Em, Strong, etc.)

Based on PDF/UA-1 (ISO 14289-1), PDF/UA-2 (ISO 14289-2:2024), and WCAG 2.1 requirements.
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Pdf, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Pdf = Any
    Dictionary = Any
    Array = Any
    Name = Any
    String = Any

logger = logging.getLogger(__name__)


class PDFStructureTree:
    """
    Helper class for manipulating PDF structure trees with pikepdf.

    This class provides the core functionality for creating accessible PDFs:
    - Creating structure tree root if missing
    - Adding alt text to images (Figure elements)
    - Creating heading structure (H1-H6)
    - Setting document language and title
    - Building proper reading order

    Usage:
        with pikepdf.open('input.pdf') as pdf:
            struct_tree = PDFStructureTree(pdf)
            struct_tree.set_document_language('en')
            struct_tree.add_alt_text_to_image(1, 0, 'Chart showing revenue')
            struct_tree.add_heading(1, 1, 'Introduction')
            pdf.save('output.pdf')
    """

    def __init__(self, pdf: Pdf):
        """
        Initialize the PDF structure tree helper.

        Args:
            pdf: An open pikepdf.Pdf object
        """
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for PDF structure manipulation. "
                "Install with: pip install pikepdf"
            )

        self.pdf = pdf
        self._ensure_struct_tree_root()
        self._element_count = 0

    def _ensure_struct_tree_root(self):
        """Create StructTreeRoot if it doesn't exist."""
        if Name.StructTreeRoot not in self.pdf.Root:
            logger.info("Creating new StructTreeRoot for PDF")

            # Create basic structure tree root
            # Note: pikepdf Dictionary requires string keys with leading "/"
            struct_root = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructTreeRoot,
                        "/K": Array([]),  # Kids array for structure elements
                        "/ParentTree": Dictionary(
                            {
                                "/Nums": Array([]),  # Number tree for parent mappings
                            }
                        ),
                        "/RoleMap": Dictionary(
                            {
                                # Map custom tags to standard structure types (PDF/UA-1)
                                "/H1": Name.H1,
                                "/H2": Name.H2,
                                "/H3": Name.H3,
                                "/H4": Name.H4,
                                "/H5": Name.H5,
                                "/H6": Name.H6,
                                "/P": Name.P,
                                "/Figure": Name.Figure,
                                "/Table": Name.Table,
                                "/TR": Name.TR,
                                "/TH": Name.TH,
                                "/TD": Name.TD,
                                "/L": Name.L,  # List
                                "/LI": Name.LI,  # List item
                                "/Lbl": Name.Lbl,  # List label
                                "/LBody": Name.LBody,  # List body
                                # PDF/UA-2 (ISO 14289-2:2024) structure elements
                                "/DocumentFragment": Name(
                                    "/DocumentFragment"
                                ),  # Part of document
                                "/Aside": Name(
                                    "/Aside"
                                ),  # Side content (footnotes, annotations)
                                "/FENote": Name("/FENote"),  # Footnote/endnote
                                "/Sub": Name("/Sub"),  # Subscript
                                "/Em": Name("/Em"),  # Emphasis (typically italic)
                                "/Strong": Name(
                                    "/Strong"
                                ),  # Strong emphasis (typically bold)
                                # Ruby text support for East Asian languages
                                "/Ruby": Name("/Ruby"),  # Ruby annotation container
                                "/RB": Name("/RB"),  # Ruby base text
                                "/RT": Name("/RT"),  # Ruby annotation text
                                "/RP": Name("/RP"),  # Ruby parenthesis
                                # Phoneme for pronunciation hints
                                "/Phoneme": Name("/Phoneme"),  # Pronunciation hint
                            }
                        ),
                    }
                )
            )
            self.pdf.Root[Name.StructTreeRoot] = struct_root

            # Mark document as tagged
            if Name.MarkInfo not in self.pdf.Root:
                self.pdf.Root[Name.MarkInfo] = Dictionary({})
            self.pdf.Root[Name.MarkInfo][Name.Marked] = True

            logger.info("StructTreeRoot created successfully")

    @property
    def struct_root(self) -> Dictionary:
        """Get the StructTreeRoot dictionary."""
        return self.pdf.Root[Name.StructTreeRoot]

    @property
    def kids(self) -> Array:
        """Get the Kids array from StructTreeRoot."""
        if Name.K not in self.struct_root:
            self.struct_root[Name.K] = Array([])
        kids = self.struct_root[Name.K]
        # Handle case where K is a single element, not an array
        if not isinstance(kids, Array):
            self.struct_root[Name.K] = Array([kids])
        return self.struct_root[Name.K]

    def add_alt_text_to_image(
        self,
        page_num: int,
        alt_text: str,
        image_index: int = 0,
        image_bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add /Alt entry to an image by creating a Figure structure element.

        This is the key function for WCAG 1.1.1 compliance - Non-text Content.
        Screen readers will announce the alt text when encountering the image.

        Args:
            page_num: 1-indexed page number
            alt_text: Alternative text description for the image
            image_index: Index of the image on the page (0-indexed)
            image_bbox: Optional bounding box (x0, y0, x1, y1)

        Returns:
            True if alt text was successfully added
        """
        try:
            self._element_count += 1

            # Create a Figure structure element with alt text
            # Note: pikepdf Dictionary requires string keys with leading "/"
            # Use .obj to get the underlying object for pages
            page_obj = self.pdf.pages[page_num - 1].obj
            fig_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name.Figure,  # Structure type
                        "/P": self.struct_root,  # Parent is root
                        "/Alt": String(alt_text),  # THE KEY: Alt text entry
                        "/Pg": page_obj,  # Page reference
                    }
                )
            )

            # Add optional bounding box if provided
            if image_bbox:
                fig_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(image_bbox)),
                    }
                )

            # Add to structure tree kids
            self.kids.append(fig_elem)

            logger.info(
                f"Added Figure with alt text on page {page_num}: "
                f"{alt_text[:50]}{'...' if len(alt_text) > 50 else ''}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add alt text to image: {e}")
            return False

    def add_heading(
        self,
        page_num: int,
        level: int,
        text: str,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add a heading structure element (H1-H6).

        This is essential for WCAG 1.3.1 (Info and Relationships) and
        2.4.1 (Bypass Blocks) compliance.

        Args:
            page_num: 1-indexed page number
            level: Heading level (1-6)
            text: The heading text content
            bbox: Optional bounding box

        Returns:
            True if heading was successfully added
        """
        if level < 1 or level > 6:
            logger.warning(f"Invalid heading level {level}, clamping to 1-6")
            level = max(1, min(6, level))

        try:
            self._element_count += 1

            heading_type = Name(f"/H{level}")
            page_obj = self.pdf.pages[page_num - 1].obj

            heading_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": heading_type,
                        "/P": self.struct_root,
                        "/ActualText": String(text),  # Actual text for screen readers
                        "/Pg": page_obj,
                    }
                )
            )

            if bbox:
                heading_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(bbox)),
                    }
                )

            self.kids.append(heading_elem)

            logger.info(f"Added H{level} on page {page_num}: {text[:50]}")
            return True

        except Exception as e:
            logger.error(f"Failed to add heading: {e}")
            return False

    def add_paragraph(
        self,
        page_num: int,
        text: str,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add a paragraph structure element.

        Args:
            page_num: 1-indexed page number
            text: The paragraph text content
            bbox: Optional bounding box

        Returns:
            True if paragraph was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            para_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name.P,
                        "/P": self.struct_root,
                        "/ActualText": String(text),
                        "/Pg": page_obj,
                    }
                )
            )

            if bbox:
                para_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(bbox)),
                    }
                )

            self.kids.append(para_elem)
            return True

        except Exception as e:
            logger.error(f"Failed to add paragraph: {e}")
            return False

    def add_table(
        self,
        page_num: int,
        headers: List[str],
        rows: List[List[str]],
        summary: Optional[str] = None,
    ) -> bool:
        """
        Add a table structure with proper header markup.

        Creates Table, TR, TH, and TD elements with proper Scope
        attributes for WCAG 1.3.1 compliance.

        Args:
            page_num: 1-indexed page number
            headers: List of header cell texts
            rows: List of rows, each row is a list of cell texts
            summary: Optional table summary for screen readers

        Returns:
            True if table was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            # Create Table element
            table_kids = Array([])

            table_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name.Table,
                        "/P": self.struct_root,
                        "/K": table_kids,
                        "/Pg": page_obj,
                    }
                )
            )

            if summary:
                table_elem[Name.Alt] = String(summary)

            # Add header row with TH elements
            if headers:
                header_row_kids = Array([])
                header_row = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.TR,
                            "/P": table_elem,
                            "/K": header_row_kids,
                        }
                    )
                )

                for header_text in headers:
                    th_elem = self.pdf.make_indirect(
                        Dictionary(
                            {
                                "/Type": Name.StructElem,
                                "/S": Name.TH,
                                "/P": header_row,
                                "/Scope": Name.Column,  # WCAG requirement
                                "/ActualText": String(header_text),
                            }
                        )
                    )
                    header_row_kids.append(th_elem)

                table_kids.append(header_row)

            # Add data rows
            for row_data in rows:
                data_row_kids = Array([])
                data_row = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.TR,
                            "/P": table_elem,
                            "/K": data_row_kids,
                        }
                    )
                )

                for cell_text in row_data:
                    td_elem = self.pdf.make_indirect(
                        Dictionary(
                            {
                                "/Type": Name.StructElem,
                                "/S": Name.TD,
                                "/P": data_row,
                                "/ActualText": String(str(cell_text)),
                            }
                        )
                    )
                    data_row_kids.append(td_elem)

                table_kids.append(data_row)

            self.kids.append(table_elem)

            logger.info(
                f"Added table on page {page_num}: "
                f"{len(headers)} columns, {len(rows)} rows"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add table structure: {e}")
            return False

    def add_list(
        self,
        page_num: int,
        items: List[str],
        ordered: bool = False,
    ) -> bool:
        """
        Add a list structure with proper list item markup.

        Args:
            page_num: 1-indexed page number
            items: List of item texts
            ordered: Whether this is an ordered list

        Returns:
            True if list was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            list_kids = Array([])

            list_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name.L,
                        "/P": self.struct_root,
                        "/K": list_kids,
                        "/Pg": page_obj,
                    }
                )
            )

            for idx, item_text in enumerate(items):
                li_kids = Array([])

                li_elem = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.LI,
                            "/P": list_elem,
                            "/K": li_kids,
                        }
                    )
                )

                # Add label (bullet or number)
                label = f"{idx + 1}." if ordered else "\u2022"
                lbl_elem = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.Lbl,
                            "/P": li_elem,
                            "/ActualText": String(label),
                        }
                    )
                )
                li_kids.append(lbl_elem)

                # Add body
                lbody_elem = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name.LBody,
                            "/P": li_elem,
                            "/ActualText": String(item_text),
                        }
                    )
                )
                li_kids.append(lbody_elem)

                list_kids.append(li_elem)

            self.kids.append(list_elem)

            logger.info(f"Added list on page {page_num}: {len(items)} items")
            return True

        except Exception as e:
            logger.error(f"Failed to add list structure: {e}")
            return False

    def set_document_language(self, lang: str = "en") -> bool:
        """
        Set PDF document language in Catalog.

        Required for WCAG 3.1.1 (Language of Page) compliance.

        Args:
            lang: Language code (e.g., 'en', 'en-US', 'es', 'fr')

        Returns:
            True if language was successfully set
        """
        try:
            self.pdf.Root[Name.Lang] = String(lang)
            logger.info(f"Set document language to: {lang}")
            return True
        except Exception as e:
            logger.error(f"Failed to set language: {e}")
            return False

    def set_document_title(
        self,
        title: str,
        display_title: bool = True,
    ) -> bool:
        """
        Set document title in metadata and mark for display.

        Required for WCAG 2.4.2 (Page Titled) compliance.

        Args:
            title: The document title
            display_title: Whether to display title in title bar

        Returns:
            True if title was successfully set
        """
        try:
            # Set in XMP metadata
            with self.pdf.open_metadata() as meta:
                meta["dc:title"] = title

            # Set ViewerPreferences to display title
            if Name.ViewerPreferences not in self.pdf.Root:
                self.pdf.Root[Name.ViewerPreferences] = Dictionary({})
            self.pdf.Root[Name.ViewerPreferences][Name.DisplayDocTitle] = display_title

            logger.info(f"Set document title: {title}")
            return True

        except Exception as e:
            logger.error(f"Failed to set title: {e}")
            return False

    def add_bookmarks(
        self,
        bookmarks: List[Dict[str, Any]],
    ) -> bool:
        """
        Add bookmarks/outline to the PDF document.

        Required for WCAG 2.4.1 (Bypass Blocks) and 2.4.5 (Multiple Ways)
        compliance for documents longer than 10 pages.

        Args:
            bookmarks: List of bookmark dicts with keys:
                - level: int (1-6, where 1 is top level)
                - title: str (bookmark text)
                - page: int (1-indexed page number)

        Returns:
            True if bookmarks were successfully added
        """
        if not bookmarks:
            logger.warning("No bookmarks provided to add_bookmarks()")
            return False

        try:
            # Create outline items
            outline_items = []
            for bm in bookmarks:
                level = bm.get("level", 1)
                title = bm.get("title", "Untitled")
                page_num = bm.get("page", 1)

                # Create page destination (go to top of page)
                if page_num <= len(self.pdf.pages):
                    page_obj = self.pdf.pages[page_num - 1].obj
                    # /Fit destination - fit page in window
                    dest = Array([page_obj, Name.Fit])
                else:
                    # Fallback to first page if page number invalid
                    dest = Array([self.pdf.pages[0].obj, Name.Fit])

                outline_item = self.pdf.make_indirect(
                    Dictionary(
                        {
                            "/Title": String(title),
                            "/Dest": dest,
                        }
                    )
                )
                outline_items.append({"item": outline_item, "level": level})

            if not outline_items:
                return False

            # Build the outline tree structure
            # For simplicity, we'll create a flat list (all items at same level)
            # with proper /First, /Last, /Next, /Prev, /Parent links

            # Create the Outlines dictionary
            outlines = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.Outlines,
                        "/Count": len(outline_items),
                    }
                )
            )

            # Link all items
            for i, item_data in enumerate(outline_items):
                item = item_data["item"]
                item[Name.Parent] = outlines

                if i > 0:
                    item[Name.Prev] = outline_items[i - 1]["item"]
                if i < len(outline_items) - 1:
                    item[Name.Next] = outline_items[i + 1]["item"]

            # Set First and Last on Outlines
            outlines[Name.First] = outline_items[0]["item"]
            outlines[Name.Last] = outline_items[-1]["item"]

            # Add Outlines to document root
            self.pdf.Root[Name.Outlines] = outlines

            # Update PageMode to show outlines by default (optional but helpful)
            self.pdf.Root[Name.PageMode] = Name.UseOutlines

            logger.info(f"Added {len(outline_items)} bookmarks to PDF")
            return True

        except Exception as e:
            logger.error(f"Failed to add bookmarks: {e}")
            return False

    # ==================== PDF/UA-2 Structure Elements ====================

    def add_emphasis(
        self,
        page_num: int,
        text: str,
        strong: bool = False,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add emphasis structure element (<Em> or <Strong>).

        PDF/UA-2 (ISO 14289-2:2024) introduces semantic emphasis elements
        instead of relying on visual styling alone.

        Args:
            page_num: 1-indexed page number
            text: The emphasized text content
            strong: If True, use <Strong> (bold emphasis), else <Em> (italic emphasis)
            bbox: Optional bounding box

        Returns:
            True if element was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            elem_type = Name("/Strong") if strong else Name("/Em")

            em_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": elem_type,
                        "/P": self.struct_root,
                        "/ActualText": String(text),
                        "/Pg": page_obj,
                    }
                )
            )

            if bbox:
                em_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(bbox)),
                    }
                )

            self.kids.append(em_elem)
            logger.info(
                f"Added {'Strong' if strong else 'Em'} on page {page_num}: {text[:50]}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add emphasis element: {e}")
            return False

    def add_aside(
        self,
        page_num: int,
        text: str,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add an Aside structure element for supplementary content.

        PDF/UA-2 introduces <Aside> for content that is tangentially related
        to the main content, such as sidebars or pull quotes.

        Args:
            page_num: 1-indexed page number
            text: The aside text content
            bbox: Optional bounding box

        Returns:
            True if element was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            aside_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/Aside"),
                        "/P": self.struct_root,
                        "/ActualText": String(text),
                        "/Pg": page_obj,
                    }
                )
            )

            if bbox:
                aside_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(bbox)),
                    }
                )

            self.kids.append(aside_elem)
            logger.info(f"Added Aside on page {page_num}: {text[:50]}")
            return True

        except Exception as e:
            logger.error(f"Failed to add aside element: {e}")
            return False

    def add_footnote(
        self,
        page_num: int,
        text: str,
        note_type: str = "footnote",
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add a footnote/endnote structure element (FENote).

        PDF/UA-2 introduces <FENote> for footnotes and endnotes with
        proper semantic markup.

        Args:
            page_num: 1-indexed page number
            text: The footnote text content
            note_type: "footnote" or "endnote"
            bbox: Optional bounding box

        Returns:
            True if element was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            fenote_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/FENote"),
                        "/P": self.struct_root,
                        "/ActualText": String(text),
                        "/Pg": page_obj,
                    }
                )
            )

            # Add note type attribute
            fenote_elem[Name.A] = Dictionary(
                {
                    "/O": Name("/FENote"),
                    "/NoteType": String(note_type),
                }
            )

            if bbox:
                fenote_elem[Name.A][Name("/BBox")] = Array(list(bbox))

            self.kids.append(fenote_elem)
            logger.info(f"Added FENote ({note_type}) on page {page_num}: {text[:50]}")
            return True

        except Exception as e:
            logger.error(f"Failed to add footnote element: {e}")
            return False

    def add_document_fragment(
        self,
        page_num: int,
        title: Optional[str] = None,
    ) -> Dictionary:
        """
        Add a DocumentFragment structure element for grouping related content.

        PDF/UA-2 introduces <DocumentFragment> for representing parts of
        a document that form a logical unit.

        Args:
            page_num: 1-indexed page number
            title: Optional title for the fragment

        Returns:
            The created DocumentFragment element (for adding children)
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            fragment_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/DocumentFragment"),
                        "/P": self.struct_root,
                        "/K": Array([]),  # Kids array for child elements
                        "/Pg": page_obj,
                    }
                )
            )

            if title:
                fragment_elem[Name("/T")] = String(title)

            self.kids.append(fragment_elem)
            logger.info(f"Added DocumentFragment on page {page_num}")
            return fragment_elem

        except Exception as e:
            logger.error(f"Failed to add document fragment: {e}")
            return None

    def add_ruby_annotation(
        self,
        page_num: int,
        base_text: str,
        annotation_text: str,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add Ruby annotation structure for East Asian text.

        PDF/UA-2 adds proper support for Ruby annotations, which provide
        pronunciation guides (furigana in Japanese, zhuyin in Chinese, etc.)

        Args:
            page_num: 1-indexed page number
            base_text: The base text being annotated
            annotation_text: The Ruby annotation (pronunciation guide)
            bbox: Optional bounding box

        Returns:
            True if element was successfully added
        """
        try:
            self._element_count += 1
            page_obj = self.pdf.pages[page_num - 1].obj

            # Create Ruby container with RB (base) and RT (annotation)
            rb_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/RB"),
                        "/ActualText": String(base_text),
                    }
                )
            )

            rt_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/RT"),
                        "/ActualText": String(annotation_text),
                    }
                )
            )

            ruby_elem = self.pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name.StructElem,
                        "/S": Name("/Ruby"),
                        "/P": self.struct_root,
                        "/K": Array([rb_elem, rt_elem]),
                        "/Pg": page_obj,
                    }
                )
            )

            # Set parent references
            rb_elem[Name.P] = ruby_elem
            rt_elem[Name.P] = ruby_elem

            if bbox:
                ruby_elem[Name.A] = Dictionary(
                    {
                        "/O": Name.Layout,
                        "/BBox": Array(list(bbox)),
                    }
                )

            self.kids.append(ruby_elem)
            logger.info(
                f"Added Ruby annotation on page {page_num}: {base_text} ({annotation_text})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add ruby annotation: {e}")
            return False

    def set_pdfua_identifier(self, version: int = 2) -> bool:
        """
        Set PDF/UA identifier in XMP metadata.

        Args:
            version: PDF/UA version (1 or 2)

        Returns:
            True if identifier was successfully set
        """
        try:
            with self.pdf.open_metadata() as meta:
                # Set pdfuaid:part namespace
                meta["{http://www.aiim.org/pdfua/ns/id/}part"] = str(version)
                if version == 2:
                    # UA-2 may have revision info
                    meta["{http://www.aiim.org/pdfua/ns/id/}rev"] = "2024"

            logger.info(f"Set PDF/UA-{version} identifier in XMP metadata")
            return True

        except Exception as e:
            logger.error(f"Failed to set PDF/UA identifier: {e}")
            return False

    def create_formula_element(
        self,
        page_num: int,
        alt_text: str,
        mathml_string: str,
        bbox: Optional[tuple] = None,
        mcid: Optional[int] = None,
    ) -> Any:
        """Create, but do not place, one Formula element and MathML attachment."""
        page_obj = self.pdf.pages[page_num - 1].obj
        formula_dict = {
            "/Type": Name.StructElem,
            "/S": Name("/Formula"),
            "/Alt": String(alt_text),
            "/Pg": page_obj,
        }
        if mcid is not None:
            if isinstance(mcid, bool) or not isinstance(mcid, int) or mcid < 0:
                raise ValueError("mcid must be a non-negative integer")
            formula_dict["/K"] = Dictionary(
                {"/Type": Name("/MCR"), "/MCID": mcid, "/Pg": page_obj}
            )
        formula_elem = self.pdf.make_indirect(Dictionary(formula_dict))

        if bbox:
            formula_elem[Name.A] = Dictionary(
                {"/O": Name.Layout, "/BBox": Array(list(bbox))}
            )

        mathml_bytes = mathml_string.encode("utf-8")
        mathml_stream = self.pdf.make_stream(mathml_bytes)
        mathml_stream[Name.Type] = Name("/EmbeddedFile")
        mathml_stream[Name.Subtype] = Name("/application#2Fmathml+xml")
        mathml_stream[Name("/Params")] = Dictionary(
            {
                "/Size": len(mathml_bytes),
                "/CheckSum": String(
                    hashlib.md5(mathml_bytes, usedforsecurity=False).digest()
                ),
            }
        )
        filespec = self.pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name("/Filespec"),
                    "/F": String("formula.mml"),
                    "/EF": Dictionary({"/F": mathml_stream}),
                    "/AFRelationship": Name("/Supplement"),
                }
            )
        )
        formula_elem["/AF"] = Array([filespec])
        return formula_elem

    def add_formula(
        self,
        page_num: int,
        alt_text: str,
        mathml_string: str,
        bbox: Optional[tuple] = None,
    ) -> bool:
        """
        Add a Formula structure element with MathML via /AF associated file.

        Args:
            page_num: 1-indexed page number
            alt_text: Human-readable ARIA label for screen readers
            mathml_string: MathML markup string
            bbox: Optional bounding box (x0, y0, x1, y1)

        Returns:
            True if formula was successfully added
        """
        try:
            self._element_count += 1
            formula_elem = self.create_formula_element(
                page_num=page_num,
                alt_text=alt_text,
                mathml_string=mathml_string,
                bbox=bbox,
            )
            formula_elem[Name.P] = self.struct_root
            self.kids.append(formula_elem)

            logger.info(f"Added Formula on page {page_num}: {alt_text[:50]}")
            return True

        except Exception as e:
            logger.error(f"Failed to add formula: {e}")
            return False

    def add_role_mapping(self, custom_tag: str, standard_tag: str) -> bool:
        """
        Add a role mapping from a custom tag to a standard PDF tag.

        Extends the existing /RoleMap in the StructTreeRoot.

        Args:
            custom_tag: The non-standard tag name (without leading /)
            standard_tag: The standard PDF 1.7 tag to map to (without leading /)

        Returns:
            True if mapping was successfully added
        """
        try:
            role_map = self.struct_root.get("/RoleMap")
            if role_map is None:
                self.struct_root["/RoleMap"] = Dictionary({})
                role_map = self.struct_root["/RoleMap"]

            key = f"/{custom_tag}"
            role_map[key] = Name(f"/{standard_tag}")

            logger.info(f"Added role mapping: {custom_tag} -> {standard_tag}")
            return True

        except Exception as e:
            logger.error(f"Failed to add role mapping: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the structure tree.

        Returns:
            Dictionary with structure tree statistics
        """
        stats = {
            "has_struct_tree": Name.StructTreeRoot in self.pdf.Root,
            "is_marked": False,
            "element_count": self._element_count,
            "has_language": Name.Lang in self.pdf.Root,
            "language": None,
        }

        if Name.MarkInfo in self.pdf.Root:
            stats["is_marked"] = bool(
                self.pdf.Root[Name.MarkInfo].get(Name.Marked, False)
            )

        if stats["has_language"]:
            stats["language"] = str(self.pdf.Root[Name.Lang])

        return stats


def verify_pdf_accessibility(file_path: str) -> Dict[str, Any]:
    """
    Verify PDF accessibility by inspecting structure tree.

    Checks:
    1. Document is tagged (MarkInfo.Marked = true)
    2. StructTreeRoot exists
    3. Document language is set
    4. Title is set
    5. Count of structure elements

    Args:
        file_path: Path to PDF file

    Returns:
        Dictionary with verification results
    """
    if not HAS_PIKEPDF:
        return {"error": "pikepdf not installed"}

    results = {
        "is_tagged": False,
        "has_struct_tree": False,
        "language_set": False,
        "language": None,
        "title_set": False,
        "title": None,
        "figure_count": 0,
        "heading_count": 0,
        "table_count": 0,
        "issues": [],
    }

    try:
        with pikepdf.open(file_path) as pdf:
            # Check tagged flag
            if Name.MarkInfo in pdf.Root:
                results["is_tagged"] = bool(
                    pdf.Root[Name.MarkInfo].get(Name.Marked, False)
                )

            if not results["is_tagged"]:
                results["issues"].append("Document is not marked as tagged")

            # Check StructTreeRoot
            results["has_struct_tree"] = Name.StructTreeRoot in pdf.Root

            if not results["has_struct_tree"]:
                results["issues"].append("Missing StructTreeRoot")

            # Check language
            if Name.Lang in pdf.Root:
                results["language_set"] = True
                results["language"] = str(pdf.Root[Name.Lang])
            else:
                results["issues"].append("Missing document language")

            # Check title
            try:
                with pdf.open_metadata() as meta:
                    title = meta.get("dc:title")
                    if title:
                        results["title_set"] = True
                        results["title"] = str(title)
            except Exception:
                pass

            if not results["title_set"]:
                results["issues"].append("Missing document title")

            # Count structure elements
            if results["has_struct_tree"]:

                def count_elements(elem, counts):
                    if hasattr(elem, "S"):
                        elem_type = str(elem.S)
                        if elem_type == "/Figure":
                            counts["figure"] += 1
                        elif elem_type.startswith("/H"):
                            counts["heading"] += 1
                        elif elem_type == "/Table":
                            counts["table"] += 1

                    if hasattr(elem, "K"):
                        kids = elem.K
                        if isinstance(kids, Array):
                            for kid in kids:
                                if hasattr(kid, "S"):
                                    count_elements(kid, counts)
                        elif hasattr(kids, "S"):
                            count_elements(kids, counts)

                counts = {"figure": 0, "heading": 0, "table": 0}
                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K in struct_root:
                    kids = struct_root[Name.K]
                    if isinstance(kids, Array):
                        for kid in kids:
                            count_elements(kid, counts)
                    else:
                        count_elements(kids, counts)

                results["figure_count"] = counts["figure"]
                results["heading_count"] = counts["heading"]
                results["table_count"] = counts["table"]

    except Exception as e:
        results["error"] = str(e)
        results["issues"].append(f"Error reading PDF: {e}")

    return results


__all__ = ["PDFStructureTree", "verify_pdf_accessibility", "HAS_PIKEPDF"]
