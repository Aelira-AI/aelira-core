"""
Document Processor Facade

Provides a unified interface for document processing.
Re-exports from education module for backwards compatibility with tests.
"""

from typing import Dict, Any, Optional
from pathlib import Path
import logging

from src.education.pdf_processor import PDFProcessor
from src.education.docx_processor import DocxProcessor
from src.education.pptx_processor import PowerPointProcessor
from src.education.xlsx_processor import XlsxProcessor

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """
    Unified document processor that routes to appropriate processor based on file type.

    Supported file types:
    - PDF (.pdf)
    - Word (.docx, .doc)
    - PowerPoint (.pptx, .ppt)
    - Excel (.xlsx, .xls)
    """

    def __init__(self):
        """Initialize document processor with all sub-processors."""
        self.pdf_processor = PDFProcessor()
        self.docx_processor = DocxProcessor()
        self.pptx_processor = PowerPointProcessor()
        self.xlsx_processor = XlsxProcessor()

    def scan(
        self,
        file_path: str,
        file_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scan a document for accessibility issues.

        Args:
            file_path: Path to the document file
            file_type: Optional file type override (pdf, docx, pptx, xlsx)

        Returns:
            Dict with scan results including issues found
        """
        path = Path(file_path)
        ext = file_type or path.suffix.lower().lstrip(".")

        try:
            if ext == "pdf":
                return self.pdf_processor.scan(file_path)
            elif ext in ("docx", "doc"):
                return self.docx_processor.scan(file_path)
            elif ext in ("pptx", "ppt"):
                return self.pptx_processor.scan(file_path)
            elif ext in ("xlsx", "xls"):
                return self.xlsx_processor.scan(file_path)
            else:
                return {
                    "success": False,
                    "error": f"Unsupported file type: {ext}",
                    "issues": [],
                }
        except Exception as e:
            logger.error(f"Error scanning document {file_path}: {e}")
            return {
                "success": False,
                "error": str(e),
                "issues": [],
            }


__all__ = ["DocumentProcessor"]
