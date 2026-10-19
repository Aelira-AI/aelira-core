"""Bounded structural verification for generated WebVTT caption files.

A pass shows that the saved file is well-formed WebVTT whose cues are exactly
the transcription the remediator produced, within the media's duration. It does
not show that the transcription is accurate; generated captions always need
human review before use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

MAX_CAPTION_BYTES = 5 * 1024 * 1024
# Speech timestamps come from decoded audio and may trail the container duration.
DURATION_TOLERANCE_SECONDS = 1.0
TIMESTAMP_TOLERANCE_SECONDS = 0.002

_TIMESTAMP = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})$")


@dataclass(frozen=True)
class WebVttCue:
    start: float
    end: float
    text: str


def _seconds(value: str) -> float:
    match = _TIMESTAMP.match(value)
    if match is None:
        raise ValueError(f"invalid cue timestamp: {value!r}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


# The scanner's WCAG 1.2.2 finding for media with no caption track.
MISSING_CAPTIONS_PREFIX = "missing captions"


def is_missing_captions_finding(description: str) -> bool:
    """Only the missing-track finding is resolved by a generated caption file."""
    return str(description).strip().lower().startswith(MISSING_CAPTIONS_PREFIX)


def normalize_cue_text(text: str) -> str:
    """Collapse whitespace the way the generator writes a single cue line."""
    return " ".join(str(text).split())


def cue_text(text: str) -> str:
    """Single-line cue text with WebVTT's reserved characters escaped."""
    return (
        normalize_cue_text(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def parse_webvtt(content: str) -> List[WebVttCue]:
    """Parse the plain cue subset the caption generator writes, or raise ValueError.

    Cue settings, markup, NOTE, STYLE and REGION blocks are valid WebVTT but are
    never generated here, so their presence means the file is not the generated
    output. Cue text is compared in its escaped form.
    """
    if content.startswith("﻿"):
        content = content[1:]
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header = lines[0]
    if header != "WEBVTT" and not header.startswith(("WEBVTT ", "WEBVTT\t")):
        raise ValueError("missing WEBVTT header")
    if len(lines) < 2 or lines[1] != "":
        raise ValueError("header must be followed by a blank line")

    blocks: List[List[str]] = []
    current: List[str] = []
    for line in lines[2:]:
        if line == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    cues: List[WebVttCue] = []
    for block in blocks:
        if "-->" not in block[0]:
            if block[0].startswith(("NOTE", "STYLE", "REGION")):
                raise ValueError(f"unexpected {block[0].split()[0]} block")
            block = block[1:]
        if not block or "-->" not in block[0]:
            raise ValueError("cue is missing its timing line")
        timing = block[0].split(" --> ")
        if len(timing) != 2:
            raise ValueError(f"invalid cue timing line: {block[0]!r}")
        start, end = _seconds(timing[0]), _seconds(timing[1])
        if not start < end:
            raise ValueError("cue end must follow its start")
        text_lines = block[1:]
        if not text_lines or any("-->" in line for line in text_lines):
            raise ValueError("cue must have text and no further timing")
        if any("<" in line for line in text_lines):
            raise ValueError("cue markup is never generated")
        text = normalize_cue_text(" ".join(text_lines))
        if not text:
            raise ValueError("cue text is empty")
        cues.append(WebVttCue(start=start, end=end, text=text))
    return cues


def caption_file_problem(
    path: str, segments: Iterable[dict[str, Any]], duration: Optional[float]
) -> Optional[str]:
    """Return why the saved caption file is not the transcription, or None if it is."""
    expected = [
        (float(segment["start"]), float(segment["end"]), text)
        for segment in segments
        if (text := cue_text(segment.get("text", "")))
    ]
    if not expected:
        return "no transcription to compare"
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_CAPTION_BYTES + 1)
        if len(raw) > MAX_CAPTION_BYTES:
            return "caption file is too large"
        cues = parse_webvtt(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"caption file is not valid generated WebVTT ({exc})"
    if len(cues) != len(expected):
        return "cue count differs from the transcription"
    previous_start = 0.0
    for cue, (start, end, text) in zip(cues, expected):
        if cue.start < previous_start:
            return "cues are out of order"
        previous_start = cue.start
        if (
            abs(cue.start - start) > TIMESTAMP_TOLERANCE_SECONDS
            or abs(cue.end - end) > TIMESTAMP_TOLERANCE_SECONDS
            or cue.text != text
        ):
            return "cue differs from the transcription"
        if duration and cue.end > duration + DURATION_TOLERANCE_SECONDS:
            return "cue ends after the media"
    return None
