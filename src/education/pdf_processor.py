"""
PDF OCR and Remediation Module

This module provides functionality to:
1. Extract text from text-based PDFs
2. OCR scanned PDFs using OCRmyPDF (with pytesseract fallback)
3. Detect document structure (headings, paragraphs, lists, tables)
4. Generate accessible HTML output
5. Check WCAG 2.1 compliance
6. Extract and analyze images with AI-generated alt text

OCRmyPDF Integration:
- Preserves PDF structure (headings, lists, tables)
- Auto-deskews crooked scans
- Cleans background noise
- Creates searchable PDFs with text layer
- Falls back to pytesseract if OCRmyPDF fails
"""

from typing import List, Dict, Optional
import pytesseract
from pdf2image import convert_from_path
from pypdf import (
    PdfReader,
)  # Migrated from PyPDF2 (maintained fork with security fixes)
import os
import gc
import tempfile
import fitz  # PyMuPDF for image extraction
import logging
from sqlalchemy.orm import Session
import ocrmypdf  # Enhanced PDF OCR with structure preservation
from src.config.settings import get_settings

# Try to import pikepdf for PDF structure checks
try:
    import pikepdf
    from pikepdf import Name

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Name = None

from src.ai.providers import get_provider_manager
from src.education.color_blindness_simulator import (
    ColorBlindnessSimulator,
)
from src.education.pdf_checks.models import (
    PDFProcessingResult,
)
from src.education.pdf_checks.table_checker import TableAccessibilityChecker
from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.pdf_checks.contrast_checker import ColorContrastChecker
from src.education.pdf_checks.form_checker import FormFieldChecker
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.pdf_checks.math_checker import MathEquationChecker
from src.education.pdf_checks.image_checker import ImageAccessibilityChecker

logger = logging.getLogger(__name__)


