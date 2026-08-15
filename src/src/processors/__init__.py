"""
Processors Module Facade

Re-exports document processors from src.education for backwards compatibility.
"""

from src.education.pdf_processor import PDFProcessor
from src.education.docx_processor import DocxProcessor
from src.education.pptx_processor import PowerPointProcessor
from src.education.xlsx_processor import XlsxProcessor
from src.education.latex_processor import LaTeXProcessor
from src.education.multimedia_processor import MultimediaProcessor

__all__ = [
    "PDFProcessor",
    "DocxProcessor",
    "PowerPointProcessor",
    "XlsxProcessor",
    "LaTeXProcessor",
    "MultimediaProcessor",
]
