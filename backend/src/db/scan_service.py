"""
Scan Service - Database operations for scan results

Provides high-level functions to store and retrieve scan results
for PDF, PowerPoint, and LaTeX processing.
"""

from sqlalchemy.orm import Session
from typing import Optional, List, Dict
from datetime import datetime
import hashlib
import logging

from .models import Scan, ScanResult, ScanType, ScanStatus
from ..education.pdf_processor import PDFProcessingResult
from ..education.pptx_processor import PowerPointProcessingResult
from ..education.latex_processor import DocumentConversionResult
from ..education.docx_processor import DocxProcessingResult
from ..education.xlsx_processor import XlsxProcessingResult

logger = logging.getLogger(__name__)


# Issue type to category mapping for new accessibility checks (Tasks 1-14)
ISSUE_TYPE_CATEGORY_MAP = {
    # PDF (Tasks 1, 4, 14)
    "reading_order": "reading_order",
    "table_header": "table",
    "table_accessibility": "table",
    # Web - Shadow DOM (Task 13)
    "shadow_dom": "shadow_dom",
    "image-alt": "alt_text",
    "button-name": "aria",
    "link-name": "link",
    "form-label": "form",
    # XLSX (Tasks 10, 11)
    "conditional_format": "conditional_format",
    "pivot_table": "pivot_table",
    "color_only": "color",
    # Multimedia (Tasks 2, 5)
    "red_flash": "flashing",
    "flashing_content": "flashing",
    "speaker_diarization": "media",
    # PPTX (Tasks 8, 9)
    "animation": "animation",
    "animation_flash": "animation",
    "animation_auto": "animation",
    "embedded_media": "media",
    "missing_captions": "media",
    "missing_transcript": "media",
    # DOCX (Tasks 6, 7)
    "smartart": "smartart",
    "embedded_object": "embedded_object",
    "ole_object": "embedded_object",
}

# Issue types that should always be marked as critical severity (seizure risk)
CRITICAL_SEVERITY_TYPES = {"red_flash", "animation_flash", "flashing_content"}


def normalize_issue(issue: Dict) -> Dict:
    """
    Normalize an issue dictionary for consistent dashboard display.

    Handles field name mapping, category assignment, and severity overrides
    for seizure-risk issues.

    Args:
        issue: Raw issue dictionary from a processor

    Returns:
        Normalized issue dictionary
    """
    normalized = issue.copy()

    # Add criterion field from rule field (e.g., 'WCAG 3.1.1' -> '3.1.1')
    if "rule" in issue and "criterion" not in issue:
        rule_text = issue["rule"]
        if "WCAG" in rule_text:
            criterion = rule_text.replace("WCAG", "").strip()
        else:
            criterion = rule_text
        normalized["criterion"] = criterion

    # Map severity to impact for consistency
    if "severity" in issue and "impact" not in issue:
        normalized["impact"] = issue["severity"]

    # Add description field if missing (use message as fallback)
    if "description" not in issue and "message" in issue:
        normalized["description"] = issue["message"]

    # Map issue type to category for dashboard filtering
    issue_type = issue.get("type", "")
    if issue_type and "category" not in issue:
        normalized["category"] = ISSUE_TYPE_CATEGORY_MAP.get(issue_type, "other")

    # Force critical severity for seizure-risk issues (WCAG 2.3.1)
    if issue_type in CRITICAL_SEVERITY_TYPES or issue.get("red_flash_detected"):
        normalized["severity"] = "critical"
        normalized["impact"] = "critical"

    return normalized


def normalize_issues(issues: List[Dict]) -> List[Dict]:
    """
    Normalize a list of issues for consistent dashboard display.

    Args:
        issues: List of raw issue dictionaries

    Returns:
        List of normalized issue dictionaries
    """
    return [normalize_issue(issue) for issue in issues]


