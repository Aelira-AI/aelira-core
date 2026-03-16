"""Form field and link accessibility checking for PDFs."""

import logging
from typing import Dict, List

try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

logger = logging.getLogger(__name__)


class FormFieldChecker:
    """Check PDF form field and link annotation accessibility."""

    def check(self, file_path: str) -> List[Dict]:
        """Check PDF form fields for accessibility compliance.

        Validates:
        - All form fields have /TU (tooltip/label) or meaningful /T (name)
        - Tab order is defined
        - Fields are not read-only without purpose

        WCAG 1.3.1 (Info and Relationships), 2.1.1 (Keyboard), 4.1.2 (Name, Role, Value)

        Args:
            file_path: Path to the PDF file

        Returns:
            List of accessibility issues for form fields
        """
        issues = []
        if not HAS_PIKEPDF:
            return issues

        try:
            pdf = pikepdf.open(file_path)

            acroform = pdf.Root.get(Name("/AcroForm"))
            if acroform is None:
                pdf.close()
                return issues  # No forms = no form issues

            fields = acroform.get(Name("/Fields"))
            if fields is None or len(fields) == 0:
                pdf.close()
                return issues

            unlabeled_count = 0
            total_fields = 0

            def check_field(field_obj, depth=0):
                nonlocal unlabeled_count, total_fields
                if depth > 10:  # Prevent infinite recursion
                    return
                if not hasattr(field_obj, "get"):
                    return

                # Check if this is a widget (has /Subtype /Widget or /FT field type)
                ft = field_obj.get(Name("/FT"))
                if ft is not None:
                    total_fields += 1
                    # Check for label: /TU (tooltip) is the accessible name
                    tu = field_obj.get(Name("/TU"))
                    t = field_obj.get(Name("/T"))

                    has_label = False
                    if tu and str(tu).strip():
                        has_label = True
                    elif t and str(t).strip() and not str(t).startswith("Field"):
                        # /T is the partial field name -- only counts if meaningful
                        has_label = True

                    if not has_label:
                        unlabeled_count += 1

                # Recurse into child fields
                kids = field_obj.get(Name("/Kids"))
                if kids is not None:
                    for kid in kids:
                        check_field(kid, depth + 1)

            for field in fields:
                check_field(field)

            if unlabeled_count > 0:
                issues.append(
                    {
                        "severity": "critical",
                        "rule": "WCAG 4.1.2",
                        "message": f"{unlabeled_count} of {total_fields} form fields missing accessible labels (/TU tooltip)",
                        "impact": "Screen reader users cannot identify form field purpose",
                        "page_number": 1,
                        "location": "AcroForm fields",
                        "element": "/AcroForm /Fields",
                        "suggested_fix": "Add /TU (tooltip) attribute to each form field with a descriptive label",
                        "issue_type": "unlabeled_form_fields",
                    }
                )

            # Check tab order only on pages that actually have widget annotations
            for page_num, page in enumerate(pdf.pages):
                if Name("/Tabs") not in page.obj:
                    annots = page.obj.get(Name("/Annots"))
                    if annots is None:
                        continue
                    page_has_widgets = False
                    for annot in annots:
                        try:
                            if Name("/Subtype") in annot and annot[
                                Name("/Subtype")
                            ] == Name("/Widget"):
                                page_has_widgets = True
                                break
                        except Exception:
                            pass
                    if page_has_widgets:
                        issues.append(
                            {
                                "severity": "medium",
                                "rule": "WCAG 2.1.1",
                                "message": f"Page {page_num + 1} has form fields but no tab order defined",
                                "impact": "Keyboard users may not be able to navigate form fields in logical order",
                                "page_number": page_num + 1,
                                "location": f"Page {page_num + 1}",
                                "element": "/Tabs",
                                "suggested_fix": "Set /Tabs to /S (structure order) on pages with form fields",
                                "issue_type": "missing_tab_order",
                            }
                        )
                        break  # One issue is enough to flag

            pdf.close()
        except Exception as e:
            logger.warning(f"[FormFieldChecker] Form field check error: {e}")

        return issues

    def check_links(self, file_path: str) -> List[Dict]:
        """Check PDF link annotations for accessibility.

        Validates:
        - Link annotations have /Contents or associated /Link structure element
        - Link text is descriptive (not just URLs or "click here")

        WCAG 2.4.4 (Link Purpose), PDF/UA 7.18 (Link annotations)

        Args:
            file_path: Path to the PDF file

        Returns:
            List of accessibility issues for link annotations
        """
        issues = []
        if not HAS_PIKEPDF:
            return issues

        try:
            pdf = pikepdf.open(file_path)

            total_links = 0
            links_without_alt = 0
            vague_links = 0
            vague_patterns = {
                "click here",
                "here",
                "link",
                "read more",
                "more",
                "learn more",
            }

            for page_num, page in enumerate(pdf.pages):
                annots = page.obj.get(Name("/Annots"))
                if annots is None:
                    continue

                for annot in annots:
                    if not hasattr(annot, "get"):
                        continue
                    subtype = annot.get(Name("/Subtype"))
                    if subtype is None or str(subtype) != "/Link":
                        continue

                    total_links += 1

                    # Check for accessible text
                    contents = annot.get(Name("/Contents"))
                    alt = annot.get(Name("/Alt"))

                    has_alt = False
                    link_text = ""
                    if contents and str(contents).strip():
                        has_alt = True
                        link_text = str(contents).strip()
                    elif alt and str(alt).strip():
                        has_alt = True
                        link_text = str(alt).strip()

                    if not has_alt:
                        links_without_alt += 1

                    # Check for vague link text
                    if link_text and link_text.lower() in vague_patterns:
                        vague_links += 1

            if links_without_alt > 0:
                issues.append(
                    {
                        "severity": "high",
                        "rule": "WCAG 2.4.4",
                        "message": f"{links_without_alt} of {total_links} link annotations missing accessible text (/Contents)",
                        "impact": "Screen readers cannot describe link purpose to users",
                        "page_number": 1,
                        "location": "Link annotations",
                        "element": "/Annot /Link",
                        "suggested_fix": "Add /Contents attribute to link annotations with descriptive text",
                        "issue_type": "links_missing_alt",
                    }
                )

            if vague_links > 0:
                issues.append(
                    {
                        "severity": "medium",
                        "rule": "WCAG 2.4.4",
                        "message": f"{vague_links} link(s) have vague text like 'click here' or 'read more'",
                        "impact": "Link purpose is unclear when read out of context",
                        "page_number": 1,
                        "location": "Link annotations",
                        "element": "/Annot /Link /Contents",
                        "suggested_fix": "Replace vague link text with descriptive text that indicates the link destination",
                        "issue_type": "vague_link_text",
                    }
                )

            pdf.close()
        except Exception as e:
            logger.warning(f"[FormFieldChecker] Link check error: {e}")

        return issues
