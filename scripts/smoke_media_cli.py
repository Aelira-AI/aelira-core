#!/usr/bin/env python3
"""Exercise every ffmpeg and ffprobe call site against the final image's CLI.

The API image ships ffmpeg and ffprobe from its own FFmpeg 9.0.2 build, which
compiles in only the file and pipe protocols. The repository's other media
tests run on the CI runner's ffmpeg, so this script is the evidence that the
shipped programs still do what the application asks of them. It calls the
real application methods on small hash-pinned fixtures (see
scripts/generate_media_cli_fixtures.sh) and checks their results by probing
the outputs. Negative controls prove that network inputs and a refused codec
fail, and that each output check can tell a wrong result from a right one.

Run inside the image, without network:

    docker run --rm --network none --read-only \\
      --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \\
      --volume "$PWD/tests/fixtures/media_cli:/fixtures:ro" \\
      --entrypoint /opt/venv/bin/python IMAGE \\
      /app/scripts/smoke_media_cli.py /fixtures

It prints one JSON object of observations and exits 1 if any check fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from src.education.media_inputs import local_media_command  # noqa: E402
from src.education.multimedia_processor import MultimediaProcessor  # noqa: E402
from src.education.speaker_diarization import SpeakerDiarizer  # noqa: E402
from src.education.tts_processor import (  # noqa: E402
    AudioFormat,
    PiperTTSEngine,
    TTSProcessor,
)

FIXTURES = {
    # 4 s H.264 160x120 at 5 fps, red until 2.0 s then blue; stereo 44.1 kHz
    # AAC with a 440 Hz tone, silent from 1.0 s to 2.5 s.
    "lecture.mp4": "7e6405b4ac1814dbafa0a835a3cbdeb877b90c407a64d18b251a23e6e8be6793",
    # 1 s of MPEG-2 video; MPEG-2 is not an accepted upload codec.
    "refused-codec.mkv": "594b6c89e732c7b9969d8b65659962e254ef8b3904b4a55ecc886b6d631408cf",
}
DURATION = 4.0
CUT = 2.0
SILENCE = (1.0, 2.5)
TOLERANCE = 0.15
SRT = "1\n00:00:00,000 --> 00:00:01,000\nA tone plays.\n\n2\n00:00:02,500 --> 00:00:04,000\nThe tone returns.\n"
PLAYLIST = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n"
    "#EXTINF:4.0,\nhttp://127.0.0.1:9/segment.ts\n#EXT-X-ENDLIST\n"
)


class Smoke:
    def __init__(self) -> None:
        self.observed: dict[str, Any] = {}
        self.failures: list[str] = []

    def record(self, name: str, value: Any) -> Any:
        self.observed[name] = value
        return value

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)


def near(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) <= TOLERANCE


def probe(path: Path) -> dict[str, Any]:
    """Describe an output file with ffprobe. Outputs are trusted local files."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_name,codec_type,"
            "sample_rate,channels,width,height:stream_tags=title,language",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    streams = [
        {
            key: stream[key]
            for key in (
                "codec_type",
                "codec_name",
                "sample_rate",
                "channels",
                "width",
                "height",
            )
            if key in stream
        }
        | (
            {"title": stream["tags"]["title"]}
            if "title" in stream.get("tags", {})
            else {}
        )
        for stream in data.get("streams", [])
    ]
    duration = data.get("format", {}).get("duration")
    return {
        "format": data.get("format", {}).get("format_name"),
        "duration": round(float(duration), 2) if duration else None,
        "streams": streams,
    }


def codecs(description: dict[str, Any]) -> list[str]:
    return [stream.get("codec_name", "?") for stream in description["streams"]]


