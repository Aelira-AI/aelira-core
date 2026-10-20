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
# Line breaks and tabs are layout, not hidden content. Vision models use them to
# lay out tables, lists and multi-part figures, so they become single spaces.
FOLDED_WHITESPACE = "\n\r\t\u2028\u2029"
_FOLD_WHITESPACE = str.maketrans({char: " " for char in FOLDED_WHITESPACE})


def normalize_usable_alt_text(
    value: object, *, max_length: int = MAX_ALT_TEXT_LENGTH
) -> Optional[str]:
    """Return a safe, complete description, or ``None`` when unusable.

    Normalization is deliberately lossless for already-valid descriptions.
    It removes provider labels and redundant printable whitespace. Line breaks
    (including U+2028 and U+2029) and tabs are folded into single spaces first,
    so a multi-line description of a table or list is kept as one line. Every
    other control, format or surrogate character, such as NUL, form feed,
    zero-width characters and bidirectional controls, still rejects the text,
    as does output that is invalid or apparently truncated. The length limit
    applies to the folded text.
    """
    if not isinstance(value, str):
        return None
    value = value.translate(_FOLD_WHITESPACE)
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
