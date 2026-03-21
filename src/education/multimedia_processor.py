"""
Multimedia Accessibility Processor

This module provides functionality to:
1. Transcribe video/audio files using Whisper (via Ollama or OpenAI)
2. Generate WebVTT/SRT caption files with timestamps
3. Check multimedia WCAG 2.1 compliance
4. Extract audio from video files for processing
5. Generate AI-powered audio descriptions for visual content (WCAG 1.2.3, 1.2.5)
6. Detect flashing content that could cause seizures (WCAG 2.3.1)
7. Enhanced captions with speaker identification and sound effects
8. Full transcript generation combining audio and visual descriptions
"""

from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel
import subprocess
import tempfile
import os
import re
import logging
from datetime import timedelta

# Import speaker diarization module
from src.education.speaker_diarization import (
    SpeakerDiarizer,
    DiarizationResult,
)

logger = logging.getLogger(__name__)


class TranscriptionSegment(BaseModel):
    """Single segment of transcribed audio with timestamp"""

    start_time: float  # Seconds
    end_time: float  # Seconds
    text: str
    confidence: Optional[float] = None
    speaker_id: Optional[str] = (
        None  # Speaker identification (e.g., "Speaker 1", "John")
    )
    is_sound_effect: bool = False  # True if this is a non-speech sound description


class AudioDescription(BaseModel):
    """AI-generated description of visual content at a specific timestamp"""

    timestamp: float  # Seconds
    description: str  # Description of visual content
    scene_type: Optional[str] = (
        None  # "action", "setting", "character", "text_on_screen"
    )
    importance: str = "medium"  # "high", "medium", "low"
    audio_path: Optional[str] = None  # Path to spoken audio file (if TTS generated)


class FlashingContentResult(BaseModel):
    """Result of flashing content analysis for seizure safety (WCAG 2.3.1)"""

    has_flashing: bool
    flash_count: int
    max_flash_frequency: float  # Hz
    timestamps: List[float]  # Timestamps where flashing detected
    # WCAG 2.3.1 Red Flash Threshold - separate detection for saturated red
    red_flash_detected: bool = False  # True if saturated red flashing found
    red_flash_count: int = 0  # Number of red flash transitions
    red_flash_timestamps: List[float] = []  # Timestamps of red flash events
    red_saturation_peak: Optional[float] = None  # Maximum red saturation (0-1)
    severity: str  # "safe", "warning", "dangerous"
    recommendation: str


class MultimediaProcessingResult(BaseModel):
    """Result of multimedia processing operation"""

    file_path: str
    file_name: str
    media_type: str  # "video" or "audio"
    duration: float  # Seconds
    has_captions: bool
    transcription: Optional[List[TranscriptionSegment]] = None
    caption_formats: Optional[Dict[str, str]] = None  # Format -> content mapping
    audio_descriptions: Optional[List[AudioDescription]] = (
        None  # AI-generated visual descriptions
    )
    audio_descriptions_audio_path: Optional[str] = (
        None  # Path to combined spoken audio descriptions file
    )
    flashing_analysis: Optional[FlashingContentResult] = None  # Seizure safety check
    full_transcript: Optional[str] = (
        None  # Combined transcript with visual descriptions
    )
    remediated_video_path: Optional[str] = (
        None  # Path to fully accessible video with embedded subtitles + audio descriptions
    )
    compliance_score: float
    issues: List[Dict]


