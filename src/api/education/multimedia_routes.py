"""Multimedia accessibility endpoints — transcription, captions, audio descriptions."""

import hashlib
import logging
import os
import tempfile
import time
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey, ScanType
from ...education.multimedia_processor import MultimediaProcessor
from ...middleware.quota import require_feature
from ._shared import (
    get_api_key_or_mock,
    validate_uploaded_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/multimedia/transcribe", response_model=dict)
async def transcribe_multimedia(
    file: UploadFile = File(...),
    generate_captions: bool = True,
    whisper_model: str = "base",
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Transcribe video or audio file and generate captions

    AI-POWERED TRANSCRIPTION
    REQUIRES API KEY IN PRODUCTION
    REQUIRES: video feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)
    - Uses Whisper model for speech-to-text
    - Generates WebVTT and SRT caption files
    - Checks WCAG 2.1 multimedia compliance
    - Supports video and audio files
    - Processing time: ~30 seconds per minute of audio

    Args:
        file: Video or audio file (MP4, MOV, AVI, MP3, WAV, etc.)
        generate_captions: Whether to generate captions (default: true)
        whisper_model: Whisper model size (base, small, medium, large)
    """
    _, user_id, department_id = api_key_info

    # Check feature access (tier-gated via TIER_QUOTAS)
    await require_feature(db, department_id, "video", "Video/Audio Transcription")

    # Validate file type
    valid_extensions = [
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
    ]
    if not any(file.filename.lower().endswith(ext) for ext in valid_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File must be a video or audio file. Supported: {', '.join(valid_extensions)}",
        )

    # Security validation - verify file type for multimedia files
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_video:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_video / (1024*1024):.0f}MB",
        )

    # Save temporarily
    ext = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start_time = time.time()
        logger.info(
            f"Processing multimedia: {file.filename} (generate_captions={generate_captions}, model={whisper_model})"
        )

        # Process multimedia
        processor = MultimediaProcessor(whisper_model=f"whisper:{whisper_model}")
        result = processor.process_media(tmp_path, generate_captions=generate_captions)

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"Multimedia processed in {processing_time}ms: {file.filename}")

        # Build response
        response = {
            "success": True,
            "file_name": result.file_name,
            "media_type": result.media_type,
            "duration": result.duration,
            "duration_formatted": f"{int(result.duration // 60)}:{int(result.duration % 60):02d}",
            "has_captions": result.has_captions,
            "compliance_score": result.compliance_score,
            "issues_count": len(result.issues),
            "issues": result.issues,
            "processing_time_ms": processing_time,
        }

        # Add transcription if generated
        if result.transcription:
            response["transcription"] = {
                "segments_count": len(result.transcription),
                "segments": [
                    seg.dict() for seg in result.transcription[:10]
                ],  # First 10 segments
                "full_text": " ".join([seg.text for seg in result.transcription]),
            }

        # Add caption files if generated
        if result.caption_formats:
            response["captions"] = {
                "webvtt": result.caption_formats.get("webvtt", ""),
                "srt": result.caption_formats.get("srt", ""),
                "webvtt_preview": (
                    result.caption_formats.get("webvtt", "")[:500] + "..."
                    if len(result.caption_formats.get("webvtt", "")) > 500
                    else result.caption_formats.get("webvtt", "")
                ),
            }

        return response

    except Exception as e:
        logger.error(
            f"Error processing multimedia {file.filename}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to process multimedia. Please try again."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def process_multimedia_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    generate_captions: bool,
    generate_audio_descriptions: bool,
    generate_spoken_descriptions: bool,
    detect_flashing: bool,
    generate_transcript: bool,
    whisper_model: str,
    user_id: str,
    department_id: str,
):
    """Background task to process multimedia file asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    from sqlalchemy.sql import func

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(
            f"[BACKGROUND] Processing Multimedia: {filename} (scan_id={scan_id})"
        )

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
                        f"[BACKGROUND] Multimedia Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update Multimedia progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Process multimedia with all options
        processor = MultimediaProcessor(
            whisper_model=f"whisper:{whisper_model}",
            progress_callback=update_progress,
        )
        result = processor.process_media(
            file_path,
            generate_captions=generate_captions,
            generate_audio_descriptions=generate_audio_descriptions,
            generate_spoken_descriptions=generate_spoken_descriptions,
            detect_flashing=detect_flashing,
            generate_transcript=generate_transcript,
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(
            f"[BACKGROUND] Multimedia processed in {processing_time}ms: {filename}"
        )

        # Build result structure
        structure = {
            "media_type": result.media_type,
            "duration": result.duration,
            "has_captions": result.has_captions,
            "transcription_count": (
                len(result.transcription) if result.transcription else 0
            ),
            "audio_descriptions_count": (
                len(result.audio_descriptions) if result.audio_descriptions else 0
            ),
        }

        if result.flashing_analysis:
            structure["flashing_analysis"] = {
                "has_flashing": result.flashing_analysis.has_flashing,
                "flash_count": result.flashing_analysis.flash_count,
                "severity": result.flashing_analysis.severity,
            }

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = 1  # Multimedia doesn't have pages
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()

        # Create ScanResult
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=sum(
                1 for i in result.issues if i.get("severity") == "critical"
            ),
            high_issues=sum(1 for i in result.issues if i.get("severity") == "high"),
            medium_issues=sum(
                1 for i in result.issues if i.get("severity") == "medium"
            ),
            low_issues=sum(1 for i in result.issues if i.get("severity") == "low"),
            issues=result.issues,
            structure=structure,
            ocr_used=False,
            ollama_used=generate_audio_descriptions,
            ollama_calls=(
                len(result.audio_descriptions) if result.audio_descriptions else 0
            ),
        )

        db.add(scan_result)
        db.commit()

        logger.info(f"[BACKGROUND] Multimedia Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing Multimedia {filename}: {str(e)}",
            exc_info=True,
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            scan.error_message = str(e)
            scan.progress_message = f"Processing failed: {str(e)}"
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/multimedia/scan", response_model=dict)
async def scan_multimedia(
    file: UploadFile = File(...),
    generate_captions: bool = True,
    generate_audio_descriptions: bool = True,
    generate_spoken_descriptions: bool = False,
    detect_flashing: bool = True,
    generate_transcript: bool = False,
    whisper_model: str = "base",
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Full accessibility scan for video/audio files

    AI-POWERED MULTIMEDIA ACCESSIBILITY
    REQUIRES API KEY IN PRODUCTION
    REQUIRES: video feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)
    - Transcription with speaker identification (Whisper)
    - AI audio descriptions for visual content (WCAG 1.2.3, 1.2.5)
    - Text-to-speech conversion for blind users (Piper TTS)
    - Flashing content detection for seizure safety (WCAG 2.3.1)
    - Full transcript generation (WCAG 1.2.8)

    Args:
        file: Video or audio file (MP4, MOV, AVI, MP3, WAV, etc.)
        generate_captions: Generate captions/transcription (default: true)
        generate_audio_descriptions: Generate AI descriptions of visual content (default: true)
        generate_spoken_descriptions: Convert descriptions to spoken audio via TTS (default: false)
        detect_flashing: Check for seizure-triggering content (default: true)
        generate_transcript: Generate combined text transcript (default: false)
        whisper_model: Whisper model size (base, small, medium, large)
    """
    _, user_id, department_id = api_key_info

    # Check feature access (tier-gated via TIER_QUOTAS)
    await require_feature(db, department_id, "video", "Video Accessibility Scanning")

    # Validate file type
    valid_extensions = [
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
    ]
    if not any(file.filename.lower().endswith(ext) for ext in valid_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File must be a video or audio file. Supported: {', '.join(valid_extensions)}",
        )

    # Security validation - verify file type for multimedia files
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_video:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_video / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanStatus

    scan = Scan(
        scan_type=ScanType.MULTIMEDIA,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting multimedia processing...",
    )
    db.add(scan)
    db.flush()

    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)
    scan.storage_path = storage_path

    from ...jobs.local_scan_job import enqueue_local_scan_job

    enqueue_local_scan_job(
        db,
        scan=scan,
        scan_kind="local_multimedia",
        options={
            "generate_captions": generate_captions,
            "generate_audio_descriptions": generate_audio_descriptions,
            "generate_spoken_descriptions": generate_spoken_descriptions,
            "detect_flashing": detect_flashing,
            "generate_transcript": generate_transcript,
            "whisper_model": whisper_model,
        },
        input_sha256=hashlib.sha256(content).hexdigest(),
    )

    db.commit()
    db.refresh(scan)

    logger.info(
        f"Created scan {scan.id} for Multimedia: {file.filename} "
        f"(captions={generate_captions}, audio_desc={generate_audio_descriptions})"
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "message": "Multimedia processing started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting multimedia processing...",
    }
