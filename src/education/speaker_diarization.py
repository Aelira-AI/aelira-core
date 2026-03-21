"""
Speaker Diarization Module

Provides ML-based speaker diarization using pyannote.audio when available,
with graceful fallback to heuristic-based detection.

pyannote.audio Dependencies:
- pyannote.audio>=3.1 (pip install pyannote.audio)
- HuggingFace token with access to pyannote/speaker-diarization-3.1
- Set HUGGINGFACE_TOKEN environment variable

Fallback:
When pyannote.audio is not available, uses silence-based heuristics
to estimate speaker changes.
"""

from typing import List, Optional, Dict, Any, Tuple
import os
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Try to import pyannote.audio
try:
    from pyannote.audio import Pipeline
    import torch

    HAS_PYANNOTE = True
except ImportError:
    HAS_PYANNOTE = False
    Pipeline = None
    torch = None


class SpeakerSegment(BaseModel):
    """A segment of audio attributed to a specific speaker."""

    start: float  # Start time in seconds
    end: float  # End time in seconds
    speaker_id: str  # Speaker identifier (e.g., "SPEAKER_00", "SPEAKER_01")
    confidence: Optional[float] = None  # Confidence score if available


class DiarizationResult(BaseModel):
    """Result of speaker diarization analysis."""

    segments: List[SpeakerSegment]
    num_speakers: int
    total_duration: float
    method_used: str  # "pyannote" or "heuristic"
    speaker_stats: Dict[str, float]  # Speaker ID -> total speaking time


