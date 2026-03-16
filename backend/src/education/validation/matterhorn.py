"""
Matterhorn Protocol Validator for PDF Accessibility.

Validates PDF files against ~15 core machine-checkable conditions from the
Matterhorn Protocol (derived from PDF/UA ISO 14289). Supports both PDF/UA-1
(ISO 14289-1) and PDF/UA-2 (ISO 14289-2, based on PDF 2.0).

This module is read-only: it opens the PDF, inspects its structure, and
reports findings.

The Matterhorn Protocol defines 136 failure conditions (31 machine-checkable)
across 31 checkpoints. This implementation focuses on the most impactful
conditions that can be reliably detected via structure inspection.

PDF/UA-2 extends UA-1 with additional checks for:
- Namespace validation (PDF 2.0 standard structure namespaces)
- Pronunciation attributes (/Phoneme, /PhoneticAlphabet)
- Ruby annotations (/Ruby, /RB, /RT, /RP)
- MathML (/Formula elements with alt text or associated MathML)
- Associated files (/AF key with /AFRelationship)
- Stricter artifact handling

Usage:
    validator = MatterhornValidator()

    # UA-1 validation (default)
    result = validator.validate("document.pdf")

    # UA-2 validation (runs both UA-1 and UA-2 checks)
    result = validator.validate("document.pdf", ua_version=2)

    # Auto-detect version from XMP metadata
    result = validator.validate("document.pdf", ua_version="auto")

    for cp in result.checkpoints:
        print(f"{cp.id}: {cp.status.value} - {cp.name}")

Dependencies:
    - pikepdf (optional; raises ImportError if missing at validate time)
"""

import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic import BaseModel, computed_field

logger = logging.getLogger(__name__)

