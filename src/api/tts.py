"""
Text-to-Speech API Endpoints

Provides audio generation for accessibility:
- Convert text to speech (MP3/WAV)
- Generate audio versions of content
- Support multiple TTS providers

WCAG References:
- WCAG 1.2.1: Audio-only and Video-only (Prerecorded)
- WCAG 1.2.3: Audio Description or Media Alternative
"""

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from src.education.tts_processor import (
    TTSProvider,
    AudioFormat,
    get_tts_processor,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tts", tags=["Text-to-Speech"])


class SynthesizeRequest(BaseModel):
    """Request to synthesize speech from text."""

    text: str = Field(
        ..., min_length=1, max_length=5000, description="Text to convert to speech"
    )
    voice: Optional[str] = Field(
        None, description="Voice identifier (provider-specific)"
    )
    provider: Optional[str] = Field(
        None, description="TTS provider: piper, coqui, openai"
    )
    format: Optional[str] = Field(
        "mp3", description="Output format: mp3, wav, ogg, flac"
    )
    speed: Optional[float] = Field(
        1.0, ge=0.5, le=2.0, description="Speech speed (0.5-2.0)"
    )


class SynthesizeResponse(BaseModel):
    """Response from synthesis (metadata only, audio returned separately)."""

    success: bool
    provider: str
    voice: str
    format: str
    duration_seconds: float
    size_bytes: int
    error: Optional[str] = None


class BatchSynthesizeRequest(BaseModel):
    """Request to synthesize multiple texts."""

    texts: List[str] = Field(
        ..., min_items=1, max_items=50, description="List of texts to convert"
    )
    voice: Optional[str] = None
    provider: Optional[str] = None
    format: Optional[str] = "mp3"


class DocumentAudioRequest(BaseModel):
    """Request to generate audio for a document."""

    sections: List[dict] = Field(
        ...,
        description="List of {title: str, content: str} sections",
    )
    voice: Optional[str] = None
    provider: Optional[str] = None


class VoiceInfo(BaseModel):
    """Information about a TTS voice."""

    id: str
    name: str
    gender: Optional[str] = None
    quality: Optional[str] = None
    recommended: Optional[bool] = None


class ProviderStatus(BaseModel):
    """Status of a TTS provider."""

    available: bool
    local: bool
    name: str


class HealthResponse(BaseModel):
    """Health check response."""

    providers: dict
    active_provider: Optional[str]


@router.post("/synthesize", response_class=Response)
async def synthesize_speech(request: SynthesizeRequest):
    """
    Convert text to speech audio.

    Returns audio file directly (MP3 by default).

    Example:
        POST /api/tts/synthesize
        {"text": "Welcome to Aelira accessibility platform"}

    Returns: Audio file (MP3/WAV/OGG/FLAC)
    """
    processor = get_tts_processor()

    # Parse provider
    provider = None
    if request.provider:
        try:
            provider = TTSProvider(request.provider.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider. Options: {[p.value for p in TTSProvider]}",
            )

    # Parse format
    try:
        output_format = (
            AudioFormat(request.format.lower()) if request.format else AudioFormat.MP3
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format. Options: {[f.value for f in AudioFormat]}",
        )

    # Synthesize
    result = processor.synthesize(
        text=request.text,
        voice=request.voice,
        output_format=output_format,
        speed=request.speed or 1.0,
        provider=provider,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    # Return audio file
    content_types = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
    }

    return Response(
        content=result.audio_data,
        media_type=content_types.get(result.format, "audio/mpeg"),
        headers={
            "Content-Disposition": f"attachment; filename=speech.{result.format}",
            "X-TTS-Provider": result.provider,
            "X-TTS-Voice": result.voice,
            "X-TTS-Duration": str(result.duration_seconds),
        },
    )


@router.post("/synthesize/metadata", response_model=SynthesizeResponse)
async def synthesize_speech_metadata(request: SynthesizeRequest):
    """
    Synthesize speech and return metadata (without audio).

    Useful for checking synthesis result before downloading.
    Use /synthesize endpoint to get actual audio.
    """
    processor = get_tts_processor()

    provider = None
    if request.provider:
        try:
            provider = TTSProvider(request.provider.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid provider")

    output_format = (
        AudioFormat(request.format.lower()) if request.format else AudioFormat.MP3
    )

    result = processor.synthesize(
        text=request.text,
        voice=request.voice,
        output_format=output_format,
        speed=request.speed or 1.0,
        provider=provider,
    )

    return SynthesizeResponse(
        success=result.success,
        provider=result.provider,
        voice=result.voice,
        format=result.format,
        duration_seconds=result.duration_seconds,
        size_bytes=len(result.audio_data) if result.audio_data else 0,
        error=result.error,
    )


@router.post("/batch")
async def batch_synthesize(request: BatchSynthesizeRequest):
    """
    Synthesize multiple texts to speech.

    Returns list of base64-encoded audio files.
    For large batches, consider using individual /synthesize calls.
    """
    import base64

    processor = get_tts_processor()

    provider = None
    if request.provider:
        try:
            provider = TTSProvider(request.provider.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid provider")

    output_format = (
        AudioFormat(request.format.lower()) if request.format else AudioFormat.MP3
    )

    results = []
    for text in request.texts:
        result = processor.synthesize(
            text=text,
            voice=request.voice,
            output_format=output_format,
            provider=provider,
        )

        results.append(
            {
                "success": result.success,
                "audio_base64": (
                    base64.b64encode(result.audio_data).decode()
                    if result.audio_data
                    else None
                ),
                "format": result.format,
                "duration_seconds": result.duration_seconds,
                "error": result.error,
            }
        )

    return {
        "success": all(r["success"] for r in results),
        "results": results,
        "total": len(results),
    }


@router.get("/voices")
async def list_voices(provider: Optional[str] = None):
    """
    List available voices for TTS.

    Args:
        provider: Optional provider filter (piper, coqui, openai)

    Returns list of available voices with metadata.
    """
    processor = get_tts_processor()

    provider_enum = None
    if provider:
        try:
            provider_enum = TTSProvider(provider.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider. Options: {[p.value for p in TTSProvider]}",
            )

    voices = processor.get_available_voices(provider_enum)

    return {
        "provider": provider or "auto",
        "voices": voices,
    }


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Check TTS provider availability.

    Returns status of all configured TTS providers.
    """
    processor = get_tts_processor()
    return processor.health_check()


@router.get("/providers")
async def list_providers():
    """
    List all TTS providers with their status.
    """
    processor = get_tts_processor()
    health = processor.health_check()

    providers = []
    for name, status in health["providers"].items():
        providers.append(
            {
                "id": name,
                "name": name.title(),
                "available": status["available"],
                "local": status["local"],
                "description": _get_provider_description(name),
            }
        )

    return {
        "providers": providers,
        "active": health["active_provider"],
    }


def _get_provider_description(provider: str) -> str:
    """Get description for a TTS provider."""
    descriptions = {
        "piper": "Fast local TTS using VITS neural models (10-50x real-time on CPU)",
        "coqui": "High-quality local TTS with XTTS v2 (GPU recommended)",
        "openai": "Cloud-based TTS with excellent quality (requires API key)",
    }
    return descriptions.get(provider, "TTS provider")
