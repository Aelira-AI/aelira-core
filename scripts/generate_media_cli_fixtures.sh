#!/usr/bin/env bash
# Regenerate tests/fixtures/media_cli/, the inputs of scripts/smoke_media_cli.py.
# They are synthetic: colour cards and sine tones, no recorded media.
#
#   scripts/generate_media_cli_fixtures.sh [ffmpeg]
#
# Use a full-featured ffmpeg (Debian's package or a distribution build), not
# the image's own minimal build, so the files under test are not produced by
# the binary under test. Update the SHA-256 pins in scripts/smoke_media_cli.py
# after regenerating.
set -euo pipefail
ffmpeg="${1:-ffmpeg}"
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/tests/fixtures/media_cli"
mkdir -p "$out"

# lecture.mp4: 4 s of H.264 video that cuts from red to blue at 2.0 s, with
# stereo 44.1 kHz AAC audio: tone, silence from 1.0 s to 2.5 s, tone.
"$ffmpeg" -y -loglevel error -bitexact \
  -f lavfi -i "color=c=red:s=160x120:r=5:d=2[a];color=c=blue:s=160x120:r=5:d=2[b];[a][b]concat=n=2:v=1:a=0" \
  -f lavfi -i "aevalsrc=if(lt(t\,1)+gte(t\,2.5)\,0.5*sin(2*PI*440*t)\,0)|if(lt(t\,1)+gte(t\,2.5)\,0.5*sin(2*PI*440*t)\,0):s=44100:d=4" \
  -map_metadata -1 -c:v libx264 -preset veryslow -crf 30 -pix_fmt yuv420p -g 5 \
  -c:a aac -b:a 32k -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -movflags +faststart "$out/lecture.mp4"

# refused-codec.mkv: 1 s of MPEG-2 video. MPEG-2 is not an accepted upload
# codec, so every application command must refuse to decode it. The decoder
# exists in both Debian's and the image's FFmpeg, so the refusal comes from
# the application's codec whitelist, not from a missing decoder.
"$ffmpeg" -y -loglevel error -bitexact \
  -f lavfi -i "color=c=green:s=32x32:r=5:d=1" \
  -map_metadata -1 -c:v mpeg2video -fflags +bitexact -flags:v +bitexact \
  "$out/refused-codec.mkv"

sha256sum "$out/lecture.mp4" "$out/refused-codec.mkv"
