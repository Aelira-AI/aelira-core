"""PDF/UA version detection and compliance checking."""

import logging
from typing import Dict

try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

from .models import PDFUAComplianceResult, PDFUAVersion

logger = logging.getLogger(__name__)


class PDFUADetector:
    """Detect PDF/UA-1 vs PDF/UA-2 compliance level.

    PDF/UA-2 (ISO 14289-2:2024) introduces several new features:
    - Additional structure element namespaces (Math, PrintField, etc.)
    - Pronunciation hints for assistive technology
    - Ruby text for East Asian languages
    - New elements: DocumentFragment, Aside, FENote, Sub, Em, Strong
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, file_path: str) -> PDFUAComplianceResult:
        """Detect PDF/UA compliance version and validate requirements.

        Args:
            file_path: Path to PDF file

        Returns:
            PDFUAComplianceResult with version detection and compliance details
        """
        # Default result for non-compliant or error cases
        default_result = PDFUAComplianceResult(
            version_detected=PDFUAVersion.NONE,
            ua2_features={
                "namespaces": False,
                "pronunciation": False,
                "ruby": False,
                "document_fragment": False,
                "aside": False,
                "fenote": False,
                "emphasis_elements": False,
            },
            ua2_issues=[],
            upgrade_recommendations=[],
            conformance_level=None,
            pdfua_identifier=None,
        )

        if not HAS_PIKEPDF:
            logger.warning("pikepdf not available, skipping PDF/UA version detection")
            default_result.ua2_issues.append(
                "pikepdf not installed - cannot check PDF/UA compliance"
            )
            return default_result

        try:
            with pikepdf.open(file_path) as pdf:
                ua_version = PDFUAVersion.NONE
                pdfua_part = None
                conformance = None
                ua2_features = {
                    "namespaces": False,
                    "pronunciation": False,
                    "ruby": False,
                    "document_fragment": False,
                    "aside": False,
                    "fenote": False,
                    "emphasis_elements": False,
                }
                ua2_issues = []
                upgrade_recommendations = []

                # 1. Check XMP metadata for pdfuaid:part
                try:
                    with pdf.open_metadata() as meta:
                        # pdfuaid:part = 1 for UA-1, part = 2 for UA-2
                        pdfua_part_raw = meta.get(
                            "{http://www.aiim.org/pdfua/ns/id/}part"
                        )
                        if pdfua_part_raw:
                            pdfua_part = str(pdfua_part_raw)
                            if pdfua_part == "2":
                                ua_version = PDFUAVersion.UA2
                            elif pdfua_part == "1":
                                ua_version = PDFUAVersion.UA1
                            logger.info(
                                f"[PDFUADetector] Detected pdfuaid:part={pdfua_part}"
                            )

                        # Check for conformance level (rev in UA-2)
                        pdfua_rev = meta.get("{http://www.aiim.org/pdfua/ns/id/}rev")
                        if pdfua_rev:
                            conformance = str(pdfua_rev)

                        # Also check for amd (amendment) in UA-2
                        pdfua_amd = meta.get("{http://www.aiim.org/pdfua/ns/id/}amd")
                        if pdfua_amd and conformance:
                            conformance = f"{conformance}+amd{pdfua_amd}"
                except Exception as e:
                    logger.warning(f"[PDFUADetector] Could not read XMP metadata: {e}")

                # 2. Check for UA-2 specific structure elements
                if Name.StructTreeRoot in pdf.Root:
                    struct_root = pdf.Root[Name.StructTreeRoot]

                    # Check for namespaces (UA-2 feature)
                    if hasattr(struct_root, "get"):
                        # Check for namespace dictionary (UA-2)
                        ns_dict = struct_root.get(Name("/Namespaces"))
                        if ns_dict:
                            ua2_features["namespaces"] = True
                            logger.info(
                                "[PDFUADetector] Found UA-2 namespace dictionary"
                            )

                    # Check for UA-2 specific structure elements in the tree
                    ua2_elements = self._find_ua2_elements(struct_root)
                    if ua2_elements.get("DocumentFragment"):
                        ua2_features["document_fragment"] = True
                    if ua2_elements.get("Aside"):
                        ua2_features["aside"] = True
                    if ua2_elements.get("FENote"):
                        ua2_features["fenote"] = True
                    if ua2_elements.get("Em") or ua2_elements.get("Strong"):
                        ua2_features["emphasis_elements"] = True
                    if (
                        ua2_elements.get("Ruby")
                        or ua2_elements.get("RB")
                        or ua2_elements.get("RT")
                    ):
                        ua2_features["ruby"] = True

                    # Check for pronunciation hints (Phoneme element or /ActualText with pronunciation)
                    if ua2_elements.get("Phoneme"):
                        ua2_features["pronunciation"] = True

                # 3. Generate UA-2 compliance issues and upgrade recommendations
                if ua_version == PDFUAVersion.UA1:
                    # Check what UA-2 features are missing for upgrade
                    if not ua2_features["namespaces"]:
                        upgrade_recommendations.append(
                            "Add PDF 2.0 namespace dictionary to structure tree for UA-2 compliance"
                        )
                    if not ua2_features["emphasis_elements"]:
                        upgrade_recommendations.append(
                            "Use <Em> and <Strong> elements instead of inline styling for emphasis"
                        )
                    upgrade_recommendations.append(
                        "Update pdfuaid:part to '2' in XMP metadata after implementing UA-2 features"
                    )

                elif ua_version == PDFUAVersion.UA2:
                    # Validate UA-2 requirements
                    if not ua2_features["namespaces"]:
                        ua2_issues.append(
                            "UA-2 requires namespace dictionary in structure tree"
                        )
                    # Check for proper structure tree (required for both UA-1 and UA-2)
                    if Name.StructTreeRoot not in pdf.Root:
                        ua2_issues.append(
                            "Missing required StructTreeRoot for PDF/UA compliance"
                        )

                elif ua_version == PDFUAVersion.NONE:
                    # Not PDF/UA compliant
                    ua2_issues.append(
                        "Document does not declare PDF/UA compliance in XMP metadata"
                    )
                    if Name.StructTreeRoot not in pdf.Root:
                        ua2_issues.append("No structure tree - document is not tagged")
                        upgrade_recommendations.append(
                            "Add structure tree with proper tags (P, H1-H6, Table, Figure, etc.)"
                        )
                    else:
                        upgrade_recommendations.append(
                            "Add pdfuaid:part='1' or '2' to XMP metadata to declare UA compliance"
                        )

                return PDFUAComplianceResult(
                    version_detected=ua_version,
                    ua2_features=ua2_features,
                    ua2_issues=ua2_issues,
                    upgrade_recommendations=upgrade_recommendations,
                    conformance_level=conformance,
                    pdfua_identifier=pdfua_part,
                )

        except Exception as e:
            logger.error(f"[PDFUADetector] Error detecting PDF/UA version: {e}")
            default_result.ua2_issues.append(f"Error reading PDF: {str(e)}")
            return default_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_ua2_elements(self, struct_root) -> Dict[str, bool]:
        """Recursively search structure tree for UA-2 specific elements.

        UA-2 adds: DocumentFragment, Aside, FENote, Sub, Em, Strong, Ruby, RB, RT, Phoneme

        Args:
            struct_root: Structure tree root element

        Returns:
            Dictionary of element names to boolean (True if found)
        """
        ua2_element_types = {
            "DocumentFragment": False,
            "Aside": False,
            "FENote": False,
            "Sub": False,
            "Em": False,
            "Strong": False,
            "Ruby": False,
            "RB": False,
            "RT": False,
            "Phoneme": False,
        }

        def search_element(elem):
            """Recursively search for UA-2 elements."""
            if hasattr(elem, "S"):
                elem_type = str(elem.S).lstrip("/")
                if elem_type in ua2_element_types:
                    ua2_element_types[elem_type] = True

            if hasattr(elem, "K"):
                kids = elem.K
                if hasattr(kids, "__iter__"):
                    for kid in kids:
                        if hasattr(kid, "S") or hasattr(kid, "K"):
                            search_element(kid)
                elif hasattr(kids, "S") or hasattr(kids, "K"):
                    search_element(kids)

        # Start search from struct root kids
        if hasattr(struct_root, "get") and Name.K in struct_root:
            kids = struct_root[Name.K]
            if hasattr(kids, "__iter__"):
                for kid in kids:
                    search_element(kid)
            elif hasattr(kids, "S") or hasattr(kids, "K"):
                search_element(kids)

        return ua2_element_types
