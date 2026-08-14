"""Document scanning endpoints — PDF, PPTX, DOCX, XLSX, LaTeX."""

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Depends
from typing import Optional, Tuple
from sqlalchemy.orm import Session
import logging
import os
import tempfile
import time

from ...db.database import get_db_dependency
from ...db.models import APIKey, ScanType
from ...education.pdf_processor import PDFProcessor
from ...education.pptx_processor import PowerPointProcessor
from ...education.docx_processor import DocxProcessor
from ...education.xlsx_processor import XlsxProcessor
from ...education.latex_processor import LaTeXProcessor
from ...middleware.quota import increment_usage, require_feature
from ._shared import (
    _run_in_thread,
    check_scan_quota,
    validate_uploaded_file,
    get_api_key_or_mock,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== PDF Endpoints ====================


def process_pdf_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    generate_alt_text: bool,
    enhance_descriptions: bool,
):
    """Background task to process PDF asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing PDF: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                # Handle None values (can happen if progress_callback receives unexpected values)
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                # Use a separate DB session for progress updates to avoid blocking
                # the main session or other requests
                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] Progress: {progress_pct}% - {message} (current={current}, total={total})"
                    )
            except Exception as e:
                logger.error(
                    f"[BACKGROUND] Failed to update progress: {e} (current={current}, total={total}, message={message})"
                )
            finally:
                if progress_db:
                    progress_db.close()

        # Process PDF (don't pass db_session to avoid holding connection for entire processing)
        processor = PDFProcessor(
            generate_alt_text=generate_alt_text,
            enhance_descriptions=enhance_descriptions,
            db_session=None,  # PDFProcessor should create its own sessions when needed
            progress_callback=update_progress,
        )
        result = processor.process_pdf(file_path, original_filename=filename)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] PDF processed in {processing_time}ms: {filename}")

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.pages
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()

        # Create ScanResult
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=len(
                [i for i in result.issues if i.get("severity") == "critical"]
            ),
            high_issues=len([i for i in result.issues if i.get("severity") == "high"]),
            medium_issues=len(
                [i for i in result.issues if i.get("severity") == "medium"]
            ),
            low_issues=len([i for i in result.issues if i.get("severity") == "low"]),
            issues=result.issues,
            structure=result.structure,
            html_output=result.html_output,
            ocr_used=result.ocr_used,
            ollama_used=result.image_issues is not None
            and len(result.image_issues) > 0,
            ollama_calls=len(result.image_issues) if result.image_issues else 0,
        )

        db.add(scan_result)
        db.commit()

        logger.info(f"[BACKGROUND] Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing PDF {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/pdf/scan", response_model=dict)
async def scan_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_alt_text: bool = False,
    enhance_descriptions: bool = True,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan a single PDF file for accessibility compliance - NOW WITH REAL-TIME PROGRESS!

    ✨ NEW: Asynchronous processing with real-time progress updates
    - Returns scan_id immediately (no waiting!)
    - Frontend polls /scans/{scan_id}/progress for updates
    - Shows messages like "Analyzing image 3 of 23 (Page 5, ~10s per image)"

    NOW STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    ENFORCES QUOTA LIMITS FOR FREE TIER 📊

    ✨ AI-powered fix descriptions using RAG + Ollama (default: enabled)
    - Queries WCAG knowledge base for detailed rule information
    - Uses Ollama (llama3.2) to generate human-friendly, actionable fix descriptions
    - Adds 'how_to_fix' field to each issue with clear remediation steps
    - Set enhance_descriptions=false to disable (faster processing)

    ✨ OPTIONAL: AI-powered alt text generation for images
    - Set generate_alt_text=true to automatically generate alt text for embedded images
    - Uses llava:7b vision model for educational context descriptions
    - Extracts images from PDF using PyMuPDF
    - Significantly increases processing time (~10s per image)
    """
    _, user_id, department_id = api_key_info

    # Check quota before processing (tier quotas may impose limits)
    await check_scan_quota(db, department_id, pages=0)  # Pages checked after reading

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # Security validation - check for malicious content
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_pdf:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_pdf / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.PDF,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting PDF processing...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Save to persistent storage for remediation
    from ...utils.file_storage import save_uploaded_file

    # Rewind file to beginning for save
    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)
    scan.storage_path = storage_path

    # Also save temporarily for immediate processing
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    db.commit()

    logger.info(
        f"Created scan {scan.id} for PDF: {file.filename} (generate_alt_text={generate_alt_text})"
    )

    # Increment usage quota for free tier tracking
    # Note: We count scans, pages are counted during processing
    await increment_usage(db, department_id, scans=1, pages=0)

    # Start background processing (in thread pool to avoid blocking the event loop)
    background_tasks.add_task(
        _run_in_thread(
            process_pdf_background,
            tmp_path,
            content,
            file.filename,
            scan.id,
            generate_alt_text,
            enhance_descriptions,
        )
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",  # Uppercase to match ScanStatus enum
        "message": "PDF processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting PDF processing...",
    }


