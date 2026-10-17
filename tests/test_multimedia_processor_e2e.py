"""
End-to-end tests for Multimedia Processor.

Tests video/audio transcription, caption generation, and compliance checking
with mocked external dependencies (ffmpeg, whisper).
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
from types import SimpleNamespace

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

    @pytest.mark.parametrize(
        "returncode,stdout",
        [
            (1, ""),
            (0, ""),
            (0, "duration=bad\ncodec_type=audio"),
            (0, "duration=10.0"),
            (0, "codec_type=invalid\nduration=10.0"),
        ],
    )
    @pytest.mark.parametrize("file_path", ["lecture.mp4", "lecture.mp3"])
    def test_probe_failure_never_claims_audio_only(
        self, processor, mock_subprocess, returncode, stdout, file_path
    ):
        mock_subprocess.return_value.returncode = returncode
        mock_subprocess.return_value.stdout = stdout
        result = processor.process_media(
            file_path,
            generate_captions=False,
            generate_audio_descriptions=False,
            detect_flashing=False,
        )
        assert result.media_type == "unknown"
        assert result.compliance_score is None
        assert result.flashing_analysis.state == "unassessed"
        assert result.flashing_analysis.reason == "analysis_failed"
        assert result.issues == []

    def test_missing_probe_dependency_is_bounded_and_unassessed(
        self, processor, mock_subprocess, caplog
    ):
        mock_subprocess.side_effect = FileNotFoundError("/private/probe")
        result = processor.process_media(
            "lecture.mp3",
            generate_captions=False,
            generate_audio_descriptions=False,
            detect_flashing=True,
        )
        assert result.media_type == "unknown"
        assert result.compliance_score is None
        assert result.flashing_analysis.reason == "analysis_failed"
        assert "/private/probe" not in caplog.text

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
            assert result.has_captions is False
            assert result.compliance_score == 80.0
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

            if "format=duration" in cmd:
                mock_res.stdout = "duration=15.0\ncodec_type=video"
            elif "ffprobe" in cmd:
                mock_res.stdout = "codec_type=video"
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
                assert result.compliance_score is None
                assert result.flashing_analysis.state == "unassessed"
                assert result.flashing_analysis.reason == "disabled"
                assert result.flashing_analysis.flash_count is None

    @pytest.mark.parametrize(
        "second_returncode,second_stdout",
        [
            (1, ""),
            (0, ""),
            (0, "codec_type=invalid"),
            (0, "codec_type=audio"),
        ],
    )
    def test_failed_second_video_probe_cannot_claim_missing_captions(
        self, processor, mock_subprocess, second_returncode, second_stdout
    ):
        first = SimpleNamespace(returncode=0, stdout="duration=15.0\ncodec_type=video")
        second = SimpleNamespace(returncode=second_returncode, stdout=second_stdout)
        mock_subprocess.side_effect = [first, second]
        with pytest.raises(RuntimeError, match="Caption stream inspection unavailable"):
            processor.process_media(
                "lecture.mp4",
                generate_captions=False,
                generate_audio_descriptions=False,
                detect_flashing=False,
            )
        assert mock_subprocess.call_count == 2

    def test_missing_caption_probe_dependency_has_bounded_error(
        self, processor, mock_subprocess, caplog, capsys
    ):
        first = SimpleNamespace(returncode=0, stdout="duration=15.0\ncodec_type=video")
        mock_subprocess.side_effect = [first, FileNotFoundError("/private/probe")]
        with pytest.raises(
            RuntimeError, match="Caption stream inspection unavailable"
        ) as raised:
            processor.process_media(
                "lecture.mp4",
                generate_captions=False,
                generate_audio_descriptions=False,
                detect_flashing=False,
            )
        assert "/private/probe" not in str(raised.value)
        assert "/private/probe" not in caplog.text
        assert "/private/probe" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        "streams,has_captions",
        [
            ("codec_type=video", False),
            ("codec_type=video\ncodec_type=subtitle", True),
        ],
    )
    def test_successful_video_stream_probe_establishes_caption_presence(
        self, processor, mock_subprocess, streams, has_captions
    ):
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = streams
        assert processor._check_existing_captions("lecture.mp4") is has_captions

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
        assert score is None
        assert len(issues) == 0

        # Case 2: Missing captions (Critical)
        score, issues = processor._check_compliance(
            media_type="video", duration=10.0, has_captions=False, transcription=None
        )
        assert score is None
        assert any(i["severity"] == "critical" for i in issues)

        # Case 3: Missing audio descriptions (High)
        score, issues = processor._check_compliance(
            media_type="video",
            duration=10.0,
            has_captions=True,
            transcription=[],
            audio_descriptions=None,  # Missing
        )
        assert score is None
        assert any(i["severity"] == "high" for i in issues)


class TestFlashingAssessmentContainment:
    """The retired heuristic cannot create measurements or safety claims."""

    @pytest.mark.parametrize("frame_output", ["", "I\nP\n", "0.1,I\n0.2,P\n"])
    def test_frame_metadata_cannot_establish_safety(
        self, processor, mock_subprocess, frame_output
    ):
        mock_subprocess.return_value.stdout = frame_output
        result = processor._detect_flashing_content("video.mp4", 60.0)
        assert result.state == "unassessed"
        assert result.reason == "detector_unavailable"
        assert result.has_flashing is None
        assert result.flash_count is None
        assert result.max_flash_frequency is None
        assert result.red_flash_detected is None
        assert result.severity is None
        mock_subprocess.assert_not_called()

    def test_unassessed_model_rejects_fake_safe_measurements(self):
        with pytest.raises(ValueError):
            FlashingContentResult(has_flashing=False, flash_count=0, severity="safe")

    def test_legacy_reload_cannot_publish_safe_or_numeric_video_claim(self):
        from src.education.latex_evidence import public_scan_structure

        legacy = {
            "media_type": "video",
            "flashing_analysis": {
                "severity": "safe",
                "has_flashing": False,
                "flash_count": 0,
            },
            "transcription_output": {
                "compliance_score": 100,
                "issues": [{"rule": "WCAG 1.2.2", "severity": "critical"}],
            },
        }
        shown = public_scan_structure(legacy, "MULTIMEDIA")
        assert shown["flashing_assessment"] == {
            "state": "unassessed",
            "reason": "detector_unavailable",
            "version": "unassessed-v1",
        }
        assert shown["transcription_output"]["compliance_score"] is None
        assert (
            shown["transcription_output"]["issues"]
            == legacy["transcription_output"]["issues"]
        )
        assert "flashing_analysis" not in shown

    def test_local_durable_result_keeps_findings_and_companions(self, tmp_path):
        from src.api.education.multimedia_routes import process_multimedia_background
        from src.db.models import ScanResult, ScanStatus

        media = tmp_path / "lecture.mp4"
        media.write_bytes(b"local test media")
        scan = SimpleNamespace(id="scan-1", department_id="dept-1")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = scan
        processor = MagicMock()
        processor.process_media.return_value = MultimediaProcessingResult(
            file_path=str(media),
            file_name="lecture.mp4",
            media_type="video",
            duration=12.0,
            has_captions=False,
            compliance_score=None,
            flashing_analysis=FlashingContentResult(),
            transcription=[
                TranscriptionSegment(start_time=0, end_time=1, text="Hello")
            ],
            caption_formats={"webvtt": "WEBVTT", "srt": "1"},
            issues=[
                {
                    "rule": "WCAG 1.2.2",
                    "severity": "critical",
                    "message": "Missing captions",
                }
            ],
        )
        with (
            patch("src.db.database.SessionLocal", return_value=db),
            patch(
                "src.education.multimedia_processor.MultimediaProcessor",
                return_value=processor,
            ),
            patch(
                "src.ai.workspace_provider_runtime.workspace_provider_runtime",
                return_value=MagicMock(),
            ),
            patch("src.services.visual_analysis_service.DurableVisualAnalysisRecorder"),
            patch(
                "src.api.education.multimedia_routes.runtime_ollama_usage",
                return_value={},
            ),
        ):
            process_multimedia_background(
                str(media),
                b"local test media",
                "lecture.mp4",
                "scan-1",
                True,
                False,
                False,
                True,
                False,
                "base",
                "user-1",
                "dept-1",
            )
        saved = next(
            call.args[0]
            for call in db.add.call_args_list
            if isinstance(call.args[0], ScanResult)
        )
        assert scan.status == ScanStatus.COMPLETED
        assert saved.compliance_score is None
        assert saved.issues == processor.process_media.return_value.issues
        assert saved.structure["flashing_assessment"] == {
            "state": "unassessed",
            "reason": "detector_unavailable",
            "version": "unassessed-v1",
        }
        output = saved.structure["transcription_output"]
        assert output["compliance_score"] is None
        assert output["transcription"]["full_text"] == "Hello"
        assert output["captions"] == {"webvtt": "WEBVTT", "srt": "1"}

    @pytest.mark.asyncio
    async def test_unassessed_video_cannot_export_compliance_pdf(self):
        from fastapi import HTTPException
        from src.api.education.scan_history_routes import download_scan_report
        from src.db.models import ScanType

        result = SimpleNamespace(
            compliance_score=None,
            issues=[{"rule": "WCAG 1.2.2", "severity": "critical"}],
            structure={
                "media_type": "video",
                "flashing_assessment": {"state": "unassessed"},
            },
        )
        scan = SimpleNamespace(
            id="scan-1", scan_type=ScanType.MULTIMEDIA, result=result
        )
        principal = MagicMock()
        principal.as_legacy_tuple.return_value = (None, "user-1", "dept-1")
        with (
            patch(
                "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
                return_value=scan,
            ),
            patch("src.api.education.scan_history_routes.authorize_scan_access"),
        ):
            with pytest.raises(HTTPException) as raised:
                await download_scan_report(
                    "scan-1", db=MagicMock(), principal=principal
                )
        assert raised.value.status_code == 409
        assert result.issues[0]["rule"] == "WCAG 1.2.2"

    @pytest.mark.asyncio
    async def test_legacy_media_detail_keeps_findings_without_safe_claim(self):
        from datetime import datetime, timezone
        from src.api.education.scan_history_routes import get_scan_details
        from src.db.models import ScanStatus, ScanType

        finding = {
            "rule": "WCAG 1.2.2",
            "severity": "critical",
            "message": "Missing captions",
        }
        retired = {
            "rule": "WCAG 2.3.1",
            "severity": "critical",
            "message": "Dangerous flashing content detected (4.0 Hz)",
            "timestamps": [1.0],
        }
        result = SimpleNamespace(
            compliance_score=100,
            wcag_level="AA",
            issues=[finding, retired],
            critical_issues=2,
            high_issues=0,
            medium_issues=0,
            low_issues=0,
            structure={
                "media_type": "video",
                "flashing_analysis": {"severity": "safe", "flash_count": 0},
                "transcription_output": {
                    "issues": [finding, retired],
                    "issues_count": 2,
                    "compliance_score": 100,
                },
            },
            suggestions=None,
            ocr_used=False,
            ollama_used=False,
        )
        scan = SimpleNamespace(
            id="scan-1",
            file_name="lecture.mp4",
            scan_type=ScanType.MULTIMEDIA,
            status=ScanStatus.COMPLETED,
            pages=1,
            file_size_bytes=100,
            processing_time_ms=10,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            result=result,
        )
        principal = MagicMock()
        principal.as_legacy_tuple.return_value = (None, "user-1", "dept-1")
        with (
            patch(
                "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
                return_value=scan,
            ),
            patch("src.api.education.scan_history_routes.authorize_scan_access"),
            patch(
                "src.api.education.remediation_routes.document_remediation_eligibility",
                return_value=None,
            ),
        ):
            response = await get_scan_details(
                "scan-1", db=MagicMock(), principal=principal
            )
        shown = response["scan"]["result"]
        assert shown["compliance_score"] is None
        assert shown["wcag_level"] is None
        assert shown["issues"] == [finding]
        assert shown["summary"] == {
            "critical": 1,
            "high": 0,
            "medium": 0,
            "low": 0,
            "total": 1,
        }
        assert shown["structure"]["flashing_assessment"]["state"] == "unassessed"
        assert shown["structure"]["transcription_output"]["issues"] == [finding]
        assert shown["structure"]["transcription_output"]["issues_count"] == 1
        assert "flashing_analysis" not in shown["structure"]
        assert result.issues == [finding, retired]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
