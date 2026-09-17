#!/usr/bin/env python3
"""Synthesize real PCM audio using the final image's already-baked Piper voice."""

from __future__ import annotations

import io
import wave
from pathlib import Path

VOICE_PATH = (
    Path(__file__).resolve().parents[1] / "data/piper-voices/en_US-lessac-medium.onnx"
)


def synthesize(model_path: Path = VOICE_PATH) -> tuple[int, int]:
    """Return sample rate and frame count after checking synthesized WAV audio."""
    config_path = model_path.with_suffix(".onnx.json")
    for path in (model_path, config_path):
        if not path.is_file():
            raise RuntimeError(f"Baked Piper voice asset is missing: {path}")

    from piper import PiperVoice

    voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        voice.synthesize_wav("Aelira makes learning accessible.", wav_file)

    output.seek(0)
    with wave.open(output, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        audio = wav_file.readframes(frames)
        if (
            wav_file.getcomptype() != "NONE"
            or channels != 1
            or sample_width != 2
            or sample_rate <= 0
            or frames <= 0
            or len(audio) != frames * channels * sample_width
            or not any(audio)
        ):
            raise RuntimeError(
                "Piper did not synthesize non-silent mono PCM16 WAV audio"
            )
    return sample_rate, frames


def main() -> None:
    sample_rate, frames = synthesize()
    print(f"Piper runtime verified: {frames} WAV frames at {sample_rate} Hz")


if __name__ == "__main__":
    main()
