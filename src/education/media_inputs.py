"""
Keep media decoders on local files.

FFmpeg demuxers can follow references stored inside a media file, such as
playlist segments or nested inputs, and open them over any compiled-in
protocol. Every input the application decodes is a file it has already
downloaded or received, so decoders are limited to the local file protocol.
"""

import os
from typing import List

LOCAL_PROTOCOLS = "file"
OPENCV_CAPTURE_OPTIONS = "OPENCV_FFMPEG_CAPTURE_OPTIONS"


def local_media_command(cmd: List[str]) -> List[str]:
    """Return an ffmpeg or ffprobe command whose inputs open local files only."""
    if not cmd:
        raise ValueError("Media command is empty")
    tool = os.path.basename(cmd[0])
    whitelist = ["-protocol_whitelist", LOCAL_PROTOCOLS]
    if tool == "ffprobe":
        return [cmd[0], *whitelist, *cmd[1:]]
    if tool == "ffmpeg":
        # Input options apply only to the next -i, so every input needs its own.
        restricted = [cmd[0]]
        for arg in cmd[1:]:
            if arg == "-i":
                restricted.extend(whitelist)
            restricted.append(arg)
        if "-i" not in cmd[1:]:
            raise ValueError("ffmpeg command has no input")
        return restricted
    raise ValueError(f"Unsupported media tool: {tool}")


def restrict_opencv_capture() -> None:
    """Limit OpenCV's FFmpeg capture backend to local files for this process."""
    os.environ[OPENCV_CAPTURE_OPTIONS] = f"protocol_whitelist;{LOCAL_PROTOCOLS}"