# pikepdf is optional — we defer the ImportError to validate() time
try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None  # type: ignore[assignment]
    Name = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CheckpointStatus(str, Enum):
    """Status of a single Matterhorn checkpoint."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class MatterhornCheckpoint(BaseModel):
    """
    A single Matterhorn Protocol checkpoint result.

    Attributes:
        id: Matterhorn checkpoint ID (e.g. "01-003")
        name: Human-readable checkpoint name
        status: PASS, FAIL, or WARNING
        severity: "error" or "warning"
        details: Optional explanation of the failure
        page_number: Optional page where the issue was found
    """

    id: str
    name: str
    status: CheckpointStatus
    severity: str
    details: Optional[str] = None
    page_number: Optional[int] = None


class MatterhornResult(BaseModel):
    """
    Aggregated result of a Matterhorn Protocol validation run.

    Computed properties provide summary statistics and an overall
    compliance level.

    Attributes:
        checkpoints: List of individual checkpoint results.
        ua_version: PDF/UA version used for validation (1 or 2).
    """

    checkpoints: List[MatterhornCheckpoint]
    ua_version: int = 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Total number of checkpoints evaluated."""
        return len(self.checkpoints)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> int:
        """Number of checkpoints that passed."""
        return sum(1 for cp in self.checkpoints if cp.status == CheckpointStatus.PASS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed(self) -> int:
        """Number of checkpoints that failed."""
        return sum(1 for cp in self.checkpoints if cp.status == CheckpointStatus.FAIL)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> int:
        """Number of checkpoints with warnings."""
        return sum(
            1 for cp in self.checkpoints if cp.status == CheckpointStatus.WARNING
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def compliance_level(self) -> str:
        """
        Overall compliance level based on checkpoint results.

        Returns:
            "compliant"     — all checkpoints pass (warnings OK)
            "partial"       — failures <= 20% of total
            "non_compliant" — no checkpoints or > 20% failures
        """
        if self.total == 0:
            return "non_compliant"
        if self.failed == 0:
            return "compliant"
        if self.failed / self.total <= 0.20:
            return "partial"
        return "non_compliant"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class MatterhornValidator:
    """
    Validates a PDF against ~15 core Matterhorn Protocol checkpoints.

    This validator is stateless and read-only. Each call to validate()
    opens the PDF, runs all checks, and returns a MatterhornResult.

    All checks follow the naming convention _check_<feature>(pdf) and
    return a list of MatterhornCheckpoint objects.
    """

    def validate(
        self, pdf_path: str, ua_version: Union[int, str] = 1
    ) -> MatterhornResult:
        """
        Validate a PDF file against Matterhorn Protocol checkpoints.

        Args:
            pdf_path: Path to the PDF file to validate.
            ua_version: PDF/UA version to validate against.
                - 1: Run only UA-1 checks (default, backward compatible).
                - 2: Run both UA-1 and UA-2 checks.
                - "auto": Auto-detect version from XMP metadata
                  (falls back to 1 if not found).

        Returns:
            MatterhornResult with all checkpoint outcomes and the
            resolved ua_version.

        Raises:
            ImportError: If pikepdf is not installed.
            FileNotFoundError: If pdf_path does not exist.
        """
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for Matterhorn validation. "
                "Install with: pip install pikepdf"
            )

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        checkpoints: List[MatterhornCheckpoint] = []

        try:
            with pikepdf.open(str(path)) as pdf:
                # Resolve ua_version if "auto"
                resolved_version = self._resolve_ua_version(pdf, ua_version)

                # Run UA-1 checks (always run)
                checkpoints.extend(self._check_structure_tree(pdf))
                checkpoints.extend(self._check_mark_info(pdf))
                checkpoints.extend(self._check_language(pdf))
                checkpoints.extend(self._check_title(pdf))
                checkpoints.extend(self._check_display_doc_title(pdf))
                checkpoints.extend(self._check_role_mappings(pdf))
                checkpoints.extend(self._check_alt_text_on_figures(pdf))
                checkpoints.extend(self._check_heading_hierarchy(pdf))
                checkpoints.extend(self._check_table_structure(pdf))
                checkpoints.extend(self._check_empty_elements(pdf))
                checkpoints.extend(self._check_pdfua_identifier(pdf))
                checkpoints.extend(self._check_content_marking(pdf))
                checkpoints.extend(self._check_parent_tree(pdf))
                checkpoints.extend(self._check_document_root(pdf))

                # Run UA-2 checks only when version is 2
                if resolved_version == 2:
                    checkpoints.extend(self._check_ua2_namespaces(pdf))
                    checkpoints.extend(self._check_ua2_pronunciation(pdf))
                    checkpoints.extend(self._check_ua2_ruby(pdf))
                    checkpoints.extend(self._check_ua2_mathml(pdf))
                    checkpoints.extend(self._check_ua2_associated_files(pdf))
                    checkpoints.extend(self._check_ua2_artifacts(pdf))
        except Exception:
            logger.exception("Error validating PDF: %s", pdf_path)
            raise

        return MatterhornResult(
            checkpoints=checkpoints, ua_version=resolved_version
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_structure_tree(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 01-003: Document shall contain a StructTreeRoot entry.

        A structure tree is the foundation of PDF accessibility — without it,
        assistive technology cannot determine reading order or semantics.
        """
        has_struct = Name.StructTreeRoot in pdf.Root

        if has_struct:
            return [
                MatterhornCheckpoint(
                    id="01-003",
                    name="Structure tree present",
                    status=CheckpointStatus.PASS,
                    severity="error",
                )
            ]
        return [
            MatterhornCheckpoint(
                id="01-003",
                name="Structure tree present",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="PDF is missing /StructTreeRoot — no tagged structure found",
            )
        ]

    def _check_mark_info(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 01-004: MarkInfo dictionary shall be present with Marked=true.

        This flag indicates the PDF was created with tagged content in mind.
        """
        if Name.MarkInfo not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="01-004",
                    name="Marked content flag",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="PDF is missing /MarkInfo dictionary",
                )
            ]

        mark_info = pdf.Root[Name.MarkInfo]
        if Name.Marked in mark_info and bool(mark_info[Name.Marked]):
            return [
                MatterhornCheckpoint(
                    id="01-004",
                    name="Marked content flag",
                    status=CheckpointStatus.PASS,
                    severity="error",
                )
            ]

        return [
            MatterhornCheckpoint(
                id="01-004",
                name="Marked content flag",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="/MarkInfo exists but /Marked is not set to true",
            )
        ]

    def _check_language(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 06-001: Document catalog shall contain a /Lang entry.

        Screen readers need to know the document language for correct
        pronunciation and text-to-speech behavior.
        """
        if Name.Lang in pdf.Root:
            lang_val = str(pdf.Root[Name.Lang])
            if lang_val.strip():
                return [
                    MatterhornCheckpoint(
                        id="06-001",
                        name="Document language set",
                        status=CheckpointStatus.PASS,
                        severity="error",
                        details=f"Language: {lang_val}",
                    )
                ]

        return [
            MatterhornCheckpoint(
                id="06-001",
                name="Document language set",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="No /Lang entry in document catalog",
            )
        ]

    def _check_title(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 07-001: Document should have a title in metadata.

        Checks both the docinfo dictionary and XMP metadata for dc:title.
        """
        # Check docinfo (legacy metadata)
        title_found = False
        title_value = ""

        if pdf.docinfo and Name.Title in pdf.docinfo:
            raw_title = str(pdf.docinfo[Name.Title]).strip()
            if raw_title:
                title_found = True
                title_value = raw_title

        # Check XMP metadata (preferred for modern PDFs)
        if not title_found:
            try:
                with pdf.open_metadata() as meta:
                    xmp_title = meta.get("dc:title", "")
                    if xmp_title and str(xmp_title).strip():
                        title_found = True
                        title_value = str(xmp_title).strip()
            except Exception:
                # XMP metadata may not be parseable
                pass

        if title_found:
            return [
                MatterhornCheckpoint(
                    id="07-001",
                    name="Document title in metadata",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details=f'Title: "{title_value}"',
                )
            ]

        return [
            MatterhornCheckpoint(
                id="07-001",
                name="Document title in metadata",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="No title found in docinfo or XMP metadata",
            )
        ]

    def _check_display_doc_title(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 07-002: ViewerPreferences should set DisplayDocTitle to true.

        This is a WARNING rather than a failure — it controls whether the
        window title bar shows the document title instead of the filename.
        """
        if Name.ViewerPreferences in pdf.Root:
            viewer_prefs = pdf.Root[Name.ViewerPreferences]
            if Name.DisplayDocTitle in viewer_prefs:
                if bool(viewer_prefs[Name.DisplayDocTitle]):
                    return [
                        MatterhornCheckpoint(
                            id="07-002",
                            name="Display document title enabled",
                            status=CheckpointStatus.PASS,
                            severity="warning",
                        )
                    ]

        return [
            MatterhornCheckpoint(
                id="07-002",
                name="Display document title enabled",
                status=CheckpointStatus.WARNING,
                severity="warning",
                details="ViewerPreferences.DisplayDocTitle is not set to true",
            )
        ]

    def _check_role_mappings(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 09-*: Role mappings should map to valid standard types.

        Custom structure element types must map to standard PDF structure types
        through /RoleMap entries in the structure tree root.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="09-004",
                    name="Valid role mappings",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="Cannot check role mappings — no StructTreeRoot",
                )
            ]

        struct_root = pdf.Root[Name.StructTreeRoot]

        # Standard PDF structure types (PDF 1.7 + PDF 2.0 / PDF/UA-2)
        standard_types = {
            "Document", "Part", "Art", "Sect", "Div", "BlockQuote",
            "Caption", "TOC", "TOCI", "Index", "NonStruct", "Private",
            "H", "H1", "H2", "H3", "H4", "H5", "H6",
            "P", "L", "LI", "Lbl", "LBody",
            "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot",
            "Span", "Quote", "Note", "Reference", "BibEntry", "Code",
            "Link", "Annot", "Ruby", "Warichu",
            "Figure", "Formula", "Form",
            # PDF/UA-2 additions
            "DocumentFragment", "Aside", "Title", "FENote",
            "Sub", "Em", "Strong",
        }

        if "/RoleMap" not in struct_root and Name("/RoleMap") not in struct_root:
            # No role map is fine — all elements use standard types
            return [
                MatterhornCheckpoint(
                    id="09-004",
                    name="Valid role mappings",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No custom role mappings defined (standard types only)",
                )
            ]

        role_map = struct_root.get(Name("/RoleMap"), {})
        invalid_mappings = []

        for custom_type, mapped_type in dict(role_map).items():
            mapped_name = str(mapped_type).lstrip("/")
            if mapped_name not in standard_types:
                invalid_mappings.append(f"{custom_type} -> {mapped_type}")

        if invalid_mappings:
            return [
                MatterhornCheckpoint(
                    id="09-004",
                    name="Valid role mappings",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"Invalid role mappings: {', '.join(invalid_mappings)}"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="09-004",
                name="Valid role mappings",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"{len(dict(role_map))} custom role mappings validated",
            )
        ]

    def _check_alt_text_on_figures(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 13-004: Figure elements shall have /Alt or /ActualText.

        Images marked as Figure in the structure tree must provide alternative
        text for screen reader users.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="13-004",
                    name="Alt text on figures",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="Cannot check figures — no StructTreeRoot",
                )
            ]

        figures = self._find_elements_by_type(pdf, "Figure")

        if not figures:
            return [
                MatterhornCheckpoint(
                    id="13-004",
                    name="Alt text on figures",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No Figure elements found in structure tree",
                )
            ]

        missing_alt = []
        for i, fig in enumerate(figures, 1):
            has_alt = Name.Alt in fig
            has_actual_text = Name.ActualText in fig
            if not has_alt and not has_actual_text:
                missing_alt.append(i)

        if missing_alt:
            return [
                MatterhornCheckpoint(
                    id="13-004",
                    name="Alt text on figures",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"{len(missing_alt)} of {len(figures)} figures missing "
                        f"/Alt or /ActualText"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="13-004",
                name="Alt text on figures",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"All {len(figures)} figures have alt text",
            )
        ]

    def _check_heading_hierarchy(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 14-*: Heading hierarchy should be logical.

        - 14-002: Document should contain at least one heading
        - 14-003: Heading levels should not be skipped (e.g. H1 -> H3)
        """
        results: List[MatterhornCheckpoint] = []

        if Name.StructTreeRoot not in pdf.Root:
            results.append(
                MatterhornCheckpoint(
                    id="14-002",
                    name="Headings present",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details="Cannot check headings — no StructTreeRoot",
                )
            )
            return results

        heading_types = ["H1", "H2", "H3", "H4", "H5", "H6"]
        found_levels: List[int] = []

        for ht in heading_types:
            elements = self._find_elements_by_type(pdf, ht)
            if elements:
                level = int(ht[1])
                found_levels.append(level)

        # Also check for generic /H element
        generic_h = self._find_elements_by_type(pdf, "H")

        if not found_levels and not generic_h:
            results.append(
                MatterhornCheckpoint(
                    id="14-002",
                    name="Headings present",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details="No heading elements (H1-H6) found in structure tree",
                )
            )
            return results

        results.append(
            MatterhornCheckpoint(
                id="14-002",
                name="Headings present",
                status=CheckpointStatus.PASS,
                severity="warning",
                details=f"Heading levels found: {sorted(set(found_levels))}",
            )
        )

        # Check for skipped levels
        if found_levels:
            sorted_levels = sorted(set(found_levels))
            skipped = []
            for i in range(len(sorted_levels) - 1):
                gap = sorted_levels[i + 1] - sorted_levels[i]
                if gap > 1:
                    for missing in range(sorted_levels[i] + 1, sorted_levels[i + 1]):
                        skipped.append(missing)

            if skipped:
                results.append(
                    MatterhornCheckpoint(
                        id="14-003",
                        name="Heading hierarchy logical",
                        status=CheckpointStatus.WARNING,
                        severity="warning",
                        details=f"Skipped heading levels: H{', H'.join(str(s) for s in skipped)}",
                    )
                )
            else:
                results.append(
                    MatterhornCheckpoint(
                        id="14-003",
                        name="Heading hierarchy logical",
                        status=CheckpointStatus.PASS,
                        severity="warning",
                    )
                )

        return results

    def _check_table_structure(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 15-*: Tables should have proper structure.

        - Tables should contain TR (table row) elements
        - Tables should have TH (header) or THead elements
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="15-003",
                    name="Table structure",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No structure tree — table check skipped",
                )
            ]

        tables = self._find_elements_by_type(pdf, "Table")

        if not tables:
            return [
                MatterhornCheckpoint(
                    id="15-003",
                    name="Table structure",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No Table elements found in structure tree",
                )
            ]

        issues = []
        for i, table in enumerate(tables, 1):
            children = self._get_children(table)
            child_types = [self._get_element_type(c) for c in children if c is not None]

            has_tr = "TR" in child_types
            has_thead = "THead" in child_types

            if not has_tr:
                issues.append(f"Table {i}: missing TR elements")
            if not has_thead:
                # Check for TH inside any TR
                has_th = False
                for child in children:
                    if self._get_element_type(child) == "TR":
                        tr_children = self._get_children(child)
                        tr_child_types = [
                            self._get_element_type(c)
                            for c in tr_children
                            if c is not None
                        ]
                        if "TH" in tr_child_types:
                            has_th = True
                            break
                if not has_th:
                    issues.append(f"Table {i}: no THead or TH elements")

        if issues:
            return [
                MatterhornCheckpoint(
                    id="15-003",
                    name="Table structure",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="; ".join(issues),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="15-003",
                name="Table structure",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"All {len(tables)} tables have proper structure",
            )
        ]

    def _check_empty_elements(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 09-005: Structure elements should not be empty.

        Empty elements (with no /K children) can confuse assistive technology
        and indicate incomplete tagging.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="09-005",
                    name="No empty structure elements",
                    status=CheckpointStatus.PASS,
                    severity="warning",
                    details="No structure tree — empty element check skipped",
                )
            ]

        struct_root = pdf.Root[Name.StructTreeRoot]
        children = self._get_children(struct_root)

        # Only check top-level structural elements (not deeply nested)
        # to avoid false positives on intentionally empty containers
        empty_count = 0
        checked_count = 0
        semantic_types = {
            "P", "H1", "H2", "H3", "H4", "H5", "H6",
            "L", "LI", "Table", "TR", "Figure",
        }

        for child in children:
            elem_type = self._get_element_type(child)
            if elem_type in semantic_types:
                checked_count += 1
                child_kids = self._get_children(child)
                if not child_kids:
                    empty_count += 1

        if empty_count > 0:
            return [
                MatterhornCheckpoint(
                    id="09-005",
                    name="No empty structure elements",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details=f"{empty_count} empty semantic elements found at top level",
                )
            ]

        return [
            MatterhornCheckpoint(
                id="09-005",
                name="No empty structure elements",
                status=CheckpointStatus.PASS,
                severity="warning",
                details=f"Checked {checked_count} top-level semantic elements",
            )
        ]

    def _check_pdfua_identifier(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 06-003: PDF/UA identifier should be present in XMP metadata.

        The pdfuaid:part entry in XMP metadata declares PDF/UA conformance level.
        """
        try:
            with pdf.open_metadata() as meta:
                xmp_raw = str(meta)
                # Look for pdfuaid:part in the XMP
                if "pdfuaid:part" in xmp_raw or "pdfaid:part" in xmp_raw:
                    return [
                        MatterhornCheckpoint(
                            id="06-003",
                            name="PDF/UA identifier",
                            status=CheckpointStatus.PASS,
                            severity="error",
                            details="PDF/UA identifier found in XMP metadata",
                        )
                    ]
        except Exception:
            # XMP might not be parseable
            pass

        return [
            MatterhornCheckpoint(
                id="06-003",
                name="PDF/UA identifier",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="No PDF/UA identifier (pdfuaid:part) in XMP metadata",
            )
        ]

    def _check_content_marking(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 01-001: Content streams shall contain marked content operators.

        At least one page must have BDC/BMC + EMC operators in its content
        stream, indicating that content is associated with structure elements
        via marked-content sequences.
        """
        try:
            for page in pdf.pages:
                ops = list(pikepdf.parse_content_stream(page))
                has_marking = any(
                    str(op.operator) in ("BDC", "BMC") for op in ops
                )
                if has_marking:
                    return [
                        MatterhornCheckpoint(
                            id="01-001",
                            name="Content marked in streams",
                            status=CheckpointStatus.PASS,
                            severity="error",
                            details="At least one page contains marked content operators",
                        )
                    ]
        except Exception:
            logger.debug("Error parsing content streams for 01-001 check")

        return [
            MatterhornCheckpoint(
                id="01-001",
                name="Content marked in streams",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="No pages contain marked content operators (BDC/BMC + EMC)",
            )
        ]

    def _check_parent_tree(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 01-002: ParentTree shall map MCIDs to structure elements.

        The ParentTree (a number tree in StructTreeRoot) links marked-content
        identifiers back to their parent structure elements. Its /Nums array
        must not be empty.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="01-002",
                    name="ParentTree populated",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="Cannot check ParentTree — no StructTreeRoot",
                )
            ]

        struct_root = pdf.Root[Name.StructTreeRoot]
        parent_tree = struct_root.get(Name.ParentTree)

        if parent_tree is None:
            return [
                MatterhornCheckpoint(
                    id="01-002",
                    name="ParentTree populated",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="StructTreeRoot has no /ParentTree entry",
                )
            ]

        nums = parent_tree.get(Name.Nums, pikepdf.Array([]))
        if len(nums) > 0:
            return [
                MatterhornCheckpoint(
                    id="01-002",
                    name="ParentTree populated",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details=f"ParentTree /Nums contains {len(nums)} entries",
                )
            ]

        return [
            MatterhornCheckpoint(
                id="01-002",
                name="ParentTree populated",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="ParentTree /Nums array is empty — MCIDs are not mapped",
            )
        ]

    def _check_document_root(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        Matterhorn 09-001: Structure tree shall have a Document root element.

        The top-level child (/K) of StructTreeRoot must be (or contain) a
        structure element with /S of /Document or /Part.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="09-001",
                    name="Document root element",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="Cannot check document root — no StructTreeRoot",
                )
            ]

        struct_root = pdf.Root[Name.StructTreeRoot]
        kids = struct_root.get(Name.K)

        if kids is None:
            return [
                MatterhornCheckpoint(
                    id="09-001",
                    name="Document root element",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="StructTreeRoot has no /K entry",
                )
            ]

        # /K can be a single element or an array
        elements = kids if isinstance(kids, pikepdf.Array) else [kids]

        for elem in elements:
            try:
                if Name.S in elem:
                    tag = str(elem[Name.S])
                    if tag in ("/Document", "/Part"):
                        return [
                            MatterhornCheckpoint(
                                id="09-001",
                                name="Document root element",
                                status=CheckpointStatus.PASS,
                                severity="error",
                                details=f"Root structure element is {tag}",
                            )
                        ]
            except Exception:
                continue

        return [
            MatterhornCheckpoint(
                id="09-001",
                name="Document root element",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="Root of structure tree is not /Document or /Part",
            )
        ]

    # ------------------------------------------------------------------
    # UA version detection
    # ------------------------------------------------------------------

    def _resolve_ua_version(
        self, pdf: Any, ua_version: Union[int, str]
    ) -> int:
        """
        Resolve the effective UA version for validation.

        Args:
            pdf: An open pikepdf.Pdf object.
            ua_version: Caller-specified version (1, 2, or "auto").

        Returns:
            Resolved integer version (1 or 2).
        """
        if isinstance(ua_version, int):
            return ua_version

        if ua_version == "auto":
            return self._detect_ua_version(pdf)

        # Fallback for unexpected values
        return 1

    def _detect_ua_version(self, pdf: Any) -> int:
        """
        Detect PDF/UA version from XMP metadata.

        Looks for ``pdfuaid:part`` in the XMP metadata stream. The value
        indicates the conformance level:
        - "1" -> PDF/UA-1 (ISO 14289-1)
        - "2" -> PDF/UA-2 (ISO 14289-2)

        Returns:
            Detected version (1 or 2), defaulting to 1 if not found.
        """
        try:
            with pdf.open_metadata() as meta:
                xmp_raw = str(meta)

                # Look for pdfuaid:part value in the XMP XML
                # Match patterns like <pdfuaid:part>2</pdfuaid:part>
                # or pdfuaid:part="2"
                match = re.search(
                    r"pdfuaid:part[\">\s]*(\d+)", xmp_raw
                )
                if match:
                    version = int(match.group(1))
                    if version in (1, 2):
                        return version

                # Also check via pikepdf metadata API
                part_value = meta.get("pdfuaid:part", "")
                if part_value:
                    try:
                        version = int(str(part_value).strip())
                        if version in (1, 2):
                            return version
                    except (ValueError, TypeError):
                        pass
        except Exception:
            # XMP metadata may not be parseable
            logger.debug("Could not read XMP metadata for UA version detection")

        return 1

    # ------------------------------------------------------------------
    # PDF/UA-2 checks
    # ------------------------------------------------------------------

    def _check_ua2_namespaces(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Structure elements should use PDF 2.0 standard namespaces.

        PDF/UA-2 requires that the structure tree root declares standard
        namespaces via a /Namespaces array. Each namespace dictionary should
        have a /NS entry with a recognized namespace URI.

        Checkpoint ID: ua2-01-001
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-01-001",
                    name="PDF 2.0 namespace declarations",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="Cannot check namespaces — no StructTreeRoot",
                )
            ]

        struct_root = pdf.Root[Name.StructTreeRoot]
        ns_key = Name("/Namespaces")

        if ns_key not in struct_root:
            return [
                MatterhornCheckpoint(
                    id="ua2-01-001",
                    name="PDF 2.0 namespace declarations",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details=(
                        "No /Namespaces array in StructTreeRoot — "
                        "PDF 2.0 namespace declarations are recommended for UA-2"
                    ),
                )
            ]

        namespaces = struct_root[ns_key]
        if not isinstance(namespaces, pikepdf.Array) or len(namespaces) == 0:
            return [
                MatterhornCheckpoint(
                    id="ua2-01-001",
                    name="PDF 2.0 namespace declarations",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details="/Namespaces array is empty",
                )
            ]

        # Validate each namespace entry has /NS
        valid_count = 0
        invalid_count = 0
        for ns_entry in namespaces:
            try:
                if Name("/NS") in ns_entry:
                    valid_count += 1
                else:
                    invalid_count += 1
            except (TypeError, AttributeError):
                invalid_count += 1

        if invalid_count > 0:
            return [
                MatterhornCheckpoint(
                    id="ua2-01-001",
                    name="PDF 2.0 namespace declarations",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"{invalid_count} namespace entries missing /NS URI; "
                        f"{valid_count} valid"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="ua2-01-001",
                name="PDF 2.0 namespace declarations",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"{valid_count} namespace declarations validated",
            )
        ]

    def _check_ua2_pronunciation(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Pronunciation attributes should be well-formed.

        When /Phoneme is present on a structure element, /PhoneticAlphabet
        should also be specified to indicate the phonetic system used
        (e.g., "ipa" for International Phonetic Alphabet).

        Checkpoint ID: ua2-02-001
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-02-001",
                    name="Pronunciation attributes well-formed",
                    status=CheckpointStatus.PASS,
                    severity="warning",
                    details="No structure tree — pronunciation check skipped",
                )
            ]

        phoneme_elements = self._find_elements_with_key(pdf, "/Phoneme")

        if not phoneme_elements:
            return [
                MatterhornCheckpoint(
                    id="ua2-02-001",
                    name="Pronunciation attributes well-formed",
                    status=CheckpointStatus.PASS,
                    severity="warning",
                    details="No elements with /Phoneme attribute found",
                )
            ]

        missing_alphabet = 0
        for elem in phoneme_elements:
            if Name("/PhoneticAlphabet") not in elem:
                missing_alphabet += 1

        if missing_alphabet > 0:
            return [
                MatterhornCheckpoint(
                    id="ua2-02-001",
                    name="Pronunciation attributes well-formed",
                    status=CheckpointStatus.WARNING,
                    severity="warning",
                    details=(
                        f"{missing_alphabet} of {len(phoneme_elements)} elements "
                        f"with /Phoneme are missing /PhoneticAlphabet"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="ua2-02-001",
                name="Pronunciation attributes well-formed",
                status=CheckpointStatus.PASS,
                severity="warning",
                details=(
                    f"All {len(phoneme_elements)} pronunciation elements "
                    f"have /PhoneticAlphabet"
                ),
            )
        ]

    def _check_ua2_ruby(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Ruby annotation structure elements should be well-formed.

        A /Ruby structure element must contain at least /RB (ruby base)
        and /RT (ruby text) children. /RP (ruby parenthesis) is optional.

        Checkpoint ID: ua2-03-001
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-03-001",
                    name="Ruby annotation structure",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No structure tree — ruby check skipped",
                )
            ]

        ruby_elements = self._find_elements_by_type(pdf, "Ruby")

        if not ruby_elements:
            return [
                MatterhornCheckpoint(
                    id="ua2-03-001",
                    name="Ruby annotation structure",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No Ruby elements found in structure tree",
                )
            ]

        invalid_rubies = []
        for i, ruby in enumerate(ruby_elements, 1):
            children = self._get_children(ruby)
            child_types = {
                self._get_element_type(c) for c in children if c is not None
            }
            has_rb = "RB" in child_types
            has_rt = "RT" in child_types

            if not has_rb or not has_rt:
                missing = []
                if not has_rb:
                    missing.append("RB")
                if not has_rt:
                    missing.append("RT")
                invalid_rubies.append(
                    f"Ruby {i}: missing {', '.join(missing)}"
                )

        if invalid_rubies:
            return [
                MatterhornCheckpoint(
                    id="ua2-03-001",
                    name="Ruby annotation structure",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"{len(invalid_rubies)} of {len(ruby_elements)} Ruby "
                        f"elements malformed: {'; '.join(invalid_rubies)}"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="ua2-03-001",
                name="Ruby annotation structure",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"All {len(ruby_elements)} Ruby elements have RB and RT children",
            )
        ]

    def _check_ua2_mathml(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Formula elements should have accessible representation.

        In PDF/UA-2, /Formula structure elements should have either:
        - /Alt text providing a textual description of the formula, or
        - /AF (associated file) referencing a MathML representation, or
        - /ActualText with the formula content

        Checkpoint ID: ua2-04-001
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-04-001",
                    name="Formula/MathML accessibility",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No structure tree — MathML check skipped",
                )
            ]

        formulas = self._find_elements_by_type(pdf, "Formula")

        if not formulas:
            return [
                MatterhornCheckpoint(
                    id="ua2-04-001",
                    name="Formula/MathML accessibility",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No Formula elements found in structure tree",
                )
            ]

        inaccessible = []
        for i, formula in enumerate(formulas, 1):
            has_alt = Name.Alt in formula
            has_actual_text = Name.ActualText in formula
            has_af = Name("/AF") in formula

            if not has_alt and not has_actual_text and not has_af:
                inaccessible.append(i)

        if inaccessible:
            return [
                MatterhornCheckpoint(
                    id="ua2-04-001",
                    name="Formula/MathML accessibility",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"{len(inaccessible)} of {len(formulas)} Formula elements "
                        f"missing /Alt, /ActualText, or /AF (MathML)"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="ua2-04-001",
                name="Formula/MathML accessibility",
                status=CheckpointStatus.PASS,
                severity="error",
                details=(
                    f"All {len(formulas)} Formula elements have accessible "
                    f"representation"
                ),
            )
        ]

    def _check_ua2_associated_files(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Associated files must have /AFRelationship.

        PDF 2.0 requires that file attachments declared via /AF (associated
        files) include an /AFRelationship entry specifying the relationship
        type (e.g., /Supplement, /Source, /Data, /Alternative).

        Checkpoint ID: ua2-05-001
        """
        af_key = Name("/AF")

        if af_key not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-05-001",
                    name="Associated file relationships",
                    status=CheckpointStatus.PASS,
                    severity="error",
                    details="No associated files (/AF) in document catalog",
                )
            ]

        af_array = pdf.Root[af_key]
        if not isinstance(af_array, pikepdf.Array):
            # Single entry
            af_array = [af_array]

        missing_relationship = 0
        total = 0

        for filespec in af_array:
            total += 1
            try:
                if Name("/AFRelationship") not in filespec:
                    missing_relationship += 1
            except (TypeError, AttributeError):
                missing_relationship += 1

        if missing_relationship > 0:
            return [
                MatterhornCheckpoint(
                    id="ua2-05-001",
                    name="Associated file relationships",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details=(
                        f"{missing_relationship} of {total} associated files "
                        f"missing /AFRelationship"
                    ),
                )
            ]

        return [
            MatterhornCheckpoint(
                id="ua2-05-001",
                name="Associated file relationships",
                status=CheckpointStatus.PASS,
                severity="error",
                details=f"All {total} associated files have /AFRelationship",
            )
        ]

    def _check_ua2_artifacts(self, pdf: Any) -> List[MatterhornCheckpoint]:
        """
        UA-2 check: Artifacts in the structure tree should be properly typed.

        PDF/UA-2 requires stricter artifact handling. Artifact structure
        elements (if present in the tree) should have /Subtype indicating
        the artifact type (e.g., /Pagination, /Layout, /Background).

        In practice, artifacts should generally NOT appear in the structure
        tree (they should be marked content outside the tree), so finding
        Artifact-typed structure elements is itself a concern.

        Checkpoint ID: ua2-06-001
        """
        if Name.StructTreeRoot not in pdf.Root:
            return [
                MatterhornCheckpoint(
                    id="ua2-06-001",
                    name="Artifact handling",
                    status=CheckpointStatus.PASS,
                    severity="warning",
                    details="No structure tree — artifact check skipped",
                )
            ]

        artifact_elements = self._find_elements_by_type(pdf, "Artifact")

        if not artifact_elements:
            return [
                MatterhornCheckpoint(
                    id="ua2-06-001",
                    name="Artifact handling",
                    status=CheckpointStatus.PASS,
                    severity="warning",
                    details="No Artifact elements in structure tree (correct behavior)",
                )
            ]

        # Artifacts in structure tree is itself problematic in UA-2
        untyped = 0
        for artifact in artifact_elements:
            if Name.Subtype not in artifact and Name("/Subtype") not in artifact:
                untyped += 1

        return [
            MatterhornCheckpoint(
                id="ua2-06-001",
                name="Artifact handling",
                status=CheckpointStatus.WARNING,
                severity="warning",
                details=(
                    f"{len(artifact_elements)} Artifact elements found in structure "
                    f"tree (should be marked content outside tree); "
                    f"{untyped} missing /Subtype"
                ),
            )
        ]

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _find_elements_by_type(
        self, pdf: Any, element_type: str
    ) -> List[Any]:
        """
        Walk the structure tree and find all elements of a given type.

        Args:
            pdf: An open pikepdf.Pdf object.
            element_type: The structure type to search for (e.g. "Figure", "H1").

        Returns:
            List of pikepdf Dictionary objects matching the type.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return []

        struct_root = pdf.Root[Name.StructTreeRoot]
        results: List[Any] = []
        self._walk_tree(struct_root, element_type, results)
        return results

    def _walk_tree(
        self, element: Any, target_type: str, results: List[Any]
    ) -> None:
        """Recursively walk a structure element and collect matches."""
        elem_type = self._get_element_type(element)
        if elem_type == target_type:
            results.append(element)

        for child in self._get_children(element):
            if child is not None:
                try:
                    self._walk_tree(child, target_type, results)
                except (TypeError, AttributeError):
                    # Skip non-dictionary children (MCR references, etc.)
                    continue

    def _get_element_type(self, element: Any) -> Optional[str]:
        """
        Get the /S (structure type) value of an element.

        Returns:
            The type name as a string (e.g. "Figure", "P"), or None.
        """
        try:
            if Name.S in element:
                return str(element[Name.S]).lstrip("/")
        except (TypeError, AttributeError):
            pass
        return None

    def _get_children(self, element: Any) -> List[Any]:
        """
        Get the /K (kids) array of an element.

        Handles both single-element /K values and arrays.

        Returns:
            List of child elements (may include MCR references).
        """
        try:
            if Name.K not in element:
                return []
            kids = element[Name.K]
            # /K can be a single item or an array
            if isinstance(kids, pikepdf.Array):
                return list(kids)
            return [kids]
        except (TypeError, AttributeError):
            return []

    def _find_elements_with_key(
        self, pdf: Any, key: str
    ) -> List[Any]:
        """
        Walk the structure tree and find all elements containing a given key.

        Args:
            pdf: An open pikepdf.Pdf object.
            key: The dictionary key to search for (e.g. "/Phoneme").

        Returns:
            List of pikepdf Dictionary objects that contain the key.
        """
        if Name.StructTreeRoot not in pdf.Root:
            return []

        struct_root = pdf.Root[Name.StructTreeRoot]
        results: List[Any] = []
        self._walk_tree_for_key(struct_root, Name(key), results)
        return results

    def _walk_tree_for_key(
        self, element: Any, target_key: Any, results: List[Any]
    ) -> None:
        """Recursively walk a structure element and collect elements with key."""
        try:
            if target_key in element:
                results.append(element)
        except (TypeError, AttributeError):
            pass

        for child in self._get_children(element):
            if child is not None:
                try:
                    self._walk_tree_for_key(child, target_key, results)
                except (TypeError, AttributeError):
                    continue
