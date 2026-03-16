"""
Tests for Speaker Diarization Module (Task 5)

Tests cover:
- SpeakerDiarizer initialization and configuration
- Heuristic-based diarization fallback
- Diarization result model
- Transcription merging
- Speaker statistics
"""

import pytest
import tempfile
import os

from src.education.speaker_diarization import (
    SpeakerDiarizer,
    SpeakerSegment,
    DiarizationResult,
    HAS_PYANNOTE,
)


class TestSpeakerSegmentModel:
    """Test SpeakerSegment Pydantic model."""

    def test_speaker_segment_creation(self):
        """Test creating a speaker segment."""
        segment = SpeakerSegment(
            start=0.0,
            end=5.5,
            speaker_id="SPEAKER_00",
            confidence=0.95,
        )

        assert segment.start == 0.0
        assert segment.end == 5.5
        assert segment.speaker_id == "SPEAKER_00"
        assert segment.confidence == 0.95

    def test_speaker_segment_optional_confidence(self):
        """Test speaker segment without confidence score."""
        segment = SpeakerSegment(
            start=1.0,
            end=3.0,
            speaker_id="SPEAKER_01",
        )

        assert segment.confidence is None


class TestDiarizationResultModel:
    """Test DiarizationResult Pydantic model."""

    def test_diarization_result_creation(self):
        """Test creating a diarization result."""
        segments = [
            SpeakerSegment(start=0.0, end=5.0, speaker_id="SPEAKER_00"),
            SpeakerSegment(start=5.5, end=10.0, speaker_id="SPEAKER_01"),
        ]

        result = DiarizationResult(
            segments=segments,
            num_speakers=2,
            total_duration=10.0,
            method_used="heuristic",
            speaker_stats={"SPEAKER_00": 5.0, "SPEAKER_01": 4.5},
        )

        assert len(result.segments) == 2
        assert result.num_speakers == 2
        assert result.total_duration == 10.0
        assert result.method_used == "heuristic"
        assert result.speaker_stats["SPEAKER_00"] == 5.0


class TestSpeakerDiarizerInit:
    """Test SpeakerDiarizer initialization."""

    def test_diarizer_default_init(self):
        """Test default initialization."""
        diarizer = SpeakerDiarizer()

        assert diarizer.use_gpu is False
        assert diarizer.min_speakers == 1
        assert diarizer.max_speakers == 10
        assert diarizer.pipeline is None

    def test_diarizer_custom_init(self):
        """Test initialization with custom parameters."""
        diarizer = SpeakerDiarizer(
            use_gpu=True,
            huggingface_token="test_token",
            min_speakers=2,
            max_speakers=5,
        )

        assert diarizer.use_gpu is True
        assert diarizer.huggingface_token == "test_token"
        assert diarizer.min_speakers == 2
        assert diarizer.max_speakers == 5

    def test_is_pyannote_available(self):
        """Test checking pyannote availability."""
        # This should match the actual import status
        assert SpeakerDiarizer.is_pyannote_available() == HAS_PYANNOTE


class TestHeuristicDiarization:
    """Test heuristic-based speaker diarization fallback."""

    @pytest.fixture
    def diarizer(self):
        """Create diarizer without pyannote (heuristic mode)."""
        diarizer = SpeakerDiarizer()
        diarizer.pipeline = None  # Force heuristic mode
        diarizer._initialized = True
        return diarizer

    def test_heuristic_diarization_with_audio(self, diarizer):
        """Test heuristic diarization on audio file."""
        # Create a minimal test audio file using ffmpeg
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name

        try:
            # Generate 5 seconds of silence with ffmpeg
            import subprocess

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "5",
                    audio_path,
                ],
                capture_output=True,
                timeout=30,
            )

            result = diarizer._diarize_with_heuristics(audio_path)

            assert isinstance(result, DiarizationResult)
            assert result.method_used == "heuristic"
            assert result.total_duration >= 0

        finally:
            os.unlink(audio_path)

    def test_heuristic_diarization_nonexistent_file(self, diarizer):
        """Test heuristic diarization with nonexistent file."""
        result = diarizer._diarize_with_heuristics("/nonexistent/audio.wav")

        assert isinstance(result, DiarizationResult)
        assert result.method_used == "heuristic"
        # Should still return a valid result structure

    def test_detect_silence_gaps(self, diarizer):
        """Test silence gap detection."""
        # Create audio with silence gaps
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name

        try:
            import subprocess

            # Generate audio with silence in the middle
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "3",
                    audio_path,
                ],
                capture_output=True,
                timeout=30,
            )

            gaps = diarizer._detect_silence_gaps(audio_path)

            # Should return a list (may be empty for silent audio)
            assert isinstance(gaps, list)

        finally:
            os.unlink(audio_path)


