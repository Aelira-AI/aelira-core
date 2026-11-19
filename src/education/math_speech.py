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
from typing import Any, List, NamedTuple, Optional
from xml.etree import ElementTree

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
#      "x^{n}) + 1", because a screen reader may not speak a parenthesis.
#   4. A brace group that is not an argument is refused. The label drops braces,
#      so ``{ab}^2`` and ``ab^2`` would read the same.
#   5. A script directly after an integral, sum, product or contour integral is a
#      limit, and is refused: the label would read it as a script on the symbol.
#   6. A command with no spoken form is never erased. Only the typography
#      commands above, which say nothing, are dropped.
#   7. A backslash, a caret or an underscore never survives into the label.
#   8. A script is refused when its base is a closing parenthesis, bracket or bar
#      or a ``\mathrm`` group, so ``(2x)^2``, ``|x|^2`` and ``2x^2`` do not read
#      alike. Plain grouping is not protected: ``(a + b)c`` and ``a + bc`` read
#      alike when the reader does not announce the brackets, and only speech
#      generated from MathML can fix that. A
#      ``\mathrm`` group holds one letter, which has the same MathML as the
#      plain letter; a longer one would hide where a base begins.
#   9. A run of letters that spells a word the labels use ("over", "sub",
#      "squared", "pi", "sum", "times"), or a phrase they use ("divided by"), is
#      refused, because ``a over b`` would read like ``\frac{a}{b}``. The words
#      are looked for after the typography commands are applied, so a command that
#      only changes how the equation is drawn cannot split one: ``a o\left.ver b``
#      and ``x \mathrm{s}quared`` are refused too. A word that occurs only inside
#      a longer phrase, such as "by", is not refused alone: ``ax + by = c`` is
#      said.
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


# The words that join the parts of a construct ("over" in a fraction, "choose" in
# a binomial, "sub" and "to the power" in a script, "root" in a root). They have
# no spoken form of their own, so the table below cannot list them.
_CONNECTING_WORDS = frozenset({"over", "choose", "sub", "power", "root"})


def _spoken_vocabulary() -> "tuple[frozenset[str], frozenset[tuple[str, ...]]]":
    """The single words and the phrases a label can contain because of a rule.

    A rule whose spoken form is one word makes that word a keyword. A rule whose
    spoken form is several words makes the phrase one, and its words are not
    keywords alone: "by" occurs only in "divided by", so ``by`` is an ordinary
    variable in ``ax + by = c``. A template that takes an argument ("\\1 over \\2")
    contributes the connecting words above.
    """
    keywords = set(_CONNECTING_WORDS)
    phrases = set()
    for _pattern, replacement in _SPOKEN_RULES:
        if not isinstance(replacement, str) or re.search(r"\\\d", replacement):
            continue
        words = tuple(word.casefold() for word in re.findall(r"[A-Za-z]+", replacement))
        if len(words) == 1:
            keywords.add(words[0])
        elif words:
            phrases.add(words)
    return frozenset(keywords), frozenset(phrases)


# A run of letters in the equation that spells a keyword, or a sequence of runs
# that spells a phrase, reads exactly like the construct that produces it, so it
# is refused (rule 9).
_SPOKEN_KEYWORDS, _SPOKEN_PHRASES = _spoken_vocabulary()
_WORD_SEQUENCE = re.compile(r"[A-Za-z]+(?: +[A-Za-z]+)*")


def _as_the_label_would_join_it(text: str) -> str:
    """The text with the typography commands applied and the other commands marked.

    ``\\mathrm{s}quared`` becomes ``squared`` and ``o\\left.ver`` becomes ``over``,
    which is what the label says for them, so a command that only changes how the
    equation is drawn cannot split a word. A command that is spoken becomes a
    backslash, which no word runs across.
    """
    for pattern, replacement in _TYPOGRAPHY_RULES:
        text = pattern.sub(replacement, text)
    return _COMMAND.sub(lambda _match: "\\", text)


def _letters_spell_a_spoken_word(text: str) -> bool:
    for sequence in _WORD_SEQUENCE.findall(_as_the_label_would_join_it(text)):
        words = [word.casefold() for word in sequence.split()]
        if any(word in _SPOKEN_KEYWORDS for word in words):
            return True
        for phrase in _SPOKEN_PHRASES:
            width = len(phrase)
            if any(
                tuple(words[start : start + width]) == phrase
                for start in range(len(words) - width + 1)
            ):
                return True
    return False


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
    end the form too: a screen reader may not speak the parenthesis, so
    ``(1 + x^{n}) + 2`` could read like ``1 + x^{n} + 2``.
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


