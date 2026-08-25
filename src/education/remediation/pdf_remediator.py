"""
PDF Remediator for Aelira Auto-Remediation Engine.

This module provides automatic remediation for accessibility issues in
PDF documents using pikepdf for direct structure tree manipulation.

Supported auto-fixes:
- Add alt text directly to images (via pikepdf structure tree)
- Add document language (in PDF Catalog)
- Add heading structure tags (H1-H6)
- Create bookmarks/outline
- Bind verified table cells to real marked content and route ambiguity to review
- Set document title in metadata
- Generate accessible HTML version as fallback

Note: Uses pikepdf for structure tree manipulation (the key for PDF/UA
compliance) and PyMuPDF (fitz) for text/image extraction during analysis.
"""

import base64
import binascii
import hashlib
import html
import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
import warnings
from contextlib import contextmanager
from dataclasses import asdict, replace
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

try:
    import fitz  # PyMuPDF - for reading/analyzing PDFs

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pikepdf  # For structure tree manipulation

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None

try:
    import ocrmypdf  # OCR engine for image-only (scanned) PDFs

    HAS_OCRMYPDF = True
except ImportError:
    HAS_OCRMYPDF = False
    ocrmypdf = None

from .base import (
    BaseRemediator,
    RemediationIssue,
    IssueCategory,
    RemediationConfig,
    RemediationResult,
)
from .confidence import ConfidenceCalculator, FixMethod
from .pdf_structure import PDFStructureTree
from .reading_order import HeuristicStrategy, ReadingOrderFixResult
from .content_tagger import ContentTagger
from .content_tagger_v2 import (
    ContentTaggerV2,
    associate_image_formula,
    verify_image_formula_association,
)
from .table_tagger import (
    MAX_TABLE_CELLS,
    MAX_TABLE_COLUMNS,
    MAX_TABLES,
    TABLE_STRUCTURE_NOT_VERIFIED,
    TABLE_STRUCTURE_TOO_COMPLEX,
    TableTagResult,
    TableTagger,
)
from .form_fixer import FormFixer
from .link_fixer import LinkFixer
from .role_mapping_fixer import RoleMappingFixer
from .font_unicode_fixer import FontUnicodeFixer
from .math_fixer import MathFixer
from src.education.math_contracts import CONCRETE_MATH_ISSUE_TYPES
from src.education.pdf_checks.image_checker import _displayed_image_occurrences

MATH_SPECIALIST_ISSUE_TYPES = CONCRETE_MATH_ISSUE_TYPES
from .contrast_flagger import ContrastFlagger
from .output_claim import DescriptorBoundOutputClaim

logger = logging.getLogger(__name__)


