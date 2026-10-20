"""Spoken words for mathematics in alternative text, in one place.

Two paths turn LaTeX into words a screen reader can announce. The equation path
reads an equation image and builds its label with ``generate_equation_alt_text``.
The generic image-description path asks a vision model to describe an image, and
a model that meets an equation tends to answer in LaTeX, which a screen reader
announces as backslashes, braces and dollar signs. Both paths share this module
so the same table of spoken forms, and the same refusal to guess, applies to
each.

``speak_math_in_description`` is the single check every generated description
passes through before it is used as alternative text. Text without mathematics
comes back unchanged. Mathematics is converted with the table below. Anything
the table cannot say reliably is refused with ``None``, never guessed: the
caller leaves the image for a person.
"""

from __future__ import annotations

import re
from typing import List, Optional


def strip_math_delimiters(latex: str) -> str:
    """Remove one pair of display or inline math delimiters, if present."""
    stripped = latex.strip()
    for start, end in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)")):
        if stripped.startswith(start) and stripped.endswith(end):
            stripped = stripped[len(start) : len(stripped) - len(end)].strip()
            break
    if stripped.startswith("$") and stripped.endswith("$") and len(stripped) > 1:
        stripped = stripped[1:-1].strip()
    return stripped


# ---------------------------------------------------------------------------
# Spoken-word label for LaTeX (the equation path) and its ambiguity guard
# ---------------------------------------------------------------------------

_SPOKEN_SUBSTITUTIONS: List[tuple] = [
    # Fractions and binomial coefficients
    (re.compile(r"\\[dtc]?frac\{([^}]+)\}\{([^}]+)\}"), r"\1 over \2"),
    (re.compile(r"\\[dt]?binom\{([^}]+)\}\{([^}]+)\}"), r"\1 choose \2"),
    # Square root
    (re.compile(r"\\sqrt\{([^}]+)\}"), r"square root of \1"),
    (re.compile(r"\\sqrt\[([^\]]+)\]\{([^}]+)\}"), r"\1th root of \2"),
    # Powers (must come before simple ^ handler)
    (re.compile(r"\^2\b"), " squared"),
    (re.compile(r"\^3\b"), " cubed"),
    (re.compile(r"\^\{2\}"), " squared"),
    (re.compile(r"\^\{3\}"), " cubed"),
    (re.compile(r"\^\{([^}]+)\}"), r" to the power \1"),
    (re.compile(r"\^(\w)"), r" to the power \1"),
    # Subscripts
    (re.compile(r"_\{([^}]+)\}"), r" sub \1"),
    (re.compile(r"_(\w)"), r" sub \1"),
    # Greek letters (lowercase)
    (re.compile(r"\\alpha\b"), "alpha"),
    (re.compile(r"\\beta\b"), "beta"),
    (re.compile(r"\\gamma\b"), "gamma"),
    (re.compile(r"\\delta\b"), "delta"),
    (re.compile(r"\\epsilon\b"), "epsilon"),
    (re.compile(r"\\zeta\b"), "zeta"),
    (re.compile(r"\\eta\b"), "eta"),
    (re.compile(r"\\theta\b"), "theta"),
    (re.compile(r"\\iota\b"), "iota"),
    (re.compile(r"\\kappa\b"), "kappa"),
    (re.compile(r"\\lambda\b"), "lambda"),
    (re.compile(r"\\mu\b"), "mu"),
    (re.compile(r"\\nu\b"), "nu"),
    (re.compile(r"\\xi\b"), "xi"),
    (re.compile(r"\\pi\b"), "pi"),
    (re.compile(r"\\rho\b"), "rho"),
    (re.compile(r"\\sigma\b"), "sigma"),
    (re.compile(r"\\tau\b"), "tau"),
    (re.compile(r"\\upsilon\b"), "upsilon"),
    (re.compile(r"\\phi\b"), "phi"),
    (re.compile(r"\\chi\b"), "chi"),
    (re.compile(r"\\psi\b"), "psi"),
    (re.compile(r"\\omega\b"), "omega"),
    # Greek letters (uppercase)
    (re.compile(r"\\Gamma\b"), "Gamma"),
    (re.compile(r"\\Delta\b"), "Delta"),
    (re.compile(r"\\Theta\b"), "Theta"),
    (re.compile(r"\\Lambda\b"), "Lambda"),
    (re.compile(r"\\Xi\b"), "Xi"),
    (re.compile(r"\\Pi\b"), "Pi"),
    (re.compile(r"\\Sigma\b"), "Sigma"),
    (re.compile(r"\\Upsilon\b"), "Upsilon"),
    (re.compile(r"\\Phi\b"), "Phi"),
    (re.compile(r"\\Psi\b"), "Psi"),
    (re.compile(r"\\Omega\b"), "Omega"),
    # Operators
    (re.compile(r"\\times\b"), "times"),
    (re.compile(r"\\div\b"), "divided by"),
    (re.compile(r"\\pm\b"), "plus or minus"),
    (re.compile(r"\\mp\b"), "minus or plus"),
    (re.compile(r"\\leq\b"), "less than or equal to"),
    (re.compile(r"\\geq\b"), "greater than or equal to"),
    (re.compile(r"\\neq\b"), "not equal to"),
    (re.compile(r"\\approx\b"), "approximately equal to"),
    (re.compile(r"\\equiv\b"), "equivalent to"),
    (re.compile(r"\\cdot\b"), "dot"),
    (re.compile(r"\\ldots\b"), "ellipsis"),
    (re.compile(r"\\cdots\b"), "ellipsis"),
    (re.compile(r"\\infty\b"), "infinity"),
    (re.compile(r"\\partial\b"), "partial derivative"),
    (re.compile(r"\\nabla\b"), "nabla"),
    (re.compile(r"\\sum\b"), "sum"),
    (re.compile(r"\\prod\b"), "product"),
    (re.compile(r"\\int\b"), "integral"),
    (re.compile(r"\\oint\b"), "contour integral"),
]

