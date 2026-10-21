"""Spoken words for mathematics in alternative text, in one place.

Two paths put mathematics into alternative text. The equation path reads an
equation image and builds its label from the LaTeX with
``generate_equation_alt_text``; ``equation_alt_text_is_ambiguous`` refuses the
equations whose label would garble or mislead. The generic image-description path
asks a vision model to describe an image, and a model that meets an equation can
answer in LaTeX, which a screen reader announces as backslashes, braces and
dollar signs.

The description path never rewrites. A prompt that asks for plain spoken words
removes the markup, so ``refuse_math_markup`` returns a description unchanged or
refuses it whole, and the image is left for a person. Both checks refuse rather
than guess.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

from src.education.latex_commands import LATEX_COMMAND_NAMES


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

# A backslash followed by letters is a LaTeX command, unless the token is
# clearly a Windows path (see ``_WINDOWS_PATH``).
_COMMAND = re.compile(r"\\[A-Za-z]+")

# The word a numeric root index is spoken as. Any other number has no word here
# and is refused, so ``\sqrt[3]{x}`` reads "cube root of x" and never "3th".
_ROOT_WORDS = {
    "2": "square",
    "3": "cube",
    "4": "fourth",
    "5": "fifth",
    "6": "sixth",
    "7": "seventh",
    "8": "eighth",
    "9": "ninth",
    "10": "tenth",
}


def _indexed_root(match: "re.Match[str]") -> str:
    index = match.group(1).strip()
    return f"{_ROOT_WORDS.get(index, index + 'th')} root of {match.group(2)}"


_SPOKEN_RULES: List[tuple] = [
    # Fractions and binomial coefficients
    (re.compile(r"\\[dtc]?frac\{([^}]+)\}\{([^}]+)\}"), r"\1 over \2"),
    (re.compile(r"\\[dt]?binom\{([^}]+)\}\{([^}]+)\}"), r"\1 choose \2"),
    # Square root
    (re.compile(r"\\sqrt\{([^}]+)\}"), r"square root of \1"),
    (re.compile(r"\\sqrt\[([^\]]+)\]\{([^}]+)\}"), _indexed_root),
    # Powers (must come before simple ^ handler)
    (re.compile(r"\^2(?![A-Za-z0-9])"), " squared"),
    (re.compile(r"\^3(?![A-Za-z0-9])"), " cubed"),
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
    (re.compile(r"\\leq?\b"), "less than or equal to"),
    (re.compile(r"\\geq?\b"), "greater than or equal to"),
    (re.compile(r"\\neq?\b"), "not equal to"),
    (re.compile(r"\\approx\b"), "approximately equal to"),
    (re.compile(r"\\equiv\b"), "equivalent to"),
    (re.compile(r"\\cdot\b"), "dot"),
    (re.compile(r"\\ldots\b"), "ellipsis"),
    (re.compile(r"\\cdots\b"), "centered ellipsis"),
    (re.compile(r"\\infty\b"), "infinity"),
    (re.compile(r"\\partial\b"), "partial"),
    (re.compile(r"\\nabla\b"), "nabla"),
    (re.compile(r"\\sum\b"), "sum"),
    (re.compile(r"\\prod\b"), "product"),
    (re.compile(r"\\int\b"), "integral"),
    (re.compile(r"\\oint\b"), "contour integral"),
    # Functions
    (re.compile(r"\\sin\b"), "sine"),
    (re.compile(r"\\cos\b"), "cosine"),
    (re.compile(r"\\tan\b"), "tangent"),
    (re.compile(r"\\log\b"), "log"),
    (re.compile(r"\\ln\b"), "natural log"),
]


def _padded(pattern: "re.Pattern[str]", replacement):
    """Keep a spoken form from running into the letter or digit before it.

    A command written hard against a symbol, as in ``2\\pi`` or ``a\\times b``,
    would otherwise read "2pi" and "atimes". ``replacement`` is a template or a
    function of the match.
    """

    def replace(match: "re.Match[str]") -> str:
        before = (
            match.string[match.start() - 1 : match.start()] if match.start() else ""
        )
        spoken = (
            replacement(match) if callable(replacement) else match.expand(replacement)
        )
        return (" " if before.isalnum() else "") + spoken

    return pattern, replace


# Commands that only change how an equation is drawn. They have no spoken form
# and nothing to say, so removing them loses no meaning. Every other command that
# the table above does not translate is refused, never erased.
_TYPOGRAPHY_RULES: List[tuple] = [
    (re.compile(r"\\(?:left|right)\s*\."), ""),
    (re.compile(r"\\(?:left|right|displaystyle)(?![A-Za-z])"), ""),
    (re.compile(r"\\mathrm\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\(?:quad|qquad)(?![A-Za-z])"), " "),
    (re.compile(r"\\[,;:!]"), " "),
]

# The commands the table translates are padded; the scripts already carry a
# leading space, and the typography rules must not add one.
_SPOKEN_SUBSTITUTIONS: List[tuple] = [
    (
        _padded(pattern, replacement)
        if pattern.pattern.startswith("\\\\")
        else (pattern, replacement)
    )
    for pattern, replacement in _SPOKEN_RULES
] + _TYPOGRAPHY_RULES

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


# ---------------------------------------------------------------------------
# The ambiguity guard
#
# The label is built by substitution, so it says the words of an equation but not
# where its parts begin and end. ``x^{n+1}`` and ``x^{n}+1`` both read "x to the
# power n+1". The guard is a set of refusal rules and not a proof. Each rule
# exists because two equations with different MathML were found that read the
# same, and the guard refuses rather than adds words. The property tests check
# the rules against the corpora of single-symbol edits and different pairs and
# against about 2,600 generated placements, which is evidence and not a
# guarantee for constructs nobody wrote a case for.
#
#   1. Every argument of a fraction, binomial, root or script is one token: a
#      number, a single letter, or one Greek letter or infinity. Anything joined
#      to it, whether by an operator, a space, another symbol or a second
#      construct, makes the argument compound, and a compound argument is what
#      lets the group's edge move without the words changing. A script written
#      without braces takes one ASCII letter or digit and nothing else.
#   2. A group is never followed, with nothing between, by a letter, a digit, an
#      opening brace or a command, which would run into its last word
#      ("x squaredy"), nor by a decimal point or comma and a digit, which would
#      read as a longer number. A fraction, binomial or root is also never
#      followed by a script. A script may be followed by the other kind of script.
#   3. A spoken form that is not closed is accepted only where nothing can be read
#      into it: at the end, before a comma or a relation, or before a closing
#      parenthesis that closes a parenthesis opened earlier and is itself followed
#      only by such an ending. That covers a fraction, a binomial, a root and a
#      script spoken "to the power n" or "sub n". "squared" and "cubed" are closed
#      forms and may be followed by anything the other rules allow. So "x squared
#      plus 3 x" is accepted, "x to the power n plus 1" is refused because it
#      could also be x to the power n+1, and so are "(1 + x^{n}) + 2" and
#      "x^{n}) + 1", because a screen reader at default punctuation does not
#      speak a parenthesis.
#   4. A brace group that is not an argument is refused. The label drops braces,
#      so ``{ab}^2`` and ``ab^2`` would read the same.
#   5. A script directly after an integral, sum, product or contour integral is a
#      limit, and is refused: the label would read it as a script on the symbol.
#   6. A command with no spoken form is never erased. Only the typography
#      commands above, which say nothing, are dropped.
#   7. A backslash, a caret or an underscore never survives into the label.
#   8. A script is refused when its base is a closing parenthesis or bracket or a
#      ``\mathrm`` group, so ``(2x)^2`` and ``2x^2`` do not read alike. A
#      ``\mathrm`` group holds one letter, which has the same MathML as the
#      plain letter; a longer one would hide where a base begins.
#   9. A run of letters that spells a word the labels use ("over", "sub",
#      "squared", "pi", "sum", "times") is refused, because ``a over b`` would
#      read like ``\frac{a}{b}``.
#
# Some equations are meant to read alike and the guard does not refuse them:
# spacing commands, ``\left``, ``\right`` and ``\displaystyle`` are read as
# nothing or one space, and a single-letter ``\mathrm`` reads as the letter.
#
# The label also puts a space before a spoken form that follows a letter or
# digit, so ``a\frac{b}{c}`` reads "a b over c" and ``\frac{ab}{c}`` "ab over c".
# ---------------------------------------------------------------------------

_SYMBOL_COMMANDS = (
    "alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|"
    "rho|sigma|tau|upsilon|phi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|"
    "Upsilon|Phi|Psi|Omega|infty"
)
_TOKEN = (
    r"(?:[0-9]+(?:\.[0-9]+)?|[A-Za-z](?![A-Za-z0-9])|\\(?:"
    + _SYMBOL_COMMANDS
    + r")(?![A-Za-z]))"
)
_BRACED_TOKEN = re.compile(r"\{\s*" + _TOKEN + r"\s*\}")
_BRACKETED_TOKEN = re.compile(r"\[\s*" + _TOKEN + r"\s*\]")
_PREFIX_COMMAND = re.compile(r"\\(?:[dtc]?frac|[dt]?binom)(?![A-Za-z])")
_SQRT_COMMAND = re.compile(r"\\sqrt(?![A-Za-z])")
_MATHRM_GROUP = re.compile(r"\\mathrm\{[A-Za-z]\}")


def _spoken_vocabulary() -> "frozenset[str]":
    """Every word a label can contain because of a spoken form."""
    words = set(_ROOT_WORDS.values()) | {"root", "of"}
    for _pattern, replacement in _SPOKEN_RULES:
        if isinstance(replacement, str):
            words.update(re.findall(r"[A-Za-z]+", replacement))
    return frozenset(word.casefold() for word in words)


# A run of letters in the equation that spells one of these reads exactly like the
# construct that produces the word, so it is refused (rule 9).
_SPOKEN_WORDS = _spoken_vocabulary()


def _letters_spell_a_spoken_word(text: str) -> bool:
    without_commands = _COMMAND.sub(" ", text)
    return any(
        run.casefold() in _SPOKEN_WORDS
        for run in re.findall(r"[A-Za-z]+", without_commands)
    )


def _unspoken_commands(latex: str) -> List[str]:
    """The commands the table leaves behind: the ones the label would erase."""
    text = strip_math_delimiters(latex)
    for pattern, replacement in _SPOKEN_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return _COMMAND.findall(text)


# What may follow a group directly although it starts with a backslash: a space
# command, which the label turns into a space, or the closing delimiter of a
# ``\left`` ... ``\right`` pair, which the label turns into the delimiter.
_SEPARATOR_COMMAND = re.compile(
    r"\\(?:[,;:!]|quad(?![A-Za-z])|qquad(?![A-Za-z])|right\s*[)\]|.])"
)
# What a spoken form that is not closed may be followed by: the end of the text,
# a comma, a relation, or a closing parenthesis that closes one opened before the
# group and is itself followed only by such an ending. "x to the power n"
# followed by "plus 1" could be read as "x to the power (n plus 1)", so an
# open-ended group is accepted only where nothing can be read into it.
_RELATION_COMMAND = re.compile(r"\\(?:le|leq|ge|geq|ne|neq|approx|equiv)(?![A-Za-z])")
_CLOSING_PAREN = re.compile(r"\)|\\right\s*\)")
# A spacing command the label turns into a space, skipped when looking ahead.
_SPACING_COMMAND = re.compile(r"\\(?:[,;:!]|quad(?![A-Za-z])|qquad(?![A-Za-z]))")
# What a limit sits on: a script directly after one of these is a limit, which
# the label would read as a script on the symbol.
_BEFORE_LIMITS = re.compile(r"\\(?:int|sum|prod|oint)\s*$")


def _may_follow(text: str, end: int, own: Optional[str]) -> bool:
    """Whether what comes right after a group keeps its last word apart.

    ``own`` is ``^`` or ``_`` for a script group, ``None`` for a fraction,
    binomial or root.
    """
    following = text[end : end + 1]
    if following == "\\":
        return _SEPARATOR_COMMAND.match(text, end) is not None
    if following.isalnum() or following == "{":
        return False
    # A decimal point or comma with a digit behind it would read as a longer
    # number: ``x^{1}.5`` and ``x^{1.5}`` would say the same thing.
    if following in (".", ",") and text[end + 1 : end + 2].isdigit():
        return False
    if following in ("^", "_"):
        return own is not None and following != own
    return True


def _is_terminal(text: str, end: int, depth: int = 0) -> bool:
    """Whether an open-ended spoken form ends here, so nothing can be read into it.

    ``depth`` is the number of parentheses still open at the group. A closing
    parenthesis ends the form only when one is open, and then what follows it must
    end the form too: a screen reader does not speak the parenthesis, so
    ``(1 + x^{n}) + 2`` would read like ``1 + x^{n} + 2``.
    """
    index = end
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        spacing = _SPACING_COMMAND.match(text, index)
        if spacing is None:
            break
        index = spacing.end()
    if index >= len(text):
        return True
    if text[index] in "=<>,":
        return True
    closing = _CLOSING_PAREN.match(text, index)
    if closing is not None:
        return depth > 0 and _is_terminal(text, closing.end(), depth - 1)
    return _RELATION_COMMAND.match(text, index) is not None


def _closed_script(character: str, argument: str) -> bool:
    """A script spoken "squared" or "cubed" is closed; every other one is not."""
    return character == "^" and argument.strip() in ("2", "3")


# What a script may not stand on: a closing parenthesis or bracket, whose
# parenthesis the label keeps but a screen reader does not speak, and a
# ``\mathrm`` group.
_CLOSING_BASE = re.compile(r"(?:[)\]]|\\mathrm\{[A-Za-z]\})\s*$")


def _structure_is_unambiguous(text: str) -> bool:
    index = 0
    length = len(text)
    depth = 0
    while index < length:
        character = text[index]
        if character == "\\":
            command = _PREFIX_COMMAND.match(text, index)
            if command:
                first = _BRACED_TOKEN.match(text, command.end())
                second = _BRACED_TOKEN.match(text, first.end()) if first else None
                if (
                    second is None
                    or not _may_follow(text, second.end(), None)
                    or not _is_terminal(text, second.end(), depth)
                ):
                    return False
                index = second.end()
                continue
            command = _SQRT_COMMAND.match(text, index)
            if command:
                position = command.end()
                optional = _BRACKETED_TOKEN.match(text, position)
                if optional:
                    root_index = text[position + 1 : optional.end() - 1].strip()
                    if root_index not in _ROOT_WORDS and not re.fullmatch(
                        "[A-Za-z]", root_index
                    ):
                        return False
                    position = optional.end()
                radicand = _BRACED_TOKEN.match(text, position)
                if (
                    radicand is None
                    or not _may_follow(text, radicand.end(), None)
                    or not _is_terminal(text, radicand.end(), depth)
                ):
                    return False
                index = radicand.end()
                continue
            group = _MATHRM_GROUP.match(text, index)
            if group:
                index = group.end()
                continue
            escaped = text[index + 1 : index + 2]
            index += 2 if escaped and escaped in "{}^_" else 1
            continue
        if character in "^_":
            if _BEFORE_LIMITS.search(text, 0, index):
                return False
            if _CLOSING_BASE.search(text, 0, index):
                return False
            if text[index + 1 : index + 2] == "{":
                group = _BRACED_TOKEN.match(text, index + 1)
                if group is None or not _may_follow(text, group.end(), character):
                    return False
                argument = text[index + 2 : group.end() - 1]
                if not _closed_script(character, argument) and not _is_terminal(
                    text, group.end(), depth
                ):
                    return False
                index = group.end()
                continue
            script = text[index + 1 :]
            first = script[:1]
            if not first or not (first.isascii() and first.isalnum()):
                return False
            if script[1:2].isalnum():
                return False
            if not _may_follow(text, index + 2, character):
                return False
            if not _closed_script(character, first) and not _is_terminal(
                text, index + 2, depth
            ):
                return False
            index += 2
            continue
        if character in "{}":
            return False
        if character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        index += 1
    return True


def equation_alt_text_is_ambiguous(latex: str) -> bool:
    """Whether the deterministic label cannot be trusted for this LaTeX.

    The label is refused, never guessed, when it falls back to the generic text,
    when it would erase a command it has no words for, when a backslash, a caret
    or an underscore would survive into it, when letters in the equation spell a
    word the labels use, or when its words could belong to a different equation
    (see the rules above). ``\\frac12`` would read "12", ``x^{n}y`` and ``x^{ny}``
    would read the same, ``\\sin x = 0`` would read "x = 0", and ``a over b``
    would read like ``\\frac{a}{b}``. The rules are a best-effort set and not a
    proof that no two accepted equations read alike.
    """
    label = generate_equation_alt_text(latex)
    if label == _EQUATION_ALT_TEXT_FALLBACK or any(
        mark in label for mark in ("\\", "^", "_")
    ):
        return True
    text = strip_math_delimiters(latex)
    if _unspoken_commands(text):
        return True
    if _letters_spell_a_spoken_word(text):
        return True
    return not _structure_is_unambiguous(text)


# ---------------------------------------------------------------------------
# One check for every generated description
#
# A description that holds mathematics is never rewritten here. A prompt that
# asks for plain spoken words removes the markup, so a description that still
# carries it is not trusted: it is refused whole and the image is left for a
# person. Text without markup is returned as the same string.
# ---------------------------------------------------------------------------

# Returned as the ``error`` of a generation result that was refused. It avoids
# the words the visual-analysis recorder matches on to classify a provider
# failure, so a refusal is final and is never retried.
MATH_NOT_SPEAKABLE_ERROR = "description_math_not_speakable"

# The manual reason a remediator records when a proposed description was refused.
IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON = "image_description_math_not_speakable"

# What a remediator tells a person when it refused such a description.
MATH_DESCRIPTION_RECOMMENDATION = (
    "The proposed description contained mathematical notation, such as LaTeX, "
    "that a screen reader would announce symbol by symbol, or characters that "
    "cannot be read as text, so nothing was "
    "written. Describe the image yourself, writing any mathematics in plain "
    "words, or tag an equation with a Formula element and its alt text."
)

# What an API caller is told when a description was refused.
MATH_REFUSAL_MESSAGE = (
    "The generated description contained mathematical notation that a screen "
    "reader would announce symbol by symbol, or characters that cannot be read "
    "as text, so it was not used. Describe the "
    "image yourself, or mark it as an equation."
)

# The sentence every alternative-text prompt carries. It asks for words and does
# not show LaTeX, so the prompt does not prime the model to write any.
SPOKEN_MATH_INSTRUCTION = (
    "Describe any mathematics in plain spoken words, the way you would read it "
    'aloud, for example "x squared plus 3 x minus 7 equals 0" or "a over b". '
    "Do not write LaTeX, markdown or math delimiters, which means no dollar "
    "signs, backslashes, carets or braces."
)

# Delimited mathematics that is not an inline dollar span: ``\(...\)`` and
# ``\[...\]``.
_ESCAPED_DELIMITERS = re.compile(r"\\\(.+?\\\)|\\\[.+?\\\]", re.DOTALL)

# A braced script, ``x^{2}`` or ``a_{n}``.
_SCRIPT_GROUP = re.compile(r"[\^_]\{")
# A bare script with something on both sides: ``r^2`` or ``(a+b)^2``. A caret that
# has a space on either side, as in "press ^C", is not read as a script.
_BARE_CARET = re.compile(r"(?<=\S)\^(?=\S)")
# A bare subscript between single symbols: ``x_i`` or ``a_10``. The symbol on the
# left stands alone, so ``snake_case`` and ``my_file`` are not read as scripts.
_BARE_SUBSCRIPT = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]_[A-Za-z0-9]")

# Dollar spans, in the order the alternatives are tried. The inline form follows
# the usual Markdown rule so that prices stay prices: the opening dollar sign is
# followed by something other than a space, the closing one is not preceded by a
# space or an operator and is not followed by a digit, and an escaped dollar sign
# never opens or closes anything. A span is mathematics only when what is between
# the signs holds mathematics, so a unit such as ``($)`` or ``($M)`` is not.
_DOLLAR_SPAN = re.compile(
    r"(?<!\\)\$\$(?P<display>.+?)(?<!\\)\$\$"
    r"|(?<![\\$])\$(?![\s$])(?P<inline>[^$\n]+?)(?<![\s\\=+\-*/,:;<>])\$(?![\d$])",
    re.DOTALL,
)
_INNER_MATH = re.compile(
    r"\\[A-Za-z]"  # a command
    r"|[\^_{}|]"  # a script, a brace or a bar
    r"|^\s*[A-Za-z]+\("  # a function call
    r"|^\s*[A-Za-z0-9]\s*$"  # a lone symbol
)
# An operator between symbols, with or without spaces: ``y=mx`` and ``y = mx + b``.
_SPACED_OPERATOR = re.compile(r"[A-Za-z0-9)\]]\s*[=<>+\-*/^_]\s*[A-Za-z0-9(\[]")
_LETTER = re.compile(r"[A-Za-z]")
# What is between two unit markers, as in ``($M) and ($K)``: the first parenthesis
# of such a span is a closing one.
_BETWEEN_UNIT_MARKERS = re.compile(r"^[^()]*\)")


def _span_holds_math(inner: str) -> bool:
    """Whether what a dollar span encloses is mathematics and not a price or unit.

    A span the dollar rule matched has already been told apart from prices: its
    closing sign follows no space, operator or digit. What is inside is
    mathematics when it holds a command, a script, a brace or a bar, a function
    call, a lone symbol, an operator between symbols (spaces allowed), or any
    letter, which is a variable (``$ab$``). Two kinds of text are not: what is
    between two unit markers, as in ``($M) and ($K)``, and words set off by spaces
    between two display signs, as in the price tiers of ``$$ or $$$``. A letter
    makes a span mathematics only when it touches the signs, which is how an
    inline span is always written and how display mathematics usually is.
    """
    if _INNER_MATH.search(inner) or _SPACED_OPERATOR.search(inner):
        return True
    if _LETTER.search(inner) is None or _BETWEEN_UNIT_MARKERS.match(inner):
        return False
    return not (inner[:1].isspace() or inner[-1:].isspace())


# Names that are LaTeX commands, so ``C:\frac`` and ``P:\Rightarrow`` are not read
# as a drive path. The list is shared with the check for JSON-swallowed commands.
_KNOWN_COMMANDS = LATEX_COMMAND_NAMES

# A token that is clearly a Windows path is not mathematics. The rule is
# deliberately narrow: a drive letter (``C:\Users\...``), a UNC share
# (``\\server\share``), or a relative path that ends in a file extension
# (``src\education\math.py``). None of them holds a brace, a caret or a dollar
# sign, and a drive path does not start with a LaTeX command, so ``B:\frac{1}{2}``
# is mathematics. A backslash followed by letters in anything else is read as a
# LaTeX command, which is the cautious reading.
_PATH_LEAD = r"[\"'(\[<]*"
_WINDOWS_PATH = re.compile(
    rf"^{_PATH_LEAD}[A-Za-z]:\\[^\s{{}}^$]*$"
    rf"|^{_PATH_LEAD}\\\\[^\\\s]+\\[^\s{{}}^$]*$"
    rf"|^{_PATH_LEAD}[\w.-]+(?:\\[\w.-]+)+\.[A-Za-z0-9]{{1,5}}[\"')\].,;:!?>]*$"
)
_DRIVE_COMPONENT = re.compile(rf"^{_PATH_LEAD}[A-Za-z]:\\([A-Za-z]+)")
# A token that cannot continue a path that contains spaces.
_NOT_PATH = re.compile(r"[{}^$]")
# A token that starts with a backslash and a letter is a command and never the
# next word of a path: ``C:\data \theta\cdot\pi`` is a path and LaTeX.
_STARTS_WITH_COMMAND = re.compile(rf"^{_PATH_LEAD}\\[A-Za-z]")


def _is_windows_path(token: str) -> bool:
    if _WINDOWS_PATH.search(token) is None:
        return False
    drive = _DRIVE_COMPONENT.match(token)
    return not (drive and drive.group(1) in _KNOWN_COMMANDS)


# What a description may carry that is not printable. Line breaks, tabs and the
# Unicode line and paragraph separators are ordinary whitespace in a description
# and are folded to a space where it is written. Every other control, format,
# surrogate, private-use or unassigned character is refused: a NUL or an escape
# is not text, and a direction override such as U+202E reverses what a reader
# sees.
_FOLDED_WHITESPACE = frozenset("\n\r\t\u2028\u2029")
_UNREADABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def holds_unreadable_characters(text: str) -> bool:
    """Whether a description holds a character that cannot be read as text."""
    return any(
        character not in _FOLDED_WHITESPACE
        and unicodedata.category(character) in _UNREADABLE_CATEGORIES
        for character in text
    )


def holds_math_markup(text: str) -> bool:
    """Whether a description carries LaTeX or other mathematical markup.

    That is any of: a backslash command that is not part of a clearly identified
    Windows path; ``\\(...\\)`` or ``\\[...\\]``; ``^{`` or ``_{``; a bare script
    with a symbol on both sides, such as ``r^2`` or ``x_i``; or a dollar span
    with mathematics between the signs, meaning a command, a script, a bar, a
    brace, an operator between symbols (with or without spaces), a function call,
    a single symbol or any letter. Prices such as ``$5 and $10`` or ``$5-$10``,
    units such as ``($M)`` and paths are not markup.
    """
    if _ESCAPED_DELIMITERS.search(text):
        return True
    for match in _DOLLAR_SPAN.finditer(text):
        inner = match.group("display")
        if inner is None:
            inner = match.group("inline")
        if _span_holds_math(inner):
            return True
    in_path = False
    for token in text.split():
        if _is_windows_path(token):
            in_path = True
            continue
        if (
            in_path
            and "\\" in token
            and not _NOT_PATH.search(token)
            and not _STARTS_WITH_COMMAND.match(token)
        ):
            # The next word of a path that has a space in it, such as the
            # ``Files\App`` of ``C:\Program Files\App``.
            continue
        in_path = False
        if (
            _COMMAND.search(token)
            or _SCRIPT_GROUP.search(token)
            or _BARE_CARET.search(token)
            or _BARE_SUBSCRIPT.search(token)
        ):
            return True
    return False


def refuse_math_markup(text: str) -> Optional[str]:
    """Return ``text`` unchanged, or ``None`` when it carries mathematical markup.

    This is the one check a generated description passes through before it is
    used as alternative text. It never rewrites anything: the string that comes
    back is the string that went in. ``None`` means the description must not be
    used and the image is left for a person. That is the case for mathematical
    markup and for a character that cannot be read as text, such as a NUL, an
    escape or a direction override; line breaks and tabs are not refused.
    """
    if (
        not isinstance(text, str)
        or holds_unreadable_characters(text)
        or holds_math_markup(text)
    ):
        return None
    return text


__all__ = [
    "IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON",
    "MATH_DESCRIPTION_RECOMMENDATION",
    "MATH_NOT_SPEAKABLE_ERROR",
    "MATH_REFUSAL_MESSAGE",
    "SPOKEN_MATH_INSTRUCTION",
    "equation_alt_text_is_ambiguous",
    "generate_equation_alt_text",
    "holds_math_markup",
    "holds_unreadable_characters",
    "refuse_math_markup",
    "strip_math_delimiters",
]
