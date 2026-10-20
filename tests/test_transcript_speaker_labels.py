"""
Transcripts never assert a speaker without evidence.

The product has no working speaker identification, so a single voice must
never come out as several speakers. These tests drive one voice through the
transcription path with a pause longer than two seconds and a line that starts
with a word and a colon, the two inputs the removed heuristic turned into
"Speaker N" and name labels, and check every output that could carry a label.
"""

import inspect
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.education.multimedia_processor import (
    AudioDescription,
    MultimediaProcessor,
    TranscriptionSegment,
)
from src.education.speaker_diarization import (
    DiarizationResult,
    SpeakerDiarizer,
    SpeakerSegment,
)

# One voice. The second cue follows a 3 s pause, the third starts with a word
# and a colon, and the fourth continues more than 2 s after the last label.
ONE_VOICE = [
    (0.0, 2.0, "Hello and welcome."),
    (5.0, 7.0, "We resume after a long pause."),
    (7.2, 9.0, "Note: this line begins with a word and a colon."),
    (9.4, 11.0, "Still the same voice."),
]

EXPECTED_VTT = (
    "WEBVTT\n\n"
    "1\n00:00:00.000 --> 00:00:02.000\nHello and welcome.\n\n"
    "2\n00:00:05.000 --> 00:00:07.000\nWe resume after a long pause.\n\n"
    "3\n00:00:07.200 --> 00:00:09.000\n"
    "Note: this line begins with a word and a colon.\n\n"
    "4\n00:00:09.400 --> 00:00:11.000\nStill the same voice.\n\n"
)

EXPECTED_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nHello and welcome.\n\n"
    "2\n00:00:05,000 --> 00:00:07,000\nWe resume after a long pause.\n\n"
    "3\n00:00:07,200 --> 00:00:09,000\n"
    "Note: this line begins with a word and a colon.\n\n"
    "4\n00:00:09,400 --> 00:00:11,000\nStill the same voice.\n\n"
)


def _whisper_segments():
    return [
        SimpleNamespace(start=start, end=end, text=text, no_speech_prob=0.1)
        for start, end, text in ONE_VOICE
    ]


@pytest.fixture
def one_voice_whisper():
    """faster-whisper returning the single-voice transcript above."""
    with patch("faster_whisper.WhisperModel") as model_cls:
        model_cls.return_value.transcribe.return_value = (_whisper_segments(), None)
        yield model_cls


@pytest.fixture
def processor():
    return MultimediaProcessor(use_gemini=False)


def _process_audio(processor, **options):
    with (
        patch.object(processor, "_get_media_info", return_value=("audio", 12.0)),
        patch.object(processor, "_extract_audio", return_value="/nonexistent/a.wav"),
    ):
        return processor.process_media(
            "clip.wav",
            generate_audio_descriptions=False,
            detect_flashing=False,
            **options,
        )


def _assert_no_speaker_markup(vtt: str, srt: str) -> None:
    assert "<v" not in vtt
    assert "Speaker" not in vtt and "Speaker" not in srt
    # SRT speaker prefixes look like "[Name]: text" at the start of a cue line.
    assert re.search(r"^\[[^\]\n]+\]:", srt, re.MULTILINE) is None


