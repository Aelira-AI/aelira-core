#!/usr/bin/env python3
"""Transcribe synthesized speech with a model fetched through huggingface-hub.

The ``download`` phase needs network access: it fetches the small
``tiny.en`` faster-whisper model through the installed huggingface-hub into
``--cache-dir`` and transcribes speech synthesized by the baked Piper voice.
The ``offline`` phase must work with the network unavailable: it sets
``HF_HUB_OFFLINE=1`` before huggingface-hub is imported, requires that
setting to be in force, and loads the same model from the cache alone.
The ``offline-refused`` phase is the negative control: with an empty cache and
``HF_HUB_OFFLINE=1`` the load must fail, which shows the offline phase can only
succeed by reading the cache.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
import wave
from pathlib import Path

MODEL = "tiny.en"
SPEECH = "Good captions make learning accessible for every student."
EXPECTED_WORDS = ("captions", "learning", "accessible", "every", "student")
VOICE_PATH = (
    Path(__file__).resolve().parents[1] / "data/piper-voices/en_US-lessac-medium.onnx"
)


def normalize(text: str) -> str:
    """Lowercase and strip punctuation so assertions ignore casing and commas."""
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def missing_words(text: str, expected: tuple[str, ...] = EXPECTED_WORDS) -> list[str]:
    """Return the expected words absent from a transcript."""
    words = set(normalize(text).split())
    return [word for word in expected if word not in words]


def synthesize(path: Path, voice_path: Path = VOICE_PATH) -> None:
    """Write real synthesized speech as a mono PCM16 WAV file."""
    config_path = voice_path.with_suffix(".onnx.json")
    for asset in (voice_path, config_path):
        if not asset.is_file():
            raise RuntimeError(f"Piper voice asset is missing: {asset}")

    from piper import PiperVoice

    voice = PiperVoice.load(voice_path, config_path=config_path, use_cuda=False)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(SPEECH, wav_file)
    path.write_bytes(buffer.getvalue())


def transcribe(audio_path: Path, cache_dir: Path) -> str:
    """Load the model through huggingface-hub's cache and transcribe the clip."""
    import huggingface_hub
    from faster_whisper import WhisperModel

    model = WhisperModel(
        MODEL,
        device="cpu",
        compute_type="int8",
        download_root=str(cache_dir),
    )
    segments, info = model.transcribe(str(audio_path), beam_size=5, language="en")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    print(f"huggingface_hub {huggingface_hub.__version__}")
    print(f"model: {MODEL}, language: {info.language}")
    print(f"transcript: {text}")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("download", "offline", "offline-refused"))
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--voice", type=Path, default=VOICE_PATH)
    args = parser.parse_args()

    if args.phase in ("offline", "offline-refused"):
        os.environ["HF_HUB_OFFLINE"] = "1"
        import huggingface_hub.constants as hub_constants

        if not hub_constants.HF_HUB_OFFLINE:
            sys.exit("huggingface-hub did not honour HF_HUB_OFFLINE=1")
    else:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as scratch:
        clip = Path(scratch) / "speech.wav"
        synthesize(clip, args.voice)
        if args.phase == "offline-refused":
            try:
                transcribe(clip, args.cache_dir)
            except Exception as error:
                print(f"offline load refused as expected: {type(error).__name__}")
                return
            sys.exit("an empty cache loaded a model with HF_HUB_OFFLINE=1")
        text = transcribe(clip, args.cache_dir)

    absent = missing_words(text)
    if absent:
        sys.exit(f"transcript is missing expected words {absent}: {text!r}")
    print(f"whisper transcription verified ({args.phase})")


if __name__ == "__main__":
    main()
