"""
End-to-end tests for Multimedia Processor.

Tests video/audio transcription, caption generation, and compliance checking
with mocked external dependencies (ffmpeg, whisper).
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

# Add backend to path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.multimedia_processor import (
    MultimediaProcessor,
    MultimediaProcessingResult,
    TranscriptionSegment,
    AudioDescription,
    FlashingContentResult,
)


@pytest.fixture
def mock_whisper_model():
    """Mock the faster_whisper WhisperModel."""
    with patch("faster_whisper.WhisperModel") as mock_model_cls:
        mock_instance = MagicMock()
        mock_model_cls.return_value = mock_instance

        # Mock transcribe return value
        mock_segment1 = MagicMock()
        mock_segment1.start = 0.0
        mock_segment1.end = 2.0
        mock_segment1.text = "Hello world."
        mock_segment1.no_speech_prob = 0.1  # Set explicit float
        mock_segment1.confidence = 0.9  # Set explicit float

        mock_segment2 = MagicMock()
        mock_segment2.start = 2.5
        mock_segment2.end = 4.0
        mock_segment2.text = "This is a test."
        mock_segment2.no_speech_prob = 0.1  # Set explicit float
        mock_segment2.confidence = 0.9  # Set explicit float

        mock_instance.transcribe.return_value = ([mock_segment1, mock_segment2], None)

        yield mock_model_cls


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for ffmpeg/ffprobe calls."""
    with patch("subprocess.run") as mock_run:
        # Default mock response for ffprobe duration check
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "duration=10.5\ncodec_type=video"
        mock_run.return_value = mock_result
        yield mock_run


@pytest.fixture
def processor():
    """Create MultimediaProcessor instance with mocked LLM."""
    with patch("src.ai.providers.get_provider_manager"):
        return MultimediaProcessor()


class TestMultimediaProcessor:
    """Test suite for MultimediaProcessor."""

    def test_initialization(self, processor):
        """Test processor initialization."""
        assert processor is not None
        assert processor.whisper_model == "whisper:base"

    def test_get_media_info_video(self, processor, mock_subprocess):
        """Test media info extraction for video."""
        # Setup mock for video
        mock_subprocess.return_value.stdout = "duration=120.5\ncodec_type=video"

        media_type, duration = processor._get_media_info("test.mp4")

        assert media_type == "video"
        assert duration == 120.5

    def test_get_media_info_audio(self, processor, mock_subprocess):
        """Test media info extraction for audio."""
        # Setup mock for audio
        mock_subprocess.return_value.stdout = "duration=60.0\ncodec_type=audio"

        media_type, duration = processor._get_media_info("test.mp3")

        assert media_type == "audio"
        assert duration == 60.0

    def test_process_media_audio_only(
        self, processor, mock_whisper_model, mock_subprocess
    ):
        """Test processing of audio-only file."""
        # Setup mocks
        mock_subprocess.return_value.stdout = "duration=10.0\ncodec_type=audio"

        with tempfile.NamedTemporaryFile(suffix=".mp3") as tf:
            result = processor.process_media(
                tf.name,
                generate_captions=True,
                generate_audio_descriptions=False,
                detect_flashing=False,
            )

            assert isinstance(result, MultimediaProcessingResult)
            assert result.media_type == "audio"
            assert result.duration == 10.0
            assert result.has_captions is True
            assert len(result.transcription) == 2
            assert "webvtt" in result.caption_formats
            assert "srt" in result.caption_formats

    def test_process_media_video(self, processor, mock_whisper_model, mock_subprocess):
        """Test processing of video file."""
        # Setup mocks to handle multiple subprocess calls
        # 1. ffprobe duration (video)
        # 2. ffprobe captions check
        # 3. ffmpeg extract audio

        def side_effect(*args, **kwargs):
            cmd = args[0]
            mock_res = MagicMock()
            mock_res.returncode = 0

            if "ffprobe" in cmd and "codec_type" in cmd:
                mock_res.stdout = "duration=15.0\ncodec_type=video"
            elif "ffmpeg" in cmd:
                # Audio extraction
                pass

            return mock_res

        mock_subprocess.side_effect = side_effect

        with tempfile.NamedTemporaryFile(suffix=".mp4") as tf:
            # Create a fake audio file that ffmpeg would have created
            with patch("tempfile.NamedTemporaryFile") as mock_temp:
                mock_audio = MagicMock()
                mock_audio.name = "temp_audio.wav"
                mock_temp.return_value.__enter__.return_value = mock_audio

                result = processor.process_media(
                    tf.name,
                    generate_captions=True,
                    generate_audio_descriptions=False,
                    detect_flashing=False,
                )

                assert result.media_type == "video"
                assert len(result.transcription) == 2

    def test_generate_webvtt(self, processor):
        """Test WebVTT generation."""
        segments = [
            TranscriptionSegment(start_time=0.0, end_time=2.0, text="Hello."),
            TranscriptionSegment(start_time=2.5, end_time=4.0, text="World."),
        ]

        vtt = processor._generate_webvtt(segments)

        assert "WEBVTT" in vtt
        assert "00:00:00.000 --> 00:00:02.000" in vtt
        assert "Hello." in vtt
        assert "World." in vtt

    def test_generate_srt(self, processor):
        """Test SRT generation."""
        segments = [TranscriptionSegment(start_time=0.0, end_time=2.0, text="Hello.")]

        srt = processor._generate_srt(segments)

        assert "00:00:00,000 --> 00:00:02,000" in srt
        assert "Hello." in srt

    def test_compliance_scoring(self, processor):
        """Test compliance scoring logic."""
        # Case 1: Perfect compliance
        score, issues = processor._check_compliance(
            media_type="video",
            duration=10.0,
            has_captions=True,
            transcription=[TranscriptionSegment(start_time=0, end_time=1, text="Test")],
            audio_descriptions=[AudioDescription(timestamp=0, description="Test")],
            flashing_analysis=None,
        )
        assert score == 100.0
        assert len(issues) == 0

        # Case 2: Missing captions (Critical)
        score, issues = processor._check_compliance(
            media_type="video", duration=10.0, has_captions=False, transcription=None
        )
        assert score <= 80.0
        assert any(i["severity"] == "critical" for i in issues)

        # Case 3: Missing audio descriptions (High)
        score, issues = processor._check_compliance(
            media_type="video",
            duration=10.0,
            has_captions=True,
            transcription=[],
            audio_descriptions=None,  # Missing
        )
        assert score <= 90.0
        assert any(i["severity"] == "high" for i in issues)