# The catch-all clean-up that runs after the spoken forms above. It deletes every
# command it does not recognise and keeps the braced argument, so it is kept
# apart: the commands the spoken forms leave behind are exactly the ones this
# clean-up would silently erase.
_CLEANUP_SUBSTITUTIONS: List[tuple] = [
    # Remove remaining LaTeX commands (leave their arguments visible)
    (re.compile(r"\\[a-zA-Z]+\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\[a-zA-Z]+"), ""),
    # Clean up braces, backticks, etc.
    (re.compile(r"[{}]"), ""),
    # Collapse whitespace
    (re.compile(r"\s{2,}"), " "),
]

_ARIA_SUBSTITUTIONS: List[tuple] = [*_SPOKEN_SUBSTITUTIONS, *_CLEANUP_SUBSTITUTIONS]


_EQUATION_ALT_TEXT_FALLBACK = "mathematical equation"


def generate_equation_alt_text(latex: str) -> str:
    """Return the existing deterministic plain-English equation label."""

    label = latex.strip()
    for delimiter_pair in [("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)")]:
        start, end = delimiter_pair
        if label.startswith(start) and label.endswith(end):
            label = label[len(start) : len(label) - len(end)].strip()
            break
    if label.startswith("$") and label.endswith("$") and len(label) > 1:
        label = label[1:-1].strip()
    for pattern, replacement in _ARIA_SUBSTITUTIONS:
        label = pattern.sub(replacement, label)
    label = label.strip()
    return label if len(label) >= 2 else _EQUATION_ALT_TEXT_FALLBACK


_FRACTION_COMMAND = re.compile(r"\\(?:[dtc]?frac|[dt]?binom)(?![A-Za-z])")
_FRACTION_ARGUMENTS = re.compile(r"\{[^{}]+\}\{[^{}]+\}")
_SQRT_COMMAND = re.compile(r"\\sqrt(?![A-Za-z])")
_SQRT_ARGUMENTS = re.compile(r"(?:\[[^\[\]{}]+\])?\{[^{}]+\}")
_BRACED_SCRIPT = re.compile(r"\{[^{}]+\}")


def equation_alt_text_is_ambiguous(latex: str) -> bool:
    """Whether the deterministic label cannot be trusted for this LaTeX.

    The label comes from regular expressions, not from the equation's structure.
    It is refused, never guessed, when it falls back to the generic text, when a
    fraction, binomial or root does not carry the plain braced arguments the
    expressions translate, or when a script is not one character or a plain
    braced group. Without that, ``\\frac12`` would read "12", ``\\binom{n}{k}``
    "nk" and ``x^2y`` "x to the power 2y".
    """
    if generate_equation_alt_text(latex) == _EQUATION_ALT_TEXT_FALLBACK:
        return True
    text = strip_math_delimiters(latex)
    for command in _FRACTION_COMMAND.finditer(text):
        if _FRACTION_ARGUMENTS.match(text, command.end()) is None:
            return True
    for command in _SQRT_COMMAND.finditer(text):
        if _SQRT_ARGUMENTS.match(text, command.end()) is None:
            return True
    for index, character in enumerate(text):
        if character not in "^_" or (index and text[index - 1] == "\\"):
            continue
        script = text[index + 1 :]
        if script.startswith("{"):
            if _BRACED_SCRIPT.match(script) is None:
                return True
            continue
        first = script[:1]
        if not first or first.isspace() or first in "\\{}^_":
            return True
        if first.isalnum() and script[1:2].isalnum():
            return True
    return False