# What a script may not stand on: a closing parenthesis, bracket or bar, which the
# label keeps but a screen reader may not speak, and a ``\mathrm`` group. A closing
# bar is ``|``, ``\right|``, ``\rvert`` or ``\rVert``, so ``|x|^2`` and
# ``\left| x \right|^2`` are refused like ``(2x)^2``.
_CLOSING_BASE = re.compile(r"(?:[)\]|]|\\mathrm\{[A-Za-z]\}|\\rvert|\\rVert)\s*$")


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
# Spoken text for the verified MathML
#
# The label above reads LaTeX with regular expressions, so its words are only
# as right as those regexes are: ``\frac13`` reads "13" and ``x^10`` (x to the
# first power, then 0) reads "x to the power 10". Verification, though, agrees
# on canonical MathML, and the Formula carries that MathML. The walk below
# speaks the MathML tree itself, so what a screen reader announces is what was
# verified: a fraction is an mfrac with two children, a script is an msup, msub
# or msubsup, and an argument written without braces is a single mi or mn
# element, not a regex group.
#
# The walk refuses rather than guesses, and it never contradicts the tree. An
# element it has no rule for, an operator outside the word list, an argument
# that is not exactly one token, and a parse failure all return ``None``, and
# the caller keeps the label it already trusted (or the generic one). Keeping
# every construct's argument to one token is the same rule the ambiguity guard
# applies to the LaTeX, applied here to the tree: it is what stops
# ``\frac{a+b}{c}`` from reading "a + b over c", which is also how
# ``\frac{a}{b} + c`` would read. The guard's refusals stand on their own
# upstream; the walk never loosens them.
# ---------------------------------------------------------------------------


class _SpokenPart(NamedTuple):
    """One spoken piece and how it may sit against its neighbours.

    ``symbol`` is a single letter or number, which runs into the symbol
    before it the way "3x" does. ``word`` is a leaf with its own word or
    punctuation, and ``group`` is a construct (a fraction, a root, a script)
    whose own words already carry its shape.
    """

    text: str
    kind: str


_SYMBOL = "symbol"
_WORD = "word"
_GROUP = "group"

# The Greek letters mi carries, in the same words the LaTeX table speaks.
_ML_LETTER_WORDS = {
    "\u03b1": "alpha",
    "\u03b2": "beta",
    "\u03b3": "gamma",
    "\u03b4": "delta",
    "\u03b5": "epsilon",
    "\u03b6": "zeta",
    "\u03b7": "eta",
    "\u03b8": "theta",
    "\u03b9": "iota",
    "\u03ba": "kappa",
    "\u03bb": "lambda",
    "\u03bc": "mu",
    "\u03bd": "nu",
    "\u03be": "xi",
    "\u03c0": "pi",
    "\u03c1": "rho",
    "\u03c3": "sigma",
    "\u03c4": "tau",
    "\u03c5": "upsilon",
    "\u03c6": "phi",
    "\u03c7": "chi",
    "\u03c8": "psi",
    "\u03c9": "omega",
    "\u0393": "Gamma",
    "\u0394": "Delta",
    "\u0398": "Theta",
    "\u039b": "Lambda",
    "\u039e": "Xi",
    "\u03a0": "Pi",
    "\u03a3": "Sigma",
    "\u03a5": "Upsilon",
    "\u03a6": "Phi",
    "\u03a8": "Psi",
    "\u03a9": "Omega",
}

# The operators mo carries that have words, in the same words the LaTeX table
# speaks.
_ML_OPERATOR_WORDS = {
    "\u00d7": "times",
    "\u00f7": "divided by",
    "\u00b1": "plus or minus",
    "\u2213": "minus or plus",
    "\u2264": "less than or equal to",
    "\u2265": "greater than or equal to",
    "\u2260": "not equal to",
    "\u2248": "approximately equal to",
    "\u2261": "equivalent to",
    "\u22c5": "dot",
    "\u2026": "ellipsis",
    "\u22ef": "centered ellipsis",
    "\u221e": "infinity",
    "\u2202": "partial",
    "\u2207": "nabla",
    "\u2211": "sum",
    "\u220f": "product",
    "\u222b": "integral",
    "\u222e": "contour integral",
}

