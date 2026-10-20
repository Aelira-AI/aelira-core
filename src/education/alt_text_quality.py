"""Shared normalization and validation for generated image alt text."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

MAX_ALT_TEXT_LENGTH = 500
_LABEL_PREFIX = re.compile(
    r"^(?:alt\s*text|description|image\s*description)\s*:\s*",
    re.IGNORECASE,
)
_PLACEHOLDERS = frozenset(
    {
        "image",
        "photo",
        "picture",
        "graphic",
        "img",
        "image content not specified",
        "no description",
        "not specified",
        "description unavailable",
        "alt text",
        "todo",
        "n/a",
        "unknown",
    }
)
_FAILURE_PREFIXES = ("error", "unknown", "unable", "cannot")


def normalize_usable_alt_text(
    value: object, *, max_length: int = MAX_ALT_TEXT_LENGTH
) -> Optional[str]:
    """Return a safe, complete description, or ``None`` when unusable.

    Normalization is deliberately lossless for already-valid descriptions.
    It removes provider labels and redundant printable whitespace. Invalid,
    control-bearing, or apparently truncated output is rejected rather than
    repaired or truncated.
    """
    if not isinstance(value, str):
        return None
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} or not char.isprintable()
        for char in value
    ):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    text = _LABEL_PREFIX.sub("", text).strip()
    if not text or len(text) > max_length:
        return None
    if text.endswith(("...", "…")):
        return None
    folded = text.casefold().strip().rstrip(".").strip()
    if folded in _PLACEHOLDERS or folded.startswith(_FAILURE_PREFIXES):
        return None
    return text


def is_usable_alt_text(value: object, *, max_length: int = MAX_ALT_TEXT_LENGTH) -> bool:
    """Whether ``value`` is a validated image description."""
    return normalize_usable_alt_text(value, max_length=max_length) is not None


_IMAGE_EXTENSIONS = "png|jpe?g|gif|bmp|tiff?|svg|emf|wmf|webp|heic"
# A file name, optionally with a Windows, POSIX or URL path in front of it.
_FILENAME_PLACEHOLDER = re.compile(
    rf"^(?:[a-z]:)?(?:[^\\/]*[\\/])*[^\\/]+\.(?:{_IMAGE_EXTENSIONS})$",
    re.IGNORECASE,
)
# Names that authoring tools give a picture, with an optional counter.
_TOOL_PLACEHOLDER = re.compile(
    r"^(?:picture|image|img|graphic|photo|screenshot|screen[ _-]?shot)"
    r"(?:[ _-]?\d{1,4})?$",
    re.IGNORECASE,
)
_MAX_FILENAME_WORDS = 8


def alt_text_is_placeholder(text: object) -> bool:
    """Whether alt text is only a file name or an authoring tool's placeholder.

    Tools fill a picture's description with its file name ("nature1.png",
    "C:\\pics\\photo.JPG") or a generic label ("Picture 3", "Image 1",
    "image001", "Screenshot") when nobody wrote one. Such text satisfies a
    presence check and tells a screen reader user nothing.

    The match is deliberately narrow and covers the whole string only. A real
    description that mentions a file type ("Diagram of a PNG compression
    pipeline") is not a placeholder, and neither is a sentence that merely
    starts with "Screenshot" or "Image 3". Empty text is not reported here;
    callers already treat it as missing.
    """
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value or "\n" in value or "\r" in value:
        return False
    if _TOOL_PLACEHOLDER.match(value):
        return True
    if not _FILENAME_PLACEHOLDER.match(value):
        return False
    name = re.split(r"[\\/]", value)[-1]
    # Real file names can hold spaces, but a name with sentence punctuation or
    # many words is prose that happens to end in an extension.
    return not any(mark in name for mark in ",;!?") and (
        len(name.split()) <= _MAX_FILENAME_WORDS
    )