# ---------------------------------------------------------------------------
# One check for every generated description
# ---------------------------------------------------------------------------

# Returned as the ``error`` of a generation result that was refused. It avoids
# the words the visual-analysis recorder matches on to classify a provider
# failure, so a refusal is final and is never retried.
MATH_NOT_SPEAKABLE_ERROR = "description_math_not_speakable"

# The manual reason a remediator records when a proposed description was refused.
IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON = "image_description_math_not_speakable"

# What a remediator tells a person when it refused such a description.
MATH_DESCRIPTION_RECOMMENDATION = (
    "The proposed description contained mathematical notation that could not be "
    "turned into reliable spoken words, so nothing was written. Describe the "
    "image yourself, writing any mathematics in plain words, or tag an equation "
    "with a Formula element and its alt text."
)

# The sentence every alternative-text prompt carries. It asks for words and does
# not show LaTeX, so the prompt does not prime the model to write any.
SPOKEN_MATH_INSTRUCTION = (
    "Describe any mathematics in plain spoken words, the way you would read it "
    'aloud, for example "x squared plus 3 x minus 7 equals 0" or "a over b". '
    "Do not write LaTeX, markdown or math delimiters, which means no dollar "
    "signs, backslashes, carets or braces."
)

# A backslash followed by letters is a LaTeX command, unless the token is
# clearly a Windows path (see ``_WINDOWS_PATH``).
_COMMAND = re.compile(r"\\[A-Za-z]+")
# A braced script, ``x^{2}`` or ``a_{n}``: the other bare LaTeX signal.
_SCRIPT_GROUP = re.compile(r"[\^_]\{")
# What ``json.loads`` makes of the first letter of a command written with a
# single backslash inside a JSON string: ``\f`` and ``\b`` become a form feed and
# a backspace (``\frac``, ``\beta``), ``\t`` and ``\r`` become a tab and a
# carriage return when a lowercase letter follows (``\times``, ``\rho``). The
# command is already gone when the text reaches this module, so the character
# that replaced it is the only trace. None of them belongs in a description.
_DECODED_ESCAPE = re.compile(r"[\x08\x0c]|(?<![A-Za-z])[\t\r](?=[a-z])")

# Delimited mathematics, in the order the alternatives are tried. The inline
# ``$...$`` form follows the usual Markdown rule so that prices stay prices: the
# opening dollar sign is followed by something other than a space, the closing
# one is not preceded by a space or an operator and is not followed by a digit,
# and an escaped ``\$`` never opens or closes anything. ``$5 and $10`` and
# ``$5-$10`` therefore contain no mathematics.
_DELIMITED = re.compile(
    r"(?<!\\)\$\$(?P<display>.+?)(?<!\\)\$\$"
    r"|\\\[(?P<bracket>.+?)\\\]"
    r"|\\\((?P<paren>.+?)\\\)"
    r"|(?<![\\$])\$(?![\s$])(?P<inline>[^$\n]+?)(?<![\s\\=+\-*/,:;<>|])\$(?![\d$])",
    re.DOTALL,
)

# A token that is clearly a Windows path is not mathematics. The rule is
# deliberately narrow: a drive letter (``C:\Users\...``), a UNC share
# (``\\server\share``), or a relative path that ends in a file extension
# (``src\education\math.py``). A backslash followed by letters in anything else
# is read as a LaTeX command, which is the cautious reading: it ends in a
# refusal and a person looks, rather than a path or a command being spoken.
_PATH_LEAD = r"[\"'(\[<]*"
_WINDOWS_PATH = re.compile(
    rf"^{_PATH_LEAD}(?:[A-Za-z]:\\|\\\\[^\\\s]+\\)\S*"
    rf"|^{_PATH_LEAD}[\w.-]+(?:\\[\w.-]+)+\.[A-Za-z0-9]{{1,5}}[\"')\].,;:!?>]*$"
)
# A token that cannot continue a path that contains spaces.
_NOT_PATH = re.compile(r"[{}^$]")

# How far a braced group may run before an unclosed ``{`` is taken literally.
_MAX_GROUP = 500


def _unspoken_commands(latex: str) -> List[str]:
    """The commands the spoken forms leave behind.

    The clean-up that follows deletes them without a trace, so a command that
    appears here is one the label would silently erase.
    """
    text = strip_math_delimiters(latex)
    for pattern, replacement in _SPOKEN_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return _COMMAND.findall(text)


