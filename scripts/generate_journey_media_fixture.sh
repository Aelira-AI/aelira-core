#!/usr/bin/env bash
# Regenerate tests/journeys/fixtures/teaching-clip.mp4: a short synthetic
# teaching clip. The speech comes from the Piper voice the product ships, run
# from a built API image; the picture is a plain colour card. No recorded
# person, no third-party media.
#
#   scripts/generate_journey_media_fixture.sh <api-image>
set -euo pipefail
image="${1:?usage: $0 <api-image>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
docker run --rm --entrypoint python -v "$work:/out" "$image" -c '
import wave
from pathlib import Path
from piper import PiperVoice
model = Path("/app/data/piper-voices/en_US-lessac-medium.onnx")
voice = PiperVoice.load(model, config_path=model.with_suffix(".onnx.json"), use_cuda=False)
text = "Welcome to the biology lab. Today we measure plant growth and record the results in a table."
with wave.open("/out/speech.wav", "wb") as target:
    voice.synthesize_wav(text, target)
'
ffmpeg -y -loglevel error \
  -f lavfi -i "color=c=0x1f3a5f:s=320x180:r=5" -i "$work/speech.wav" \
  -map_metadata -1 -shortest -c:v libx264 -preset veryslow -crf 32 -pix_fmt yuv420p \
  -c:a aac -b:a 48k -ar 16000 -ac 1 -movflags +faststart \
  "$root/tests/journeys/fixtures/teaching-clip.mp4"
ls -l "$root/tests/journeys/fixtures/teaching-clip.mp4"