class MultimediaProcessor:
    """Process video/audio files for accessibility compliance"""

    def __init__(
        self,
        whisper_model: str = "whisper:base",
        use_gemini: bool = True,
        progress_callback: callable = None,
    ):
        """
        Initialize multimedia processor

        Args:
            whisper_model: Whisper model to use (base, small, medium, large)
            use_gemini: Whether to use Gemini for AI vision tasks (audio descriptions)
            progress_callback: Optional callback function(current, total, message) for progress updates
        """
        self.whisper_model = whisper_model
        self.use_gemini = use_gemini
        self.progress_callback = progress_callback
        self._llm_client = None
        self._image_generator = None  # For smart image analysis (charts/infographics)
        self._tts_processor = None  # For generating spoken audio descriptions

    def _get_llm_client(self):
        """Lazy-load LLM provider manager for vision/text tasks."""
        if self._llm_client is None:
            try:
                from src.ai.providers import get_provider_manager

                self._llm_client = get_provider_manager()
            except ImportError:
                logger.warning(
                    "LLM provider not available, audio descriptions disabled"
                )
                self._llm_client = False
        return self._llm_client if self._llm_client else None

    def _get_image_generator(self):
        """Lazy-load ImageAltTextGenerator for smart image analysis (charts/infographics)."""
        if self._image_generator is None:
            try:
                from .image_alt_text import ImageAltTextGenerator

                self._image_generator = ImageAltTextGenerator()
                logger.info(
                    "[MultimediaProcessor] ImageAltTextGenerator loaded for smart image analysis"
                )
            except ImportError as e:
                logger.warning(f"ImageAltTextGenerator not available: {e}")
                self._image_generator = False
        return self._image_generator if self._image_generator else None

    def _get_tts_processor(self):
        """Lazy-load TTS processor for generating spoken audio descriptions."""
        if self._tts_processor is None:
            try:
                from .tts_processor import get_tts_processor

                self._tts_processor = get_tts_processor()
                logger.info(
                    "[MultimediaProcessor] TTS processor loaded for spoken audio generation"
                )
            except ImportError as e:
                logger.warning(f"TTS processor not available: {e}")
                self._tts_processor = False
        return self._tts_processor if self._tts_processor else None

    def process_media(
        self,
        file_path: str,
        generate_captions: bool = True,
        generate_audio_descriptions: bool = False,
        generate_spoken_descriptions: bool = False,
        detect_flashing: bool = True,
        enhance_captions: bool = True,
        generate_transcript: bool = False,
    ) -> MultimediaProcessingResult:
        """
        Process a video or audio file for accessibility

        Args:
            file_path: Path to media file
            generate_captions: Whether to generate captions/transcription
            generate_audio_descriptions: Whether to generate AI audio descriptions (WCAG 1.2.3, 1.2.5)
            generate_spoken_descriptions: Whether to convert text descriptions to spoken audio (TTS)
            detect_flashing: Whether to check for seizure-triggering content (WCAG 2.3.1)
            enhance_captions: Whether to add speaker ID and sound effect descriptions
            generate_transcript: Whether to generate full text transcript (WCAG 1.2.8)

        Returns:
            MultimediaProcessingResult with transcription, audio descriptions, and compliance info
        """
        # Progress tracking - 6 main steps
        total_steps = 6

        def report_progress(step: int, message: str):
            if self.progress_callback:
                self.progress_callback(step, total_steps, message)

        report_progress(1, "Analyzing media file format...")

        # 1. Detect media type and duration
        media_type, duration = self._get_media_info(file_path)

        # 2. Check for existing captions (video only)
        has_captions = False
        if media_type == "video":
            has_captions = self._check_existing_captions(file_path)

        # 3. Generate transcription if requested
        transcription = None
        caption_formats = None
        audio_path = None

        if generate_captions:
            report_progress(2, "Transcribing audio with AI (this may take a minute)...")

            # Determine audio source
            # For audio files: use file directly
            # For video files: extract audio first (returns None if no audio stream)
            if media_type == "video":
                audio_path = self._extract_audio(file_path)
                if audio_path is None:
                    # Video has no audio stream - skip transcription
                    logger.info(
                        "[MultimediaProcessor] Video has no audio stream, skipping caption generation"
                    )
            else:
                # Audio file - use directly
                audio_path = file_path

            # Only attempt transcription if we have audio
            if audio_path is not None:
                try:
                    # Transcribe audio with enhanced features
                    transcription = self._transcribe_audio(
                        audio_path, enhance_captions=enhance_captions
                    )

                    report_progress(3, "Generating caption files (WebVTT, SRT)...")

                    # Generate caption files
                    if transcription:
                        caption_formats = {
                            "webvtt": self._generate_webvtt(transcription),
                            "srt": self._generate_srt(transcription),
                        }
                except Exception as e:
                    logger.error(f"[MultimediaProcessor] Transcription failed: {e}")

        # 4. Detect flashing content (video only) - WCAG 2.3.1
        flashing_analysis = None
        if detect_flashing and media_type == "video":
            report_progress(4, "Detecting seizure-triggering content (WCAG 2.3.1)...")
            try:
                flashing_analysis = self._detect_flashing_content(file_path, duration)
            except Exception as e:
                logger.error(f"[MultimediaProcessor] Flashing detection failed: {e}")

        # 5. Generate AI audio descriptions (video only) - WCAG 1.2.3, 1.2.5
        audio_descriptions = None
        if generate_audio_descriptions and media_type == "video":
            report_progress(5, "Generating AI audio descriptions for visual content...")
            try:
                audio_descriptions = self._generate_audio_descriptions(
                    file_path, duration
                )
            except Exception as e:
                logger.error(
                    f"[MultimediaProcessor] Audio description generation failed: {e}"
                )

        # 5.5 Generate spoken audio from descriptions (TTS) - For blind users
        audio_descriptions_audio_path = None
        if generate_spoken_descriptions and audio_descriptions:
            report_progress(5, "Converting descriptions to spoken audio (TTS)...")
            try:
                audio_descriptions_audio_path = (
                    self._generate_spoken_audio_descriptions(
                        audio_descriptions, file_path
                    )
                )
            except Exception as e:
                logger.error(
                    f"[MultimediaProcessor] Spoken audio generation failed: {e}"
                )

        # 5.6 Enhance captions with visual context from frame analysis
        # This is our differentiator: context-aware captions using AI vision
        if transcription and audio_descriptions and enhance_captions:
            report_progress(5, "Enhancing captions with visual context...")
            try:
                transcription = self._enhance_captions_with_visual_context(
                    transcription, audio_descriptions
                )
                # Regenerate caption formats with enhanced transcription
                caption_formats = {
                    "webvtt": self._generate_webvtt(transcription),
                    "srt": self._generate_srt(transcription),
                }
                logger.info(
                    "[MultimediaProcessor] Enhanced captions with visual context"
                )
            except Exception as e:
                logger.error(
                    f"[MultimediaProcessor] Caption context enhancement failed: {e}"
                )

        # 6. Generate full transcript - WCAG 1.2.8
        full_transcript = None
        if generate_transcript and transcription:
            report_progress(6, "Generating full text transcript...")
            full_transcript = self._generate_full_transcript(
                transcription, audio_descriptions
            )

        # 7. Clean up temp audio file
        if audio_path and media_type == "video" and audio_path != file_path:
            try:
                os.unlink(audio_path)
            except Exception:
                pass

        # 8. Create fully accessible video (with embedded subtitles + audio descriptions)
        remediated_video_path = None
        if media_type == "video":
            # Get subtitle file path (VTT or SRT)
            subtitle_path = None
            if caption_formats:
                # Save VTT temporarily for muxing
                base_name = os.path.splitext(file_path)[0]
                vtt_path = f"{base_name}_temp_captions.vtt"
                with open(vtt_path, "w", encoding="utf-8") as f:
                    f.write(caption_formats.get("webvtt", ""))
                subtitle_path = vtt_path if caption_formats.get("webvtt") else None

            # Create accessible video if we have any accessibility content
            if subtitle_path or audio_descriptions_audio_path:
                report_progress(6, "Creating accessible video with embedded tracks...")
                remediated_video_path = self.create_accessible_video(
                    video_path=file_path,
                    subtitle_path=subtitle_path,
                    audio_description_path=audio_descriptions_audio_path,
                )

                # Clean up temp subtitle file
                if subtitle_path and os.path.exists(subtitle_path):
                    try:
                        os.unlink(subtitle_path)
                    except Exception:
                        pass

        # 9. Check WCAG compliance (enhanced with new features)
        score, issues = self._check_compliance(
            media_type=media_type,
            duration=duration,
            has_captions=has_captions or (transcription is not None),
            transcription=transcription,
            audio_descriptions=audio_descriptions,
            flashing_analysis=flashing_analysis,
        )

        return MultimediaProcessingResult(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            media_type=media_type,
            duration=duration,
            has_captions=has_captions or (transcription is not None),
            transcription=transcription,
            caption_formats=caption_formats,
            audio_descriptions=audio_descriptions,
            audio_descriptions_audio_path=audio_descriptions_audio_path,
            flashing_analysis=flashing_analysis,
            full_transcript=full_transcript,
            remediated_video_path=remediated_video_path,
            compliance_score=score,
            issues=issues,
        )

    def _get_media_info(self, file_path: str) -> Tuple[str, float]:
        """
        Get media type and duration using ffprobe

        Returns:
            (media_type, duration_in_seconds)
        """
        try:
            # Use ffprobe to get media info
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1",
                file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            # Parse output
            output = result.stdout
            duration = 0.0
            has_video = "codec_type=video" in output

            # Extract duration
            duration_match = re.search(r"duration=([\d.]+)", output)
            if duration_match:
                duration = float(duration_match.group(1))

            # Determine media type
            media_type = "video" if has_video else "audio"

            return media_type, duration

        except Exception as e:
            print(f"[MultimediaProcessor] Failed to get media info: {e}")
            # Fallback: guess from extension
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
                return "video", 0.0
            else:
                return "audio", 0.0

    def _check_existing_captions(self, video_path: str) -> bool:
        """
        Check if video file has embedded captions/subtitles

        Args:
            video_path: Path to video file

        Returns:
            True if captions found, False otherwise
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1",
                video_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return "codec_type=subtitle" in result.stdout
        except Exception as e:
            print(f"[MultimediaProcessor] Failed to check captions: {e}")
            return False

    def _extract_audio(self, video_path: str) -> Optional[str]:
        """
        Extract audio from video file to temporary WAV file

        Args:
            video_path: Path to video file

        Returns:
            Path to extracted audio file, or None if video has no audio stream
        """
        try:
            # First check if video has an audio stream using ffprobe
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                video_path,
            ]
            probe_result = subprocess.run(
                probe_cmd, capture_output=True, text=True, timeout=30
            )

            if not probe_result.stdout.strip():
                logger.info(
                    f"[MultimediaProcessor] No audio stream in {video_path}, skipping extraction"
                )
                return None

            # Create temp file for audio
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                audio_path = tmp.name

            # Extract audio using ffmpeg
            cmd = [
                "ffmpeg",
                "-i",
                video_path,
                "-vn",  # No video
                "-acodec",
                "pcm_s16le",  # PCM 16-bit
                "-ar",
                "16000",  # 16kHz sample rate (Whisper default)
                "-ac",
                "1",  # Mono
                "-y",  # Overwrite
                audio_path,
            ]

            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
            logger.info(f"[MultimediaProcessor] Extracted audio to: {audio_path}")
            return audio_path

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Failed to extract audio: {e}")
            raise

    def _transcribe_audio(
        self, audio_path: str, enhance_captions: bool = True
    ) -> List[TranscriptionSegment]:
        """
        Transcribe audio using Whisper model with optional enhancement

        Args:
            audio_path: Path to audio file
            enhance_captions: Add speaker diarization and sound effect detection

        Returns:
            List of transcription segments with timestamps
        """
        try:
            # Use faster-whisper library for local transcription
            # This provides better timestamp accuracy than Ollama
            from faster_whisper import WhisperModel

            # Initialize model (downloads on first use)
            model_size = self.whisper_model.replace("whisper:", "")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

            # Transcribe with word timestamps for better accuracy
            segments_list, info = model.transcribe(
                audio_path,
                beam_size=5,
                word_timestamps=enhance_captions,  # Enable for speaker detection
            )

            # Convert to our format
            transcription = []
            current_speaker = None
            speaker_changes = []

            for segment in segments_list:
                text = segment.text.strip()

                # Detect speaker changes based on pauses and patterns
                speaker_id = None
                if enhance_captions:
                    speaker_id = self._detect_speaker(
                        text, segment.start, speaker_changes, current_speaker
                    )
                    current_speaker = speaker_id

                # Check if this is a sound effect (non-speech audio)
                is_sound_effect = False
                if enhance_captions:
                    is_sound_effect, text = self._detect_sound_effect(text, segment)

                transcription.append(
                    TranscriptionSegment(
                        start_time=segment.start,
                        end_time=segment.end,
                        text=text,
                        confidence=(
                            segment.confidence
                            if hasattr(segment, "confidence")
                            else None
                        ),
                        speaker_id=speaker_id,
                        is_sound_effect=is_sound_effect,
                    )
                )

            logger.info(
                f"[MultimediaProcessor] Transcribed {len(transcription)} segments"
            )
            return transcription

        except ImportError:
            logger.warning(
                "[MultimediaProcessor] faster-whisper not installed, falling back to basic method"
            )
            return self._transcribe_audio_fallback(audio_path)
        except Exception as e:
            logger.error(f"[MultimediaProcessor] Transcription failed: {e}")
            return []

    def _detect_speaker(
        self,
        text: str,
        timestamp: float,
        speaker_changes: List[Tuple[float, str]],
        current_speaker: Optional[str],
    ) -> str:
        """
        Detect speaker changes based on audio patterns

        Simple heuristic-based speaker detection:
        - Detects quoted dialogue
        - Tracks speaker changes based on pauses
        - Assigns speaker IDs

        Args:
            text: Transcribed text
            timestamp: Start timestamp
            speaker_changes: List of (timestamp, speaker_id) tuples
            current_speaker: Current speaker ID

        Returns:
            Speaker ID string
        """
        # Simple speaker detection heuristics
        # In production, use pyannote.audio for proper diarization

        # Check for dialogue patterns indicating speaker change
        dialogue_patterns = [
            r'^".*"$',  # Quoted text
            r"^[A-Z][a-z]+:",  # Name followed by colon
            r"^\[.*\]:",  # Bracketed speaker name
        ]

        for pattern in dialogue_patterns:
            if re.match(pattern, text):
                # Extract speaker name if present
                name_match = re.match(r"^([A-Z][a-z]+):", text)
                if name_match:
                    return name_match.group(1)
                bracket_match = re.match(r"^\[([^\]]+)\]:", text)
                if bracket_match:
                    return bracket_match.group(1)

        # Check for significant pause (>2 seconds) indicating speaker change
        if speaker_changes:
            last_timestamp, last_speaker = speaker_changes[-1]
            if timestamp - last_timestamp > 2.0:
                # Likely a speaker change
                speaker_num = 1
                if last_speaker and last_speaker.startswith("Speaker "):
                    try:
                        speaker_num = int(last_speaker.split()[-1]) % 3 + 1
                    except Exception:
                        pass
                new_speaker = f"Speaker {speaker_num}"
                speaker_changes.append((timestamp, new_speaker))
                return new_speaker

        # No speaker change detected
        if current_speaker:
            return current_speaker

        # First speaker
        speaker_changes.append((timestamp, "Speaker 1"))
        return "Speaker 1"

    def _transcribe_with_diarization(
        self,
        audio_path: str,
        use_ml_diarization: bool = True,
        enhance_captions: bool = True,
    ) -> Tuple[List[TranscriptionSegment], Optional[DiarizationResult]]:
        """
        Transcribe audio with ML-based speaker diarization.

        Uses pyannote.audio for accurate speaker identification when available,
        falling back to heuristic detection otherwise.

        Args:
            audio_path: Path to audio file
            use_ml_diarization: Whether to use ML-based diarization
            enhance_captions: Whether to enhance captions with speaker labels

        Returns:
            Tuple of (transcription segments, diarization result)
        """
        # Get base transcription
        segments = self._transcribe_audio(audio_path, enhance_captions=False)

        if not segments:
            return segments, None

        diarization_result = None

        if use_ml_diarization:
            try:
                # Initialize diarizer
                diarizer = SpeakerDiarizer(use_gpu=False)

                # Run diarization
                diarization_result = diarizer.diarize(audio_path)

                logger.info(
                    f"[MultimediaProcessor] Diarization complete: "
                    f"{diarization_result.num_speakers} speakers detected "
                    f"using {diarization_result.method_used}"
                )

                # Merge diarization with transcription
                if diarization_result.segments:
                    merged = diarizer.merge_with_transcription(
                        diarization_result,
                        [
                            {
                                "start": s.start_time,
                                "end": s.end_time,
                                "text": s.text,
                            }
                            for s in segments
                        ],
                    )

                    # Update segments with speaker info
                    for i, merged_seg in enumerate(merged):
                        if i < len(segments):
                            segments[i].speaker_id = merged_seg.get("speaker")

            except Exception as e:
                logger.warning(
                    f"[MultimediaProcessor] ML diarization failed, "
                    f"using heuristic fallback: {e}"
                )
                # Fall back to existing heuristic method
                speaker_changes: List[Tuple[float, str]] = []
                current_speaker = None

                for segment in segments:
                    speaker = self._detect_speaker(
                        segment.text,
                        segment.start_time,
                        speaker_changes,
                        current_speaker,
                    )
                    segment.speaker_id = speaker
                    current_speaker = speaker

        return segments, diarization_result

    def get_speaker_statistics(
        self, diarization: Optional[DiarizationResult]
    ) -> Dict[str, any]:
        """
        Get speaking time statistics for each speaker.

        Args:
            diarization: Diarization result from _transcribe_with_diarization

        Returns:
            Dictionary with speaker statistics
        """
        if not diarization:
            return {"error": "No diarization data available"}

        stats = {
            "num_speakers": diarization.num_speakers,
            "total_duration": diarization.total_duration,
            "method": diarization.method_used,
            "speakers": {},
        }

        for speaker_id, speaking_time in diarization.speaker_stats.items():
            percentage = (
                (speaking_time / diarization.total_duration * 100)
                if diarization.total_duration > 0
                else 0
            )
            stats["speakers"][speaker_id] = {
                "speaking_time": round(speaking_time, 2),
                "percentage": round(percentage, 1),
            }

        return stats

    def _detect_sound_effect(self, text: str, segment) -> Tuple[bool, str]:
        """
        Detect and describe non-speech audio (sound effects, music, ambient sounds)

        Args:
            text: Transcribed text
            segment: Whisper segment with audio info

        Returns:
            Tuple of (is_sound_effect, formatted_text)
        """
        # Common sound effect patterns in speech-to-text
        sound_patterns = {
            r"\[music\]": "[♪ Music playing ♪]",
            r"\[laughter\]": "[Laughter]",
            r"\[applause\]": "[Applause]",
            r"\[silence\]": "[Silence]",
            r"\[inaudible\]": "[Inaudible]",
            r"\[noise\]": "[Background noise]",
            r"\[cough\]": "[Coughing]",
            r"\[sigh\]": "[Sighing]",
        }

        # Check for existing sound markers
        text_lower = text.lower()
        for pattern, replacement in sound_patterns.items():
            if re.search(pattern, text_lower):
                return True, replacement

        # Detect music/ambient based on segment properties
        # In production, use audio analysis for proper detection
        if hasattr(segment, "no_speech_prob") and segment.no_speech_prob > 0.7:
            return True, "[Non-speech audio]"

        # Check for very short text that might be non-speech
        if len(text) < 3 and not text.isalpha():
            return True, "[Sound]"

        return False, text

    def _enhance_captions_with_visual_context(
        self,
        transcription: List[TranscriptionSegment],
        audio_descriptions: List[AudioDescription],
    ) -> List[TranscriptionSegment]:
        """
        Enhance captions with visual context from frame analysis.

        This is Aelira's differentiator: context-aware captions that use AI vision
        to provide richer accessibility for deaf and hard-of-hearing users.

        Enhancements:
        1. Speaker identification from visuals (e.g., "Woman in blue blazer")
        2. Scene context for sound effects (e.g., "[Applause as speaker finishes]")
        3. Visual cues in captions (e.g., "[Music plays over title slide]")

        Args:
            transcription: List of transcription segments
            audio_descriptions: List of audio descriptions from frame analysis

        Returns:
            Enhanced transcription segments
        """
        if not audio_descriptions or not transcription:
            return transcription

        # Build a timeline of visual context from audio descriptions
        # Each description has a timestamp and scene description
        visual_context_timeline = []
        for desc in audio_descriptions:
            visual_context_timeline.append({
                "timestamp": desc.timestamp,
                "description": desc.description,
                "scene_type": desc.scene_type,
            })

        # Sort by timestamp for efficient lookup
        visual_context_timeline.sort(key=lambda x: x["timestamp"])

        # Extract speaker appearances from frame descriptions
        speaker_appearances = self._extract_speaker_info_from_descriptions(audio_descriptions)

        # Enhance each caption segment
        enhanced_segments = []
        for segment in transcription:
            # Find relevant visual context for this segment
            context = self._find_visual_context_for_timestamp(
                segment.start_time, visual_context_timeline
            )

            # 1. Enhance speaker labels with visual identification
            enhanced_speaker_id = self._enhance_speaker_with_visual_context(
                segment.speaker_id, segment.start_time, speaker_appearances
            )

            # 2. Enhance sound effects with scene context
            enhanced_text = segment.text
            if segment.is_sound_effect and context:
                enhanced_text = self._enhance_sound_effect_with_context(
                    segment.text, context
                )

            # 3. Add visual cue prefixes for non-speech segments (music over slides, etc.)
            if segment.is_sound_effect and context:
                visual_cue = self._extract_visual_cue(context)
                if visual_cue:
                    enhanced_text = f"[{visual_cue}] {enhanced_text}".replace("[[", "[").replace("]]", "]")

            enhanced_segments.append(
                TranscriptionSegment(
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    text=enhanced_text,
                    confidence=segment.confidence,
                    speaker_id=enhanced_speaker_id,
                    is_sound_effect=segment.is_sound_effect,
                )
            )

        return enhanced_segments

    def _extract_speaker_info_from_descriptions(
        self, audio_descriptions: List[AudioDescription]
    ) -> List[Dict]:
        """
        Extract speaker appearance information from frame descriptions.

        Looks for patterns like "man in blue shirt", "woman at podium",
        "Dr. Smith speaking", etc.

        Returns list of {timestamp, description, speaker_visual} dicts
        """
        speaker_info = []
        speaker_patterns = [
            # Person descriptions
            r"(man|woman|person|speaker|presenter|instructor|professor|student)\s+(in|wearing|with)\s+([^,.]+)",
            r"(man|woman|person)\s+(at|behind|near)\s+(podium|desk|screen|whiteboard)",
            # Named speakers
            r"(Dr\.?|Prof\.?|Mr\.?|Ms\.?|Mrs\.?)\s+(\w+)\s+(?:is\s+)?speaking",
            # Action-based identification
            r"(male|female)\s+(speaker|presenter|instructor|lecturer)",
        ]

        for desc in audio_descriptions:
            for pattern in speaker_patterns:
                match = re.search(pattern, desc.description, re.IGNORECASE)
                if match:
                    speaker_info.append({
                        "timestamp": desc.timestamp,
                        "description": desc.description,
                        "speaker_visual": match.group(0).strip(),
                    })
                    break

        return speaker_info

    def _find_visual_context_for_timestamp(
        self, timestamp: float, timeline: List[Dict]
    ) -> Optional[Dict]:
        """Find the closest visual context for a given timestamp."""
        if not timeline:
            return None

        # Find the most recent context before or at this timestamp
        closest = None
        for ctx in timeline:
            if ctx["timestamp"] <= timestamp + 2.0:  # 2s lookahead window
                closest = ctx
            else:
                break

        return closest

    def _enhance_speaker_with_visual_context(
        self,
        current_speaker_id: Optional[str],
        timestamp: float,
        speaker_appearances: List[Dict],
    ) -> Optional[str]:
        """
        Enhance speaker label with visual description.

        Transforms "Speaker 1" into "Woman at podium" or "Dr. Smith"
        """
        if not speaker_appearances:
            return current_speaker_id

        # Find the closest speaker appearance to this timestamp
        closest_appearance = None
        min_distance = float("inf")

        for appearance in speaker_appearances:
            distance = abs(appearance["timestamp"] - timestamp)
            if distance < min_distance and distance < 10.0:  # Within 10 seconds
                min_distance = distance
                closest_appearance = appearance

        if closest_appearance:
            visual_id = closest_appearance["speaker_visual"]
            # Clean up the visual ID for caption display
            visual_id = visual_id.strip().capitalize()
            # Limit length for readability
            if len(visual_id) > 40:
                visual_id = visual_id[:37] + "..."
            return visual_id

        return current_speaker_id

    def _enhance_sound_effect_with_context(
        self, sound_effect_text: str, context: Dict
    ) -> str:
        """
        Enhance sound effect descriptions with scene context.

        Transforms "[Applause]" into "[Applause as speaker finishes presentation]"
        """
        scene_desc = context.get("description", "")
        scene_type = context.get("scene_type", "")

        # Extract key action from scene description
        action_patterns = [
            r"(speaker|presenter)\s+(finishes|concludes|ends|begins|starts)",
            r"(audience|crowd)\s+(reacts|responds|laughs|applauds)",
            r"(video|presentation|slide)\s+(shows|displays|changes)",
            r"(transition|cut)\s+to\s+([^,.]+)",
        ]

        for pattern in action_patterns:
            match = re.search(pattern, scene_desc, re.IGNORECASE)
            if match:
                action_context = match.group(0).strip().lower()
                # Add context to sound effect
                if sound_effect_text.startswith("[") and sound_effect_text.endswith("]"):
                    base_effect = sound_effect_text[1:-1]
                    return f"[{base_effect} as {action_context}]"
                return f"{sound_effect_text} ({action_context})"

        return sound_effect_text

    def _extract_visual_cue(self, context: Dict) -> Optional[str]:
        """
        Extract visual cue for captions (e.g., "Title slide: Introduction to Python").
        """
        scene_desc = context.get("description", "")
        scene_type = context.get("scene_type", "")

        # Look for on-screen text, title slides, or significant visuals
        visual_cue_patterns = [
            (r"title\s+slide[:\s]+(.+?)(?:\.|$)", "Title slide"),
            (r"slide\s+(?:shows?|displays?)[:\s]+(.+?)(?:\.|$)", "On screen"),
            (r"text\s+(?:on\s+screen|appears?)[:\s]+(.+?)(?:\.|$)", "On screen"),
            (r"(?:shows?|displays?)\s+(?:the\s+)?text[:\s]+[\"']?(.+?)[\"']?(?:\.|$)", "Text"),
        ]

        for pattern, prefix in visual_cue_patterns:
            match = re.search(pattern, scene_desc, re.IGNORECASE)
            if match:
                visual_text = match.group(1).strip()
                if len(visual_text) > 50:
                    visual_text = visual_text[:47] + "..."
                return f"{prefix}: {visual_text}"

        return None

    def _transcribe_audio_fallback(self, audio_path: str) -> List[TranscriptionSegment]:
        """
        Fallback transcription method using whisper CLI

        Args:
            audio_path: Path to audio file

        Returns:
            List of transcription segments
        """
        try:
            # Use whisper CLI if available
            cmd = ["whisper", audio_path, "--model", "base", "--output_format", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

            if result.returncode == 0:
                # Parse JSON output (simplified - actual parsing would be more complex)
                import json

                output_file = audio_path.replace(".wav", ".json")
                if os.path.exists(output_file):
                    with open(output_file, "r") as f:
                        data = json.load(f)
                    segments = []
                    for seg in data.get("segments", []):
                        segments.append(
                            TranscriptionSegment(
                                start_time=seg["start"],
                                end_time=seg["end"],
                                text=seg["text"].strip(),
                            )
                        )
                    return segments

            return []
        except Exception as e:
            print(f"[MultimediaProcessor] Fallback transcription failed: {e}")
            return []

    def _generate_webvtt(self, segments: List[TranscriptionSegment]) -> str:
        """
        Generate WebVTT caption file content with speaker identification

        Args:
            segments: List of transcription segments

        Returns:
            WebVTT file content as string
        """
        vtt = "WEBVTT\n\n"

        current_speaker = None

        for i, segment in enumerate(segments, start=1):
            start = self._format_timestamp_vtt(segment.start_time)
            end = self._format_timestamp_vtt(segment.end_time)

            # Format text with speaker identification
            text = segment.text
            if segment.speaker_id and segment.speaker_id != current_speaker:
                # WebVTT supports <v speaker> voice spans
                text = f"<v {segment.speaker_id}>{segment.text}"
                current_speaker = segment.speaker_id
            elif segment.is_sound_effect:
                # Style sound effects differently
                text = f"<c.sound>{segment.text}</c>"

            vtt += f"{i}\n{start} --> {end}\n{text}\n\n"

        return vtt

    def _generate_srt(self, segments: List[TranscriptionSegment]) -> str:
        """
        Generate SRT caption file content with speaker identification

        Args:
            segments: List of transcription segments

        Returns:
            SRT file content as string
        """
        srt = ""

        current_speaker = None

        for i, segment in enumerate(segments, start=1):
            start = self._format_timestamp_srt(segment.start_time)
            end = self._format_timestamp_srt(segment.end_time)

            # Format text with speaker identification
            text = segment.text
            if segment.speaker_id and segment.speaker_id != current_speaker:
                # SRT uses simple text prefix for speaker
                text = f"[{segment.speaker_id}]: {segment.text}"
                current_speaker = segment.speaker_id

            srt += f"{i}\n{start} --> {end}\n{text}\n\n"

        return srt

    def _format_timestamp_vtt(self, seconds: float) -> str:
        """Format timestamp for WebVTT (HH:MM:SS.mmm)"""
        td = timedelta(seconds=seconds)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    def _format_timestamp_srt(self, seconds: float) -> str:
        """Format timestamp for SRT (HH:MM:SS,mmm)"""
        vtt_timestamp = self._format_timestamp_vtt(seconds)
        return vtt_timestamp.replace(".", ",")

    def _check_compliance(
        self,
        media_type: str,
        duration: float,
        has_captions: bool,
        transcription: Optional[List[TranscriptionSegment]],
        audio_descriptions: Optional[List[AudioDescription]] = None,
        flashing_analysis: Optional[FlashingContentResult] = None,
    ) -> Tuple[float, List[Dict]]:
        """
        Check WCAG 2.1/2.2 compliance for multimedia

        Returns:
            (score, issues)
        """
        issues = []

        # WCAG 1.2.1: Audio-only and Video-only (Prerecorded) - Level A
        # WCAG 1.2.2: Captions (Prerecorded) - Level A
        if not has_captions:
            issues.append(
                {
                    "severity": "critical",
                    "rule": "WCAG 1.2.2",
                    "message": "Missing captions/transcription for multimedia content",
                    "impact": "Deaf and hard-of-hearing users cannot access audio content",
                }
            )

        # WCAG 1.2.3: Audio Description or Media Alternative (Prerecorded) - Level A
        # Only report if we didn't generate audio descriptions
        if media_type == "video" and not audio_descriptions:
            issues.append(
                {
                    "severity": "high",
                    "rule": "WCAG 1.2.3",
                    "message": "Audio description not provided for visual content",
                    "impact": "Blind and visually impaired users cannot access visual information",
                    "recommendation": "Enable audio description generation or add manually",
                }
            )

        # WCAG 2.3.1: Three Flashes or Below Threshold - Level A (CRITICAL for seizure safety)
        if flashing_analysis:
            if flashing_analysis.severity == "dangerous":
                issues.append(
                    {
                        "severity": "critical",
                        "rule": "WCAG 2.3.1",
                        "message": f"Dangerous flashing content detected ({flashing_analysis.max_flash_frequency:.1f} Hz)",
                        "impact": "Can trigger seizures in people with photosensitive epilepsy",
                        "recommendation": flashing_analysis.recommendation,
                        "timestamps": flashing_analysis.timestamps[
                            :5
                        ],  # First 5 timestamps
                    }
                )
            elif flashing_analysis.severity == "warning":
                issues.append(
                    {
                        "severity": "high",
                        "rule": "WCAG 2.3.1",
                        "message": f"Potentially problematic flashing detected ({flashing_analysis.flash_count} flashes)",
                        "impact": "May cause discomfort or trigger seizures in sensitive individuals",
                        "recommendation": flashing_analysis.recommendation,
                    }
                )

        # Check caption quality if we have transcription
        if transcription:
            for segment in transcription:
                # Check for very short segments (might be errors)
                if segment.end_time - segment.start_time < 0.5:
                    issues.append(
                        {
                            "severity": "low",
                            "rule": "Caption Quality",
                            "message": f'Very short caption segment: "{segment.text[:30]}"',
                            "impact": "May be difficult to read or inaccurate",
                        }
                    )

        # Calculate score
        critical_count = len([i for i in issues if i["severity"] == "critical"])
        high_count = len([i for i in issues if i["severity"] == "high"])
        medium_count = len([i for i in issues if i["severity"] == "medium"])
        low_count = len([i for i in issues if i["severity"] == "low"])

        score = (
            100
            - (critical_count * 20)
            - (high_count * 10)
            - (medium_count * 5)
            - (low_count * 2)
        )
        return max(0.0, float(score)), issues

    # =========================================================================
    # NEW FEATURE: Flashing Content Detection (WCAG 2.3.1)
    # =========================================================================

    def _detect_flashing_content(
        self, video_path: str, duration: float
    ) -> FlashingContentResult:
        """
        Detect flashing content that could trigger seizures (WCAG 2.3.1)

        WCAG 2.3.1 requires that content does not contain anything that flashes
        more than 3 times per second, OR the flashing is below the general flash
        and red flash thresholds.

        This method checks BOTH:
        1. General flash threshold (luminance changes)
        2. Red flash threshold (saturated red transitions) - MORE RESTRICTIVE

        Args:
            video_path: Path to video file
            duration: Video duration in seconds

        Returns:
            FlashingContentResult with analysis
        """
        try:
            # Extract frames for analysis (sample every 0.1 seconds for first 30 seconds)
            sample_duration = min(duration, 30.0)  # Analyze first 30 seconds
            frame_interval = 0.1  # 10 fps sampling

            # Extract brightness values from frames
            brightness_values = self._extract_frame_brightness(
                video_path, sample_duration, frame_interval
            )

            if not brightness_values:
                return FlashingContentResult(
                    has_flashing=False,
                    flash_count=0,
                    max_flash_frequency=0.0,
                    timestamps=[],
                    red_flash_detected=False,
                    red_flash_count=0,
                    red_flash_timestamps=[],
                    red_saturation_peak=None,
                    severity="safe",
                    recommendation="Unable to analyze video frames",
                )

            # Detect general flashes (significant brightness changes)
            flashes, timestamps = self._analyze_brightness_changes(
                brightness_values, frame_interval
            )

            # Calculate maximum flash frequency
            max_frequency = self._calculate_max_flash_frequency(
                timestamps, window_size=1.0  # 1-second sliding window
            )

            # WCAG 2.3.1 Red Flash Threshold Detection
            # Saturated red flashing is particularly dangerous and has stricter limits
            red_detected, red_count, red_timestamps, red_saturation = (
                self._detect_red_flashes(video_path, sample_duration, frame_interval)
            )

            # Calculate red flash frequency if detected
            red_flash_frequency = 0.0
            if red_timestamps:
                red_flash_frequency = self._calculate_max_flash_frequency(
                    red_timestamps, window_size=1.0
                )

            # Determine severity based on both general and red flash thresholds
            severity = "safe"
            recommendation = (
                "No accessibility concerns detected for photosensitive users."
            )

            # Red flash is MORE dangerous - check first
            if red_detected and red_flash_frequency > 3.0:
                severity = "dangerous"
                recommendation = (
                    "CRITICAL: This video contains RED FLASHING content exceeding 3 Hz. "
                    "Saturated red flashing is particularly dangerous for photosensitive users. "
                    "IMMEDIATELY add a prominent seizure warning, desaturate the red content, "
                    "or provide an alternative version without red flashing."
                )
            elif red_detected and red_count > 5:
                # Even moderate red flashing is concerning
                if severity != "dangerous":
                    severity = "dangerous" if red_flash_frequency > 2.0 else "warning"
                recommendation = (
                    "WARNING: This video contains saturated red flashing content. "
                    "Red flashing poses elevated seizure risk. Consider adding a "
                    "photosensitivity warning and desaturating red elements."
                )
            elif max_frequency > 3.0:
                severity = "dangerous"
                recommendation = (
                    "CRITICAL: This video contains flashing content exceeding 3 Hz, "
                    "which can trigger seizures. Add a seizure warning, reduce flash frequency, "
                    "or provide an alternative version."
                )
            elif flashes > 10 or max_frequency > 2.0:
                severity = "warning"
                recommendation = (
                    "This video contains frequent brightness changes. Consider adding "
                    "a photosensitivity warning or reducing contrast in flashing sections."
                )

            return FlashingContentResult(
                has_flashing=flashes > 0,
                flash_count=flashes,
                max_flash_frequency=max_frequency,
                timestamps=timestamps,
                red_flash_detected=red_detected,
                red_flash_count=red_count,
                red_flash_timestamps=red_timestamps,
                red_saturation_peak=red_saturation,
                severity=severity,
                recommendation=recommendation,
            )

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Flashing detection failed: {e}")
            return FlashingContentResult(
                has_flashing=False,
                flash_count=0,
                max_flash_frequency=0.0,
                timestamps=[],
                red_flash_detected=False,
                red_flash_count=0,
                red_flash_timestamps=[],
                red_saturation_peak=None,
                severity="safe",
                recommendation=f"Flashing analysis failed: {e}",
            )

    def _extract_frame_brightness(
        self, video_path: str, duration: float, interval: float
    ) -> List[Tuple[float, float]]:
        """
        Extract brightness values from video frames

        Args:
            video_path: Path to video file
            duration: Duration to analyze
            interval: Time between samples

        Returns:
            List of (timestamp, brightness) tuples
        """
        try:
            # Use ffmpeg to extract frames and calculate brightness
            # This is more efficient than extracting actual images

            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=pkt_pts_time,pict_type",
                "-of",
                "csv=p=0",
                "-read_intervals",
                f"%+{duration}",
                video_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                # Fallback: use simpler frame extraction
                return self._extract_frame_brightness_simple(
                    video_path, duration, interval
                )

            # Parse frame data
            brightness_values = []
            lines = result.stdout.strip().split("\n")

            for line in lines:
                parts = line.split(",")
                if len(parts) >= 1 and parts[0]:
                    try:
                        timestamp = float(parts[0])
                        if timestamp <= duration:
                            # Estimate brightness from frame type (simplified)
                            # I-frames typically have different characteristics
                            frame_type = parts[1] if len(parts) > 1 else "P"
                            brightness = 0.5 + (0.1 if frame_type == "I" else 0)
                            brightness_values.append((timestamp, brightness))
                    except (ValueError, IndexError):
                        continue

            return brightness_values

        except Exception as e:
            logger.warning(f"[MultimediaProcessor] Frame extraction failed: {e}")
            return []

    def _extract_frame_brightness_simple(
        self, video_path: str, duration: float, interval: float
    ) -> List[Tuple[float, float]]:
        """
        Simple brightness extraction using ffmpeg thumbnail filter

        Creates temporary thumbnails and analyzes their brightness.
        """
        try:

            # Use ffmpeg to get frame statistics
            cmd = [
                "ffmpeg",
                "-i",
                video_path,
                "-vf",
                f"fps=1/{interval},showinfo",
                "-f",
                "null",
                "-",
                "-t",
                str(duration),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # Parse showinfo output for frame data
            brightness_values = []
            timestamp = 0.0

            for line in result.stderr.split("\n"):
                if "showinfo" in line and "n:" in line:
                    # Extract frame number
                    match = re.search(r"n:\s*(\d+)", line)
                    if match:
                        frame_num = int(match.group(1))
                        timestamp = frame_num * interval

                        # Estimate brightness from frame stats if available
                        # Default to 0.5 (mid-brightness)
                        brightness = 0.5
                        brightness_values.append((timestamp, brightness))

            return brightness_values

        except Exception as e:
            logger.warning(f"[MultimediaProcessor] Simple extraction failed: {e}")
            return []

    def _analyze_brightness_changes(
        self, brightness_values: List[Tuple[float, float]], interval: float
    ) -> Tuple[int, List[float]]:
        """
        Analyze brightness changes to detect flashes

        A flash is defined as a rapid change in relative luminance
        (increase then decrease or vice versa).

        Args:
            brightness_values: List of (timestamp, brightness) tuples
            interval: Time between samples

        Returns:
            Tuple of (flash_count, flash_timestamps)
        """
        if len(brightness_values) < 3:
            return 0, []

        flashes = 0
        timestamps = []
        threshold = 0.1  # 10% brightness change threshold

        for i in range(1, len(brightness_values) - 1):
            prev_time, prev_bright = brightness_values[i - 1]
            curr_time, curr_bright = brightness_values[i]
            next_time, next_bright = brightness_values[i + 1]

            # Check for flash pattern: up-down or down-up
            diff1 = curr_bright - prev_bright
            diff2 = next_bright - curr_bright

            # Flash detected if direction changes significantly
            if abs(diff1) > threshold and abs(diff2) > threshold:
                if (diff1 > 0 and diff2 < 0) or (diff1 < 0 and diff2 > 0):
                    flashes += 1
                    timestamps.append(curr_time)

        return flashes, timestamps

    def _calculate_max_flash_frequency(
        self, timestamps: List[float], window_size: float = 1.0
    ) -> float:
        """
        Calculate maximum flash frequency using sliding window

        Args:
            timestamps: List of flash timestamps
            window_size: Window size in seconds

        Returns:
            Maximum flashes per second (Hz)
        """
        if len(timestamps) < 2:
            return 0.0

        max_frequency = 0.0

        for i, start_time in enumerate(timestamps):
            # Count flashes within window
            count = 0
            for j in range(i, len(timestamps)):
                if timestamps[j] - start_time <= window_size:
                    count += 1
                else:
                    break

            frequency = count / window_size
            max_frequency = max(max_frequency, frequency)

        return max_frequency

    def _detect_red_flashes(
        self, video_path: str, duration: float, interval: float = 0.1
    ) -> Tuple[bool, int, List[float], Optional[float]]:
        """
        Detect saturated red flashing per WCAG 2.3.1 Red Flash Threshold.

        A "red flash" is a pair of opposing transitions involving a saturated red.
        WCAG defines saturated red as: R/(R+G+B) >= 0.8 when R > 128 (on 0-255 scale).

        This is a separate and more restrictive threshold than general flashing
        because saturated red is particularly likely to trigger photosensitive seizures.

        Args:
            video_path: Path to video file
            duration: Duration to analyze (seconds)
            interval: Time between frame samples (seconds)

        Returns:
            Tuple of (red_flash_detected, red_flash_count, timestamps, max_saturation)
        """
        try:
            import tempfile
            import os

            # Create temp directory for frame extraction
            with tempfile.TemporaryDirectory() as temp_dir:
                # Extract frames using ffmpeg
                sample_duration = min(duration, 30.0)  # First 30 seconds
                output_pattern = os.path.join(temp_dir, "frame_%04d.png")

                cmd = [
                    "ffmpeg",
                    "-i",
                    video_path,
                    "-t",
                    str(sample_duration),
                    "-vf",
                    f"fps=1/{interval}",
                    "-f",
                    "image2",
                    output_pattern,
                    "-y",  # Overwrite
                    "-loglevel",
                    "error",
                ]

                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    logger.warning(
                        f"[MultimediaProcessor] Frame extraction failed: {result.stderr.decode()}"
                    )
                    return False, 0, [], None

                # Analyze frames for red saturation
                frame_files = sorted(
                    [f for f in os.listdir(temp_dir) if f.startswith("frame_")]
                )

                if len(frame_files) < 3:
                    return False, 0, [], None

                red_flash_count = 0
                red_flash_timestamps = []
                max_saturation = 0.0
                prev_is_saturated_red = False

                for i, frame_file in enumerate(frame_files):
                    frame_path = os.path.join(temp_dir, frame_file)
                    timestamp = i * interval

                    # Calculate red saturation for this frame
                    is_saturated, saturation = self._check_frame_red_saturation(
                        frame_path
                    )
                    max_saturation = max(max_saturation, saturation)

                    # Detect transition to/from saturated red
                    if i > 0:
                        if is_saturated != prev_is_saturated_red:
                            # Transition detected - check if this forms a flash
                            # A red flash requires two opposing transitions
                            if is_saturated:
                                # Transition TO saturated red - mark for potential flash
                                pass
                            else:
                                # Transition FROM saturated red - complete flash
                                red_flash_count += 1
                                red_flash_timestamps.append(timestamp)

                    prev_is_saturated_red = is_saturated

                red_flash_detected = red_flash_count > 0
                return (
                    red_flash_detected,
                    red_flash_count,
                    red_flash_timestamps,
                    max_saturation if max_saturation > 0 else None,
                )

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Red flash detection failed: {e}")
            return False, 0, [], None

    def _check_frame_red_saturation(self, frame_path: str) -> Tuple[bool, float]:
        """
        Check if a frame contains significant saturated red content.

        Per WCAG 2.3.1, saturated red is defined as:
        - R/(R+G+B) >= 0.8 (red dominates the color)
        - R > 128 (on 0-255 scale, meaning reasonably bright red)

        We check if more than 10% of the frame area meets this criteria.

        Args:
            frame_path: Path to frame image file

        Returns:
            Tuple of (is_saturated_red, average_red_saturation)
        """
        try:
            from PIL import Image
            import numpy as np

            with Image.open(frame_path) as img:
                # Convert to RGB if necessary
                if img.mode != "RGB":
                    img = img.convert("RGB")

                # Convert to numpy array
                pixels = np.array(img, dtype=np.float32)

                # Extract RGB channels
                r = pixels[:, :, 0]
                g = pixels[:, :, 1]
                b = pixels[:, :, 2]

                # Calculate red saturation: R / (R + G + B)
                # Add small epsilon to avoid division by zero
                total = r + g + b + 1e-6
                red_saturation = r / total

                # Check WCAG criteria: R/(R+G+B) >= 0.8 AND R > 128
                saturated_red_mask = (red_saturation >= 0.8) & (r > 128)

                # Calculate percentage of pixels that are saturated red
                saturated_red_percentage = (
                    np.sum(saturated_red_mask) / saturated_red_mask.size
                )

                # Calculate average red saturation for the entire frame
                avg_saturation = float(np.mean(red_saturation))

                # Consider frame as having saturated red if >10% of pixels qualify
                is_saturated_red = saturated_red_percentage > 0.10

                return is_saturated_red, avg_saturation

        except ImportError:
            logger.warning(
                "[MultimediaProcessor] PIL/numpy not available for red flash detection"
            )
            return False, 0.0
        except Exception as e:
            logger.warning(f"[MultimediaProcessor] Frame analysis failed: {e}")
            return False, 0.0

    # =========================================================================
    # NEW FEATURE: AI Audio Descriptions (WCAG 1.2.3, 1.2.5)
    # =========================================================================

    def _generate_audio_descriptions(
        self, video_path: str, duration: float
    ) -> List[AudioDescription]:
        """
        Generate AI-powered audio descriptions for visual content

        Extracts keyframes from video and uses Gemini vision to describe
        visual elements that are important for understanding content.

        Args:
            video_path: Path to video file
            duration: Video duration in seconds

        Returns:
            List of AudioDescription objects
        """
        llm_client = self._get_llm_client()
        if not llm_client:
            logger.warning(
                "[MultimediaProcessor] LLM provider not available for audio descriptions"
            )
            return []

        try:
            # Extract keyframes at regular intervals (every 5 seconds)
            keyframe_interval = 5.0
            keyframes = self._extract_keyframes(video_path, duration, keyframe_interval)

            if not keyframes:
                logger.warning("[MultimediaProcessor] No keyframes extracted")
                return []

            descriptions = []

            for timestamp, frame_path in keyframes:
                try:
                    # Generate description using LLM vision
                    description = self._describe_keyframe(
                        llm_client, frame_path, timestamp
                    )
                    if description:
                        descriptions.append(description)
                finally:
                    # Clean up temp frame
                    try:
                        os.unlink(frame_path)
                    except Exception:
                        pass

            logger.info(
                f"[MultimediaProcessor] Generated {len(descriptions)} audio descriptions"
            )
            return descriptions

        except Exception as e:
            logger.error(
                f"[MultimediaProcessor] Audio description generation failed: {e}"
            )
            return []

    def _extract_keyframes(
        self, video_path: str, duration: float, interval: float
    ) -> List[Tuple[float, str]]:
        """
        Extract keyframes using scene-change detection for comprehensive coverage.

        Strategy:
        1. Use ffmpeg scene detection to find actual visual changes
           (speaker changes, slide transitions, camera cuts, etc.)
        2. Also sample at regular intervals as backup (catch gradual changes)
        3. Merge nearby timestamps to avoid redundant frames

        This ensures blind users get descriptions of ALL meaningful visual
        changes, not just arbitrary time-based samples.

        Args:
            video_path: Path to video file
            duration: Video duration in seconds
            interval: Minimum interval between frames (to avoid duplicates)

        Returns:
            List of (timestamp, frame_path) tuples
        """
        keyframes = []

        try:
            # PHASE 1: Scene-change detection using ffmpeg
            # This finds frames where visual content actually changes
            # (slide transitions, speaker changes, camera cuts, etc.)
            scene_timestamps = self._detect_scene_changes(video_path, duration)
            logger.info(
                f"[MultimediaProcessor] Scene detection found {len(scene_timestamps)} visual changes"
            )

            # PHASE 2: Add regular interval samples as backup
            # This catches gradual changes that scene detection might miss
            # Sample every 5 seconds for comprehensive coverage
            BACKUP_INTERVAL = 5.0
            backup_timestamps = []
            t = 0.0
            while t < duration:
                backup_timestamps.append(t)
                t += BACKUP_INTERVAL

            # PHASE 3: Merge scene changes + backup samples
            # Remove duplicates within 2 seconds of each other
            MIN_GAP = 2.0
            all_timestamps = sorted(set(scene_timestamps + backup_timestamps))

            merged_timestamps = []
            for ts in all_timestamps:
                if not merged_timestamps or (ts - merged_timestamps[-1]) >= MIN_GAP:
                    merged_timestamps.append(ts)

            # Always include near the end
            if merged_timestamps and (duration - merged_timestamps[-1]) > MIN_GAP:
                merged_timestamps.append(max(0, duration - 1.0))

            # Cap at reasonable maximum to manage API costs
            # 200 frames = ~$0.05 with Gemini Flash, acceptable for demo
            MAX_FRAMES = 200
            if len(merged_timestamps) > MAX_FRAMES:
                # Keep evenly distributed subset
                step = len(merged_timestamps) / MAX_FRAMES
                merged_timestamps = [
                    merged_timestamps[int(i * step)]
                    for i in range(MAX_FRAMES)
                ]

            timestamps = merged_timestamps
            logger.info(
                f"[MultimediaProcessor] Video {duration:.1f}s: extracting {len(timestamps)} keyframes "
                f"(scene detection + {BACKUP_INTERVAL}s backup sampling)"
            )

            for timestamp in timestamps:
                # Create temp file for frame
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                    frame_path = tmp.name

                # Extract frame using ffmpeg
                cmd = [
                    "ffmpeg",
                    "-ss",
                    str(timestamp),
                    "-i",
                    video_path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    frame_path,
                ]

                result = subprocess.run(cmd, capture_output=True, timeout=30)

                if result.returncode == 0 and os.path.exists(frame_path):
                    keyframes.append((timestamp, frame_path))
                else:
                    # Clean up failed frame
                    try:
                        os.unlink(frame_path)
                    except Exception:
                        pass

            return keyframes

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Keyframe extraction failed: {e}")
            return []

    def _detect_scene_changes_pyscenedetect(
        self, video_path: str
    ) -> List[float]:
        """
        Detect scene changes using PySceneDetect library.

        PySceneDetect provides more accurate scene detection than ffmpeg's
        simple threshold-based approach, especially for:
        - Gradual transitions (fades, dissolves)
        - Content-aware detection
        - Adaptive thresholding

        Args:
            video_path: Path to video file

        Returns:
            List of timestamps where scene changes occur
        """
        try:
            from scenedetect import detect, ContentDetector

            # ContentDetector with threshold 30.0 is good for presentations
            # Lower = more sensitive (more scenes detected)
            scene_list = detect(video_path, ContentDetector(threshold=30.0))

            timestamps = [0.0]  # Always include start
            for scene in scene_list:
                start_time = scene[0].get_seconds()
                if start_time > 0:
                    timestamps.append(start_time)

            logger.debug(
                f"[MultimediaProcessor] PySceneDetect found {len(timestamps)} scene changes"
            )
            return timestamps

        except ImportError:
            logger.debug(
                "[MultimediaProcessor] PySceneDetect not installed, using ffmpeg fallback"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[MultimediaProcessor] PySceneDetect failed: {e}, using ffmpeg fallback"
            )
            return None

    def _detect_scene_changes(
        self, video_path: str, duration: float
    ) -> List[float]:
        """
        Detect scene changes in video using PySceneDetect (preferred) or ffmpeg.

        This finds frames where significant visual changes occur:
        - Slide transitions in presentations
        - Speaker/camera changes
        - Scene cuts
        - Major visual transitions

        Args:
            video_path: Path to video file
            duration: Video duration in seconds

        Returns:
            List of timestamps where scene changes occur
        """
        # Try PySceneDetect first (more accurate)
        timestamps = self._detect_scene_changes_pyscenedetect(video_path)
        if timestamps is not None:
            return timestamps

        # Fall back to ffmpeg-based detection
        try:
            # Use ffmpeg to detect scene changes
            # scene threshold 0.3 = 30% pixel change (good for presentations)
            # Lower values = more sensitive, higher = less sensitive
            cmd = [
                "ffmpeg",
                "-i", video_path,
                "-vf", "select='gt(scene,0.3)',showinfo",
                "-f", "null",
                "-"
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=min(300, duration * 2)  # Timeout scales with video length
            )

            # Parse timestamps from ffmpeg output
            # Format: "pts_time:12.345" in showinfo output
            timestamps = [0.0]  # Always include start
            import re
            for line in result.stderr.split('\n'):
                if 'pts_time:' in line:
                    match = re.search(r'pts_time:(\d+\.?\d*)', line)
                    if match:
                        ts = float(match.group(1))
                        if ts < duration:
                            timestamps.append(ts)

            logger.debug(
                f"[MultimediaProcessor] ffmpeg scene detection found {len(timestamps)} changes"
            )
            return timestamps

        except subprocess.TimeoutExpired:
            logger.warning(
                f"[MultimediaProcessor] Scene detection timed out, using fallback"
            )
            return [0.0]  # Just return start, backup sampling will cover rest
        except Exception as e:
            logger.warning(
                f"[MultimediaProcessor] Scene detection failed: {e}, using fallback"
            )
            return [0.0]

    def _describe_keyframe(
        self, llm_client, frame_path: str, timestamp: float
    ) -> Optional[AudioDescription]:
        """
        Use smart image analysis to describe a video frame for audio description.

        SMART IMAGE ANALYSIS (Cross-Scanner Integration):
        1. First detect if keyframe contains a chart/graph/infographic
        2. If chart detected: use describe_chart_or_graph() for detailed data description
        3. Otherwise: use standard scene description

        Args:
            llm_client: ProviderManager instance
            frame_path: Path to frame image
            timestamp: Timestamp of the frame

        Returns:
            AudioDescription or None
        """
        import asyncio

        try:
            # Try smart image analysis first (if available)
            image_generator = self._get_image_generator()

            if image_generator:
                # PHASE 1: Detect if this is a chart/graph/infographic
                try:
                    type_result = asyncio.run(
                        image_generator.detect_image_type(
                            image_path=frame_path,
                            context=f"Video keyframe at {timestamp:.1f} seconds",
                        )
                    )

                    if type_result.get("success"):
                        image_type = type_result.get("image_type", "informative")
                        image_purpose = type_result.get("image_purpose", "")

                        # Check if it's a chart/graph/infographic
                        is_chart = image_type == "complex" or any(
                            term in image_purpose.lower()
                            for term in [
                                "chart",
                                "graph",
                                "plot",
                                "diagram",
                                "infographic",
                                "data",
                                "visualization",
                                "statistics",
                            ]
                        )

                        if is_chart:
                            # PHASE 2: Use specialized chart description
                            chart_result = asyncio.run(
                                image_generator.describe_chart_or_graph(
                                    image_path=frame_path,
                                    context=f"Video keyframe at {timestamp:.1f} seconds - describing data visualization",
                                    detail_level="standard",
                                )
                            )

                            if chart_result.get("success"):
                                # Use detailed description for audio description (more context needed)
                                detailed_desc = chart_result.get(
                                    "detailed_description", ""
                                )
                                short_desc = chart_result.get("short_description", "")
                                chart_type = chart_result.get("chart_type", "chart")

                                # Combine for comprehensive audio description
                                description_text = detailed_desc or short_desc
                                if description_text:
                                    logger.info(
                                        f"[MultimediaProcessor] Frame {timestamp:.1f}s: Generated CHART description ({chart_type})"
                                    )

                                    return AudioDescription(
                                        timestamp=timestamp,
                                        description=description_text,
                                        scene_type="text_on_screen",  # Charts are typically visual data
                                        importance="high",  # Charts usually convey important information
                                    )

                        elif type_result.get("is_decorative"):
                            # Skip decorative frames (background, transition, etc.)
                            logger.info(
                                f"[MultimediaProcessor] Frame {timestamp:.1f}s: Skipping decorative frame"
                            )
                            return None

                except Exception as e:
                    logger.warning(
                        f"[MultimediaProcessor] Smart image analysis failed, falling back: {e}"
                    )

            # FALLBACK: Standard scene description using LLM provider
            # Read the image
            with open(frame_path, "rb") as f:
                image_data = f.read()

            # Create prompt for audio description
            prompt = """Analyze this video frame and provide an audio description for blind users.