# ==================== PowerPoint Endpoints ====================


def process_pptx_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    generate_alt_text: bool,
    validate_alt_text: bool,
    storage_path: str,
    user_id: str,
    department_id: str,
):
    """Background task to process PowerPoint asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing PPTX: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] PPTX Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update PPTX progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process PowerPoint with progress callback
        processor = PowerPointProcessor(
            generate_alt_text=generate_alt_text,
            validate_alt_text=validate_alt_text,
            progress_callback=update_progress,
        )
        result = processor.process_pptx(file_path)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] PPTX processed in {processing_time}ms: {filename}")

        # Aggregate issues from all slides
        all_issues = []
        critical = high = medium = low = 0

        for slide in result.slides:
            for issue in slide.contrast_issues:
                severity = "high" if issue.contrast_ratio < 3.0 else "medium"
                issue_obj = {
                    "slide": slide.slide_number,
                    "type": "contrast",
                    "severity": severity,
                    **issue.model_dump(),
                }
                all_issues.append(issue_obj)
                if severity == "high":
                    high += 1
                else:
                    medium += 1

            for issue in slide.alt_text_issues:
                issue_obj = {
                    "slide": slide.slide_number,
                    "type": "alt_text",
                    "severity": "critical",
                    **issue.model_dump(),
                }
                all_issues.append(issue_obj)
                critical += 1

            # Slide title issues -> Medium severity (WCAG 1.3.1)
            for issue in slide.title_issues:
                medium += 1
                all_issues.append(
                    {
                        "slide": slide.slide_number,
                        "type": "title",
                        "severity": "medium",
                        "issue_type": issue.issue_type,
                        "existing_title": issue.existing_title,
                        "suggested_title": issue.suggested_title,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "WCAG 1.3.1",
                        "criterion": "1.3.1",
                    }
                )

            # Image of text issues -> Medium severity (WCAG 1.4.5)
            for issue in slide.image_of_text_issues:
                medium += 1
                all_issues.append(
                    {
                        "slide": slide.slide_number,
                        "type": "image_of_text",
                        "severity": "medium",
                        "shape_id": issue.shape_id,
                        "shape_name": issue.shape_name,
                        "detected_text": issue.detected_text,
                        "text_length": issue.text_length,
                        "confidence": issue.confidence,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "WCAG 1.4.5",
                        "criterion": "1.4.5",
                    }
                )

        # Store slide structure
        structure = {
            "total_slides": result.total_slides,
            "total_shapes": result.total_shapes,
            "total_images": result.total_images,
            "slides": [
                {
                    "slide_number": slide.slide_number,
                    "title": slide.slide_title,
                    "issues": slide.total_issues,
                }
                for slide in result.slides
            ],
        }

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.total_slides
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()
        scan.storage_path = storage_path

        # Create ScanResult
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
            ocr_used=False,
            ollama_used=generate_alt_text,
            ollama_calls=result.total_images if generate_alt_text else 0,
        )

        db.add(scan_result)
        db.commit()

        # Increment usage quota for free tier tracking
        from ...middleware.quota import increment_usage_sync

        increment_usage_sync(db, department_id, scans=1, pages=result.total_slides)

        logger.info(f"[BACKGROUND] PPTX Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing PPTX {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/powerpoint/scan", response_model=dict)
async def scan_powerpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_alt_text: bool = False,
    validate_alt_text: bool = False,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan a PowerPoint file for accessibility compliance

    NOW STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    ENFORCES QUOTA LIMITS FOR FREE TIER 📊

    ✨ NEW: Optional AI-powered alt text generation
    - Set generate_alt_text=true to automatically generate alt text for images
    - Uses llava:7b vision model for educational context descriptions
    - Significantly increases processing time (~10s per image)

    ✨ NEW: Optional alt text validation
    - Set validate_alt_text=true to verify existing alt text accuracy
    - Uses AI vision to check if alt text matches image content
    """
    _, user_id, department_id = api_key_info

    # Check quota before processing (tier quotas may impose limits)
    await check_scan_quota(db, department_id, pages=0)

    if not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="File must be a PowerPoint (.pptx)")

    # Security validation - check for malicious macros and content
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_pptx:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_pptx / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.POWERPOINT,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting PowerPoint processing...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Save to persistent storage for remediation
    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)

    # Save temporarily for immediate processing
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pptx") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    db.commit()

    logger.info(
        f"Created scan {scan.id} for PPTX: {file.filename} (generate_alt_text={generate_alt_text})"
    )

    # Start background processing (in thread pool to avoid blocking the event loop)
    background_tasks.add_task(
        _run_in_thread(
            process_pptx_background,
            tmp_path,
            content,
            file.filename,
            scan.id,
            generate_alt_text,
            validate_alt_text,
            storage_path,
            user_id,
            department_id,
        )
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "message": "PowerPoint processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting PowerPoint processing...",
    }


