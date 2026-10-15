"""Media decoders only open local files in the formats Aelira accepts."""

import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.education.media_inputs import (
    OPENCV_CAPTURE_OPTIONS,
    UPLOAD_CODECS,
    UPLOAD_FORMATS,
    local_media_command,
    restrict_opencv_capture,
)

RESTRICTIONS = [
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    ",".join(UPLOAD_FORMATS),
    "-codec_whitelist",
    ",".join(UPLOAD_CODECS),
]
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


def test_ffprobe_input_is_restricted():
    assert local_media_command(["ffprobe", "-v", "error", "in.mp4"]) == [
        "ffprobe",
        *RESTRICTIONS,
        "-v",
        "error",
        "in.mp4",
    ]


def test_every_ffmpeg_input_is_restricted():
    cmd = ["ffmpeg", "-y", "-ss", "3", "-i", "a.mp4", "-i", "b.wav", "out.mp4"]
    assert local_media_command(cmd) == [
        "ffmpeg",
        "-y",
        "-ss",
        "3",
        *RESTRICTIONS,
        "-i",
        "a.mp4",
        *RESTRICTIONS,
        "-i",
        "b.wav",
        "out.mp4",
    ]


def test_callers_can_extend_formats_and_codecs_for_generated_inputs():
    restricted = local_media_command(
        ["ffmpeg", "-i", "a.mp4", "out.mp4"],
        extra_formats=("srt",),
        extra_codecs=("subrip",),
    )
    assert restricted[restricted.index("-format_whitelist") + 1].endswith(",srt")
    assert restricted[restricted.index("-codec_whitelist") + 1].endswith(",subrip")


def test_absolute_tool_paths_are_recognised():
    assert local_media_command(["/usr/bin/ffprobe", "x"])[1:7] == RESTRICTIONS


@pytest.mark.parametrize("cmd", [[], ["ffmpeg", "-y", "out.wav"], ["sox", "-i", "a"]])
def test_unsupported_commands_are_refused(cmd):
    with pytest.raises(ValueError):
        local_media_command(cmd)


def test_opencv_capture_receives_the_same_restrictions(monkeypatch):
    monkeypatch.setenv(OPENCV_CAPTURE_OPTIONS, "protocol_whitelist;file,http,https")
    restrict_opencv_capture()
    assert os.environ[OPENCV_CAPTURE_OPTIONS] == "|".join(
        (
            "protocol_whitelist;file",
            "format_whitelist;" + ",".join(UPLOAD_FORMATS),
            "codec_whitelist;" + ",".join(UPLOAD_CODECS),
        )
    )


@pytest.fixture
def request_log():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], requests
    finally:
        server.shutdown()
        server.server_close()


@needs_ffmpeg
def test_local_media_never_opens_network_references(tmp_path, request_log):
    port, requests = request_log

    # Control: the probe observes ffmpeg when it is pointed at the server directly.
    direct = f"http://127.0.0.1:{port}/direct.ts"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-i", direct, "-f", "null", "-"],
        capture_output=True,
        timeout=30,
    )
    assert requests == ["/direct.ts"], "the probe cannot observe ffmpeg requests"

    requests.clear()
    playlist = tmp_path / "lecture.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
        f"http://127.0.0.1:{port}/segment.ts\n#EXT-X-ENDLIST\n"
    )
    cmd = ["ffmpeg", "-nostdin", "-i", str(playlist), "-f", "null", "-"]
    for attempt in (cmd, local_media_command(cmd)):
        result = subprocess.run(attempt, capture_output=True, timeout=30)
        assert result.returncode != 0
    assert requests == []


VIDEO = ["-f", "lavfi", "-i", "testsrc=d=1:s=64x48"]
AUDIO = ["-f", "lavfi", "-i", "sine=d=1"]
ACCEPTED_SAMPLES = {
    "mpeg4_aac.mp4": [*VIDEO, *AUDIO, "-c:v", "mpeg4", "-c:a", "aac"],
    "mjpeg.avi": [*VIDEO, "-c:v", "mjpeg"],
    "mpeg4_flac.mkv": [*VIDEO, *AUDIO, "-c:v", "mpeg4", "-c:a", "flac"],
    "audio.wav": [*AUDIO],
    "audio.flac": [*AUDIO],
}
REFUSED_SAMPLES = {
    "cfhd.mov": ([*VIDEO, "-c:v", "cfhd"], "Codec (cfhd) not on whitelist"),
    "png.mov": ([*VIDEO, "-c:v", "png"], "Codec (png) not on whitelist"),
    "dirac.mkv": (
        [*VIDEO, "-c:v", "vc2", "-strict", "-1"],
        "Codec (dirac) not on whitelist",
    ),
    "audio.caf": ([*AUDIO, "-f", "caf"], "Format not on whitelist"),
    "tv.wtv": (
        [*VIDEO, *AUDIO, "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "wtv"],
        "Format not on whitelist",
    ),
}


def _make_sample(tmp_path, name, args):
    path = tmp_path / name
    made = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert made.returncode == 0, f"could not create {name}: {made.stderr}"
    return str(path)


def _decode(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


@needs_ffmpeg
@pytest.mark.parametrize("name", sorted(ACCEPTED_SAMPLES))
def test_accepted_media_still_decodes(tmp_path, name):
    sample = _make_sample(tmp_path, name, ACCEPTED_SAMPLES[name])
    decoded = _decode(
        local_media_command(["ffmpeg", "-nostdin", "-i", sample, "-f", "null", "-"])
    )
    assert decoded.returncode == 0, decoded.stderr
    probed = _decode(local_media_command(["ffprobe", "-v", "error", sample]))
    assert probed.returncode == 0, probed.stderr


@needs_ffmpeg
@pytest.mark.parametrize("name", sorted(REFUSED_SAMPLES))
def test_other_formats_and_codecs_are_refused_before_decoding(tmp_path, name):
    args, refusal = REFUSED_SAMPLES[name]
    sample = _make_sample(tmp_path, name, args)
    cmd = ["ffmpeg", "-nostdin", "-i", sample, "-f", "null", "-"]

    # Control: without the restriction this ffmpeg really decodes the sample.
    assert _decode(cmd).returncode == 0

    restricted = _decode(local_media_command(cmd))
    assert restricted.returncode != 0
    assert refusal in restricted.stderr
    probed = _decode(local_media_command(["ffprobe", "-v", "error", sample]))
    assert probed.returncode != 0
    assert refusal in probed.stderr


@needs_ffmpeg
def test_concat_extension_reads_generated_audio_parts(tmp_path):
    parts = [_make_sample(tmp_path, f"part{i}.wav", AUDIO) for i in range(2)]
    listing = tmp_path / "concat.txt"
    listing.write_text("".join(f"file '{part}'\n" for part in parts))
    cmd = ["ffmpeg", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing)]
    cmd += ["-f", "null", "-"]
    assert _decode(local_media_command(cmd)).returncode != 0
    joined = _decode(local_media_command(cmd, extra_formats=("concat",)))
    assert joined.returncode == 0, joined.stderr