Focus on:
1. Main actions happening in the scene
2. Important visual information (text on screen, expressions, gestures)
3. Scene setting if it changed
4. Any visual information essential to understanding the content

Provide a concise description (1-2 sentences) suitable for reading aloud between dialogue.

Format: Just the description text, no labels or explanations."""

            # Call LLM provider with vision
            result = llm_client.analyze_image_sync(
                image_data=image_data,
                prompt=prompt,
                max_tokens=200,
            )

            if result.get("success"):
                description_text = result.get("content", "").strip()

                if description_text:
                    # Classify scene type
                    scene_type = self._classify_scene_type(description_text)

                    return AudioDescription(
                        timestamp=timestamp,
                        description=description_text,
                        scene_type=scene_type,
                        importance="medium",
                    )

            return None

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Keyframe description failed: {e}")
            return None

    def _classify_scene_type(self, description: str) -> str:
        """
        Classify the type of scene from description

        Args:
            description: Generated description text

        Returns:
            Scene type string
        """
        description_lower = description.lower()

        if any(
            word in description_lower
            for word in [
                "walks",
                "runs",
                "moves",
                "picks up",
                "puts down",
                "opens",
                "closes",
            ]
        ):
            return "action"
        elif any(
            word in description_lower
            for word in ["room", "building", "outdoor", "inside", "outside", "location"]
        ):
            return "setting"
        elif any(
            word in description_lower
            for word in ["person", "man", "woman", "child", "face", "expression"]
        ):
            return "character"
        elif any(
            word in description_lower
            for word in ["text", "title", "caption", "screen", "display", "shows"]
        ):
            return "text_on_screen"
        else:
            return "general"

    # =========================================================================
    # NEW FEATURE: Full Transcript Generation (WCAG 1.2.8)
    # =========================================================================

    def _generate_full_transcript(
        self,
        transcription: List[TranscriptionSegment],
        audio_descriptions: Optional[List[AudioDescription]] = None,
    ) -> str:
        """
        Generate a full text transcript combining audio and visual information

        WCAG 1.2.8 requires a text alternative for prerecorded synchronized media
        that includes all audio information and visual descriptions.

        Args:
            transcription: List of transcription segments
            audio_descriptions: Optional list of audio descriptions

        Returns:
            Full transcript as formatted text
        """
        # Combine transcription and audio descriptions, sorted by timestamp
        all_items = []

        # Add transcription segments
        for segment in transcription:
            all_items.append(
                {
                    "timestamp": segment.start_time,
                    "type": "audio",
                    "speaker": segment.speaker_id,
                    "text": segment.text,
                    "is_sound": segment.is_sound_effect,
                }
            )

        # Add audio descriptions
        if audio_descriptions:
            for desc in audio_descriptions:
                all_items.append(
                    {
                        "timestamp": desc.timestamp,
                        "type": "visual",
                        "text": desc.description,
                        "scene_type": desc.scene_type,
                    }
                )

        # Sort by timestamp
        all_items.sort(key=lambda x: x["timestamp"])

        # Generate formatted transcript
        lines = [
            "=" * 60,
            "FULL TRANSCRIPT",
            "Generated by Aelira Accessibility Platform",
            "Includes: Audio transcription + Visual descriptions",
            "=" * 60,
            "",
        ]

        current_speaker = None

        for item in all_items:
            timestamp_str = self._format_timestamp_readable(item["timestamp"])

            if item["type"] == "audio":
                if item.get("is_sound"):
                    lines.append(f"[{timestamp_str}] {item['text']}")
                else:
                    speaker = item.get("speaker", "Unknown")
                    if speaker != current_speaker:
                        lines.append(f"\n[{timestamp_str}] {speaker}:")
                        current_speaker = speaker
                    lines.append(f"  {item['text']}")
            else:
                # Visual description
                lines.append(f"\n[{timestamp_str}] [VISUAL: {item['text']}]")

        lines.extend(["", "=" * 60, "END OF TRANSCRIPT", "=" * 60])

        return "\n".join(lines)

    def _format_timestamp_readable(self, seconds: float) -> str:
        """
        Format timestamp in readable format (MM:SS or HH:MM:SS)

        Args:
            seconds: Time in seconds

        Returns:
            Formatted timestamp string
        """
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    # =========================================================================
    # NEW FEATURE: Spoken Audio Descriptions (TTS for WCAG 1.2.3, 1.2.5)
    # =========================================================================

    def _generate_spoken_audio_descriptions(
        self,
        audio_descriptions: List[AudioDescription],
        video_path: str,
    ) -> Optional[str]:
        """
        Convert text audio descriptions to spoken audio using TTS.

        For blind users who benefit from hearing visual descriptions
        rather than reading them.

        Args:
            audio_descriptions: List of AudioDescription objects with text
            video_path: Path to the original video file (for output naming)

        Returns:
            Path to the generated audio file, or None if failed
        """
        tts_processor = self._get_tts_processor()
        if not tts_processor:
            logger.warning(
                "[MultimediaProcessor] TTS processor not available for spoken descriptions"
            )
            return None

        if not audio_descriptions:
            return None

        try:
            # Prepare sections for document audio generation
            sections = []
            for desc in audio_descriptions:
                timestamp_str = self._format_timestamp_readable(desc.timestamp)
                sections.append(
                    {
                        "title": f"At {timestamp_str}",
                        "content": desc.description,
                    }
                )

            # Generate output path next to the video
            video_dir = os.path.dirname(video_path)
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(
                video_dir, f"{video_name}_audio_descriptions.mp3"
            )

            # Generate spoken audio using TTS processor
            result = tts_processor.generate_document_audio(
                sections=sections,
                output_path=output_path,
            )

            if result.success:
                logger.info(
                    f"[MultimediaProcessor] Generated spoken audio descriptions: {output_path}"
                )

                # Update individual descriptions with audio paths if separate files generated
                if result.audio_path:
                    return result.audio_path
                return output_path
            else:
                logger.error(
                    f"[MultimediaProcessor] TTS generation failed: {result.error}"
                )
                return None

        except Exception as e:
            logger.error(f"[MultimediaProcessor] Spoken audio generation failed: {e}")
            return None

    def generate_spoken_description_for_text(
        self,
        text: str,
        output_path: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate spoken audio for arbitrary text.

        Useful for generating audio versions of:
        - Document summaries
        - Alt text descriptions
        - Remediation explanations

        Args:
            text: Text to convert to speech
            output_path: Optional path for output audio file
            voice: Optional voice to use

        Returns:
            Path to the generated audio file, or None if failed
        """
        tts_processor = self._get_tts_processor()
        if not tts_processor:
            logger.warning("[MultimediaProcessor] TTS processor not available")
            return None

        try:
            result = tts_processor.synthesize(
                text=text,
                voice=voice,
            )

            if result.success:
                # Save to file if path specified
                if output_path and result.audio_data:
                    with open(output_path, "wb") as f:
                        f.write(result.audio_data)
                    return output_path
                elif result.audio_path:
                    return result.audio_path

                # Create temp file if no path specified
                if result.audio_data:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=f".{result.format}"
                    ) as tmp:
                        tmp.write(result.audio_data)
                        return tmp.name

            return None

        except Exception as e:
            logger.error(f"[MultimediaProcessor] TTS synthesis failed: {e}")
            return None

    def create_accessible_video(
        self,
        video_path: str,
        subtitle_path: Optional[str] = None,
        audio_description_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a fully accessible video with embedded subtitle and audio description tracks.

        This creates a proper accessible video file where:
        - Subtitles/captions are embedded as a soft subtitle track (not burned in)
        - Audio descriptions are added as a separate audio track

        Users can toggle subtitles on/off and switch audio tracks in their player.

        Args:
            video_path: Path to original video file
            subtitle_path: Path to VTT or SRT subtitle file
            audio_description_path: Path to audio description MP3/WAV file
            output_path: Optional output path (auto-generated if not provided)

        Returns:
            Path to the accessible video file, or None if failed
        """
        if not subtitle_path and not audio_description_path:
            logger.warning("[MultimediaProcessor] No accessibility content to embed")
            return None

        try:
            # Determine output format
            # MKV is best for multiple tracks, but MP4 is more compatible
            # Use MKV if we have audio descriptions (multiple audio tracks)
            # Use MP4 if we only have subtitles
            input_ext = os.path.splitext(video_path)[1].lower()
            if audio_description_path:
                output_ext = ".mkv"  # MKV supports multiple audio tracks better
            else:
                output_ext = input_ext if input_ext in [".mp4", ".mkv", ".webm"] else ".mp4"

            # Generate output path
            if not output_path:
                base_name = os.path.splitext(video_path)[0]
                output_path = f"{base_name}_accessible{output_ext}"

            # Build ffmpeg command
            cmd = ["ffmpeg", "-y"]  # -y to overwrite

            # Input files
            cmd.extend(["-i", video_path])

            input_index = 1  # Track input file indices

            if subtitle_path and os.path.exists(subtitle_path):
                cmd.extend(["-i", subtitle_path])
                subtitle_input = input_index
                input_index += 1
            else:
                subtitle_input = None

            if audio_description_path and os.path.exists(audio_description_path):
                cmd.extend(["-i", audio_description_path])
                audio_desc_input = input_index
                input_index += 1
            else:
                audio_desc_input = None

            # Map streams
            cmd.extend(["-map", "0:v"])  # Video from original
            cmd.extend(["-map", "0:a?"])  # Original audio (if present)

            if audio_desc_input is not None:
                cmd.extend(["-map", f"{audio_desc_input}:a"])  # Audio descriptions as second audio track

            if subtitle_input is not None:
                cmd.extend(["-map", f"{subtitle_input}:s"])  # Subtitles

            # Codec settings
            cmd.extend(["-c:v", "copy"])  # Don't re-encode video
            cmd.extend(["-c:a", "aac"])  # AAC audio for compatibility

            if subtitle_input is not None:
                # Subtitle codec depends on output format
                if output_ext == ".mkv":
                    cmd.extend(["-c:s", "srt"])  # SRT for MKV
                else:
                    cmd.extend(["-c:s", "mov_text"])  # MOV text for MP4

            # Metadata for tracks
            if audio_desc_input is not None:
                # Label the audio tracks
                cmd.extend(["-metadata:s:a:0", "title=Original Audio"])
                cmd.extend(["-metadata:s:a:0", "language=eng"])
                cmd.extend(["-metadata:s:a:1", "title=Audio Descriptions"])
                cmd.extend(["-metadata:s:a:1", "language=eng"])

            if subtitle_input is not None:
                # Label subtitle track
                cmd.extend(["-metadata:s:s:0", "title=Captions"])
                cmd.extend(["-metadata:s:s:0", "language=eng"])

            # Output file
            cmd.append(output_path)

            logger.info(f"[MultimediaProcessor] Creating accessible video: {' '.join(cmd)}")

            # Run ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes max
            )

            if result.returncode != 0:
                logger.error(f"[MultimediaProcessor] ffmpeg failed: {result.stderr}")
                return None

            if os.path.exists(output_path):
                logger.info(f"[MultimediaProcessor] Created accessible video: {output_path}")
                return output_path
            else:
                logger.error("[MultimediaProcessor] Output file not created")
                return None

        except subprocess.TimeoutExpired:
            logger.error("[MultimediaProcessor] Video muxing timed out")
            return None
        except Exception as e:
            logger.error(f"[MultimediaProcessor] Failed to create accessible video: {e}")
            return None
