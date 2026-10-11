"""Exercise the installed PyAV decoder through faster-whisper's real audio path."""

import math
import struct
import wave

import av
from faster_whisper.audio import decode_audio


def test_pyav_decodes_and_resamples_audio_for_transcription(tmp_path):
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
