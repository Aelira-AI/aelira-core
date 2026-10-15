"""
Keep media decoders on local files in the formats Aelira accepts.

FFmpeg demuxers can follow references stored inside a media file, such as
playlist segments or nested inputs, and open them over any compiled-in
protocol. Every input the application decodes is a file it has already
downloaded or received, so decoders are limited to the local file protocol.

FFmpeg also probes content rather than trusting file extensions, so an
uploaded file can select any of its hundreds of demuxers and decoders.
Inputs are limited to the container formats and codecs of the media types
Aelira accepts; anything else is refused before an unrelated decoder runs.
"""

import os
from typing import Iterable, List

LOCAL_PROTOCOLS = "file"

# Demuxer names for the accepted upload types: MP4/MOV/M4A, WebM/MKV, AVI,
# MP3, WAV, Ogg and FLAC.
UPLOAD_FORMATS = (
    "mov",
    "mp4",
    "m4a",
    "3gp",
    "3g2",
    "mj2",
    "matroska",
    "webm",
    "avi",
    "mp3",
    "wav",
    "ogg",
    "flac",
)

# Decoder names for mainstream codecs carried by those containers.
UPLOAD_CODECS = (
    "h264",
    "hevc",
    "vp8",
    "vp9",
    "av1",
    "libdav1d",
    "mpeg4",
    "mjpeg",
    "aac",
    "mp3",
    "mp3float",
    "opus",
    "libopus",
    "vorbis",
    "flac",
    "alac",
    "ac3",
    "eac3",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "pcm_f32le",
    "pcm_u8",
)

# Application-generated subtitle files muxed into accessible videos.
SUBTITLE_FORMATS = ("srt", "webvtt")
SUBTITLE_CODECS = ("srt", "subrip", "webvtt")

OPENCV_CAPTURE_OPTIONS = "OPENCV_FFMPEG_CAPTURE_OPTIONS"


def _input_restrictions(
    extra_formats: Iterable[str], extra_codecs: Iterable[str]
) -> List[str]:
    return [
        "-protocol_whitelist",
        LOCAL_PROTOCOLS,
        "-format_whitelist",
        ",".join((*UPLOAD_FORMATS, *extra_formats)),
        "-codec_whitelist",
        ",".join((*UPLOAD_CODECS, *extra_codecs)),
    ]


def local_media_command(
    cmd: List[str],
    extra_formats: Iterable[str] = (),
    extra_codecs: Iterable[str] = (),
) -> List[str]:
    """Return an ffmpeg or ffprobe command whose inputs are restricted.

    Every input may only use the local file protocol and the accepted
    container formats and codecs, plus any extras the caller needs for
    application-generated inputs.
    """
    if not cmd:
        raise ValueError("Media command is empty")
    tool = os.path.basename(cmd[0])
    restrictions = _input_restrictions(extra_formats, extra_codecs)
    if tool == "ffprobe":
        return [cmd[0], *restrictions, *cmd[1:]]
    if tool == "ffmpeg":
        if "-i" not in cmd[1:]:
            raise ValueError("ffmpeg command has no input")
        # Input options apply only to the next -i, so every input needs its own.
        restricted = [cmd[0]]
        for arg in cmd[1:]:
            if arg == "-i":
                restricted.extend(restrictions)
            restricted.append(arg)
        return restricted
    raise ValueError(f"Unsupported media tool: {tool}")


def restrict_opencv_capture() -> None:
    """Apply the same input restrictions to OpenCV's FFmpeg capture backend."""
    os.environ[OPENCV_CAPTURE_OPTIONS] = "|".join(
        (
            f"protocol_whitelist;{LOCAL_PROTOCOLS}",
            f"format_whitelist;{','.join(UPLOAD_FORMATS)}",
            f"codec_whitelist;{','.join(UPLOAD_CODECS)}",
        )
    )