class TestTranscriptionMerging:
    """Test merging diarization with transcription."""

    @pytest.fixture
    def diarizer(self):
        """Create diarizer for testing."""
        return SpeakerDiarizer()

    def test_merge_with_transcription(self, diarizer):
        """Test merging diarization results with transcription."""
        diarization = DiarizationResult(
            segments=[
                SpeakerSegment(start=0.0, end=5.0, speaker_id="SPEAKER_00"),
                SpeakerSegment(start=5.0, end=10.0, speaker_id="SPEAKER_01"),
            ],
            num_speakers=2,
            total_duration=10.0,
            method_used="heuristic",
            speaker_stats={"SPEAKER_00": 5.0, "SPEAKER_01": 5.0},
        )

        transcription = [
            {"start": 0.5, "end": 2.0, "text": "Hello there"},
            {"start": 3.0, "end": 4.5, "text": "How are you"},
            {"start": 5.5, "end": 7.0, "text": "I'm fine thanks"},
            {"start": 8.0, "end": 9.5, "text": "Good to hear"},
        ]

        merged = diarizer.merge_with_transcription(diarization, transcription)

        assert len(merged) == 4
        # First two should be SPEAKER_00 (0-5s)
        assert merged[0]["speaker"] == "SPEAKER_00"
        assert merged[1]["speaker"] == "SPEAKER_00"
        # Last two should be SPEAKER_01 (5-10s)
        assert merged[2]["speaker"] == "SPEAKER_01"
        assert merged[3]["speaker"] == "SPEAKER_01"
        # Text should be preserved
        assert merged[0]["text"] == "Hello there"

    def test_merge_empty_transcription(self, diarizer):
        """Test merging with empty transcription."""
        diarization = DiarizationResult(
            segments=[
                SpeakerSegment(start=0.0, end=5.0, speaker_id="SPEAKER_00"),
            ],
            num_speakers=1,
            total_duration=5.0,
            method_used="heuristic",
            speaker_stats={"SPEAKER_00": 5.0},
        )

        merged = diarizer.merge_with_transcription(diarization, [])

        assert merged == []

    def test_merge_empty_diarization(self, diarizer):
        """Test merging with empty diarization."""
        diarization = DiarizationResult(
            segments=[],
            num_speakers=0,
            total_duration=0.0,
            method_used="heuristic",
            speaker_stats={},
        )

        transcription = [
            {"start": 0.0, "end": 2.0, "text": "Some text"},
        ]

        merged = diarizer.merge_with_transcription(diarization, transcription)

        assert len(merged) == 1
        assert merged[0]["speaker"] == "SPEAKER_UNKNOWN"


class TestMultimediaProcessorIntegration:
    """Test integration with MultimediaProcessor."""

    @pytest.fixture
    def processor(self):
        """Create MultimediaProcessor for testing."""
        from src.education.multimedia_processor import MultimediaProcessor

        return MultimediaProcessor()

    def test_transcribe_with_diarization_exists(self, processor):
        """Test that _transcribe_with_diarization method exists."""
        assert hasattr(processor, "_transcribe_with_diarization")

    def test_get_speaker_statistics_exists(self, processor):
        """Test that get_speaker_statistics method exists."""
        assert hasattr(processor, "get_speaker_statistics")

    def test_get_speaker_statistics_no_data(self, processor):
        """Test speaker statistics with no diarization data."""
        stats = processor.get_speaker_statistics(None)

        assert "error" in stats

    def test_get_speaker_statistics_with_data(self, processor):
        """Test speaker statistics with diarization data."""
        diarization = DiarizationResult(
            segments=[
                SpeakerSegment(start=0.0, end=10.0, speaker_id="SPEAKER_00"),
                SpeakerSegment(start=10.0, end=20.0, speaker_id="SPEAKER_01"),
            ],
            num_speakers=2,
            total_duration=20.0,
            method_used="heuristic",
            speaker_stats={"SPEAKER_00": 10.0, "SPEAKER_01": 10.0},
        )

        stats = processor.get_speaker_statistics(diarization)

        assert stats["num_speakers"] == 2
        assert stats["total_duration"] == 20.0
        assert stats["method"] == "heuristic"
        assert "SPEAKER_00" in stats["speakers"]
        assert stats["speakers"]["SPEAKER_00"]["percentage"] == 50.0


class TestPyannoteIntegration:
    """Test pyannote.audio integration (when available)."""

    @pytest.mark.skipif(not HAS_PYANNOTE, reason="pyannote.audio not installed")
    def test_pyannote_initialization(self):
        """Test pyannote pipeline initialization."""
        # This test only runs if pyannote is installed
        diarizer = SpeakerDiarizer(huggingface_token=os.getenv("HUGGINGFACE_TOKEN"))

        if diarizer.huggingface_token:
            # Will attempt to initialize pyannote
            success = diarizer._init_pyannote()
            # May succeed or fail depending on token validity
            assert isinstance(success, bool)
        else:
            # Without token, should fall back to heuristic
            success = diarizer._init_pyannote()
            assert success is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