class SpeakerDiarizer:
    """
    Speaker diarization using pyannote.audio with heuristic fallback.

    Usage:
        diarizer = SpeakerDiarizer()
        result = diarizer.diarize("audio.wav")
        for segment in result.segments:
            print(f"{segment.speaker_id}: {segment.start:.2f}s - {segment.end:.2f}s")
    """

    def __init__(
        self,
        use_gpu: bool = False,
        huggingface_token: Optional[str] = None,
        min_speakers: int = 1,
        max_speakers: int = 10,
    ):
        """
        Initialize the speaker diarizer.

        Args:
            use_gpu: Use GPU acceleration if available
            huggingface_token: HuggingFace token for pyannote model access
            min_speakers: Minimum expected number of speakers
            max_speakers: Maximum expected number of speakers
        """
        self.use_gpu = use_gpu
        self.huggingface_token = huggingface_token or os.getenv("HUGGINGFACE_TOKEN")
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.pipeline = None
        self._initialized = False

    def _init_pyannote(self) -> bool:
        """
        Initialize pyannote.audio pipeline.

        Returns:
            True if successfully initialized, False otherwise
        """
        if not HAS_PYANNOTE:
            logger.info(
                "[SpeakerDiarizer] pyannote.audio not installed, using heuristic fallback"
            )
            return False

        if not self.huggingface_token:
            logger.warning(
                "[SpeakerDiarizer] No HuggingFace token provided, using heuristic fallback"
            )
            return False

        try:
            self.pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.huggingface_token,
            )

            if self.use_gpu and torch.cuda.is_available():
                self.pipeline.to(torch.device("cuda"))
                logger.info("[SpeakerDiarizer] Using GPU for diarization")
            else:
                logger.info("[SpeakerDiarizer] Using CPU for diarization")

            self._initialized = True
            return True

        except Exception as e:
            logger.warning(f"[SpeakerDiarizer] Failed to initialize pyannote: {e}")
            return False

    def diarize(self, audio_path: str) -> DiarizationResult:
        """
        Perform speaker diarization on audio file.

        Args:
            audio_path: Path to audio file (WAV, MP3, etc.)

        Returns:
            DiarizationResult with speaker segments and statistics
        """
        # Try pyannote first
        if not self._initialized:
            self._init_pyannote()

        if self.pipeline is not None:
            return self._diarize_with_pyannote(audio_path)
        else:
            return self._diarize_with_heuristics(audio_path)

    def _diarize_with_pyannote(self, audio_path: str) -> DiarizationResult:
        """
        Use pyannote.audio for ML-based diarization.

        Args:
            audio_path: Path to audio file

        Returns:
            DiarizationResult from pyannote analysis
        """
        try:
            # Run diarization with speaker count hints
            diarization = self.pipeline(
                audio_path,
                min_speakers=self.min_speakers,
                max_speakers=self.max_speakers,
            )

            segments = []
            speaker_times: Dict[str, float] = {}
            total_duration = 0.0

            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segment = SpeakerSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker_id=speaker,
                )
                segments.append(segment)

                # Track speaker times
                duration = turn.end - turn.start
                speaker_times[speaker] = speaker_times.get(speaker, 0.0) + duration
                total_duration = max(total_duration, turn.end)

            return DiarizationResult(
                segments=segments,
                num_speakers=len(speaker_times),
                total_duration=total_duration,
                method_used="pyannote",
                speaker_stats=speaker_times,
            )

        except Exception as e:
            logger.error(f"[SpeakerDiarizer] pyannote diarization failed: {e}")
            # Fall back to heuristics
            return self._diarize_with_heuristics(audio_path)

    def _diarize_with_heuristics(self, audio_path: str) -> DiarizationResult:
        """
        Use silence-based heuristics for speaker change detection.

        This is a fallback when pyannote is not available. It detects
        pauses in audio and estimates speaker changes at significant pauses.

        Args:
            audio_path: Path to audio file

        Returns:
            DiarizationResult with estimated speaker segments
        """
        segments = []
        total_duration = 0.0

        try:
            # Try to get audio duration using ffprobe
            import subprocess

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                total_duration = float(result.stdout.strip())
        except Exception as e:
            logger.warning(f"[SpeakerDiarizer] Failed to get duration: {e}")
            total_duration = 0.0

        # Simple heuristic: detect silence gaps for speaker changes
        try:
            silence_gaps = self._detect_silence_gaps(audio_path)
        except Exception as e:
            logger.warning(f"[SpeakerDiarizer] Silence detection failed: {e}")
            silence_gaps = []

        # Create segments between silences
        current_start = 0.0
        current_speaker = "SPEAKER_00"
        speaker_times: Dict[str, float] = {}

        for gap_start, gap_end in silence_gaps:
            if gap_start > current_start:
                # Add segment before this gap
                segment = SpeakerSegment(
                    start=current_start,
                    end=gap_start,
                    speaker_id=current_speaker,
                )
                segments.append(segment)

                duration = gap_start - current_start
                speaker_times[current_speaker] = (
                    speaker_times.get(current_speaker, 0.0) + duration
                )

                # Alternate speaker after significant pauses (>1.5 seconds)
                if gap_end - gap_start > 1.5:
                    if current_speaker == "SPEAKER_00":
                        current_speaker = "SPEAKER_01"
                    else:
                        current_speaker = "SPEAKER_00"

            current_start = gap_end

        # Add final segment
        if total_duration > current_start:
            segment = SpeakerSegment(
                start=current_start,
                end=total_duration,
                speaker_id=current_speaker,
            )
            segments.append(segment)
            duration = total_duration - current_start
            speaker_times[current_speaker] = (
                speaker_times.get(current_speaker, 0.0) + duration
            )

        # If no segments detected, create single segment
        if not segments and total_duration > 0:
            segments.append(
                SpeakerSegment(
                    start=0.0,
                    end=total_duration,
                    speaker_id="SPEAKER_00",
                )
            )
            speaker_times["SPEAKER_00"] = total_duration

        return DiarizationResult(
            segments=segments,
            num_speakers=len(speaker_times),
            total_duration=total_duration,
            method_used="heuristic",
            speaker_stats=speaker_times,
        )

    def _detect_silence_gaps(self, audio_path: str) -> List[Tuple[float, float]]:
        """
        Detect silence gaps in audio using ffmpeg.

        Args:
            audio_path: Path to audio file

        Returns:
            List of (start, end) tuples for silence periods
        """
        import subprocess

        gaps = []

        try:
            # Use ffmpeg silencedetect filter
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    audio_path,
                    "-af",
                    "silencedetect=n=-30dB:d=0.5",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Parse silence detect output from stderr
            silence_start = None
            for line in result.stderr.split("\n"):
                if "silence_start" in line:
                    try:
                        # Extract timestamp after "silence_start: "
                        parts = line.split("silence_start:")
                        if len(parts) > 1:
                            silence_start = float(parts[1].strip().split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "silence_end" in line and silence_start is not None:
                    try:
                        parts = line.split("silence_end:")
                        if len(parts) > 1:
                            silence_end = float(parts[1].strip().split()[0])
                            gaps.append((silence_start, silence_end))
                            silence_start = None
                    except (ValueError, IndexError):
                        pass

        except Exception as e:
            logger.warning(f"[SpeakerDiarizer] Silence detection error: {e}")

        return gaps

    @staticmethod
    def is_pyannote_available() -> bool:
        """Check if pyannote.audio is available."""
        return HAS_PYANNOTE

    def merge_with_transcription(
        self,
        diarization: DiarizationResult,
        transcription_segments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge diarization results with transcription segments.

        Combines speaker labels from diarization with text from transcription
        based on timestamp alignment.

        Args:
            diarization: Speaker diarization result
            transcription_segments: List of transcription segments with
                'start', 'end', 'text' fields

        Returns:
            List of segments with 'start', 'end', 'text', 'speaker' fields
        """
        merged = []

        for trans_seg in transcription_segments:
            trans_start = trans_seg.get("start", 0.0)
            trans_end = trans_seg.get("end", 0.0)
            trans_text = trans_seg.get("text", "")

            # Find the speaker for this segment based on overlap
            best_speaker = "SPEAKER_UNKNOWN"
            best_overlap = 0.0

            for diar_seg in diarization.segments:
                # Calculate overlap
                overlap_start = max(trans_start, diar_seg.start)
                overlap_end = min(trans_end, diar_seg.end)
                overlap = max(0.0, overlap_end - overlap_start)

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = diar_seg.speaker_id

            merged.append(
                {
                    "start": trans_start,
                    "end": trans_end,
                    "text": trans_text,
                    "speaker": best_speaker,
                }
            )

        return merged