class PDFProcessor:
    """Process PDF files for accessibility compliance"""

    def __init__(
        self,
        generate_alt_text: bool = False,
        validate_alt_text: bool = False,
        enhance_descriptions: bool = False,
        db_session: Optional[Session] = None,
        progress_callback: Optional[callable] = None,
        simulate_color_blindness: bool = False,
        latex_aware: bool = False,
        llm_client=None,
        visual_analysis_recorder=None,
    ):
        self.tesseract_config = (
            "--oem 3 --psm 6"  # OCR Engine Mode 3, Page Segmentation Mode 6
        )
        self.generate_alt_text = generate_alt_text
        self.validate_alt_text = validate_alt_text
        self.enhance_descriptions = enhance_descriptions
        self.db_session = db_session
        self.progress_callback = progress_callback
        self.image_generator = None
        self.llm_client = (
            llm_client
            if llm_client is not None
            else get_provider_manager() if self.enhance_descriptions else None
        )
        # Color vision deficiency simulation
        self.simulate_color_blindness = simulate_color_blindness
        self.cvd_simulator = (
            ColorBlindnessSimulator() if simulate_color_blindness else None
        )
        # LaTeX-aware mode: enhanced math/equation detection for STEM PDFs
        self.latex_aware = latex_aware

        # Lazy import to avoid circular dependencies
        if self.generate_alt_text or self.validate_alt_text:
            try:
                from .image_alt_text import ImageAltTextGenerator

                self.image_generator = ImageAltTextGenerator(
                    lms_client=llm_client,
                    allow_legacy_transport=llm_client is None,
                    visual_analysis_recorder=visual_analysis_recorder,
                )
            except Exception as e:
                print(
                    f"[PDFProcessor] Warning: Could not initialize ImageAltTextGenerator: {e}"
                )
                self.generate_alt_text = False
                self.validate_alt_text = False

    def _validate_pdf_size(self, file_path: str) -> None:
        """
        Validate PDF file size before processing to prevent memory issues.

        Args:
            file_path: Path to the PDF file

        Raises:
            ValueError: If PDF is too large to process safely
        """
        settings = get_settings()
        max_size_mb = getattr(settings, "pdf_max_size_mb", 100)

        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValueError(
                f"PDF too large: {size_mb:.1f}MB (max: {max_size_mb}MB). "
                "Consider splitting the document or contact support for larger files."
            )
        logger.debug(
            f"[PDFProcessor] PDF size: {size_mb:.1f}MB (limit: {max_size_mb}MB)"
        )

    def _validate_pdf_pages(self, file_path: str) -> int:
        """
        Validate PDF page count and return total pages.

        Args:
            file_path: Path to the PDF file

        Returns:
            Total page count

        Raises:
            ValueError: If PDF exceeds maximum page limit
        """
        settings = get_settings()
        max_pages = getattr(settings, "pdf_max_pages", 500)

        with fitz.open(file_path) as doc:
            total_pages = len(doc)

        if total_pages > max_pages:
            raise ValueError(
                f"PDF has too many pages: {total_pages} (max: {max_pages}). "
                "Consider splitting the document or processing in sections."
            )
        logger.debug(f"[PDFProcessor] PDF pages: {total_pages} (limit: {max_pages})")
        return total_pages

    def process_pdf(
        self, file_path: str, original_filename: str = None
    ) -> PDFProcessingResult:
        """
        Process a PDF file and extract accessible content

        Args:
            file_path: Path to PDF file
            original_filename: Optional original filename (if different from file_path basename)

        Returns:
            PDFProcessingResult with extracted content and compliance info
        """
        # 0. Validate file size and page count before processing
        self._validate_pdf_size(file_path)
        self._validate_pdf_pages(file_path)

        # 1. Try text extraction first (for text-based PDFs)
        text = self._extract_text(file_path)

        # 2. If no text or very little, use OCR (for scanned PDFs)
        ocr_used = False
        if not text or len(text) < 100:
            text = self._ocr_pdf(file_path)
            ocr_used = True

        # 3. Analyze structure
        structure = self._analyze_structure(text)

        # 4. Extract document context for AI-powered fixes
        document_context = self._extract_document_context(
            structure, file_path, original_filename
        )

        # 5. Generate accessible HTML
        html = self._generate_html(structure, os.path.basename(file_path))

        # 6. Check compliance (pass context for enhanced descriptions)
        score, issues = self._check_compliance(html, structure, document_context)

        # 7. Always scan images. The checker itself stays in no-AI scan-only
        # mode unless generation or validation was explicitly requested.
        image_checker = ImageAccessibilityChecker(
            generate_alt_text=self.generate_alt_text,
            validate_alt_text=self.validate_alt_text,
            image_generator=self.image_generator,
            progress_callback=self.progress_callback,
            cvd_simulator=self.cvd_simulator,
        )
        logger.info("[PDFProcessor] Scanning images for accessibility issues...")
        image_issues = image_checker.check(file_path, document_context)
        logger.info(
            f"[PDFProcessor] _check_images() returned {len(image_issues) if image_issues else 0} issues"
        )

        # Merge image issues into main issues array
        if image_issues:
            logger.info(
                f"[PDFProcessor] Merging {len(image_issues)} image issues into main issues array"
            )
            for img_issue in image_issues:
                # Handle different image types appropriately
                if img_issue.image_type == "decorative":
                    # Decorative images need empty alt text (not missing alt text)
                    message = 'Decorative image detected - use empty alt="" attribute'
                    suggested_fix = 'Add alt="" (empty) to mark this as decorative'
                    severity = "medium"  # Lower severity - just needs empty alt
                elif img_issue.is_chart and img_issue.detailed_description:
                    # Chart/graph with detailed description
                    short_desc = img_issue.suggested_alt_text or ""
                    message = (
                        f'Chart/Graph detected - Alt: "{short_desc[:100]}..."'
                        if len(short_desc) > 100
                        else f'Chart/Graph detected - Alt: "{short_desc}"'
                    )
                    suggested_fix = (
                        f'Add alt text: "{short_desc}"\n\nFor complex charts, also add a longer description:\n{img_issue.detailed_description[:500]}...'
                        if len(img_issue.detailed_description or "") > 500
                        else f'Add alt text: "{short_desc}"\n\nFor complex charts, also add a longer description:\n{img_issue.detailed_description}'
                    )
                    severity = "high"
                elif img_issue.suggested_alt_text:
                    # Standard informative image with alt text
                    message = f'AI-Generated Alt Text: "{img_issue.suggested_alt_text}"'
                    suggested_fix = f'Add this alt text to the image: "{img_issue.suggested_alt_text}"'
                    severity = "high"
                else:
                    message = "Image missing alternative text - AI analysis pending"
                    suggested_fix = "Add descriptive alt text for this image"
                    severity = "high"

                new_issue = {
                    "severity": severity,
                    "rule": "WCAG 1.1.1",
                    "message": message,
                    "impact": (
                        "Screen reader users cannot understand image content"
                        if img_issue.image_type != "decorative"
                        else "Decorative images should have empty alt to be ignored by screen readers"
                    ),
                    "page_number": img_issue.page_number,
                    "location": f"Page {img_issue.page_number}, Image {img_issue.image_index + 1}",
                    "element": f"<img> (image {img_issue.image_index + 1})",
                    "suggested_fix": suggested_fix,
                    "alt_text": img_issue.suggested_alt_text,  # Add alt_text field for easy access
                    "image_type": img_issue.image_type,  # decorative/informative/functional/complex
                    "is_decorative": img_issue.image_type == "decorative",
                    "image_xref": img_issue.image_xref,  # PDF xref so remediator can extract image bytes
                    "image_index": img_issue.image_index,
                    "occurrence_ordinal": img_issue.occurrence_ordinal,
                    "bbox": list(img_issue.bbox),
                    "occurrence_id": img_issue.occurrence_id,
                    "is_chart": img_issue.is_chart,  # True if chart/graph/infographic
                    "detailed_description": img_issue.detailed_description,  # For charts/complex images
                }
                logger.info(
                    f"[PDFProcessor] Adding image issue: type={img_issue.image_type}, xref={img_issue.image_xref}, is_chart={img_issue.is_chart}, alt={img_issue.suggested_alt_text[:50] if img_issue.suggested_alt_text else 'None'}..."
                )
                issues.append(new_issue)

            # Recalculate compliance score with image issues included
            logger.info(
                f"[PDFProcessor] Recalculating compliance score with {len(issues)} total issues"
            )
            score = self._calculate_compliance_score(issues)
            logger.info(f"[PDFProcessor] New compliance score: {score}")
        else:
            logger.info("[PDFProcessor] No image issues to merge")

        # Conservative equation candidates are independent of LaTeX-aware
        # document-wide warnings and never invoke a provider.
        equation_candidates = MathEquationChecker().find_image_equation_candidates(
            file_path, image_issues
        )
        if equation_candidates:
            issues.extend(equation_candidates)
            score = self._calculate_compliance_score(issues)

        # 7.5. Check PDF structure accessibility (language, title, structure tree,
        #       list structure, font/role mapping, etc.)
        logger.info("[PDFProcessor] Checking PDF structure accessibility...")
        structure_checker = StructureTreeChecker()
        structure_issues = structure_checker.check(file_path)
        if structure_issues:
            logger.info(
                f"[PDFProcessor] Found {len(structure_issues)} PDF structure issues"
            )
            issues.extend(structure_issues)
            # Recalculate score with structure issues
            score = self._calculate_compliance_score(issues)
            logger.info(f"[PDFProcessor] Updated compliance score: {score}")

        # 7.55. Check table accessibility (detect untagged tables)
        try:
            table_issues = TableAccessibilityChecker().check(file_path)
            if table_issues:
                issues.extend(table_issues)
                score = self._calculate_compliance_score(issues)
                logger.info(
                    f"[PDFProcessor] Updated compliance score after table checks: {score}"
                )
        except Exception as e:
            logger.warning(
                f"[PDFProcessor] Table accessibility analysis failed (non-fatal): {e}"
            )

        # 7.56. Check reading order (visual vs structure tree order)
        try:
            ro_result = ReadingOrderVerifier().check(file_path, max_pages=10)
            if ro_result.issues:
                for ro_issue in ro_result.issues:
                    # Skip "no structure tree" issues (empty actual_order) --
                    # already covered by document-level structure checks
                    if not ro_issue.actual_order:
                        continue
                    issues.append(
                        {
                            "severity": (
                                "high" if ro_issue.severity == "critical" else "medium"
                            ),
                            "rule": "WCAG 1.3.2",
                            "message": f"Reading order mismatch on page {ro_issue.page_number}",
                            "impact": "Screen readers may read content in wrong order, especially in multi-column layouts",
                            "page_number": ro_issue.page_number,
                            "location": f"Page {ro_issue.page_number}",
                            "element": "Reading order",
                            "suggested_fix": ro_issue.recommendation,
                            "issue_type": "reading_order_mismatch",
                        }
                    )
                if any(i.get("issue_type") == "reading_order_mismatch" for i in issues):
                    score = self._calculate_compliance_score(issues)
                    logger.info(
                        f"[PDFProcessor] Updated compliance score after reading order checks: {score}"
                    )
                if ro_result.multi_column_detected:
                    logger.info("[PDFProcessor] Multi-column layout detected")
        except Exception as e:
            logger.warning(
                f"[PDFProcessor] Reading order analysis failed (non-fatal): {e}"
            )

        # 7.58. Check form field and link accessibility
        try:
            form_checker = FormFieldChecker()
            form_issues = form_checker.check(file_path)
            if form_issues:
                issues.extend(form_issues)
                score = self._calculate_compliance_score(issues)
                logger.info(
                    f"[PDFProcessor] Found {len(form_issues)} form field issues, score: {score}"
                )
            link_issues = form_checker.check_links(file_path)
            if link_issues:
                issues.extend(link_issues)
                score = self._calculate_compliance_score(issues)
                logger.info(
                    f"[PDFProcessor] Found {len(link_issues)} link issues, score: {score}"
                )
        except Exception as e:
            logger.warning(f"[PDFProcessor] Form/link check failed (non-fatal): {e}")

        # 7.61. Check color contrast (WCAG 1.4.3)
        try:
            contrast_issues = ColorContrastChecker().check(file_path)
            if contrast_issues:
                issues.extend(contrast_issues)
                score = self._calculate_compliance_score(issues)
                logger.info(
                    f"[PDFProcessor] Found {len(contrast_issues)} contrast issues, score: {score}"
                )
        except Exception as e:
            logger.warning(
                f"[PDFProcessor] Color contrast check failed (non-fatal): {e}"
            )

        # 7.6. Filter out "missing H1" issue if structure tree has H1
        # The text-based heuristics may not detect H1 after remediation adds structure tags
        if structure_checker.has_h1(file_path):
            original_count = len(issues)
            issues = [
                i
                for i in issues
                if not (
                    i.get("message", "").startswith("Document should start with H1")
                    and i.get("rule") == "WCAG 1.3.1"
                )
            ]
            if len(issues) < original_count:
                logger.info(
                    "[PDFProcessor] Filtered out 'missing H1' issue - H1 found in structure tree"
                )
                # Recalculate score after filtering
                score = self._calculate_compliance_score(issues)

        # 7.7. Check math/equation accessibility if latex_aware mode is enabled
        if self.latex_aware:
            logger.info(
                "[PDFProcessor] Running LaTeX-aware math/equation accessibility checks..."
            )
            math_issues = MathEquationChecker().check(file_path, text, structure)
            if math_issues:
                logger.info(
                    f"[PDFProcessor] Found {len(math_issues)} math/equation accessibility issues"
                )
                issues.extend(math_issues)
                # Recalculate score with math issues
                score = self._calculate_compliance_score(issues)
                logger.info(
                    f"[PDFProcessor] Updated compliance score after math checks: {score}"
                )

        # 8. Analyze color vision deficiency accessibility if enabled
        cvd_analysis = None
        if self.simulate_color_blindness and self.cvd_simulator:
            logger.info("[PDFProcessor] Running CVD accessibility analysis...")
            cvd_analysis = image_checker.check_cvd(file_path)
            if cvd_analysis:
                logger.info(
                    f"[PDFProcessor] CVD analysis complete: {len(cvd_analysis)} color pairs tested"
                )

        return PDFProcessingResult(
            file_path=file_path,
            file_name=(
                original_filename if original_filename else os.path.basename(file_path)
            ),
            pages=self._get_page_count(file_path),
            text_extracted=bool(text),
            ocr_used=ocr_used,
            structure=structure,
            html_output=html,
            compliance_score=score,
            issues=issues,
            image_issues=image_issues,
            cvd_analysis=cvd_analysis,
        )

    def _extract_text(self, file_path: str) -> str:
        """Extract text from text-based PDF"""
        try:
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
            return text.strip()
        except Exception as e:
            print(f"[PDFProcessor] Text extraction failed: {e}")
            return ""

    def _ocr_pdf(self, file_path: str) -> str:
        """
        OCR scanned PDF using OCRmyPDF (enhanced with structure preservation)
        Falls back to pytesseract if OCRmyPDF fails.
        """
        # Try enhanced OCRmyPDF first
        try:
            text, ocr_pdf_path = self._ocr_pdf_enhanced(file_path)
            logger.info(f"[PDFProcessor] OCRmyPDF successful, text length: {len(text)}")

            # Clean up temp OCR'd PDF (we only need the text for now)
            if (
                ocr_pdf_path
                and ocr_pdf_path != file_path
                and os.path.exists(ocr_pdf_path)
            ):
                try:
                    os.unlink(ocr_pdf_path)
                except Exception as e:
                    logger.warning(
                        f"[PDFProcessor] Could not delete temp OCR file: {e}"
                    )

            return text
        except Exception as e:
            # Fallback to pytesseract if OCRmyPDF fails
            logger.warning(
                f"[PDFProcessor] OCRmyPDF failed ({e}), using pytesseract fallback"
            )
            return self._ocr_pdf_fallback(file_path)

    def _ocr_pdf_enhanced(self, file_path: str) -> tuple:
        """
        OCR scanned PDF using OCRmyPDF (enhanced with structure preservation)

        Args:
            file_path: Path to input PDF

        Returns:
            Tuple of (text, output_pdf_path)
            - text: Extracted text content
            - output_pdf_path: Path to OCR'd PDF with searchable text layer
        """
        # Create temporary output file
        output_fd, output_path = tempfile.mkstemp(suffix="_ocr.pdf")
        os.close(output_fd)  # Close file descriptor, OCRmyPDF will write to it

        try:
            logger.info(f"[PDFProcessor] Running OCRmyPDF on {file_path}")

            # Run OCRmyPDF to add searchable text layer + structure
            ocrmypdf.ocr(
                input_file=file_path,
                output_file=output_path,
                # OCR Options
                force_ocr=False,  # Skip OCR if text layer exists (faster)
                skip_text=False,  # Don't skip text pages
                redo_ocr=False,  # Don't redo existing OCR
                # Image Processing
                deskew=True,  # Straighten crooked scans
                clean=True,  # Clean background noise
                remove_background=False,  # Don't remove colored backgrounds (may remove content)
                # Quality Settings
                optimize=1,  # Optimize file size (0=none, 3=aggressive)
                language=["eng"],  # English (can add more: ['eng', 'spa', 'fra'])
                output_type="pdf",  # Keep as PDF
                # Tesseract Config
                tesseract_timeout=180,  # 3 minute timeout per page
                # Misc
                progress_bar=False,  # No progress bar (we have our own)
                use_threads=True,  # Use multiple threads (faster)
            )

            logger.info(f"[PDFProcessor] OCRmyPDF completed: {output_path}")

            # Extract text from OCR'd PDF
            text = self._extract_text(output_path)

            if not text or len(text) < 50:
                raise RuntimeError(
                    "OCRmyPDF produced empty output, text extraction failed"
                )

            return text, output_path

        except (
            ocrmypdf.exceptions.PriorOcrFoundError,
            ocrmypdf.exceptions.TaggedPDFError,
        ):
            # PDF already carries a text layer (PriorOcrFound) or a structure
            # tree (TaggedPDF), so OCRmyPDF refuses to run. TaggedPDFError is
            # expected during remediation verification, where we re-scan a PDF
            # we just added tags to.
            logger.info(
                "[PDFProcessor] PDF already has a text layer or structure tags, "
                "extracting directly without OCR"
            )
            text = self._extract_text(file_path)

            # Clean up temp file
            if os.path.exists(output_path):
                os.unlink(output_path)

            # A structure tree is not a text layer. Remediation writes tags onto
            # the original scan, so the verification re-scan hits TaggedPDFError
            # on a file whose pages are still pure image — direct extraction
            # returns nothing and the document reads as having no content at
            # all. OCR the rasterised pages instead, matching the threshold the
            # OCRmyPDF path uses to decide its own output was empty.
            if len(text) < 50:
                logger.info(
                    f"[PDFProcessor] Direct extraction yielded {len(text)} chars "
                    "from a tagged/text-layer PDF; running pytesseract to read "
                    "the page images"
                )
                text = self._ocr_pdf_fallback(file_path)

            return text, file_path

        except Exception as e:
            logger.error(f"[PDFProcessor] OCRmyPDF failed: {e}")
            # Clean up temp file on error
            if os.path.exists(output_path):
                os.unlink(output_path)
            # Re-raise to trigger fallback
            raise

    def _ocr_pdf_fallback(self, file_path: str, max_pages: int = None) -> str:
        """
        Fallback OCR method using pytesseract with memory-efficient batch processing.

        Processes PDF pages in batches to limit memory usage, preventing crashes
        on large documents.

        Used when OCRmyPDF fails or is not available.

        Args:
            file_path: Path to the PDF file
            max_pages: Maximum pages to process (defaults to settings.pdf_max_pages)

        Returns:
            Extracted text from OCR
        """
        settings = get_settings()
        if max_pages is None:
            max_pages = getattr(settings, "pdf_max_pages", 500)

        # Get configurable DPI (lower = less memory)
        ocr_dpi = getattr(settings, "pdf_ocr_dpi", 150)
        batch_size = getattr(settings, "pdf_ocr_batch_size", 10)

        try:
            # Get total page count first using fitz (memory efficient)
            with fitz.open(file_path) as doc:
                total_pages = min(len(doc), max_pages)

            if total_pages == 0:
                logger.warning(f"[PDFProcessor] PDF has no pages: {file_path}")
                return ""

            logger.info(
                f"[PDFProcessor] OCR fallback: processing {total_pages} pages in batches of {batch_size} at {ocr_dpi} DPI"
            )

            all_text = []

            # Process in batches to limit memory usage
            for start in range(0, total_pages, batch_size):
                end = min(start + batch_size, total_pages)

                try:
                    # Convert only this batch of pages to images
                    images = convert_from_path(
                        file_path,
                        dpi=ocr_dpi,
                        first_page=start + 1,  # pdf2image uses 1-indexed pages
                        last_page=end,
                    )

                    # OCR each page in the batch
                    for i, image in enumerate(images):
                        page_num = start + i + 1
                        try:
                            page_text = pytesseract.image_to_string(
                                image, config=self.tesseract_config
                            )
                            all_text.append(
                                f"\n\n--- Page {page_num} ---\n\n{page_text}"
                            )
                        finally:
                            # Explicitly close the image to free memory
                            image.close()

                    # Clean up batch
                    del images
                    gc.collect()

                    logger.debug(
                        f"[PDFProcessor] OCR batch complete: pages {start + 1}-{end}"
                    )

                except Exception as batch_error:
                    logger.warning(
                        f"[PDFProcessor] OCR batch {start + 1}-{end} failed: {batch_error}"
                    )
                    # Continue with other batches

            return "".join(all_text).strip()

        except Exception as e:
            logger.error(f"[PDFProcessor] Fallback pytesseract OCR failed: {e}")
            return ""

    def _analyze_structure(self, text: str) -> Dict[str, List]:
        """
        Detect headings, paragraphs, lists, tables using heuristics

        This is an 80% solution - we'll improve with ML in future iterations
        """
        lines = text.split("\n")
        structure = {"headings": [], "paragraphs": [], "lists": [], "tables": []}

        current_paragraph = []

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                # Save accumulated paragraph
                if current_paragraph:
                    structure["paragraphs"].append(
                        {"text": " ".join(current_paragraph), "line": i}
                    )
                    current_paragraph = []
                continue

            # Heading detection (all caps, short line, or numbered)
            if self._is_heading(line):
                # Save any accumulated paragraph first
                if current_paragraph:
                    structure["paragraphs"].append(
                        {"text": " ".join(current_paragraph), "line": i}
                    )
                    current_paragraph = []

                level = self._detect_heading_level(line)
                structure["headings"].append({"level": level, "text": line, "line": i})

            # List detection (starts with bullet or number)
            elif self._is_list_item(line):
                # Save any accumulated paragraph first
                if current_paragraph:
                    structure["paragraphs"].append(
                        {"text": " ".join(current_paragraph), "line": i}
                    )
                    current_paragraph = []

                structure["lists"].append({"text": line, "line": i})

            # Table detection (lots of spaces or tabs)
            elif self._is_table_row(line):
                # Save any accumulated paragraph first
                if current_paragraph:
                    structure["paragraphs"].append(
                        {"text": " ".join(current_paragraph), "line": i}
                    )
                    current_paragraph = []

                structure["tables"].append({"text": line, "line": i})

            # Everything else accumulates into paragraph
            else:
                current_paragraph.append(line)

        # Save any remaining paragraph
        if current_paragraph:
            structure["paragraphs"].append(
                {"text": " ".join(current_paragraph), "line": len(lines)}
            )

        return structure

    def _is_heading(self, line: str) -> bool:
        """Heuristic to detect if line is a heading"""
        # All caps (but not too long)
        if line.isupper() and 5 < len(line) < 100:
            return True

        # Starts with number pattern (1., 1.1, I., A., etc.)
        if len(line) > 2 and line[0].isdigit() and line[1] in (".", ")"):
            return True
        if len(line) > 3 and line[:2].isdigit() and line[2] == ".":
            return True

        # Short line (likely a heading if under 80 chars and no period at end)
        if len(line) < 80 and not line.endswith(".") and not line.endswith(","):
            # Check if it starts with capital
            if line[0].isupper():
                return True

        return False

    def _detect_heading_level(self, line: str) -> int:
        """Detect H1, H2, H3, etc. based on formatting"""
        # All caps = H1
        if line.isupper():
            return 1

        # Numbered sections
        if line[0].isdigit():
            # Count dots (1. = H2, 1.1 = H3, 1.1.1 = H4)
            dot_count = line[:20].count(".")
            return min(dot_count + 1, 6)  # Max H6

        # Default to H2
        return 2

    def _is_list_item(self, line: str) -> bool:
        """Detect list items"""
        # Bullet points
        if line.startswith(("•", "-", "*", "○", "▪", "◦", "–", "—")):
            return True

        # Numbered lists (1. , 1) , a. , a) , etc.)
        if len(line) > 2:
            if line[0].isdigit() and line[1:3] in (". ", ") ", ".\t", ")\t"):
                return True
            if line[0].isalpha() and line[1:3] in (". ", ") ", ".\t", ")\t"):
                return True

        return False

    def _is_table_row(self, line: str) -> bool:
        """Detect table rows (naive approach - multiple spaces/tabs indicate columns)"""
        # Multiple consecutive spaces (table columns)
        if "  " in line or "\t" in line:
            # But not if it's just indentation at start
            stripped = line.lstrip()
            if "  " in stripped or "\t" in stripped:
                return True
        return False

    def _extract_document_context(
        self, structure: Dict[str, List], file_path: str, original_filename: str = None
    ) -> Dict:
        """
        Extract comprehensive document context for AI to understand the document structure.
        This helps AI generate more relevant alt text and fix suggestions.
        """
        filename = original_filename or os.path.basename(file_path)

        context = {
            "filename": filename,
            "document_title": None,
            "headings": [],
            "topics": [],
            "total_paragraphs": len(structure.get("paragraphs", [])),
            "total_lists": len(structure.get("lists", [])),
            "total_tables": len(structure.get("tables", [])),
            "page_count": self._get_page_count(file_path),
        }

        # Extract document title (usually first H1 heading)
        if structure.get("headings"):
            for heading in structure["headings"]:
                if heading.get("level") == 1:
                    context["document_title"] = heading.get("text", "")[:100]
                    break
            # If no H1, use first heading as title
            if not context["document_title"]:
                context["document_title"] = structure["headings"][0].get("text", "")[
                    :100
                ]

            # Extract all heading texts for topic understanding
            context["headings"] = [
                {"level": h.get("level"), "text": h.get("text", "")[:80]}
                for h in structure["headings"][:15]  # Limit to first 15 headings
            ]

        # Extract key topics from first few paragraphs
        if structure.get("paragraphs"):
            first_paragraphs = structure["paragraphs"][:3]
            context["topics"] = [p.get("text", "")[:150] for p in first_paragraphs]

        return context

    def _generate_html(self, structure: Dict[str, List], title: str) -> str:
        """Generate accessible HTML from structure"""
        html = '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        html += '  <meta charset="UTF-8">\n'
        html += (
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        )
        html += f"  <title>{title}</title>\n"
        html += "  <style>\n"
        html += "    body { font-family: Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }\n"
        html += (
            "    h1, h2, h3, h4, h5, h6 { margin-top: 1.5em; margin-bottom: 0.5em; }\n"
        )
        html += "    p { margin-bottom: 1em; }\n"
        html += "    ul, ol { margin-bottom: 1em; }\n"
        html += "    table { border-collapse: collapse; width: 100%; margin-bottom: 1em; }\n"
        html += (
            "    td, th { border: 1px solid #ddd; padding: 8px; text-align: left; }\n"
        )
        html += "  </style>\n"
        html += "</head>\n<body>\n"

        # Combine all elements and sort by line number
        all_elements = []
        for heading in structure["headings"]:
            all_elements.append(("heading", heading))
        for para in structure["paragraphs"]:
            all_elements.append(("paragraph", para))
        for item in structure["lists"]:
            all_elements.append(("list", item))
        for row in structure["tables"]:
            all_elements.append(("table", row))

        all_elements.sort(key=lambda x: x[1]["line"])

        # Generate HTML
        in_list = False
        in_table = False

        for elem_type, elem in all_elements:
            # Close open tags if switching type
            if elem_type != "list" and in_list:
                html += "  </ul>\n"
                in_list = False
            if elem_type != "table" and in_table:
                html += "  </table>\n"
                in_table = False

            if elem_type == "heading":
                level = elem["level"]
                html += f'  <h{level}>{elem["text"]}</h{level}>\n'

            elif elem_type == "paragraph":
                html += f'  <p>{elem["text"]}</p>\n'

            elif elem_type == "list":
                if not in_list:
                    html += "  <ul>\n"
                    in_list = True
                # Remove leading bullet/number
                text = elem["text"].lstrip("•-*○▪◦–—")
                if len(text) >= 2 and text[0].isdigit() and text[1] in (".", ")"):
                    text = text[2:].lstrip()
                elif len(text) >= 2 and text[0].isalpha() and text[1] in (".", ")"):
                    text = text[2:].lstrip()
                html += f"    <li>{text.strip()}</li>\n"

            elif elem_type == "table":
                if not in_table:
                    html += "  <table>\n"
                    in_table = True
                # Simple table row (split by multiple spaces or tabs)
                cells = [c.strip() for c in elem["text"].split("  ") if c.strip()]
                if not cells:
                    cells = [c.strip() for c in elem["text"].split("\t") if c.strip()]
                html += "    <tr>\n"
                for cell in cells:
                    html += f"      <td>{cell}</td>\n"
                html += "    </tr>\n"

        # Close any open tags
        if in_list:
            html += "  </ul>\n"
        if in_table:
            html += "  </table>\n"

        html += "</body>\n</html>"
        return html

    def _enhance_fix_description(self, issue: Dict) -> Optional[str]:
        """
        Enhance fix description using RAG + Ollama

        Query WCAG knowledge base for the rule and use Ollama to generate
        human-friendly, actionable fix descriptions.

        Args:
            issue: Issue dictionary with 'rule', 'message', 'impact'

        Returns:
            Enhanced fix description or None if enhancement failed
        """
        if (
            not self.enhance_descriptions
            or not self.db_session
            or self.llm_client is None
        ):
            return None

        # Extract criterion from rule (e.g., 'WCAG 3.1.1' -> '3.1.1')
        rule_text = issue.get("rule", "")
        if "WCAG" in rule_text:
            criterion = rule_text.replace("WCAG", "").strip()
        else:
            criterion = rule_text

        try:
            # Query WCAG knowledge base
            from ..db.models import WCAGGuideline

            guideline = (
                self.db_session.query(WCAGGuideline)
                .filter(WCAGGuideline.wcag_criterion == criterion)
                .first()
            )

            if not guideline:
                logger.warning(
                    f"[PDF+RAG] No guideline found for criterion: {criterion}"
                )
                return None

            # Build prompt with WCAG context
            prompt = f"""You are an accessibility expert. A PDF document has the following accessibility issue:

Issue: {issue.get('message', 'Unknown issue')}
Impact: {issue.get('impact', 'Unknown impact')}
WCAG Criterion: {guideline.wcag_criterion} - {guideline.title}
Level: {guideline.wcag_level}

WCAG Guideline Description:
{guideline.description}

Best Practices:
{chr(10).join(f"- {practice}" for practice in guideline.best_practices[:3])}

Provide a clear, actionable explanation of:
1. Why this is an accessibility issue
2. How to fix it in a PDF document
3. Specific steps to remediate

Be concise (2-3 sentences) and focus on practical solutions for PDF creators."""

            # Call Gemini for human-friendly explanation
            try:
                result = self.llm_client.generate_text_sync(
                    prompt=prompt, max_tokens=250, temperature=0.3
                )

                if result.get("success"):
                    enhanced_description = result["content"].strip()
                    if enhanced_description and len(enhanced_description) > 20:
                        logger.info(
                            f"[PDF+Gemini] Enhanced fix description for {criterion} (provider: {result.get('provider')})"
                        )
                        return enhanced_description
                    else:
                        logger.warning(
                            f"[PDF+Gemini] Enhanced description too short: {len(enhanced_description)} chars"
                        )
                        return None
                else:
                    logger.warning(
                        f"[PDF+Gemini] Generation failed for {criterion}: {result.get('error')}"
                    )
                    return None

            except Exception as e:
                logger.warning(f"[PDF+Gemini] Call failed for {criterion}: {e}")
                return None

        except Exception as e:
            logger.error(f"[PDF+RAG] Fix enhancement failed for {criterion}: {e}")
            return None

    def _check_compliance(
        self, html: str, structure: Dict[str, List], document_context: Dict = None
    ) -> tuple[float, List[Dict]]:
        """Check WCAG 2.1 compliance of generated HTML with document context for enhanced descriptions"""
        issues = []

        # Check for language attribute
        if "lang=" not in html:
            issues.append(
                {
                    "severity": "critical",
                    "rule": "WCAG 3.1.1",
                    "message": "Missing lang attribute on html element",
                    "impact": "Screen readers cannot determine document language",
                    "page_number": 1,
                    "location": "Document root",
                    "element": "<html>",
                }
            )

        # Check for title
        if "<title>" not in html or "<title></title>" in html:
            issues.append(
                {
                    "severity": "high",
                    "rule": "WCAG 2.4.2",
                    "message": "Missing or empty page title",
                    "impact": "Users cannot identify page content quickly",
                    "page_number": 1,
                    "location": "Document metadata",
                    "element": "<title>",
                }
            )

        # Check for proper heading hierarchy
        if structure["headings"]:
            heading_levels = [h["level"] for h in structure["headings"]]
            if heading_levels and heading_levels[0] != 1:
                first_heading = structure["headings"][0]
                issues.append(
                    {
                        "severity": "medium",
                        "rule": "WCAG 1.3.1",
                        "message": "Document should start with H1 heading",
                        "impact": "Improper heading hierarchy affects navigation",
                        "page_number": 1,
                        "location": "Beginning of document",
                        "element": f"First heading: {first_heading['text'][:50]}...",
                    }
                )

        # Check for content
        if not structure["headings"] and not structure["paragraphs"]:
            issues.append(
                {
                    "severity": "critical",
                    "rule": "WCAG 2.4.1",
                    "message": "No content extracted from PDF",
                    "impact": "Document may be inaccessible or empty",
                    "page_number": 1,
                    "location": "Entire document",
                    "element": "N/A",
                }
            )

        # Enhance fix descriptions with RAG + Ollama
        if self.enhance_descriptions:
            for issue in issues:
                enhanced_desc = self._enhance_fix_description(issue)
                if enhanced_desc:
                    # Add enhanced description field (keeps original message too)
                    issue["how_to_fix"] = enhanced_desc

        # Use unified scoring (consistent with _calculate_compliance_score)
        score = self._calculate_compliance_score(issues)
        return max(0.0, float(score)), issues

    def _calculate_compliance_score(
        self, issues: List[Dict], total_elements: int = None
    ) -> float:
        """Calculate compliance score using unified scoring system.

        Uses the shared compliance_scoring module for consistent scoring
        across all Aelira scanners (PDF, PPTX, Web, Code, etc.).

        Args:
            issues: List of issue dictionaries with 'severity' field
            total_elements: Optional total elements scanned (for ratio-based scoring)

        Returns:
            Compliance score (0-100)
        """
        from .compliance_scoring import get_score_only

        return get_score_only(issues, total_elements, severity_field="severity")

    def _get_page_count(self, file_path: str) -> int:
        """Get PDF page count"""
        try:
            reader = PdfReader(file_path)
            return len(reader.pages)
        except Exception as e:
            print(f"[PDFProcessor] Failed to get page count: {e}")
            return 0

    # _analyze_cvd_accessibility, _check_images, _check_image_has_alt_text,
    # _build_context_string_for_image, _get_page_context_for_image moved to
    # src.education.pdf_checks.image_checker.ImageAccessibilityChecker

    async def export_to_html(self, result: PDFProcessingResult) -> str:
        """
        Export processing result to accessible HTML (async wrapper for tests)

        The HTML is already generated during processing and stored in result.html_output.
        This method provides an async interface for compatibility with test suites.

        Args:
            result: PDFProcessingResult from process_pdf()

        Returns:
            HTML string with accessible content
        """
        return result.html_output


class PDFBatchProcessor:
    """Process multiple PDF files in batch"""

    def __init__(self, generate_alt_text: bool = False):
        self.processor = PDFProcessor(generate_alt_text=generate_alt_text)

    def process_directory(self, directory: str) -> List[PDFProcessingResult]:
        """
        Process all PDFs in a directory

        Args:
            directory: Path to directory containing PDF files

        Returns:
            List of PDFProcessingResult for each file
        """
        results = []

        if not os.path.exists(directory):
            raise ValueError(f"Directory does not exist: {directory}")

        for filename in os.listdir(directory):
            if filename.lower().endswith(".pdf"):
                file_path = os.path.join(directory, filename)
                try:
                    print(f"[PDFBatchProcessor] Processing: {filename}")
                    result = self.processor.process_pdf(file_path)
                    results.append(result)
                except Exception as e:
                    print(f"[PDFBatchProcessor] Error processing {filename}: {e}")
                    # Continue with other files

        return results