# What mo may say as itself. The Unicode minus is spoken as the ASCII hyphen
# the equation was written with. Bars, asterisks and anything else outside
# this list are refused rather than read.
_ML_PUNCTUATION = frozenset("+-\u2212=<>()[],;!/")

# A multi-letter mi is a function name. These are the words the LaTeX table
# gives them; any other name is refused, because the tree cannot tell a
# function from a product of letters.
_ML_FUNCTION_WORDS = {
    "sin": "sine",
    "cos": "cosine",
    "tan": "tangent",
    "log": "log",
    "ln": "natural log",
}

# A fence that opens or closes nothing it has words for reads best against
# what it holds: "f(x)" and "(a over b)", not "f ( x )".
_ML_OPENING = ("(", "[")
_ML_CLOSING = (")", "]")

# Punctuation that belongs to the word before it: "x, y", never "x , y".
_ML_GLUED_PUNCTUATION = (",", ";")


def _ml_local_name(tag: str) -> str:
    """The MathML element name, with any namespace removed."""
    _, brace, local = tag.rpartition("}")
    return local if brace else tag


def _ml_is_binomial(element: ElementTree.Element) -> bool:
    """Whether an mfrac is the zero-rule fraction a binomial is drawn with."""
    return (
        _ml_local_name(element.tag) == "mfrac" and element.get("linethickness") == "0"
    )


def _ml_is_binomial_fence(
    child: ElementTree.Element, siblings: List[ElementTree.Element], index: int
) -> bool:
    """Whether this parenthesis is one of the pair a binomial brings.

    latex2mathml draws ``\\binom{n}{k}`` as ``( n choose k )``; the table above
    says "n choose k", so the parentheses written straight against the
    zero-rule fraction are its own drawing, not a grouping to announce. A
    parenthesis outside that pair, as in ``(\\binom{n}{k})``, stays.
    """
    if _ml_local_name(child.tag) != "mo":
        return False
    text = (child.text or "").strip()
    if text == "(":
        return index + 1 < len(siblings) and _ml_is_binomial(siblings[index + 1])
    if text == ")":
        return index > 0 and _ml_is_binomial(siblings[index - 1])
    return False


def _ml_join(parts: List[_SpokenPart]) -> str:
    joined: List[str] = []
    for index, part in enumerate(parts):
        if index:
            previous = parts[index - 1]
            run_together = (
                (previous.kind == _SYMBOL and part.kind == _SYMBOL)
                or previous.text in _ML_OPENING
                or part.text in _ML_CLOSING
                or part.text in _ML_GLUED_PUNCTUATION
                or (previous.kind == _SYMBOL and part.text in _ML_OPENING)
            )
            if not run_together:
                joined.append(" ")
        joined.append(part.text)
    return "".join(joined)


def _ml_speak_leaf(
    name: str, element: ElementTree.Element
) -> Optional[List[_SpokenPart]]:
    if len(list(element)):
        return None
    text = (element.text or "").strip()
    if not text:
        return None
    if name == "mo":
        word = _ML_OPERATOR_WORDS.get(text)
        if word is not None:
            return [_SpokenPart(word, _WORD)]
        if len(text) == 1 and text in _ML_PUNCTUATION:
            return [_SpokenPart("-" if text == "\u2212" else text, _WORD)]
        return None
    if name == "mn":
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
            return [_SpokenPart(text, _SYMBOL)]
        return None
    word = _ML_LETTER_WORDS.get(text)
    if word is not None:
        return [_SpokenPart(word, _WORD)]
    if len(text) == 1 and text in _ML_OPERATOR_WORDS:
        # latex2mathml sets \ldots on an mi, not an mo; the word is the same.
        return [_SpokenPart(_ML_OPERATOR_WORDS[text], _WORD)]
    if len(text) == 1 and text.isascii() and text.isalnum():
        return [_SpokenPart(text, _SYMBOL)]
    word = _ML_FUNCTION_WORDS.get(text)
    if word is not None:
        return [_SpokenPart(word, _WORD)]
    return None