class ScanService:
    """Service for managing scan operations in the database"""

    @staticmethod
    def store_pdf_scan(
        db: Session,
        result: PDFProcessingResult,
        user_id: str,
        department_id: str,
        file_content: bytes,
    ) -> Scan:
        """
        Store PDF scan results in database

        Args:
            db: Database session
            result: PDF processing result from PDFProcessor
            user_id: User who initiated the scan
            department_id: Department the scan belongs to
            file_content: Raw file bytes for hash calculation

        Returns:
            Created Scan object
        """
        # Calculate file hash for deduplication
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Create Scan record
        scan = Scan(
            scan_type=ScanType.PDF,
            status=ScanStatus.COMPLETED,
            file_name=result.file_name,
            file_size_bytes=len(file_content),
            file_hash=file_hash,
            user_id=user_id,
            department_id=department_id,
            processing_time_ms=0,  # TODO: Track actual processing time
            pages=result.pages,
            completed_at=datetime.utcnow(),
        )
        db.add(scan)
        db.flush()  # Get scan.id

        # Normalize issues for dashboard compatibility
        normalized_issues = normalize_issues(result.issues)

        # Check if any issue has 'how_to_fix' field (indicates Ollama was used)
        ollama_used = any("how_to_fix" in issue for issue in normalized_issues)

        # Create ScanResult record
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=sum(
                1 for issue in result.issues if issue.get("severity") == "critical"
            ),
            high_issues=sum(
                1 for issue in result.issues if issue.get("severity") == "high"
            ),
            medium_issues=sum(
                1 for issue in result.issues if issue.get("severity") == "medium"
            ),
            low_issues=sum(
                1 for issue in result.issues if issue.get("severity") == "low"
            ),
            issues=normalized_issues,  # Use normalized issues instead of raw issues
            structure=result.structure,
            html_output=result.html_output,
            ocr_used=result.ocr_used,
            ollama_used=ollama_used,  # True if Ollama was used for fix descriptions
        )
        db.add(scan_result)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"Stored PDF scan: {scan.id} ({result.file_name}, score={result.compliance_score})"
        )
        return scan

    @staticmethod
    def store_powerpoint_scan(
        db: Session,
        result: PowerPointProcessingResult,
        user_id: str,
        department_id: str,
        file_content: bytes,
    ) -> Scan:
        """
        Store PowerPoint scan results in database

        Args:
            db: Database session
            result: PowerPoint processing result from PowerPointProcessor
            user_id: User who initiated the scan
            department_id: Department the scan belongs to
            file_content: Raw file bytes for hash calculation

        Returns:
            Created Scan object
        """
        # Calculate file hash
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Create Scan record
        scan = Scan(
            scan_type=ScanType.POWERPOINT,
            status=ScanStatus.COMPLETED,
            file_name=result.file_name,
            file_size_bytes=len(file_content),
            file_hash=file_hash,
            user_id=user_id,
            department_id=department_id,
            processing_time_ms=0,  # TODO: Track actual processing time
            pages=result.total_slides,  # Slides = pages for PowerPoint
            completed_at=datetime.utcnow(),
        )
        db.add(scan)
        db.flush()

        # Normalize issues for dashboard compatibility (Tasks 1-14 support)
        all_issues = normalize_issues(result.issues)
        critical = high = medium = low = 0
        for issue in all_issues:
            sev = (issue.get("severity") or "").lower()
            if sev == "critical":
                critical += 1
            elif sev == "high":
                high += 1
            elif sev == "medium":
                medium += 1
            elif sev == "low":
                low += 1

        # Store slide structure
        structure = {
            "total_slides": result.total_slides,
            "total_shapes": result.total_shapes,
            "total_images": result.total_images,
            "slides": [
                {
                    "slide_number": slide.slide_number,
                    "title": slide.slide_title,
                    "shapes": slide.total_shapes,
                    "images": slide.total_images,
                    "issues": slide.total_issues,
                }
                for slide in result.slides
            ],
        }

        # Create ScanResult record
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=all_issues,
            structure=structure,
            suggestions=result.remediation_suggestions,
            ocr_used=False,  # PowerPoint doesn't use OCR
            ollama_used=False,  # PowerPoint doesn't use Ollama yet
        )
        db.add(scan_result)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"Stored PowerPoint scan: {scan.id} ({result.file_name}, score={result.compliance_score})"
        )
        return scan

    @staticmethod
    def store_latex_scan(
        db: Session,
        result: DocumentConversionResult,
        user_id: str,
        department_id: str,
        file_content: bytes,
        ollama_used: bool = False,
    ) -> Scan:
        """
        Store LaTeX conversion results in database

        Args:
            db: Database session
            result: LaTeX processing result from LaTeXProcessor
            user_id: User who initiated the scan
            department_id: Department the scan belongs to
            file_content: Raw file bytes for hash calculation
            ollama_used: Whether Ollama was used for ARIA labels

        Returns:
            Created Scan object
        """
        # Calculate file hash
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Create Scan record
        scan = Scan(
            scan_type=ScanType.LATEX,
            status=ScanStatus.COMPLETED,
            file_name=result.file_name,
            file_size_bytes=len(file_content),
            file_hash=file_hash,
            user_id=user_id,
            department_id=department_id,
            processing_time_ms=0,  # TODO: Track actual processing time
            pages=1,  # LaTeX documents don't have pages in this context
            completed_at=datetime.utcnow(),
        )
        db.add(scan)
        db.flush()

        # Aggregate issues from equations
        all_issues = []
        critical = high = medium = low = 0

        for eq in result.equations:
            if not eq.conversion_success:
                issue = {
                    "equation_id": eq.equation_id,
                    "type": "conversion_failed",
                    "category": "latex",
                    "severity": "high",
                    "title": "LaTeX Conversion Failed",
                    "description": f"Equation #{eq.equation_id} could not be converted to MathML: {eq.error_message or 'unknown error'}",
                    "location": f"Equation {eq.equation_id}",
                    "wcag_criterion": "WCAG 1.1.1",
                    "suggested_fix": "Check the LaTeX syntax and ensure all packages are supported. Consider providing an alt-text description manually.",
                    "latex": eq.latex_source[:100],
                    "error": eq.error_message,
                }
                all_issues.append(issue)
                high += 1
            elif not eq.wcag_compliant:
                issue = {
                    "equation_id": eq.equation_id,
                    "type": "wcag_noncompliant",
                    "category": "latex",
                    "severity": "medium",
                    "title": "Equation Missing Accessibility Metadata",
                    "description": f"Equation #{eq.equation_id} is missing an ARIA label or MathML representation needed for screen readers",
                    "location": f"Equation {eq.equation_id}",
                    "wcag_criterion": "WCAG 1.1.1",
                    "suggested_fix": "Add an ARIA label describing the equation's meaning, or ensure MathML output is generated.",
                    "latex": eq.latex_source[:100],
                    "reason": "Missing ARIA label or MathML",
                }
                all_issues.append(issue)
                medium += 1

        # Store equation structure
        structure = {
            "total_equations": result.total_equations,
            "successful_conversions": result.successful_conversions,
            "failed_conversions": result.failed_conversions,
            "equations": [
                {
                    "equation_id": eq.equation_id,
                    "latex_source": eq.latex_source[:100],
                    "conversion_success": eq.conversion_success,
                    "wcag_compliant": eq.wcag_compliant,
                    "aria_label": eq.aria_label,
                }
                for eq in result.equations
            ],
        }

        # Create ScanResult record
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=all_issues,
            structure=structure,
            html_output=result.html_output,
            ocr_used=False,  # LaTeX doesn't use OCR
            ollama_used=ollama_used,
            ollama_calls=result.total_equations if ollama_used else 0,
        )
        db.add(scan_result)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"Stored LaTeX scan: {scan.id} ({result.file_name}, score={result.compliance_score})"
        )
        return scan

    @staticmethod
    def get_scan_history(
        db: Session,
        department_id: str,
        user_id: Optional[str] = None,
        scan_type: Optional[ScanType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Scan]:
        """
        Get scan history for a department (with optional filters)

        Args:
            db: Database session
            department_id: Department to query
            user_id: Optional user filter
            scan_type: Optional scan type filter
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of Scan objects
        """
        query = db.query(Scan).filter(Scan.department_id == department_id)

        if user_id:
            query = query.filter(Scan.user_id == user_id)

        if scan_type:
            query = query.filter(Scan.scan_type == scan_type)

        scans = query.order_by(Scan.created_at.desc()).limit(limit).offset(offset).all()

        return scans

    @staticmethod
    def get_scan_with_result(db: Session, scan_id: str) -> Optional[Scan]:
        """
        Get a scan with its result loaded

        Args:
            db: Database session
            scan_id: Scan ID to retrieve

        Returns:
            Scan object with result relationship loaded, or None
        """
        from sqlalchemy.orm import joinedload

        scan = (
            db.query(Scan)
            .options(joinedload(Scan.result))
            .filter(Scan.id == scan_id)
            .first()
        )

        return scan

    @staticmethod
    def get_department_stats(db: Session, department_id: str) -> Dict:
        """
        Get statistics for a department

        Args:
            db: Database session
            department_id: Department to analyze

        Returns:
            Dictionary with stats (total_scans, avg_compliance_score, etc.)
        """

        scans = db.query(Scan).filter(Scan.department_id == department_id).all()

        if not scans:
            return {
                "total_scans": 0,
                "avg_compliance_score": 0,
                "total_pages": 0,
                "total_issues": 0,
            }

        # Calculate stats
        total_scans = len(scans)
        total_pages = sum(scan.pages or 0 for scan in scans)

        # Get compliance scores
        results = (
            db.query(ScanResult)
            .filter(ScanResult.scan_id.in_([scan.id for scan in scans]))
            .all()
        )

        avg_score = (
            sum(r.compliance_score for r in results) / len(results) if results else 0
        )
        total_issues = sum(
            r.critical_issues + r.high_issues + r.medium_issues + r.low_issues
            for r in results
        )

        return {
            "total_scans": total_scans,
            "avg_compliance_score": round(avg_score, 2),
            "total_pages": total_pages,
            "total_issues": total_issues,
            "scans_by_type": {
                "pdf": sum(1 for s in scans if s.scan_type == ScanType.PDF),
                "powerpoint": sum(
                    1 for s in scans if s.scan_type == ScanType.POWERPOINT
                ),
                "word": sum(1 for s in scans if s.scan_type == ScanType.WORD),
                "excel": sum(1 for s in scans if s.scan_type == ScanType.EXCEL),
                "latex": sum(1 for s in scans if s.scan_type == ScanType.LATEX),
            },
        }

    @staticmethod
    def store_word_scan(
        db: Session,
        result: DocxProcessingResult,
        user_id: str,
        department_id: str,
        file_content: bytes,
    ) -> Scan:
        """
        Store Word document scan results in database

        Args:
            db: Database session
            result: Word processing result from DocxProcessor
            user_id: User who initiated the scan
            department_id: Department the scan belongs to
            file_content: Raw file bytes for hash calculation

        Returns:
            Created Scan object
        """
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Create Scan record
        scan = Scan(
            scan_type=ScanType.WORD,
            status=ScanStatus.COMPLETED,
            file_name=result.file_name,
            file_size_bytes=len(file_content),
            file_hash=file_hash,
            user_id=user_id,
            department_id=department_id,
            processing_time_ms=0,
            pages=result.total_paragraphs,  # Use paragraphs as page proxy
            completed_at=datetime.utcnow(),
        )
        db.add(scan)
        db.flush()

        # Normalize issues for dashboard compatibility (Tasks 1-14 support)
        all_issues = normalize_issues(result.issues)
        critical = high = medium = low = 0
        for issue in all_issues:
            sev = (issue.get("severity") or "").lower()
            if sev == "critical":
                critical += 1
            elif sev == "high":
                high += 1
            elif sev == "medium":
                medium += 1
            elif sev == "low":
                low += 1

        # Create ScanResult record
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=all_issues,
            structure={
                "total_paragraphs": result.total_paragraphs,
                "total_images": result.total_images,
                "total_tables": result.total_tables,
                "total_lists": result.total_lists,
                "total_links": result.total_links,
            },
            html_output=result.html_output,
            ocr_used=False,
            ollama_used=any(i.suggested_alt_text for i in result.image_issues),
        )
        db.add(scan_result)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"Stored Word scan: {scan.id} ({result.file_name}, score={result.compliance_score})"
        )
        return scan

    @staticmethod
    def store_excel_scan(
        db: Session,
        result: XlsxProcessingResult,
        user_id: str,
        department_id: str,
        file_content: bytes,
    ) -> Scan:
        """
        Store Excel scan results in database

        Args:
            db: Database session
            result: Excel processing result from XlsxProcessor
            user_id: User who initiated the scan
            department_id: Department the scan belongs to
            file_content: Raw file bytes for hash calculation

        Returns:
            Created Scan object
        """
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Create Scan record
        scan = Scan(
            scan_type=ScanType.EXCEL,
            status=ScanStatus.COMPLETED,
            file_name=result.file_name,
            file_size_bytes=len(file_content),
            file_hash=file_hash,
            user_id=user_id,
            department_id=department_id,
            processing_time_ms=0,
            pages=result.total_sheets,  # Sheets as pages
            completed_at=datetime.utcnow(),
        )
        db.add(scan)
        db.flush()

        # Normalize issues for dashboard compatibility (Tasks 1-14 support)
        all_issues = normalize_issues(result.issues)
        critical = high = medium = low = 0
        for issue in all_issues:
            sev = (issue.get("severity") or "").lower()
            if sev == "critical":
                critical += 1
            elif sev == "high":
                high += 1
            elif sev == "medium":
                medium += 1
            elif sev == "low":
                low += 1

        # Create ScanResult record
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=all_issues,
            structure={
                "total_sheets": result.total_sheets,
                "total_rows": result.total_rows,
                "total_charts": result.total_charts,
                "total_images": result.total_images,
                "sheets": [
                    {
                        "name": s.sheet_name,
                        "rows": s.row_count,
                        "columns": s.column_count,
                        "has_data": s.has_data,
                        "has_tables": s.has_tables,
                        "has_charts": s.has_charts,
                        "has_images": s.has_images,
                        "has_frozen_panes": s.has_frozen_panes,
                    }
                    for s in result.sheets
                ],
            },
            html_output=None,  # Excel doesn't generate HTML
            ocr_used=False,
            ollama_used=any(
                any(i.suggested_alt_text for i in s.image_issues) for s in result.sheets
            ),
        )
        db.add(scan_result)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"Stored Excel scan: {scan.id} ({result.file_name}, score={result.compliance_score})"
        )
        return scan
