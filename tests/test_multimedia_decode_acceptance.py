"""Exercise installed native media wheels through the application's consumers."""

import hashlib
import math
import struct
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import av
import cv2
from src.education.media_inputs import restrict_opencv_capture
from src.education.multimedia_processor import MultimediaProcessor

SCENE_FIXTURES = {
    "h264.mp4": "4183e7c90cfd890f37a9c184c51d58b52fecd80db683eae2e77d4013b071dc22",
    "hevc.mkv": "c11be4cab3ffe3a4011214ec5ac2e52cb2630a7676c665ad376a92004cf828b2",
    "vp8.webm": "a9840ab18740e6eef10e39c053533bfcd1d0dff22c1d088ac7e91cda84a703c7",
    "vp9.webm": "c87830dffb43099a128549f0c64d12e1d8840399659888350319e74f3828bf3e",
    "mpeg4.avi": "57fffa62554ffcb09f61d4b1f8abc4acb86effeb1972f4279bc8d73339a73101",
    "mjpeg.avi": "04691a379ca0431670cce34589b273c577c2837c7ecff3c0460c1fb891085a20",
    "av1.mp4": "501deca2b190ca261067192df48b9b11c85066e92a39c44e4bb033afbfef7b57",
}


def test_pyav_decodes_and_resamples_audio_for_transcription(tmp_path):
    from faster_whisper.audio import decode_audio

    sample_rate = 8_000
    duration_seconds = 0.4
    frame_count = int(sample_rate * duration_seconds)
    audio_path = tmp_path / "transcription-input.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            b"".join(
                struct.pack(
                    "<h", int(10_000 * math.sin(2 * math.pi * 440 * i / sample_rate))
                )
                for i in range(frame_count)
            )
        )

    with av.open(str(audio_path)) as container:
        assert len(container.streams.audio) == 1
        assert container.streams.audio[0].rate == sample_rate
        assert math.isclose(float(container.duration / av.time_base), duration_seconds)

    samples = decode_audio(str(audio_path), sampling_rate=16_000)
    assert len(samples) == 16_000 * duration_seconds
    assert samples.max() > 0.2
    assert samples.min() < -0.2


def check_source_opencv_decodes_scene_codecs_and_real_cuts(
    fixture_root: Path = Path("tests/fixtures/scene_codecs"),
) -> None:
    """All accepted video codecs must decode frames and reach the real app path."""
    processor = MultimediaProcessor(use_gemini=False)
    for name, digest in SCENE_FIXTURES.items():
        path = fixture_root / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        restrict_opencv_capture()
        capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        try:
            assert capture.isOpened(), name
            assert capture.get(cv2.CAP_PROP_BACKEND) == cv2.CAP_FFMPEG, name
            count = 0
            while True:
                decoded, frame = capture.read()
                if not decoded:
                    break
                assert frame.shape == (96, 160, 3), name
                count += 1
        finally:
            capture.release()
        assert count == 90, (name, count)
        starts = processor._detect_scene_changes_pyscenedetect(str(path))
        assert starts == [0.0, 3.0, 6.0], (name, starts)


def test_opencv_distinguishes_single_scene_from_zero_frames(
    fixture_root: Path = Path("tests/fixtures/scene_codecs"),
) -> None:
    processor = MultimediaProcessor(use_gemini=False)
    path = fixture_root / "single-h264.mp4"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "3386fe7f37732c4cd28a2dcd34e86a995e6b394d7c491920c500b6660daceb92"
    )
    assert processor._detect_scene_changes_pyscenedetect(str(path)) == [0.0]

    class EmptyCapture:
        def isOpened(self):
            return True

        def read(self):
            return False, None

        def release(self):
            pass

    with patch.object(cv2, "VideoCapture", return_value=EmptyCapture()):
        with patch(
            "scenedetect.detect",
            side_effect=AssertionError("must not accept zero frames"),
        ):
            assert processor._detect_scene_changes_pyscenedetect(str(path)) is None

    with patch.object(
        processor, "_detect_scene_changes_pyscenedetect", return_value=None
    ):
        with patch(
            "src.education.multimedia_processor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="0\n", stderr=""),
        ):
            assert processor._detect_scene_changes(str(path), 3.0) == []
        with patch(
            "src.education.multimedia_processor.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout="30\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ],
        ):
            assert processor._detect_scene_changes(str(path), 3.0) == [0.0]


def test_pillow_keeps_image_decode_without_opencv_image_backends(tmp_path):
    """Normal image input remains available through the image consumer."""
    from PIL import Image, features

    assert features.check("libtiff")
    source = Image.new("RGB", (8, 8), (32, 96, 160))
    for suffix, format_name in (("jpg", "JPEG"), ("png", "PNG"), ("tiff", "TIFF")):
        path = tmp_path / f"image.{suffix}"
        source.save(path, format=format_name)
        with Image.open(path) as decoded:
            assert decoded.size == (8, 8)
            assert decoded.convert("RGB").getpixel((0, 0))[1] >= 90