def centre_colour(path: Path) -> str:
    with Image.open(path) as image:
        red, green, blue = image.convert("RGB").getpixel(
            (image.width // 2, image.height // 2)
        )
    if red > 180 and green < 80 and blue < 80:
        return "red"
    if blue > 180 and red < 80 and green < 80:
        return "blue"
    return f"rgb({red},{green},{blue})"


def run(smoke: Smoke, fixtures: Path, work: Path) -> None:
    for name, digest in FIXTURES.items():
        actual = hashlib.sha256((fixtures / name).read_bytes()).hexdigest()
        smoke.check(actual == digest, f"fixture {name} sha256 {actual} != {digest}")
    lecture = work / "lecture.mp4"
    shutil.copyfile(fixtures / "lecture.mp4", lecture)
    refused = work / "refused-codec.mkv"
    shutil.copyfile(fixtures / "refused-codec.mkv", refused)

    processor = MultimediaProcessor()
    # Force the ffprobe/ffmpeg scene fallback, the path that uses the CLI.
    processor._detect_scene_changes_pyscenedetect = lambda _path: None

    # 1. ffprobe media type and duration.
    kind, duration = smoke.record(
        "media_info.lecture", list(processor._get_media_info(str(lecture)))
    )
    smoke.check(
        kind == "video" and near(duration, DURATION), f"media info {kind} {duration}"
    )

    # 2. ffprobe embedded caption check.
    captions = smoke.record(
        "captions.lecture", processor._check_existing_captions(str(lecture))
    )
    smoke.check(captions is False, f"lecture reports captions: {captions}")

    # 3, 4. Audio stream probe and 16 kHz mono WAV extraction for Whisper.
    wav = Path(processor._extract_audio(str(lecture)))
    wav_info = smoke.record("extract_audio.wav", probe(wav))
    smoke.check(
        wav_info["streams"]
        == [
            {
                "codec_type": "audio",
                "codec_name": "pcm_s16le",
                "sample_rate": "16000",
                "channels": 1,
            }
        ]
        and near(wav_info["duration"], DURATION),
        f"extracted WAV is {wav_info}",
    )
    smoke.record("media_info.wav", list(processor._get_media_info(str(wav))))
    smoke.check(
        smoke.observed["media_info.wav"][0] == "audio",
        f"WAV media type {smoke.observed['media_info.wav']}",
    )
    silent_video = work / "no-audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(lecture),
            "-an",
            "-c:v",
            "copy",
            str(silent_video),
        ],
        check=True,
    )
    no_audio = smoke.record(
        "extract_audio.no_audio", processor._extract_audio(str(silent_video))
    )
    smoke.check(
        no_audio is None, f"audio extracted from a video without audio: {no_audio}"
    )

    # 6, 7. Decoded frame count and select+showinfo scene timestamps.
    scenes = smoke.record(
        "scene_changes.lecture",
        [
            round(value, 2)
            for value in processor._detect_scene_changes(str(lecture), DURATION)
        ],
    )
    smoke.check(
        len(scenes) == 2 and scenes[0] == 0.0 and near(scenes[1], CUT),
        f"scene changes {scenes}; expected [0.0, {CUT}]",
    )

    # 5. JPEG keyframes at the scene timestamps.
    keyframes = processor._extract_keyframes(str(lecture), DURATION, 2.0)
    frames = smoke.record(
        "keyframes.lecture",
        [
            [
                round(timestamp, 2),
                Path(path).read_bytes()[:2].hex(),
                centre_colour(Path(path)),
            ]
            for timestamp, path in keyframes
        ],
    )
    smoke.check(
        [frame[1:] for frame in frames] == [["ffd8", "red"], ["ffd8", "blue"]],
        f"keyframes {frames}; expected a red then a blue JPEG",
    )

    # 9, 10. Duration probe and silencedetect parsing for diarization.
    diarizer = SpeakerDiarizer()
    gaps = smoke.record(
        "silence_gaps.wav",
        [
            [round(start, 2), round(end, 2)]
            for start, end in diarizer._detect_silence_gaps(str(wav))
        ],
    )
    smoke.check(
        len(gaps) == 1
        and near(gaps[0][0], SILENCE[0])
        and near(gaps[0][1], SILENCE[1]),
        f"silence gaps {gaps}; expected one from {SILENCE[0]} to {SILENCE[1]}",
    )
    diarized = diarizer._diarize_with_heuristics(str(wav))
    smoke.record(
        "diarization.wav",
        {
            "total_duration": round(diarized.total_duration, 2),
            "segments": len(diarized.segments),
        },
    )
    smoke.check(
        near(diarized.total_duration, DURATION) and len(diarized.segments) == 2,
        f"diarization {smoke.observed['diarization.wav']}",
    )

    # 11. Piper WAV conversion to MP3, Ogg and FLAC.
    wav_bytes = wav.read_bytes()
    engine = PiperTTSEngine()
    expected_codecs = {
        AudioFormat.MP3: "mp3",
        AudioFormat.OGG: "vorbis",
        AudioFormat.FLAC: "flac",
    }
    converted: dict[str, Path] = {}
    for audio_format, expected_codec in expected_codecs.items():
        output = work / f"speech.{audio_format.value}"
        output.write_bytes(engine._convert_audio(wav_bytes, audio_format))
        converted[audio_format.value] = output
        info = smoke.record(f"convert_audio.{audio_format.value}", probe(output))
        smoke.check(
            codecs(info) == [expected_codec] and near(info["duration"], DURATION),
            f"{audio_format.value} conversion is {info}; expected {expected_codec}",
        )

    # 12. concat demuxer to MP3 with libmp3lame.
    joined = work / "joined.mp3"
    TTSProcessor()._concatenate_audio([wav_bytes, wav_bytes], str(joined))
    joined_info = smoke.record("concatenate_audio.mp3", probe(joined))
    smoke.check(
        codecs(joined_info) == ["mp3"] and near(joined_info["duration"], 2 * DURATION),
        f"concatenated audio is {joined_info}",
    )

    # 8. Accessible video: MP4 with mov_text, MKV with SRT and a second track.
    subtitles = work / "captions.srt"
    subtitles.write_text(SRT)
    mp4 = processor.create_accessible_video(
        str(lecture),
        subtitle_path=str(subtitles),
        output_path=str(work / "accessible.mp4"),
    )
    mp4_info = smoke.record("accessible_video.mp4", probe(Path(mp4)) if mp4 else None)
    smoke.check(
        mp4_info is not None
        and codecs(mp4_info) == ["h264", "aac", "mov_text"]
        and mp4_info["streams"][0].get("width") == 160,
        f"accessible MP4 is {mp4_info}",
    )
    mkv = processor.create_accessible_video(
        str(lecture),
        subtitle_path=str(subtitles),
        audio_description_path=str(converted["mp3"]),
        output_path=str(work / "accessible.mkv"),
    )
    mkv_info = smoke.record("accessible_video.mkv", probe(Path(mkv)) if mkv else None)
    smoke.check(
        mkv_info is not None
        and codecs(mkv_info) == ["h264", "aac", "aac", "subrip"]
        and [stream.get("title") for stream in mkv_info["streams"]]
        == [None, "Original Audio", "Audio Descriptions", "Captions"],
        f"accessible MKV is {mkv_info}",
    )

    # Negative controls: the build has no network protocol at all, so even
    # a command without the application's whitelist cannot open a URL.
    protocols = subprocess.run(
        ["ffmpeg", "-hide_banner", "-protocols"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    smoke.record("protocols", protocols)
    smoke.check(
        protocols
        == [
            "Supported",
            "file",
            "protocols:",
            "Input:",
            "file",
            "pipe",
            "Output:",
            "file",
            "pipe",
        ],
        f"ffmpeg protocols {protocols}",
    )
    url = "http://127.0.0.1:9/lecture.mp4"
    raw = subprocess.run(
        ["ffprobe", "-v", "error", url], capture_output=True, text=True, timeout=30
    )
    smoke.record("refusal.raw_http", [raw.returncode, raw.stderr.strip()[-200:]])
    smoke.check(
        raw.returncode != 0 and "Protocol not found" in raw.stderr,
        f"raw http input was not refused at compile time: {raw.returncode} {raw.stderr!r}",
    )
    smoke.record("refusal.app_http", list(processor._get_media_info(url)))
    smoke.check(
        smoke.observed["refusal.app_http"] == ["unknown", 0.0],
        f"application probed a URL: {smoke.observed['refusal.app_http']}",
    )
    playlist = work / "remote.m3u8"
    playlist.write_text(PLAYLIST)
    hls = subprocess.run(
        ["ffprobe", "-v", "error", str(playlist)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    smoke.record("refusal.raw_hls_segment", [hls.returncode, hls.stderr.strip()[-200:]])
    smoke.check(
        hls.returncode != 0,
        f"HLS playlist with a network segment opened: {hls.stderr!r}",
    )
    smoke.record("refusal.app_hls", list(processor._get_media_info(str(playlist))))
    smoke.check(
        smoke.observed["refusal.app_hls"] == ["unknown", 0.0],
        f"application opened an HLS playlist: {smoke.observed['refusal.app_hls']}",
    )
    smoke.record(
        "refusal.codec_keyframes", processor._extract_keyframes(str(refused), 1.0, 1.0)
    )
    smoke.check(
        smoke.observed["refusal.codec_keyframes"] == [],
        f"refused codec decoded: {smoke.observed['refusal.codec_keyframes']}",
    )
    refused_audio = subprocess.run(
        local_media_command(
            ["ffmpeg", "-v", "error", "-i", str(refused), "-f", "null", "-"]
        ),
        capture_output=True,
        text=True,
    )
    smoke.record(
        "refusal.codec_decode",
        [refused_audio.returncode, refused_audio.stderr.strip()[-200:]],
    )
    smoke.check(
        refused_audio.returncode != 0 and "not on whitelist" in refused_audio.stderr,
        f"MPEG-2 decode was not refused by the whitelist: {refused_audio.returncode} {refused_audio.stderr!r}",
    )

    # The decoder exists, so the refusal above is the whitelist's doing.
    unrestricted = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(refused), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    smoke.check(
        unrestricted.returncode == 0,
        f"MPEG-2 control does not decode without the whitelist: {unrestricted.stderr!r}",
    )

    # Controls for the output checks: each must see a wrong result as wrong.
    flac_in_ogg = work / "flac-in.ogg"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "flac", str(flac_in_ogg)],
        check=True,
    )
    smoke.check(
        codecs(probe(flac_in_ogg)) == ["flac"], "Ogg codec check cannot see FLAC"
    )
    smoke.check(
        centre_colour(Path(keyframes[0][1])) != centre_colour(Path(keyframes[-1][1])),
        "keyframe colour check cannot tell the two scenes apart",
    )
    tone = work / "tone.wav"
    with wave.open(str(tone), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
                for i in range(16000 * 2)
            )
        )
    smoke.check(
        diarizer._detect_silence_gaps(str(tone)) == [],
        "silence check reports a gap in a continuous tone",
    )
    for _timestamp, path in keyframes:
        os.unlink(path)
    os.unlink(wav)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: smoke_media_cli.py FIXTURE_DIR", file=sys.stderr)
        return 2
    logging.disable(logging.CRITICAL)
    smoke = Smoke()
    with tempfile.TemporaryDirectory(prefix="media-cli-") as directory:
        try:
            run(smoke, Path(argv[1]), Path(directory))
        except Exception as exc:  # noqa: BLE001 - report every failure the same way
            smoke.failures.append(f"smoke aborted: {type(exc).__name__}: {exc}")
    print(json.dumps(smoke.observed, indent=2, sort_keys=True, default=str))
    for failure in smoke.failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if smoke.failures:
        return 1
    print("media CLI call sites verified", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
