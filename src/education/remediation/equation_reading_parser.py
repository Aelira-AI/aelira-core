"""Shared strict parser for model answers that carry one equation reading.

Every equation recogniser asks the model for one JSON object such as
``{"classification": "printed_equation", "latex": "..."}``. Models routinely
write LaTeX inside that string with single backslashes, which JSON does not
allow. The answer is then lost in one of two ways: an invalid escape such as
``\\approx`` makes the strict parse fail, and a valid escape such as ``\\frac``
or ``\\theta`` silently becomes a control character (form feed, tab) that the
printable check rejects.

The parse here is strict first. Only when that fails, or when the parsed LaTeX
holds a non-printable character, are the unescaped backslashes doubled and the
answer parsed one more time. A strict parse that already produced printable
LaTeX is never repaired, so a correctly escaped answer keeps its meaning.
"""

from __future__ import annotations

import json
import re
from typing import Any, FrozenSet, Optional, Tuple


class EquationReadingRejected(ValueError):
    """The answer was not exactly one acceptable equation reading."""


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
_INVISIBLE_LATEX = re.compile(r"[​-‏‪-‮⁦-⁩]")
_INVALID = "invalid_provider_response"


def double_unescaped_backslashes(content: str) -> str:
    """Return ``content`` with each bare JSON backslash made a literal one."""
    return _UNESCAPED_BACKSLASH.sub(r"\1\\\\", content)


def _parse_once(
    content: str,
    *,
    equation_classification: str,
    null_classifications: FrozenSet[str],
    max_latex_chars: int,
    require_clean_latex: bool,
) -> Tuple[str, Optional[str]]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EquationReadingRejected(_INVALID)
            result[key] = value
        return result

    try:
        parsed = json.loads(content, object_pairs_hook=reject_duplicates)
    except EquationReadingRejected:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        raise EquationReadingRejected(_INVALID) from None
    if not isinstance(parsed, dict) or set(parsed) != {"classification", "latex"}:
        raise EquationReadingRejected(_INVALID)
    classification = parsed["classification"]
    latex = parsed["latex"]
    if not isinstance(classification, str):
        raise EquationReadingRejected(_INVALID)
    if classification in null_classifications:
        if latex is not None:
            raise EquationReadingRejected(_INVALID)
        return classification, None
    if classification != equation_classification or not isinstance(latex, str):
        raise EquationReadingRejected(_INVALID)
    if not latex or len(latex) > max_latex_chars:
        raise EquationReadingRejected(_INVALID)
    if any(not character.isprintable() for character in latex):
        raise EquationReadingRejected(_INVALID)
    if require_clean_latex and (
        latex != latex.strip() or _INVISIBLE_LATEX.search(latex)
    ):
        raise EquationReadingRejected(_INVALID)
    return classification, latex


def parse_equation_reading(
    content: Any,
    *,
    equation_classification: str,
    null_classifications: FrozenSet[str],
    max_latex_chars: int,
    max_response_chars: int,
    require_clean_latex: bool = False,
) -> Tuple[str, Optional[str]]:
    """Return ``(classification, latex)`` for one acceptable model answer.

    ``equation_classification`` is the only classification that carries LaTeX.
    Each of ``null_classifications`` must carry ``null``. Duplicate keys, extra
    keys, prose around the object, oversized answers and non-printable LaTeX are
    rejected both before and after the backslash repair. ``require_clean_latex``
    additionally rejects untrimmed LaTeX and invisible direction or width marks.
    """
    if (
        not isinstance(content, str)
        or not content
        or len(content) > max_response_chars
        or content.strip() != content
    ):
        raise EquationReadingRejected(_INVALID)
    options = {
        "equation_classification": equation_classification,
        "null_classifications": null_classifications,
        "max_latex_chars": max_latex_chars,
        "require_clean_latex": require_clean_latex,
    }
    try:
        return _parse_once(content, **options)
    except EquationReadingRejected:
        repaired = double_unescaped_backslashes(content)
        if repaired == content:
            raise
    return _parse_once(repaired, **options)


__all__ = [
    "EquationReadingRejected",
    "double_unescaped_backslashes",
    "parse_equation_reading",
]
