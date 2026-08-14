"""Image accessibility analysis endpoints — alt text, chart description, type detection."""

import logging
import os
import tempfile
import time
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey
from ...education.image_alt_text import ImageAltTextGenerator
from ...middleware.quota import increment_image_usage
from ._shared import (
    check_image_analysis_quota,
    get_api_key_or_mock,
    validate_uploaded_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/image/alt-text")
async def generate_image_alt_text(
    file: UploadFile = File(...),
    context: Optional[str] = None,
    educational_context: bool = True,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Generate accessible alt text for an image using llava vision model

    ✨ NEW ENDPOINT - AI-powered image description generation
    REQUIRES API KEY IN PRODUCTION 🔒

    Args:
        file: Image file (jpg, png, gif, bmp, webp)
        context: Optional context (e.g., "Statistics lecture slide")
        educational_context: Whether this is for educational materials (default: True)

    Returns:
        - alt_text: Concise alt text (<125 characters)
        - long_description: Detailed description
        - image_type: Chart, Diagram, Photo, etc.
        - educational_value: Essential, Supplementary, or Decorative
        - contains_text: Whether image contains visible text
        - text_content: Any text detected in the image
    """
    _, user_id, department_id = api_key_info

    # Check image quota (separate from document scan quota)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate file extension
    allowed_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}",
        )

    # Security validation - verify file type via magic bytes
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_image:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(f"Generating alt text for: {file.filename} (user={user_id})")

        # Generate alt text
        generator = ImageAltTextGenerator()
        result = await generator.generate_alt_text(
            image_path=tmp_path,
            context=context,
            educational_context=educational_context,
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"Alt text generated in {processing_time}ms for: {file.filename}")

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to generate alt text"),
            )

        # Increment image usage after successful alt text generation
        await increment_image_usage(db, department_id, count=1)

        return {
            "success": True,
            "file_name": file.filename,
            "alt_text": result["alt_text"],
            "long_description": result["long_description"],
            "image_type": result["image_type"],
            "educational_value": result.get("educational_value"),
            "contains_text": result.get("contains_text", False),
            "text_content": result.get("text_content"),
            "image_metadata": result["image_metadata"],
            "inference_time_ms": int(result["inference_time"] * 1000),
            "model": result["model"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error generating alt text for {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to generate alt text. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/image/batch-alt-text")
async def batch_generate_alt_text(
    files: List[UploadFile] = File(...),
    context: Optional[str] = None,
    educational_context: bool = True,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Generate alt text for multiple images at once

    ✨ NEW ENDPOINT - Batch image processing
    REQUIRES API KEY IN PRODUCTION 🔒

    Useful for processing entire slide decks or document collections
    """
    _, user_id, department_id = api_key_info

    if len(files) > 50:
        raise HTTPException(
            status_code=400, detail="Maximum 50 images per batch request"
        )

    # Check image quota (each image in batch counts toward quota)
    await check_image_analysis_quota(db, department_id, count=len(files))

    # Check file size limits
    from ...config.settings import get_settings

    settings = get_settings()
    total_size = 0
    for file in files:
        content = await file.read()
        total_size += len(content)
        if len(content) > settings.max_file_size_image:
            raise HTTPException(
                status_code=400,
                detail=f"File {file.filename} too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
            )
        # Reset file pointer for processing
        await file.seek(0)

    results = []
    temp_files = []

    try:
        # Save all files temporarily
        for file in files:
            file_ext = os.path.splitext(file.filename)[1].lower()
            content = await file.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
                tmp.write(content)
                temp_files.append({"path": tmp.name, "filename": file.filename})

        # Process batch
        generator = ImageAltTextGenerator()
        image_paths = [tf["path"] for tf in temp_files]

        batch_result = await generator.batch_generate_alt_text(
            image_paths=image_paths,
            context=context,
            educational_context=educational_context,
        )

        # Format results
        for i, item in enumerate(batch_result["results"]):
            result = item["result"]
            results.append(
                {
                    "file_name": temp_files[i]["filename"],
                    "success": result.get("success", False),
                    "alt_text": result.get("alt_text"),
                    "long_description": result.get("long_description"),
                    "image_type": result.get("image_type"),
                    "educational_value": result.get("educational_value"),
                    "error": result.get("error"),
                }
            )

        # Increment image usage for successfully processed images
        await increment_image_usage(
            db, department_id, count=batch_result["success_count"]
        )

        return {
            "success": True,
            "total_images": batch_result["total_images"],
            "success_count": batch_result["success_count"],
            "failed_count": batch_result["failed_count"],
            "total_time_ms": int(batch_result["total_inference_time"] * 1000),
            "average_time_ms": int(batch_result["average_time_per_image"] * 1000),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Error in batch alt text generation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Batch processing failed. Please try again."
        )
    finally:
        # Cleanup all temp files
        for tf in temp_files:
            try:
                os.unlink(tf["path"])
            except Exception:
                pass


@router.post("/image/validate-alt-text")
async def validate_image_alt_text(
    file: UploadFile = File(...),
    existing_alt_text: str = Form(...),
    context: Optional[str] = Form(None),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Validate if existing alt text accurately describes an image

    🔍 AI-POWERED ALT TEXT VALIDATION
    REQUIRES API KEY IN PRODUCTION 🔒

    Uses vision AI to analyze the image and compare it to the existing alt text.
    Returns:
    - is_accurate: Whether the alt text correctly describes the image
    - accuracy_score: 0-1 score of how well the alt text matches
    - issues: Specific problems found (too generic, missing elements, etc.)
    - suggested_improvement: Better alt text if the current one is inaccurate

    Useful for:
    - Auditing existing websites for alt text quality
    - Validating bulk alt text before publishing
    - Training content creators on good alt text practices
    """
    _, user_id, department_id = api_key_info

    # Check image quota (standalone image API calls)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate file type
    valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image. Supported: {', '.join(valid_extensions)}",
        )

    # Read and validate size
    content = await file.read()
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_image:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
        )

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(f"Validating alt text for: {file.filename} (user={user_id})")

        # Validate alt text
        generator = ImageAltTextGenerator()
        result = await generator.validate_alt_text(
            image_path=tmp_path, existing_alt_text=existing_alt_text, context=context
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"Alt text validation completed in {processing_time}ms for: {file.filename}"
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to validate alt text"),
            )

        # Increment image usage after successful validation
        await increment_image_usage(db, department_id, count=1)

        return {
            "success": True,
            "file_name": file.filename,
            "existing_alt_text": existing_alt_text,
            "is_accurate": result.get("is_accurate", False),
            "accuracy_score": result.get("accuracy_score", 0.0),
            "issues": result.get("issues", []),
            "suggested_improvement": result.get("suggested_improvement"),
            "reasoning": result.get("reasoning", ""),
            "inference_time_ms": int(result.get("inference_time", 0) * 1000),
            "model": result.get("model", "unknown"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error validating alt text for {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to validate alt text. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/image/score-alt-text")
async def score_alt_text_quality(
    file: UploadFile = File(...),
    alt_text: str = Form(...),
    context: Optional[str] = Form(None),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Score alt text quality on a 0-100 scale with detailed breakdown

    📊 AI-POWERED ALT TEXT QUALITY SCORING
    REQUIRES API KEY IN PRODUCTION 🔒

    Evaluates alt text on multiple WCAG-aligned criteria to provide
    a quantitative quality score for tracking and reporting.

    Criteria scored:
    - **Length** (15%): Appropriate length for image complexity
    - **Descriptiveness** (25%): Specific and concrete vs generic
    - **Accuracy** (30%): How well it describes the actual image
    - **Accessibility** (20%): WCAG best practices compliance
    - **Context Fit** (10%): Appropriateness for usage context

    Returns:
    - overall_score: 0-100 quality score
    - grade: A/B/C/D/F letter grade
    - criteria_scores: Individual scores for each criterion
    - issues: List of specific problems found
    - suggestions: List of improvement recommendations
    - passes_wcag: Whether it meets WCAG 2.1 AA requirements

    Use cases:
    - Department-wide alt text quality reporting
    - Faculty training and feedback
    - Before/after remediation comparison
    - Audit preparation
    """
    _, user_id, department_id = api_key_info

    # Check image quota (standalone image API calls)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate file type
    valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image. Supported: {', '.join(valid_extensions)}",
        )

    # Save file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(f"Scoring alt text quality for: {file.filename} (user={user_id})")

        # Score alt text quality
        generator = ImageAltTextGenerator()
        result = await generator.score_alt_text_quality(
            image_path=tmp_path, alt_text=alt_text, context=context
        )

        processing_time = int((time.time() - start_time) * 1000)

        logger.info(
            f"Alt text quality scored in {processing_time}ms for: {file.filename} "
            f"(score={result.get('overall_score', 0)}, grade={result.get('grade', 'N/A')})"
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to score alt text quality"),
            )

        # Increment image usage after successful scoring
        await increment_image_usage(db, department_id, count=1)

        return {
            "success": True,
            "filename": file.filename,
            "overall_score": result.get("overall_score"),
            "grade": result.get("grade"),
            "criteria_scores": result.get("criteria_scores", {}),
            "criteria_analysis": result.get("criteria_analysis", {}),
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
            "best_practice_violations": result.get("best_practice_violations", []),
            "passes_wcag": result.get("passes_wcag", False),
            "alt_text_analyzed": alt_text,
            "processing_time_ms": processing_time,
            "provider": result.get("provider"),
            "model": result.get("model"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error scoring alt text for {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to score alt text quality. Please try again.",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/image/batch-score-alt-text")
async def batch_score_alt_text_quality(
    context: Optional[str] = Form(None),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Batch score alt text quality for multiple images

    📊 BATCH ALT TEXT QUALITY SCORING
    REQUIRES API KEY IN PRODUCTION 🔒

    Score multiple image alt texts at once and get aggregate statistics
    for department-wide quality reporting.

    Request body (multipart form):
    - items: JSON array of objects with 'filename' and 'alt_text'
    - files: Multiple image files matching the filenames
    - context: Optional shared context for all images

    Returns:
    - summary: Aggregate statistics (average score, grade distribution, common issues)
    - results: Individual scores for each image

    Note: This endpoint is currently a placeholder. For batch scoring,
    use the department analytics endpoints which aggregate stored scan results.
    """
    _, user_id, department_id = api_key_info

    # For now, return instructions on using the analytics endpoints
    return {
        "success": True,
        "message": "For batch alt text quality scoring, use the analytics endpoints",
        "recommended_endpoints": [
            {
                "endpoint": "GET /analytics/alt-text-quality/{department_id}",
                "description": "Get aggregate alt text quality metrics for a department",
            },
            {
                "endpoint": "POST /education/image/score-alt-text",
                "description": "Score individual images (call multiple times for batch)",
            },
        ],
        "note": "Alt text quality scores are automatically tracked when using scan endpoints with generate_alt_text=true",
    }


@router.post("/image/detect-type")
async def detect_image_type(
    file: UploadFile = File(...),
    context: Optional[str] = Form(None),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Detect if an image is decorative or informative (WCAG 1.1.1)

    🔍 AI-POWERED IMAGE CLASSIFICATION
    REQUIRES API KEY IN PRODUCTION 🔒

    Uses vision AI to analyze the image and classify its purpose:
    - DECORATIVE: Visual decoration only (use alt="")
    - INFORMATIVE: Conveys information (needs descriptive alt)
    - FUNCTIONAL: Performs an action (needs action-based alt)
    - COMPLEX: Charts, graphs, diagrams (needs long description)

    Returns:
    - is_decorative: Whether to use empty alt attribute
    - image_purpose: Classification type
    - confidence: 0-1 confidence score
    - recommended_alt: Suggested alt text (empty for decorative)
    """
    _, user_id, department_id = api_key_info

    # Check image quota (standalone image API calls)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate file type
    valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image. Supported: {', '.join(valid_extensions)}",
        )

    # Read and validate size
    content = await file.read()
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_image:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
        )

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(f"Detecting image type for: {file.filename} (user={user_id})")

        # Detect image type
        generator = ImageAltTextGenerator()
        result = await generator.detect_image_type(image_path=tmp_path, context=context)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"Image type detection completed in {processing_time}ms for: {file.filename}"
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to detect image type"),
            )

        # Increment image usage after successful detection
        await increment_image_usage(db, department_id, count=1)

        return {
            "success": True,
            "file_name": file.filename,
            "is_decorative": result.get("is_decorative", False),
            "image_purpose": result.get("image_purpose", "informative"),
            "confidence": result.get("confidence", 0.0),
            "reasoning": result.get("reasoning", ""),
            "recommended_alt": result.get("recommended_alt", ""),
            "visual_elements": result.get("visual_elements", []),
            "inference_time_ms": int(result.get("inference_time", 0) * 1000),
            "model": result.get("model", "unknown"),
            "wcag_guidance": {
                "decorative": 'Use alt="" (empty alt attribute)',
                "informative": "Use descriptive alt text",
                "functional": 'Describe the action (e.g., "Submit form")',
                "complex": "Use aria-describedby with long description",
            }.get(
                result.get("image_purpose", "informative"), "Use descriptive alt text"
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error detecting image type for {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to detect image type. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/image/describe-chart")
async def describe_chart_or_graph(
    file: UploadFile = File(...),
    context: Optional[str] = Form(None),
    detail_level: str = Form("standard"),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Generate detailed accessible descriptions for charts, graphs, and infographics

    📊 AI-POWERED CHART DESCRIPTION
    REQUIRES API KEY IN PRODUCTION 🔒

    Uses vision AI to analyze complex visualizations and generate:
    - Short description for alt text (under 150 chars)
    - Detailed description for long description/figcaption
    - Data summary with key values and trends
    - Key insights from the visualization

    Supports:
    - Bar charts, line graphs, pie charts
    - Scatter plots, flow diagrams
    - Infographics, maps, timelines
    - Organizational charts, tables

    Args:
        file: Image file containing chart/graph
        context: Optional context about the data
        detail_level: "brief", "standard", or "detailed"
    """
    _, user_id, department_id = api_key_info

    # Check image quota (standalone image API calls)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate detail_level
    if detail_level not in ["brief", "standard", "detailed"]:
        detail_level = "standard"

    # Validate file type
    valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image. Supported: {', '.join(valid_extensions)}",
        )

    # Read and validate size
    content = await file.read()
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_image:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
        )

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(
            f"Describing chart/graph: {file.filename} (user={user_id}, detail={detail_level})"
        )

        # Generate chart description
        generator = ImageAltTextGenerator()
        result = await generator.describe_chart_or_graph(
            image_path=tmp_path, context=context, detail_level=detail_level
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"Chart description completed in {processing_time}ms for: {file.filename}"
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to describe chart")
            )

        # Increment image usage after successful chart description
        await increment_image_usage(db, department_id, count=1)

        return {
            "success": True,
            "file_name": file.filename,
            "chart_type": result.get("chart_type", "unknown"),
            "title": result.get("title", ""),
            "short_description": result.get("short_description", ""),
            "detailed_description": result.get("detailed_description", ""),
            "data_summary": result.get("data_summary", {}),
            "insights": result.get("insights", []),
            "visual_elements": result.get("visual_elements", {}),
            "accessibility_note": result.get("accessibility_note", ""),
            "inference_time_ms": int(result.get("inference_time", 0) * 1000),
            "model": result.get("model", "unknown"),
            "implementation_example": f"""<figure>
  <img src="chart.png" alt="{result.get('short_description', 'Chart')[:100]}">
  <figcaption>{result.get('detailed_description', '')[:300]}...</figcaption>
</figure>""",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error describing chart for {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to describe chart. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/image/analyze-comprehensive")
async def analyze_image_comprehensive(
    file: UploadFile = File(...),
    context: Optional[str] = Form(None),
    existing_alt_text: Optional[str] = Form(None),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
    db: Session = Depends(get_db_dependency),
):
    """
    Comprehensive image analysis - detect type, generate description, validate alt text

    🔬 COMPLETE IMAGE ACCESSIBILITY ANALYSIS
    REQUIRES API KEY IN PRODUCTION 🔒

    Performs a full accessibility analysis in one request:
    1. Detects if image is decorative/informative/complex
    2. Generates appropriate description based on type
    3. If existing alt text provided, validates its accuracy

    Returns complete recommendations for making the image accessible.
    """
    _, user_id, department_id = api_key_info

    # Check image quota (standalone image API calls)
    await check_image_analysis_quota(db, department_id, count=1)

    # Validate file type
    valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image. Supported: {', '.join(valid_extensions)}",
        )

    # Read and validate size
    content = await file.read()
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_image:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_image / (1024*1024):.0f}MB",
        )

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(f"Comprehensive image analysis: {file.filename} (user={user_id})")

        # Perform comprehensive analysis
        generator = ImageAltTextGenerator()
        result = await generator.analyze_image_comprehensive(
            image_path=tmp_path, context=context, existing_alt_text=existing_alt_text
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"Comprehensive analysis completed in {processing_time}ms for: {file.filename}"
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to analyze image")
            )

        # Increment image usage after successful comprehensive analysis
        await increment_image_usage(db, department_id, count=1)

        response = {
            "success": True,
            "file_name": file.filename,
            "type_detection": result.get("type_detection", {}),
            "description": result.get("description", {}),
            "recommendation": result.get("recommendation", {}),
            "total_inference_time_ms": int(
                result.get("total_inference_time", 0) * 1000
            ),
            "processing_time_ms": processing_time,
        }

        # Add validation results if existing alt text was provided
        if existing_alt_text and result.get("validation"):
            response["validation"] = result.get("validation")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error in comprehensive analysis for {file.filename}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="Failed to analyze image. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
