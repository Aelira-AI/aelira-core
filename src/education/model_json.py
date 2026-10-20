"""Parsing model answers that carry JSON with LaTeX written inside its strings.

A model that writes LaTeX inside a JSON string usually uses single backslashes,
which JSON does not allow. The answer is then damaged in one of two ways. An
invalid escape such as ``\\approx`` or ``\\alpha`` makes the strict parse fail.
A valid escape silently becomes a control character: ``\\frac`` a form feed,
``\\beta`` a backspace, ``\\times``, ``\\theta`` and ``\\to`` a tab, ``\\rho``
a carriage return, and ``\\ne``, ``\\nabla`` and ``\\neq`` a line feed followed
by the rest of the name. The command is gone before any check on the text can
see it.

``loads_tolerant`` parses strictly first. When that fails, or when a decoded
string carries the trace of a swallowed command, every bare backslash is doubled
and the answer is parsed once more, so the command survives as a backslash
command that the markup check refuses. A correctly escaped answer is never
repaired.
"""

from __future__ import annotations

import json
import re
from typing import Any

# A backslash that does not start a JSON escape the model could have meant:
# one not followed by a quote, a backslash or a slash, and not the start of a
# complete four-digit unicode escape. The leading group swallows pairs of
# backslashes first, so an even run (already escaped backslashes) is untouched
# and only the last backslash of an odd run is doubled. A well-formed unicode
# escape stays an escape, so an answer that mixes ``\\u03b1`` with a bare
# ``\\beta`` keeps the first meaning and still repairs the second.
_UNESCAPED_BACKSLASH = re.compile(
    r'(?<!\\)((?:\\\\)*)\\(?=[^"\\/u]|u(?![0-9A-Fa-f]{4}))'
)

# The line feed that ``\n`` leaves in front of the rest of a command's name. The
# name is only recognised when it is exactly one of these, so a line that was
# hard-wrapped before a word such as "nothing" or "other" is not mistaken for
# ``\not`` or ``\nother``.
_N_COMMANDS = frozenset(
    {
        "ne", "neq", "nu", "nabla", "neg", "ni", "not", "notin", "newline",
        "nolimits", "nleq", "ngeq", "nless", "ngtr", "nsim", "ncong", "nmid",
        "nparallel", "nrightarrow", "nleftarrow", "nsubseteq", "nexists",
        "nvdash", "nequiv", "napprox", "nprec", "nsucc", "nsupseteq",
    }
)  # fmt: skip
_LINE_FEED_THEN_NAME = re.compile(r"\n([a-z]+)")


def double_unescaped_backslashes(content: str) -> str:
    """Return ``content`` with each bare JSON backslash made a literal one."""
    return _UNESCAPED_BACKSLASH.sub(r"\1\\\\", content)


def swallowed_escape(text: str) -> bool:
    """Whether a decoded string holds what JSON decoding made of a LaTeX command.

    A form feed, a backspace, a tab or a carriage return that is not part of a
    carriage return and line feed pair never belongs in a description. A line
    feed is suspect only when the letters after it complete a LaTeX command that
    starts with ``n``.
    """
    if "\x0c" in text or "\x08" in text or "\t" in text:
        return True
    if re.search(r"\r(?!\n)", text):
        return True
    return any(
        "n" + match.group(1) in _N_COMMANDS
        for match in _LINE_FEED_THEN_NAME.finditer(text)
    )


def _holds_swallowed_escape(value: Any) -> bool:
    if isinstance(value, str):
        return swallowed_escape(value)
    if isinstance(value, list):
        return any(_holds_swallowed_escape(item) for item in value)
    if isinstance(value, dict):
        return any(_holds_swallowed_escape(item) for item in value.values())
    return False


def loads_tolerant(content: str) -> Any:
    """Parse JSON text, repairing LaTeX written with single backslashes.

    Raises ``json.JSONDecodeError`` when the answer cannot be parsed either way.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        repaired = double_unescaped_backslashes(content)
        if repaired == content:
            raise
        return json.loads(repaired)
    if _holds_swallowed_escape(parsed):
        repaired = double_unescaped_backslashes(content)
        if repaired != content:
            return json.loads(repaired)
    return parsed


__all__ = ["double_unescaped_backslashes", "loads_tolerant", "swallowed_escape"]