class TestSingleVoiceIsNeverSplitIntoSpeakers:
    @pytest.mark.parametrize("enhance_captions", [True, False])
    def test_transcription_has_no_speakers(
        self, processor, one_voice_whisper, enhance_captions
    ):
        segments = processor._transcribe_audio(
            "clip.wav", enhance_captions=enhance_captions
        )

        assert [s.speaker_id for s in segments] == [None] * len(ONE_VOICE)
        assert [s.model_dump()["speaker_id"] for s in segments] == [None] * len(
            ONE_VOICE
        )
        # Text and timing are exactly what was transcribed.
        assert [(s.start_time, s.end_time, s.text) for s in segments] == ONE_VOICE

    @pytest.mark.parametrize("enhance_captions", [True, False])
    def test_webvtt_and_srt_have_no_speaker_labels(
        self, processor, one_voice_whisper, enhance_captions
    ):
        # enhance_captions=True is the scan API's default and live path.
        result = _process_audio(processor, enhance_captions=enhance_captions)

        vtt = result.caption_formats["webvtt"]
        srt = result.caption_formats["srt"]
        _assert_no_speaker_markup(vtt, srt)
        assert vtt == EXPECTED_VTT
        assert srt == EXPECTED_SRT

    def test_stored_segments_carry_no_speaker(self, processor, one_voice_whisper):
        result = _process_audio(processor)

        stored = [segment.model_dump() for segment in result.transcription]
        assert [segment["speaker_id"] for segment in stored] == [None] * len(ONE_VOICE)

    def test_plain_text_transcript_has_no_speaker_headers(
        self, processor, one_voice_whisper
    ):
        result = _process_audio(processor, generate_transcript=True)

        transcript = result.full_transcript
        assert "Speaker" not in transcript
        assert "None" not in transcript and "Unknown" not in transcript
        lines = transcript.splitlines()
        # Every segment keeps its own timestamp and text, with no header line.
        assert "[00:00] Hello and welcome." in lines
        assert "[00:05] We resume after a long pause." in lines
        assert "[00:07] Note: this line begins with a word and a colon." in lines
        assert "[00:09] Still the same voice." in lines
        assert not [
            line for line in lines if re.fullmatch(r"\[[\d:]+\] [^\s].*:", line)
        ]

    def test_saved_scan_structure_carries_no_speaker(self, one_voice_whisper, tmp_path):
        """The route builds the real processor and stores what the CLI and dashboard read."""
        from src.api.education.multimedia_routes import process_multimedia_background
        from src.db.models import ScanResult, ScanStatus

        media = tmp_path / "clip.wav"
        media.write_bytes(b"local test media")
        scan = SimpleNamespace(id="scan-1", department_id="dept-1")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = scan
        original_process_media = MultimediaProcessor.process_media
        with (
            patch("src.db.database.SessionLocal", return_value=db),
            patch.object(
                MultimediaProcessor, "_get_media_info", return_value=("audio", 12.0)
            ),
            patch.object(
                MultimediaProcessor, "_extract_audio", return_value="/nonexistent/a.wav"
            ),
            patch.object(
                MultimediaProcessor,
                "process_media",
                autospec=True,
                side_effect=original_process_media,
            ) as process_media,
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
                "clip.wav",
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

        # The route never passes enhance_captions, so the default applies, and
        # the default is the enhanced path that used to invent speakers.
        assert (
            inspect.signature(original_process_media)
            .parameters["enhance_captions"]
            .default
            is True
        )
        call = process_media.call_args
        assert call.args[1] == str(media)
        assert call.kwargs == {
            "generate_captions": True,
            "generate_audio_descriptions": False,
            "generate_spoken_descriptions": False,
            "detect_flashing": True,
            "generate_transcript": False,
        }

        assert scan.status == ScanStatus.COMPLETED
        output = saved.structure["transcription_output"]
        _assert_no_speaker_markup(
            output["captions"]["webvtt"], output["captions"]["srt"]
        )
        assert output["captions"]["webvtt"] == EXPECTED_VTT
        assert output["captions"]["srt"] == EXPECTED_SRT
        segments = output["transcription"]["segments"]
        assert len(segments) == len(ONE_VOICE)
        assert [segment["speaker_id"] for segment in segments] == [None] * len(
            ONE_VOICE
        )
        assert output["transcription"]["full_text"] == " ".join(
            text for _, _, text in ONE_VOICE
        )


class TestVisualContextDoesNotNameSpeakers:
    """A person visible in a frame is not evidence of who is speaking."""

    DESCRIPTIONS = [
        AudioDescription(
            timestamp=0.5,
            description="A woman in a blue blazer stands at a podium.",
            scene_type="character",
        )
    ]

    def test_unlabelled_segments_stay_unlabelled(self, processor):
        segments = [
            TranscriptionSegment(start_time=0.0, end_time=2.0, text="Hello."),
            TranscriptionSegment(start_time=3.0, end_time=4.0, text="Welcome."),
        ]

        enhanced = processor._enhance_captions_with_visual_context(
            segments, self.DESCRIPTIONS
        )

        assert [s.speaker_id for s in enhanced] == [None, None]
        assert [s.text for s in enhanced] == ["Hello.", "Welcome."]

    def test_an_established_speaker_is_kept_not_replaced(self, processor):
        segments = [
            TranscriptionSegment(
                start_time=0.0, end_time=2.0, text="Hello.", speaker_id="SPEAKER_00"
            )
        ]

        enhanced = processor._enhance_captions_with_visual_context(
            segments, self.DESCRIPTIONS
        )

        assert enhanced[0].speaker_id == "SPEAKER_00"


def _diarizer_returning(outcome):
    """A SpeakerDiarizer whose diarize() yields a fixed result or raises."""

    class _Diarizer(SpeakerDiarizer):
        def diarize(self, audio_path):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return _Diarizer


def _diarization(method_used):
    return DiarizationResult(
        segments=[
            SpeakerSegment(start=0.0, end=3.0, speaker_id="SPEAKER_00"),
            SpeakerSegment(start=5.0, end=8.0, speaker_id="SPEAKER_01"),
        ],
        num_speakers=2,
        total_duration=8.0,
        method_used=method_used,
        speaker_stats={"SPEAKER_00": 3.0, "SPEAKER_01": 3.0},
    )


class TestDiarizationPathLabelsOnlyRealDiarization:
    @pytest.fixture
    def base_segments(self):
        return [
            TranscriptionSegment(start_time=0.0, end_time=2.0, text="One."),
            # No diarization turn overlaps this segment.
            TranscriptionSegment(start_time=4.0, end_time=4.5, text="Two."),
            TranscriptionSegment(start_time=5.5, end_time=7.0, text="Three."),
        ]

    def _run(self, processor, base_segments, diarizer_cls):
        with (
            patch.object(processor, "_transcribe_audio", return_value=base_segments),
            patch("src.education.multimedia_processor.SpeakerDiarizer", diarizer_cls),
        ):
            return processor._transcribe_with_diarization("clip.wav")

    def test_silence_heuristic_result_is_not_turned_into_labels(
        self, processor, base_segments
    ):
        segments, diarization = self._run(
            processor, base_segments, _diarizer_returning(_diarization("heuristic"))
        )

        assert [s.speaker_id for s in segments] == [None, None, None]
        assert diarization is None

    def test_diarizer_failure_leaves_speakers_unlabelled(
        self, processor, base_segments
    ):
        segments, diarization = self._run(
            processor, base_segments, _diarizer_returning(RuntimeError("no model"))
        )

        assert [s.speaker_id for s in segments] == [None, None, None]
        assert diarization is None

    def test_pyannote_result_labels_overlapping_segments_only(
        self, processor, base_segments
    ):
        segments, diarization = self._run(
            processor, base_segments, _diarizer_returning(_diarization("pyannote"))
        )

        # The unmatched segment gets no speaker rather than "SPEAKER_UNKNOWN".
        assert [s.speaker_id for s in segments] == ["SPEAKER_00", None, "SPEAKER_01"]
        assert diarization.method_used == "pyannote"


class TestRealSpeakerLabelsStillRender:
    """Output formats keep honouring a speaker that real diarization supplied."""

    SEGMENTS = [
        TranscriptionSegment(
            start_time=0.0, end_time=2.0, text="Hello.", speaker_id="SPEAKER_00"
        ),
        TranscriptionSegment(
            start_time=2.5, end_time=4.0, text="Hi.", speaker_id="SPEAKER_01"
        ),
    ]

    def test_webvtt(self, processor):
        vtt = processor._generate_webvtt(self.SEGMENTS)
        assert "<v SPEAKER_00>Hello." in vtt
        assert "<v SPEAKER_01>Hi." in vtt

    def test_srt(self, processor):
        srt = processor._generate_srt(self.SEGMENTS)
        assert "[SPEAKER_00]: Hello." in srt
        assert "[SPEAKER_01]: Hi." in srt

    def test_transcript(self, processor):
        transcript = processor._generate_full_transcript(self.SEGMENTS)
        assert "[00:00] SPEAKER_00:\n  Hello." in transcript
        assert "[00:02] SPEAKER_01:\n  Hi." in transcript