# ==================== Word Document Endpoints ====================


def process_docx_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    generate_alt_text: bool,
    validate_alt_text: bool,
    storage_path: str,
    user_id: str,
    department_id: str,
):
    """Background task to process Word document asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing DOCX: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] DOCX Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update DOCX progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process Word document with progress callback
        processor = DocxProcessor(
            generate_alt_text=generate_alt_text,
            validate_alt_text=validate_alt_text,
            progress_callback=update_progress,
        )
        result = processor.process_docx(file_path, original_filename=filename)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] DOCX processed in {processing_time}ms: {filename}")

        # Aggregate all issues
        all_issues = []
        critical = high = medium = low = 0

        # Heading issues -> Medium severity
        for issue in result.heading_issues:
            medium += 1
            all_issues.append(
                {
                    "type": "heading",
                    "severity": "medium",
                    "paragraph_index": issue.paragraph_index,
                    "text": issue.text,
                    "issue_type": issue.issue_type,
                    "current_level": issue.current_level,
                    "expected_level": issue.expected_level,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "WCAG 1.3.1",
                    "criterion": "1.3.1",
                }
            )

        # Image issues -> High severity
        for issue in result.image_issues:
            high += 1
            all_issues.append(
                {
                    "type": "image",
                    "severity": "high",
                    "paragraph_index": issue.paragraph_index,
                    "image_index": issue.image_index,
                    "has_alt_text": issue.has_alt_text,
                    "existing_alt_text": issue.existing_alt_text,
                    "suggested_alt_text": issue.suggested_alt_text,
                    "detected_image_type": issue.detected_image_type,
                    "is_decorative": issue.is_decorative,
                    "is_chart": issue.is_chart,
                    "detailed_description": issue.detailed_description,
                    "rule": "WCAG 1.1.1",
                    "criterion": "1.1.1",
                }
            )

        # Table issues -> Medium severity
        for issue in result.table_issues:
            medium += 1
            all_issues.append(
                {
                    "type": "table",
                    "severity": "medium",
                    "table_index": issue.table_index,
                    "issue_type": issue.issue_type,
                    "row_count": issue.row_count,
                    "column_count": issue.column_count,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "WCAG 1.3.1",
                    "criterion": "1.3.1",
                }
            )

        # List issues -> Low severity
        for issue in result.list_issues:
            low += 1
            all_issues.append(
                {
                    "type": "list",
                    "severity": "low",
                    "paragraph_index": issue.paragraph_index,
                    "text": issue.text,
                    "issue_type": issue.issue_type,
                    "rule": "WCAG 1.3.1",
                    "criterion": "1.3.1",
                }
            )

        # Link issues -> Medium severity
        for issue in result.link_issues:
            medium += 1
            all_issues.append(
                {
                    "type": "link",
                    "severity": "medium",
                    "paragraph_index": issue.paragraph_index,
                    "link_text": issue.link_text,
                    "link_url": issue.link_url,
                    "issue_type": issue.issue_type,
                    "rule": "WCAG 2.4.4",
                    "criterion": "2.4.4",
                }
            )

        # Language issues -> High severity (WCAG 3.1.1)
        for issue in result.language_issues:
            high += 1
            all_issues.append(
                {
                    "type": "language",
                    "severity": "high",
                    "issue_type": issue.issue_type,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "WCAG 3.1.1",
                    "criterion": "3.1.1",
                }
            )

        # Title issues -> Medium severity (WCAG 2.4.2)
        for issue in result.title_issues:
            medium += 1
            all_issues.append(
                {
                    "type": "title",
                    "severity": "medium",
                    "issue_type": issue.issue_type,
                    "existing_title": issue.existing_title,
                    "suggested_title": issue.suggested_title,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "WCAG 2.4.2",
                    "criterion": "2.4.2",
                }
            )

        # Font size issues -> Medium severity (WCAG 1.4.4)
        for issue in result.font_size_issues:
            medium += 1
            all_issues.append(
                {
                    "type": "font_size",
                    "severity": "medium",
                    "paragraph_index": issue.paragraph_index,
                    "text_preview": issue.text_preview,
                    "font_size_pt": issue.font_size_pt,
                    "issue_type": issue.issue_type,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "WCAG 1.4.4",
                    "criterion": "1.4.4",
                }
            )

        # Store document structure
        structure = {
            "total_paragraphs": result.total_paragraphs,
            "total_images": result.total_images,
            "total_tables": result.total_tables,
            "total_lists": result.total_lists,
            "total_links": result.total_links,
            "heading_issues_count": len(result.heading_issues),
        }

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.total_paragraphs
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()
        scan.storage_path = storage_path

        # Create ScanResult
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
            ocr_used=False,
            ollama_used=generate_alt_text,
            ollama_calls=result.total_images if generate_alt_text else 0,
        )

        db.add(scan_result)
        db.commit()

        # Increment usage quota for free tier tracking
        from ...middleware.quota import increment_usage_sync

        increment_usage_sync(
            db, department_id, scans=1, pages=result.total_paragraphs // 10 or 1
        )

        logger.info(f"[BACKGROUND] DOCX Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing DOCX {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/word/scan", response_model=dict)
async def scan_word_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_alt_text: bool = False,
    validate_alt_text: bool = False,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan a Word document (.docx) for accessibility compliance

    STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    ENFORCES QUOTA LIMITS FOR FREE TIER 📊

    Checks for:
    - Heading structure (H1-H6 hierarchy)
    - Image alt text
    - Table headers
    - Fake lists (bullets via symbols)
    - Non-descriptive link text ("click here")
    - Language specification

    ✨ Optional AI-powered alt text generation for images
    """
    _, user_id, department_id = api_key_info

    # Check quota before processing (tier quotas may impose limits)
    await check_scan_quota(db, department_id, pages=0)

    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=400, detail="File must be a Word document (.docx)"
        )

    # Security validation - check for malicious macros and content
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    max_size = getattr(settings, "max_file_size_docx", 50 * 1024 * 1024)  # 50MB default
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {max_size / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting Word document processing...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Save to persistent storage for remediation
    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)

    # Save temporarily for immediate processing
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    db.commit()

    logger.info(
        f"Created scan {scan.id} for DOCX: {file.filename} (generate_alt_text={generate_alt_text})"
    )

    # Start background processing (in thread pool to avoid blocking the event loop)
    background_tasks.add_task(
        _run_in_thread(
            process_docx_background,
            tmp_path,
            content,
            file.filename,
            scan.id,
            generate_alt_text,
            validate_alt_text,
            storage_path,
            user_id,
            department_id,
        )
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "message": "Word document processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting Word document processing...",
    }


