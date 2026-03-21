# Aelira Auto-Remediation Engine
# Automatically fix accessibility issues in documents

from .base import (
    BaseRemediator,
    RemediationResult,
    RemediationIssue,
    FixedIssue,
    ManualIssue,
    RemediationConfig,
    IssueCategory,
    IssueSeverity,
    FixStatus,
)

from .docx_remediator import DocxRemediator
from .pptx_remediator import PptxRemediator
from .pdf_remediator import PdfRemediator
from .xlsx_remediator import XlsxRemediator
from .latex_remediator import LatexRemediator
from .html_remediator import HtmlRemediator
from .multimedia_remediator import MultimediaRemediator

# PDF structure manipulation (direct PDF/UA remediation)
from .pdf_structure import PDFStructureTree, verify_pdf_accessibility, HAS_PIKEPDF
from .content_tagger import ContentTagger

__all__ = [
    # Base classes
    "BaseRemediator",
    "RemediationResult",
    "RemediationIssue",
    "FixedIssue",
    "ManualIssue",
    "RemediationConfig",
    "IssueCategory",
    "IssueSeverity",
    "FixStatus",
    # Document-specific remediators
    "DocxRemediator",
    "PptxRemediator",
    "PdfRemediator",
    "XlsxRemediator",
    "LatexRemediator",
    "HtmlRemediator",
    "MultimediaRemediator",
    # PDF structure manipulation
    "PDFStructureTree",
    "verify_pdf_accessibility",
    "HAS_PIKEPDF",
    "ContentTagger",
]


def get_remediator_for_file(file_path: str):
    """
    Get the appropriate remediator class for a file based on extension.

    Args:
        file_path: Path to the document file

    Returns:
        The appropriate remediator class, or None if not supported
    """
    from pathlib import Path

    ext = Path(file_path).suffix.lower()

    remediators = {
        ".docx": DocxRemediator,
        ".pptx": PptxRemediator,
        ".pdf": PdfRemediator,
        ".xlsx": XlsxRemediator,
        ".tex": LatexRemediator,
        ".html": HtmlRemediator,
        ".htm": HtmlRemediator,
        ".css": HtmlRemediator,
        ".js": HtmlRemediator,
        # Multimedia files
        ".mp4": MultimediaRemediator,
        ".webm": MultimediaRemediator,
        ".mov": MultimediaRemediator,
        ".avi": MultimediaRemediator,
        ".mkv": MultimediaRemediator,
        ".mp3": MultimediaRemediator,
        ".wav": MultimediaRemediator,
        ".m4a": MultimediaRemediator,
        ".ogg": MultimediaRemediator,
    }

    return remediators.get(ext)