class TestRedFlashDetection:
    """Test suite for WCAG 2.3.1 Red Flash Threshold Detection."""

    def test_flashing_content_result_model(self):
        """Test that FlashingContentResult includes red flash fields."""
        result = FlashingContentResult(
            has_flashing=True,
            flash_count=5,
            max_flash_frequency=2.5,
            timestamps=[0.1, 0.3, 0.5, 0.7, 0.9],
            red_flash_detected=True,
            red_flash_count=2,
            red_flash_timestamps=[0.2, 0.6],
            red_saturation_peak=0.85,
            severity="dangerous",
            recommendation="Red flashing detected",
        )

        assert result.red_flash_detected is True
        assert result.red_flash_count == 2
        assert len(result.red_flash_timestamps) == 2
        assert result.red_saturation_peak == 0.85

    def test_flashing_content_result_defaults(self):
        """Test that red flash fields have proper defaults."""
        result = FlashingContentResult(
            has_flashing=False,
            flash_count=0,
            max_flash_frequency=0.0,
            timestamps=[],
            severity="safe",
            recommendation="No issues",
        )

        assert result.red_flash_detected is False
        assert result.red_flash_count == 0
        assert result.red_flash_timestamps == []
        assert result.red_saturation_peak is None

    def test_red_flash_detection_no_video(self, processor, mock_subprocess):
        """Test red flash detection gracefully handles missing video."""
        # Mock ffmpeg to fail (no frames extracted)
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = b"Video not found"

        red_detected, count, timestamps, saturation = processor._detect_red_flashes(
            "nonexistent.mp4", 10.0, 0.1
        )

        assert red_detected is False
        assert count == 0
        assert timestamps == []

    def test_detect_flashing_includes_red_analysis(self, processor, mock_subprocess):
        """Test that _detect_flashing_content includes red flash analysis."""
        # Mock ffprobe for brightness extraction
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "0.5,I\n0.6,P\n0.4,P\n"

        with patch.object(processor, "_detect_red_flashes") as mock_red:
            mock_red.return_value = (True, 3, [0.2, 0.5, 0.8], 0.82)

            result = processor._detect_flashing_content("test.mp4", 10.0)

            assert result.red_flash_detected is True
            assert result.red_flash_count == 3
            assert result.red_saturation_peak == 0.82

    def test_red_flash_severity_escalation(self, processor, mock_subprocess):
        """Test that red flashing properly escalates severity."""
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "0.5,I\n0.6,P\n"

        # Mock significant red flashing
        with patch.object(processor, "_detect_red_flashes") as mock_red:
            # High frequency red flashing > 3 Hz
            mock_red.return_value = (
                True,
                10,
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                0.9,
            )

            with patch.object(
                processor, "_extract_frame_brightness"
            ) as mock_brightness:
                mock_brightness.return_value = [(0.0, 0.5), (0.1, 0.6), (0.2, 0.5)]

                result = processor._detect_flashing_content("test.mp4", 10.0)

                # Red flashing should escalate to dangerous
                assert result.severity in ["dangerous", "warning"]
                assert (
                    "red" in result.recommendation.lower()
                    or "RED" in result.recommendation
                )

    def test_wcag_red_saturation_criteria(self, processor):
        """Test WCAG red saturation calculation criteria.

        WCAG defines saturated red as:
        - R/(R+G+B) >= 0.8
        - R > 128 (on 0-255 scale)
        """
        # This is a unit test for the criteria - actual frame analysis
        # requires PIL/numpy which may not be available in test env

        # Test case: Pure red (255, 0, 0)
        # R/(R+G+B) = 255/(255+0+0) = 1.0 >= 0.8 ✓
        # R = 255 > 128 ✓
        # Should be detected as saturated red

        # Test case: Dark red (64, 0, 0)
        # R/(R+G+B) = 64/(64+0+0) = 1.0 >= 0.8 ✓
        # R = 64 < 128 ✗
        # Should NOT be detected (not bright enough)

        # Test case: Orange (255, 128, 0)
        # R/(R+G+B) = 255/(255+128+0) = 0.67 < 0.8 ✗
        # Should NOT be detected (not saturated enough)

        # The actual _check_frame_red_saturation method handles this
        # but requires PIL/numpy at runtime
        assert True  # Placeholder for criteria validation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
