"""Role Mapping Fixer for PDF accessibility remediation.

Ensures all non-standard structure tags in a PDF's role map are mapped
to standard PDF 1.7 (ISO 32000-1) structure types. This is required for
PDF/UA compliance and correct screen-reader interpretation.

WCAG 1.3.1 (Info and Relationships): Structure is programmatically
determinable only when tags resolve to known semantic roles.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Array = None
    Dictionary = None
    Name = None
    String = None

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .base import IssueCategory, IssueSeverity, RemediationIssue

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standard PDF 1.7 structure tag set (ISO 32000-1, Table 333)
# ---------------------------------------------------------------------------

STANDARD_TAGS: Set[str] = {
    # Document-level grouping
    "Document",
    "Part",
    "Art",
    "Sect",
    "Div",
    "BlockQuote",
    "Caption",
    "TOC",
    "TOCI",
    "Index",
    "NonStruct",
    "Private",
    # Block-level structure
    "P",
    "H",
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    # List structure
    "L",
    "LI",
    "Lbl",
    "LBody",
    # Table structure
    "Table",
    "TR",
    "TH",
    "TD",
    "THead",
    "TBody",
    "TFoot",
    # Inline-level structure
    "Span",
    "Quote",
    "Note",
    "Reference",
    "BibEntry",
    "Code",
    "Link",
    "Annot",
    # Illustration elements
    "Figure",
    "Formula",
    "Form",
}

# ---------------------------------------------------------------------------
# Mapping from common non-standard tags to their standard equivalents
# ---------------------------------------------------------------------------

TAG_MAPPING: Dict[str, str] = {
    # Text-like containers
    "textbox": "Div",
    "text": "P",
    "normal": "P",
    "body": "P",
    "bodytext": "P",
    "paragraph": "P",
    "para": "P",
    "text_body": "P",
    "textbody": "P",
    "default": "P",
    # Headings
    "title": "H1",
    "heading": "H1",
    "heading1": "H1",
    "heading2": "H2",
    "heading3": "H3",
    "heading4": "H4",
    "heading5": "H5",
    "heading6": "H6",
    "h": "H1",
    "subtitle": "H2",
    "subheading": "H2",
    "subheadline": "H2",
    "headline": "H1",
    # Lists
    "list": "L",
    "listitem": "LI",
    "list_item": "LI",
    "listbody": "LBody",
    "list_body": "LBody",
    "listlabel": "Lbl",
    "list_label": "Lbl",
    # Tables
    "table": "Table",
    "tablerow": "TR",
    "table_row": "TR",
    "tableheader": "TH",
    "table_header": "TH",
    "tabledata": "TD",
    "table_data": "TD",
    "tablecell": "TD",
    "table_cell": "TD",
    # Containers / layout
    "sidebar": "Div",
    "box": "Div",
    "container": "Div",
    "wrapper": "Div",
    "section": "Sect",
    "article": "Art",
    "block": "Div",
    "callout": "Div",
    "pullquote": "BlockQuote",
    "blockquote": "BlockQuote",
    "quote": "BlockQuote",
    # Inline
    "strong": "Span",
    "bold": "Span",
    "italic": "Span",
    "emphasis": "Span",
    "em": "Span",
    "b": "Span",
    "i": "Span",
    "u": "Span",
    "underline": "Span",
    "strikethrough": "Span",
    "hyperlink": "Link",
    "url": "Link",
    "anchor": "Link",
    # Media / figures
    "image": "Figure",
    "img": "Figure",
    "photo": "Figure",
    "illustration": "Figure",
    "chart": "Figure",
    "diagram": "Figure",
    "graph": "Figure",
    # Form elements
    "field": "Form",
    "input": "Form",
    "checkbox": "Form",
    "radiobutton": "Form",
    "button": "Form",
    "dropdown": "Form",
    "select": "Form",
    "textarea": "Form",
    "formfield": "Form",
    # Footnotes / annotations
    "footnote": "Note",
    "endnote": "Note",
    "annotation": "Annot",
    "comment": "Note",
    # Code
    "code": "Code",
    "pre": "Code",
    "codeblock": "Code",
    "verbatim": "Code",
    "monospace": "Code",
    # Math
    "math": "Formula",
    "equation": "Formula",
    "formula": "Formula",
    # TOC
    "toc": "TOC",
    "tableofcontents": "TOC",
    "tocitem": "TOCI",
    "toc_item": "TOCI",
    # Captions
    "caption": "Caption",
    "figcaption": "Caption",
    "tablecaption": "Caption",
    # Document
    "document": "Document",
    "doc": "Document",
    "page": "Div",
    # Bibliographic
    "bibliography": "Index",
    "reference": "Reference",
    "citation": "BibEntry",
    "bibentry": "BibEntry",
}

# Default fallback for unmapped non-standard tags
_DEFAULT_FALLBACK = "Div"


@dataclass
class RoleMappingResult:
    """Result from a RoleMappingFixer.fix() call."""

    success: bool
    tags_examined: int = 0
    tags_mapped: int = 0
    tags_already_standard: int = 0
    mapped: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


class RoleMappingFixer:
    """Fix non-standard structure tag role mappings in a PDF.

    Walks the structure tree to find every tag name in use, then ensures
    the document's ``/RoleMap`` dictionary maps each non-standard name to
    its closest PDF 1.7 standard equivalent.

    Args:
        pdf: An open ``pikepdf.Pdf`` object.
        fitz_doc: An open ``fitz.Document`` object (accepted for interface
            consistency but not used by this fixer).
    """

    def __init__(self, pdf: "pikepdf.Pdf", fitz_doc=None) -> None:
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for role mapping. "
                "Install with: pip install pikepdf"
            )
        self.pdf = pdf
        # fitz_doc accepted for interface parity; not needed here.
        self._fitz_doc = fitz_doc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self, issues: List[RemediationIssue]) -> List[RoleMappingResult]:
        """Apply role-mapping fixes for the supplied issues.

        A single structural pass is made regardless of how many issues are
        provided.  The method returns a list with one ``RoleMappingResult``.

        Args:
            issues: List of ``RemediationIssue`` objects.  Issues whose
                category is not ``STRUCTURE`` are silently skipped.

        Returns:
            A list containing one ``RoleMappingResult``.
        """
        relevant = [
            i
            for i in issues
            if i.category == IssueCategory.STRUCTURE
        ]

        if not relevant:
            return [
                RoleMappingResult(
                    success=True,
                    error="No STRUCTURE issues to process",
                )
            ]

        return [self._apply_role_mappings()]

    # ------------------------------------------------------------------
    # Core fix logic
    # ------------------------------------------------------------------

    def _apply_role_mappings(self) -> RoleMappingResult:
        """Walk the structure tree and populate /RoleMap."""
        try:
            struct_root = self.pdf.Root.get(Name.StructTreeRoot)
            if struct_root is None:
                return RoleMappingResult(
                    success=False,
                    error="No StructTreeRoot found in document",
                )

            # Collect all tag names used in the structure tree
            used_tags = self._collect_tags(struct_root)

            # Separate non-standard from standard tags
            non_standard = {t for t in used_tags if t not in STANDARD_TAGS}
            already_standard = used_tags - non_standard

            if not non_standard:
                return RoleMappingResult(
                    success=True,
                    tags_examined=len(used_tags),
                    tags_already_standard=len(already_standard),
                    tags_mapped=0,
                )

            # Ensure /RoleMap exists on StructTreeRoot
            role_map = self._ensure_role_map(struct_root)

            # Write mappings
            mapped: Dict[str, str] = {}
            for tag in sorted(non_standard):
                standard = self._resolve_tag(tag)
                role_map[Name(f"/{tag}")] = Name(f"/{standard}")
                mapped[tag] = standard
                logger.debug("RoleMap: /%s -> /%s", tag, standard)

            return RoleMappingResult(
                success=True,
                tags_examined=len(used_tags),
                tags_already_standard=len(already_standard),
                tags_mapped=len(mapped),
                mapped=mapped,
            )

        except Exception as exc:
            logger.error("RoleMappingFixer failed: %s", exc, exc_info=True)
            return RoleMappingResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Tag collection
    # ------------------------------------------------------------------

    def _collect_tags(self, struct_root) -> Set[str]:
        """Recursively walk the structure tree and collect every /S tag name."""
        tags: Set[str] = set()
        visited: Set[int] = set()

        kids = struct_root.get(Name.K)
        if kids is None:
            return tags

        if not isinstance(kids, Array):
            kids = Array([kids])

        stack = list(kids)
        while stack:
            elem = stack.pop()
            if not hasattr(elem, "keys"):
                continue

            elem_id = id(elem)
            if elem_id in visited:
                continue
            visited.add(elem_id)

            # Collect this element's tag
            s_val = elem.get(Name.S)
            if s_val is not None:
                tag_name = str(s_val).lstrip("/")
                tags.add(tag_name)

            # Recurse into children
            child_kids = elem.get(Name.K)
            if child_kids is not None:
                if not isinstance(child_kids, Array):
                    child_kids = Array([child_kids])
                for child in child_kids:
                    if hasattr(child, "keys"):
                        stack.append(child)

        return tags

    # ------------------------------------------------------------------
    # RoleMap helpers
    # ------------------------------------------------------------------

    def _ensure_role_map(self, struct_root) -> "Dictionary":
        """Return the /RoleMap dictionary, creating it if absent."""
        role_map = struct_root.get(Name.RoleMap)
        if role_map is None:
            role_map = Dictionary({})
            struct_root[Name.RoleMap] = role_map
        return role_map

    def _resolve_tag(self, tag: str) -> str:
        """Map a non-standard tag name to its standard PDF 1.7 equivalent.

        Lookup is case-insensitive.  Falls back to ``Div`` for unknown tags.
        """
        lower = tag.lower()
        if lower in TAG_MAPPING:
            return TAG_MAPPING[lower]
        # Try stripping common prefixes/suffixes (e.g. "my_p" -> "p" -> "P")
        stripped = lower.strip("_- ")
        if stripped in TAG_MAPPING:
            return TAG_MAPPING[stripped]
        # Check if it is already standard (case-insensitive)
        for std in STANDARD_TAGS:
            if std.lower() == lower:
                return std
        return _DEFAULT_FALLBACK


__all__ = [
    "RoleMappingFixer",
    "RoleMappingResult",
    "STANDARD_TAGS",
    "TAG_MAPPING",
]