# ==================== Excel Endpoints ====================


def process_xlsx_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    generate_chart_descriptions: bool,
    generate_alt_text: bool,
    storage_path: str,
    user_id: str,
    department_id: str,
):
    """Background task to process Excel spreadsheet asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing XLSX: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] XLSX Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update XLSX progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process Excel spreadsheet with progress callback
        processor = XlsxProcessor(
            generate_chart_descriptions=generate_chart_descriptions,
            generate_alt_text=generate_alt_text,
            progress_callback=update_progress,
        )
        result = processor.process_xlsx(file_path, original_filename=filename)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] XLSX processed in {processing_time}ms: {filename}")

        # Aggregate all issues
        all_issues = []
        critical = high = medium = low = 0

        # Sheet name issues -> Low severity
        for issue in result.sheet_name_issues:
            low += 1
            all_issues.append(
                {
                    "type": "sheet_name",
                    "severity": "low",
                    "sheet_name": issue.sheet_name,
                    "sheet_index": issue.sheet_index,
                    "issue_type": issue.issue_type,
                    "suggested_fix": issue.suggested_fix,
                    "rule": "Best Practice",
                    "criterion": "BP",
                }
            )

        # Per-sheet issues
        for sheet in result.sheets:
            # Table header issues -> High severity
            for issue in sheet.table_header_issues:
                high += 1
                all_issues.append(
                    {
                        "type": "table_header",
                        "severity": "high",
                        "sheet_name": issue.sheet_name,
                        "table_range": issue.table_range,
                        "issue_type": issue.issue_type,
                        "row_count": issue.row_count,
                        "column_count": issue.column_count,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "WCAG 1.3.1",
                        "criterion": "1.3.1",
                    }
                )

            # Chart issues -> High severity
            for issue in sheet.chart_issues:
                high += 1
                all_issues.append(
                    {
                        "type": "chart",
                        "severity": "high",
                        "sheet_name": issue.sheet_name,
                        "chart_index": issue.chart_index,
                        "chart_type": issue.chart_type,
                        "has_alt_text": issue.has_alt_text,
                        "existing_alt_text": issue.existing_alt_text,
                        "suggested_alt_text": issue.suggested_alt_text,
                        "detailed_description": issue.detailed_description,
                        "data_summary": issue.data_summary,
                        "rule": "WCAG 1.1.1",
                        "criterion": "1.1.1",
                    }
                )

            # Image issues -> High severity
            for issue in sheet.image_issues:
                high += 1
                all_issues.append(
                    {
                        "type": "image",
                        "severity": "high",
                        "sheet_name": issue.sheet_name,
                        "image_index": issue.image_index,
                        "has_alt_text": issue.has_alt_text,
                        "existing_alt_text": issue.existing_alt_text,
                        "suggested_alt_text": issue.suggested_alt_text,
                        "detected_image_type": issue.detected_image_type,
                        "is_decorative": issue.is_decorative,
                        "rule": "WCAG 1.1.1",
                        "criterion": "1.1.1",
                    }
                )

            # Merge issues -> Low severity
            for issue in sheet.merge_issues:
                low += 1
                all_issues.append(
                    {
                        "type": "merge",
                        "severity": "low",
                        "sheet_name": issue.sheet_name,
                        "merge_range": issue.merge_range,
                        "rows_merged": issue.rows_merged,
                        "cols_merged": issue.cols_merged,
                        "issue_type": issue.issue_type,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "Best Practice",
                        "criterion": "BP",
                    }
                )

            # Color issues -> Medium severity
            for issue in sheet.color_issues:
                medium += 1
                all_issues.append(
                    {
                        "type": "color",
                        "severity": "medium",
                        "sheet_name": issue.sheet_name,
                        "cell_range": issue.cell_range,
                        "issue_type": issue.issue_type,
                        "colors_used": issue.colors_used,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "WCAG 1.4.1",
                        "criterion": "1.4.1",
                    }
                )

            # Navigation issues -> Low severity
            for issue in sheet.navigation_issues:
                low += 1
                all_issues.append(
                    {
                        "type": "navigation",
                        "severity": "low",
                        "sheet_name": issue.sheet_name,
                        "issue_type": issue.issue_type,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "Best Practice",
                        "criterion": "BP",
                    }
                )

            # Contrast issues -> High/Medium severity (WCAG 1.4.3)
            for issue in sheet.contrast_issues:
                severity = "high" if issue.contrast_ratio < 3.0 else "medium"
                if severity == "high":
                    high += 1
                else:
                    medium += 1
                all_issues.append(
                    {
                        "type": "contrast",
                        "severity": severity,
                        "sheet_name": issue.sheet_name,
                        "cell_reference": issue.cell_reference,
                        "text_preview": issue.text_preview,
                        "foreground_color": issue.foreground_color,
                        "background_color": issue.background_color,
                        "contrast_ratio": issue.contrast_ratio,
                        "wcag_aa_pass": issue.wcag_aa_pass,
                        "suggested_fix": issue.suggested_fix,
                        "rule": "WCAG 1.4.3",
                        "criterion": "1.4.3",
                    }
                )

        # Store spreadsheet structure
        structure = {
            "total_sheets": result.total_sheets,
            "total_rows": result.total_rows,
            "total_charts": result.total_charts,
            "total_images": result.total_images,
            "sheets": [
                {
                    "sheet_name": sheet.sheet_name,
                    "row_count": sheet.row_count,
                    "has_data": sheet.has_data,
                    "has_tables": sheet.has_tables,
                    "has_charts": sheet.has_charts,
                }
                for sheet in result.sheets
            ],
        }

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.total_sheets
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()
        scan.storage_path = storage_path

        # Create ScanResult
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
            ocr_used=False,
            ollama_used=generate_alt_text or generate_chart_descriptions,
            ollama_calls=(
                (result.total_images + result.total_charts)
                if (generate_alt_text or generate_chart_descriptions)
                else 0
            ),
        )

        db.add(scan_result)
        db.commit()

        # Increment usage quota for free tier tracking
        from ...middleware.quota import increment_usage_sync

        increment_usage_sync(db, department_id, scans=1, pages=result.total_sheets)

        logger.info(f"[BACKGROUND] XLSX Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing XLSX {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/excel/scan", response_model=dict)
async def scan_excel_spreadsheet(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_chart_descriptions: bool = False,
    generate_alt_text: bool = False,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan an Excel spreadsheet (.xlsx) for accessibility compliance

    STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    ENFORCES QUOTA LIMITS FOR FREE TIER 📊

    Checks for:
    - Sheet names (meaningful vs "Sheet1")
    - Table headers (defined vs missing)
    - Chart alt text/titles
    - Image alt text
    - Merged cells (accessibility impact)
    - Color-only information (WCAG 1.4.1)
    - Frozen panes (navigation aid)

    ✨ Optional AI-powered chart descriptions and image alt text
    """
    _, user_id, department_id = api_key_info

    # Check quota before processing (tier quotas may impose limits)
    await check_scan_quota(db, department_id, pages=0)

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=400, detail="File must be an Excel spreadsheet (.xlsx)"
        )

    # Security validation - check for malicious macros and content
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    max_size = getattr(
        settings, "max_file_size_xlsx", 100 * 1024 * 1024
    )  # 100MB default
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {max_size / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.EXCEL,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting Excel spreadsheet processing...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Save to persistent storage for remediation
    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)

    # Save temporarily for immediate processing
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    db.commit()

    logger.info(
        f"Created scan {scan.id} for XLSX: {file.filename} (generate_alt_text={generate_alt_text})"
    )

    # Start background processing (in thread pool to avoid blocking the event loop)
    background_tasks.add_task(
        _run_in_thread(
            process_xlsx_background,
            tmp_path,
            content,
            file.filename,
            scan.id,
            generate_chart_descriptions,
            generate_alt_text,
            storage_path,
            user_id,
            department_id,
        )
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "message": "Excel spreadsheet processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting Excel spreadsheet processing...",
    }