_ALLOWED_PYMUPDF_HTML_TAGS = frozenset(
    {
        "a",
        "b",
        "br",
        "div",
        "em",
        "i",
        "img",
        "p",
        "span",
        "strong",
        "sub",
        "sup",
    }
)
_BLOCKED_PYMUPDF_HTML_CONTAINER_TAGS = frozenset(
    {"script", "style", "iframe", "object"}
)
_BLOCKED_PYMUPDF_HTML_VOID_TAGS = frozenset({"embed", "meta", "link"})
_VOID_PYMUPDF_HTML_TAGS = frozenset({"br", "img"})
_PAGE_ID_RE = re.compile(r"page[0-9]+\Z")
_IMAGE_DATA_RE = re.compile(
    r"data:image/(?P<format>png|jpeg);base64,(?P<payload>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
_BASE64_DECODE_ERRORS = (binascii.Error, ValueError)
_MAX_IMAGE_DATA_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_WIDTH = 10_000
_MAX_IMAGE_HEIGHT = 10_000
_MAX_IMAGE_PIXELS = 25_000_000
_MAX_PDF_CANDIDATE_BYTES = 100 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PUBLICATION_DIR_FD_FUNCTIONS = {
    name: getattr(os, name, None)
    for name in ("open", "mkdir", "rename", "stat", "unlink", "rmdir")
}


def _is_complete_png_file(image_bytes: bytes) -> bool:
    """Require valid PNG chunk framing ending at a zero-length IEND."""
    if not image_bytes.startswith(_PNG_SIGNATURE):
        return False

    offset = len(_PNG_SIGNATURE)
    while offset < len(image_bytes):
        if len(image_bytes) - offset < 12:
            return False

        chunk_length = int.from_bytes(image_bytes[offset : offset + 4], "big")
        chunk_type = image_bytes[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > len(image_bytes):
            return False
        if chunk_type == b"IEND":
            return chunk_length == 0 and chunk_end == len(image_bytes)
        offset = chunk_end

    return False


def _is_complete_jpeg_file(image_bytes: bytes) -> bool:
    """Require a framed JPEG whose first real EOI marker is at exact EOF."""
    if (
        len(image_bytes) < 4
        or len(image_bytes) > _MAX_IMAGE_DATA_BYTES
        or image_bytes[:2] != b"\xff\xd8"
    ):
        return False

    offset = 2
    in_entropy_data = False
    while offset < len(image_bytes):
        marker_from_entropy = in_entropy_data
        if in_entropy_data:
            while offset < len(image_bytes) and image_bytes[offset] != 0xFF:
                offset += 1
            if offset == len(image_bytes):
                return False
        elif image_bytes[offset] != 0xFF:
            return False

        while offset < len(image_bytes) and image_bytes[offset] == 0xFF:
            offset += 1
        if offset == len(image_bytes):
            return False

        marker = image_bytes[offset]
        offset += 1
        if marker_from_entropy and (marker == 0x00 or 0xD0 <= marker <= 0xD7):
            continue
        if marker == 0x00 or marker == 0xD8:
            return False
        if marker == 0xD9:
            return offset == len(image_bytes)
        if 0xD0 <= marker <= 0xD7:
            return False
        if marker == 0x01:
            in_entropy_data = marker_from_entropy
            continue

        if offset + 2 > len(image_bytes):
            return False
        segment_length = int.from_bytes(image_bytes[offset : offset + 2], "big")
        if segment_length < 2:
            return False
        segment_end = offset + segment_length
        if segment_end > len(image_bytes):
            return False

        offset = segment_end
        in_entropy_data = marker == 0xDA

    return False


def _has_safe_image_dimensions(image: Image.Image) -> bool:
    """Return whether an opened image fits every configured dimension bound."""
    width, height = image.size
    return (
        width > 0
        and height > 0
        and width <= _MAX_IMAGE_WIDTH
        and height <= _MAX_IMAGE_HEIGHT
        and width * height <= _MAX_IMAGE_PIXELS
    )


def _is_verified_image(image_bytes: bytes, declared_format: str) -> bool:
    """Decode and structurally verify a bounded PNG or JPEG payload."""
    expected_format = {"png": "PNG", "jpeg": "JPEG"}[declared_format]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as image:
                if image.format != expected_format or not _has_safe_image_dimensions(
                    image
                ):
                    return False
                image.verify()

            with Image.open(BytesIO(image_bytes)) as image:
                if image.format != expected_format or not _has_safe_image_dimensions(
                    image
                ):
                    return False
                image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        EOFError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return False

    if declared_format == "png":
        return _is_complete_png_file(image_bytes)
    return _is_complete_jpeg_file(image_bytes)


def _normalize_untrusted_html_text(value: Any) -> str:
    """Replace characters that cannot safely occur in UTF-8 HTML."""
    return "".join(
        "\ufffd" if char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF else char
        for char in str(value)
    )


def _escape_html_interpolation(value: Any, *, quote: bool) -> str:
    """Normalize untrusted text to valid UTF-8, then escape it for HTML."""
    return html.escape(_normalize_untrusted_html_text(value), quote=quote)


def _safe_fragment_href(value: str) -> Optional[str]:
    """Return a canonical passive link target, or None for unsafe input."""
    if not value or "\\" in value:
        return None
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return None
    if value.startswith("#"):
        return value

    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            if not value[len(parsed.scheme) :].startswith("://") or not parsed.hostname:
                return None
        elif scheme == "mailto":
            if not parsed.path:
                return None
        else:
            return None
    except ValueError:
        return None

    return scheme + value[len(parsed.scheme) :]


def _safe_image_data_source(value: str) -> Optional[str]:
    """Return a canonical PNG/JPEG data URL after strict base64 validation."""
    match = _IMAGE_DATA_RE.fullmatch(value)
    if not match:
        return None

    raw_payload = match.group("payload")
    max_encoded_length = 4 * ((_MAX_IMAGE_DATA_BYTES + 2) // 3)
    max_raw_payload_length = max_encoded_length + 2 * ((max_encoded_length + 75) // 76)
    if len(raw_payload) > max_raw_payload_length:
        return None

    payload = "".join(raw_payload.split())
    if not _BASE64_RE.fullmatch(payload) or len(payload) % 4:
        return None
    if len(payload) > max_encoded_length:
        return None

    try:
        decoded = base64.b64decode(payload, validate=True)
    except _BASE64_DECODE_ERRORS:
        return None
    if not decoded or len(decoded) > _MAX_IMAGE_DATA_BYTES:
        return None

    image_format = match.group("format").lower()
    if not _is_verified_image(decoded, image_format):
        return None

    canonical_payload = base64.b64encode(decoded).decode("ascii")
    return f"data:image/{image_format};base64,{canonical_payload}"


class _PyMuPdfHtmlFragmentSanitizer(HTMLParser):
    """Rebuild a PyMuPDF fragment from a small passive HTML allowlist."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._output: List[str] = []
        self._open_tags: List[str] = []
        self._blocked_tags: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if self._blocked_tags:
            if tag in _BLOCKED_PYMUPDF_HTML_CONTAINER_TAGS:
                self._blocked_tags.append(tag)
            return
        if tag in _BLOCKED_PYMUPDF_HTML_CONTAINER_TAGS:
            self._blocked_tags.append(tag)
            return
        if tag in _BLOCKED_PYMUPDF_HTML_VOID_TAGS:
            return
        if tag not in _ALLOWED_PYMUPDF_HTML_TAGS:
            return

        source_attributes: Dict[str, str] = {}
        for name, value in attrs:
            normalized_name = name.lower()
            if normalized_name not in source_attributes and value is not None:
                source_attributes[normalized_name] = _normalize_untrusted_html_text(
                    value
                )

        safe_attributes: Dict[str, str] = {}
        element_id = source_attributes.get("id")
        if element_id is not None and _PAGE_ID_RE.fullmatch(element_id):
            safe_attributes["id"] = element_id

        if tag == "a" and "href" in source_attributes:
            href = _safe_fragment_href(source_attributes["href"])
            if href is not None:
                safe_attributes["href"] = href

        if tag == "img":
            if "src" in source_attributes:
                src = _safe_image_data_source(source_attributes["src"])
                if src is not None:
                    safe_attributes["src"] = src
            for name in ("alt", "title"):
                if name in source_attributes:
                    safe_attributes[name] = source_attributes[name]

        attribute_order = (
            ("id", "href")
            if tag == "a"
            else ("id", "src", "alt", "title") if tag == "img" else ("id",)
        )
        rendered_attributes = "".join(
            f' {name}="{html.escape(safe_attributes[name], quote=True)}"'
            for name in attribute_order
            if name in safe_attributes
        )
        self._output.append(f"<{tag}{rendered_attributes}>")
        if tag not in _VOID_PYMUPDF_HTML_TAGS:
            self._open_tags.append(tag)

    def handle_startendtag(
        self, tag: str, attrs: List[tuple[str, Optional[str]]]
    ) -> None:
        if self._blocked_tags or tag.lower() in (
            _BLOCKED_PYMUPDF_HTML_CONTAINER_TAGS | _BLOCKED_PYMUPDF_HTML_VOID_TAGS
        ):
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._blocked_tags:
            if tag in self._blocked_tags:
                blocked_index = (
                    len(self._blocked_tags) - 1 - self._blocked_tags[::-1].index(tag)
                )
                del self._blocked_tags[blocked_index:]
            return
        if tag in _VOID_PYMUPDF_HTML_TAGS or tag not in self._open_tags:
            return

        open_index = len(self._open_tags) - 1 - self._open_tags[::-1].index(tag)
        for open_tag in reversed(self._open_tags[open_index:]):
            self._output.append(f"</{open_tag}>")
        del self._open_tags[open_index:]

    def handle_data(self, data: str) -> None:
        if not self._blocked_tags:
            self._output.append(
                html.escape(_normalize_untrusted_html_text(data), quote=False)
            )

    def sanitized_html(self) -> str:
        for tag in reversed(self._open_tags):
            self._output.append(f"</{tag}>")
        self._open_tags.clear()
        return "".join(self._output)


def _sanitize_pymupdf_html_fragment(fragment: str) -> str:
    """Return canonical passive HTML rebuilt from an untrusted page fragment."""
    normalized = _normalize_untrusted_html_text(fragment)
    sanitizer = _PyMuPdfHtmlFragmentSanitizer()
    try:
        sanitizer.feed(normalized)
        sanitizer.close()
    except Exception:
        return html.escape(normalized, quote=False)
    return sanitizer.sanitized_html()


class PdfRemediator(BaseRemediator):
    """
    Remediator for PDF documents using pikepdf for direct structure manipulation.

    This remediator provides ACTUAL PDF fixes via structure tree manipulation:
    - Add alt text DIRECTLY in PDF (Figure elements with /Alt)
    - Create heading structure tags (H1-H6)
    - Set document language in Catalog (/Lang)
    - Set document title with display preference
    - Refuse table structure writes that lack verified content bindings
    - Create bookmarks from heading structure
    - Generate accessible HTML as fallback for edge cases

    Uses two libraries:
    - pikepdf: For structure tree manipulation (the key for accessibility)
    - PyMuPDF (fitz): For text/image extraction during analysis

    For heavily image-based PDFs (scanned documents), we generate
    an accessible HTML version with OCR text and alt text.

    Usage:
        issues = [{'type': 'heading', 'severity': 'high', ...}]
        remediator = PdfRemediator('document.pdf', issues)
        result = remediator.remediate()
    """

    DOCUMENT_TYPE = "pdf"
    SUPPORTED_EXTENSIONS = [".pdf"]

    AUTO_FIXABLE_CATEGORIES = [
        IssueCategory.LANGUAGE,
        IssueCategory.NAVIGATION,  # Bookmarks
        IssueCategory.STRUCTURE,  # Basic tagging + structure tree creation
        IssueCategory.ALT_TEXT,  # Direct in PDF structure tree
        IssueCategory.HEADING,  # H1-H6 structure tags
        IssueCategory.TITLE,  # Document title in metadata
        IssueCategory.READING_ORDER,  # Heuristic reading order reordering
        IssueCategory.TABLE,  # Verified Table/TR/TH/TD structure tags
        IssueCategory.LIST,  # L/LI/Lbl/LBody structure tags
        IssueCategory.FORM,  # Form field tooltips + tab order
        IssueCategory.LINK,  # Link annotation /Contents + vague text
    ]

    def __init__(
        self,
        file_path: str,
        issues: List[Dict[str, Any]],
        config: Optional[RemediationConfig] = None,
        ai_client: Optional[Any] = None,
        *,
        alt_text_client: Optional[Any] = None,
    ):
        """Initialize the PDF remediator."""
        if not HAS_PYMUPDF:
            raise ImportError(
                "PyMuPDF (fitz) is required for PDF remediation. "
                "Install with: pip install PyMuPDF"
            )
        if not HAS_PIKEPDF:
            logger.warning(
                "pikepdf not available - PDF structure manipulation disabled. "
                "Install with: pip install pikepdf"
            )
        super().__init__(
            file_path, issues, config, ai_client, alt_text_client=alt_text_client
        )
        # Working-copy state: every remediation runs against a staged copy in
        # a private temp directory so no in-place pass (TableTagger flushes,
        # reading-order rewrites) can ever mutate the original bytes.
        # self.file_path stays the immutable original for naming and backup;
        # image-only inputs additionally get an OCR'd searchable working copy
        # so the delivered output keeps a text layer.
        self._work_dir: Optional[str] = None
        self._working_file_path: Optional[str] = None
        self._ocr_applied: bool = False
        # Set when the input's own text layer is below the searchable
        # threshold: the delivered output must then carry at least the
        # minimum usable text layer or delivery fails closed.
        self._require_output_text_layer: bool = False
        self._pdf: Optional[Any] = None  # PyMuPDF document for reading
        self._pikepdf_doc: Optional[Any] = None  # pikepdf document for writing
        self._struct_tree: Optional[PDFStructureTree] = None  # Structure tree helper
        self._structure_modified: bool = False  # Track if we made structure changes
        self._html_output: Optional[str] = None
        self._pending_bookmarks: List[Dict[str, Any]] = (
            []
        )  # Bookmarks to add via pikepdf
        self._confidence = ConfidenceCalculator()
        # Stats from ContentTaggerV2, recorded by _save_document on success;
        # None means the tagger did not run (or fell back to v1/failed).
        self._content_tagger_stats: Optional[Dict[str, int]] = None
        self._pending_image_equations: List[tuple[Any, Any]] = []
        self._verified_image_equations: List[tuple[Any, Any, Any]] = []

        # WCAG criteria mapping per issue category
        self._wcag_map: Dict[IssueCategory, str] = {
            IssueCategory.LANGUAGE: "3.1.1",
            IssueCategory.TITLE: "2.4.2",
            IssueCategory.STRUCTURE: "4.1.2",
            IssueCategory.ALT_TEXT: "1.1.1",
            IssueCategory.HEADING: "1.3.1",
            IssueCategory.NAVIGATION: "2.4.5",
            IssueCategory.READING_ORDER: "1.3.2",
            IssueCategory.TABLE: "1.3.1",
            IssueCategory.LIST: "1.3.1",
            IssueCategory.FORM: "4.1.2",
            IssueCategory.LINK: "2.4.4",
        }

    # ------------------------------------------------------------------
    # Two-phase remediation override
    # ------------------------------------------------------------------

    def remediate(self) -> "RemediationResult":
        """Execute two-phase remediation.

        Phase 1 — Document-level passes, ordered by dependency so that
        early passes (e.g. structure tree creation) set up the context
        that later passes (e.g. heading tags, table tags) rely on.

        Phase 2 — Content marking (ContentTagger v2) and finalization
        (contrast reporting, save, verify).
        """

        try:
            logger.info("Starting two-phase remediation of %s", self.file_path)

            # Table safety must be decided from the untouched input. Cache the
            # read-only document-wide outcome before backup creation, document
            # loading, or any non-table mutation.
            if any(issue.category == IssueCategory.TABLE for issue in self.issues):
                self._prepare_table_fixes()
                if self._table_batch_is_too_complex():
                    self._complete_table_complexity_refusal()
                    logger.info(
                        "Remediation refused before backup or document load: %s",
                        TABLE_STRUCTURE_TOO_COMPLEX,
                    )
                    return self.result

            # Create backup if configured
            if self.config.create_backup and not self._has_only_table_issues():
                self.result.backup_path = self._create_backup()

            # Stage a private working copy (and OCR it if the input is
            # image-only) so every in-place pass mutates the copy, never
            # the original. Fails closed on signed input or OCR failure.
            self._stage_working_copy()

            # Load the document
            document = self._load_document()

            # Group issues by category for efficient batch dispatch
            issues_by_category: Dict[IssueCategory, List[RemediationIssue]] = {}
            for issue in self.issues:
                issues_by_category.setdefault(issue.category, []).append(issue)

            # Also group STRUCTURE issues by specialist-relevant issue_type
            specialist_structure_types = {
                "missing_role_map": "role_mapping",
                "incomplete_role_map": "role_mapping",
                "missing_tounicode": "font_unicode",
                **{issue_type: "math" for issue_type in MATH_SPECIALIST_ISSUE_TYPES},
            }
            specialist_issues: Dict[str, List[RemediationIssue]] = {}
            core_structure_issues: List[RemediationIssue] = []
            for issue in issues_by_category.get(IssueCategory.STRUCTURE, []):
                itype = issue.metadata.get("issue_type", "")
                if itype in specialist_structure_types:
                    bucket = specialist_structure_types[itype]
                    specialist_issues.setdefault(bucket, []).append(issue)
                else:
                    core_structure_issues.append(issue)

            # ============================================================
            # Phase 1 — Document-level passes (ordered by dependency)
            # ============================================================

            # 1. Language
            for issue in issues_by_category.get(IssueCategory.LANGUAGE, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing LANGUAGE issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 2. Title
            for issue in issues_by_category.get(IssueCategory.TITLE, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing TITLE issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 3. Structure completeness (core structure — not specialist types)
            for issue in core_structure_issues:
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing STRUCTURE issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 4. Headings
            for issue in issues_by_category.get(IssueCategory.HEADING, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing HEADING issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 5. Navigation / Bookmarks
            for issue in issues_by_category.get(IssueCategory.NAVIGATION, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error(
                        "Error processing NAVIGATION issue %s: %s", issue.id, e
                    )
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 6. Role Mapping (specialist)
            if specialist_issues.get("role_mapping"):
                self._run_specialist(
                    "role_mapping", specialist_issues["role_mapping"], document
                )

            # 7. Lists
            for issue in issues_by_category.get(IssueCategory.LIST, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing LIST issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 8. Forms (specialist)
            if issues_by_category.get(IssueCategory.FORM):
                self._run_specialist(
                    "form", issues_by_category[IssueCategory.FORM], document
                )

            # 9. Links (specialist)
            if issues_by_category.get(IssueCategory.LINK):
                self._run_specialist(
                    "link", issues_by_category[IssueCategory.LINK], document
                )

            # 10. Math (specialist)
            if specialist_issues.get("math"):
                self._run_specialist("math", specialist_issues["math"], document)

            # 11. Font/Unicode (specialist)
            if specialist_issues.get("font_unicode"):
                self._run_specialist(
                    "font_unicode", specialist_issues["font_unicode"], document
                )

            # 12. Reading order
            for issue in issues_by_category.get(IssueCategory.READING_ORDER, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error(
                        "Error processing READING_ORDER issue %s: %s", issue.id, e
                    )
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 13. Alt text (vision AI — runs last because it may use AI)
            for issue in issues_by_category.get(IssueCategory.ALT_TEXT, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing ALT_TEXT issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # 14. Tables run last so the retained rollback snapshot contains
            # every non-table change if final saved-file verification fails.
            for issue in issues_by_category.get(IssueCategory.TABLE, []):
                try:
                    self._process_issue(issue, document)
                except Exception as e:
                    logger.error("Error processing TABLE issue %s: %s", issue.id, e)
                    self.result.failed_issues.append(
                        {
                            "issue_id": issue.id,
                            "description": issue.description,
                            "error": str(e),
                        }
                    )
                    self.result.failed_count += 1

            # ============================================================
            # Phase 2 — Content marking and finalization
            # ============================================================

            # A table-only refusal is a true no-op: do not let final-save or
            # fallback paths create an output that implies remediation.
            if self._is_table_only_manual_noop():
                self._discard_failed_table_output(self._get_output_path())
                self._close_documents_without_save(document)
                self._calculate_scores()
                self.result.complete()
                logger.info("Table-only remediation refused without modifying the PDF")
                return self.result

            # ContentTagger v2 handles content marking, ParentTree,
            # Document root, and PDF/UA identifier — always run it.
            self._structure_modified = True

            # 15. ContentTagger v2 runs inside _save_document()
            # 16. Contrast (report-only — produces manual issues)
            contrast_issues = issues_by_category.get(IssueCategory.CONTRAST, [])
            if contrast_issues:
                try:
                    flagger = ContrastFlagger()
                    guidances = flagger.flag(contrast_issues)
                    for guidance in guidances:
                        # Find the original issue to pass to _add_manual_issue
                        original = next(
                            (i for i in contrast_issues if i.id == guidance.issue_id),
                            None,
                        )
                        if original:
                            self._add_manual_issue(
                                original,
                                reason=guidance.guidance,
                                recommendation=(
                                    "Adjust foreground/background colors to achieve a "
                                    f"contrast ratio of at least {guidance.required_ratio:.1f}:1."
                                ),
                            )
                except Exception as e:
                    logger.warning("ContrastFlagger failed (non-fatal): %s", e)

            # 17. Save + verify
            output_path = self._save_document(document)
            self.result.output_file = output_path

            self._reconcile_verified_image_equations()

            # ContentTagger v2 (inside _save_document) fixes the document-
            # level structure issues Phase 1 had to file as manual; move
            # them to the fixed bucket based on what the tagger reported.
            if output_path:
                self._reconcile_content_tagger_fixes()

            if self.config.verify_fixes and output_path:
                self._verify_fixes(output_path)

            self._calculate_scores()
            self.result.complete()

            logger.info(
                "Two-phase remediation complete: %d fixed, %d manual, %d failed",
                self.result.fixed_count,
                self.result.manual_count,
                self.result.failed_count,
            )

        except Exception as e:
            logger.error("Remediation failed: %s", e)
            if any(issue.category == IssueCategory.TABLE for issue in self.issues):
                if getattr(self, "_pre_table_pikepdf_doc", None) is not None:
                    self._rollback_provisional_table_fixes()
                self._discard_failed_table_output(self._get_output_path())
            self.result.success = False
            self.result.error_message = str(e)
            self.result.close_output_claim()
            self.result.complete()
        finally:
            self._cleanup_working_copy()

        return self.result

    # ------------------------------------------------------------------
    # Specialist module dispatch
    # ------------------------------------------------------------------

    def _run_specialist(self, name: str, issues: List[RemediationIssue], document: Any):
        """Run a specialist module with error isolation.

        Attribute types (from _load_document):
          self._pikepdf_doc -> pikepdf.Pdf  (first arg: pdf)
          self._pdf         -> fitz.Document (second arg: fitz_doc)
        """
        if not self._pikepdf_doc:
            for issue in issues:
                self._add_manual_issue(
                    issue,
                    reason="pikepdf not available for specialist module",
                    recommendation="Install pikepdf for full remediation support",
                )
            return

        try:
            if name == "form":
                specialist = FormFixer(self._pikepdf_doc, self._pdf)
                results = specialist.fix(issues)
                for i, result in enumerate(results):
                    issue = issues[i] if i < len(issues) else None
                    if issue is None:
                        continue
                    if result.success:
                        self._structure_modified = True
                        self._add_fixed_issue(
                            issue,
                            fixed_content=result.description,
                            fix_method="rule",
                            confidence=result.confidence,
                            wcag_criteria=self._wcag_map.get(issue.category),
                            page_number=issue.metadata.get("page_number"),
                        )
                    else:
                        self._add_manual_issue(
                            issue,
                            reason=result.description,
                            recommendation="Manually add form field labels/tooltips",
                        )

            elif name == "link":
                specialist = LinkFixer(
                    self._pikepdf_doc, self._pdf, ai_client=self.ai_client
                )
                results = specialist.fix(issues)
                for i, result in enumerate(results):
                    issue = issues[i] if i < len(issues) else None
                    if issue is None:
                        continue
                    if result.success:
                        self._structure_modified = True
                        self._add_fixed_issue(
                            issue,
                            fixed_content=result.notes
                            or f"Fixed {result.links_fixed} links",
                            fix_method=result.fix_method,
                            confidence=0.85,
                            wcag_criteria=self._wcag_map.get(issue.category),
                            page_number=issue.metadata.get("page_number"),
                        )
                    else:
                        self._add_manual_issue(
                            issue,
                            reason=result.error or "Could not fix link annotations",
                            recommendation="Manually add descriptive /Contents to link annotations",
                        )

            elif name == "role_mapping":
                specialist = RoleMappingFixer(self._pikepdf_doc, self._pdf)
                results = specialist.fix(issues)
                # RoleMappingFixer returns one summary result for all issues
                if results and results[0].success:
                    self._structure_modified = True
                    for issue in issues:
                        mapped_info = (
                            f"Mapped {results[0].tags_mapped} non-standard tags"
                            if results[0].tags_mapped > 0
                            else "All tags already standard"
                        )
                        self._add_fixed_issue(
                            issue,
                            fixed_content=mapped_info,
                            fix_method="rule",
                            confidence=0.95,
                            wcag_criteria=self._wcag_map.get(issue.category),
                            page_number=issue.metadata.get("page_number"),
                        )
                elif results:
                    for issue in issues:
                        self._add_manual_issue(
                            issue,
                            reason=results[0].error or "Role mapping fix failed",
                            recommendation="Manually add /RoleMap entries for non-standard tags",
                        )
                else:
                    for issue in issues:
                        self._add_manual_issue(
                            issue,
                            reason="RoleMappingFixer returned no results",
                            recommendation="Manually review role mappings",
                        )

            elif name == "font_unicode":
                specialist = FontUnicodeFixer(self._pikepdf_doc, self._pdf)
                results = specialist.fix(issues)
                for i, result in enumerate(results):
                    issue = issues[i] if i < len(issues) else None
                    if issue is None:
                        continue
                    if result.success:
                        self._structure_modified = True
                        self._add_fixed_issue(
                            issue,
                            fixed_content=(
                                f"Added ToUnicode CMap for {result.font_name} "
                                f"({result.mappings_added} mappings)"
                            ),
                            fix_method="rule",
                            confidence=result.confidence,
                            needs_review=result.needs_review,
                            wcag_criteria=self._wcag_map.get(issue.category),
                            page_number=issue.metadata.get("page_number"),
                        )
                    else:
                        self._add_manual_issue(
                            issue,
                            reason=(
                                f"Cannot build ToUnicode CMap for {result.font_name}: "
                                "no /Encoding /Differences available"
                            ),
                            recommendation=(
                                "Re-embed the font with proper Unicode mappings, "
                                "or add /ActualText spans for affected text."
                            ),
                        )

            elif name == "math":
                specialist = MathFixer(
                    self._pikepdf_doc,
                    self._pdf,
                    struct_tree=self._struct_tree,
                    ai_client=None,
                    alt_text_client=self.alt_text_client,
                )
                results = specialist.fix(issues)
                for i, result in enumerate(results):
                    issue = issues[i] if i < len(issues) else None
                    if issue is None:
                        continue
                    if result.pending_association is not None:
                        self._pending_image_equations.append((issue, result))
                        self._add_manual_issue(
                            issue,
                            reason="Verified image equation awaits exact content association",
                            recommendation=(
                                "Keep this candidate pending until Formula/MCID/ParentTree "
                                "association is verified."
                            ),
                        )
                        continue
                    if result.success:
                        self._structure_modified = True
                        desc = (
                            result.aria_label
                            or f"Math formula on page {result.page_number}"
                        )
                        self._add_fixed_issue(
                            issue,
                            fixed_content=desc,
                            fix_method="heuristic",
                            confidence=0.80 if result.has_mathml else 0.60,
                            wcag_criteria="1.1.1",
                            page_number=result.page_number,
                        )
                    else:
                        self._add_manual_issue(
                            issue,
                            reason=result.error or "Could not convert math content",
                            recommendation=(
                                "Manually add Formula structure elements with MathML "
                                "and alt text for mathematical content."
                            ),
                        )

            else:
                logger.warning("Unknown specialist module: %s", name)
                for issue in issues:
                    self._add_manual_issue(
                        issue,
                        reason=f"No specialist module for: {name}",
                        recommendation="Review and fix manually",
                    )

        except Exception as e:
            logger.error("Specialist module '%s' failed: %s", name, e, exc_info=True)
            for issue in issues:
                self._add_manual_issue(
                    issue,
                    reason=f"Specialist module '{name}' raised an error: {e}",
                    recommendation="Review and fix manually",
                )

    def _process_issue(self, issue: RemediationIssue, document: Any):
        """Process a single issue with confidence scoring.

        Overrides the base class to attach per-fix confidence, fix method,
        WCAG criteria, and review-needed flags to every FixedIssue.
        """
        # Delegate to base for category-disabled / can't-auto-fix checks
        if not self._is_category_enabled(issue.category):
            self._add_manual_issue(
                issue,
                reason="Category disabled in configuration",
                recommendation=f"Enable {issue.category.value} fixing in remediation settings",
            )
            return

        if not self.can_auto_fix(issue):
            self._add_manual_issue(
                issue,
                reason=self._get_manual_reason(issue),
                recommendation=self._get_manual_recommendation(issue),
            )
            return

        # Generate fix content
        fix_content = self._generate_fix(issue, document)
        if fix_content is None:
            self._add_manual_issue(
                issue,
                reason="Could not generate appropriate fix",
                recommendation=self._get_manual_recommendation(issue),
            )
            return

        # Determine fix method, confidence, and model BEFORE applying
        fix_method, confidence, model_used = self._compute_fix_metadata(
            issue, fix_content
        )

        # Apply the fix
        success = self.apply_fix(issue, document, fix_content)

        if success:
            page_num = issue.metadata.get("page_number")
            wcag = self._wcag_map.get(issue.category)

            self._add_fixed_issue(
                issue,
                fixed_content=fix_content,
                fix_method=fix_method.value,
                confidence=confidence,
                needs_review=self._confidence.needs_review(confidence),
                model_used=model_used,
                wcag_criteria=wcag,
                page_number=page_num,
            )
        else:
            table_refusal = getattr(self, "_table_safety_refusals", {}).get(issue.id)
            if issue.category == IssueCategory.TABLE and table_refusal:
                issue.metadata["remediation_error_code"] = table_refusal
                recommendation = (
                    "Verify the table's real cell content and structure manually."
                    if table_refusal == TABLE_STRUCTURE_NOT_VERIFIED
                    else "Simplify or split the table, then verify its structure manually."
                )
                self._add_manual_issue(
                    issue,
                    reason=table_refusal,
                    recommendation=recommendation,
                )
                return
            self.result.failed_issues.append(
                {
                    "issue_id": issue.id,
                    "description": issue.description,
                    "error": "Failed to apply fix",
                }
            )
            self.result.failed_count += 1

    def _compute_fix_metadata(self, issue: RemediationIssue, fix_content: str) -> tuple:
        """Determine the FixMethod, confidence score, and model_used for an issue.

        Returns:
            (FixMethod, confidence: float, model_used: Optional[str])
        """
        # --- Rule-based categories ---
        if issue.category == IssueCategory.LANGUAGE:
            confidence = self._confidence.calculate(FixMethod.RULE, verified=True)
            return FixMethod.RULE, confidence, None

        if issue.category == IssueCategory.TITLE:
            confidence = self._confidence.calculate(FixMethod.RULE, verified=True)
            return FixMethod.RULE, confidence, None

        if issue.category == IssueCategory.STRUCTURE:
            confidence = self._confidence.calculate(FixMethod.RULE, verified=True)
            return FixMethod.RULE, confidence, None

        # --- Heuristic categories ---
        if issue.category == IssueCategory.HEADING:
            # Signal strength: how confident we are in the heading detection
            # based on font size delta from body text (12pt baseline)
            font_size = issue.metadata.get("font_size")
            if font_size is not None:
                signal = min(1.0, (float(font_size) - 12.0) / 12.0)
            else:
                signal = 0.5
            confidence = self._confidence.calculate(
                FixMethod.HEURISTIC, signal_strength=signal
            )
            return FixMethod.HEURISTIC, confidence, None

        if issue.category == IssueCategory.NAVIGATION:
            # Bookmarks are heuristic — derived from heading detection
            headings = issue.metadata.get("headings", [])
            signal = min(1.0, len(headings) / 5.0) if headings else 0.5
            confidence = self._confidence.calculate(
                FixMethod.HEURISTIC, signal_strength=signal
            )
            return FixMethod.HEURISTIC, confidence, None

        # --- Reading order (heuristic) ---
        if issue.category == IssueCategory.READING_ORDER:
            # Confidence comes from the ReadingOrderFixResult stored on the issue
            ro_confidence = issue.metadata.get("reading_order_confidence", 0.7)
            return FixMethod.HEURISTIC, ro_confidence, None

        # --- Table (heuristic + optional AI vision) ---
        if issue.category == IssueCategory.TABLE:
            # Confidence comes from the TableTagResult stored on the issue
            table_confidence = issue.metadata.get("table_confidence", 0.7)
            return FixMethod.HEURISTIC, table_confidence, None

        # --- List (heuristic) ---
        if issue.category == IssueCategory.LIST:
            return FixMethod.HEURISTIC, 0.8, None

        # --- AI categories ---
        if issue.category == IssueCategory.ALT_TEXT:
            # Check if alt text came from AI or was pre-existing
            pre_existing = self.config.allow_legacy_nested_ai and (
                issue.metadata.get("suggested_alt_text")
                or issue.metadata.get("generated_alt_text")
            )
            if pre_existing:
                # Pre-generated alt text — treat as AI text (we trust it somewhat)
                confidence = self._confidence.calculate(
                    FixMethod.AI_TEXT, context_quality=0.6
                )
                return FixMethod.AI_TEXT, confidence, None

            if self.config.fix_alt_text and self.alt_text_client:
                # We used AI to generate the alt text
                has_context = bool(
                    self._pdf
                    and len(self._pdf) > 0
                    and issue.metadata.get("page_number")
                )
                context_quality = 0.7 if has_context else 0.3
                confidence = self._confidence.calculate(
                    FixMethod.AI_VISION, context_quality=context_quality
                )
                model_name = getattr(self.config, "ai_model", "gemini")
                return FixMethod.AI_VISION, confidence, model_name

            # Fallback alt text (generic placeholder)
            confidence = self._confidence.calculate(
                FixMethod.AI_TEXT, context_quality=0.2
            )
            return FixMethod.AI_TEXT, confidence, None

        # Default fallback
        confidence = self._confidence.calculate(FixMethod.HEURISTIC)
        return FixMethod.HEURISTIC, confidence, None

    # ------------------------------------------------------------------
    # Working-copy staging and OCR preprocessing
    # ------------------------------------------------------------------

    # Below this many extractable characters the input is treated as
    # image-only and OCR'd (mirrors the scanner's threshold in
    # PDFProcessor.process_pdf).
    _MIN_SEARCHABLE_TEXT_CHARS = 100
    # Minimum extractable text the OCR derivative (and the delivered
    # output) must carry to count as searchable (mirrors the scanner's
    # empty-output threshold in _ocr_pdf_enhanced).
    _MIN_OCR_TEXT_CHARS = 50

    @property
    def _working_path(self) -> str:
        """Path all remediation passes operate on.

        The staged working copy once _stage_working_copy has run; the
        original path before staging (analysis-only contexts).
        """
        return self._working_file_path or self.file_path

    @staticmethod
    def _extract_all_text(path: str) -> str:
        """Extract the full text layer of a PDF with fitz (no OCR)."""
        with fitz.open(path) as doc:
            return "".join(page.get_text() for page in doc)

    @classmethod
    def _field_tree_has_signature(cls, fields, inherited_ft=None, seen=None) -> bool:
        """Recursively scan an AcroForm field tree for signature fields.

        /FT is inheritable: a terminal widget may carry no /FT of its own
        and take it from an ancestor, so the inherited value is threaded
        down through /Kids. Visited indirect objects are tracked to stay
        safe on malformed PDFs with /Kids cycles.
        """
        if seen is None:
            seen = set()
        for field in fields:
            objgen = getattr(field, "objgen", (0, 0))
            if objgen != (0, 0):
                if objgen in seen:
                    continue
                seen.add(objgen)
            ft = field.get("/FT")
            ft_str = str(ft) if ft is not None else inherited_ft
            if ft_str == "/Sig":
                return True
            kids = field.get("/Kids")
            if kids is not None and cls._field_tree_has_signature(kids, ft_str, seen):
                return True
        return False

    def _signature_preflight(self, path: str) -> None:
        """Fail closed unless the exact staged PDF is confirmed unsigned.

        Remediation rewrites the document, which invalidates digital
        signatures. Signature evidence (AcroForm /SigFlags bit 1 or any
        /FT /Sig anywhere in the field tree) rejects the input, and so
        does anything indeterminate: pikepdf unavailable, an AcroForm we
        cannot parse, or an XFA form (which can embed signatures opaquely).
        """
        if not HAS_PIKEPDF:
            raise RuntimeError(
                "Signature preflight failed: pikepdf is unavailable, so the "
                "input cannot be confirmed unsigned. Failing closed rather "
                "than risking silent signature invalidation."
            )
        try:
            with pikepdf.open(path) as pdf:
                acroform = pdf.Root.get("/AcroForm")
                if acroform is None:
                    has_xfa = False
                    signed = False
                else:
                    has_xfa = acroform.get("/XFA") is not None
                    sig_flags = acroform.get("/SigFlags")
                    signed = bool(sig_flags is not None and int(sig_flags) & 1)
                    if not signed:
                        signed = self._field_tree_has_signature(
                            acroform.get("/Fields", [])
                        )
        except Exception as e:
            raise RuntimeError(
                "Signature preflight failed: could not inspect the AcroForm "
                f"({e}). Failing closed rather than risking silent signature "
                "invalidation."
            ) from e
        if has_xfa:
            raise RuntimeError(
                "Signature preflight failed: input is an XFA form, which "
                "can embed signatures this scan cannot see. Failing closed "
                "rather than risking silent signature invalidation."
            )
        if signed:
            raise RuntimeError(
                "Input PDF contains digital signature fields; remediation "
                "would rewrite the document and invalidate the signature. "
                "Failing closed — remediate a signature-free copy or handle "
                "this document manually."
            )

    def _stage_working_copy(self) -> None:
        """Stage the input into a private temp dir; OCR it if image-only.

        The scan pipeline builds an OCR'd searchable derivative and then
        deletes it, so remediation receives the original image-only bytes.
        Remediating those directly would deliver a PDF with no text layer.
        This stages a copy (so in-place passes never touch the original)
        and, for image-only inputs, runs OCRmyPDF to produce a searchable
        working PDF whose text layer survives into the delivered output.

        Fails closed (raises) on signed input, OCR engine failure, or an
        OCR result with no genuinely extractable text.
        """
        self._work_dir = tempfile.mkdtemp(prefix="aelira_pdf_remediation_")
        staged_path = str(Path(self._work_dir) / Path(self.file_path).name)
        shutil.copy2(self.file_path, staged_path)
        self._working_file_path = staged_path
        self._signature_preflight(staged_path)

        page_texts, page_has_images = self._page_text_profile(staged_path)
        total_text = "".join(page_texts).strip()
        # Mixed-safe OCR can recognize image-only pages while preserving pages
        # that already have a usable text layer. It cannot safely repair an
        # image page with a short direct layer: skip_text would silently skip
        # it, while forced OCR could destroy existing text and structure.
        partial_text_pages = [
            i
            for i, page_text in enumerate(page_texts)
            if page_has_images[i]
            and 0 < len(page_text.strip()) < self._MIN_OCR_TEXT_CHARS
        ]
        if partial_text_pages:
            partial_page_numbers = [i + 1 for i in partial_text_pages]
            raise RuntimeError(
                "Image page(s) "
                f"{partial_page_numbers} contain partial direct text below "
                f"{self._MIN_OCR_TEXT_CHARS} characters; mixed-safe OCR would "
                "skip them. Failing closed for manual remediation."
            )

        # Only zero-text image pages are safe to send through skip_text OCR.
        # Blank pages with no images remain exempt.
        needy_pages = [
            i
            for i, page_text in enumerate(page_texts)
            if page_has_images[i] and not page_text.strip()
        ]
        # Any input that has some text but less than a searchable layer
        # (or needs OCR at all) must deliver a genuinely searchable output.
        if needy_pages or (
            total_text and len(total_text) < self._MIN_SEARCHABLE_TEXT_CHARS
        ):
            self._require_output_text_layer = True

        if not needy_pages:
            logger.info(
                "No pages need OCR (%d chars direct text across %d pages)",
                len(total_text),
                len(page_texts),
            )
            return

        if not HAS_OCRMYPDF:
            raise RuntimeError(
                "Input PDF has pages without a text layer and OCRmyPDF is "
                "not installed; failing closed rather than delivering an "
                "unsearchable remediated file."
            )

        self._assert_ocr_language_supported(staged_path)

        ocr_path = str(Path(self._work_dir) / f"{Path(self.file_path).stem}_ocr.pdf")
        try:
            logger.info(
                "Running OCRmyPDF on %d/%d pages lacking text: %s",
                len(needy_pages),
                len(page_texts),
                staged_path,
            )
            ocrmypdf.ocr(
                input_file=staged_path,
                output_file=ocr_path,
                force_ocr=False,
                # Mixed-safe: pages that already have text are passed
                # through untouched; only image pages are OCR'd.
                skip_text=True,
                redo_ocr=False,
                optimize=1,
                language=["eng"],
                output_type="pdf",
                progress_bar=False,
                use_threads=True,
            )
        except (
            ocrmypdf.exceptions.PriorOcrFoundError,
            ocrmypdf.exceptions.TaggedPDFError,
        ) as e:
            # The engine refuses tagged/prior-OCR input. Forcing OCR would
            # rasterize pages and may strip structure, while continuing would
            # leave the pages that triggered OCR without a usable text layer.
            # Fail closed and report the exact 1-based pages instead.
            needy_page_numbers = [i + 1 for i in needy_pages]
            raise RuntimeError(
                "OCR cannot run for unsearchable page(s) "
                f"{needy_page_numbers} because the input is tagged or reports "
                "prior OCR. Failing closed rather than delivering an "
                "unsearchable remediated file."
            ) from e

        derivative_texts, _ = self._page_text_profile(ocr_path)
        missing_pages = [
            i + 1
            for i in needy_pages
            if i >= len(derivative_texts)
            or len(derivative_texts[i].strip()) < self._MIN_OCR_TEXT_CHARS
        ]
        if missing_pages:
            raise RuntimeError(
                "OCR produced no usable text layer for page(s) "
                f"{missing_pages}; failing closed rather than delivering an "
                "unsearchable remediated file."
            )

        self._working_file_path = ocr_path
        self._ocr_applied = True
        self._ocr_pages = list(needy_pages)
        self.result.warnings.append(
            "Input PDF had pages without a text layer; an OCR text layer "
            "was added and preserved in the remediated output."
        )
        logger.info(
            "OCR working copy ready (%d pages recognized): %s",
            len(needy_pages),
            ocr_path,
        )

    @staticmethod
    def _page_text_profile(path: str) -> tuple:
        """Per-page direct text and image presence for a PDF (no OCR)."""
        texts: List[str] = []
        has_images: List[bool] = []
        with fitz.open(path) as doc:
            for page in doc:
                texts.append(page.get_text())
                has_images.append(bool(page.get_images(full=True)))
        return texts, has_images

    def _assert_ocr_language_supported(self, path: str) -> None:
        """Fail closed on a declared non-English document language.

        The OCR pass currently runs tesseract with eng only (matching the
        scanner). Applying English OCR to a document that declares another
        language would produce garbage text presented as accessibility.
        """
        lang = ""
        try:
            with pikepdf.open(path) as pdf:
                raw = pdf.Root.get("/Lang")
                lang = str(raw).strip() if raw is not None else ""
        except Exception as e:
            raise RuntimeError(
                "Could not determine the document language before OCR "
                f"({e}); failing closed."
            ) from e
        if lang and not lang.lower().startswith("en"):
            raise RuntimeError(
                f"Input PDF declares document language '{lang}', but only "
                "English (eng) OCR is currently supported. Failing closed "
                "rather than applying English OCR to a non-English document."
            )

    def _ensure_output_text_layer(self, output_path: str) -> None:
        """Fail closed if an output candidate lost a required text layer.

        Inspects the candidate with direct text extraction — never OCR —
        so a missing text layer cannot be masked by the verification
        re-scan's on-the-fly OCR.
        """
        if getattr(self, "_ocr_pages", []):
            page_texts, _ = self._page_text_profile(output_path)
            missing_pages = [
                page_index + 1
                for page_index in self._ocr_pages
                if page_index >= len(page_texts)
                or len(page_texts[page_index].strip()) < self._MIN_OCR_TEXT_CHARS
            ]
            if missing_pages:
                raise RuntimeError(
                    "Remediated output lost the OCR text layer for page(s) "
                    f"{missing_pages}; refusing to deliver an unsearchable file."
                )
            return

        text = self._extract_all_text(output_path)
        if len(text.strip()) < self._MIN_OCR_TEXT_CHARS:
            raise RuntimeError(
                "Remediated output lost the OCR text layer; refusing to "
                "deliver an unsearchable file for an image-only input."
            )

    def _cleanup_working_copy(self) -> None:
        """Close open handles and remove the temp working directory.

        Runs in remediate()'s finally block on both success and failure.
        Any cleanup failure is returned explicitly with the retained path.
        """
        cleanup_errors = []
        for attr in ("_pdf", "_pikepdf_doc"):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    handle.close()
                except Exception as e:
                    cleanup_errors.append(f"could not close {attr}: {e}")
                setattr(self, attr, None)
        if self._work_dir:
            work_dir = self._work_dir
            try:
                shutil.rmtree(work_dir)
            except Exception as e:
                cleanup_errors.append(
                    f"could not remove remediation work directory '{work_dir}': {e}; "
                    "remove this directory manually"
                )
            else:
                self._work_dir = None
                self._working_file_path = None

        if cleanup_errors:
            cleanup_message = "Remediation cleanup failed: " + "; ".join(cleanup_errors)
            logger.error(cleanup_message)
            self.result.success = False
            self.result.warnings.append(cleanup_message)
            if self.result.error_message:
                self.result.error_message = (
                    f"{self.result.error_message}; {cleanup_message}"
                )
            else:
                self.result.error_message = cleanup_message
            self.result.complete()

    def _get_output_path(self) -> str:
        """Resolve the output path from the original name, never the copy.

        self.file_path stays the immutable original, so the base logic
        already names the output after it; this override only guards
        against a configuration that would overwrite the original.
        """
        output_path = super()._get_output_path()
        if os.path.realpath(output_path) == os.path.realpath(self.file_path):
            raise RuntimeError(
                "Refusing to write remediation output over the original file: "
                f"{self.file_path}"
            )
        return output_path

    def _load_document(self) -> Any:
        """Load the working-copy PDF for editing with both fitz and pikepdf."""
        logger.info(f"Loading PDF working copy: {self._working_path}")

        # Open with PyMuPDF for reading/analysis
        self._pdf = fitz.open(self._working_path)

        # Open with pikepdf for structure manipulation
        if HAS_PIKEPDF:
            try:
                self._pikepdf_doc = pikepdf.open(
                    self._working_path, allow_overwriting_input=True
                )
                self._struct_tree = PDFStructureTree(self._pikepdf_doc)
                logger.info(
                    "Initialized pikepdf structure tree for direct PDF manipulation"
                )
            except Exception as e:
                logger.warning(f"Could not initialize pikepdf: {e}")
                self._pikepdf_doc = None
                self._struct_tree = None

        return self._pdf

    def _save_document(self, document: Any) -> str:
        """Prepare privately and publish through retained directory descriptors."""
        output_path = self._get_output_path()
        logger.info(f"Saving remediated PDF to: {output_path}")

        output_dir = Path(output_path).parent
        output_name = Path(output_path).name
        html_name: Optional[str] = None
        html_path: Optional[str] = None
        html_existed = False
        output_dir_fd: Optional[int] = None
        candidate_dir_fd: Optional[int] = None
        candidate_dir_name: Optional[str] = None
        candidate_dir_path: Optional[str] = None
        candidate_identity: Optional[tuple[int, int]] = None
        serialization_dir_path: Optional[str] = None
        serialization_cleanup_attempted = False
        pdf_candidate_identity: Optional[tuple[int, int]] = None
        pdf_candidate_fd: Optional[int] = None
        local_claim: Optional[DescriptorBoundOutputClaim] = None
        html_candidate_identity: Optional[tuple[int, int]] = None
        document_handles_closed = False
        primary_error: Optional[Exception] = None
        maintenance_errors: List[str] = []

        try:
            self._require_descriptor_publication_support()
            output_dir.mkdir(parents=True, exist_ok=True)
            output_dir_fd = self._open_bound_directory(
                str(output_dir), purpose="output directory"
            )
            candidate_dir_name, candidate_identity = self._create_candidate_directory(
                output_dir_fd,
                prefix=f".{Path(output_path).stem}.candidate-",
            )
            candidate_dir_path = str(output_dir / candidate_dir_name)
            candidate_dir_fd = self._open_bound_directory(
                candidate_dir_name,
                purpose="candidate directory",
                dir_fd=output_dir_fd,
                expected_mode=0o700,
            )
            candidate_stat = os.fstat(candidate_dir_fd)
            if (candidate_stat.st_dev, candidate_stat.st_ino) != candidate_identity:
                raise RuntimeError(
                    "Candidate directory identity changed while it was being bound; "
                    f"private candidate may be retained at '{candidate_dir_path}'"
                )

            serialization_dir_path = tempfile.mkdtemp(
                prefix="aelira_pdf_serialization_"
            )
            self._validate_serialization_directory(serialization_dir_path, output_dir)
            serialization_path = str(Path(serialization_dir_path) / "serialized.pdf")
            self._write_pdf_output(document, serialization_path)
            close_error = self._close_document_handles(document)
            document_handles_closed = True
            if close_error:
                raise RuntimeError(close_error)
            self._copy_serialized_pdf_to_candidate(serialization_path, candidate_dir_fd)
            serialization_cleanup_attempted = True
            serialization_cleanup_error = self._remove_serialization_directory(
                serialization_dir_path
            )
            if serialization_cleanup_error:
                raise RuntimeError(serialization_cleanup_error)
            serialization_dir_path = None
            (
                pdf_candidate_fd,
                validated_candidate_size,
                validated_candidate_sha256,
            ) = self._validate_output_candidate(
                "candidate.pdf", dir_fd=candidate_dir_fd
            )
            pdf_candidate_metadata = os.fstat(pdf_candidate_fd)
            pdf_candidate_identity = (
                pdf_candidate_metadata.st_dev,
                pdf_candidate_metadata.st_ino,
            )
            try:
                local_claim = (
                    DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
                        pdf_candidate_fd,
                        filename=output_name,
                        display_path=output_path,
                        mime="application/pdf",
                    )
                )
            finally:
                pdf_candidate_fd = None
            if (
                local_claim.size != validated_candidate_size
                or local_claim.sha256 != validated_candidate_sha256
            ):
                raise RuntimeError(
                    "Validated PDF candidate changed while its private output "
                    "claim snapshot was created"
                )

            if self._html_output is not None:
                html_name = f"{Path(output_path).stem}_accessible.html"
                html_path = str(Path(output_path).with_name(html_name))
                html_existed = self._directory_entry_exists(output_dir_fd, html_name)
                self._write_html_candidate("candidate.html", dir_fd=candidate_dir_fd)
                html_candidate_identity = self._validate_html_candidate(
                    "candidate.html", dir_fd=candidate_dir_fd
                )

            self._verify_bound_directory(
                output_dir_fd, str(output_dir), purpose="output directory"
            )
            self._verify_bound_directory(
                candidate_dir_fd,
                candidate_dir_path,
                purpose="candidate directory",
                expected_mode=0o700,
            )
            self._verify_candidate_entry(
                candidate_dir_fd,
                "candidate.pdf",
                pdf_candidate_identity,
                purpose="PDF candidate",
            )
            if html_candidate_identity is not None:
                self._verify_candidate_entry(
                    candidate_dir_fd,
                    "candidate.html",
                    html_candidate_identity,
                    purpose="HTML candidate",
                )
            self._verify_current_path_binding(
                output_dir_fd, str(output_dir), purpose="output directory"
            )
            os.rename(
                "candidate.pdf",
                output_name,
                src_dir_fd=candidate_dir_fd,
                dst_dir_fd=output_dir_fd,
            )
            logger.info("Atomically published validated PDF: %s", output_path)

            if html_path and html_name:
                try:
                    os.rename(
                        "candidate.html",
                        html_name,
                        src_dir_fd=candidate_dir_fd,
                        dst_dir_fd=output_dir_fd,
                    )
                except Exception as e:
                    if html_existed:
                        html_state = f"prior HTML retained at: {html_path}"
                    else:
                        html_state = "no HTML final was published"
                    warning = (
                        "Accessible HTML publication failed after PDF commit; "
                        f"valid PDF retained at: {output_path}; {html_state}: {e}"
                    )
                    logger.warning(warning)
                    self.result.warnings.append(warning)
                else:
                    logger.info("Atomically published accessible HTML: %s", html_path)
                    self.result.warnings.append(
                        f"HTML alternative saved to: {html_path}"
                    )
        except Exception as e:
            primary_error = e
        finally:
            if not document_handles_closed:
                close_error = self._close_document_handles(document)
                if close_error:
                    maintenance_errors.append(close_error)

            if serialization_dir_path and not serialization_cleanup_attempted:
                serialization_cleanup_error = self._remove_serialization_directory(
                    serialization_dir_path
                )
                if serialization_cleanup_error:
                    maintenance_errors.append(serialization_cleanup_error)

            if candidate_dir_name and output_dir_fd is not None:
                cleanup_error = self._remove_candidate_directory(
                    candidate_dir_path or str(output_dir / candidate_dir_name),
                    candidate_dir_name,
                    candidate_dir_fd,
                    output_dir_fd,
                    candidate_identity,
                )
                if cleanup_error:
                    maintenance_errors.append(cleanup_error)

            for descriptor, purpose, retained_path in (
                (
                    candidate_dir_fd,
                    "candidate directory",
                    candidate_dir_path or str(output_dir),
                ),
                (output_dir_fd, "output directory", str(output_dir)),
            ):
                if descriptor is None:
                    continue
                try:
                    os.close(descriptor)
                except Exception as e:
                    close_error = (
                        f"Publication descriptor close failed for {purpose} "
                        f"'{retained_path}'; descriptor may remain open: {e}"
                    )
                    logger.error(close_error)
                    self.result.warnings.append(close_error)
                    maintenance_errors.append(close_error)

        if (
            primary_error is not None or maintenance_errors
        ) and pdf_candidate_fd is not None:
            try:
                os.close(pdf_candidate_fd)
            except Exception as e:
                close_error = (
                    "Validated PDF candidate descriptor close failed for "
                    f"'{output_path}'; descriptor may remain open: {e}"
                )
                logger.error(close_error)
                self.result.warnings.append(close_error)
                maintenance_errors.append(close_error)
            else:
                pdf_candidate_fd = None

        if primary_error is not None or maintenance_errors:
            if local_claim is not None:
                try:
                    local_claim.close()
                except Exception as e:
                    close_error = (
                        "Private output claim close failed after unsuccessful "
                        f"publication for '{output_path}': {e}"
                    )
                    logger.error(close_error)
                    self.result.warnings.append(close_error)
                    maintenance_errors.append(close_error)
                else:
                    local_claim = None

        if primary_error is not None:
            if maintenance_errors:
                raise RuntimeError(
                    f"{primary_error}; {'; '.join(maintenance_errors)}"
                ) from primary_error
            raise primary_error
        if maintenance_errors:
            raise RuntimeError("; ".join(maintenance_errors))

        previous_table_pdf = getattr(self, "_pre_table_pikepdf_doc", None)
        if previous_table_pdf is not None:
            previous_table_pdf.close()
            self._pre_table_pikepdf_doc = None

        if local_claim is None:
            raise RuntimeError("Validated PDF output claim was not retained")
        try:
            self.result.set_output_claim(local_claim)
        except Exception:
            local_claim.close()
            raise
        local_claim = None
        return output_path

    @staticmethod
    def _validate_serialization_directory(
        serialization_dir: str, output_dir: Path
    ) -> None:
        """Require a private library-write directory outside caller output."""
        os.chmod(serialization_dir, 0o700)
        metadata = os.lstat(serialization_dir)
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(
                f"PDF serialization path is not a directory: '{serialization_dir}'"
            )
        if metadata.st_uid != os.geteuid():
            raise RuntimeError(
                "PDF serialization directory is not owned by the current user: "
                f"'{serialization_dir}'"
            )
        if mode != 0o700:
            raise RuntimeError(
                f"PDF serialization directory '{serialization_dir}' has mode "
                f"{mode:#o}; expected 0o700"
            )
        resolved_serialization = Path(serialization_dir).resolve(strict=True)
        resolved_output = output_dir.resolve(strict=True)
        if (
            resolved_serialization == resolved_output
            or resolved_output in resolved_serialization.parents
        ):
            raise RuntimeError(
                "PDF serialization directory must be outside the caller output "
                f"directory: '{serialization_dir}'"
            )

    @staticmethod
    def _copy_serialized_pdf_to_candidate(
        serialization_path: str, candidate_dir_fd: int
    ) -> None:
        """Validate a library serialization and copy it to a bound candidate."""
        source_fd: Optional[int] = None
        destination_fd: Optional[int] = None
        destination_created = False
        transfer_error: Optional[Exception] = None
        close_errors = []

        try:
            source_flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                source_flags |= os.O_CLOEXEC
            try:
                source_fd = os.open(serialization_path, source_flags)
            except Exception as e:
                raise RuntimeError(
                    "PDF serialization source could not be opened securely; "
                    f"symbolic links are forbidden: {e}"
                ) from e

            source_metadata = os.fstat(source_fd)
            if not stat.S_ISREG(source_metadata.st_mode):
                raise RuntimeError("PDF serialization source is not a regular file")
            if source_metadata.st_uid != os.geteuid():
                raise RuntimeError(
                    "PDF serialization source is not owned by the current user"
                )
            if source_metadata.st_size <= 0:
                raise RuntimeError(
                    "PDF serialization source size must be greater than zero"
                )
            if source_metadata.st_size > _MAX_PDF_CANDIDATE_BYTES:
                raise RuntimeError(
                    "PDF serialization source size exceeds the publication "
                    f"limit of {_MAX_PDF_CANDIDATE_BYTES} bytes"
                )

            destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                destination_flags |= os.O_CLOEXEC
            destination_fd = os.open(
                "candidate.pdf",
                destination_flags,
                0o600,
                dir_fd=candidate_dir_fd,
            )
            destination_created = True

            remaining = source_metadata.st_size
            while remaining:
                chunk = os.read(source_fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError(
                        "PDF serialization source ended before its validated size"
                    )
                remaining -= len(chunk)
                offset = 0
                while offset < len(chunk):
                    written = os.write(destination_fd, chunk[offset:])
                    if written <= 0:
                        raise RuntimeError(
                            "PDF candidate copy made no forward progress"
                        )
                    offset += written
            if os.read(source_fd, 1):
                raise RuntimeError(
                    "PDF serialization source grew beyond its validated size"
                )
            os.fsync(destination_fd)
        except Exception as e:
            transfer_error = e
        finally:
            for descriptor, label in (
                (destination_fd, "PDF candidate destination"),
                (source_fd, "PDF serialization source"),
            ):
                if descriptor is None:
                    continue
                try:
                    os.close(descriptor)
                except Exception as e:
                    close_errors.append(f"could not close {label}: {e}")

        if transfer_error is None and not close_errors:
            return

        cleanup_error = None
        if destination_created:
            try:
                os.unlink("candidate.pdf", dir_fd=candidate_dir_fd)
            except FileNotFoundError:
                pass
            except Exception as e:
                cleanup_error = f"could not unlink the bound partial PDF candidate: {e}"

        errors = []
        if transfer_error is not None:
            errors.append(str(transfer_error))
        errors.extend(close_errors)
        if cleanup_error:
            errors.append(cleanup_error)
        raise RuntimeError("PDF serialization copy failed: " + "; ".join(errors))

    def _remove_serialization_directory(self, serialization_dir: str) -> Optional[str]:
        """Remove the private library-write directory or report its path."""
        try:
            shutil.rmtree(serialization_dir)
        except FileNotFoundError:
            return None
        except Exception as e:
            cleanup_error = (
                "Serialization cleanup failed; private serialization directory "
                f"retained at '{serialization_dir}' for operator cleanup: {e}"
            )
            logger.error(cleanup_error)
            self.result.warnings.append(cleanup_error)
            return cleanup_error
        return None

    @staticmethod
    def _require_descriptor_publication_support() -> None:
        """Fail closed unless the host provides every required dir-fd primitive."""
        missing = []
        for constant in ("O_DIRECTORY", "O_NOFOLLOW"):
            if not hasattr(os, constant):
                missing.append(constant)
        supported = getattr(os, "supports_dir_fd", set())
        for function_name, function in _PUBLICATION_DIR_FD_FUNCTIONS.items():
            if function is None or function not in supported:
                missing.append(f"os.{function_name}(dir_fd=...)")
        for function_name in ("fchmod", "fstat", "geteuid"):
            if not hasattr(os, function_name):
                missing.append(f"os.{function_name}")
        if missing:
            raise RuntimeError(
                "Descriptor-bound publication is unsupported on this platform; "
                f"missing: {', '.join(missing)}"
            )

    @classmethod
    def _open_bound_directory(
        cls,
        path: str,
        *,
        purpose: str,
        dir_fd: Optional[int] = None,
        expected_mode: Optional[int] = None,
    ) -> int:
        """Open a directory without symlink traversal and verify it by fstat."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(path, flags, dir_fd=dir_fd)
        except Exception as e:
            raise RuntimeError(
                f"Could not securely open {purpose} '{path}': {e}"
            ) from e
        try:
            if expected_mode is not None:
                os.fchmod(descriptor, expected_mode)
            cls._verify_bound_directory(
                descriptor,
                path,
                purpose=purpose,
                expected_mode=expected_mode,
            )
        except Exception as e:
            try:
                os.close(descriptor)
            except Exception as close_exception:
                raise RuntimeError(
                    f"{e}; descriptor close failed for {purpose} '{path}': "
                    f"{close_exception}"
                ) from e
            raise
        return descriptor

    @staticmethod
    def _verify_bound_directory(
        descriptor: int,
        path: str,
        *,
        purpose: str,
        expected_mode: Optional[int] = None,
    ) -> None:
        """Reject non-directory, unowned, or unexpectedly permissive bindings."""
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"Bound {purpose} '{path}' is not a directory")
        if metadata.st_uid != os.geteuid():
            raise RuntimeError(
                f"Bound {purpose} '{path}' is not owned by the current user"
            )
        if expected_mode is not None:
            if mode != expected_mode:
                raise RuntimeError(
                    f"Bound {purpose} '{path}' has mode {mode:#o}; "
                    f"expected {expected_mode:#o}"
                )
        elif mode & 0o022:
            raise RuntimeError(
                f"Bound {purpose} '{path}' is unexpectedly permissive "
                f"(mode {mode:#o})"
            )

    @staticmethod
    def _verify_current_path_binding(
        descriptor: int, path: str, *, purpose: str
    ) -> None:
        """Require the current pathname to identify the retained directory."""
        bound_metadata = os.fstat(descriptor)
        try:
            path_metadata = os.lstat(path)
        except Exception as e:
            raise RuntimeError(
                f"Current {purpose} pathname '{path}' cannot be verified: {e}"
            ) from e
        if not stat.S_ISDIR(path_metadata.st_mode):
            raise RuntimeError(
                f"Current {purpose} pathname '{path}' is not a directory"
            )
        bound_identity = (bound_metadata.st_dev, bound_metadata.st_ino)
        path_identity = (path_metadata.st_dev, path_metadata.st_ino)
        if path_identity != bound_identity:
            raise RuntimeError(
                f"Current {purpose} pathname '{path}' no longer identifies "
                "the retained publication directory"
            )

    @staticmethod
    def _create_candidate_directory(
        output_dir_fd: int, *, prefix: str
    ) -> tuple[str, tuple[int, int]]:
        """Create one private candidate child through the bound output fd."""
        for _ in range(100):
            candidate_name = f"{prefix}{secrets.token_hex(4)}"
            try:
                os.mkdir(candidate_name, 0o700, dir_fd=output_dir_fd)
            except FileExistsError:
                continue
            try:
                metadata = os.stat(
                    candidate_name, dir_fd=output_dir_fd, follow_symlinks=False
                )
            except Exception as e:
                try:
                    os.rmdir(candidate_name, dir_fd=output_dir_fd)
                except Exception as cleanup_exception:
                    raise RuntimeError(
                        "Could not verify newly created candidate directory; "
                        f"cleanup also failed for '{candidate_name}': "
                        f"{cleanup_exception}"
                    ) from e
                raise RuntimeError(
                    f"Could not verify newly created candidate directory: {e}"
                ) from e
            return candidate_name, (metadata.st_dev, metadata.st_ino)
        raise RuntimeError("Could not allocate a unique private candidate directory")

    @staticmethod
    def _directory_entry_exists(directory_fd: int, name: str) -> bool:
        """Check an entry relative to a retained directory descriptor."""
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _verify_candidate_entry(
        directory_fd: int,
        name: str,
        expected_identity: Optional[tuple[int, int]],
        *,
        purpose: str,
    ) -> None:
        """Require a candidate basename to still identify its validated file."""
        if expected_identity is None:
            raise RuntimeError(f"{purpose} has no validated file identity")
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"{purpose} is no longer a regular file")
        if (metadata.st_dev, metadata.st_ino) != expected_identity:
            raise RuntimeError(f"{purpose} identity changed after validation")

    def _write_html_candidate(
        self, candidate_path: str, *, dir_fd: Optional[int] = None
    ) -> None:
        """Exclusively create the HTML candidate without following symlinks."""
        if dir_fd is None:
            raise RuntimeError("HTML candidate write requires a bound directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(candidate_path, flags, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                stream.write(self._html_output or "")
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _validate_html_candidate(
        candidate_path: str, *, dir_fd: Optional[int] = None
    ) -> tuple[int, int]:
        """Require a non-empty UTF-8 regular file before publication."""
        if dir_fd is None:
            raise RuntimeError("HTML candidate validation requires a bound directory")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(candidate_path, flags, dir_fd=dir_fd)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(
                    "HTML candidate validation failed: not a regular file"
                )
            if metadata.st_uid != os.geteuid():
                raise RuntimeError(
                    "HTML candidate validation failed: file is not owned by "
                    "the current user"
                )
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = None
                content = stream.read()
        except UnicodeDecodeError as e:
            raise RuntimeError(
                f"HTML candidate validation failed: content is not valid UTF-8 ({e})"
            ) from e
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not content:
            raise RuntimeError("HTML candidate validation failed: content is empty")
        return metadata.st_dev, metadata.st_ino

    def _close_document_handles(self, document: Any) -> Optional[str]:
        """Close save-time handles; final cleanup remains a safety net."""
        close_errors = []
        handles = [("PDF document", document)]
        if self._pikepdf_doc is not None and self._pikepdf_doc is not document:
            handles.append(("pikepdf document", self._pikepdf_doc))
        for label, handle in handles:
            try:
                if not getattr(handle, "is_closed", False):
                    handle.close()
            except Exception as e:
                close_errors.append(f"could not close {label}: {e}")
        self._pdf = None
        self._pikepdf_doc = None
        if close_errors:
            return "Save handle cleanup failed: " + "; ".join(close_errors)
        return None

    def _validate_output_candidate(
        self, candidate_path: str, *, dir_fd: Optional[int] = None
    ) -> tuple[int, int, str]:
        """Validate bytes and retain their descriptor, size, and digest."""
        if dir_fd is None:
            raise RuntimeError("PDF candidate validation requires a bound directory")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(candidate_path, flags, dir_fd=dir_fd)
            os.set_inheritable(descriptor, False)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("candidate is not a regular file")
            if metadata.st_uid != os.geteuid():
                raise RuntimeError("candidate is not owned by the current user")
            if metadata.st_size <= 0:
                raise RuntimeError("candidate size must be greater than zero")
            if metadata.st_size > _MAX_PDF_CANDIDATE_BYTES:
                raise RuntimeError(
                    "candidate size exceeds the publication limit of "
                    f"{_MAX_PDF_CANDIDATE_BYTES} bytes"
                )

            remaining = metadata.st_size
            chunks = []
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("candidate ended before its validated size")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise RuntimeError("candidate grew beyond its validated size")
            os.lseek(descriptor, 0, os.SEEK_SET)
            candidate_bytes = b"".join(chunks)

            with fitz.open(stream=candidate_bytes, filetype="pdf") as candidate:
                if not candidate.is_pdf or candidate.page_count < 1:
                    raise RuntimeError("candidate is not a non-empty PDF")
                candidate_pages = candidate.page_count
                candidate_texts = []
                for page in candidate:
                    candidate_texts.append(page.get_text())
            with fitz.open(self._working_path) as source:
                source_pages = source.page_count
            if candidate_pages != source_pages:
                raise RuntimeError(
                    f"candidate page count changed from {source_pages} to "
                    f"{candidate_pages}"
                )
            with pikepdf.open(BytesIO(candidate_bytes)) as candidate_pdf:
                if len(candidate_pdf.pages) != source_pages:
                    raise RuntimeError("candidate page tree is inconsistent")

            if self._require_output_text_layer:
                if getattr(self, "_ocr_pages", []):
                    missing_pages = [
                        page_index + 1
                        for page_index in self._ocr_pages
                        if page_index >= len(candidate_texts)
                        or len(candidate_texts[page_index].strip())
                        < self._MIN_OCR_TEXT_CHARS
                    ]
                else:
                    combined_text = "".join(candidate_texts)
                    missing_pages = (
                        []
                        if len(combined_text.strip()) >= self._MIN_OCR_TEXT_CHARS
                        else [1]
                    )
                if missing_pages:
                    if getattr(self, "_ocr_pages", []):
                        raise RuntimeError(
                            "Remediated output lost the OCR text layer for page(s) "
                            f"{missing_pages}; refusing to deliver an unsearchable file."
                        )
                    raise RuntimeError(
                        "Remediated output lost the OCR text layer; refusing to "
                        "deliver an unsearchable file for an image-only input."
                    )
            if self._bytes_are_identical_to_file(candidate_bytes, self.file_path):
                raise RuntimeError(
                    "Remediated output is byte-identical to the original input; "
                    "refusing to publish a no-op remediation."
                )
        except Exception as e:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise RuntimeError(
                f"Remediated output candidate failed PDF validation: {e}"
            ) from e

        return descriptor, metadata.st_size, hashlib.sha256(candidate_bytes).hexdigest()

    @staticmethod
    def _bytes_are_identical_to_file(candidate_bytes: bytes, path: str) -> bool:
        """Compare in-memory validated bytes with a file in bounded chunks."""
        if len(candidate_bytes) != os.path.getsize(path):
            return False
        offset = 0
        with open(path, "rb") as source:
            while True:
                source_chunk = source.read(1024 * 1024)
                if not source_chunk:
                    return offset == len(candidate_bytes)
                next_offset = offset + len(source_chunk)
                if candidate_bytes[offset:next_offset] != source_chunk:
                    return False
                offset = next_offset

    @staticmethod
    def _files_are_identical(first_path: str, second_path: str) -> bool:
        """Compare two files byte-for-byte without loading them into memory."""
        if os.path.getsize(first_path) != os.path.getsize(second_path):
            return False
        with open(first_path, "rb") as first, open(second_path, "rb") as second:
            while True:
                first_chunk = first.read(1024 * 1024)
                second_chunk = second.read(1024 * 1024)
                if first_chunk != second_chunk:
                    return False
                if not first_chunk:
                    return True

    def _remove_candidate_directory(
        self,
        candidate_dir: str,
        candidate_name: str,
        candidate_dir_fd: Optional[int],
        output_dir_fd: int,
        candidate_identity: Optional[tuple[int, int]],
    ) -> Optional[str]:
        """Remove only expected candidate entries through retained descriptors."""
        errors = []
        if candidate_dir_fd is not None:
            for filename in ("candidate.pdf", "candidate.html"):
                try:
                    os.unlink(filename, dir_fd=candidate_dir_fd)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    errors.append(f"could not unlink {filename}: {e}")

        try:
            current = os.stat(
                candidate_name,
                dir_fd=output_dir_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if candidate_dir_fd is not None:
                errors.append(
                    "candidate child is no longer reachable by its bound name"
                )
        except Exception as e:
            errors.append(f"could not verify candidate child identity: {e}")
        else:
            current_identity = (current.st_dev, current.st_ino)
            if candidate_identity is None or current_identity != candidate_identity:
                errors.append(
                    "candidate child identity changed; replacement not removed"
                )
            elif errors:
                pass
            else:
                try:
                    os.rmdir(candidate_name, dir_fd=output_dir_fd)
                except Exception as e:
                    errors.append(f"could not remove candidate directory: {e}")

        if not errors:
            return None
        cleanup_error = (
            "Candidate cleanup failed; private candidate directory retained at "
            f"'{candidate_dir}' for operator cleanup: {'; '.join(errors)}"
        )
        logger.error(cleanup_error)
        self.result.warnings.append(cleanup_error)
        return cleanup_error

    def _write_pdf_output(self, document: Any, output_path: str) -> None:
        """Write the current PDF state to ``output_path``."""

        # If we modified the structure tree with pikepdf, save with pikepdf
        # This is critical because PyMuPDF cannot save structure tree changes
        if self._structure_modified and self._pikepdf_doc:
            logger.info("Saving PDF with pikepdf (structure tree was modified)")
            working_pdf = self._pikepdf_doc
            working_struct_tree = self._struct_tree
            working_fitz = self._pdf
            transaction_fitz = None
            try:
                pending_requests = [
                    staged.pending_association
                    for _, staged in self._pending_image_equations
                    if staged.pending_association is not None
                ]
                occurrence_keys = [
                    (
                        int(pending.page_number),
                        int(pending.image_xref),
                        int(pending.image_index),
                        int(pending.occurrence_ordinal),
                        str(pending.occurrence_id),
                    )
                    for pending in pending_requests
                ]
                if len(occurrence_keys) != len(set(occurrence_keys)):
                    raise RuntimeError("Duplicate pending image-equation occurrence")

                if pending_requests:
                    transaction_bytes = BytesIO()
                    self._pikepdf_doc.save(transaction_bytes)
                    transaction_bytes.seek(0)
                    working_pdf = pikepdf.open(transaction_bytes)
                    working_struct_tree = PDFStructureTree(working_pdf)
                    transaction_fitz = fitz.open(
                        stream=transaction_bytes.getvalue(), filetype="pdf"
                    )
                    working_fitz = transaction_fitz

                working_pending_requests = []
                for pending in pending_requests:
                    occurrences = _displayed_image_occurrences(
                        working_fitz[pending.page_number - 1], pending.page_number
                    )
                    matches = [
                        occurrence
                        for occurrence in occurrences
                        if occurrence["image_index"] == pending.image_index
                        and occurrence["occurrence_ordinal"]
                        == pending.occurrence_ordinal
                        and all(
                            abs(left - right) <= 1e-6
                            for left, right in zip(occurrence["bbox"], pending.bbox)
                        )
                    ]
                    if len(matches) != 1:
                        raise RuntimeError("Pending occurrence changed in transaction")
                    occurrence = matches[0]
                    image_bytes = working_fitz.extract_image(
                        occurrence["image_xref"]
                    ).get("image")
                    working_pending_requests.append(
                        replace(
                            pending,
                            image_xref=occurrence["image_xref"],
                            occurrence_id=occurrence["occurrence_id"],
                            image_stream_sha256=hashlib.sha256(image_bytes).hexdigest(),
                        )
                    )

                # Apply pending bookmarks via pikepdf BEFORE saving
                # (PyMuPDF's set_toc changes are lost when saving with pikepdf)
                if self._pending_bookmarks and working_struct_tree:
                    logger.info(
                        f"Adding {len(self._pending_bookmarks)} bookmarks via pikepdf"
                    )
                    working_struct_tree.add_bookmarks(self._pending_bookmarks)

                # Tag content streams with BDC/EMC markers and build ParentTree
                try:
                    tagger = ContentTaggerV2(
                        working_pdf,
                        working_fitz,
                        excluded_image_occurrences=working_pending_requests,
                    )
                    stats = tagger.tag_all_pages()
                    self._content_tagger_stats = stats
                    tagged = stats.get("blocks_matched", 0) + stats.get(
                        "blocks_created", 0
                    )
                    if tagged:
                        logger.info(
                            "Content streams tagged with BDC/EMC markers (v2): "
                            "%d pages, %d matched, %d created",
                            stats.get("pages_processed", 0),
                            stats.get("blocks_matched", 0),
                            stats.get("blocks_created", 0),
                        )
                    else:
                        # Tagging nothing is a silent failure: the structure
                        # tree ends up unreachable from page content, so the
                        # document reads as tagged but is not navigable.
                        logger.warning(
                            "ContentTaggerV2 marked no content across %d page(s); "
                            "structure tree will not be reachable from page "
                            "content",
                            stats.get("pages_processed", 0),
                        )
                except Exception as e:
                    if self._pending_image_equations:
                        raise RuntimeError(
                            "ContentTaggerV2 failed before exact image-equation "
                            "association"
                        ) from e
                    logger.warning(f"ContentTaggerV2 failed, falling back to v1: {e}")
                    try:
                        tagger_v1 = ContentTagger(self._pikepdf_doc)
                        tagger_v1.tag_all_pages()
                        logger.info(
                            "Content streams tagged with BDC/EMC markers (v1 fallback)"
                        )
                    except Exception as e2:
                        logger.warning(
                            f"Content stream tagging failed (non-fatal): {e2}"
                        )

                staged_associations = []
                for pending_index, (issue, staged) in enumerate(
                    self._pending_image_equations
                ):
                    pending = staged.pending_association
                    if pending is None:
                        raise RuntimeError(
                            "Missing typed image-equation association request"
                        )
                    association = associate_image_formula(
                        working_pdf,
                        working_fitz,
                        working_pending_requests[pending_index],
                    )
                    if not association.success:
                        raise RuntimeError(
                            "Exact image-equation association failed: "
                            f"{association.error or 'unknown'}"
                        )
                    staged_associations.append((issue, staged, association))

                working_pdf.save(output_path)
                for issue, staged, association in staged_associations:
                    if not verify_image_formula_association(
                        output_path, staged.pending_association, association
                    ):
                        raise RuntimeError(
                            "Post-save image-equation association verification failed"
                        )
                expected_table_cells = int(
                    getattr(self, "_table_expected_bound_cells", 0)
                )
                if expected_table_cells and not TableTagger.verify_file(
                    output_path, expected_table_cells
                ):
                    raise RuntimeError(
                        "Post-save table association verification failed"
                    )
                self._verified_image_equations = staged_associations
                if working_pdf is not self._pikepdf_doc:
                    original_pdf = self._pikepdf_doc
                    self._pikepdf_doc = working_pdf
                    self._struct_tree = working_struct_tree
                    original_pdf.close()
                if transaction_fitz is not None:
                    transaction_fitz.close()
                logger.info("Successfully saved PDF with structure tree modifications")
            except Exception as e:
                if transaction_fitz is not None:
                    transaction_fitz.close()
                if working_pdf is not self._pikepdf_doc:
                    working_pdf.close()
                logger.error(f"Failed to save with pikepdf: {e}")
                if getattr(self, "_table_expected_bound_cells", 0):
                    self._rollback_provisional_table_fixes()
                    Path(output_path).unlink(missing_ok=True)
                    if not self._has_only_table_issues():
                        return self._write_pdf_output(document, output_path)
                    raise
                if self._pending_image_equations:
                    self._verified_image_equations = []
                    raise
                # Fall back to PyMuPDF
                document.save(output_path, garbage=4, deflate=True)
        else:
            # Use PyMuPDF for non-structure changes (metadata, bookmarks)
            document.save(output_path, garbage=4, deflate=True)

    def _rollback_provisional_table_fixes(self) -> None:
        """Restore the pre-table PDF and move provisional fixes to review."""
        provisional_ids = set(getattr(self, "_table_fixed_issue_ids", set()))
        current_pdf = self._pikepdf_doc
        previous_pdf = getattr(self, "_pre_table_pikepdf_doc", None)
        if previous_pdf is not None:
            self._pikepdf_doc = previous_pdf
            self._struct_tree = PDFStructureTree(previous_pdf)
        self._pre_table_pikepdf_doc = None
        self._structure_modified = getattr(self, "_pre_table_structure_modified", False)
        self._table_expected_bound_cells = 0
        self._table_fixed_issue_ids = set()
        if current_pdf is not None and current_pdf is not previous_pdf:
            current_pdf.close()

        self.result.fixed_issues = [
            fixed
            for fixed in self.result.fixed_issues
            if fixed.issue_id not in provisional_ids
        ]
        self.result.fixed_count = len(self.result.fixed_issues)
        manual_ids = {manual.issue_id for manual in self.result.manual_issues}
        for issue in self.issues:
            if issue.id not in provisional_ids or issue.id in manual_ids:
                continue
            self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
            issue.metadata["remediation_error_code"] = TABLE_STRUCTURE_NOT_VERIFIED
            self._add_manual_issue(
                issue,
                reason=TABLE_STRUCTURE_NOT_VERIFIED,
                recommendation=(
                    "Verify the table's real cell content and structure manually."
                ),
            )

    def _discard_failed_table_output(self, output_path: str) -> None:
        """Remove only the resolved deterministic output after refusal."""
        source = Path(self.file_path)
        destination = Path(output_path)
        configured_directory = (
            Path(self.config.output_directory)
            if self.config.output_directory
            else source.parent
        )
        try:
            resolved_directory = configured_directory.resolve(strict=False)
            resolved_parent = destination.parent.resolve(strict=False)
            resolved_source = source.resolve(strict=False)
        except OSError as exc:
            logger.warning("Could not resolve failed table output safely: %s", exc)
            return

        candidate = resolved_parent / destination.name
        try:
            candidate.relative_to(resolved_directory)
        except ValueError:
            logger.warning(
                "Refusing to remove failed table output outside its directory: %s",
                output_path,
            )
            return
        if candidate == resolved_source:
            logger.warning("Refusing to remove the source PDF: %s", candidate)
            return
        if candidate.is_dir() and not candidate.is_symlink():
            logger.warning("Refusing to remove output directory: %s", candidate)
            return
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not remove failed table output %s: %s", candidate, exc
            )

    # Issue types ContentTaggerV2 always resolves when it completes, and
    # those it only resolves when it actually tagged content blocks
    # (an empty tagging pass leaves the ParentTree /Nums empty).
    _TAGGER_FIXED_ALWAYS = frozenset(
        ["missing_document_root", "missing_pdfua_identifier"]
    )
    _TAGGER_FIXED_IF_TAGGED = frozenset(
        ["missing_content_marking", "empty_parent_tree"]
    )

    def _reconcile_content_tagger_fixes(self) -> None:
        """Move manual issues that ContentTaggerV2 actually fixed to the
        fixed bucket (issue #48).

        Phase 1 files the scanner's document-level structure findings as
        manual because no per-issue fixer handles them, but the tagger
        resolves exactly these during save: content marking (BDC/EMC),
        ParentTree /Nums, /Document root, and the PDF/UA identifier.
        Reclassification is driven by the tagger's own stats, so a v1
        fallback or tagger failure leaves the issues manual.
        """
        stats = self._content_tagger_stats
        if stats is None:
            return

        tagged = stats.get("blocks_matched", 0) + stats.get("blocks_created", 0)
        handled = set(self._TAGGER_FIXED_ALWAYS)
        if tagged > 0:
            handled |= self._TAGGER_FIXED_IF_TAGGED

        issues_by_id = {issue.id: issue for issue in self.issues}
        remaining_manual = []
        for manual in self.result.manual_issues:
            issue_type = manual.metadata.get("issue_type", "")
            original = issues_by_id.get(manual.issue_id)
            if issue_type not in handled or original is None:
                remaining_manual.append(manual)
                continue

            self.result.manual_count -= 1
            confidence = self._confidence.calculate(FixMethod.RULE, verified=True)
            self._add_fixed_issue(
                original,
                fixed_content=(
                    "Resolved by content tagging pass: "
                    f"{issue_type.replace('_', ' ')}"
                ),
                fix_method=FixMethod.RULE.value,
                confidence=confidence,
                needs_review=self._confidence.needs_review(confidence),
                wcag_criteria=self._wcag_map.get(original.category),
                page_number=original.metadata.get("page_number"),
            )
            logger.info(
                "Reclassified manual issue as fixed by ContentTagger: %s (%s)",
                manual.issue_id,
                issue_type,
            )
        self.result.manual_issues = remaining_manual

    def _reconcile_verified_image_equations(self) -> None:
        """Promote only image equations proven in the reopened saved candidate."""
        if not self._verified_image_equations:
            return
        remaining_manual = list(self.result.manual_issues)
        for issue, staged, association in self._verified_image_equations:
            if not getattr(association, "success", False):
                continue
            matching = [
                manual for manual in remaining_manual if manual.issue_id == issue.id
            ]
            if len(matching) != 1:
                continue
            remaining_manual.remove(matching[0])
            self.result.manual_count -= 1
            evidence = asdict(staged.verification_evidence)
            self._add_fixed_issue(
                issue,
                fixed_content=staged.aria_label,
                fix_method="ai_vision",
                confidence=min(float(staged.confidence), 0.55),
                needs_review=True,
                provider_used=staged.provider_used,
                model_used=staged.model_used,
                source_kind="image_equation",
                verification_evidence=evidence,
                wcag_criteria="1.1.1",
                page_number=staged.page_number,
            )
        self.result.manual_issues = remaining_manual

    def _reload_pikepdf_doc(self) -> None:
        """Reload the pikepdf document handle from disk.

        Called after an external tool (e.g. TableTagger) has saved changes
        directly to the working copy with its own pikepdf handle. Without
        this reload, _save_document() would overwrite those changes with the
        stale in-memory state.
        """
        if self._pikepdf_doc:
            try:
                self._pikepdf_doc.close()
            except Exception:
                pass

        try:
            self._pikepdf_doc = pikepdf.open(self._working_path)
            self._struct_tree = PDFStructureTree(self._pikepdf_doc)
            logger.debug("Reloaded pikepdf document after external modification")
        except Exception as e:
            logger.error("Failed to reload pikepdf document: %s", e)
            self._pikepdf_doc = None
            self._struct_tree = None

    def can_auto_fix(self, issue: RemediationIssue) -> bool:
        """
        Determine if an issue can be automatically fixed.

        With pikepdf, we can now do DIRECT PDF fixes:
        - Language: Set /Lang in PDF Catalog
        - Title: Set dc:title in metadata + DisplayDocTitle
        - Navigation: Add bookmarks/outline
        - Structure: Add structure tags (H1-H6, Table, etc.) + create structure tree
        - Alt text: Embed directly in Figure structure elements
        - Heading: Add H1-H6 structure tags
        """
        if issue.category not in self.AUTO_FIXABLE_CATEGORIES:
            return False

        if issue.category == IssueCategory.LANGUAGE:
            return True  # Can always set language in Catalog

        if issue.category == IssueCategory.TITLE:
            return True  # Can always set title in metadata

        if issue.category == IssueCategory.NAVIGATION:
            # Can add bookmarks if we have heading info or just need outline
            return True  # Always fixable - we can add basic outline

        if issue.category == IssueCategory.STRUCTURE:
            # Can create/tag document structure with pikepdf
            issue_type = issue.metadata.get("issue_type")
            if issue_type in [
                "missing_tags",
                "missing_headings",
                "missing_structure",
                "missing_structure_tree",
                "empty_structure_tree",
                "not_marked_tagged",
                # Specialist module issue types
                "missing_role_map",
                "incomplete_role_map",
                "missing_tounicode",
                *MATH_SPECIALIST_ISSUE_TYPES,
            ]:
                return bool(self._struct_tree)  # Need pikepdf for structure fixes
            return False

        if issue.category == IssueCategory.ALT_TEXT:
            # With pikepdf, we can embed alt text directly in PDF structure
            # Requires either AI for generating alt text or existing alt text in metadata
            if self._struct_tree:
                return (
                    self.config.use_ai
                    or self.config.fix_alt_text
                    or bool(
                        issue.metadata.get("suggested_alt_text")
                        or issue.metadata.get("generated_alt_text")
                    )
                )
            # Fall back to HTML alternative if no pikepdf
            return self.config.use_ai or self.config.fix_alt_text

        if issue.category == IssueCategory.HEADING:
            # Can add heading structure tags with pikepdf
            # Even without pikepdf, we can add bookmarks as fallback
            # Heading level defaults to 1 if not specified
            logger.debug(
                f"HEADING can_auto_fix: struct_tree={self._struct_tree is not None}, "
                f"metadata={issue.metadata}"
            )
            return True  # Always fixable - we have bookmarks as fallback

        if issue.category == IssueCategory.READING_ORDER:
            # Reading order fix requires both PyMuPDF and pikepdf
            return HAS_PYMUPDF and HAS_PIKEPDF

        if issue.category == IssueCategory.TABLE:
            # Route through the deterministic preflight/refusal path even
            # when detection dependencies are unavailable.
            return True

        if issue.category == IssueCategory.LIST:
            # List structure tagging requires pikepdf + PyMuPDF for text detection
            return HAS_PYMUPDF and HAS_PIKEPDF

        if issue.category == IssueCategory.FORM:
            # Form field tooltip + tab order fixes require pikepdf
            return HAS_PIKEPDF

        if issue.category == IssueCategory.LINK:
            # Link annotation /Contents fixes require pikepdf
            return HAS_PIKEPDF

        return False

    def apply_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply a fix to the PDF document.

        Args:
            issue: The issue being fixed
            document: The PDF document object (PyMuPDF for reading)
            fix_content: The content to apply as the fix

        Returns:
            True if fix was applied successfully
        """
        try:
            if issue.category == IssueCategory.LANGUAGE:
                return self._apply_language_fix(issue, document, fix_content)

            if issue.category == IssueCategory.TITLE:
                return self._apply_title_fix(issue, document, fix_content)

            if issue.category == IssueCategory.NAVIGATION:
                return self._apply_navigation_fix(issue, document, fix_content)

            if issue.category == IssueCategory.STRUCTURE:
                return self._apply_structure_fix(issue, document, fix_content)

            if issue.category == IssueCategory.ALT_TEXT:
                return self._apply_alt_text_fix(issue, document, fix_content)

            if issue.category == IssueCategory.HEADING:
                return self._apply_heading_fix(issue, document, fix_content)

            if issue.category == IssueCategory.READING_ORDER:
                return self._apply_reading_order_fix(issue, document, fix_content)

            if issue.category == IssueCategory.TABLE:
                return self._apply_table_fix(issue, document, fix_content)

            if issue.category == IssueCategory.LIST:
                return self._apply_list_fix(issue, document, fix_content)

            return False

        except Exception as e:
            logger.error(f"Failed to apply fix for issue {issue.id}: {e}")
            return False

    def _apply_language_fix(
        self, issue: RemediationIssue, document: Any, language: str
    ) -> bool:
        """Set PDF document language in Catalog and metadata."""
        try:
            lang_code = language or "en"

            # Use pikepdf to set /Lang in Catalog (the proper way for PDF/UA)
            if self._struct_tree:
                if self._struct_tree.set_document_language(lang_code):
                    self._structure_modified = True
                    logger.info(f"Set PDF /Lang in Catalog to: {lang_code}")
                    return True

            # Fall back to PyMuPDF metadata (less effective but better than nothing)
            metadata = document.metadata
            metadata["language"] = lang_code
            document.set_metadata(metadata)

            logger.info(f"Set PDF language metadata to: {lang_code}")
            return True

        except Exception as e:
            logger.error(f"Error setting PDF language: {e}")
            return False

    def _apply_title_fix(
        self, issue: RemediationIssue, document: Any, title: str
    ) -> bool:
        """Set PDF document title in metadata and ViewerPreferences."""
        try:
            # Try to get a good title
            doc_title = title
            if not doc_title:
                # Try to extract from first page text
                if document and len(document) > 0:
                    try:
                        first_page_text = document[0].get_text("text").strip()
                        if first_page_text:
                            lines = [
                                line.strip()
                                for line in first_page_text.split("\n")
                                if line.strip()
                            ]
                            if lines:
                                doc_title = lines[0][:100]
                    except Exception:
                        pass

            if not doc_title:
                # Use filename as fallback
                doc_title = (
                    Path(self.file_path).stem.replace("_", " ").replace("-", " ")
                )

            # Use pikepdf to set title properly
            if self._struct_tree:
                if self._struct_tree.set_document_title(doc_title):
                    self._structure_modified = True
                    logger.info(f"Set PDF title via pikepdf: {doc_title}")
                    return True

            # Fall back to PyMuPDF metadata
            metadata = document.metadata
            metadata["title"] = doc_title
            document.set_metadata(metadata)

            logger.info(f"Set PDF title in metadata: {doc_title}")
            return True

        except Exception as e:
            logger.error(f"Error setting PDF title: {e}")
            return False

    def _normalize_toc_hierarchy(self, toc: List[List]) -> List[List]:
        """
        Normalize TOC hierarchy to ensure valid levels.

        PyMuPDF requires that:
        - First item must be level 1
        - No level can jump more than 1 from the previous item
        - e.g., [1, 3, 2] is invalid (can't go from 1 to 3)
        - e.g., [1, 2, 3, 2, 1] is valid
        """
        if not toc:
            return toc

        normalized = []
        prev_level = 0

        for item in toc:
            level, title, page = item[0], item[1], item[2]

            # Ensure first item is level 1
            if prev_level == 0:
                level = 1
            # Ensure we don't jump more than 1 level
            elif level > prev_level + 1:
                level = prev_level + 1

            normalized.append([level, title, page])
            prev_level = level

        return normalized

    def _apply_navigation_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """Add bookmarks/outline to PDF based on headings."""
        try:
            headings = issue.metadata.get("headings", [])

            if not headings:
                # Try to detect headings from document
                headings = self._detect_headings(document)

            if not headings:
                logger.warning("No headings found for bookmark creation")
                return False

            # Store bookmarks for pikepdf (will be applied when saving)
            # This is needed because PyMuPDF's set_toc() changes are lost
            # when we save with pikepdf (which we do when structure is modified)
            for heading in headings:
                level = heading.get("level", 1)
                title = heading.get("text", "")[:100]
                page = heading.get("page", 1)

                if title.strip():
                    self._pending_bookmarks.append(
                        {
                            "level": level,
                            "title": title,
                            "page": page,
                        }
                    )

            # Build TOC entries
            new_toc_entries = []
            for heading in headings:
                level = heading.get("level", 1)
                title = heading.get("text", "")[:100]
                page = heading.get("page", 1)
                if title.strip():
                    new_toc_entries.append([level, title, page])

            # Normalize hierarchy to avoid "bad hierarchy level" errors
            new_toc_entries = self._normalize_toc_hierarchy(new_toc_entries)

            # Also set via PyMuPDF for non-structure-modified saves
            toc = document.get_toc()
            toc.extend(new_toc_entries)
            document.set_toc(toc)

            logger.info(f"Added {len(headings)} bookmarks to PDF")
            return True

        except Exception as e:
            logger.error(f"Error adding PDF bookmarks: {e}")
            return False

    def _detect_headings(self, document: Any) -> List[Dict]:
        """Detect headings in PDF based on font size and styling."""
        headings = []

        try:
            for page_num in range(len(document)):
                page = document[page_num]
                blocks = page.get_text("dict")["blocks"]

                for block in blocks:
                    if "lines" not in block:
                        continue

                    for line in block["lines"]:
                        for span in line["spans"]:
                            font_size = span.get("size", 12)
                            text = span.get("text", "").strip()
                            flags = span.get("flags", 0)

                            # Detect headings by size and styling
                            if font_size >= 18 and text:
                                # Likely H1
                                headings.append(
                                    {"level": 1, "text": text, "page": page_num + 1}
                                )
                            elif font_size >= 14 and (flags & 2**4):  # Bold flag
                                # Likely H2
                                headings.append(
                                    {"level": 2, "text": text, "page": page_num + 1}
                                )
                            elif font_size >= 12 and (flags & 2**4) and len(text) < 100:
                                # Likely H3
                                headings.append(
                                    {"level": 3, "text": text, "page": page_num + 1}
                                )

            # Remove duplicates
            seen = set()
            unique_headings = []
            for h in headings:
                key = (h["text"], h["page"])
                if key not in seen:
                    seen.add(key)
                    unique_headings.append(h)

            return unique_headings[:50]  # Limit to 50 headings

        except Exception as e:
            logger.error(f"Error detecting headings: {e}")
            return []

    def _apply_structure_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """Apply structure fixes to PDF.

        Handles:
        - missing_structure_tree: Creates StructTreeRoot via pikepdf
        - empty_structure_tree: Adds basic structure elements
        - not_marked_tagged: Sets MarkInfo.Marked = true
        - missing_tags: Adds bookmarks as navigation fallback
        - missing_headings: Generates accessible HTML
        """
        try:
            issue_type = issue.metadata.get("issue_type")
            logger.info(f"Applying structure fix for issue_type: {issue_type}")

            if issue_type in [
                "missing_structure_tree",
                "empty_structure_tree",
                "not_marked_tagged",
            ]:
                # These are all handled by ensuring structure tree exists AND has content
                # PDFStructureTree.__init__ calls _ensure_struct_tree_root which:
                # 1. Creates StructTreeRoot if missing
                # 2. Sets MarkInfo.Marked = true
                if self._struct_tree:
                    # Structure tree already exists (was created in _load_document)
                    # Always add at least a basic heading to ensure structure is non-empty
                    # This prevents the follow-up "empty_structure_tree" issue
                    if issue_type in ["missing_structure_tree", "empty_structure_tree"]:
                        # Add at least a basic heading to make structure non-empty
                        # Try to get title from first page
                        title = "Document"
                        if document and len(document) > 0:
                            try:
                                first_page_text = document[0].get_text("text").strip()
                                if first_page_text:
                                    lines = [
                                        line.strip()
                                        for line in first_page_text.split("\n")
                                        if line.strip()
                                    ]
                                    if lines:
                                        title = lines[0][:100]
                            except Exception:
                                pass
                        self._struct_tree.add_heading(1, 1, title)
                        logger.info(
                            f"Added H1 heading to structure tree: {title[:50]}..."
                        )

                    self._structure_modified = True
                    logger.info(f"Structure tree created/fixed for issue: {issue_type}")
                    return True
                else:
                    logger.warning("pikepdf not available for structure tree creation")
                    return False

            if issue_type == "missing_tags":
                # Add basic document structure - mostly via bookmarks
                return self._apply_navigation_fix(issue, document, fix_content)

            if issue_type == "missing_headings":
                # Generate accessible HTML as alternative
                return self._generate_accessible_html(document)

            return False

        except Exception as e:
            logger.error(f"Error applying structure fix: {e}")
            return False

    def _apply_alt_text_fix(
        self, issue: RemediationIssue, document: Any, alt_text: str
    ) -> bool:
        """
        Apply alt text fix by embedding directly in PDF structure tree.

        With pikepdf, we create a Figure structure element with /Alt entry.
        This is the proper PDF/UA way to add alt text - screen readers will
        announce it when encountering the image.

        Falls back to HTML alternative if pikepdf is not available.
        """
        try:
            page_num = issue.metadata.get("page_number", 1)
            image_index = issue.metadata.get("image_index", 0)
            image_bbox = issue.metadata.get("bbox")

            # Try to embed directly in PDF structure tree (THE KEY FIX)
            if self._struct_tree and alt_text:
                if self._struct_tree.add_alt_text_to_image(
                    page_num=page_num,
                    alt_text=alt_text,
                    image_index=image_index,
                    image_bbox=image_bbox,
                ):
                    self._structure_modified = True
                    logger.info(
                        f"Embedded alt text directly in PDF structure on page {page_num}: "
                        f"{alt_text[:50]}{'...' if len(alt_text) > 50 else ''}"
                    )
                    return True
                else:
                    logger.warning(
                        "Failed to embed alt text in structure, falling back to HTML"
                    )

            # Fall back: Store alt text for HTML generation
            if not hasattr(self, "_alt_texts"):
                self._alt_texts = {}

            key = (page_num, image_index)
            self._alt_texts[key] = alt_text

            # Trigger HTML generation if not already done
            if not self._html_output:
                self._generate_accessible_html(document)

            logger.info(f"Stored alt text for HTML alternative on page {page_num}")
            return True

        except Exception as e:
            logger.error(f"Error applying alt text fix: {e}")
            return False

    def _apply_heading_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply heading structure fix by creating H1-H6 structure elements.

        With pikepdf, we create proper heading structure tags. This enables:
        - Screen reader heading navigation (h key in NVDA/VoiceOver)
        - Document outline generation
        - WCAG 1.3.1 and 2.4.1 compliance

        Falls back to bookmarks if pikepdf is not available.
        """
        try:
            page_num = issue.metadata.get("page_number", 1)
            heading_level = issue.metadata.get("suggested_level", 1)
            heading_text = issue.metadata.get("text", "")
            heading_bbox = issue.metadata.get("bbox")

            logger.info(
                f"_apply_heading_fix: page={page_num}, level={heading_level}, "
                f"text='{heading_text}', fix_content='{fix_content}', "
                f"struct_tree={self._struct_tree is not None}"
            )

            # Parse fix_content if it contains heading info
            if fix_content and not heading_text:
                heading_text = fix_content
                logger.info(f"Using fix_content as heading_text: '{heading_text}'")

            if not heading_text:
                logger.warning("No heading text available, cannot apply heading fix")
                return False

            # Try to add heading structure tag with pikepdf
            if self._struct_tree and heading_text:
                if self._struct_tree.add_heading(
                    page_num=page_num,
                    level=heading_level,
                    text=heading_text,
                    bbox=heading_bbox,
                ):
                    self._structure_modified = True
                    logger.info(
                        f"Added H{heading_level} structure tag on page {page_num}: "
                        f"{heading_text[:50]}{'...' if len(heading_text) > 50 else ''}"
                    )
                    return True
                else:
                    logger.warning(
                        "Failed to add heading structure, falling back to bookmarks"
                    )

            # Fall back: Add as bookmark (still helps navigation)
            try:
                toc = document.get_toc()
                toc.append([heading_level, heading_text[:100], page_num])
                document.set_toc(toc)
                logger.info(f"Added heading as bookmark: {heading_text[:50]}")
                return True
            except Exception as e:
                logger.warning(f"Could not add heading bookmark: {e}")
                return False

        except Exception as e:
            logger.error(f"Error applying heading fix: {e}")
            return False

    def _apply_reading_order_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply reading order fix using the heuristic strategy.

        Creates a HeuristicStrategy, runs it against the PDF file, and
        records the result metadata on the issue for confidence scoring.
        """
        try:
            strategy = HeuristicStrategy()
            result: ReadingOrderFixResult = strategy.fix(self._working_path)

            if not result.success:
                logger.error("Reading order fix failed: %s", result.error)
                return False

            # Store confidence on issue metadata so _compute_fix_metadata can use it
            issue.metadata["reading_order_confidence"] = result.confidence
            issue.metadata["reading_order_layout"] = result.layout_type.value
            issue.metadata["reading_order_reordered"] = result.reordered_count
            issue.metadata["reading_order_artifacts"] = result.artifacts_marked

            if result.reordered_count > 0:
                self._structure_modified = True
                logger.info(
                    "Reading order fix applied: reordered %d elements, "
                    "marked %d artifacts, layout=%s, confidence=%.2f",
                    result.reordered_count,
                    result.artifacts_marked,
                    result.layout_type.value,
                    result.confidence,
                )
            else:
                logger.info(
                    "Reading order already correct (layout=%s, confidence=%.2f)",
                    result.layout_type.value,
                    result.confidence,
                )

            return True

        except Exception as e:
            logger.error("Error applying reading order fix: %s", e)
            return False

    def _apply_table_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """Apply the prepared table batch only after verified content binding."""
        try:
            if not getattr(self, "_table_batch_prepared", False):
                self._prepare_table_fixes()
            if not getattr(self, "_table_binding_attempted", False):
                self._bind_prepared_tables()
            return issue.id in getattr(self, "_table_fixed_issue_ids", set())

        except Exception as e:
            logger.error("Error applying table fix: %s", e)
            self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
            return False

    def _prepare_table_fixes(self) -> None:
        """Preflight table bounds and cache untouched-input detection."""
        if getattr(self, "_table_batch_prepared", False):
            return
        self._table_batch_prepared = True
        self._table_fixed_issue_ids = set()
        self._table_safety_refusals = {}
        self._table_preflight_detection = None
        self._table_binding_attempted = False
        table_issues = [
            issue for issue in self.issues if issue.category == IssueCategory.TABLE
        ]

        try:
            if self._table_preflight_too_complex(table_issues):
                for issue in table_issues:
                    self._record_table_safety_refusal(
                        issue, TABLE_STRUCTURE_TOO_COMPLEX
                    )
                return

            tagger = TableTagger(use_ai=False)
            detection = tagger.detect_tables(self.file_path)
            self._table_preflight_detection = detection
        except Exception as exc:
            logger.error("Table preflight failed closed: %s", exc, exc_info=True)
            for issue in table_issues:
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
            return
        if detection.error_code == TABLE_STRUCTURE_TOO_COMPLEX:
            for issue in table_issues:
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_TOO_COMPLEX)
            return
        if not detection.success or not detection.tables:
            for issue in table_issues:
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)

    def _bind_prepared_tables(self) -> None:
        """Bind the uniquely identified issue tables on a transactional clone."""
        self._table_binding_attempted = True
        table_issues = [
            issue for issue in self.issues if issue.category == IssueCategory.TABLE
        ]
        if self._table_safety_refusals:
            return
        detection = self._table_preflight_detection
        if (
            detection is None
            or not detection.success
            or not detection.tables
            or self._pikepdf_doc is None
            or self._pdf is None
        ):
            for issue in table_issues:
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
            return

        selected: Dict[tuple[int, int], Any] = {}
        issue_tables: Dict[str, Any] = {}
        for issue in table_issues:
            table = self._table_for_issue(issue, detection.tables)
            if table is None:
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
                continue
            key = (table.page_num, table.table_index)
            selected[key] = table
            issue_tables[issue.id] = table

        if not selected:
            return

        tagger = TableTagger(use_ai=False)
        binding = tagger.bind_tables(
            self._pikepdf_doc,
            self._pdf,
            [selected[key] for key in sorted(selected)],
        )
        if (
            not isinstance(binding, TableTagResult)
            or not binding.success
            or binding.bound_pdf is None
        ):
            for issue_id in issue_tables:
                issue = next(item for item in table_issues if item.id == issue_id)
                self._record_table_safety_refusal(issue, TABLE_STRUCTURE_NOT_VERIFIED)
            return

        previous_pdf = self._pikepdf_doc
        self._pre_table_pikepdf_doc = previous_pdf
        self._pre_table_structure_modified = self._structure_modified
        self._pikepdf_doc = binding.bound_pdf
        self._struct_tree = PDFStructureTree(self._pikepdf_doc)
        self._structure_modified = True
        self._table_expected_bound_cells = binding.total_cells
        self._table_fixed_issue_ids.update(issue_tables)

    @staticmethod
    def _table_for_issue(issue: RemediationIssue, tables: List[Any]) -> Optional[Any]:
        """Resolve an issue to one detected table; ambiguity fails closed."""
        page_number = issue.metadata.get("page_number")
        if page_number is None and issue.location:
            match = re.search(r"Page\s+(\d+)", str(issue.location), re.I)
            page_number = int(match.group(1)) if match else None
        candidates = list(tables)
        if page_number is not None:
            try:
                page_index = int(page_number) - 1
            except (TypeError, ValueError):
                return None
            candidates = [table for table in candidates if table.page_num == page_index]

        table_number = issue.metadata.get("table_number")
        if table_number is None and issue.location:
            match = re.search(r"Table\s+(\d+)", str(issue.location), re.I)
            table_number = int(match.group(1)) if match else None
        if table_number is not None:
            try:
                table_index = int(table_number) - 1
            except (TypeError, ValueError):
                return None
            candidates = [
                table for table in candidates if table.table_index == table_index
            ]
        return candidates[0] if len(candidates) == 1 else None

    def _record_table_safety_refusal(
        self, issue: RemediationIssue, error_code: str
    ) -> None:
        """Record a table safety refusal for manual routing by _process_issue."""
        if not hasattr(self, "_table_safety_refusals"):
            self._table_safety_refusals = {}
        self._table_safety_refusals[issue.id] = error_code

    @classmethod
    def _table_preflight_too_complex(cls, table_issues: List[RemediationIssue]) -> bool:
        """Check every issue and aggregate limits before any structure write."""
        if len(table_issues) > MAX_TABLES:
            return True
        total_cells = 0
        for issue in table_issues:
            issue_cells = 0
            for rows, columns in cls._table_issue_dimension_evidence(issue):
                if rows <= 0 or columns <= 0:
                    # Invalid declarations are not evidence and must never
                    # reduce the aggregate contributed by other issues.
                    continue
                if columns > MAX_TABLE_COLUMNS:
                    return True
                # Multiple declarations for one issue describe the same
                # table. Use the largest evidenced grid rather than adding
                # duplicate metadata representations together.
                issue_cells = max(issue_cells, rows * columns)
            total_cells += issue_cells
            if total_cells > MAX_TABLE_CELLS:
                return True
        return False

    @staticmethod
    def _table_issue_dimension_evidence(
        issue: RemediationIssue,
    ) -> List[tuple[int, int]]:
        """Collect every declared grid so smaller metadata cannot hide risk."""
        evidence: List[tuple[int, int]] = []
        metadata = issue.metadata
        for row_key, column_key in (
            ("rows", "columns"),
            ("rows", "cols"),
            ("row_count", "column_count"),
        ):
            rows = metadata.get(row_key)
            columns = metadata.get(column_key)
            if rows is None or columns is None:
                continue
            try:
                evidence.append((int(rows), int(columns)))
            except (TypeError, ValueError):
                evidence.append((0, 0))

        dimension_pattern = re.compile(
            r"(-?\d+)\s*rows?\s*(?:x|×)\s*(-?\d+)\s*col", re.I
        )
        text_sources = (
            metadata.get("element"),
            issue.element_type,
            issue.location,
        )
        for source in text_sources:
            if source is None:
                continue
            evidence.extend(
                (int(rows), int(columns))
                for rows, columns in dimension_pattern.findall(str(source))
            )

        return evidence

    def _is_table_only_manual_noop(self) -> bool:
        return self._has_only_table_issues() and self.result.manual_count == len(
            self.issues
        )

    def _table_batch_is_too_complex(self) -> bool:
        """Return whether untouched-input preflight blocks the whole batch."""
        refusals = getattr(self, "_table_safety_refusals", {})
        return TABLE_STRUCTURE_TOO_COMPLEX in refusals.values()

    def _complete_table_complexity_refusal(self) -> None:
        """Finish an oversized batch without backup, load, mutation, or output."""
        for issue in self.issues:
            if issue.category != IssueCategory.TABLE:
                self.result.skipped_count += 1
                continue
            issue.metadata["remediation_error_code"] = TABLE_STRUCTURE_TOO_COMPLEX
            self._add_manual_issue(
                issue,
                reason=TABLE_STRUCTURE_TOO_COMPLEX,
                recommendation=(
                    "Simplify or split the table, then verify its structure manually."
                ),
            )
        self.result.warnings.append(
            "Remediation was not started because table_structure_too_complex "
            "was detected."
        )
        self._calculate_scores()
        self.result.complete()

    def _has_only_table_issues(self) -> bool:
        return bool(self.issues) and all(
            issue.category == IssueCategory.TABLE for issue in self.issues
        )

    def _close_documents_without_save(self, document: Any) -> None:
        document.close()
        if self._pikepdf_doc:
            try:
                self._pikepdf_doc.close()
            except Exception:
                pass

    # Bullet characters used for list item detection (includes common PDF
    # text-extraction variants where bullets render as special Unicode).
    _BULLET_CHARS = frozenset("•‣⁃∙◦○●◉◆◇▪▫▸▹►▻–—-*→⮞")

    # Regex for ordered list markers: "1.", "1)", "a.", "a)", "i.", "iv)" etc.
    _ORDERED_RE = __import__("re").compile(
        r"^(?:\d{1,3}|[a-zA-Z]|[ivxlIVXL]{1,4})[.)]\s"
    )

    def _apply_list_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply list structure fix using pdf_structure.add_list().

        Detects list items in the PDF text content and creates L/LI/Lbl/LBody
        structure elements via pikepdf.
        """
        try:
            if not self._struct_tree:
                logger.warning("Cannot apply list fix: no structure tree")
                return False

            # Use PyMuPDF to find list items on each page
            with fitz.open(self._working_path) as doc:
                lists_added = 0
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text()
                    lines = text.split("\n")

                    current_list_items: list[str] = []
                    current_ordered = False

                    def _flush_list():
                        nonlocal lists_added
                        if len(current_list_items) >= 2:
                            self._struct_tree.add_list(
                                page_num=page_num + 1,
                                items=current_list_items,
                                ordered=current_ordered,
                            )
                            lists_added += 1

                    for line in lines:
                        line = line.strip()
                        if not line:
                            _flush_list()
                            current_list_items = []
                            continue

                        # Check for bullet list items (single leading char)
                        if line[0] in self._BULLET_CHARS and len(line) > 1:
                            # Strip the bullet and any trailing whitespace
                            item_text = line[1:].lstrip()
                            if item_text:
                                current_list_items.append(item_text)
                                current_ordered = False
                                continue

                        # Check for ordered list markers: "1. ", "a) " etc.
                        m = self._ORDERED_RE.match(line)
                        if m:
                            item_text = line[m.end() :].strip()
                            if item_text:
                                current_list_items.append(item_text)
                                current_ordered = True
                                continue

                        # Non-list line: flush any accumulated items
                        _flush_list()
                        current_list_items = []

                    # Flush any remaining list at end of page
                    _flush_list()
                    current_list_items = []

                if lists_added > 0:
                    self._structure_modified = True
                    logger.info(
                        "List structure fix applied: added %d lists", lists_added
                    )
                    return True
                else:
                    logger.warning(
                        "List fix: no lists detected via text heuristics on %d pages",
                        len(doc),
                    )
                    return False

        except Exception as e:
            logger.error("Error applying list fix: %s", e)
            return False

    def _generate_accessible_html(self, document: Any) -> bool:
        """Generate an accessible HTML version of the PDF."""
        try:
            html_parts = [
                "<!DOCTYPE html>",
                '<html lang="en">',
                "<head>",
                '<meta charset="UTF-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
                f"<title>{_escape_html_interpolation(self._get_document_title(document), quote=False)}</title>",
                "<style>",
                "body { font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }",
                "h1, h2, h3 { color: #1a1a1a; margin-top: 1.5em; }",
                "p { margin: 1em 0; }",
                "img { max-width: 100%; height: auto; }",
                ".page-break { border-top: 1px solid #ccc; margin: 2em 0; padding-top: 1em; }",
                ".page-number { color: #666; font-size: 0.9em; }",
                "</style>",
                "</head>",
                "<body>",
                "<main>",
            ]

            # Process each page
            for page_num in range(len(document)):
                page = document[page_num]

                if page_num > 0:
                    html_parts.append(
                        f'<div class="page-break"><span class="page-number">Page {page_num + 1}</span></div>'
                    )

                # Extract text with structure
                text = _sanitize_pymupdf_html_fragment(page.get_text("html"))

                # Clean and add to output
                html_parts.append(f'<section aria-label="Page {page_num + 1}">')
                html_parts.append(text)

                # Add images with alt text
                images = page.get_images()
                for img_index, img in enumerate(images):
                    alt = self._get_alt_text_for_image(page_num + 1, img_index)
                    html_parts.append(
                        f'<img alt="{_escape_html_interpolation(alt, quote=True)}">'
                    )

                html_parts.append("</section>")

            html_parts.extend(["</main>", "</body>", "</html>"])

            self._html_output = "\n".join(html_parts)
            logger.info("Generated accessible HTML version")
            return True

        except Exception as e:
            logger.error(f"Error generating accessible HTML: {e}")
            return False

    def _get_document_title(self, document: Any) -> str:
        """Get document title from metadata or filename."""
        try:
            metadata = document.metadata
            title = metadata.get("title", "")
            if title:
                return title
        except Exception:
            pass

        return Path(self.file_path).stem

    def _get_alt_text_for_image(self, page_num: int, image_index: int) -> str:
        """Get stored alt text for an image, or generate placeholder."""
        if hasattr(self, "_alt_texts"):
            key = (page_num, image_index)
            if key in self._alt_texts:
                return self._alt_texts[key]

        return f"Image {image_index + 1} on page {page_num}"

    def _get_rule_based_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """Get a rule-based fix for an issue."""
        if issue.category == IssueCategory.ALT_TEXT:
            # Decorative images get empty alt text per WCAG 1.1.1
            if (
                issue.metadata.get("is_decorative")
                or issue.metadata.get("image_type") == "decorative"
            ):
                return ""
            # Use pre-generated alt text from the scanner if available
            if self.config.allow_legacy_nested_ai:
                generated_alt = issue.metadata.get(
                    "suggested_alt_text"
                ) or issue.metadata.get("generated_alt_text")
                if generated_alt:
                    return generated_alt
                if issue.fix_suggestion:
                    return issue.fix_suggestion
            # Return None to let AI generation handle it in _generate_fix()
            return None

        if issue.category == IssueCategory.LANGUAGE:
            return "en"

        if issue.category == IssueCategory.TITLE:
            # Try to get a meaningful title from first page
            if self._pdf and len(self._pdf) > 0:
                try:
                    first_page_text = self._pdf[0].get_text("text").strip()
                    if first_page_text:
                        lines = [
                            line.strip()
                            for line in first_page_text.split("\n")
                            if line.strip()
                        ]
                        if lines:
                            return lines[0][:100]
                except Exception:
                    pass
            # Fallback to filename
            return Path(self.file_path).stem.replace("_", " ").replace("-", " ")

        if issue.category == IssueCategory.NAVIGATION:
            return "add_bookmarks"

        if issue.category == IssueCategory.STRUCTURE:
            # For structure tree issues, just return a marker
            # The actual fix is done in _apply_structure_fix
            return "create_structure_tree"

        if issue.category == IssueCategory.READING_ORDER:
            # The actual fix is done in _apply_reading_order_fix; return a marker.
            return "reorder_reading_order"

        if issue.category == IssueCategory.TABLE:
            # The actual fix is done in _apply_table_fix; return a marker.
            return "tag_table_structure"

        if issue.category == IssueCategory.LIST:
            # The actual fix is done in _apply_list_fix; return a marker.
            return "tag_list_structure"

        if issue.category == IssueCategory.HEADING:
            # Try to extract heading text from issue metadata
            text = issue.metadata.get("text", "")
            if not text:
                # Try to get title from PDF metadata
                if self._pdf:
                    try:
                        metadata = self._pdf.metadata
                        if metadata and metadata.get("title"):
                            text = metadata.get("title")
                            logger.info(f"Using PDF metadata title for heading: {text}")
                    except Exception:
                        pass
            if not text:
                # Try to extract from first page text (first line)
                if self._pdf and len(self._pdf) > 0:
                    try:
                        first_page_text = self._pdf[0].get_text("text").strip()
                        if first_page_text:
                            # Use first non-empty line as title
                            lines = [
                                line.strip()
                                for line in first_page_text.split("\n")
                                if line.strip()
                            ]
                            if lines:
                                text = lines[0][:100]  # Limit to 100 chars
                                logger.info(f"Using first line as heading: {text}")
                    except Exception:
                        pass
            if not text:
                # Fallback to generic title
                text = "Document Title"
                logger.info("Using placeholder 'Document Title' for heading")
            return text

        return None

    def _get_ai_generated_fix(
        self, issue: RemediationIssue, document: Any, *, client: Any
    ) -> Optional[str]:
        """Get an AI-generated fix for an issue."""

        try:
            self.result.ai_calls_made += 1

            if issue.category == IssueCategory.ALT_TEXT:
                return self._generate_alt_text_with_ai(issue, document, client=client)

            return None

        except Exception as e:
            logger.error(f"AI generation failed: {e}")
            return None

    def _generate_alt_text_with_ai(
        self, issue: RemediationIssue, document: Any, *, client: Any
    ) -> Optional[str]:
        """Generate alt text for a PDF image using AI (vision first, text fallback)."""
        page_num = issue.metadata.get("page_number", 1)
        image_xref = issue.metadata.get("image_xref")

        # Try vision AI first if we have an image xref and a fitz document
        if image_xref is not None and document is not None and hasattr(self, "_pdf"):
            try:
                img_info = self._pdf.extract_image(image_xref)
                image_bytes = img_info.get("image") if img_info else None
                if image_bytes and hasattr(client, "analyze_image_sync"):
                    vision_prompt = (
                        f"Generate concise, descriptive alt text for this image from a PDF document "
                        f"(page {page_num}). Be concise (under 125 characters). "
                        "Describe the image's content and purpose. "
                        "Don't start with 'Image of' or 'Picture of'. "
                        "Focus on what's important for understanding the document. "
                        "Generate only the alt text, nothing else."
                    )
                    result = client.analyze_image_sync(
                        image_data=image_bytes,
                        prompt=vision_prompt,
                        max_tokens=200,
                    )
                    if result.get("success") and result.get("content"):
                        alt_text = result["content"].strip().strip("\"'")
                        if alt_text:
                            return alt_text[:125]
            except Exception as e:
                logger.warning(f"Vision AI alt text failed, falling back to text: {e}")

        # Fall back to text-only generation using page context
        context_text = ""
        if document and page_num <= len(document):
            page = document[page_num - 1]
            context_text = page.get_text("text")[:500]

        prompt = f"""Generate concise, descriptive alt text for an image in a PDF document.

Page context:
{context_text if context_text else 'No context available'}

Image location: Page {page_num}

Requirements:
- Be concise (under 125 characters)
- Describe the image's content and purpose
- Don't start with "Image of" or "Picture of"
- Focus on what's important for understanding the document

Generate only the alt text, nothing else:"""

        try:
            if hasattr(client, "generate_text_sync"):
                result = client.generate_text_sync(
                    prompt=prompt,
                    max_tokens=200,
                    temperature=0.3,
                )
                if result.get("success") and result.get("content"):
                    alt_text = result["content"].strip().strip("\"'")
                    return alt_text[:125] if alt_text else None
        except Exception as e:
            logger.error(f"AI alt text generation failed: {e}")

        # Fail closed: never emit placeholder alt text counted as a fix.
        # Returning None routes this issue to the human review queue (WCAG 1.1.1).
        return None

    def _calculate_scores(self):
        """Calculate compliance scores for the remediation.

        Uses the unified compliance_scoring system for consistency with
        the scanner pipeline (PDFProcessor.process_pdf).

        If _verify_fixes() already ran a full re-scan via PDFProcessor,
        its remediated_compliance_score is preserved — that score comes
        from the same pipeline as the initial scan and is authoritative.
        """
        if self.result.total_issues > 0:
            from ..compliance_scoring import get_score_only

            # Original score: use unified scoring on all original issues
            original_issue_dicts = [
                {"severity": issue.severity.value} for issue in self.issues
            ]
            self.result.original_compliance_score = get_score_only(original_issue_dicts)

            # Remediated score: prefer the verified re-scan score when
            # available — it runs the full scanner pipeline (reading order,
            # form fields, links, contrast, etc.) on the actual output file.
            # Check both verification_result AND that score was actually set
            # (verification can fail with an exception, setting result but
            # not the score).
            if (
                self.result.verification_result is not None
                and self.result.remediated_compliance_score is not None
            ):
                # _verify_fixes already set remediated_compliance_score
                # from PDFProcessor.process_pdf()
                logger.info(
                    "Using verified re-scan score: %.1f (not penalty estimate)",
                    self.result.remediated_compliance_score,
                )
            else:
                # Fallback: estimate from remaining issues using unified scoring
                fixed_issue_ids = {f.issue_id for f in self.result.fixed_issues}
                remaining_issue_dicts = [
                    {"severity": issue.severity.value}
                    for issue in self.issues
                    if issue.id not in fixed_issue_ids
                ]
                self.result.remediated_compliance_score = get_score_only(
                    remaining_issue_dicts
                )

            self.result.improvement = (
                self.result.remediated_compliance_score
                - self.result.original_compliance_score
            )

            # Note about PDF remediation
            if self._structure_modified:
                self.result.warnings.append(
                    "PDF structure tree was modified directly. "
                    "Alt text, headings, and document metadata have been embedded in the PDF."
                )

            if self.result.manual_count > 0:
                self.result.warnings.append(
                    "Some PDF issues require source document access for full remediation. "
                    "An accessible HTML version has been generated as an alternative."
                )

    @contextmanager
    def _materialize_output_claim_for_verification(self):
        """Materialize exact claimed bytes in a fresh private directory."""
        verification_dir = tempfile.mkdtemp(prefix="aelira_pdf_verification_")
        verification_path = str(Path(verification_dir) / "claimed-output.pdf")
        try:
            os.chmod(verification_dir, 0o700)
            directory_metadata = os.lstat(verification_dir)
            if not stat.S_ISDIR(directory_metadata.st_mode):
                raise RuntimeError("PDF verification workspace is not a directory")
            if directory_metadata.st_uid != os.geteuid():
                raise RuntimeError(
                    "PDF verification workspace is not owned by the current user"
                )
            if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
                raise RuntimeError("PDF verification workspace must have mode 0o700")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(verification_path, flags, 0o600)
            try:
                os.set_inheritable(descriptor, False)
                with self.result.open_output_stream() as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        offset = 0
                        while offset < len(chunk):
                            written = os.write(descriptor, chunk[offset:])
                            if written <= 0:
                                raise RuntimeError(
                                    "PDF verification materialization made no progress"
                                )
                            offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            yield verification_path
        finally:
            shutil.rmtree(verification_dir)

    def _verify_fixes(self, output_path: str):
        """Re-scan only bytes borrowed from the descriptor-bound output claim."""
        from .base import VerificationResult
        from ..pdf_processor import PDFProcessor

        logger.info("Verifying descriptor-bound remediation output for %s", output_path)

        try:
            with self._materialize_output_claim_for_verification() as verification_path:
                processor = PDFProcessor(
                    generate_alt_text=False,
                    validate_alt_text=False,
                    simulate_color_blindness=False,
                )
                new_result = processor.process_pdf(verification_path)

                new_issue_types = {
                    (
                        issue.get("type", issue.get("rule", "unknown")),
                        issue.get("location", ""),
                        issue.get("message", "")[:50],
                    )
                    for issue in new_result.issues
                }
                issues_fixed = []
                issues_remaining = []
                for issue in self.issues:
                    still_exists = any(
                        issue.category.value.lower() in str(new_type[0]).lower()
                        or issue.description[:30].lower() in str(new_type[2]).lower()
                        for new_type in new_issue_types
                    )
                    if still_exists:
                        issues_remaining.append(issue.id)
                    else:
                        issues_fixed.append(issue.id)

                regressions = []
                for new_issue in new_result.issues:
                    new_desc = new_issue.get("message", "")[:30].lower()
                    new_type = new_issue.get("type", new_issue.get("rule", "")).lower()
                    is_regression = not any(
                        new_type in original.category.value.lower()
                        or new_desc in original.description[:30].lower()
                        for original in self.issues
                    )
                    if is_regression:
                        regressions.append(
                            new_issue.get("message", "Unknown issue")[:100]
                        )

                issues_before = len(self.issues)
                issues_after = len(new_result.issues)
                if issues_before == 0:
                    verification_score = 100.0
                else:
                    improvement_ratio = len(issues_fixed) / issues_before
                    regression_penalty = len(regressions) * 0.1
                    verification_score = max(
                        0.0,
                        min(
                            100.0,
                            improvement_ratio * 100 - regression_penalty * 100,
                        ),
                    )

                verification = VerificationResult(
                    passed=len(regressions) == 0
                    and (len(issues_fixed) > 0 or issues_before == 0),
                    issues_before=issues_before,
                    issues_after=issues_after,
                    issues_fixed=issues_fixed,
                    issues_remaining=issues_remaining,
                    regressions=regressions,
                    verification_score=verification_score,
                )

                try:
                    from ..validation.matterhorn import (
                        CheckpointStatus,
                        MatterhornValidator,
                    )

                    mh_result = MatterhornValidator().validate(verification_path)
                    if mh_result:
                        failed_checks = [
                            checkpoint
                            for checkpoint in mh_result.checkpoints
                            if checkpoint.status == CheckpointStatus.FAIL
                        ]
                        if failed_checks:
                            verification.passed = False
                            verification.regressions.extend(
                                f"Matterhorn {checkpoint.id}: {checkpoint.name}"
                                for checkpoint in failed_checks
                            )
                            logger.info(
                                "Matterhorn: %d failed checkpoints",
                                len(failed_checks),
                            )
                        else:
                            logger.info("Matterhorn: all checkpoints passed")
                except Exception as e:
                    logger.warning("Matterhorn validation skipped: %s", e)

            self.result.verification_passed = verification.passed
            self.result.verification_result = verification
            self.result.remediated_compliance_score = new_result.compliance_score
            logger.info(
                "Verification complete: %d fixed, %d remaining, %d regressions",
                len(issues_fixed),
                len(issues_remaining),
                len(verification.regressions),
            )
            return verification
        except Exception as e:
            logger.error("Verification failed: %s", e)
            verification = VerificationResult(
                passed=False,
                issues_before=len(self.issues),
                issues_after=0,
                issues_fixed=[],
                issues_remaining=[issue.id for issue in self.issues],
                regressions=[f"Verification failed: {e}"],
                verification_score=0.0,
            )
            self.result.verification_passed = False
            self.result.verification_result = verification
            return verification
        finally:
            if not self.result.verification_passed:
                self.result.close_output_claim()
