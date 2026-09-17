"""Exercise WAV checks without downloading voices or loading an inference model."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.smoke_piper_runtime import synthesize


@pytest.mark.parametrize("asset", ["model", "config"])
def test_piper_smoke_requires_baked_assets(tmp_path: Path, asset: str) -> None:
    model = tmp_path / "voice.onnx"
    if asset == "config":
        model.write_bytes(b"model")
    with pytest.raises(RuntimeError, match="Baked Piper voice asset is missing"):
        synthesize(model)


@pytest.mark.parametrize("audio", [b"\x01\x00" * 100, b"", b"\x00\x00" * 100])
def test_piper_smoke_validates_synthesized_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audio: bytes
) -> None:
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"model")
    config = model.with_suffix(".onnx.json")
    config.write_text("{}")

    class Voice:
        @staticmethod
        def load(model_path, *, config_path, use_cuda):
            assert model_path == model
            assert config_path == config
            assert use_cuda is False
            return Voice()

        def synthesize_wav(self, text, wav_file):
            assert text.strip()
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            wav_file.writeframes(audio)

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=Voice))
    if audio and any(audio):
        assert synthesize(model) == (22050, 100)
    else:
        with pytest.raises(RuntimeError, match="non-silent mono PCM16 WAV"):
            synthesize(model)