# ==================== LaTeX Endpoints ====================


def process_latex_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    use_ollama: bool,
    user_id: str,
    department_id: str,
):
    """Background task to process LaTeX document asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing LaTeX: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] LaTeX Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update LaTeX progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process LaTeX document with progress callback
        processor = LaTeXProcessor(
            use_ai=use_ollama,  # use_ollama maps to use_ai parameter
            progress_callback=update_progress,
        )
        result = processor.process_document(file_path)

        # Also detect accessibility issues in the source
        latex_text = file_content.decode("utf-8", errors="ignore")
        accessibility_issues = processor.detect_accessibility_issues(latex_text)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] LaTeX processed in {processing_time}ms: {filename}")
        logger.info(
            f"[BACKGROUND] Found {len(accessibility_issues)} accessibility issues"
        )

        # Aggregate issues from equations AND accessibility checks
        all_issues = []
        critical = high = medium = low = 0

        # Add equation conversion issues
        for eq in result.equations:
            if not eq.conversion_success:
                all_issues.append(
                    {
                        "equation_id": eq.equation_id,
                        "type": "conversion_failed",
                        "severity": "high",
                        "latex": eq.latex_source[:100],
                        "error": eq.error_message,
                        "wcag": "1.1.1",
                        "recommendation": "Check LaTeX syntax for errors.",
                    }
                )
                high += 1
            elif not eq.wcag_compliant:
                all_issues.append(
                    {
                        "equation_id": eq.equation_id,
                        "type": "wcag_noncompliant",
                        "severity": "medium",
                        "latex": eq.latex_source[:100],
                        "reason": "Missing ARIA label or MathML",
                        "wcag": "1.1.1",
                        "recommendation": "Ensure equation has proper ARIA labeling.",
                    }
                )
                medium += 1

        # Add accessibility issues (missing alt text, captions, metadata, etc.)
        severity_map = {
            "critical": "critical",
            "serious": "high",
            "moderate": "medium",
            "minor": "low",
        }
        for issue in accessibility_issues:
            mapped_severity = severity_map.get(issue.severity, "medium")
            all_issues.append(
                {
                    "type": issue.issue_type,
                    "severity": mapped_severity,
                    "description": issue.description,
                    "line_number": issue.line_number,
                    "latex_snippet": issue.latex_snippet,
                    "wcag": issue.wcag_criterion,
                    "recommendation": issue.recommendation,
                }
            )
            if mapped_severity == "critical":
                critical += 1
            elif mapped_severity == "high":
                high += 1
            elif mapped_severity == "medium":
                medium += 1
            else:
                low += 1

        # Store equation structure
        structure = {
            "total_equations": result.total_equations,
            "successful_conversions": result.successful_conversions,
            "failed_conversions": result.failed_conversions,
            "accessibility_issues_found": len(accessibility_issues),
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

        # Calculate compliance score based on ALL issues (not just equation conversion)
        # Weight: critical=10, high=5, medium=2, low=1
        total_penalty = (critical * 10) + (high * 5) + (medium * 2) + (low * 1)
        compliance_score = max(0.0, 100.0 - min(total_penalty, 100.0))

        logger.info(
            f"[BACKGROUND] LaTeX compliance: {compliance_score:.1f}% "
            f"(critical={critical}, high={high}, medium={medium}, low={low})"
        )

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = 1  # LaTeX documents don't have pages
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()

        # Create ScanResult
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=all_issues,
            structure=structure,
            html_output=result.html_output,
            ocr_used=False,
            ollama_used=use_ollama,
            ollama_calls=result.total_equations if use_ollama else 0,
        )

        db.add(scan_result)
        db.commit()

        logger.info(f"[BACKGROUND] LaTeX Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing LaTeX {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


def process_latex_pdf_background(
    file_path: str,
    filename: str,
    scan_id: str,
    use_ollama: bool,
    user_id: str,
    department_id: str,
):
    """
    Background task to process PDF with LaTeX-aware mode.

    This processes PDFs uploaded to the LaTeX scanner with enhanced math/equation
    detection. It uses the PDF processor with latex_aware=True to detect:
    - LaTeX-compiled PDFs (via producer metadata)
    - Math equations rendered as images without alt text
    - Untagged equation content
    - Missing MathML representations
    """
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(
            f"[BACKGROUND] Processing LaTeX PDF: {filename} (scan_id={scan_id})"
        )

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Update progress
        scan.progress = 10
        scan.progress_message = "Analyzing PDF structure..."
        db.commit()

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 30

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] LaTeX PDF Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process PDF with LaTeX-aware mode enabled
        processor = PDFProcessor(
            generate_alt_text=use_ollama,
            validate_alt_text=use_ollama,
            enhance_descriptions=use_ollama,
            progress_callback=update_progress,
            latex_aware=True,  # Enable enhanced math/equation detection
        )
        result = processor.process_pdf(file_path, filename)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"[BACKGROUND] LaTeX PDF processed in {processing_time}ms: {filename}"
        )

        # Convert issues to list format
        all_issues = []
        critical = high = medium = low = 0

        for issue in result.issues:
            severity = issue.get("severity", "medium").lower()
            all_issues.append(issue)

            if severity == "critical":
                critical += 1
            elif severity == "high":
                high += 1
            elif severity == "medium":
                medium += 1
            else:
                low += 1

        # Store structure info
        structure = {
            "pages": result.pages,
            "text_extracted": result.text_extracted,
            "ocr_used": result.ocr_used,
            "latex_aware_mode": True,
            "headings": result.structure.get("headings", []),
            "paragraphs_count": len(result.structure.get("paragraphs", [])),
        }

        # Read file content for hash
        with open(file_path, "rb") as f:
            file_content = f.read()

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.pages
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()

        # Create ScanResult
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
            ocr_used=result.ocr_used,
            ollama_used=use_ollama,
            ollama_calls=0,  # Tracked separately by PDFProcessor
        )

        db.add(scan_result)
        db.commit()

        logger.info(
            f"[BACKGROUND] LaTeX PDF Scan {scan_id} completed: "
            f"score={result.compliance_score:.1f}%, issues={len(all_issues)}"
        )

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing LaTeX PDF {filename}: {str(e)}",
            exc_info=True,
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            # Full traceback already logged above (exc_info=True); these two
            # fields render in the UI, so no internal exception text here.
            scan.error_message = "Processing encountered an error. Please try again."
            scan.progress_message = "Processing encountered an error. Please try again."
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/latex/scan", response_model=dict)
async def scan_latex_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    use_ollama: bool = True,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan a LaTeX document for accessibility issues.

    Analyzes LaTeX documents for:
    - Math equations without proper MathML/ARIA accessibility
    - Missing figure descriptions/alt text
    - Document structure issues
    - Missing title/author metadata

    NOW STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    """
    # Delegate to convert_latex_document which handles the actual processing
    return await convert_latex_document(
        background_tasks=background_tasks,
        file=file,
        use_ollama=use_ollama,
        db=db,
        api_key_info=api_key_info,
    )


