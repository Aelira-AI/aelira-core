"""Parsing model answers that carry JSON with LaTeX written inside its strings.

A model that writes LaTeX inside a JSON string usually uses single backslashes,
which JSON does not allow. The answer is then damaged in one of two ways. An
invalid escape such as ``\\approx`` or ``\\alpha`` makes the strict parse fail.
A valid escape silently becomes a control character: ``\\frac`` a form feed,
``\\beta`` a backspace, ``\\times``, ``\\theta`` and ``\\to`` a tab, ``\\rho``
a carriage return, and ``\\ne``, ``\\nabla``, ``\\notin`` and ``\\nsubset`` a line
feed. The escape takes its letter with it, so the control character is followed
by the rest of the name only (``\\theta`` arrives as a tab and ``heta``). The
command is gone before any check on the text can see it, and a negation such as
``\\nsubset`` reads as its opposite.

``loads_tolerant`` parses strictly first. When that fails, or when a decoded
string carries the trace of a swallowed command, every bare backslash is doubled
and the answer is parsed once more, so the command survives as a backslash
command that the markup check refuses. A correctly escaped answer is never
repaired.

What counts as the trace of a swallowed command is deliberately narrow, because
a description may legitimately contain line breaks and tabs. A form feed or a
backspace never belongs in a description and always counts. A line feed, a tab
or a carriage return counts only when the letters that follow it, with the
letter the escape swallowed put back, spell a known LaTeX command
(``latex_commands``), compared case sensitively. A genuine line break before a
word that happens to complete such a name is therefore repaired and then
refused: that fails closed and is accepted, because the alternative is to
announce "subset" for "not a subset".
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.education.latex_commands import LATEX_COMMAND_NAMES

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

# The letter that each JSON escape swallows along with its backslash: ``\n`` takes
# an ``n``, ``\t`` a ``t`` and ``\r`` an ``r``. A control character followed by
# letters is the start of a LaTeX command when the swallowed letter put back in
# front of those letters spells one.
_SWALLOWED_LETTER = {"\n": "n", "\t": "t", "\r": "r"}
_CONTROL_THEN_LETTERS = re.compile(r"([\n\t\r])([A-Za-z]+)")


def double_unescaped_backslashes(content: str) -> str:
    """Return ``content`` with each bare JSON backslash made a literal one."""
    return _UNESCAPED_BACKSLASH.sub(r"\1\\\\", content)


def swallowed_escape(text: str) -> bool:
    """Whether a decoded string holds what JSON decoding made of a LaTeX command.

    A form feed or a backspace never belongs in a description and always counts.
    A line feed, a tab or a carriage return counts only when the letters after it
    complete a known LaTeX command name together with the letter the escape
    swallowed, so ``\\ne`` (a line feed and ``e``), ``\\nsubset``, ``\\theta`` (a
    tab and ``heta``) and ``\\rho`` (a carriage return and ``ho``) count, while
    ordinary line breaks and tabs, including a carriage return before its line
    feed, do not. The letters are matched case sensitively, so ``\\nRightarrow``
    (a line feed and ``Rightarrow``) counts.
    """
    if "\x0c" in text or "\x08" in text:
        return True
    return any(
        _SWALLOWED_LETTER[match.group(1)] + match.group(2) in LATEX_COMMAND_NAMES
        for match in _CONTROL_THEN_LETTERS.finditer(text)
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