def _ml_speak_children(element: ElementTree.Element) -> Optional[List[_SpokenPart]]:
    """The parts of an element's children, or None if any child is refused."""
    if (element.text or "").strip():
        return None
    children = list(element)
    parts: List[_SpokenPart] = []
    for index, child in enumerate(children):
        if (child.tail or "").strip():
            return None
        if _ml_is_binomial_fence(child, children, index):
            continue
        spoken = _ml_speak(child)
        if spoken is None:
            return None
        parts.extend(spoken)
    return parts


def _ml_token(element: ElementTree.Element) -> Optional[str]:
    """The words of an element when it is exactly one token and no group."""
    spoken = _ml_speak(element)
    if not spoken or len(spoken) != 1 or spoken[0].kind == _GROUP:
        return None
    return spoken[0].text


def _ml_root_word(index: str) -> Optional[str]:
    """The word a root index is spoken as, as the LaTeX table speaks it."""
    if re.fullmatch(r"[0-9]+", index):
        return _ROOT_WORDS.get(index, f"{index}th")
    if len(index) == 1 and index.isascii() and index.isalpha():
        return f"{index}th"
    return None


def _ml_power_phrase(base: str, exponent: str) -> str:
    if exponent == "2":
        return f"{base} squared"
    if exponent == "3":
        return f"{base} cubed"
    return f"{base} to the power {exponent}"


def _ml_speak_script(element: ElementTree.Element) -> Optional[List[_SpokenPart]]:
    name = _ml_local_name(element.tag)
    children = list(element)
    expected = 3 if name == "msubsup" else 2
    if len(children) != expected:
        return None
    base = _ml_token(children[0])
    if base is None:
        return None
    if name == "msub":
        sub = _ml_token(children[1])
        if sub is None:
            return None
        return [_SpokenPart(f"{base} sub {sub}", _GROUP)]
    if name == "msup":
        exponent = _ml_token(children[1])
        if exponent is None:
            return None
        return [_SpokenPart(_ml_power_phrase(base, exponent), _GROUP)]
    sub = _ml_token(children[1])
    exponent = _ml_token(children[2])
    if sub is None or exponent is None:
        return None
    return [_SpokenPart(f"{_ml_power_phrase(base, exponent)} sub {sub}", _GROUP)]


def _ml_speak(element: ElementTree.Element) -> Optional[List[_SpokenPart]]:
    """The spoken parts of one MathML element, or None if it cannot be said.

    Only the elements canonical MathML carries a meaning for are spoken.
    A style wrapper (an mstyle) says nothing of its own; munder, mover,
    mtable, menclose, mtext, mspace and every other element are refused,
    because the walk has no words that could not mislead.
    """
    name = _ml_local_name(element.tag)
    if name in ("math", "mrow", "mstyle"):
        return _ml_speak_children(element)
    if name in ("mi", "mn", "mo"):
        return _ml_speak_leaf(name, element)
    if name == "mfrac":
        arguments = list(element)
        if len(arguments) != 2:
            return None
        numerator = _ml_token(arguments[0])
        denominator = _ml_token(arguments[1])
        if numerator is None or denominator is None:
            return None
        if _ml_is_binomial(element):
            return [_SpokenPart(f"{numerator} choose {denominator}", _GROUP)]
        return [_SpokenPart(f"{numerator} over {denominator}", _GROUP)]
    if name == "msqrt":
        radicand = _ml_speak_children(element)
        if radicand is None or len(radicand) != 1 or radicand[0].kind == _GROUP:
            return None
        return [_SpokenPart(f"square root of {radicand[0].text}", _GROUP)]
    if name == "mroot":
        arguments = list(element)
        if len(arguments) != 2:
            return None
        radicand = _ml_token(arguments[0])
        index = _ml_token(arguments[1])
        if radicand is None or index is None:
            return None
        word = _ml_root_word(index)
        if word is None:
            return None
        return [_SpokenPart(f"{word} root of {radicand}", _GROUP)]
    if name in ("msup", "msub", "msubsup"):
        return _ml_speak_script(element)
    return None