@router.post("/latex/convert", response_model=dict)
async def convert_latex_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    use_ollama: bool = True,  # ✨ NEW: Optional Ollama usage
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Convert a document containing LaTeX equations to accessible MathML

    Accepts:
    - .tex, .txt, .md files: Scans LaTeX source for accessibility issues
    - .pdf files: Scans PDF with enhanced math/equation detection (LaTeX-aware mode)

    NOW STORES RESULTS IN DATABASE! ✅
    REQUIRES API KEY IN PRODUCTION 🔒
    REQUIRES: latex feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)
    """
    _, user_id, department_id = api_key_info

    # Check feature access (tier-gated via TIER_QUOTAS)
    await require_feature(db, department_id, "latex", "LaTeX Conversion")

    filename_lower = file.filename.lower()
    is_pdf = filename_lower.endswith(".pdf")
    is_tex = filename_lower.endswith((".tex", ".txt", ".md"))

    if not is_pdf and not is_tex:
        raise HTTPException(
            status_code=400, detail="File must be .tex, .txt, .md, or .pdf"
        )

    # Security validation
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    max_size = settings.max_file_size_pdf if is_pdf else settings.max_file_size_code
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {max_size / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.LATEX,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting LaTeX processing...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Save to persistent storage for remediation
    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)
    scan.storage_path = storage_path

    # Save temporarily for immediate processing
    suffix = ".pdf" if is_pdf else ".tex"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    db.commit()

    logger.info(
        f"Created scan {scan.id} for LaTeX: {file.filename} (is_pdf={is_pdf}, use_ollama={use_ollama}, storage={storage_path})"
    )

    # Route to appropriate background processor (in thread pool to avoid blocking the event loop)
    if is_pdf:
        # PDF uploaded to LaTeX scanner -> use PDF processor with latex_aware mode
        background_tasks.add_task(
            _run_in_thread(
                process_latex_pdf_background,
                tmp_path,
                file.filename,
                scan.id,
                use_ollama,
                user_id,
                department_id,
            )
        )
    else:
        # .tex file -> use LaTeX processor
        background_tasks.add_task(
            _run_in_thread(
                process_latex_background,
                tmp_path,
                content,
                file.filename,
                scan.id,
                use_ollama,
                user_id,
                department_id,
            )
        )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "message": "LaTeX processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting LaTeX processing...",
    }
