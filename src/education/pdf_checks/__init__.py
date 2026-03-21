"""PDF accessibility checker package."""

from .models import (
    PDFUAVersion,
    PDFUAComplianceResult,
    ReadingOrderIssue,
    ReadingOrderResult,
    TableCell,
    PDFTable,
    TableHeaderDetectionResult,
    TableFix,
    PDFImageIssue,
    PDFProcessingResult,
)
from .table_checker import TableAccessibilityChecker
from .structure_checker import StructureTreeChecker
from .contrast_checker import ColorContrastChecker
from .form_checker import FormFieldChecker
from .reading_order import ReadingOrderVerifier
from .pdfua_detector import PDFUADetector
from .math_checker import MathEquationChecker
from .image_checker import ImageAccessibilityChecker

__all__ = [
    "PDFUAVersion",
    "PDFUAComplianceResult",
    "ReadingOrderIssue",
    "ReadingOrderResult",
    "TableCell",
    "PDFTable",
    "TableHeaderDetectionResult",
    "TableFix",
    "PDFImageIssue",
    "PDFProcessingResult",
    "TableAccessibilityChecker",
    "StructureTreeChecker",
    "ColorContrastChecker",
    "FormFieldChecker",
    "ReadingOrderVerifier",
    "PDFUADetector",
    "MathEquationChecker",
    "ImageAccessibilityChecker",
]