def spoken_text_from_mathml(mathml: Any) -> Optional[str]:
    """Return what a screen reader should announce for this MathML, or None.

    The words come from walking the tree, so a construct is read the way the
    tree groups it: ``<mfrac><mn>1</mn><mn>3</mn></mfrac>`` is "1 over 3"
    however the LaTeX was braced, and ``x^10`` (an msup over 1, then a 0) is
    "x to the power 1 0". The walk refuses rather than guesses: anything it
    has no rule for, an argument that is not exactly one token, and a parse
    failure all return ``None`` so the caller keeps a label it already
    trusts. It never emits a reading the tree does not say.
    """
    if not isinstance(mathml, str) or not mathml.strip():
        return None
    try:
        root = ElementTree.fromstring(mathml)
        if _ml_local_name(root.tag) != "math":
            return None
        parts = _ml_speak(root)
    except (ElementTree.ParseError, RecursionError):
        return None
    if not parts:
        return None
    return _ml_join(parts).strip() or None


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
# followed by something other than a space and does not follow a letter or a digit,
# so ``US$100`` and ``A$`` are currency prefixes and never open a span, the closing
# one is not preceded by a space or an operator and is not followed by a digit, and
# an escaped dollar sign never opens or closes anything. A span is mathematics only
# when what is between the signs holds mathematics, so a unit such as ``($)`` or
# ``($M)`` is not.
_DOLLAR_SPAN = re.compile(
    r"(?<!\\)\$\$(?P<display>.+?)(?<!\\)\$\$"
    r"|(?<![\\$A-Za-z0-9])\$(?![\s$])(?P<inline>[^$\n]+?)(?<![\s\\=+\-*/,:;<>])\$(?![\d$])",
    re.DOTALL,
)
# A span with a space inside both signs, ``$ y = mx + b $``. It is checked for what
# is certainly mathematics only, because words set off the same way are how price
# tiers are written (``$ or $``): letters alone do not make it mathematics.
_PADDED_DOLLAR_SPAN = re.compile(
    r"(?<![\\$A-Za-z0-9])\$(?=\s)(?P<inner>[^$\n]+?)(?<=\s)\$(?![\d$])"
)
_INNER_MATH = re.compile(
    r"\\[A-Za-z]"  # a command
    r"|[\^_{}|]"  # a script, a brace or a bar
    r"|^\s*[A-Za-z]+\("  # a function call
    r"|^\s*[A-Za-z]\s*$"  # a lone symbol
    r"|^\s*\d+(?:[.,]\d+)?\s*$"  # a lone number, ``$3.14$``
)
# An operator between symbols, with or without spaces: ``y=mx`` and ``y = mx + b``.
_SPACED_OPERATOR = re.compile(
    r"[A-Za-z0-9)\]]\s*[=<>+\-*/^_\u00d7\u00f7\u2212\u00b1\u2264\u2265\u2260\u2248]\s*[A-Za-z0-9(\[]"
)
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
_LEADING_LETTERS = re.compile(r"[A-Za-z]+")
# A token that cannot continue a path that contains spaces.
_NOT_PATH = re.compile(r"[{}^$]")
# A token that starts with a backslash and a letter is a command and never the
# next word of a path: ``C:\data \theta\cdot\pi`` is a path and LaTeX.
_STARTS_WITH_COMMAND = re.compile(rf"^{_PATH_LEAD}\\[A-Za-z]")


def _is_windows_path(token: str) -> bool:
    if _WINDOWS_PATH.search(token) is None:
        return False
    drive = _DRIVE_COMPONENT.match(token)
    if drive and drive.group(1) in _KNOWN_COMMANDS:
        return False
    # A backslash followed by the name of a command is that command wherever it
    # stands: ``categories.\ne.g.`` is a sentence and a negation, not a file.
    return not any(
        (name := _LEADING_LETTERS.match(segment)) is not None
        and name.group() in _KNOWN_COMMANDS
        for segment in token.split("\\")[1:]
    )


# What a description may carry that is not printable. Line breaks, tabs and the
# Unicode line and paragraph separators are ordinary whitespace in a description
# and are not refused here. What a path does with them is that path's own rule:
# the PDF remediator writes them unchanged, and the generic generator's normaliser
# refuses them today. Every other control, format, surrogate, private-use or
# unassigned character is refused: a NUL or an escape is not text, and a direction
# override such as U+202E reverses what a reader sees.
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
    for match in _PADDED_DOLLAR_SPAN.finditer(text):
        inner = match.group("inner")
        if _INNER_MATH.search(inner) or _SPACED_OPERATOR.search(inner):
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
    "spoken_text_from_mathml",
    "strip_math_delimiters",
]
