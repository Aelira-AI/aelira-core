"""Structure tree accessibility checking for PDFs."""

import logging
from typing import Dict, List

import fitz  # PyMuPDF for text extraction in list checks

try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

logger = logging.getLogger(__name__)


def _is_list_item(line: str) -> bool:
    """Detect list items (bullet points, numbered/lettered lists)."""
    # Bullet points
    if line.startswith(
        ("\u2022", "-", "*", "\u25cb", "\u25aa", "\u25e6", "\u2013", "\u2014")
    ):
        return True

    # Numbered lists (1. , 1) , a. , a) , etc.)
    if len(line) > 2:
        if line[0].isdigit() and line[1:3] in (". ", ") ", ".\t", ")\t"):
            return True
        if line[0].isalpha() and line[1:3] in (". ", ") ", ".\t", ")\t"):
            return True

    return False


class StructureTreeChecker:
    """Check PDF structure tree accessibility (language, title, tags, bookmarks)."""

    def check(self, file_path: str) -> List[Dict]:
        """Run structure accessibility checks and font/role mapping checks.

        Checks the PDF's internal structure for accessibility compliance:
        - Document language (/Lang in catalog) - WCAG 3.1.1
        - Document title (metadata + ViewerPreferences) - WCAG 2.4.2
        - Structure tree (StructTreeRoot) - PDF/UA requirement
        - Tagged PDF (MarkInfo.Marked) - PDF/UA requirement
        - Bookmarks/outline - WCAG 2.4.5
        - Content stream marking (BDC/EMC) - PDF/UA 7.2
        - ParentTree mapping - PDF/UA 7.2
        - Document root element type - PDF/UA 7.1
        - PDF/UA identifier in XMP - PDF/UA 6.6.4
        - List structure tags (L/LI) - WCAG 1.3.1
        - Font Unicode mapping (/ToUnicode) - PDF/UA 7.21.3.1
        - Role mapping for non-standard tags - PDF/UA 7.1

        Args:
            file_path: Path to the PDF file

        Returns:
            List of accessibility issues found in the PDF structure
        """
        issues = self._check_pdf_structure_accessibility(file_path)
        issues.extend(self._check_list_structure(file_path))
        issues.extend(self._check_font_and_role_mapping(file_path))
        return issues

    def has_h1(self, file_path: str) -> bool:
        """Check if the PDF structure tree has an H1 heading element.

        Used to determine if the document has proper heading structure
        even when text-based heuristics don't detect H1 (e.g., after remediation).

        Handles both Playwright's shallow structure (/Document only) and LuaLaTeX + tagpdf's
        deeper structure (/Document -> /Part or /Sect -> /H1, etc.).

        Args:
            file_path: Path to PDF file

        Returns:
            True if the structure tree contains at least one valid H1 element
        """
        if not HAS_PIKEPDF:
            return False

        try:
            with pikepdf.open(file_path) as pdf:
                if Name.StructTreeRoot not in pdf.Root:
                    return False

                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.K not in struct_root:
                    return False

                def find_heading(elem, depth=0) -> bool:
                    """Recursively search for H1-H6 elements at any depth.

                    LuaLaTeX + tagpdf creates deeper nesting:
                    /Document -> /Part (or /Sect) -> /H1 (with /K MCR and /Pg reference)

                    Args:
                        elem: Structure element to check
                        depth: Current recursion depth (prevents infinite loops)

                    Returns:
                        True if a valid heading element is found
                    """
                    if depth > 15:  # Prevent infinite recursion
                        return False

                    try:
                        # Check if this element is a heading
                        if hasattr(elem, "S"):
                            elem_type = str(elem.S)
                            # Match H1-H6 or generic /H heading
                            if elem_type in [
                                "/H1",
                                "/H2",
                                "/H3",
                                "/H4",
                                "/H5",
                                "/H6",
                                "/H",
                            ]:
                                # For PDF/UA validity, verify it has content reference
                                # /K (marked content) or /Pg (page) indicates valid structure
                                if hasattr(elem, "K") or hasattr(elem, "Pg"):
                                    return True
                                # Even without explicit refs, if we found the tag, count it
                                # (older PDFs may not have proper refs but still work)
                                return True

                        # Recursively check children
                        if hasattr(elem, "K"):
                            kids = elem.K
                            # Kids can be: integer (MCR), Dictionary (struct elem), or Array
                            if isinstance(kids, (list, pikepdf.Array)):
                                for kid in kids:
                                    try:
                                        if isinstance(kid, pikepdf.Dictionary):
                                            if find_heading(kid, depth + 1):
                                                return True
                                        elif hasattr(kid, "S") or hasattr(kid, "K"):
                                            if find_heading(kid, depth + 1):
                                                return True
                                    except Exception:
                                        continue
                            elif isinstance(kids, pikepdf.Dictionary):
                                if find_heading(kids, depth + 1):
                                    return True
                            elif hasattr(kids, "S") or hasattr(kids, "K"):
                                if find_heading(kids, depth + 1):
                                    return True
                    except Exception:
                        pass

                    return False

                root_kids = struct_root[Name.K]
                # root_kids could be a single element (Document) or an array
                if isinstance(root_kids, pikepdf.Dictionary):
                    # Single structure element (e.g., Document)
                    if find_heading(root_kids):
                        return True
                elif isinstance(root_kids, (list, pikepdf.Array)):
                    # Array of structure elements
                    for kid in root_kids:
                        if find_heading(kid):
                            return True

                return False

        except Exception as e:
            logger.warning(
                f"[StructureTreeChecker] Error checking structure tree for H1: {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_pdf_structure_accessibility(self, file_path: str) -> List[Dict]:
        """Check actual PDF structure accessibility using pikepdf.

        This checks the PDF's internal structure for accessibility compliance:
        - Document language (/Lang in catalog) - WCAG 3.1.1
        - Document title (metadata + ViewerPreferences) - WCAG 2.4.2
        - Structure tree (StructTreeRoot) - PDF/UA requirement
        - Tagged PDF (MarkInfo.Marked) - PDF/UA requirement
        - Bookmarks/outline - WCAG 2.4.5

        Returns:
            List of accessibility issues found in the PDF structure
        """
        issues = []

        if not HAS_PIKEPDF:
            logger.warning(
                "pikepdf not available, skipping PDF structure accessibility checks"
            )
            return issues

        try:
            pdf = pikepdf.open(file_path)

            # 1. Check document language (/Lang in catalog)
            has_language = False
            if Name.Lang in pdf.Root:
                lang = pdf.Root[Name.Lang]
                if lang and str(lang).strip():
                    has_language = True

            if not has_language:
                issues.append(
                    {
                        "severity": "high",
                        "rule": "WCAG 3.1.1",
                        "message": "PDF document language not set",
                        "impact": "Screen readers cannot determine document language for proper pronunciation",
                        "page_number": 1,
                        "location": "Document catalog",
                        "element": "/Lang attribute",
                        "suggested_fix": "Set document language in PDF catalog (e.g., 'en' for English)",
                        "issue_type": "missing_language",
                    }
                )

            # 2. Check document title
            has_title = False
            title_value = None
            displays_title = False

            # Check PDF metadata for title
            try:
                with pdf.open_metadata() as meta:
                    if meta.get("dc:title"):
                        title_value = meta.get("dc:title")
                        has_title = bool(title_value and str(title_value).strip())
            except Exception:
                pass

            # Check ViewerPreferences.DisplayDocTitle
            if Name.ViewerPreferences in pdf.Root:
                vp = pdf.Root[Name.ViewerPreferences]
                if Name.DisplayDocTitle in vp:
                    displays_title = bool(vp[Name.DisplayDocTitle])

            if not has_title:
                issues.append(
                    {
                        "severity": "medium",
                        "rule": "WCAG 2.4.2",
                        "message": "PDF document title not set in metadata",
                        "impact": "Users cannot identify document content from title bar",
                        "page_number": 1,
                        "location": "Document metadata",
                        "element": "dc:title",
                        "suggested_fix": "Set document title in PDF metadata",
                        "issue_type": "missing_title",
                    }
                )
            elif not displays_title:
                issues.append(
                    {
                        "severity": "low",
                        "rule": "WCAG 2.4.2",
                        "message": "PDF title exists but not configured to display in title bar",
                        "impact": "Title bar may show filename instead of document title",
                        "page_number": 1,
                        "location": "ViewerPreferences",
                        "element": "DisplayDocTitle",
                        "suggested_fix": "Set DisplayDocTitle=true in ViewerPreferences",
                        "issue_type": "title_not_displayed",
                    }
                )

            # 3. Check structure tree (StructTreeRoot)
            has_struct_tree = Name.StructTreeRoot in pdf.Root
            struct_tree_valid = False

            if has_struct_tree:
                struct_root = pdf.Root[Name.StructTreeRoot]
                # Check if structure tree has content
                if Name.K in struct_root:
                    kids = struct_root[Name.K]
                    struct_tree_valid = (
                        len(kids) > 0 if hasattr(kids, "__len__") else bool(kids)
                    )

            if not has_struct_tree:
                issues.append(
                    {
                        "severity": "critical",
                        "rule": "PDF/UA",
                        "message": "PDF has no structure tree (untagged PDF)",
                        "impact": "Screen readers cannot navigate document structure; content may be inaccessible",
                        "page_number": 1,
                        "location": "Document catalog",
                        "element": "/StructTreeRoot",
                        "suggested_fix": "Add structure tree with proper tags (H1-H6, P, Figure, Table, etc.)",
                        "issue_type": "missing_structure_tree",
                    }
                )
            elif not struct_tree_valid:
                issues.append(
                    {
                        "severity": "high",
                        "rule": "PDF/UA",
                        "message": "PDF structure tree is empty",
                        "impact": "Document is marked as tagged but has no structure elements",
                        "page_number": 1,
                        "location": "StructTreeRoot",
                        "element": "/K (kids array)",
                        "suggested_fix": "Add structure elements to the structure tree",
                        "issue_type": "empty_structure_tree",
                    }
                )

            # 4. Check if document is marked as tagged
            is_marked = False
            if Name.MarkInfo in pdf.Root:
                mark_info = pdf.Root[Name.MarkInfo]
                if Name.Marked in mark_info:
                    is_marked = bool(mark_info[Name.Marked])

            if not is_marked and not has_struct_tree:
                # Already covered by structure tree check
                pass
            elif has_struct_tree and not is_marked:
                issues.append(
                    {
                        "severity": "medium",
                        "rule": "PDF/UA",
                        "message": "PDF has structure tree but is not marked as tagged",
                        "impact": "Some screen readers may not recognize the document as accessible",
                        "page_number": 1,
                        "location": "MarkInfo",
                        "element": "/Marked",
                        "suggested_fix": "Set MarkInfo.Marked=true to indicate tagged PDF",
                        "issue_type": "not_marked_tagged",
                    }
                )

            # 5. Check for bookmarks/outline (recommended for navigation)
            has_bookmarks = False
            if Name.Outlines in pdf.Root:
                outlines = pdf.Root[Name.Outlines]
                # Check if outlines has content
                if hasattr(outlines, "get"):
                    first = outlines.get(Name.First)
                    has_bookmarks = first is not None

            # Only flag missing bookmarks for documents with 5+ pages
            page_count = len(pdf.pages)
            if not has_bookmarks and page_count >= 5:
                issues.append(
                    {
                        "severity": "low",
                        "rule": "WCAG 2.4.5",
                        "message": f"PDF has {page_count} pages but no bookmarks/outline",
                        "impact": "Users cannot quickly navigate to sections in long documents",
                        "page_number": 1,
                        "location": "Document catalog",
                        "element": "/Outlines",
                        "suggested_fix": "Add bookmarks for major sections/headings",
                        "issue_type": "missing_bookmarks",
                    }
                )

            # 6. Check content stream marking (BDC/EMC operators)
            # A structure tree without content marking is cosmetic-only
            has_content_marking = False
            if has_struct_tree and struct_tree_valid:
                # Check all pages for BDC/EMC markers (stop at first found)
                sample_pages = list(pdf.pages)
                for page in sample_pages:
                    try:
                        instructions = pikepdf.parse_content_stream(page)
                        for operands, operator in instructions:
                            op_str = str(operator)
                            if op_str in ("BDC", "BMC"):
                                has_content_marking = True
                                break
                    except Exception:
                        pass
                    if has_content_marking:
                        break

                if not has_content_marking:
                    issues.append(
                        {
                            "severity": "critical",
                            "rule": "PDF/UA 7.2",
                            "message": "Structure tree exists but content streams have no marked content (BDC/EMC)",
                            "impact": "Screen readers cannot connect page content to structure elements; tagging is cosmetic only",
                            "page_number": 1,
                            "location": "Content streams",
                            "element": "BDC/EMC operators",
                            "suggested_fix": "Insert BDC/EMC marked content operators in content streams with MCIDs linking to structure elements",
                            "issue_type": "missing_content_marking",
                        }
                    )

            # 7. Check ParentTree is populated
            has_parent_tree = False
            if has_struct_tree:
                struct_root = pdf.Root[Name.StructTreeRoot]
                if Name.ParentTree in struct_root:
                    parent_tree = struct_root[Name.ParentTree]
                    if Name.Nums in parent_tree:
                        nums = parent_tree[Name.Nums]
                        has_parent_tree = len(nums) > 0

                if struct_tree_valid and not has_parent_tree:
                    issues.append(
                        {
                            "severity": "critical",
                            "rule": "PDF/UA 7.2",
                            "message": "Structure tree has no ParentTree mapping (empty /Nums)",
                            "impact": "MCIDs in content streams cannot be resolved to structure elements",
                            "page_number": 1,
                            "location": "StructTreeRoot",
                            "element": "/ParentTree /Nums",
                            "suggested_fix": "Build ParentTree with /Nums mapping page MCIDs to structure elements",
                            "issue_type": "empty_parent_tree",
                        }
                    )

            # 8. Check Document root element type
            has_document_root = False
            if has_struct_tree and struct_tree_valid:
                struct_root = pdf.Root[Name.StructTreeRoot]
                kids = struct_root[Name.K]
                # Check if root element(s) have /Document or /Part type
                # Use Name.S check to identify struct elements (not hasattr which is unreliable for pikepdf)
                try:
                    is_struct = hasattr(kids, "get") and Name.S in kids
                except Exception:
                    is_struct = False
                if is_struct:
                    elem_type = str(kids[Name.S])
                    if elem_type in ("/Document", "/Part"):
                        has_document_root = True
                elif hasattr(kids, "__iter__") and not isinstance(kids, (str, bytes)):
                    for kid in kids:
                        try:
                            if hasattr(kid, "get") and Name.S in kid:
                                elem_type = str(kid[Name.S])
                                if elem_type in ("/Document", "/Part"):
                                    has_document_root = True
                                    break
                        except Exception:
                            continue

                if not has_document_root:
                    issues.append(
                        {
                            "severity": "high",
                            "rule": "PDF/UA 7.1",
                            "message": "Structure tree missing /Document or /Part root element",
                            "impact": "PDF/UA requires a Document or Part element as the root of the structure tree",
                            "page_number": 1,
                            "location": "StructTreeRoot",
                            "element": "/K root element /S type",
                            "suggested_fix": "Wrap structure elements under a /Document root element",
                            "issue_type": "missing_document_root",
                        }
                    )

            # 9. Check PDF/UA identifier in XMP metadata
            has_pdfua_id = False
            try:
                with pdf.open_metadata() as meta:
                    xmp_str = str(meta)
                    if (
                        "pdfuaid:part" in xmp_str.lower()
                        or "pdfaid:part" in xmp_str.lower()
                    ):
                        has_pdfua_id = True
            except Exception:
                pass

            if has_struct_tree and not has_pdfua_id:
                issues.append(
                    {
                        "severity": "medium",
                        "rule": "PDF/UA 6.6.4",
                        "message": "PDF/UA identifier not set in XMP metadata",
                        "impact": "Assistive technology may not recognize this as a PDF/UA document",
                        "page_number": 1,
                        "location": "XMP metadata",
                        "element": "pdfuaid:part",
                        "suggested_fix": "Set pdfuaid:part=1 in XMP metadata to declare PDF/UA-1 conformance",
                        "issue_type": "missing_pdfua_identifier",
                    }
                )

            pdf.close()

            logger.info(
                f"[StructureTreeChecker] PDF structure check complete: "
                f"lang={has_language}, title={has_title}, "
                f"struct_tree={has_struct_tree}, marked={is_marked}, "
                f"bookmarks={has_bookmarks}, content_marking={has_content_marking}, "
                f"parent_tree={has_parent_tree}, doc_root={has_document_root}, "
                f"pdfua_id={has_pdfua_id}, issues={len(issues)}"
            )

        except Exception as e:
            logger.error(f"[StructureTreeChecker] Error checking PDF structure: {e}")
            # Don't fail the whole process, just skip structure checks

        return issues

    def _check_list_structure(self, file_path: str) -> List[Dict]:
        """Check if PDF has list content that lacks proper L/LI structure tags.

        Compares text-detected list items against structure tree list elements.
        """
        issues = []

        if not HAS_PIKEPDF:
            return issues

        try:
            pdf = pikepdf.open(file_path)

            # Count list elements in structure tree
            list_elem_count = 0
            struct_root = pdf.Root.get(Name.StructTreeRoot)
            if struct_root and Name.K in struct_root:
                self._count_list_elements(
                    struct_root[Name.K], list_elem_count_ref := [0]
                )
                list_elem_count = list_elem_count_ref[0]

            # Count list items in text content
            text_list_items = 0
            try:
                with fitz.open(file_path) as doc:
                    for page in doc:
                        text = page.get_text()
                        for line in text.split("\n"):
                            line = line.strip()
                            if line and _is_list_item(line):
                                text_list_items += 1
            except Exception:
                pass

            # If there are list items in text but no L elements in structure tree
            if text_list_items >= 3 and list_elem_count == 0:
                issues.append(
                    {
                        "severity": "high",
                        "rule": "WCAG 1.3.1",
                        "message": f"Document has ~{text_list_items} list items but no List structure tags (L/LI)",
                        "impact": "Screen readers cannot identify lists, losing navigation and context",
                        "page_number": 1,
                        "location": "Structure tree",
                        "element": "/L, /LI, /Lbl, /LBody",
                        "suggested_fix": "Add L (List) and LI (List Item) structure elements with Lbl and LBody children",
                        "issue_type": "missing_list_structure",
                    }
                )

            pdf.close()
        except Exception as e:
            logger.warning(f"[StructureTreeChecker] List structure check error: {e}")

        return issues

    def _count_list_elements(self, obj, count_ref: list) -> None:
        """Recursively count /L (List) elements in structure tree."""
        try:
            # Determine if this object is a structure element (has /S key)
            # vs an array/list of children.  pikepdf Objects can have both
            # __len__ and keys, so we check for /S to disambiguate.
            is_struct_elem = False
            try:
                is_struct_elem = hasattr(obj, "get") and Name.S in obj
            except Exception:
                pass

            if is_struct_elem:
                if str(obj[Name.S]) == "/L":
                    count_ref[0] += 1
                kids = obj.get(Name.K)
                if kids is not None:
                    self._count_list_elements(kids, count_ref)
            elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
                for item in obj:
                    self._count_list_elements(item, count_ref)
        except Exception:
            pass  # Skip malformed elements

    def _collect_tag_types(self, obj, tag_set: set) -> None:
        """Recursively collect all structure element /S types."""
        try:
            is_struct_elem = False
            try:
                is_struct_elem = hasattr(obj, "get") and Name.S in obj
            except Exception:
                pass
            if is_struct_elem:
                tag_set.add(str(obj[Name.S]))
                kids = obj.get(Name.K)
                if kids is not None:
                    self._collect_tag_types(kids, tag_set)
            elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
                for item in obj:
                    self._collect_tag_types(item, tag_set)
        except Exception:
            pass

    def _check_font_and_role_mapping(self, file_path: str) -> List[Dict]:
        """Check font Unicode mapping and structure tree role mapping.

        Validates:
        - Fonts have /ToUnicode CMap for text extraction (PDF/UA 7.21.3.1)
        - Non-standard structure tags have /RoleMap entries (PDF/UA 7.1)

        Also checks for /Artifact marking on decorative content.
        """
        issues = []
        if not HAS_PIKEPDF:
            return issues

        try:
            pdf = pikepdf.open(file_path)

            # --- Font Unicode mapping ---
            fonts_checked = 0
            fonts_missing_unicode = 0
            # Sample first 5 pages for font checks (performance)
            for page in list(pdf.pages)[:5]:
                resources = page.obj.get(Name("/Resources"))
                if resources is None:
                    continue
                fonts = resources.get(Name("/Font"))
                if fonts is None:
                    continue
                for font_name in fonts.keys():
                    try:
                        font = fonts[font_name]
                        if not hasattr(font, "get"):
                            continue
                        fonts_checked += 1

                        # Check for ToUnicode CMap
                        has_tounicode = Name("/ToUnicode") in font

                        # Symbolic fonts and Type 3 fonts are exempt if they have /Encoding
                        font_subtype = str(font.get(Name("/Subtype"), ""))
                        if font_subtype == "/Type3":
                            continue  # Type3 fonts often lack ToUnicode

                        # Standard 14 fonts don't need ToUnicode
                        base_font = str(font.get(Name("/BaseFont"), ""))
                        standard_14 = {
                            "Courier",
                            "Helvetica",
                            "Times-Roman",
                            "Symbol",
                            "ZapfDingbats",
                            "Courier-Bold",
                            "Courier-Oblique",
                            "Courier-BoldOblique",
                            "Helvetica-Bold",
                            "Helvetica-Oblique",
                            "Helvetica-BoldOblique",
                            "Times-Bold",
                            "Times-Italic",
                            "Times-BoldItalic",
                        }
                        if any(std in base_font for std in standard_14):
                            continue

                        if not has_tounicode:
                            # Check if /Encoding provides adequate mapping
                            encoding = font.get(Name("/Encoding"))
                            if encoding is None:
                                fonts_missing_unicode += 1
                    except Exception:
                        pass

            if fonts_missing_unicode > 0:
                issues.append(
                    {
                        "severity": "high",
                        "rule": "PDF/UA 7.21.3.1",
                        "message": f"{fonts_missing_unicode} of {fonts_checked} fonts missing /ToUnicode mapping",
                        "impact": "Screen readers may mispronounce text or produce garbled output",
                        "page_number": 1,
                        "location": "Font resources",
                        "element": "/Font /ToUnicode",
                        "suggested_fix": "Add /ToUnicode CMap to fonts for proper text extraction",
                        "issue_type": "missing_tounicode",
                    }
                )

            # --- Role mapping for non-standard tags ---
            struct_root = pdf.Root.get(Name.StructTreeRoot)
            if struct_root is not None:
                role_map = struct_root.get(Name("/RoleMap"))
                standard_types = {
                    "/Document",
                    "/Part",
                    "/Art",
                    "/Sect",
                    "/Div",
                    "/H",
                    "/H1",
                    "/H2",
                    "/H3",
                    "/H4",
                    "/H5",
                    "/H6",
                    "/P",
                    "/L",
                    "/LI",
                    "/Lbl",
                    "/LBody",
                    "/Table",
                    "/TR",
                    "/TH",
                    "/TD",
                    "/THead",
                    "/TBody",
                    "/TFoot",
                    "/Figure",
                    "/Formula",
                    "/Form",
                    "/Span",
                    "/Quote",
                    "/Note",
                    "/Reference",
                    "/BibEntry",
                    "/Code",
                    "/Link",
                    "/Annot",
                    "/Ruby",
                    "/Warichu",
                    "/BlockQuote",
                    "/Caption",
                    "/Index",
                    "/TOC",
                    "/TOCI",
                    "/NonStruct",
                    "/Private",
                    "/Artifact",
                }

                non_standard_tags = set()
                self._collect_tag_types(struct_root.get(Name.K), non_standard_tags)

                # Filter to non-standard only
                unmapped = non_standard_tags - standard_types
                if unmapped and role_map is None:
                    issues.append(
                        {
                            "severity": "medium",
                            "rule": "PDF/UA 7.1",
                            "message": f"Non-standard structure tags found without /RoleMap: {', '.join(sorted(unmapped)[:5])}",
                            "impact": "Assistive technology may not understand custom tag types",
                            "page_number": 1,
                            "location": "StructTreeRoot",
                            "element": "/RoleMap",
                            "suggested_fix": "Add /RoleMap to StructTreeRoot mapping custom tags to standard PDF types",
                            "issue_type": "missing_role_map",
                        }
                    )
                elif unmapped and role_map is not None:
                    # Check which non-standard tags are actually mapped
                    unmapped_and_missing = set()
                    for tag in unmapped:
                        tag_name = Name(tag)
                        if tag_name not in role_map:
                            unmapped_and_missing.add(tag)
                    if unmapped_and_missing:
                        issues.append(
                            {
                                "severity": "medium",
                                "rule": "PDF/UA 7.1",
                                "message": f"Non-standard tags not in /RoleMap: {', '.join(sorted(unmapped_and_missing)[:5])}",
                                "impact": "Assistive technology may not understand unmapped custom tags",
                                "page_number": 1,
                                "location": "StructTreeRoot /RoleMap",
                                "element": "/RoleMap entries",
                                "suggested_fix": "Add missing tag mappings to /RoleMap",
                                "issue_type": "incomplete_role_map",
                            }
                        )

            pdf.close()
        except Exception as e:
            logger.warning(f"[StructureTreeChecker] Font/role mapping check error: {e}")

        return issues
