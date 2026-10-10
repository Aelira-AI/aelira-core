"""Read-only, bounded source text quality before costly PDF remediation.

A visible page or nonempty text extraction does not prove that its used glyphs
have Unicode mappings. Use the strict verifier's decoder; never infer Unicode
from a CID, a replacement label, or a model's description of the page.
"""

from dataclasses import dataclass
from typing import Literal, cast

import pikepdf
from pdfminer.pdffont import PDFUnicodeNotDefined
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import stream_value

from ..pdf_checks.marked_content import (
    _FontResourceManager,
    _MarkedTextDevice,
    _PageInterpreter,
)
from .score_measurement import MeasurementError
from .pdf_font_text import FontTextBindingError, require_bounded_page_streams

MAX_DECODED_PAGE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 500


@dataclass(frozen=True)
class PDFPageTextQuality:
    page_index: int
    status: Literal["decoded", "absent", "mapping_unavailable", "scope_unsupported"]
    glyph_count: int
    evidence_origins: tuple[str, ...] = ()
    fidelity_status: Literal["unassessed"] = "unassessed"


@dataclass(frozen=True)
class PDFTextQuality:
    """Parser evidence only; decoded text is not proof of accessibility."""

    pages: tuple[PDFPageTextQuality, ...]
    reason: str | None = None
    fidelity_status: Literal["unassessed"] = "unassessed"


def inspect_pdf_text_quality(file_path: str) -> PDFTextQuality:
    """Report glyph decoding and unsupported scopes without returning text.

    An unsupported Form is explicitly reported, rather than silently counted
    as a page with no text. No provider is acquired and no PDF is saved.
    """
    pages: list[PDFPageTextQuality] = []
    checks: set[str] = set()
    from .pdf_ocr_form import (
        OCRFormFlatteningError,
        require_bounded_font_resources,
    )

    try:
        with pikepdf.open(file_path) as pdf, open(file_path, "rb") as source:
            if len(pdf.pages) > MAX_PAGES:
                return PDFTextQuality(tuple(pages), "original_scan_failed")
            for page_index, page in enumerate(PDFPage.get_pages(source)):
                if page_index >= MAX_PAGES or page_index >= len(pdf.pages):
                    return PDFTextQuality(tuple(pages), "original_scan_failed")
                checks = set()
                # Bound expansion before pdfminer allocates decoded streams.
                require_bounded_page_streams(pdf.pages[page_index])
                decoded_bytes = 0
                for content in page.contents:
                    decoded_bytes += len(stream_value(content).get_data())
                    if decoded_bytes > MAX_DECODED_PAGE_BYTES:
                        return PDFTextQuality(tuple(pages), "original_scan_failed")
                resources = pdf.pages[page_index].Resources
                require_bounded_font_resources(
                    [
                        cast(
                            pikepdf.Dictionary,
                            resources.get("/Font", pikepdf.Dictionary()),
                        )
                    ]
                )
                manager = _FontResourceManager(checks.add)
                device = _MarkedTextDevice(manager, checks.add)
                try:
                    _PageInterpreter(manager, device).process_page(page)
                except PDFUnicodeNotDefined:
                    checks.add("font_unicode")
                except Exception:
                    if not any(check.startswith("font_") for check in checks):
                        return PDFTextQuality(tuple(pages), "original_scan_failed")
                if any(check.startswith("font_") for check in checks):
                    pages.append(
                        PDFPageTextQuality(
                            page_index, "mapping_unavailable", device.character_count
                        )
                    )
                    return PDFTextQuality(
                        tuple(pages), "source_text_mapping_unavailable"
                    )
                if "stream_scope" in checks:
                    pages.append(
                        PDFPageTextQuality(
                            page_index, "scope_unsupported", device.character_count
                        )
                    )
                    return PDFTextQuality(tuple(pages), "source_text_scope_unsupported")
                pages.append(
                    PDFPageTextQuality(
                        page_index,
                        "decoded" if device.character_count else "absent",
                        device.character_count,
                        tuple(sorted(device.evidence_origins)),
                    )
                )
        return PDFTextQuality(tuple(pages))
    except (OCRFormFlatteningError, FontTextBindingError):
        return PDFTextQuality(tuple(pages), "original_scan_failed")
    except Exception:
        reason = (
            "source_text_mapping_unavailable"
            if any(check.startswith("font_") for check in checks)
            else "original_scan_failed"
        )
        return PDFTextQuality(tuple(pages), reason)


def require_decodable_pdf_text(file_path: str) -> None:
    """Refuse undecodable glyph mappings/scopes without exposing parser text."""
    quality = inspect_pdf_text_quality(file_path)
    if quality.reason is not None:
        raise MeasurementError(quality.reason)
