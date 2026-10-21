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
LaTeX is never repaired, so it keeps the meaning JSON gives it. That is the
meaning the model had for a correctly escaped answer, but not always for a
sloppy one: ``\\/`` is a valid JSON escape for a slash, so ``f\\/(x)`` arrives as
``f/(x)``, and a pair of backslashes arrives as one. Only an escape that JSON
rejects, or that decodes to a control character, is repaired.

One case is not repaired and cannot be: a LaTeX row break ``\\\\`` written with
its two backslashes is a valid JSON escape for one backslash, so the reading
arrives with a lone backslash and a changed meaning. Both the printed and the
handwritten path refuse it, because the label they would build keeps a
backslash and the equation guard refuses such a label.
"""

from __future__ import annotations

import json
import re
from typing import Any, FrozenSet, Optional, Tuple

from src.education.model_json import double_unescaped_backslashes


class EquationReadingRejected(ValueError):
    """The answer was not exactly one acceptable equation reading."""


_INVISIBLE_LATEX = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_INVALID = "invalid_provider_response"

# The answer is one JSON object around the LaTeX: the two keys, a classification
# and the braces take well under this many characters.
_JSON_OVERHEAD_CHARS = 128
# LaTeX averages a little over two characters to a token, because the command
# names and braces tokenise in pieces, so half of the characters is the allowance.
# A reading that is longer than that in tokens is cut off, no longer parses, and
# stays manual, which is the safe outcome.
_CHARS_PER_TOKEN = 2
_MIN_READING_TOKENS = 500


def reading_max_tokens(max_latex_chars: int) -> int:
    """The token allowance that covers a reading of ``max_latex_chars`` characters."""
    needed = (max_latex_chars + _JSON_OVERHEAD_CHARS + _CHARS_PER_TOKEN - 1) // (
        _CHARS_PER_TOKEN
    )
    return max(_MIN_READING_TOKENS, needed)


def response_is_cached(response: Any) -> bool:
    """Whether a provider answer was replayed from a cache, not read afresh.

    The provider manager answers a repeated image and prompt from its cache with
    the model name ``cached``. Such an answer names no model: the model that wrote
    it is not known and need not be the one now configured, so no identity may be
    recorded for it.
    """
    if not isinstance(response, dict):
        return False
    model = response.get("model")
    if isinstance(model, str) and model.strip().casefold() == "cached":
        return True
    metadata = response.get("metadata")
    return response.get("cached") is True or (
        isinstance(metadata, dict) and metadata.get("cached") is True
    )


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
    "reading_max_tokens",
    "response_is_cached",
]
