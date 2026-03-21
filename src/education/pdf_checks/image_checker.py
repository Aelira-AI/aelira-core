"""Image accessibility checking for PDFs.

Extracts images from PDF files and checks/generates alt text using AI.
Also provides color vision deficiency (CVD) accessibility analysis.
"""

import asyncio
import concurrent.futures
import logging
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from src.education.pdf_checks.models import PDFImageIssue

logger = logging.getLogger(__name__)


class ImageAccessibilityChecker:
    """Check image accessibility in PDFs and optionally generate alt text via AI."""

    def __init__(
        self,
        generate_alt_text: bool = False,
        validate_alt_text: bool = False,
        image_generator=None,
        progress_callback=None,
        cvd_simulator=None,
    ):
        self.generate_alt_text = generate_alt_text
        self.validate_alt_text = validate_alt_text
        self.image_generator = image_generator
        self.progress_callback = progress_callback
        self.cvd_simulator = cvd_simulator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self, file_path: str, document_context: Dict = None
    ) -> List[PDFImageIssue]:
        """
        Extract images from PDF and check/generate alt text
        ONLY for images that are missing alt text or have inadequate descriptions.

        PERFORMANCE OPTIMIZED: Batch processing with async concurrency (3x-5x faster)

        Args:
            file_path: Path to PDF file
            document_context: Document context for better AI-generated alt text

        Returns:
            List of PDFImageIssue with AI-generated alt text (only for images needing fixes)
        """
        logger.info(
            f"[ImageChecker] check called: generate_alt_text={self.generate_alt_text}, "
            f"validate_alt_text={self.validate_alt_text}, "
            f"has_image_generator={self.image_generator is not None}"
        )

        # When neither AI flag is set and there is no image generator we still
        # run a lightweight scan-only pass to detect images without alt text so
        # that the remediator can later use image_xref to extract bytes for
        # vision AI.  We only skip entirely if there is nothing at all to do.
        ai_enabled = (
            self.generate_alt_text or self.validate_alt_text
        ) and self.image_generator
        scan_only = not ai_enabled

        image_issues: List[PDFImageIssue] = []
        doc = None  # Track document for cleanup in finally block
        logger.info(
            f"[ImageChecker] Starting optimized batch image extraction from PDF: {file_path}"
        )

        try:
            # Open PDF with PyMuPDF (fitz)
            doc = fitz.open(file_path)
            logger.info(f"[ImageChecker] PDF opened successfully, {len(doc)} pages")

            # SINGLE PASS: Extract all images needing analysis
            images_to_analyze = []  # List of dicts

            for page_num, page in enumerate(doc, start=1):
                images = page.get_images()
                logger.info(
                    f"[ImageChecker] Page {page_num}: found {len(images)} images"
                )

                for img_index, img_info in enumerate(images):
                    xref = img_info[0]
                    has_alt_text, existing_alt_text = self._check_image_has_alt_text(
                        doc, xref, page
                    )

                    if not has_alt_text and scan_only:
                        # Scan-only mode: record the issue with xref so the
                        # remediator can extract image bytes for vision AI later.
                        image_issues.append(
                            PDFImageIssue(
                                page_number=page_num,
                                image_index=img_index,
                                has_alt_text=False,
                                image_type="informative",  # conservative default
                                image_xref=xref,
                            )
                        )
                    elif not has_alt_text and self.generate_alt_text:
                        # Extract and save image to temp file immediately
                        try:
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]

                            # Save to temp file
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix=f".{image_ext}"
                            ) as tmp:
                                tmp.write(image_bytes)
                                temp_path = tmp.name

                            # Build rich context for this image
                            if document_context:
                                context = self._get_page_context_for_image(
                                    doc, page_num, document_context
                                )
                            else:
                                context = f"Image from PDF page {page_num}"

                            images_to_analyze.append(
                                {
                                    "page_num": page_num,
                                    "img_index": img_index,
                                    "xref": xref,
                                    "temp_path": temp_path,
                                    "context": context,
                                }
                            )
                        except Exception as e:
                            logger.error(
                                f"[ImageChecker] Failed to extract image on page {page_num}: {e}"
                            )
                            # Add failed extraction as issue
                            image_issues.append(
                                PDFImageIssue(
                                    page_number=page_num,
                                    image_index=img_index,
                                    has_alt_text=False,
                                    suggested_alt_text=f"[Extraction failed: {str(e)}]",
                                    image_xref=xref,
                                )
                            )
                    elif has_alt_text and existing_alt_text and self.validate_alt_text:
                        # Image HAS alt text - validate it with AI
                        try:
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]

                            # Save to temp file
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix=f".{image_ext}"
                            ) as tmp:
                                tmp.write(image_bytes)
                                temp_path = tmp.name

                            # Build context for validation
                            if document_context:
                                context = self._get_page_context_for_image(
                                    doc, page_num, document_context
                                )
                            else:
                                context = f"Image from PDF page {page_num}"

                            # Validate existing alt text with AI
                            validation_result = asyncio.run(
                                self.image_generator.validate_alt_text(
                                    image_path=temp_path,
                                    existing_alt_text=existing_alt_text,
                                    context=context,
                                )
                            )

                            # Clean up temp file
                            try:
                                os.unlink(temp_path)
                            except Exception:
                                pass

                            if validation_result.get("success"):
                                is_accurate = validation_result.get("is_accurate", True)
                                accuracy_score = validation_result.get(
                                    "accuracy_score", 1.0
                                )
                                validation_issues = validation_result.get("issues", [])
                                suggested_improvement = validation_result.get(
                                    "suggested_improvement"
                                )

                                # Only report if alt text is inaccurate or has issues
                                if (
                                    not is_accurate
                                    or accuracy_score < 0.7
                                    or validation_issues
                                ):
                                    image_issues.append(
                                        PDFImageIssue(
                                            page_number=page_num,
                                            image_index=img_index,
                                            has_alt_text=True,
                                            existing_alt_text=existing_alt_text,
                                            suggested_alt_text=suggested_improvement,
                                            alt_text_validated=True,
                                            alt_text_accurate=is_accurate,
                                            alt_text_issues=validation_issues,
                                            validation_score=accuracy_score,
                                        )
                                    )
                        except Exception as e:
                            logger.warning(
                                f"[ImageChecker] Alt text validation failed for page {page_num}: {e}"
                            )

            total_images = len(images_to_analyze)
            logger.info(
                f"[ImageChecker] Extracted {total_images} images needing AI analysis"
            )

            if self.progress_callback:
                self.progress_callback(
                    0,
                    total_images,
                    f"Batch analyzing {total_images} images (3x faster)...",
                )

            if total_images == 0:
                return image_issues

            # BATCH PROCESS: Analyze all images concurrently with smart type detection
            image_generator = self.image_generator
            progress_callback = self.progress_callback

            async def analyze_all_images():
                """
                Analyze all images concurrently using asyncio.gather for TRUE parallelism.

                SMART IMAGE ANALYSIS (Cross-Scanner Integration):
                1. First detect image type (decorative/informative/functional/complex)
                2. If decorative: skip alt text generation (use empty alt="")
                3. If complex (chart/graph/infographic): use describe_chart_or_graph()
                4. Otherwise: use standard generate_alt_text()
                """
                # PHASE 1: Detect image types in parallel
                logger.info(
                    f"[ImageChecker] PHASE 1: Detecting image types for {len(images_to_analyze)} images..."
                )
                if progress_callback:
                    progress_callback(
                        0,
                        total_images,
                        f"Detecting image types ({total_images} images)...",
                    )

                type_tasks = [
                    image_generator.detect_image_type(
                        image_path=img_data["temp_path"], context=img_data["context"]
                    )
                    for img_data in images_to_analyze
                ]
                type_results = await asyncio.gather(*type_tasks, return_exceptions=True)

                # Categorize images by type
                decorative_images = []
                chart_images = []
                informative_images = []

                for i, (img_data, type_result) in enumerate(
                    zip(images_to_analyze, type_results)
                ):
                    if isinstance(type_result, Exception):
                        logger.warning(
                            f"[ImageChecker] Type detection failed for page {img_data['page_num']}: {type_result}"
                        )
                        # Default to informative if detection fails
                        img_data["image_type"] = "informative"
                        img_data["is_decorative"] = False
                        img_data["is_chart"] = False
                        informative_images.append(img_data)
                    elif type_result.get("success"):
                        image_type = type_result.get("image_type", "informative")
                        is_decorative = type_result.get("is_decorative", False)
                        image_purpose = type_result.get("image_purpose", "")

                        img_data["image_type"] = image_type
                        img_data["is_decorative"] = is_decorative
                        img_data["type_confidence"] = type_result.get("confidence", 0)
                        img_data["type_reasoning"] = type_result.get("reasoning", "")

                        # Check if it's a chart/graph/infographic
                        is_chart = image_type == "complex" or any(
                            term in image_purpose.lower()
                            for term in [
                                "chart",
                                "graph",
                                "plot",
                                "diagram",
                                "infographic",
                                "data visualization",
                            ]
                        )
                        img_data["is_chart"] = is_chart

                        if is_decorative:
                            decorative_images.append(img_data)
                            logger.info(
                                f"[ImageChecker] Page {img_data['page_num']} image {img_data['img_index']+1}: DECORATIVE (skipping alt text)"
                            )
                        elif is_chart:
                            chart_images.append(img_data)
                            logger.info(
                                f"[ImageChecker] Page {img_data['page_num']} image {img_data['img_index']+1}: CHART/GRAPH (using detailed description)"
                            )
                        else:
                            informative_images.append(img_data)
                            logger.info(
                                f"[ImageChecker] Page {img_data['page_num']} image {img_data['img_index']+1}: INFORMATIVE (standard alt text)"
                            )
                    else:
                        # Detection failed but didn't raise exception
                        img_data["image_type"] = "informative"
                        img_data["is_decorative"] = False
                        img_data["is_chart"] = False
                        informative_images.append(img_data)

                logger.info(
                    f"[ImageChecker] Type detection complete: {len(decorative_images)} decorative, "
                    f"{len(chart_images)} charts, {len(informative_images)} informative"
                )

                # PHASE 2: Generate descriptions based on type
                results = []

                # Handle decorative images (no alt text needed)
                for img_data in decorative_images:
                    results.append(
                        (
                            img_data,
                            {
                                "success": True,
                                "alt_text": "",  # Empty alt for decorative images (WCAG compliant)
                                "is_decorative": True,
                                "image_type": "decorative",
                                "reasoning": img_data.get(
                                    "type_reasoning",
                                    "Detected as decorative/background image",
                                ),
                            },
                        )
                    )

                # PHASE 2a: Generate detailed descriptions for charts/graphs
                if chart_images:
                    logger.info(
                        f"[ImageChecker] PHASE 2a: Generating detailed chart descriptions "
                        f"for {len(chart_images)} images..."
                    )
                    if progress_callback:
                        progress_callback(
                            len(decorative_images),
                            total_images,
                            f"Generating chart descriptions ({len(chart_images)} charts)...",
                        )

                    chart_tasks = [
                        image_generator.describe_chart_or_graph(
                            image_path=img_data["temp_path"],
                            context=img_data["context"],
                            detail_level="standard",
                        )
                        for img_data in chart_images
                    ]
                    chart_results = await asyncio.gather(
                        *chart_tasks, return_exceptions=True
                    )

                    for img_data, chart_result in zip(chart_images, chart_results):
                        if isinstance(chart_result, Exception):
                            logger.error(
                                f"[ImageChecker] Chart description failed for page "
                                f"{img_data['page_num']}: {chart_result}"
                            )
                            results.append(
                                (
                                    img_data,
                                    {
                                        "success": False,
                                        "error": str(chart_result),
                                        "is_chart": True,
                                    },
                                )
                            )
                        elif chart_result.get("success"):
                            # Use short description as alt text, detailed as long description
                            results.append(
                                (
                                    img_data,
                                    {
                                        "success": True,
                                        "alt_text": chart_result.get(
                                            "short_description", ""
                                        ),
                                        "detailed_description": chart_result.get(
                                            "detailed_description", ""
                                        ),
                                        "chart_type": chart_result.get(
                                            "chart_type", ""
                                        ),
                                        "data_summary": chart_result.get(
                                            "data_summary", ""
                                        ),
                                        "insights": chart_result.get("insights", []),
                                        "is_chart": True,
                                        "image_type": "complex",
                                    },
                                )
                            )
                            logger.info(
                                f"[ImageChecker] Generated chart description for page "
                                f"{img_data['page_num']}: {chart_result.get('chart_type', 'unknown')}"
                            )
                        else:
                            results.append(
                                (
                                    img_data,
                                    {
                                        "success": False,
                                        "error": chart_result.get("error"),
                                        "is_chart": True,
                                    },
                                )
                            )

                # PHASE 2b: Generate standard alt text for informative images
                if informative_images:
                    logger.info(
                        f"[ImageChecker] PHASE 2b: Generating standard alt text "
                        f"for {len(informative_images)} images..."
                    )
                    if progress_callback:
                        progress_callback(
                            len(decorative_images) + len(chart_images),
                            total_images,
                            f"Generating alt text ({len(informative_images)} images)...",
                        )

                    alt_tasks = [
                        image_generator.generate_alt_text(
                            image_path=img_data["temp_path"],
                            context=img_data["context"],
                            educational_context=True,
                        )
                        for img_data in informative_images
                    ]
                    alt_results = await asyncio.gather(
                        *alt_tasks, return_exceptions=True
                    )

                    for img_data, alt_result in zip(informative_images, alt_results):
                        if isinstance(alt_result, Exception):
                            logger.error(
                                f"[ImageChecker] Alt text generation failed for page "
                                f"{img_data['page_num']}: {alt_result}"
                            )
                            results.append(
                                (img_data, {"success": False, "error": str(alt_result)})
                            )
                        elif alt_result.get("success"):
                            results.append(
                                (
                                    img_data,
                                    {
                                        "success": True,
                                        "alt_text": alt_result.get("alt_text", ""),
                                        "image_type": img_data.get(
                                            "image_type", "informative"
                                        ),
                                        "is_chart": False,
                                    },
                                )
                            )
                            logger.info(
                                f"[ImageChecker] Generated alt text for page {img_data['page_num']}, "
                                f"image {img_data['img_index'] + 1}"
                            )
                        else:
                            results.append(
                                (
                                    img_data,
                                    {
                                        "success": False,
                                        "error": alt_result.get("error"),
                                    },
                                )
                            )

                # Update final progress
                if progress_callback:
                    progress_callback(
                        total_images,
                        total_images,
                        f"Completed {total_images} images with smart analysis",
                    )

                return results

            # Run batch analysis in single event loop (much faster than creating new loops per image)
            def run_batch_async():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(analyze_all_images())
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_batch_async)
                batch_results = future.result(
                    timeout=total_images * 60
                )  # 60s per image max

            # Process results and clean up temp files
            for img_data, result in batch_results:
                suggested_alt = None
                detailed_desc = None
                image_type = None
                is_chart = False

                if result.get("success"):
                    suggested_alt = result.get("alt_text", "")
                    detailed_desc = result.get("detailed_description")
                    image_type = result.get("image_type", "informative")
                    is_chart = result.get("is_chart", False)

                    # For decorative images, use empty alt text (WCAG compliant)
                    if result.get("is_decorative"):
                        suggested_alt = ""  # Empty alt for decorative
                        image_type = "decorative"
                        logger.info(
                            f"[ImageChecker] Page {img_data['page_num']} image "
                            f"{img_data['img_index']+1}: Decorative - using empty alt"
                        )
                else:
                    suggested_alt = f"[AI generation failed: {result.get('error', 'Unknown error')}]"

                image_issues.append(
                    PDFImageIssue(
                        page_number=img_data["page_num"],
                        image_index=img_data["img_index"],
                        has_alt_text=False,
                        suggested_alt_text=suggested_alt,
                        image_type=image_type,
                        is_chart=is_chart,
                        detailed_description=detailed_desc,
                        image_xref=img_data.get("xref"),
                    )
                )

                # Clean up temp file
                try:
                    os.unlink(img_data["temp_path"])
                except Exception as e:
                    logger.warning(
                        f"[ImageChecker] Failed to delete temp file {img_data['temp_path']}: {e}"
                    )

            logger.info(
                f"[ImageChecker] Image extraction complete: {len(image_issues)} image issues found "
                f"(out of {total_images} total images)"
            )

        except Exception as e:
            logger.error(f"[ImageChecker] Failed to extract images from PDF: {e}")

        finally:
            # Ensure document is always closed to prevent memory leaks
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

        logger.info(f"[ImageChecker] Returning {len(image_issues)} image issues")
        return image_issues

    def check_cvd(self, file_path: str) -> Optional[List]:
        """
        Analyze color accessibility for color-blind users using PyMuPDF.

        Extracts text colors and background colors from PDF pages
        and tests them against all CVD types.

        Args:
            file_path: Path to PDF file

        Returns:
            List of ColorBlindnessAnalysisResult for each unique color pair,
            or None/empty list if no simulator is configured.
        """
        if not self.cvd_simulator:
            return []

        results = []
        color_pairs_seen: set = set()

        try:
            with fitz.open(file_path) as doc:
                for page_num, page in enumerate(doc):
                    # Extract text blocks with styling info
                    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

                    for block in blocks.get("blocks", []):
                        if block.get("type") != 0:  # Text block
                            continue

                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                # Get text color
                                text_color = span.get("color", 0)
                                if isinstance(text_color, int):
                                    # Convert integer color to hex
                                    r = (text_color >> 16) & 0xFF
                                    g = (text_color >> 8) & 0xFF
                                    b = text_color & 0xFF
                                    fg_hex = f"#{r:02x}{g:02x}{b:02x}"
                                else:
                                    continue

                                # PDF doesn't directly give background color per span
                                # Default to white background for text
                                bg_hex = "#ffffff"

                                # Skip if already analyzed this pair
                                pair_key = (fg_hex.lower(), bg_hex.lower())
                                if pair_key in color_pairs_seen:
                                    continue
                                color_pairs_seen.add(pair_key)

                                # Skip black on white (always passes)
                                if (
                                    fg_hex.lower() == "#000000"
                                    and bg_hex.lower() == "#ffffff"
                                ):
                                    continue

                                # Analyze this color pair
                                try:
                                    analysis = (
                                        self.cvd_simulator.analyze_color_accessibility(
                                            foreground=fg_hex, background=bg_hex
                                        )
                                    )
                                    # Only include if there are issues
                                    if analysis.issues:
                                        results.append(analysis)
                                except Exception as e:
                                    logger.warning(
                                        f"[ImageChecker] CVD analysis failed for {fg_hex}/{bg_hex}: {e}"
                                    )

                    # Also check for colored backgrounds in annotations/drawings
                    for annot in page.annots() or []:
                        try:
                            colors = annot.colors
                            if colors:
                                # Stroke color
                                stroke = colors.get("stroke")
                                if stroke and len(stroke) == 3:
                                    r, g, b = [int(c * 255) for c in stroke]
                                    fg_hex = f"#{r:02x}{g:02x}{b:02x}"

                                    # Fill color as background
                                    fill = colors.get("fill")
                                    if fill and len(fill) == 3:
                                        r, g, b = [int(c * 255) for c in fill]
                                        bg_hex = f"#{r:02x}{g:02x}{b:02x}"
                                    else:
                                        bg_hex = "#ffffff"

                                    pair_key = (fg_hex.lower(), bg_hex.lower())
                                    if pair_key not in color_pairs_seen:
                                        color_pairs_seen.add(pair_key)
                                        try:
                                            analysis = self.cvd_simulator.analyze_color_accessibility(
                                                foreground=fg_hex, background=bg_hex
                                            )
                                            if analysis.issues:
                                                results.append(analysis)
                                        except Exception:
                                            pass
                        except Exception:
                            pass

        except Exception as e:
            logger.error(f"[ImageChecker] CVD analysis failed: {e}")

        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_image_has_alt_text(
        self, doc: fitz.Document, xref: int, page: fitz.Page
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an image in the PDF has alt text metadata.

        Args:
            doc: PyMuPDF document object
            xref: Image xref number
            page: PyMuPDF page object

        Returns:
            Tuple of (has_alt_text: bool, alt_text_value: str or None)
        """
        try:
            # Method 1: Check PDF structure tree for alt text (most reliable)
            # The structure tree contains accessibility information including alt text
            struct_tree = page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES)

            # Look for this image in the structure tree
            for block in struct_tree.get("blocks", []):
                if block.get("type") == 1:  # Image block
                    # Check if this block has an alt attribute
                    if "alt" in block and block["alt"]:
                        # Check if this is the image we're looking for
                        # (compare image xref if available)
                        if block.get("xref") == xref:
                            alt_text = block["alt"]
                            logger.info(
                                f"[ImageChecker] Image xref {xref} has alt text: {alt_text[:50]}..."
                            )
                            return (True, alt_text)

            # Method 2: Check PDF object dictionary for /Alt key
            obj = doc.xref_object(xref)
            if "/Alt" in obj:
                logger.info(
                    f"[ImageChecker] Image xref {xref} has /Alt key in PDF object"
                )
                # Try to extract the alt text value
                alt_match = re.search(r"/Alt\s*\((.*?)\)", obj)
                alt_text = alt_match.group(1) if alt_match else None
                return (True, alt_text)

            # No alt text found
            return (False, None)

        except Exception as e:
            logger.warning(
                f"[ImageChecker] Failed to check alt text for image xref {xref}: {e}"
            )
            # If we can't determine, assume it needs alt text (safer assumption)
            return (False, None)

    def _build_context_string_for_image(
        self, document_context: Dict, page_number: int
    ) -> str:
        """Build a context string to help AI generate relevant alt text for images."""
        parts = []

        if document_context.get("document_title"):
            parts.append(f"Document: \"{document_context['document_title']}\"")

        parts.append(
            f"Page {page_number} of {document_context.get('page_count', 'unknown')}"
        )

        if document_context.get("headings"):
            # Find relevant headings for this image's context
            parts.append("Document structure:")
            for h in document_context["headings"][:5]:  # First 5 headings
                indent = "  " * (h["level"] - 1)
                parts.append(f"  {indent}H{h['level']}: \"{h['text']}\"")

        if document_context.get("topics"):
            parts.append("Document topics:")
            for topic in document_context["topics"]:
                parts.append(
                    f'  - "{topic[:100]}..."' if len(topic) > 100 else f'  - "{topic}"'
                )

        return "\n".join(parts)

    def _get_page_context_for_image(
        self, doc, page_num: int, document_context: Dict
    ) -> str:
        """
        Extract context specific to the page containing the image.
        Provides more specific context than document-level context.
        """
        try:
            page = doc[page_num - 1]  # Convert to 0-indexed
            page_text = page.get_text("text")

            # Extract first 500 chars of page text as context
            page_excerpt = page_text[:500].strip() if page_text else ""

            # Build context string
            context_parts = [
                self._build_context_string_for_image(document_context, page_num)
            ]

            if page_excerpt:
                context_parts.append(
                    f'\nText on this page (excerpt):\n"{page_excerpt}..."'
                )

            return "\n".join(context_parts)
        except Exception as e:
            logger.warning(f"[ImageChecker] Failed to extract page context: {e}")
            return self._build_context_string_for_image(document_context, page_num)
