"""Pydantic models for PDF accessibility checking."""

from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict

# Required by PDFProcessingResult.cvd_analysis field
from ..color_blindness_simulator import ColorBlindnessAnalysisResult


class PDFUAVersion(str, Enum):
    """PDF/UA standard version detected in document."""

    UA1 = "PDF/UA-1"
    UA2 = "PDF/UA-2"
    NONE = "none"


class PDFUAComplianceResult(BaseModel):
    """Result of PDF/UA version detection and compliance check."""

    version_detected: PDFUAVersion
    ua2_features: Dict[
        str, bool
    ]  # Detected UA-2 features: namespaces, pronunciation, ruby, etc.
    ua2_issues: List[str]  # Issues preventing full UA-2 compliance
    upgrade_recommendations: List[str]  # Recommendations to upgrade from UA-1 to UA-2
    conformance_level: Optional[str] = None  # A, B for UA-1; conformance for UA-2
    pdfua_identifier: Optional[str] = None  # The pdfuaid:part value from XMP


class ReadingOrderIssue(BaseModel):
    """Individual reading order issue detected on a page."""

    page_number: int
    expected_order: List[str]  # Visual order (y-position sorted) text snippets
    actual_order: List[str]  # Structure tree order text snippets
    severity: str  # "critical" if content skipped, "warning" if order differs
    recommendation: str
    visual_positions: Optional[List[Dict[str, float]]] = (
        None  # bbox coords for visualization
    )


class ReadingOrderResult(BaseModel):
    """Result of PDF reading order verification."""

    total_pages: int
    pages_analyzed: int
    issues: List[ReadingOrderIssue]
    compliance_score: float  # 0-100 based on reading order correctness
    has_structure_tree: bool
    multi_column_detected: bool = False


class TableCell(BaseModel):
    """Represents a single cell in a PDF table for accessibility analysis."""

    row: int  # 0-indexed row number
    col: int  # 0-indexed column number
    text: str  # Cell text content
    is_bold: bool = False  # Whether text is bold
    has_background: bool = False  # Whether cell has background shading
    font_size: Optional[float] = None  # Font size if available
    font_name: Optional[str] = None  # Font name if available
    bbox: Optional[Tuple[float, float, float, float]] = None  # Bounding box


class PDFTable(BaseModel):
    """Represents a table detected in a PDF document."""

    page_number: int
    table_index: int  # Index of table on the page
    cells: List[TableCell]  # All cells in the table
    row_count: int
    col_count: int
    has_header_row: bool = False  # Whether first row appears to be headers
    has_header_column: bool = False  # Whether first column appears to be headers
    bbox: Optional[Tuple[float, float, float, float]] = None


class TableHeaderDetectionResult(BaseModel):
    """Result of heuristic table header detection."""

    detected_headers: List[TableCell]  # Cells detected as headers
    header_row_indices: List[int] = []  # Row indices that appear to be headers
    header_col_indices: List[int] = []  # Column indices that appear to be headers
    detection_method: str  # Method used: bold, background, keywords, font_size
    confidence: float  # 0-1 confidence in detection
    has_th_tags: bool = False  # Whether PDF already has TH structure tags


class TableFix(BaseModel):
    """Recommended fix for table accessibility issues."""

    table_location: str  # Page and position info
    detected_headers: List[str]  # Text of detected header cells
    recommended_scope: Optional[str]  # "col", "row", or "colgroup"/"rowgroup"
    fix_instructions: str  # Human-readable fix instructions
    priority: str = "high"  # Priority: critical, high, medium, low
    wcag_criterion: str = "1.3.1"  # Info and Relationships


class PDFImageIssue(BaseModel):
    """Image accessibility issue in PDF"""

    model_config = ConfigDict(frozen=True)

    page_number: int
    image_index: int  # Index in displayed image occurrences on the page
    occurrence_ordinal: int
    bbox: Tuple[float, float, float, float]
    occurrence_id: str
    has_alt_text: bool
    existing_alt_text: Optional[str] = None  # The current alt text if present
    suggested_alt_text: Optional[str] = None
    image_type: Optional[str] = None  # decorative, informative, functional, complex
    is_chart: bool = False  # True if detected as chart/graph/infographic
    detailed_description: Optional[str] = None  # For charts/complex images
    image_xref: int  # PDF xref for extracting image bytes in remediator
    # Alt text validation (for images WITH alt text)
    alt_text_validated: bool = False  # Whether AI validation was performed
    alt_text_accurate: Optional[bool] = None  # Whether existing alt text is accurate
    alt_text_issues: Optional[List[str]] = None  # Specific problems found
    validation_score: Optional[float] = None  # Accuracy score 0-1


class PDFProcessingResult(BaseModel):
    """Result of PDF processing operation"""

    file_path: str
    file_name: str
    pages: int
    text_extracted: bool
    ocr_used: bool
    structure: Dict[str, List]  # headings, paragraphs, lists, tables
    html_output: str
    compliance_score: float
    issues: List[Dict]
    image_issues: Optional[List[PDFImageIssue]] = None  # Image accessibility issues
    # Color vision deficiency analysis
    cvd_analysis: Optional[List[ColorBlindnessAnalysisResult]] = None
