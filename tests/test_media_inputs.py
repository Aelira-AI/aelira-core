"""Media decoders must only open the local files the application supplies."""

import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.education.media_inputs import (
    OPENCV_CAPTURE_OPTIONS,
    local_media_command,
    restrict_opencv_capture,
)


def test_ffprobe_input_is_limited_to_local_files():
    assert local_media_command(["ffprobe", "-v", "error", "in.mp4"]) == [
        "ffprobe",
        "-protocol_whitelist",
        "file",
        "-v",
        "error",
        "in.mp4",
    ]


def test_every_ffmpeg_input_is_limited_to_local_files():
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "3",
        "-i",
        "a.mp4",
        "-i",
        "b.srt",
        "-map",
        "0:v",
        "out.mp4",
    ]
    assert local_media_command(cmd) == [
        "ffmpeg",
        "-y",
        "-ss",
        "3",
        "-protocol_whitelist",
        "file",
        "-i",
        "a.mp4",
        "-protocol_whitelist",
        "file",
        "-i",
        "b.srt",
        "-map",
        "0:v",
        "out.mp4",
    ]


def test_absolute_tool_paths_are_recognised():
    assert local_media_command(["/usr/bin/ffprobe", "x"])[1:3] == [
        "-protocol_whitelist",
        "file",
    ]


@pytest.mark.parametrize("cmd", [[], ["ffmpeg", "-y", "out.wav"], ["sox", "-i", "a"]])
def test_unsupported_commands_are_refused(cmd):
    with pytest.raises(ValueError):
        local_media_command(cmd)


def test_opencv_capture_is_limited_to_local_files(monkeypatch):
    monkeypatch.setenv(OPENCV_CAPTURE_OPTIONS, "protocol_whitelist;file,http,https")
    restrict_opencv_capture()
    assert os.environ[OPENCV_CAPTURE_OPTIONS] == "protocol_whitelist;file"


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


def _playlist(tmp_path, port):
    playlist = tmp_path / "lecture.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
        f"http://127.0.0.1:{port}/segment.ts\n#EXT-X-ENDLIST\n"
    )
    return str(playlist)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_local_media_never_opens_network_references(tmp_path, request_log):
    port, requests = request_log

    # Control: the probe observes ffmpeg when it is pointed at the server directly.
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            f"http://127.0.0.1:{port}/direct.ts",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    assert requests == [
        "/direct.ts"
    ], "the probe cannot observe ffmpeg network requests"

    requests.clear()
    cmd = ["ffmpeg", "-nostdin", "-i", _playlist(tmp_path, port), "-f", "null", "-"]
    for attempt in (cmd, local_media_command(cmd)):
        result = subprocess.run(attempt, capture_output=True, timeout=30)
        assert result.returncode != 0
    assert requests == []