def _speak(latex: str) -> Optional[str]:
    """Spoken words for one span of LaTeX, or ``None`` when it cannot be said."""
    if equation_alt_text_is_ambiguous(latex):
        return None
    if _unspoken_commands(latex):
        return None
    spoken = generate_equation_alt_text(latex)
    # A backslash that survives the table (a thin space, an escaped brace, a
    # doubled command) would be announced as "backslash".
    if "\\" in spoken:
        return None
    return spoken


def _token_end(text: str, start: int) -> int:
    """End of the whitespace-delimited token at ``start``.

    A braced group is kept whole even when it contains spaces, so
    ``\\frac{a + b}{c}`` is one token. A brace that does not close within
    ``_MAX_GROUP`` characters is an ordinary character.
    """
    length = len(text)
    index = start
    while index < length and not text[index].isspace():
        if text[index] == "{":
            depth = 0
            probe = index
            limit = min(length, index + _MAX_GROUP)
            while probe < limit:
                if text[probe] == "{":
                    depth += 1
                elif text[probe] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                probe += 1
            if probe < limit and text[probe] == "}" and depth == 0:
                index = probe + 1
                continue
        index += 1
    return index


def _convert_plain(segment: str) -> Optional[str]:
    """Convert the bare LaTeX in text that holds no delimited mathematics.

    A token is converted when it holds a backslash command or a braced script,
    and is left exactly as it is otherwise. Returns ``None`` when a token that
    holds mathematics cannot be spoken reliably.
    """
    pieces: List[str] = []
    in_path = False
    index = 0
    length = len(segment)
    while index < length:
        if segment[index].isspace():
            end = index
            while end < length and segment[end].isspace():
                end += 1
            pieces.append(segment[index:end])
            index = end
            continue
        end = _token_end(segment, index)
        token = segment[index:end]
        index = end
        if _WINDOWS_PATH.search(token):
            in_path = True
            pieces.append(token)
        elif in_path and "\\" in token and not _NOT_PATH.search(token):
            # The next word of a path that has a space in it, such as the
            # ``Files\App`` of ``C:\Program Files\App``.
            pieces.append(token)
        else:
            in_path = False
            if _COMMAND.search(token) or _SCRIPT_GROUP.search(token):
                spoken = _speak(token)
                if spoken is None:
                    return None
                pieces.append(spoken)
            else:
                pieces.append(token)
    return "".join(pieces)


def speak_math_in_description(text: str) -> Optional[str]:
    """Return ``text`` with its mathematics spoken, or ``None`` to refuse it.

    This is the one check a generated description passes through before it is
    used as alternative text. Text that holds no mathematics comes back as the
    same string, byte for byte. Otherwise every span is converted with the
    equation path's table: ``$$...$$``, ``\\[...\\]``, ``\\(...\\)`` and
    ``$...$`` spans, and bare backslash commands and braced ``^{}``/``_{}``
    groups outside any delimiter. ``None`` means the description must not be
    used, and the image is left for a person, when

    - a span is one ``equation_alt_text_is_ambiguous`` refuses,
    - a command has no spoken form, so the label would erase it, or a backslash
      survives conversion, or
    - the text carries the trace of a command a JSON parser already swallowed.

    Converting twice changes nothing the second time, because the spoken form
    holds no mathematics.
    """
    if not isinstance(text, str):
        return None
    if _DECODED_ESCAPE.search(text):
        return None
    if "\\" not in text and "$" not in text and not _SCRIPT_GROUP.search(text):
        return text
    pieces: List[str] = []
    last = 0
    for match in _DELIMITED.finditer(text):
        plain = _convert_plain(text[last : match.start()])
        if plain is None:
            return None
        inner = match.group("display") or match.group("bracket")
        inner = inner or match.group("paren") or match.group("inline")
        spoken = _speak(inner)
        if spoken is None:
            return None
        before = plain[-1:] if plain else (pieces[-1][-1:] if pieces else "")
        after = text[match.end() : match.end() + 1]
        pieces.append(plain)
        # Mathematics written hard against a word would run into it once the
        # delimiters are gone.
        pieces.append((" " if before.isalnum() else "") + spoken)
        if after.isalnum():
            pieces.append(" ")
        last = match.end()
    tail = _convert_plain(text[last:])
    if tail is None:
        return None
    pieces.append(tail)
    result = "".join(pieces)
    return text if result == text else result


__all__ = [
    "IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON",
    "MATH_DESCRIPTION_RECOMMENDATION",
    "MATH_NOT_SPEAKABLE_ERROR",
    "SPOKEN_MATH_INSTRUCTION",
    "equation_alt_text_is_ambiguous",
    "generate_equation_alt_text",
    "speak_math_in_description",
    "strip_math_delimiters",
]
